"""Fact Extractor (F3) — 모든 문서를 공통 facts.json 으로 정규화.

- Excel: F2 ``records`` → ``facts`` 를 **코드로 결정적 변환**(LLM 미사용, 결정 F3-1).
  F2 가 이미 의미 정규화했으므로 규칙 매핑만 하면 된다(추가 비용·할루시네이션 0).
- Word/PPT: ``compact_raw`` 블록/도형을 **LLM 으로 추출**(결정 F3-2, F1·F2 건너뜀).

``source`` 는 Excel 은 record 좌표를 승계하고, Word/PPT 는 LLM 이 준 id 를 배치 실제
id 와 대조 검증한다(결정 F3-7) — 좌표/근거 할루시네이션 방지.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from .fact_models import Fact, FactSet
from .fact_types import FT_DESCRIPTIVE, FT_QUALITATIVE, FT_QUANTITATIVE
from .llm_stage import fingerprint_for
from .prompts import FACT_SYSTEM, FACT_VERSION, build_fact_user
from .record_models import RecordSet


def extract_facts(
    compact: dict,
    *,
    records: Optional[RecordSet] = None,
    profile: Any = None,
    runner: Any = None,
    store: Any = None,
    batch_blocks: int = 20,
    stats: Optional[dict] = None,
) -> FactSet:
    """compact(+records) → ``FactSet``. doc_type 으로 Excel(코드)/Word·PPT(LLM) 분기.

    ``store`` 가 있으면 ``facts`` 단계를 캐싱(같은 입력이면 재계산/재호출 0).

    ``stats`` 를 주면 계측값을 채운다(out-param, F3.5). Word/PPT 경로는 근거 없는
    fact 를 조용히 버리는데(§ ``_facts_from_blocks``), 이 드롭은 곧 대상 문서의
    fact 누락 → F5 의 ``missing`` 오판으로 이어지므로 **사유별로 센다**.
    """
    doc_type = compact.get("doc_type")
    computed = {"ran": False}
    drops: dict[str, Any] = {}
    if doc_type == "excel":
        rs = records if records is not None else RecordSet()

        def compute() -> dict:
            computed["ran"] = True
            return _facts_from_records(rs).to_dict()

        fp = fingerprint_for(json.dumps(rs.to_dict(), ensure_ascii=False)) if store else None
    else:

        def compute() -> dict:
            computed["ran"] = True
            return _facts_from_blocks(
                compact, profile, runner, batch_blocks, drops
            ).to_dict()

        fp = (
            fingerprint_for(json.dumps(compact, ensure_ascii=False), FACT_VERSION)
            if store
            else None
        )

    if store is not None:
        data = store.cached_or_compute("facts", compute, fingerprint=fp)
    else:
        data = compute()

    if stats is not None:
        stats.update({"cached": not computed["ran"], "facts_out": len(data.get("facts") or [])})
        if doc_type == "excel":
            stats["records_in"] = len(rs.records)
        else:
            stats.update(drops)
    return FactSet.from_dict(data)


# --------------------------------------------------------------------------- #
# Excel 경로 — records → facts (코드 결정적, 무 LLM)
# --------------------------------------------------------------------------- #
def _facts_from_records(records: RecordSet) -> FactSet:
    facts: list[Fact] = []
    for rec in records.records:
        entity_name = rec.entity.display_name or (
            rec.entity.path[-1] if rec.entity.path else ""
        )
        # F2 가 이미 attributes 로 정규화 → 그대로 통과(pass-through).
        attrs = dict(rec.attributes)
        facts.append(
            Fact(
                fact_id=f"fact-{rec.record_id}",
                fact_type=_fact_type_of(attrs),
                entity_name=entity_name,
                entity_path=list(rec.entity.path),
                attributes=attrs,
                search_text=_build_search_text(entity_name, rec.entity.path, attrs),
                source={"doc_type": "excel", **rec.source.to_dict()},
                evidence_text=rec.evidence_text,
                confidence=rec.confidence,
            )
        )
    return FactSet(location=records.location, facts=facts)


def _is_number(v: Any) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return True
    if isinstance(v, str):
        try:
            float(v.strip())
            return True
        except ValueError:
            return False
    return False


def _fact_type_of(attrs: dict) -> str:
    """숫자 값 속성이 있으면 정량, (정성만) 있으면 정성 진술, 없으면 서술."""
    if any(_is_number(a.value) for a in attrs.values()):
        return FT_QUANTITATIVE
    if attrs:
        return FT_QUALITATIVE
    return FT_DESCRIPTIVE


def _build_search_text(entity_name: str, entity_path, attributes: dict) -> str:
    """entity + path + 속성 값/단위를 공백 결합(중복 제거). F5 후보 검색용."""
    tokens = [entity_name, *entity_path]
    for a in attributes.values():
        if a.value is not None:
            tokens.append(str(a.value))
        if a.unit:
            tokens.append(a.unit)
    seen: list[str] = []
    for t in tokens:
        t = str(t).strip()
        if t and t not in seen:
            seen.append(t)
    return " ".join(seen)


# --------------------------------------------------------------------------- #
# Word/PPT 경로 — compact 블록/도형 → facts (LLM)
# --------------------------------------------------------------------------- #
def _facts_from_blocks(
    compact: dict,
    profile: Any,
    runner: Any,
    batch_blocks: int,
    drops: Optional[dict] = None,
) -> FactSet:
    doc_type = compact.get("doc_type")
    groups, unit_index = _units_by_group(compact)
    facts: list[Fact] = []
    seq = 0
    seen = 0
    dropped: dict[str, int] = {"not_dict": 0, "no_valid_source_id": 0}
    samples: list[str] = []  # 드롭된 fact 의 entity_name 예시(원인 진단용)
    cited: set[str] = set()  # 실제로 근거로 쓰인 블록 id(커버리지 계측)
    for batch in _pack_batches(groups, batch_blocks):
        batch_ids = {u["id"] for u in batch}
        obj = runner.complete_json(FACT_SYSTEM, build_fact_user(batch, doc_type, profile))
        for raw in obj.get("facts") or []:
            seen += 1
            if not isinstance(raw, dict):
                dropped["not_dict"] += 1
                continue
            # source_ids 를 배치 실제 id 와 교집합만 신뢰(할루시네이션 방지, 결정 F3-7).
            valid_ids = [i for i in (raw.get("source_ids") or []) if i in batch_ids]
            if not valid_ids:
                dropped["no_valid_source_id"] += 1  # 근거 id 가 하나도 없으면 드롭
                if len(samples) < 5:
                    samples.append(str(raw.get("entity_name") or "")[:40])
                continue
            cited.update(valid_ids)
            fact = Fact.from_llm(raw)
            seq += 1
            fact.fact_id = f"fact-{doc_type}-{seq}"
            fact.source = _build_source(doc_type, valid_ids, unit_index)
            fact.search_text = _build_search_text(
                fact.entity_name, fact.entity_path, fact.attributes
            )
            facts.append(fact)
    if drops is not None:
        # 커버리지: 입력 블록 중 **어떤 fact 의 근거로도 인용되지 않은** 블록.
        # LLM 이 애초에 뽑지 않은 내용은 드롭 카운터에 안 잡히는 무증상 손실이라
        # (실측: Word 재실행 때 한 문단이 통째로 누락) 입력 대비로 봐야 보인다.
        uncited = [uid for uid in unit_index if uid not in cited]
        drops.update({
            "llm_facts_seen": seen,
            "dropped_not_dict": dropped["not_dict"],
            "dropped_no_valid_source_id": dropped["no_valid_source_id"],
            "dropped_samples": samples,
            "blocks_in": len(unit_index),
            "blocks_cited": len(cited),
            "blocks_uncited_samples": uncited[:5],
        })
    return FactSet(location=str(compact.get("file_name", "")), facts=facts)


def _units_by_group(compact: dict) -> tuple[list[list[dict]], dict[str, dict]]:
    """compact → (그룹 목록, id→unit 인덱스).

    같은 배치에 묶여야 하는 단위를 그룹으로 만든다: Word 는 블록 1개=1그룹, PPT 는
    슬라이드(도형+스피커노트) 1개=1그룹(본문+주석 병합 유도). unit id 는 source_ids
    검증·복원용 식별자다.
    """
    doc_type = compact.get("doc_type")
    groups: list[list[dict]] = []
    index: dict[str, dict] = {}
    if doc_type == "word":
        for b in compact.get("blocks") or []:
            u = {
                "id": b.get("id"),
                "type": "table" if b.get("type") == "table" else "text",
                "text": b.get("text"),
                "rows": b.get("rows"),
            }
            groups.append([u])
            index[u["id"]] = u
    else:  # ppt
        for s in compact.get("slides") or []:
            slide_no = s.get("slide_no")
            grp: list[dict] = []
            for sh in s.get("shapes") or []:
                uid = f"s{slide_no}-{sh.get('id')}"
                u = {
                    "id": uid,
                    "type": "table" if sh.get("type") == "table" else "text",
                    "text": sh.get("text"),
                    "rows": sh.get("rows"),
                    "slide_no": slide_no,
                    "shape_id": sh.get("id"),
                    "is_note": False,
                }
                grp.append(u)
                index[uid] = u
            if s.get("notes"):
                uid = f"s{slide_no}-notes"
                u = {
                    "id": uid,
                    "type": "text",
                    "text": s.get("notes"),
                    "slide_no": slide_no,
                    "shape_id": None,
                    "is_note": True,
                }
                grp.append(u)
                index[uid] = u
            if grp:
                groups.append(grp)
    return groups, index


def _pack_batches(groups: list[list[dict]], batch_blocks: int) -> list[list[dict]]:
    """그룹을 쪼개지 않고 batch_blocks 이하로 묶는다(그룹 하나가 초과하면 단독 배치)."""
    batches: list[list[dict]] = []
    cur: list[dict] = []
    for grp in groups:
        if cur and len(cur) + len(grp) > batch_blocks:
            batches.append(cur)
            cur = []
        cur.extend(grp)
    if cur:
        batches.append(cur)
    return batches


def _build_source(doc_type: str, valid_ids: list[str], index: dict[str, dict]) -> dict:
    """검증된 id 로 doc_type 별 source 조립(코드가 최종 결정)."""
    if doc_type == "word":
        return {"doc_type": "word", "block_ids": valid_ids}
    units = [index[i] for i in valid_ids]
    slide_no = next((u.get("slide_no") for u in units if u.get("slide_no") is not None), None)
    shape_ids = [u["shape_id"] for u in units if not u.get("is_note") and u.get("shape_id")]
    from_notes = any(u.get("is_note") for u in units)
    return {
        "doc_type": "ppt",
        "slide_no": slide_no,
        "shape_ids": shape_ids,
        "from_notes": from_notes,
    }

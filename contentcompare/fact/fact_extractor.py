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

from ..llm.tracing import substage
from .fact_models import Fact, FactSet
from .fact_types import FT_DESCRIPTIVE, FT_QUALITATIVE, FT_QUANTITATIVE
from .llm_stage import fingerprint_for
from .prompts import FACT_SYSTEM, FACT_VERSION, build_fact_user
from .record_models import RecordSet
from .validator import _NUM_RE  # 수치 판정을 numeric_coverage 와 한 곳에 묶는다


# search_text 보강 모드. config 의 ``fact.search_text_augment`` 가 그대로 들어온다.
OFF, NUMBERS, FULL = "off", "numbers", "full"
AUGMENT_MODES = (OFF, NUMBERS, FULL)


def extract_facts(
    compact: dict,
    *,
    records: Optional[RecordSet] = None,
    profile: Any = None,
    runner: Any = None,
    store: Any = None,
    batch_blocks: int = 20,
    stats: Optional[dict] = None,
    lines_by_block: Optional[dict] = None,
    search_text_augment: str = OFF,
) -> FactSet:
    """compact(+records) → ``FactSet``. doc_type 으로 Excel(코드)/Word·PPT(LLM) 분기.

    ``store`` 가 있으면 ``facts`` 단계를 캐싱(같은 입력이면 재계산/재호출 0).

    ``stats`` 를 주면 계측값을 채운다(out-param, F3.5). Word/PPT 경로는 근거 없는
    fact 를 조용히 버리는데(§ ``_facts_from_blocks``), 이 드롭은 곧 대상 문서의
    fact 누락 → F5 의 ``missing`` 오판으로 이어지므로 **사유별로 센다**.

    ``lines_by_block`` 은 ``physical_raw`` 다(Word 전용). ``build_facts_by_block`` 이
    쓰는 것과 같은 인자이며, **캐시 지문에 반드시 섞는다** — 안 섞으면 같은 compact +
    다른 줄 정보인 두 실행이 캐시를 공유해 옛 결과를 준다.
    """
    doc_type = compact.get("doc_type")
    computed = {"ran": False}
    drops: dict[str, Any] = {}
    if doc_type == "excel":
        rs = records if records is not None else RecordSet()

        def compute() -> dict:
            computed["ran"] = True
            return _facts_from_records(rs, search_text_augment).to_dict()

        payload = json.dumps(rs.to_dict(), ensure_ascii=False) + _augment_key(search_text_augment)
        fp = fingerprint_for(payload) if store else None
    else:

        def compute() -> dict:
            computed["ran"] = True
            return _facts_from_blocks(
                compact, profile, runner, batch_blocks, drops,
                lines_by_block=lines_by_block, augment=search_text_augment,
            ).to_dict()

        lines_payload = _lines_index(lines_by_block)
        payload = json.dumps(compact, ensure_ascii=False)
        if lines_payload:
            payload += json.dumps(lines_payload, ensure_ascii=False, sort_keys=True)
        payload += _augment_key(search_text_augment)
        fp = fingerprint_for(payload, FACT_VERSION) if store else None

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
def _facts_from_records(records: RecordSet, augment: str = OFF) -> FactSet:
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
                search_text=_build_search_text(
                    entity_name, rec.entity.path, attrs,
                    rec.evidence_text, augment),
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


def _build_search_text(entity_name: str, entity_path, attributes: dict,
                       evidence_text: str = "", augment: str = OFF) -> str:
    """entity + path + 속성 값/단위를 공백 결합(중복 제거). F5 후보 검색용.

    ``augment`` 는 **근거 원문으로 이 문자열을 보강할지**를 정한다. 기본 ``off`` 는
    기존 동작 그대로다.

    보강이 필요한 이유는 이 함수가 원문이 아니라 **속성에서 재조립**하기 때문이다.
    속성 이름은 넣지 않고 값이 ``None`` 이면 건너뛰므로, F3 가 속성 칸만 만들고 값을
    채우지 않으면 그 fact 의 검색 문자열에는 **숫자가 하나도 없다**. 같은 표의 옆
    항목은 값이 채워져 숫자가 들어가므로 순위 경쟁이 조용히 기울어진다(실측: 정답
    후보가 16위로 밀렸다가 근거 원문을 붙이자 1위).

    ``numbers`` 는 그 구멍만 메우고(수치가 하나도 없는 fact 에 근거의 숫자만),
    ``full`` 은 근거 전문을 붙인다. 둘을 가른 것은 ``-5~5℃`` 처럼 **값·단위가 붙은
    형태**가 통째로 살아 있느냐가 검색 정확도를 가르기 때문이다 — ``numbers`` 는 그
    형태를 ``-5``/``5`` 로 분해한다. 어느 쪽이 이득인지는 데이터마다 다르므로
    ``scripts/sweep_recall.py`` 로 재고 정할 것.

    ``fact_text`` 의 ``search_text or evidence_text`` 폴백으로는 이 문제를 못 잡는다 —
    그 폴백은 ``search_text`` 가 **완전히 빈 문자열**일 때만 걸리고, 여기서는 이름과
    단위가 남아 비어 있지 않다.
    """
    tokens = [entity_name, *entity_path]
    for a in attributes.values():
        if a.value is not None:
            tokens.append(str(a.value))
        if a.unit:
            tokens.append(a.unit)
    extra = _augment_tokens(attributes, evidence_text, augment)
    if extra:
        tokens.append(extra)
    seen: list[str] = []
    for t in tokens:
        t = str(t).strip()
        if t and t not in seen:
            seen.append(t)
    return " ".join(seen)


def _augment_key(augment: str) -> str:
    """캐시 지문에 섞을 조각. ``off`` 는 빈 문자열이라 **기존 캐시가 그대로 유효**하다.

    이걸 빼먹으면 모드를 바꿔도 ``facts`` 단계가 캐시 히트라 옛 문자열이 그대로 나온다 —
    실험이 조용히 아무 일도 안 하게 되는 최악의 실패다.
    """
    return "" if augment == OFF else f"|search_text_augment={augment}"


def _augment_tokens(attributes: dict, evidence_text: str, augment: str) -> str:
    """보강분 문자열. 모드가 ``off`` 이거나 붙일 것이 없으면 빈 문자열."""
    if augment == OFF or not evidence_text:
        return ""
    if augment == FULL:
        return evidence_text
    if augment != NUMBERS:
        return ""
    # 수치가 이미 있으면 손대지 않는다 — validator.numeric_coverage 와 같은 판정이다.
    # 두 곳이 갈리면 "경고는 뜨는데 보강은 안 되는" 짝이 생긴다.
    if any(_NUM_RE.search(str(a.value)) for a in (attributes or {}).values()):
        return ""
    return " ".join(_NUM_RE.findall(evidence_text))


# --------------------------------------------------------------------------- #
# Word/PPT 경로 — compact 블록/도형 → facts (LLM)
# --------------------------------------------------------------------------- #
def _facts_from_blocks(
    compact: dict,
    profile: Any,
    runner: Any,
    batch_blocks: int,
    drops: Optional[dict] = None,
    *,
    lines_by_block: Optional[dict] = None,
    augment: str = OFF,
) -> FactSet:
    doc_type = compact.get("doc_type")
    groups, unit_index = _units_by_group(compact, lines_by_block=lines_by_block)
    facts: list[Fact] = []
    seq = 0
    seen = 0
    inherited = 0
    dropped: dict[str, int] = {"not_dict": 0, "no_valid_source_id": 0}
    samples: list[str] = []  # 드롭된 fact 의 entity_name 예시(원인 진단용)
    cited: set[str] = set()  # 실제로 근거로 쓰인 블록 id(커버리지 계측)
    batches = _with_context(_pack_batches(groups, batch_blocks))
    for index, batch in enumerate(batches, start=1):
        # 맥락 블록은 근거 id 로 인정하지 않는다 — 중복 fact 를 원천 차단한다.
        batch_ids = {u["id"] for u in batch if not u.get("context")}
        # Word/PPT 도 Excel 과 같은 이유로 배치 번호를 남긴다(record_normalizer 참고).
        with substage(f"배치 {index}/{len(batches)}", blocks=len(batch_ids)):
            obj = runner.complete_json(
                FACT_SYSTEM, build_fact_user(batch, doc_type, profile)
            )
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
                fact.entity_name, fact.entity_path, fact.attributes,
                fact.evidence_text, augment,
            )
            inherited += 1 if fact.inherited_from else 0
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
            "facts_inherited": inherited,
        })
    return FactSet(location=str(compact.get("file_name", "")), facts=facts)


def _lines_index(raw: Optional[dict]) -> dict[str, dict]:
    """``physical_raw`` → ``{block_id: {lines, indent, cell_lines}}``.

    **F3 렌더가 실제로 쓰는 것만 추린다.** 이 값이 캐시 지문에 들어가므로, 렌더에
    안 쓰는 필드까지 넣으면 무관한 변경에도 전 문서가 재추출된다.

    줄이 1개뿐인 블록은 아예 담지 않는다 — 렌더가 그때는 기존 한 줄 형태를 쓰므로
    지문에 넣을 이유가 없다.
    """
    out: dict[str, dict] = {}
    for b in (raw or {}).get("blocks") or []:
        bid = str(b.get("block_id") or "")
        if not bid:
            continue
        entry: dict[str, Any] = {}
        lines = [
            {"raw_text": str(l.get("raw_text") or ""), "indent": int(l.get("indent") or 0)}
            for l in (b.get("lines") or [])
        ]
        if len(lines) > 1:
            entry["lines"] = lines
        if b.get("indent"):
            entry["indent"] = int(b["indent"])
        if b.get("cell_lines"):
            entry["cell_lines"] = b["cell_lines"]
        if entry:
            out[bid] = entry
    return out


def _units_by_group(
    compact: dict, lines_by_block: Optional[dict] = None
) -> tuple[list[list[dict]], dict[str, dict]]:
    """compact → (그룹 목록, id→unit 인덱스).

    같은 배치에 묶여야 하는 단위를 그룹으로 만든다: Word 는 블록 1개=1그룹, PPT 는
    슬라이드(도형+스피커노트) 1개=1그룹(본문+주석 병합 유도). unit id 는 source_ids
    검증·복원용 식별자다.

    ``lines_by_block`` 에 ``physical_raw`` 를 주면 Word unit 에 줄 구조를 얹는다
    (``lines``/``indent``/``cell_lines``). 안 주면 예전 그대로다 — PPT·Excel·옛
    산출물 경로가 무변경이어야 한다.
    """
    doc_type = compact.get("doc_type")
    groups: list[list[dict]] = []
    index: dict[str, dict] = {}
    if doc_type == "word":
        index_lines = _lines_index(lines_by_block)
        for b in compact.get("blocks") or []:
            u = {
                "id": b.get("id"),
                "type": "table" if b.get("type") == "table" else "text",
                "text": b.get("text"),
                "rows": b.get("rows"),
            }
            u.update(index_lines.get(str(b.get("id") or ""), {}))
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


_CONTEXT_BLOCKS = 3
"""배치 앞에 붙일 직전 배치의 꼬리 블록 수.

배치가 20개씩 겹침 없이 잘리므로 **20블록마다 한 번씩** 의미가 경계에서 갈린다.
겹쳐 넣고 사후에 중복을 지우는 방식은 쓰지 않는다 — 중복 판정 기준이 또 하나의
추측이 되기 때문이다. 대신 맥락 블록을 ``batch_ids`` 에서 빼서 원천적으로 막는다.
"""

_CONTEXT_TABLE_ROWS = 2
"""맥락으로 실을 표의 최대 행 수. 표 하나가 배치 토큰을 통째로 삼키는 것을 막는다."""


def _with_context(batches: list[list[dict]]) -> list[list[dict]]:
    """각 배치 앞에 직전 배치의 꼬리 블록을 ``context`` 표시로 덧붙인다.

    원본 unit dict 를 건드리지 않고 **얕은 복사**를 붙인다 — 원본에 표시를 찍으면
    그 블록이 자기 배치에서도 맥락으로 렌더돼 fact 가 통째로 사라진다.
    """
    out: list[list[dict]] = []
    for i, batch in enumerate(batches):
        if i == 0:
            out.append(list(batch))
            continue
        tail = batches[i - 1][-_CONTEXT_BLOCKS:]
        out.append([_as_context(u) for u in tail] + list(batch))
    return out


def _as_context(u: dict) -> dict:
    """맥락용 사본. 표는 앞 몇 행만 남긴다."""
    ctx = dict(u)
    ctx["context"] = True
    if ctx.get("type") == "table":
        if ctx.get("rows"):
            ctx["rows"] = ctx["rows"][:_CONTEXT_TABLE_ROWS]
        if ctx.get("cell_lines"):
            ctx["cell_lines"] = ctx["cell_lines"][:_CONTEXT_TABLE_ROWS]
    return ctx


# --------------------------------------------------------------------------- #
# 블록 ↔ fact 매핑 (진단 계측)
# --------------------------------------------------------------------------- #
def build_facts_by_block(
    compact: dict,
    facts: FactSet,
    stats: Optional[dict] = None,
    *,
    lines_by_block: Optional[dict] = None,
) -> dict:
    """블록/행 → 그것을 근거로 삼은 fact id 목록. **오판 추적이 목적이다.**

    Word/PPT 는 ``document_profile`` 에서 ``facts`` 로 직행하므로 "어떤 블록이 왜
    fact 가 되지 못했는가"를 볼 자리가 없었다. 인용되지 않은 블록(``cited=False``)이
    곧 F3 추출 누락 후보이고, 그것이 F5 의 ``missing`` 오판으로 이어진다.

    **추출을 다시 돌리지 않고 결과에서 역산한다** — 실제 운영은 캐시가 켜져 있어
    :func:`extract_facts` 가 계산을 건너뛰는 것이 기본 경로이기 때문이다. 그때도
    매핑은 남아야 한다(드롭 사유만 비어 있다).

    Excel 도 같은 스키마로 낸다(행 하나 = 블록 하나). 뷰어가 doc_type 분기를 하나
    덜 하게 하려는 것이다.

    ``lines_by_block`` 에 ``physical_raw`` 를 주면 **줄 단위 커버리지**를 함께 낸다.
    블록 단위만으로는 한 문단에 조건이 넷인데 fact 가 첫 줄만 인용해도 ``cited=True``
    로 보여, 나머지 셋이 버려진 것을 알 수 없다(설계 §12.3). 안 주면 예전 스키마
    그대로다 — Excel/PPT 처럼 블록이 이미 최소 단위인 문서는 줄 개념이 없다.
    """
    doc_type = str(compact.get("doc_type") or "")
    blocks = _blocks_of(compact, doc_type)
    lines_map = _lines_of(lines_by_block)

    by_block: dict[str, list[str]] = {b["id"]: [] for b in blocks}
    orphan: list[str] = []  # 블록 목록에 없는 id 를 가리키는 fact(스키마 불일치 신호)
    for fact in facts.facts:
        ids = _source_block_ids(fact.source, doc_type)
        if not ids:
            orphan.append(fact.fact_id)
        for bid in ids:
            if bid in by_block:
                by_block[bid].append(fact.fact_id)
            else:
                orphan.append(fact.fact_id)

    evidence_by_id = {f.fact_id: _norm(f.evidence_text) for f in facts.facts}
    rows = []
    for b in blocks:
        row: dict[str, Any] = {
            "id": b["id"],
            "kind": b["kind"],
            "preview": b["preview"],
            "fact_ids": by_block[b["id"]],
            "cited": bool(by_block[b["id"]]),
        }
        row.update(_line_coverage(lines_map.get(b["id"]), row["fact_ids"], evidence_by_id))
        rows.append(row)

    summary: dict[str, Any] = {
        "blocks_in": len(rows),
        "blocks_cited": sum(1 for r in rows if r["cited"]),
        "facts_out": len(facts.facts),
        "facts_without_block": sorted(set(orphan)),
    }
    measured = [r for r in rows if "units_in" in r]
    if measured:
        # 줄이 있는 블록과 표(cell_lines)를 분모에 넣는다. 한 셀에 조건표가 통째로
        # 들어간 문서가 "표는 블록이 이미 최소 단위"라는 가정을 반박했다(설계 §8.2).
        # Excel 행은 줄이 없어 분모에 들어가지 않는다.
        summary["units_in"] = sum(r["units_in"] for r in measured)
        summary["units_linked"] = sum(r["units_linked"] for r in measured)
        summary["units_uncited"] = sum(r["units_uncited"] for r in measured)
    # extract_facts 가 채운 계측(cached / 드롭 사유별 카운트)을 그대로 얹는다.
    if stats:
        summary.update(stats)
    return {"doc_type": doc_type, "location": facts.location, "blocks": rows,
            "summary": summary}


def _norm(text: Any) -> str:
    """대조용 정규화 — 공백만 병합한다(대소문자·기호는 건드리지 않는다)."""
    return " ".join(str(text or "").split())


def _lines_of(raw: Optional[dict]) -> dict[str, list[dict]]:
    """``physical_raw`` → ``{block_id: [{line_id, raw_text}, …]}``.

    문단은 ``lines``, 표는 ``cell_lines`` 를 편다. 표를 분모에서 빼던 예전 근거는
    *"표는 블록이 이미 최소 단위"* 였는데, **한 셀에 조건표가 통째로 들어간 문서가
    그 가정을 반박했다**(설계 §8.2). 줄이 하나뿐인 셀은 담지 않는다.
    """
    out: dict[str, list[dict]] = {}
    for b in (raw or {}).get("blocks") or []:
        bid = str(b.get("block_id") or "")
        if not bid:
            continue
        lines = b.get("lines")
        if lines:
            out[bid] = list(lines)
            continue
        units: list[dict] = []
        for r, row in enumerate(b.get("cell_lines") or [], start=1):
            for c, cell in enumerate(row or [], start=1):
                for i, text in enumerate(cell or [], start=1):
                    units.append({"line_id": f"{bid}:r{r:02d}c{c:02d}l{i:02d}",
                                  "raw_text": text})
        if units:
            out[bid] = units
    return out


_MIN_FRAGMENT = 3
"""이보다 짧은 조각은 대조하지 않는다 — 아무 줄에나 걸려 인용을 부풀린다."""


def _line_coverage(
    lines: Optional[list[dict]], fact_ids: list[str], evidence_by_id: dict[str, str]
) -> dict[str, Any]:
    """이 블록의 각 줄이 어느 fact 의 근거로 쓰였는지. 줄이 없으면 빈 dict.

    후보는 **이 블록을 근거로 든 fact 로 한정**한다. 그러지 않으면 다른 블록의
    비슷한 문장이 남의 줄을 켜서, 정작 찾으려는 누락이 가려진다.

    ⚠️ 대조는 양방향 부분일치다(줄 ⊆ 근거 · 근거 ⊆ 줄). 후자는 "LLM 이 줄 일부만
    따온 경우"를 잡으려는 것인데, 짧은 조각이 여러 줄에 걸리면 인용을 **부풀린다** —
    누락 탐지에서는 위험한 방향이라 :data:`_MIN_FRAGMENT` 로 막는다.
    """
    if not lines:
        return {}
    quoted = [(fid, evidence_by_id.get(fid, "")) for fid in fact_ids]
    out = []
    linked = 0
    for line in lines:
        text = _norm(line.get("raw_text"))
        hits = [fid for fid, ev in quoted if _overlaps(text, ev)]
        linked += bool(hits)
        out.append({
            "line_id": line.get("line_id", ""),
            "preview": text[:_PREVIEW_CHARS],
            "fact_ids": hits,
            "cited": bool(hits),
        })
    return {
        "lines": out,
        "units_in": len(out),
        "units_linked": linked,
        "units_uncited": len(out) - linked,
    }


def _overlaps(line: str, evidence: str) -> bool:
    if not line or not evidence:
        return False  # 근거 원문이 없으면 어느 줄에서 왔는지 추측하지 않는다
    if min(len(line), len(evidence)) < _MIN_FRAGMENT:
        return False
    return line in evidence or evidence in line


_PREVIEW_CHARS = 80


def _blocks_of(compact: dict, doc_type: str) -> list[dict]:
    """문서를 ``{id, kind, preview}`` 목록으로 편다(문서 순서 유지).

    Word/PPT 는 :func:`_units_by_group` 의 인덱스를 그대로 쓴다 — 추출이 실제로 본
    단위와 **같은 id 체계**여야 매핑이 성립하기 때문이다.
    """
    if doc_type == "excel":
        out: list[dict] = []
        for sheet in compact.get("sheets") or []:
            for row in sheet.get("rows") or []:
                cells = row.get("cells") or {}
                out.append({
                    "id": f"row-{row.get('r')}",
                    "kind": "row",
                    "preview": _preview(" | ".join(str(v) for v in cells.values())),
                })
        return out

    _groups, index = _units_by_group(compact)
    return [
        {
            "id": uid,
            "kind": "table" if unit.get("type") == "table" else (
                "notes" if unit.get("is_note") else "text"),
            "preview": _preview(_unit_text(unit)),
        }
        for uid, unit in index.items()
    ]


def _unit_text(unit: dict) -> str:
    if unit.get("rows"):
        return " | ".join(str(c) for row in unit["rows"] for c in row)
    return str(unit.get("text") or "")


def _preview(text: str) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= _PREVIEW_CHARS else flat[:_PREVIEW_CHARS] + "…"


def _source_block_ids(source: dict, doc_type: str) -> list[str]:
    """fact 의 ``source`` 를 :func:`_blocks_of` 의 id 로 되돌린다.

    ``source`` 는 doc_type 별로 키가 완전히 다르므로(설계 §4.3) 분기가 필요하다.
    """
    source = source if isinstance(source, dict) else {}
    kind = str(source.get("doc_type") or doc_type)
    if kind == "excel":
        row = source.get("row")
        return [f"row-{row}"] if row is not None else []
    if kind == "word":
        return [str(i) for i in (source.get("block_ids") or [])]
    if kind == "ppt":
        slide_no = source.get("slide_no")
        if slide_no is None:
            return []
        ids = [f"s{slide_no}-{sid}" for sid in (source.get("shape_ids") or [])]
        if source.get("from_notes"):
            ids.append(f"s{slide_no}-notes")
        return ids
    return []


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

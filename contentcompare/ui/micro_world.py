"""파이프라인 현미경 — 실행 산출물을 **자체포함 HTML** 로 그린다.

두 가지 모드가 있다:

- **학습**: 기준 fact 하나를 골라 F0→F6 을 실제 산출물 조각으로 따라간다.
  "이 코드가 무슨 일을 하는가"를 문서가 아니라 **이번 실행의 데이터**로 설명한다.
- **디버깅**: ``⚪ 대상에 없음`` 하나를 골라 원인까지 내려간다
  (:mod:`contentcompare.fact.missing_trace` 가 판단하고 여기서는 그리기만 한다).

설계 제약 셋:

1. **streamlit 을 import 하지 않는다.** 여기까지가 순수 함수라서 브라우저 없이
   pytest 로 검증할 수 있다(``ui/runner.py`` 의 분리 원칙을 시각화까지 확장).
2. **외부 CDN 0.** 사내망 오프라인에서 열려야 하고, ``docs/understanding/*.html``
   의 확립된 규약이기도 하다. CSS 는 :mod:`contentcompare.ui.diagram` 것을 그대로 쓴다.
3. **원문은 반드시 이스케이프.** 문서 본문에 ``<``·``&``·``</script>`` 가 실제로
   들어온다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

from ..fact.artifact_reader import (
    RunSnapshot,
    attributes_text,
    low_confidence_ids,
    target_of,
)
from ..fact.missing_trace import MissingTrace, summarize, trace_all_missing
from ..report.fact_report import LABEL, ORDER, format_source
from . import diagram as dv
from .graph_layout import bipartite_layout, concept_rows, focus_layout

# 판정 색 — 라벨 자체는 리포트에서 가져온다(문구가 갈라지면 사람이 대조할 수 없다).
_TONE = {"match": "ok", "mismatch": "bad", "missing": "gray", "unknown": "amber"}

RESULT_LABEL = {k: (LABEL[k], _TONE[k]) for k in LABEL}
RESULT_ORDER = ORDER
"""확인이 필요한 것부터 — 리포트의 정렬 철학을 그대로 쓴다."""

# 학습 모드가 훑는 단계(문서 유형에 없으면 흐리게 그린다).
PIPE_ITEMS = [
    ("F0", "있는 그대로", "c", "physical_raw.json"),
    ("F0", "압축", "c", "compact_raw.json"),
    ("F1", "이 문서 뭐야?", "l", "document_profile.json"),
    ("F1", "표·열의 뜻", "l", "table_profile.json<br>column_schema.json"),
    ("F2", "행 → 레코드", "l", "records.json"),
    ("F3", "카드 만들기", "cl", "facts.json"),
    ("F4a", "카드 검사", "c", "validation_report.json"),
    ("F7", "짝 찾기", "elh", "concept_graph.json"),
    ("F5", "값 대조", "cl", "comparison_result.json"),
    ("F6", "리포트", "c", "report.md"),
]

EXCEL_ONLY = {"table_profile", "records"}

_MAX_HEIGHT = 4000


@dataclass
class RenderedHtml:
    """iframe 에 넣을 문자열과 권장 높이."""

    html: str
    height: int = 900
    notes: list[str] = field(default_factory=list)

    def __str__(self) -> str:  # pragma: no cover - 편의
        return self.html


# --------------------------------------------------------------------------- #
# 공통 껍데기
# --------------------------------------------------------------------------- #
def _page(body: str, *, theme: str = "auto") -> str:
    """자체포함 HTML 한 장. 외부 리소스를 하나도 참조하지 않는다."""
    forced = ""
    if theme == "dark":
        forced = ".dv{--dv-fg:#e8e8e4;--dv-mut:#a2a29c;--dv-line:#3a3d44;" \
                 "--dv-card:#1f2229;--dv-bg:#191c21}body{background:transparent}"
    elif theme == "light":
        forced = ".dv{--dv-fg:#1c1c1a;--dv-mut:#6b6b66;--dv-line:#dcdcd6;" \
                 "--dv-card:#fff;--dv-bg:#f6f6f4}body{background:transparent}"
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        f"<style>{dv.CSS}\n"
        "body{margin:0;padding:4px 2px;background:transparent;"
        'font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans KR",sans-serif}'
        "details{margin:6px 0}summary{cursor:pointer;font-size:.8rem;color:var(--dv-mut)}"
        "summary:hover{color:var(--dv-fg)}"
        f"{_MW_CSS}{forced}</style></head><body>{body}</body></html>"
    )


# 현미경 전용 CSS. 공용 컴포넌트가 아니므로 ``diagram.CSS`` 에 넣지 않는다.
_MW_CSS = (
    ".mw-hidden{display:none}"
    # --- 판정 표 인라인 확장 ---
    # 펼친 행을 **색으로** 표시한다. 여러 개를 펼쳐도 어느 것이 열려 있는지 한눈에 보여야
    # 한다 — 카드가 행 바로 아래 있어도 카드가 길면 행이 화면 밖으로 나가기 때문이다.
    ".mw-row{cursor:pointer}"
    ".mw-row:hover>td{background:var(--dv-bg)}"
    ".mw-row.mw-open>td{background:var(--dv-code-bg);font-weight:700}"
    ".mw-row.mw-open>td:first-child{box-shadow:inset 3px 0 0 var(--dv-code)}"
    ".mw-caret{display:inline-block;width:1em;color:var(--dv-mut)}"
    ".mw-row.mw-open .mw-caret{color:var(--dv-code)}"
    ".mw-detail>td{background:var(--dv-bg);padding:0}"
    ".mw-detail .dv-bd{padding:12px 14px}"
    # --- 그래프 색 토글 ---
    ".mw-legend{display:flex;gap:7px;flex-wrap:wrap;margin:0 0 10px}"
    ".mw-t{display:inline-flex;align-items:center;gap:6px;cursor:pointer;"
    "border:1px solid var(--dv-line);background:var(--dv-card);color:var(--dv-fg);"
    "border-radius:99px;padding:4px 12px;font-size:.76rem;font-weight:700;"
    'font-family:inherit}'
    ".mw-t:hover{border-color:var(--dv-mut)}"
    ".mw-t .mw-dot{width:10px;height:10px;border-radius:50%;border:2px solid currentColor}"
    '.mw-t[aria-pressed="true"] .mw-dot{background:currentColor}'
    '.mw-t[aria-pressed="false"]{opacity:.55}'
    '.mw-t[aria-pressed="false"] .mw-lbl{text-decoration:line-through}'
    ".mw-t[disabled]{opacity:.3;cursor:default}"
    ".mw-t .mw-n{font-variant-numeric:tabular-nums;color:var(--dv-mut)}"
    # 톤별 표시/숨김은 **컨테이너 클래스 하나**로 집행한다 — path 가 수백 개여도 즉시
    # 반영되고, 개별 style 을 건드리지 않아 원본 마크업이 그대로 남는다.
    ".mw-off-ok .mw-e-ok{display:none}"
    ".mw-off-amber .mw-e-amber{display:none}"
    ".mw-off-bad .mw-e-bad{display:none}"
    ".mw-off-gray .mw-e-gray{display:none}"
)


def _empty(message: str, hint: str = "") -> RenderedHtml:
    body = ('<div class="dv"><div class="dv-title">표시할 것이 없습니다</div>'
            f'<div class="dv-sub">{dv.esc(message)}</div>')
    if hint:
        body += f'<div class="dv-cap">{dv.esc(hint)}</div>'
    body += "</div>"
    return RenderedHtml(html=_page(body), height=220)


def _problems(snap: RunSnapshot) -> str:
    if not snap.problems:
        return ""
    items = "".join(f"<li>{dv.esc(p)}</li>" for p in snap.problems)
    return ('<div class="dv"><div class="dv-title">⚠ 이 실행에서 확인할 수 없는 것</div>'
            f'<ul style="font-size:.83rem;color:var(--dv-mut)">{items}</ul></div>')


def _result_pill(result: str) -> str:
    label, tone = RESULT_LABEL.get(result, (result or "?", "gray"))
    return dv.pill(label, tone)


def _sorted_comparisons(comparisons: list[dict]) -> list[dict]:
    rank = {r: i for i, r in enumerate(RESULT_ORDER)}
    return sorted(
        comparisons,
        key=lambda c: (rank.get(str(c.get("result")), 9),
                       str(c.get("target_doc")), str(c.get("entity_name"))),
    )


def _json_block(data: Any, limit: int = 1600) -> str:
    text = json.dumps(data, ensure_ascii=False, indent=2) if not isinstance(data, str) else data
    if len(text) > limit:
        text = text[:limit] + f"\n… ({len(text) - limit}자 더)"
    return dv.data_block(text)


# --------------------------------------------------------------------------- #
# 학습 모드
# --------------------------------------------------------------------------- #
def render_learn_html(
    snap: RunSnapshot,
    *,
    doc_name: str = "",
    fact_id: str = "",
    theme: str = "auto",
) -> RenderedHtml:
    """기준 fact 하나가 F0→F6 을 지나는 길을 실제 산출물로 보여 준다."""
    doc_name = doc_name or snap.reference_doc
    doc = snap.doc(doc_name)
    if doc is None:
        return _empty(f"'{doc_name}' 의 산출물 폴더가 없습니다.",
                      "스냅샷(_runs/*)에는 단계별 산출물이 없습니다.")

    facts = snap.facts_of(doc_name)
    if not facts:
        return _empty(f"'{doc_name}' 에 facts.json 이 없습니다.",
                      "fact.save_artifacts 가 켜진 실행인지 확인하세요.")
    fact = facts.get(fact_id) or next(iter(facts.values()))

    parts = [
        _pipe_section(snap, doc),
        _journey_section(snap, doc, fact),
        _fates_section(snap, fact),
    ]
    body = "\n".join(p for p in parts if p)
    height = min(_MAX_HEIGHT, 700 + 260 * len(snap.target_docs))
    return RenderedHtml(html=_page(body, theme=theme), height=height)


def _pipe_section(snap: RunSnapshot, doc) -> str:
    """이번 실행이 실제로 지난 단계만 진하게 — Word/PPT 의 빈칸이 그림으로 설명된다."""
    stats = doc.load("run_stats") or {}
    stages = set(stats.get("stages") or [])
    stages |= {s for s in ("concept_graph", "comparison_result")
               if snap.reference and s in snap.reference.available}
    dimmed = EXCEL_ONLY - stages
    return dv.pipemap(
        PIPE_ITEMS, active=stages, dimmed=dimmed,
        title=f"이 실행이 지난 단계 — {dv.esc(doc.doc_name)}"
              f' <span class="dv-tag">{dv.esc(doc.doc_type or "?")}</span>',
        sub="진한 칸이 실제로 돌아간 단계다. 흐린 칸은 <b>이 문서 유형에 해당하지 않는</b> 단계 "
            "— Excel 만 표·열 해석과 레코드 정규화를 거친다.",
        cap="모든 중간 산출물이 파일로 남는 것이 이 설계의 핵심이다. 틀렸을 때 어느 단계에서 "
            "틀렸는지 열어볼 수 있어야 하기 때문이다.",
    )


def _journey_section(snap: RunSnapshot, doc, fact: dict) -> str:
    """fact 하나의 여정 — 각 단계 산출물에서 **그 fact 에 해당하는 조각만** 꺼낸다."""
    name = str(fact.get("entity_name") or "")
    fid = str(fact.get("fact_id") or "")
    source = fact.get("source") if isinstance(fact.get("source"), dict) else {}
    stops: list[dict] = []

    compact = doc.load("compact_raw")
    if compact:
        stops.append(dict(
            k="f", n="F0", nm="압축 원문", fn="compact_raw.json",
            out=_slice_compact(compact, source),
            note="문서를 <b>해석 없이</b> 꺼낸 뒤 비교에 쓸모없는 서식을 걷어낸 것. "
                 "여기까지는 판단이 하나도 섞이지 않는다."))

    profile = doc.load("document_profile")
    if profile:
        stops.append(dict(
            k="l", n="F1", nm="문서 프로파일", fn="document_profile.json",
            inp="compact_raw 요약", out=json.dumps(profile, ensure_ascii=False, indent=2)[:700],
            note="LLM 이 처음 등장하는 자리. <b>문서당 한 번만</b> 묻는다."))

    schema = doc.load("column_schema")
    if schema:
        stops.append(dict(
            k="l", n="F1", nm="열의 뜻", fn="column_schema.json",
            inp="헤더 근처 (표당 1회)",
            out=json.dumps(schema.get("columns") or [], ensure_ascii=False, indent=2)[:700],
            note="이 답을 표의 <b>모든 행에 재사용</b>한다 — 비용이 여기서 결정된다."))

    record = _find_record(doc, fid)
    if record:
        stops.append(dict(
            k="l", n="F2", nm="행 → 레코드", fn="records.json",
            inp="행의 셀들 + 열의 뜻",
            out=json.dumps(record, ensure_ascii=False, indent=2),
            note="셀 값이 <b>이름 붙은 속성</b>이 됐다. 좌표는 LLM 이 아니라 "
                 "<b>코드가 채운다</b>(지어낼 수 없게)."))

    stops.append(dict(
        k="c" if doc.doc_type == "excel" else "l", n="F3", nm="카드(fact)", fn="facts.json",
        inp="records.json" if doc.doc_type == "excel" else "블록/도형 묶음",
        out=json.dumps(fact, ensure_ascii=False, indent=2),
        note=("Excel 은 규칙 매핑이라 <b>LLM 을 쓰지 않는다</b>. Word/PPT 는 줄글이라 "
              "이 자리에서 LLM 이 카드를 뽑는다.")))

    checks = _checks_for(doc, fid)
    stops.append(dict(
        k="c", n="F4a", nm="카드 검사", fn="validation_report.json",
        inp=f"{fid} 의 속성·근거·좌표",
        out=json.dumps(checks, ensure_ascii=False, indent=2) if checks
            else "이 카드에 걸린 지적 없음",
        note="문제가 있어도 <b>버리지 않고 표시만</b> 한다 — 버리면 사람이 확인할 기회가 사라진다."))

    node = snap.index.node_of(doc.doc_name, fid)
    edges = snap.index.edges_touching(doc.doc_name, fid)
    if snap.graph is not None:
        stops.append(dict(
            k="e", n="F7", nm="짝 찾기(개념)", fn="concept_graph.json",
            inp="유사도로 좁힌 후보 쌍",
            out=_edges_text(node, edges),
            note="유사도는 <b>후보를 좁히는 데만</b> 쓴다. 판정 규칙은 하나 — "
                 "개념이 <code>same_as</code> 로 이어져 있지 않으면 비교하지 않는다."))

    return dv.journey(
        stops,
        title=f"추적 여행 — <code>{dv.esc(name)}</code> 한 장이 겪는 일",
        sub=f"<code>{dv.esc(fid)}</code> 를 각 단계 산출물에서 꺼내 이어 붙인 것이다. "
            "지어낸 데이터가 하나도 없다.",
        cap="여기까지는 <b>모든 대상 문서에 공통</b>이다. 갈라지는 것은 짝을 찾는 순간부터다.",
        legend_keys=("f", "c", "l", "e"),
    )


def _fates_section(snap: RunSnapshot, fact: dict) -> str:
    """같은 카드가 대상 문서마다 어떤 길을 갔는가."""
    fid = str(fact.get("fact_id") or "")
    name = str(fact.get("entity_name") or "")
    cards = []
    for doc_name in snap.target_docs:
        comp = next(
            (c for c in snap.comparisons
             if c.get("target_doc") == doc_name
             and (c.get("reference") or {}).get("fact_id") == fid),
            None)
        if comp is None:
            continue
        cards.append(_fate_card(snap, doc_name, comp, fid))
    if not cards:
        return ""
    return dv.fates(
        cards,
        title="같은 카드 하나, 대상 문서마다 다른 길",
        sub=f"<code>{dv.esc(name)}</code> 가 대상 문서에 따라 <b>완전히 다른 경로</b>를 간다. "
            "판정을 누가 내렸는지에 주목하자.",
        cap="이름이 똑같으면 코드가 공짜로 끝내고, 이름이 다를수록 LLM 이 개입하고 검문소가 걸린다. "
            "<b>언어가 다른 문서가 어려운 이유가 여기에 있다</b> — 이름 일치가 절대 성립하지 않아 "
            "항상 가장 험한 길로만 간다.",
    )


def _fate_card(snap: RunSnapshot, doc_name: str, comp: dict, fid: str) -> dict:
    result = str(comp.get("result") or "")
    tone = RESULT_LABEL.get(result, ("", "gray"))[1]
    target = target_of(comp)
    edges = snap.index.edges_between(snap.reference_doc, fid, doc_name)

    rows = [("상대 카드",
             f'<span class="dv-mono">{dv.esc((target or {}).get("entity_name") or "(없음)")}</span>'
             f'<br>{dv.esc(attributes_text((target or {}).get("attributes")))}')]
    if edges:
        edge = edges[0]
        who = dv.who({"ontology": "h", "code": "c", "llm": "l"}.get(edge.decided_by, "c"))
        detail = f' · {dv.esc(edge.axis)}' if edge.axis else ""
        rows.append(("짝 찾기", f'{who} <span class="dv-mono">{edge.relation}{detail}</span>'
                                f'<br>유사도 {edge.recall_score:.4f}'))
        if edge.rejected_by:
            rows.append(("검문소", dv.pill("거부됨", "bad")
                         + f' {dv.esc(edge.reason)[:120]}'))
    else:
        rows.append(("짝 찾기", dv.pill("연결 없음", "gray")))

    rows.append(("값 대조",
                 dv.who("l" if comp.get("decided_by") == "llm" else "c")
                 + f' {dv.esc(str(comp.get("reason") or ""))[:160]}'))
    rows.append(("결과", _result_pill(result)
                 + f' <span class="dv-mono">decided_by: {dv.esc(comp.get("decided_by"))}</span>'))
    return {"cls": tone, "doc": f"📄 {dv.esc(doc_name)}", "rows": rows}


# --------------------------------------------------------------------------- #
# 학습 모드 — 산출물에서 조각 꺼내기
# --------------------------------------------------------------------------- #
def _slice_compact(compact: dict, source: dict) -> str:
    """압축 원문에서 **이 fact 가 나온 자리**만 꺼낸다.

    문서 전체를 보여 주면 오히려 아무것도 안 보인다. ``source`` 는 doc_type 별로
    키가 완전히 다르므로 분기가 필요하다(설계 §4.3).
    """
    kind = str(source.get("doc_type") or compact.get("doc_type") or "")
    if kind == "excel":
        row = source.get("row")
        for sheet in compact.get("sheets") or []:
            for r in sheet.get("rows") or []:
                if r.get("r") == row:
                    return json.dumps(r, ensure_ascii=False, indent=2)
    elif kind == "word":
        want = set(source.get("block_ids") or [])
        hits = [b for b in (compact.get("blocks") or []) if b.get("id") in want]
        if hits:
            return json.dumps(hits, ensure_ascii=False, indent=2)[:900]
    elif kind == "ppt":
        want = set(source.get("shape_ids") or [])
        for slide in compact.get("slides") or []:
            if slide.get("slide_no") != source.get("slide_no"):
                continue
            hits = [s for s in (slide.get("shapes") or []) if s.get("id") in want]
            out: dict = {"slide_no": slide.get("slide_no"), "shapes": hits}
            if source.get("from_notes"):
                out["notes"] = slide.get("notes")
            return json.dumps(out, ensure_ascii=False, indent=2)[:900]
    return "(이 fact 의 자리를 압축 원문에서 찾지 못했습니다)"


def _find_record(doc, fact_id: str) -> Optional[dict]:
    """Excel 경로의 대응 레코드. ``fact-<record_id>`` 규약으로 되짚는다."""
    records = doc.load("records")
    if not records or not fact_id.startswith("fact-"):
        return None
    want = fact_id[len("fact-"):]
    for rec in records.get("records") or []:
        if str(rec.get("record_id")) == want:
            return rec
    return None


def _checks_for(doc, fact_id: str) -> list[dict]:
    """이 fact 에 걸린 F4a 지적. 저신뢰(error)는 별도로 표시한다."""
    report = doc.load("validation_report") or {}
    low = low_confidence_ids(report)
    out = [c for c in (report.get("checks") or [])
           if isinstance(c, dict) and c.get("fact_id") == fact_id]
    if fact_id in low:
        out.append({"check": "(저신뢰)", "severity": "error",
                    "reason": "error 지적이 있어 F5 가 판정을 LLM 에 넘긴다."})
    return out


def _edges_text(node, edges) -> str:
    """이 fact 의 개념 소속과 관계를 사람이 읽는 형태로."""
    lines = []
    if node is not None:
        docs = sorted({m.doc for m in node.members})
        lines.append(f"개념 {node.concept_id} · 멤버 {len(node.members)}개")
        lines.append(f"  걸친 문서: {', '.join(docs)}")
    else:
        lines.append("개념 노드를 찾지 못했습니다.")
    for edge in edges[:8]:
        mark = f" [거부됨: {edge.rejected_by}]" if edge.rejected_by else ""
        axis = f" ({edge.axis})" if edge.axis else ""
        lines.append(f"  {edge.relation}{axis} ← {edge.decided_by} "
                     f"· 유사도 {edge.recall_score:.4f}{mark}")
    if len(edges) > 8:
        lines.append(f"  … {len(edges) - 8}건 더")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 디버깅 모드
# --------------------------------------------------------------------------- #
def render_debug_html(
    snap: RunSnapshot,
    *,
    target_doc: str = "",
    results: Optional[list[str]] = None,
    theme: str = "auto",
) -> RenderedHtml:
    """판정 표 → 원인 카드 → 증거 원문. 클릭 세 번으로 내려간다."""
    comparisons = snap.comparisons_for(target_doc)
    if not comparisons:
        return _empty("이 실행에는 비교 결과가 없습니다.")
    wanted = set(results or ["mismatch", "unknown", "missing"])
    shown = [c for c in _sorted_comparisons(comparisons)
             if str(c.get("result")) in wanted]

    traces = {(t.ref_fact_id, t.target_doc): t
              for t in trace_all_missing(snap, target_doc)}
    parts = [
        _overview(snap, comparisons, traces),
        _problems(snap),
        _verdict_table(snap, shown, traces),
        _graph_section(snap, target_doc),
    ]
    body = "\n".join(p for p in parts if p) + _SCRIPT
    height = min(_MAX_HEIGHT, 620 + 34 * len(shown))
    return RenderedHtml(html=_page(body, theme=theme), height=height)


def _overview(snap: RunSnapshot, comparisons: list[dict],
              traces: dict) -> str:
    counts: dict[str, int] = {}
    for c in comparisons:
        key = str(c.get("result"))
        counts[key] = counts.get(key, 0) + 1
    pills = " ".join(
        _result_pill(r) + f" {counts.get(r, 0)}" for r in RESULT_ORDER if counts.get(r)
    )
    cause = summarize(list(traces.values()))
    from ..fact.missing_trace import CAUSE_LABEL
    rows = [[dv.esc(CAUSE_LABEL.get(k, k)), str(v)]
            for k, v in sorted(cause.items(), key=lambda x: -x[1])]
    body = [f'<div style="margin:6px 0 12px">{pills}</div>']
    if rows:
        body.append("<div style='font-size:.83rem;font-weight:700;margin:10px 0 6px'>"
                    "'대상에 없음' 의 원인 분포</div>")
        body.append(dv.table(["원인", "건수"], rows, numeric=[1]))
    stats = snap.compare_stats or {}
    concept = stats.get("concept") if isinstance(stats.get("concept"), dict) else {}
    if concept.get("budget_exhausted_pairs"):
        body.append(
            '<div class="dv-note" style="border-left-color:var(--dv-bad)">'
            f'🚨 개념 판정 예산이 부족해 <b>{concept["budget_exhausted_pairs"]}쌍</b>을 '
            "판정하지 못했습니다 — 아래 결과를 그대로 믿지 마세요. "
            "<code>max_llm_calls_per_concept</code> 를 늘리고 다시 실행하세요.</div>")
    return _wrap_section(
        f"실행 요약 — {dv.esc(snap.label)}",
        f"기준 <code>{dv.esc(snap.reference_doc)}</code> · 대상 "
        f"{dv.esc(', '.join(snap.target_docs)) or '(없음)'}",
        "".join(body))


def _wrap_section(title: str, sub: str, body: str, cap: str = "") -> str:
    out = f'<div class="dv"><div class="dv-title">{title}</div>'
    if sub:
        out += f'<div class="dv-sub">{sub}</div>'
    out += body
    if cap:
        out += f'<div class="dv-cap">{cap}</div>'
    return out + "</div>"


_VERDICT_HEADERS = ("기준 항목", "대상 문서", "판정", "어긋난 속성", "사유")


def _verdict_table(snap: RunSnapshot, comparisons: list[dict], traces: dict) -> str:
    """판정 표 — 행을 누르면 **그 행 바로 아래**에 원인 카드가 열린다.

    카드를 표 밖에 모아 두면 여러 개를 펼쳤을 때 어느 행의 것인지 알 수 없다.
    """
    if not comparisons:
        return _wrap_section("판정", "", '<div class="dv-sub">해당하는 항목이 없습니다.</div>')
    rows, details = [], []
    for i, comp in enumerate(comparisons):
        fid = str((comp.get("reference") or {}).get("fact_id") or "")
        rows.append([
            f'<span class="mw-caret">▸</span> {dv.esc(comp.get("entity_name"))}',
            dv.esc(comp.get("target_doc")),
            _result_pill(str(comp.get("result"))),
            dv.esc(", ".join(comp.get("mismatch_attributes") or []) or "-"),
            dv.esc(str(comp.get("reason") or "")[:70]),
        ])
        details.append(_detail_card(
            snap, comp, traces.get((fid, str(comp.get("target_doc")))), f"mw{i}"))
    return _wrap_section(
        "판정 — 확인이 필요한 것부터",
        "행을 누르면 <b>그 자리에서</b> 원인 카드가 펼쳐진다.",
        _expandable_table(_VERDICT_HEADERS, rows, details),
    )


def _expandable_table(headers, rows, details) -> str:
    """행마다 접힌 상세 행을 끼운 표.

    ``dv.table`` 을 쓰지 않는 이유: 상세 행 삽입은 이 화면 하나만 필요한 요구라
    공용 컴포넌트의 시그니처를 늘리는 대신 클래스(``dv-scroll``/``dv-tbl``)만 빌린다.
    """
    span = len(headers)
    head = "".join(f"<th>{h}</th>" for h in headers)
    body = []
    for i, (row, detail) in enumerate(zip(rows, details)):
        key = f"mw{i}"
        cells = "".join(f"<td>{c}</td>" for c in row)
        body.append(f'<tr class="mw-row" data-mw="{key}">{cells}</tr>')
        body.append(f'<tr class="mw-detail mw-hidden" id="{key}">'
                    f'<td colspan="{span}">{detail}</td></tr>')
    return (f'<div class="dv-scroll"><table class="dv-tbl"><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')


def _findings_block(comp: dict) -> str:
    """1:N 종합 판정의 후보별 내역.

    위 표는 **대표 1건**만 보여준다. 후보가 여럿이면 나머지가 화면에서 사라져,
    리포트(`report/fact_report.py` 의 같은 이름 절)와 화면이 갈라진다 — 그러면
    사람이 둘을 대조할 수 없다. 라벨은 :data:`RESULT_LABEL` 단일 출처를 그대로 쓴다.

    후보가 1건뿐이면 붙이지 않는다. 위 표가 이미 같은 내용을 담고 있다.
    """
    findings = [f for f in (comp.get("findings") or []) if isinstance(f, dict)]
    if len(findings) < 2:
        return ""
    rows = []
    for f in findings:
        attrs = ", ".join(str(a) for a in (f.get("mismatch_attributes") or [])) or "-"
        quote = str(f.get("quote") or "")
        cell = f"“{dv.esc(quote[:80])}”" if quote else "-"
        # 인용 검증 실패는 판정을 버리지 않고 표시만 남긴다 — 사람이 원문을 확인해야
        # 하는 자리라는 뜻이지, 그 내역이 틀렸다는 뜻이 아니다. 경고는 인용부호
        # **밖**에 둔다 — 안에 넣으면 경고 문구가 원문의 일부처럼 읽힌다.
        if not f.get("quote_verified"):
            cell += ' <span class="dv-sub">⚠️ 원문에서 확인 못 함</span>'
        rows.append([
            _result_pill(str(f.get("result"))),
            dv.esc(attrs),
            dv.esc(str(f.get("reason") or "")[:80]),
            cell,
        ])
    return ('<div style="font-weight:700;font-size:.85rem;margin:12px 0 2px">'
            f'후보별 내역 <span class="dv-sub">({len(findings)}건을 종합)</span></div>'
            + dv.table(["판정", "어긋난 속성", "사유", "근거 인용"], rows))


def _detail_card(snap: RunSnapshot, comp: dict, trace: Optional[MissingTrace],
                 key: str) -> str:
    """원인 카드 — 자기 식별 헤더 · 양측 근거 · 파이프라인 흔적 · 조치 · 증거 원문.

    행 바로 아래에 열리지만 카드가 길면 스크롤 중에 행이 화면 밖으로 나간다. 그래서
    카드가 **스스로 어느 항목인지 밝히는** 헤더를 맨 위에 둔다.
    """
    body = []
    ref = comp.get("reference") or {}
    target = target_of(comp)
    body.append(dv.table(
        ["", "값", "근거 원문", "위치"],
        [["기준", dv.esc(attributes_text(ref.get("attributes"))),
          f'“{dv.esc(str(ref.get("evidence_text") or "")[:80])}”',
          dv.esc(format_source(ref.get("source") or {}))],
         ["대상", dv.esc(attributes_text((target or {}).get("attributes"))),
          f'“{dv.esc(str((target or {}).get("evidence_text") or "")[:80])}”' if target
          else "(대응 내용 없음)",
          dv.esc(format_source((target or {}).get("source") or {})) if target else "-"]],
    ))
    body.append(_findings_block(comp))

    if trace is not None and trace.cause != "unresolved" or (trace and trace.trail):
        body.append(f'<div style="font-weight:700;font-size:.85rem;margin:12px 0 2px">'
                    f'{dv.esc(trace.label)} '
                    f'{dv.pill(trace.confidence, "ok" if trace.confidence == "확정" else "amber")}'
                    "</div>")
        body.append(f'<div class="dv-sub">{dv.esc(trace.headline)}</div>')
        if trace.trail:
            body.append(dv.trail(trace.trail))
        if trace.next_action:
            body.append(f'<div class="dv-note">{dv.who("h")} '
                        f"{dv.esc(trace.next_action)}</div>")
        for ev in trace.evidence:
            body.append(
                f"<details><summary>📄 {dv.esc(ev.artifact)} · "
                f"<code>{dv.esc(ev.pointer)}</code> — {dv.esc(ev.label)}</summary>"
                f"{_json_block(ev.detail)}</details>")
    elif trace is not None:
        body.append(f'<div class="dv-note">{dv.esc(trace.headline)}<br>'
                    f"{dv.esc(trace.next_action)}</div>")

    header = (
        '<div class="dv-hd">'
        f'{_result_pill(str(comp.get("result")))}'
        f'<span class="dv-nm">{dv.esc(comp.get("entity_name"))}</span>'
        f'<span class="dv-fn">{dv.esc(comp.get("target_doc"))}</span></div>'
    )
    return (f'<div class="dv-box" style="margin:6px 0">{header}'
            f'<div class="dv-bd">{"".join(body)}</div></div>')


def _graph_section(snap: RunSnapshot, target_doc: str) -> str:
    if snap.graph is None:
        return ""
    docs = [target_doc] if target_doc else snap.target_docs
    doc = docs[0] if docs else ""
    left, right, edges = _graph_data(snap, doc)
    layout = bipartite_layout(left, right, edges)
    svg = _svg(layout)
    counts: dict[str, int] = {}
    for e in layout.edges:
        counts[e.tone] = counts.get(e.tone, 0) + 1
    off = " ".join(f"mw-off-{t}" for t in DEFAULT_HIDDEN_TONES)
    rows = [[r["concept_id"], dv.esc(r["label"]), str(r["doc_count"]),
             dv.esc(" · ".join(f"{d}" for d in r["docs"]))]
            for r in concept_rows(snap.graph.nodes)[:40]]
    note = "".join(f'<div class="dv-note">{n}</div>' for n in layout.notes)
    return _wrap_section(
        f"개념 그래프 — 기준 ↔ {dv.esc(doc) or '대상'}",
        "버튼으로 선을 켜고 끌 수 있다. <b>다른 개념</b>(주황)은 후보 대부분을 차지하는 "
        "구조적 잡음이라 <b>처음엔 꺼 둔다</b> — 이어진 연결을 먼저 보기 위해서다.",
        _tone_toggles(counts) + note
        + f'<div class="dv-scroll mw-graph {off}">{svg}</div>'
        + "<details><summary>개념 노드 목록(문서 2개 이상에 걸친 것부터)</summary>"
        + dv.table(["개념", "이름", "문서 수", "문서"], rows, numeric=[2]) + "</details>",
        cap="한 노드에 여러 문서의 fact 가 들어 있으면 <b>그 둘은 같은 것을 말한 것</b>이다. "
            "이어져 있지 않으면 값이 아무리 비슷해도 비교하지 않는다.",
    )


def _tone_toggles(counts: dict[str, int]) -> str:
    """색깔 토글 바 겸 범례.

    0건인 톤도 **비활성 버튼으로 남긴다** — 사라지면 범례 구실을 못 하고, "회색이
    없는 건가 안 그린 건가"를 다시 확인해야 한다.
    """
    out = ['<div class="mw-legend">']
    for tone in ("ok", "amber", "bad", "gray"):
        style = _TONE_STYLE[tone]
        n = counts.get(tone, 0)
        on = tone not in DEFAULT_HIDDEN_TONES and n > 0
        disabled = " disabled" if n == 0 else ""
        hidden = " (숨김)" if not on and n else ""
        out.append(
            f'<button type="button" class="mw-t" data-tone="{tone}" '
            f'aria-pressed="{"true" if on else "false"}"{disabled} '
            f'style="color:{_TONE_COLOR[tone]}">'
            f'<span class="mw-dot"></span>'
            f'<span class="mw-lbl">{_TONE_NAME[tone]} {style["rel"]}</span>'
            f'<span class="mw-n">{n}{hidden}</span></button>')
    out.append("</div>")
    return "".join(out)


def _graph_data(snap: RunSnapshot, target_doc: str):
    ref_doc = snap.reference_doc
    left, right, seen_l, seen_r = [], [], set(), set()
    edges = []
    for i, edge in enumerate(snap.graph.edges if snap.graph else []):
        pairs = [(edge.left, edge.right), (edge.right, edge.left)]
        for a, b in pairs:
            if a.doc != ref_doc or b.doc != target_doc:
                continue
            lk, rk = f"{a.doc}#{a.fact_id}", f"{b.doc}#{b.fact_id}"
            if lk not in seen_l:
                seen_l.add(lk)
                left.append({"key": lk, "label": _member_label(snap, a.doc, a.fact_id),
                             "doc": a.doc, "fact_id": a.fact_id})
            if rk not in seen_r:
                seen_r.add(rk)
                right.append({"key": rk, "label": _member_label(snap, b.doc, b.fact_id),
                              "doc": b.doc, "fact_id": b.fact_id})
            edges.append({"left_key": lk, "right_key": rk, "relation": edge.relation,
                          "rejected_by": edge.rejected_by, "index": i,
                          "label": edge.axis or edge.relation})
            break
    return left, right, edges


def _member_label(snap: RunSnapshot, doc: str, fact_id: str) -> str:
    node = snap.index.node_of(doc, fact_id)
    if node is not None:
        for m in node.members:
            if m.doc == doc and m.fact_id == fact_id and m.entity_name:
                return m.entity_name
    return fact_id


_TONE_COLOR = {"ok": "var(--dv-ok)", "amber": "var(--dv-amber)",
               "bad": "var(--dv-bad)", "gray": "var(--dv-gray)"}

# 톤별 표시 규약. ``paint`` 가 클수록 **나중에**(위에) 그린다 — 실측에서 초록 12개가
# 주황 87개에 묻혔다. 신호(이어진 연결)가 잡음(다른 개념) 위로 와야 한다.
_TONE_STYLE = {
    "gray":  {"paint": 0, "width": 1.2, "dash": True,  "label": "미판정", "rel": "unknown"},
    "amber": {"paint": 1, "width": 1.2, "dash": False, "label": "다른 개념", "rel": "differs_by"},
    "bad":   {"paint": 2, "width": 2.0, "dash": True,  "label": "거부됨", "rel": "rejected"},
    "ok":    {"paint": 3, "width": 2.2, "dash": False, "label": "이어짐", "rel": "same_as"},
}
_TONE_NAME = {"ok": "초록", "amber": "주황", "bad": "빨강", "gray": "회색"}

DEFAULT_HIDDEN_TONES = ("amber",)
"""처음에 꺼 둘 톤. ``differs_by`` 는 후보 top_k 의 대부분이라 구조적 잡음이다."""


def _svg(layout) -> str:
    if not layout.nodes:
        return '<div class="dv-sub">그릴 연결이 없습니다.</div>'
    parts = [f'<svg viewBox="0 0 {layout.width} {layout.height}" '
             f'width="{layout.width}" height="{layout.height}" '
             'role="img" aria-label="개념 그래프">']
    for e in sorted(layout.edges, key=lambda x: _TONE_STYLE[x.tone]["paint"]):
        style = _TONE_STYLE[e.tone]
        dash = ' stroke-dasharray="5 4"' if style["dash"] else ""
        parts.append(f'<path class="mw-e-{e.tone}" d="{e.path}" fill="none" '
                     f'stroke="{_TONE_COLOR[e.tone]}" stroke-width="{style["width"]}"{dash}>'
                     f'<title>{dv.esc(e.label)} (edges[{e.index}])</title></path>')
    for n in layout.nodes:
        anchor = "end" if n.side == "left" else "start"
        dx = -8 if n.side == "left" else 8
        colour = "var(--dv-fg)" if n.linked else "var(--dv-mut)"
        parts.append(f'<circle cx="{n.x}" cy="{n.y}" r="3.5" fill="{colour}"/>')
        parts.append(f'<text x="{n.x + dx}" y="{n.y + 4}" text-anchor="{anchor}" '
                     f'font-size="11" fill="{colour}">{dv.esc(n.label[:28])}</text>')
    parts.append("</svg>")
    return "".join(parts)


# 데이터는 렌더 시점에 전부 들어 있어 서버 왕복이 없다 — Streamlit 재실행 없이 즉시 반응한다.
_SCRIPT = """
<script>
(function () {
  // 행 클릭 → 상세 행 펼침 + 원본 행 강조 + 캐럿 방향. 셋을 함께 바꿔야
  // 여러 개를 펼쳤을 때 어느 것이 열려 있는지 보인다.
  document.querySelectorAll('.mw-row').forEach(function (row) {
    row.addEventListener('click', function () {
      var box = document.getElementById(row.dataset.mw);
      if (!box) return;
      var opened = box.classList.toggle('mw-hidden') === false;
      row.classList.toggle('mw-open', opened);
      var caret = row.querySelector('.mw-caret');
      if (caret) caret.textContent = opened ? '\\u25BE' : '\\u25B8';
    });
  });

  // 색깔 토글. 컨테이너 클래스 하나로 집행하므로 선이 수백 개여도 즉시 반영된다.
  // Streamlit 은 위젯을 건드릴 때마다 iframe 을 새로 만들므로 선택을 localStorage 에
  // 기억해 둔다 — 안 그러면 필터를 바꿀 때마다 매번 다시 켜야 한다.
  var KEY = 'contentcompare:mw:tones';
  var graph = document.querySelector('.mw-graph');
  var buttons = document.querySelectorAll('.mw-t');
  if (!graph || !buttons.length) return;

  // 버튼 상태·선 표시·라벨을 **한 함수에서 함께** 바꾼다. 복원과 클릭이 따로 갱신하면
  // "선은 보이는데 버튼은 (숨김)" 같은 어긋남이 생긴다.
  function paint(btn, on) {
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    graph.classList.toggle('mw-off-' + btn.dataset.tone, !on);
    var n = btn.querySelector('.mw-n');
    if (n) n.textContent = n.textContent.replace(/ \\(숨김\\)$/, '') + (on ? '' : ' (숨김)');
  }
  function save() {
    var off = [];
    buttons.forEach(function (b) {
      // 비활성(0건) 버튼은 저장하지 않는다 — 누른 적 없는 선택이 기억되면
      // 나중에 그 톤의 선이 생겼을 때 이유 없이 숨겨진다.
      if (!b.disabled && b.getAttribute('aria-pressed') !== 'true') off.push(b.dataset.tone);
    });
    try { localStorage.setItem(KEY, JSON.stringify(off)); } catch (e) {}
  }

  var saved = null;
  try { saved = JSON.parse(localStorage.getItem(KEY)); } catch (e) {}
  if (!Array.isArray(saved)) saved = null;
  buttons.forEach(function (btn) {
    if (btn.disabled) return;              // 0건인 톤은 범례로만 남긴다
    if (saved) paint(btn, saved.indexOf(btn.dataset.tone) < 0);  // 저장된 선택 우선
    btn.addEventListener('click', function () {
      paint(btn, btn.getAttribute('aria-pressed') !== 'true');
      save();
    });
  });
})();
</script>
"""


__all__ = ["RenderedHtml", "RESULT_LABEL", "RESULT_ORDER",
           "render_debug_html", "render_learn_html"]

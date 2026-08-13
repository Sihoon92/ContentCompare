"""파이프라인 현미경 HTML 빌더 테스트 — streamlit 없이 문자열만 검증한다.

브라우저 렌더링은 커버하지 않는다. 대신 계산을 전부 파이썬 순수 함수로 밀어 두고
(JS 는 행 토글 하나뿐) **깨질 수 있는 것**을 여기서 잡는다: 이스케이프, 빈 실행,
스키마 함정(target=null, source 분기), 정렬 철학.
"""

from __future__ import annotations

import json

from contentcompare.fact.artifact_reader import RunRef, load_snapshot
from contentcompare.ui.micro_world import (
    RESULT_LABEL,
    RESULT_ORDER,
    render_debug_html,
    render_learn_html,
)

REF_DOC = "기준.xlsx"
TGT_DOC = "규격서.docx"


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _comparison(entity="공칭전압", result="missing", fact_id="fact-row-7",
                decided_by="code", target=None):
    return {
        "entity_name": entity, "target_doc": TGT_DOC, "result": result,
        "mismatch_attributes": ["target_value"] if result == "mismatch" else [],
        "match_score": 0.7656, "match_method": "concept", "decided_by": decided_by,
        "reason": "값이 다릅니다",
        "reference": {"fact_id": fact_id, "entity_name": entity,
                      "attributes": {"target_value": {"value": 3.89, "unit": ""}},
                      "evidence_text": "3.89",
                      "source": {"doc_type": "excel", "sheet": "데이터", "row": 7,
                                 "cell_range": "B7:P7"}},
        "target": target,
    }


def _run(tmp_path, *, comparisons=None, edges=None, facts=None, compact=None,
         with_docs=True):
    d = tmp_path / "기준_xlsx"
    _write(d / "comparison_result.json", {
        "reference": REF_DOC, "stats": {"comparisons": 1},
        "comparisons": comparisons if comparisons is not None else [_comparison()]})
    _write(d / "concept_graph.json", {"nodes": [], "edges": edges or [], "stats": {}})
    if with_docs:
        _write(d / "facts.json", facts if facts is not None else {
            "location": "sheet=데이터", "facts": [{
                "fact_id": "fact-row-7", "fact_type": "quantitative_spec",
                "entity_name": "공칭전압", "entity_path": ["기본사양", "공칭전압"],
                "attributes": {"target_value": {"value": 3.89, "unit": ""}},
                "search_text": "공칭전압 3.89", "evidence_text": "3.89",
                "source": {"doc_type": "excel", "sheet": "데이터", "row": 7,
                           "cell_range": "B7:P7"}, "confidence": 1.0}]})
        _write(d / "compact_raw.json", compact or {
            "doc_type": "excel", "file_name": REF_DOC,
            "sheets": [{"sheet_name": "데이터",
                        "rows": [{"r": 7, "cells": {"E": "공칭전압", "G": 3.89}}]}]})
        _write(d / "run_stats.json", {"path": REF_DOC, "stages": [
            "physical_raw", "compact_raw", "document_profile", "facts"]})
        _write(d / "validation_report.json", {
            "location": "sheet=데이터", "overall": {"facts": 1, "error": 0, "warn": 1},
            "by_check": {"unit_missing": 1},
            "checks": [{"check": "unit_missing", "severity": "warn",
                        "fact_id": "fact-row-7", "reason": "단위 없음",
                        "suggestion": "확인"}]})
    return load_snapshot(RunRef(label="기준_xlsx", dir=d))


# --------------------------------------------------------------------------- #
# 자체포함 · 안전
# --------------------------------------------------------------------------- #
def test_page_is_self_contained(tmp_path):
    html = render_debug_html(_run(tmp_path)).html
    assert html.startswith("<!doctype html>")
    assert "dv-who" in html and "<style>" in html
    # 외부 리소스를 하나도 참조하지 않는다(사내망 오프라인 필수).
    for bad in ("http://", "https://", "cdn.", "<link"):
        assert bad not in html, bad


def test_document_text_with_markup_is_escaped(tmp_path):
    snap = _run(tmp_path, comparisons=[_comparison(entity="<script>alert(1)</script>")])
    html = render_debug_html(snap).html
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_ampersand_and_angle_in_evidence_survive(tmp_path):
    comp = _comparison(result="mismatch",
                       target={"fact_id": "f1", "entity_name": "V",
                               "attributes": {}, "evidence_text": "a < b & c",
                               "source": {"doc_type": "word", "block_ids": ["w_b1"]}})
    html = render_debug_html(_run(tmp_path, comparisons=[comp])).html
    assert "&lt; b &amp; c" in html


def _multi_comparison():
    """후보 3건을 종합 판정한 비교 — 리포트에는 3줄이 나온다."""
    comp = _comparison(result="mismatch")
    comp["candidate_count"] = 3
    comp["findings"] = [
        {"fact_id": "f1", "result": "match", "mismatch_attributes": [],
         "quote": "0~10C: 0.2C", "quote_verified": True, "reason": "저온 구간 일치"},
        {"fact_id": "f2", "result": "mismatch", "mismatch_attributes": ["current"],
         "quote": "10~45C: 1C", "quote_verified": True, "reason": "상온 구간이 다름"},
        {"fact_id": "f3", "result": "match", "mismatch_attributes": [],
         "quote": "지어낸 문장", "quote_verified": False, "reason": "고온 구간 일치"},
    ]
    return comp


def test_detail_card_shows_every_candidate_finding(tmp_path):
    """리포트에 4줄이 나오는데 화면엔 대표 1건만 보이면 둘을 대조할 수 없다."""
    html = render_debug_html(_run(tmp_path, comparisons=[_multi_comparison()])).html
    for quote in ("0~10C: 0.2C", "10~45C: 1C"):
        assert quote in html
    assert "상온 구간이 다름" in html


def test_detail_card_flags_unverified_quote(tmp_path):
    """인용을 원문에서 못 찾은 내역은 화면에서도 구분돼야 검수가 된다."""
    html = render_debug_html(_run(tmp_path, comparisons=[_multi_comparison()])).html
    assert "⚠️" in html


def test_detail_card_omits_findings_for_single_candidate(tmp_path):
    """후보 1건이면 위 표가 이미 같은 내용이다 — 없던 절이 생기면 회귀다."""
    html = render_debug_html(_run(tmp_path)).html
    assert "후보별 내역" not in html


def test_embedded_script_tag_does_not_break_the_page(tmp_path):
    """근거 원문에 </script> 가 들어와도 페이지가 잘리면 안 된다."""
    comp = _comparison(entity="</script><b>x")
    html = render_debug_html(_run(tmp_path, comparisons=[comp])).html
    assert html.rstrip().endswith("</html>")
    assert "&lt;/script&gt;" in html


# --------------------------------------------------------------------------- #
# 디버깅 모드
# --------------------------------------------------------------------------- #
def test_render_returns_positive_height(tmp_path):
    assert render_debug_html(_run(tmp_path)).height > 0
    assert render_learn_html(_run(tmp_path)).height > 0


def test_verdicts_are_sorted_review_first(tmp_path):
    # 이름이 서로의 부분문자열이면 안 된다("일치항목" ⊂ "불일치항목") — index 가 오인한다.
    snap = _run(tmp_path, comparisons=[
        _comparison(entity="가나다", result="match"),
        _comparison(entity="라마바", result="mismatch"),
        _comparison(entity="사아자", result="unknown"),
    ])
    html = render_debug_html(snap, results=list(RESULT_ORDER)).html
    assert html.index("라마바") < html.index("사아자") < html.index("가나다")


def test_result_filter_hides_other_verdicts(tmp_path):
    snap = _run(tmp_path, comparisons=[
        _comparison(entity="일치항목", result="match"),
        _comparison(entity="누락항목", result="missing"),
    ])
    html = render_debug_html(snap, results=["missing"]).html
    assert "누락항목" in html and "일치항목" not in html


def test_missing_target_renders_placeholder_not_crash(tmp_path):
    """result=='missing' 이면 target 이 null 이다 — 접근하면 터진다."""
    html = render_debug_html(_run(tmp_path)).html
    assert "대응 내용 없음" in html


def test_cause_card_shows_headline_and_action(tmp_path):
    snap = _run(tmp_path, edges=[{
        "relation": "unknown",
        "left": {"doc": REF_DOC, "fact_id": "fact-row-7"},
        "right": {"doc": TGT_DOC, "fact_id": "fact-word-4"},
        "axis": "", "left_text": "", "right_text": "", "reason": "거부",
        "decided_by": "llm", "promoted": False, "recall_score": 0.65,
        "rejected_by": "evidence"}])
    html = render_debug_html(snap).html
    assert "근거 게이트 강등" in html
    assert "dv-trail" in html and "dv-tr dv-fail" in html
    assert "concept_graph.json" in html


def test_budget_warning_is_shown_at_the_top(tmp_path):
    d = tmp_path / "기준_xlsx"
    _write(d / "comparison_result.json", {
        "reference": REF_DOC,
        "stats": {"concept": {"budget_exhausted_pairs": 12}},
        "comparisons": [_comparison()]})
    _write(d / "concept_graph.json", {"nodes": [], "edges": [], "stats": {}})
    html = render_debug_html(load_snapshot(RunRef(label="x", dir=d))).html
    assert "🚨" in html and "12" in html


def test_empty_run_renders_a_message(tmp_path):
    d = tmp_path / "빈실행"
    _write(d / "comparison_result.json", {"reference": REF_DOC, "comparisons": []})
    out = render_debug_html(load_snapshot(RunRef(label="빈실행", dir=d)))
    assert "표시할 것이 없습니다" in out.html
    assert out.height > 0


def test_problems_are_surfaced(tmp_path):
    html = render_debug_html(_run(tmp_path)).html
    assert "확인할 수 없는 것" in html
    assert "candidate_pairs" in html


# --------------------------------------------------------------------------- #
# 학습 모드
# --------------------------------------------------------------------------- #
def test_learn_shows_the_pipeline_map_and_journey(tmp_path):
    html = render_learn_html(_run(tmp_path), fact_id="fact-row-7").html
    assert "dv-map" in html and "dv-jrn" in html
    assert "공칭전압" in html
    assert "facts.json" in html and "validation_report.json" in html


def test_learn_marks_stages_that_actually_ran(tmp_path):
    html = render_learn_html(_run(tmp_path)).html
    assert "dv-mi dv-on" in html      # run_stats.stages 에 있는 단계
    assert "dv-off" in html           # Excel 전용인데 이번엔 안 돈 단계


def test_learn_slices_compact_to_the_facts_own_row(tmp_path):
    html = render_learn_html(_run(tmp_path), fact_id="fact-row-7").html
    assert '"r": 7' in html


def test_learn_without_facts_explains_why(tmp_path):
    out = render_learn_html(_run(tmp_path, with_docs=False))
    assert "표시할 것이 없습니다" in out.html


def test_learn_falls_back_to_first_fact(tmp_path):
    html = render_learn_html(_run(tmp_path), fact_id="없는id").html
    assert "공칭전압" in html


# --------------------------------------------------------------------------- #
# 시각 언어 — 리포트와 같은 문구를 쓴다
# --------------------------------------------------------------------------- #
def test_result_labels_match_the_report():
    assert RESULT_LABEL["match"][0] == "✅ 일치"
    assert RESULT_LABEL["mismatch"][0] == "❌ 불일치"
    assert RESULT_LABEL["missing"][0] == "⚪ 대상에 없음"
    assert RESULT_LABEL["unknown"][0] == "❓ 판단보류"


def test_order_puts_review_items_first():
    assert RESULT_ORDER[0] == "mismatch"
    assert RESULT_ORDER[-1] == "match"


def test_source_coordinates_use_the_report_formatter(tmp_path):
    """리포트와 화면의 좌표 문자열이 갈라지면 사람이 대조할 수 없다."""
    html = render_debug_html(_run(tmp_path)).html
    assert "데이터!B7:P7" in html


# --------------------------------------------------------------------------- #
# 인라인 확장 — 펼친 카드가 어느 항목인지 알 수 있어야 한다
# --------------------------------------------------------------------------- #
def _rows_and_details(html):
    """(행 id, 상세 행 id) 를 문서 순서대로 뽑는다."""
    import re
    return re.findall(r'class="mw-row" data-mw="(\w+)"', html), \
        re.findall(r'class="mw-detail mw-hidden" id="(\w+)"', html)


def test_detail_row_sits_inside_the_table_next_to_its_row(tmp_path):
    """카드를 표 밖에 모으면 여러 개 펼쳤을 때 어느 행의 것인지 알 수 없다."""
    import re
    snap = _run(tmp_path, comparisons=[
        _comparison(entity="가나다"), _comparison(entity="라마바")])
    html = render_debug_html(snap).html

    rows, details = _rows_and_details(html)
    assert rows == details == ["mw0", "mw1"]
    # 상세 행이 자기 행 **바로 뒤**에 온다.
    order = re.findall(r'class="mw-(row|detail)[^"]*"[^>]*(?:data-mw|id)="(\w+)"', html)
    assert order == [("row", "mw0"), ("detail", "mw0"),
                     ("row", "mw1"), ("detail", "mw1")]


def test_detail_colspan_follows_the_header_count(tmp_path):
    import re
    from contentcompare.ui.micro_world import _VERDICT_HEADERS
    html = render_debug_html(_run(tmp_path)).html
    assert set(re.findall(r'colspan="(\d+)"', html)) == {str(len(_VERDICT_HEADERS))}


def test_row_carries_a_caret_and_whole_row_is_clickable(tmp_path):
    html = render_debug_html(_run(tmp_path)).html
    assert 'class="mw-caret">▸' in html
    assert 'class="mw-row" data-mw=' in html      # 행 전체가 대상
    assert ".mw-row.mw-open" in html              # 펼친 행 강조 CSS


def test_card_header_identifies_itself(tmp_path):
    """카드가 길면 스크롤 중에 행이 화면 밖으로 나간다 — 카드가 스스로 밝혀야 한다."""
    snap = _run(tmp_path, comparisons=[_comparison(entity="공칭용량")])
    html = render_debug_html(snap).html
    header = html[html.index('class="dv-hd"'):]
    assert "공칭용량" in header[:400]
    assert TGT_DOC in header[:400]
    assert "⚪ 대상에 없음" in header[:400]


# --------------------------------------------------------------------------- #
# 그래프 색깔 토글 — 초록이 주황에 묻히면 안 된다
# --------------------------------------------------------------------------- #
GRAPH_EDGES = (
    [{"relation": "differs_by", "axis": "물리량",
      "left": {"doc": REF_DOC, "fact_id": f"fact-row-{i}"},
      "right": {"doc": TGT_DOC, "fact_id": f"fact-word-{i}"},
      "left_text": "", "right_text": "", "reason": "", "decided_by": "llm",
      "promoted": False, "recall_score": 0.5, "rejected_by": ""} for i in range(5)]
    + [{"relation": "same_as",
        "left": {"doc": REF_DOC, "fact_id": "fact-row-7"},
        "right": {"doc": TGT_DOC, "fact_id": "fact-word-7"},
        "axis": "", "left_text": "", "right_text": "", "reason": "",
        "decided_by": "llm", "promoted": False, "recall_score": 0.8, "rejected_by": ""}]
)


def _graph_html(tmp_path, edges=None):
    return render_debug_html(_run(tmp_path, edges=edges or GRAPH_EDGES)).html


def test_edges_carry_their_tone_as_a_class(tmp_path):
    html = _graph_html(tmp_path)
    assert 'class="mw-e-amber"' in html
    assert 'class="mw-e-ok"' in html


def test_differs_by_is_hidden_by_default(tmp_path):
    """주황은 후보 top_k 의 대부분이라 구조적 잡음 — 처음엔 꺼 둔다."""
    from contentcompare.ui.micro_world import DEFAULT_HIDDEN_TONES
    html = _graph_html(tmp_path)
    assert "mw-graph mw-off-amber" in html
    assert DEFAULT_HIDDEN_TONES == ("amber",)
    # 토글은 컨테이너 클래스 하나로 집행한다(path 수와 무관하게 즉시 반영).
    assert ".mw-off-amber .mw-e-amber{display:none}" in html


def test_green_is_painted_after_orange(tmp_path):
    """SVG 는 나중에 그린 것이 위에 온다 — 신호가 잡음 위로 와야 한다."""
    import re
    html = _graph_html(tmp_path)
    tones = re.findall(r'class="mw-e-(\w+)"', html)
    assert tones.index("ok") > tones.index("amber")


def test_green_is_drawn_thicker_than_orange(tmp_path):
    import re
    html = _graph_html(tmp_path)
    widths = dict(re.findall(r'class="mw-e-(\w+)"[^>]*stroke-width="([\d.]+)"', html))
    assert float(widths["ok"]) > float(widths["amber"])


def test_toggle_buttons_show_counts_and_state(tmp_path):
    import re
    html = _graph_html(tmp_path)
    found = dict((m.group(1), m.group(2)) for m in
                 re.finditer(r'data-tone="(\w+)" aria-pressed="(\w+)"', html))
    assert found["ok"] == "true"
    assert found["amber"] == "false"          # 기본 숨김
    assert ">5 (숨김)<" in html                # 숨겨진 건수를 밝힌다
    assert ">1<" in html                      # 초록 1건


def test_zero_count_tone_stays_as_a_disabled_legend(tmp_path):
    """0건 톤을 지우면 '없는 건가 안 그린 건가'를 다시 확인해야 한다."""
    html = _graph_html(tmp_path)
    assert 'data-tone="gray" aria-pressed="false" disabled' in html


def test_toggle_state_is_remembered_across_reruns(tmp_path):
    """Streamlit 은 위젯을 건드릴 때마다 iframe 을 새로 만든다."""
    html = _graph_html(tmp_path)
    assert "contentcompare:mw:tones" in html
    assert "localStorage" in html


# --------------------------------------------------------------------------- #
# 토글 스크립트 — 실제 DOM 실행에서 나온 결함 두 개를 고정한다
#
# 브라우저 없이 검증할 수 없는 동작이라 스크립트 소스를 직접 본다. 문자열 검사는
# 약하지만, 두 결함이 **왜 생겼는지**를 기록해 두는 값이 크다.
# --------------------------------------------------------------------------- #
def test_disabled_tone_is_not_persisted():
    """0건이라 비활성인 버튼이 저장되면, 나중에 그 톤의 선이 생겼을 때
    사용자가 누른 적 없는데 숨겨진다."""
    from contentcompare.ui.micro_world import _SCRIPT
    save_body = _SCRIPT[_SCRIPT.index("function save()"):_SCRIPT.index("var saved")]
    assert "!b.disabled" in save_body


def test_state_and_label_are_updated_together():
    """복원과 클릭이 따로 갱신하면 '선은 보이는데 버튼은 (숨김)' 이 된다."""
    from contentcompare.ui.micro_world import _SCRIPT
    assert _SCRIPT.count("function paint(") == 1
    # 복원 경로와 클릭 경로가 **같은** 함수를 쓴다.
    assert "if (saved) paint(btn," in _SCRIPT
    assert "paint(btn, btn.getAttribute('aria-pressed')" in _SCRIPT
    # 라벨 갱신이 그 함수 안에 있다.
    paint_body = _SCRIPT[_SCRIPT.index("function paint("):_SCRIPT.index("function save()")]
    assert "mw-n" in paint_body and "숨김" in paint_body


def test_corrupt_saved_state_is_ignored():
    """localStorage 에 배열이 아닌 값이 있어도 화면이 죽으면 안 된다."""
    from contentcompare.ui.micro_world import _SCRIPT
    assert "Array.isArray(saved)" in _SCRIPT

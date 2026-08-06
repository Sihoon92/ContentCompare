"""공용 다이어그램 컴포넌트 테스트 — 문자열 조립만 하므로 의존성 0.

두 가지를 지킨다:

1. **데이터 주입이 된다** — 현미경이 실행 산출물로 같은 그림을 그릴 수 있어야 한다.
2. **문서 파이프라인이 안 깨진다** — ``scripts/doc_diagrams.py`` 의 무인자 호출이
   이식 전과 같은 HTML 을 낸다(이미 발행된 설명 페이지들과의 회귀).
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

from contentcompare.ui import diagram as dv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def docdiag():
    """``scripts/`` 는 패키지가 아니라 경로로 직접 로드한다."""
    path = os.path.join(ROOT, "scripts", "doc_diagrams.py")
    spec = importlib.util.spec_from_file_location("doc_diagrams_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- #
# 배지 — 주체=색 규약
# --------------------------------------------------------------------------- #
def test_who_badges_carry_the_agent_colour_class():
    assert 'class="dv-who dv-c"' in dv.who("c")
    assert "⚙️ 코드" in dv.who("c")
    assert 'class="dv-who dv-l"' in dv.who("l")
    assert 'class="dv-who dv-h"' in dv.who("h")


def test_diagnostic_agent_names_are_aliased():
    """``missing_trace`` 는 code/llm/embed/human 을 쓴다 — 같은 색으로 이어져야 한다."""
    assert dv.who("code") == dv.who("c")
    assert dv.who("llm") == dv.who("l")
    assert dv.who("embed") == dv.who("e")
    assert dv.who("human") == dv.who("h")


def test_unknown_agent_does_not_raise():
    assert 'class="dv-who dv-f"' in dv.who("무엇인가")


def test_legend_lists_requested_agents():
    out = dv.legend(("c", "l"))
    assert out.count("dv-who") == 2


# --------------------------------------------------------------------------- #
# 이스케이프 — 문서 원문에는 실제로 <, & 가 들어온다
# --------------------------------------------------------------------------- #
def test_escapes_markup_in_data():
    assert dv.esc("a < b & c") == "a &lt; b &amp; c"
    assert dv.esc(None) == ""
    assert dv.esc(3.89) == "3.89"


def test_journey_escapes_stop_data_but_not_notes():
    html = dv.journey([{
        "k": "c", "n": "1단계", "nm": "<위험>", "fn": "a.py",
        "out": "<script>x</script>", "note": "<b>설명</b>",
    }])
    assert "&lt;script&gt;" in html          # 데이터는 이스케이프
    assert "&lt;위험&gt;" in html
    assert "<b>설명</b>" in html             # 설명은 저자가 쓴 HTML


# --------------------------------------------------------------------------- #
# 데이터 주입
# --------------------------------------------------------------------------- #
def test_journey_renders_injected_stops():
    html = dv.journey(
        [{"k": "f", "n": "출발", "nm": "원본", "fn": "x.xlsx", "out": "값 3.89"},
         {"k": "l", "n": "3단계", "nm": "열의 뜻", "fn": "y.json",
          "inp": "헤더", "out": "역할"}],
        title="제목", sub="부제", cap="설명",
    )
    assert "출발" in html and "열의 뜻" in html
    assert "받는 것" in html and "내놓는 것" in html
    assert "이렇게 생겼다" in html            # inp 가 없는 첫 stop
    assert 'class="dv-title">제목' in html
    assert 'class="dv-cap">설명' in html


def test_journey_without_title_omits_the_header():
    assert "dv-title" not in dv.journey([{"k": "c", "n": "1", "nm": "x",
                                          "fn": "f", "out": "o"}])


def test_pipemap_marks_active_and_dimmed_stages():
    items = [("F0", "꺼내기", "c", "physical_raw.json"),
             ("F2", "레코드", "l", "records.json"),
             ("F5", "값 대조", "cl", "comparison_result.json")]
    html = dv.pipemap(items, active={"physical_raw"}, dimmed={"records"})
    assert html.count("dv-mi dv-on") == 1
    assert "dv-off" in html
    assert html.count('class="dv-mi"') == 1   # 나머지 하나는 표시 없음


def test_fates_renders_cards_with_rows():
    html = dv.fates([{"cls": "ok", "doc": "📗 대상.docx",
                      "rows": [("결과", "<b>일치</b>")]}])
    assert "dv-fate dv-ok" in html
    assert "📗 대상.docx" in html
    assert "<b>일치</b>" in html


def test_trail_marks_only_the_failing_gate():
    html = dv.trail([
        {"stage": "recall", "who": "embed", "ok": True, "note": "0.65"},
        {"stage": "검문소", "who": "code", "ok": False, "note": "인용 없음"},
    ])
    assert html.count("dv-tr dv-fail") == 1
    assert "✔ recall" in html and "✖ 검문소" in html
    assert "→" in html


def test_table_marks_numeric_columns():
    html = dv.table(["항목", "점수"], [["공칭전압", "0.65"]], numeric=[1])
    assert '<td class="dv-n">0.65</td>' in html
    assert "<td>공칭전압</td>" in html


def test_pill_and_data_block():
    assert dv.pill("일치", "ok") == '<span class="dv-v dv-ok">일치</span>'
    assert "&lt;b&gt;" in dv.data_block("<b>")


# --------------------------------------------------------------------------- #
# 회귀 — 문서 파이프라인이 이식으로 깨지지 않았다
# --------------------------------------------------------------------------- #
def test_doc_diagrams_still_exposes_six_blocks(docdiag):
    assert set(docdiag.BLOCKS) == {"journey", "fates", "pipemap", "lanes", "tools", "vs"}


def test_doc_diagrams_blocks_render_without_arguments(docdiag):
    for name, fn in docdiag.BLOCKS.items():
        html = fn()
        assert html.startswith('<div class="dv">'), name
        assert html.rstrip().endswith("</div>"), name


def test_journey_still_selects_stops_by_index(docdiag):
    """설명 페이지들이 쓰는 호출 규약 — 인덱스 목록으로 일부만 고른다."""
    html = docdiag.journey(stops=[0, 1])
    assert html.count('class="dv-stop') == 2


def test_doc_diagrams_reexports_shared_names(docdiag):
    assert docdiag.CSS is dv.CSS
    assert docdiag.CSS_MARKER is dv.CSS_MARKER
    assert docdiag.who is dv.who


def test_ensure_css_is_idempotent(docdiag):
    page = "<html><head><style>body{}</style></head><body></body></html>"
    once = docdiag.ensure_css(page)
    assert docdiag.CSS_MARKER in once
    assert docdiag.ensure_css(once) == once


def test_insert_after_heading_finds_the_target(docdiag):
    page = "<h2>배경</h2><p>x</p><h2>직관</h2><p>y</p>"
    out = docdiag.insert_after_heading(page, "직관", "<BLOCK/>")
    assert out.index("<BLOCK/>") > out.index("<h2>직관</h2>")
    with pytest.raises(ValueError):
        docdiag.insert_after_heading(page, "없는제목", "<BLOCK/>")


def test_css_keeps_every_previously_published_rule(docdiag):
    """이미 발행된 페이지가 의존하는 규칙을 지우면 그 페이지들이 깨진다."""
    for rule in (".dv-who.dv-c", ".dv-jrn", ".dv-fate", ".dv-mi.dv-on",
                 ".dv-v.dv-ok", ".dv-tbl", ".dv-bar"):
        assert rule in dv.CSS, rule

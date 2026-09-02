"""타임라인 조회·표현 — CLI 플래그와 HTML 뷰.

UI 3층 분리(도메인 → 표현 → 화면)의 가운데 칸을 고정한다. **HTML 문자열 생성까지가
순수 함수**이므로 streamlit 없이 검증된다.
"""

from __future__ import annotations


import pytest

from contentcompare import timeline as tl


# --------------------------------------------------------------------------- #
# 도구
# --------------------------------------------------------------------------- #
class FakeClock:
    """호출할 때마다 ``step`` 초씩 흐르는 시계(기본 1초)."""

    def __init__(self, start: float = 1_700_000_000.0, step: float = 1.0) -> None:
        self.now = start
        self.step = step

    def __call__(self) -> float:
        value = self.now
        self.now += self.step
        return value


def _events(*rows):
    return [tl.TimelineEvent(ts=float(i), kind=k, name=n, status=s,
                             duration_ms=d, detail=dict(det))
            for i, (k, n, s, d, det) in enumerate(rows)]


@pytest.fixture(autouse=True)


def _clean_singleton():
    """싱글턴이 테스트 사이에 새지 않게 한다."""
    tl.reset_timeline()
    yield
    tl.reset_timeline()


# --------------------------------------------------------------------------- #
# CLI 플래그 (--quiet)
# --------------------------------------------------------------------------- #
def test_cli_quiet_turns_off_console_but_keeps_the_file(tmp_path, monkeypatch):
    from contentcompare.config import AppConfig

    config = AppConfig.from_dict(
        {"logging": {"timeline": True, "timeline_dir": str(tmp_path)}}
    )
    loud = tl.build_timeline(config, console=True)
    quiet = tl.build_timeline(config, console=False, label="q")
    assert loud.console and not quiet.console
    quiet.event("stage_start", "F1")
    assert (tmp_path / "q.jsonl").exists()


def test_cli_accepts_quiet_flag():
    from contentcompare.cli import build_parser

    assert build_parser().parse_args(["--quiet"]).quiet is True
    assert build_parser().parse_args([]).quiet is False


# --------------------------------------------------------------------------- #
# 표현층 (ui/timeline_view) — streamlit 없이 HTML 문자열까지
# --------------------------------------------------------------------------- #
def test_timeline_html_is_a_pure_string():
    """도메인 → 표현 → 화면 3층 분리 — 표현층은 streamlit 없이 문자열을 만든다."""
    from contentcompare.ui.timeline_view import render_timeline_html

    events = _events(
        ("stage_start", "F2 records · 자표준원문.xlsx", "", 0, {"rows": 120}),
        ("stage_end", "F2 records · 자표준원문.xlsx", "error", 366_400,
         {"error": "APITimeoutError"}),
    )
    html = render_timeline_html(events, title="demo")
    assert isinstance(html, str)
    assert "F2 records" in html
    assert "APITimeoutError" in html
    assert "366.4s" in html
    assert "<style" in html          # 자족적이어야 iframe 에 그대로 넣는다


def test_timeline_html_escapes_document_names():
    from contentcompare.ui.timeline_view import render_timeline_html

    events = _events(("stage_start", "<script>alert(1)</script>", "", 0, {}))
    html = render_timeline_html(events)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_timeline_html_handles_an_empty_run():
    from contentcompare.ui.timeline_view import render_timeline_html

    assert "없습니다" in render_timeline_html([])


# --------------------------------------------------------------------------- #
# 토큰 — ⏱ 탭에서도 보이는가
# --------------------------------------------------------------------------- #
def test_llm_row_shows_tokens_and_rate():
    from contentcompare.timeline import LLM_END, TimelineEvent
    from contentcompare.ui.timeline_view import render_timeline_html

    html = render_timeline_html([TimelineEvent(
        ts=1_700_000_000.0, kind=LLM_END, name="F2 records", status="ok",
        duration_ms=72_000,
        detail={"input_tokens": 3204, "output_tokens": 512, "tok_per_sec": 7.1},
    )])
    assert "3,204" in html and "512" in html and "7.1 tok/s" in html


def test_llm_row_without_tokens_shows_only_duration():
    """서버가 토큰을 안 주면 소요만 — 0 을 지어내지 않는다."""
    from contentcompare.timeline import LLM_END, TimelineEvent
    from contentcompare.ui.timeline_view import render_timeline_html

    html = render_timeline_html([TimelineEvent(
        ts=1_700_000_000.0, kind=LLM_END, name="F2 records", status="ok",
        duration_ms=1_500, detail={"output_chars": 40},
    )])
    assert "토큰" not in html and "tok/s" not in html

"""타임라인 표현층 — 이벤트 목록 → HTML 문자열.

**streamlit 을 import 하지 않는다.** UI 3층 분리(도메인 → 표현 → 화면)의 가운데
칸이고, 화면(:mod:`app.streamlit_app`)은 이 문자열을 iframe 에 넣기만 한다. 덕분에
"보이는 것"이 streamlit 없이 단위테스트된다.

색은 :mod:`contentcompare.ui.diagram` 의 **공용 시각 언어**를 그대로 쓴다 — 설명
페이지·현미경·타임라인이 같은 색을 써야 한 시스템으로 읽힌다. 여기서 더하는 축은
하나뿐이다: **시간**. 그래서 막대 길이는 소요를 나타내고, 실패는 diagram 의
``--dv-bad`` 를 쓴다.
"""

from __future__ import annotations

import html
from typing import Iterable, Sequence

from ..timeline import (
    ERROR_STATUSES,
    HTTP,
    LLM_END,
    LLM_START,
    RETRY,
    STAGE_END,
    STAGE_START,
    WAIT,
    TimelineEvent,
    format_clock,
    format_duration,
)

CSS = """
.tl{--tl-fg:#1c1c1a;--tl-mut:#6b6b66;--tl-line:#dcdcd6;--tl-card:#fff;--tl-bg:#f6f6f4;
    --tl-code:#1565c0;--tl-llm:#e65100;--tl-emb:#00796b;--tl-human:#6a1b9a;
    --tl-ok:#2e7d32;--tl-ok-bg:#e9f5ea;--tl-bad:#c62828;--tl-bad-bg:#fbeaea;
    --tl-amber:#ef6c00;--tl-amber-bg:#fdf0e0;--tl-gray:#607d8b;--tl-gray-bg:#eceff1;
    --tl-mono:ui-monospace,SFMono-Regular,Menlo,Consolas,"D2Coding",monospace;
    font-size:14px;line-height:1.55;color:var(--tl-fg);
    font-family:system-ui,-apple-system,"Malgun Gothic",sans-serif}
@media (prefers-color-scheme:dark){.tl{--tl-fg:#e8e8e4;--tl-mut:#a2a29c;--tl-line:#3a3d44;
    --tl-card:#1f2229;--tl-bg:#191c21;--tl-code:#8ec5f5;--tl-llm:#ffb074;
    --tl-ok:#83c98a;--tl-ok-bg:#173620;--tl-bad:#ef9a9a;--tl-bad-bg:#3a1e1e;
    --tl-amber:#ffb74d;--tl-amber-bg:#3a2a12;--tl-gray:#a8bcc6;--tl-gray-bg:#272d33}}
.tl *{box-sizing:border-box}
.tl-head{font-weight:700;margin:0 0 2px}
.tl-sub{font-size:.82rem;color:var(--tl-mut);margin:0 0 14px}
.tl-bars{margin:0 0 20px}
.tl-bar{display:flex;align-items:center;gap:10px;margin:3px 0;font-size:.83rem}
.tl-bar b{flex:0 0 auto;min-width:0;font-weight:600;
          overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:46%}
.tl-track{flex:1 1 auto;height:14px;background:var(--tl-gray-bg);border-radius:3px;
          position:relative;overflow:hidden}
.tl-fill{position:absolute;inset:0 auto 0 0;background:var(--tl-code);border-radius:3px}
.tl-fill.bad{background:var(--tl-bad)}
.tl-ms{flex:0 0 auto;color:var(--tl-mut);font-family:var(--tl-mono);font-size:.78rem}
.tl-rows{border-top:1px solid var(--tl-line);overflow-x:auto}
.tl-row{display:flex;gap:10px;padding:3px 6px;border-bottom:1px solid var(--tl-line);
        font-family:var(--tl-mono);font-size:.78rem;white-space:nowrap}
.tl-row.bad{background:var(--tl-bad-bg)}
.tl-row.warn{background:var(--tl-amber-bg)}
.tl-t{flex:0 0 auto;color:var(--tl-mut)}
.tl-m{flex:1 1 auto}
.tl-row .k{color:var(--tl-mut)}
.tl-row.bad .tl-m{color:var(--tl-bad);font-weight:600}
.tl-row.warn .tl-m{color:var(--tl-amber)}
.tl-row.llm .tl-m{color:var(--tl-llm)}
.tl-empty{padding:24px;text-align:center;color:var(--tl-mut);background:var(--tl-bg);
          border-radius:8px}
"""

#: 종류별 행 등급 — 색은 diagram 의 의미를 따른다(🤖LLM=주황, 실패=적색).
_ROW_CLASS = {LLM_START: "llm", LLM_END: "llm", RETRY: "warn", WAIT: "warn"}

#: 들여쓰기(공백 수). 콘솔의 ``_INDENT`` 와 같은 위계를 HTML 에서 재현한다.
_DEPTH = {STAGE_START: 0, STAGE_END: 0, LLM_START: 1, LLM_END: 1,
          HTTP: 2, RETRY: 2, WAIT: 2}


def _esc(text: object) -> str:
    return html.escape(str(text), quote=True)


def _row_class(event: TimelineEvent) -> str:
    if event.status in ERROR_STATUSES:
        return "bad"
    return _ROW_CLASS.get(event.kind, "")


def _message(event: TimelineEvent) -> str:
    """행 본문. 콘솔과 같은 정보를 담되 기호 대신 색·굵기로 구분한다."""
    detail = event.detail or {}
    depth = int(detail.get("depth") or 0) + _DEPTH.get(event.kind, 2)
    pad = "&nbsp;" * (depth * 3)
    name = _esc(event.name.split(" · ")[-1] if depth else event.name)
    took = format_duration(event.duration_ms) if event.duration_ms else ""

    if event.kind == STAGE_START:
        return f"{pad}▶ <b>{name}</b>"
    if event.kind == STAGE_END:
        tail = f" <span class='k'>{took}</span>" if took else ""
        if event.status in ERROR_STATUSES:
            why = _esc(detail.get("error") or event.status)
            return f"{pad}중단 — {why}{tail}"
        return f"{pad}완료{tail}"
    if event.kind == LLM_START:
        chars = detail.get("prompt_chars")
        return f"{pad}LLM 요청" + (f" <span class='k'>{chars:,}자</span>" if chars else "")
    if event.kind == LLM_END:
        if event.status in ERROR_STATUSES:
            return f"{pad}LLM 실패 — {_esc(detail.get('error') or event.status)} ({took})"
        return f"{pad}LLM 응답 <span class='k'>{took}</span>"
    if event.kind == RETRY:
        counter = f"{detail.get('attempt', '?')}/{detail.get('max', '?')}"
        why = _esc(detail.get("error") or detail.get("reason") or event.status)
        return f"{pad}재시도 {counter} — {why}"
    if event.kind == WAIT:
        return (f"{pad}대기 {detail.get('seconds', 0)}초 — "
                f"{_esc(detail.get('reason') or event.status)}")
    if event.kind == HTTP:
        code = detail.get("status_code") or "-"
        return f"{pad}HTTP {_esc(code)} <span class='k'>{took}</span>"
    return f"{pad}{name}"


def _bars(events: Sequence[TimelineEvent]) -> str:
    """단계별 소요 막대. **배치는 제외**한다 — 요약이 요약이어야 한다."""
    rows = [e for e in events
            if e.kind == STAGE_END and not (e.detail or {}).get("depth")]
    if not rows:
        return ""
    longest = max(e.duration_ms for e in rows) or 1
    out = ["<div class='tl-bars'>"]
    for event in sorted(rows, key=lambda e: e.duration_ms, reverse=True):
        width = max(1.0, event.duration_ms / longest * 100)
        bad = " bad" if event.status in ERROR_STATUSES else ""
        mark = " ✗" if bad else ""
        out.append(
            f"<div class='tl-bar'><b>{_esc(event.name)}{mark}</b>"
            f"<span class='tl-track'>"
            f"<span class='tl-fill{bad}' style='width:{width:.1f}%'></span></span>"
            f"<span class='tl-ms'>{format_duration(event.duration_ms)}</span></div>"
        )
    out.append("</div>")
    return "".join(out)


def render_timeline_html(
    events: Iterable[TimelineEvent], *, title: str = "", limit: int = 2000
) -> str:
    """이벤트 → 자족적 HTML(간트 막대 + 행 목록).

    ``limit`` 은 브라우저 보호용이다. 넘으면 **뒤쪽**을 남긴다 — 실패는 끝에 있다.
    """
    rows = list(events)
    if not rows:
        return (f"<style>{CSS}</style><div class='tl'>"
                f"<div class='tl-empty'>이 실행에는 타임라인 이벤트가 없습니다."
                f"<br><small>logging.timeline 이 켜져 있는지 확인하세요.</small>"
                f"</div></div>")

    cut = len(rows) > limit
    shown = rows[-limit:] if cut else rows
    failures = sum(1 for e in rows if e.status in ERROR_STATUSES)
    span = format_duration(int((rows[-1].ts - rows[0].ts) * 1000))

    head = [f"<style>{CSS}</style><div class='tl'>"]
    head.append(f"<div class='tl-head'>{_esc(title or '실행 타임라인')}</div>")
    note = f" · 실패 {failures}건" if failures else ""
    trimmed = f" · 앞부분 {len(rows) - len(shown)}건 생략" if cut else ""
    head.append(f"<div class='tl-sub'>{len(rows)}건 · {span}{note}{trimmed}</div>")
    head.append(_bars(rows))

    head.append("<div class='tl-rows'>")
    for event in shown:
        cls = _row_class(event)
        head.append(
            f"<div class='tl-row {cls}'>"
            f"<span class='tl-t'>{format_clock(event.ts)}</span>"
            f"<span class='tl-m'>{_message(event)}</span></div>"
        )
    head.append("</div></div>")
    return "".join(head)

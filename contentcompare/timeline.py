"""실행 타임라인 — 단계·LLM 호출·재시도·대기를 **하나의 시간축**에 세운다.

문제의식은 실측에서 나왔다. 100행 엑셀이 ``records`` 단계에서 죽었을 때, "무엇이 언제
왜 죽었는지"를 산출물에서 **역산**해야 했다 — ``run_stats.json`` 의 ``llm.calls`` 를 세어
"profile 1 + schema 1 = 2 이므로 calls 3 이면 첫 배치" 같은 추론을 사람이 손으로 했다.
그 사이 화면은 8분간 조용했고, SDK 가 120초씩 네 번 재시도하는 것도 보이지 않았다.

이 모듈이 없애려는 것이 그 역산이다.

**왜 ``llm/`` 이 아니라 최상위인가.** 기록 대상이 LLM 호출만이 아니다(파이프라인 단계·
COM 추출·대기). 그리고 :mod:`contentcompare.llm.tracing` 이 이것을 import 하므로 방향이
위에서 아래여야 순환이 없다 — 이쪽은 ``tracing`` 을 import 하지 않는다.

**:mod:`~contentcompare.llm.tracing` 과 역할이 다르다.** 그쪽(Langfuse/``JsonlTracer``)은
*무엇을 주고받았나*(프롬프트 원문)를 남기고 기본 off 다. 이쪽은 *언제 무슨 일이
있었나*(시각·소요·회차)를 남기고 **기본 on** 이다. 그래서 이 모듈은 **원문을 절대 담지
않는다** — 기본 on 인 경로에 원문이 들어가면 평문 유출이 기본값이 된다.

**append-only JSONL 인 이유.** 타임라인은 *죽는 순간*을 보려고 만드는 것이라 죽을 때
살아남아야 한다. ``JsonlTracer`` 가 매번 ``index.json`` 을 다시 쓰는 것과 의도적으로
다르며, 마지막 줄이 잘려도 앞줄은 그대로 읽힌다(:func:`load_timeline`).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Union

from .logging_setup import NO_CONSOLE, log_print

logger = logging.getLogger("contentcompare.timeline")

#: 이벤트 종류. 문자열로 두는 것은 JSONL 이 사람이 읽는 산출물이기 때문이다.
STAGE_START = "stage_start"
STAGE_END = "stage_end"
LLM_START = "llm_start"
LLM_END = "llm_end"
HTTP = "http"
RETRY = "retry"
WAIT = "wait"
NOTE = "note"

#: 실패를 뜻하는 status. 조회 스크립트·UI 가 공유한다.
ERROR_STATUSES = ("error", "timeout", "rate_limit")


# --------------------------------------------------------------------------- #
# 이벤트
# --------------------------------------------------------------------------- #
@dataclass
class TimelineEvent:
    """타임라인 한 줄. **프롬프트·응답 원문은 담지 않는다**(길이·회차·상태코드만)."""

    ts: float
    """``time.time()`` — 벽시계. 로그 파일의 ``asctime`` 과 맞춰 보는 축이다."""

    kind: str
    name: str
    """단계 이름. 예: ``"F2 records · 자표준원문.xlsx · 배치 1/4"``."""

    status: str = ""
    """``ok`` | ``error`` | ``timeout`` | ``rate_limit``. 빈 값은 '진행 중'."""

    duration_ms: int = 0
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        out: dict[str, Any] = {"ts": self.ts, "kind": self.kind, "name": self.name}
        if self.status:
            out["status"] = self.status
        if self.duration_ms:
            out["duration_ms"] = self.duration_ms
        if self.detail:
            out["detail"] = self.detail
        return out

    @classmethod
    def from_dict(cls, data: dict) -> "TimelineEvent":
        return cls(
            ts=float(data.get("ts") or 0.0),
            kind=str(data.get("kind") or ""),
            name=str(data.get("name") or ""),
            status=str(data.get("status") or ""),
            duration_ms=int(data.get("duration_ms") or 0),
            detail=dict(data.get("detail") or {}),
        )

    @property
    def failed(self) -> bool:
        return self.status in ERROR_STATUSES


# --------------------------------------------------------------------------- #
# 표현 (순수 함수 — 콘솔·스크립트·UI 가 공유한다)
# --------------------------------------------------------------------------- #
#: 기호별 ASCII 대체안. **cp949(Windows PowerShell 5.1 기본)에 ``✓``·``✗``·``⚠``·``⏳``·
#: ``—`` 가 없다** — 그대로 print 하면 ``UnicodeEncodeError`` 가 나서 그 줄이 통째로
#: 사라진다(실측: 종료 줄이 전부 유실됐다).
_FALLBACK_CHARS = {
    "✓": "OK", "✗": "XX", "⚠": "!!", "⏳": "..", "—": "-",
    "▶": ">", "│": "|", "├": "+", "·": "-", "→": "->",
}


@lru_cache(maxsize=8)
def _fallback_table(encoding: str) -> dict[int, str]:
    """그 인코딩이 **실제로 못 쓰는 문자만** 바꾸는 표(인코딩별로 캐시).

    통째로 ASCII 화하지 않는 이유는 cp949 가 ``▶``·``│``·``├`` 는 받기 때문이다 —
    쓸 수 있는 기호까지 버리면 화면이 이유 없이 초라해진다.
    """
    table: dict[int, str] = {}
    for char, replacement in _FALLBACK_CHARS.items():
        try:
            char.encode(encoding)
        except (UnicodeEncodeError, LookupError):
            table[ord(char)] = replacement
    return table


def console_safe(text: str, encoding: Optional[str]) -> str:
    """콘솔 인코딩으로 **반드시 출력 가능한** 문자열로 바꾼다.

    로그 파일과 JSONL 은 항상 UTF-8 이라 원래 기호가 남는다 — 이 함수는 화면에만
    적용된다. 그래서 화면이 초라해질지언정 **줄을 잃지는 않는다**.
    """
    enc = encoding or "utf-8"
    try:
        text.encode(enc)
        return text
    except (UnicodeEncodeError, LookupError):
        pass
    swapped = text.translate(_fallback_table(enc))
    try:
        swapped.encode(enc)
        return swapped
    except (UnicodeEncodeError, LookupError):
        # 한글까지 못 받는 콘솔(순수 ASCII)이라면 남은 것만이라도 보인다.
        return swapped.encode(enc, errors="replace").decode(enc, errors="replace")


def classify_error(exc: BaseException) -> str:
    """예외 → ``status``. **타임라인 라벨 전용이다.**

    실패의 성격(타임아웃인가 한도인가)이 갈려야 조회할 때 필터가 되고, 실패 줄만
    봐도 다음 조치가 갈린다 — 타임아웃은 배치 크기·``llm.timeout``, 한도는
    ``max_calls_per_minute`` 다.

    ⚠️ 요청 한도의 **권위 있는 판정은** :mod:`contentcompare.llm.ratelimit` 이다
    (설정으로 마커를 넓힐 수 있고 재시도를 결정한다). 이 함수는 그 결정을 흉내내지
    않으며 기록에 붙일 이름만 고른다 — 둘이 갈려도 동작에는 영향이 없다.
    """
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    if "timeout" in name or "timed out" in text or "timeout" in text:
        return "timeout"
    if ("ratelimit" in name or "rate limit" in text
            or "429" in text or "too many requests" in text):
        return "rate_limit"
    return "error"


def format_duration(ms: int) -> str:
    """사람이 읽는 소요. 초 단위가 기본이고 1초 미만만 ms 로 보인다."""
    if ms >= 1000:
        return f"{ms / 1000:.1f}s"
    return f"{ms}ms"


def format_clock(ts: float) -> str:
    """``18:42:01.3`` — 0.1초까지. 로그 파일과 눈으로 맞추기 위한 해상도다."""
    dt = datetime.fromtimestamp(ts)
    return f"{dt:%H:%M:%S}.{dt.microsecond // 100_000}"


#: 종류별 들여쓰기. 단계가 가장 왼쪽이고 그 안의 사건이 오른쪽으로 밀린다.
_INDENT = {
    STAGE_START: "",
    STAGE_END: "",
    LLM_START: "  ├ ",
    LLM_END: "  ├ ",
    HTTP: "  │  ",
    RETRY: "  │  ",
    WAIT: "  │  ",
    NOTE: "  │  ",
}


def _detail_text(detail: dict, keys: Iterable[str]) -> str:
    """``detail`` 에서 지정 키만 골라 ``k=v`` 로 잇는다(없는 키는 건너뛴다)."""
    parts = [f"{k}={detail[k]}" for k in keys if detail.get(k) not in (None, "")]
    return ", ".join(parts)


def format_line(event: TimelineEvent) -> str:
    """이벤트 한 건 → 콘솔 한 줄.

    **순수 함수**다 — 실시간 콘솔과 ``scripts/show_timeline.py`` 와 Streamlit 탭이
    같은 모양을 보여야 대조가 되기 때문에 한 곳에서만 만든다.
    """
    detail = event.detail or {}
    error = str(detail.get("error") or "")
    depth = int(detail.get("depth") or 0)
    indent = _INDENT.get(event.kind, "  │  ")
    if depth and event.kind in (STAGE_START, STAGE_END):
        indent = "  " * depth
    head = f"{format_clock(event.ts)} {indent}"

    # 반복 구간(배치)은 부모 이름을 매번 다시 찍지 않는다 — 4배치면 8줄이 같은 접두어로
    # 채워져 정작 다른 부분이 안 보인다. **실패 줄만은 예외**로 전체 이름을 남긴다:
    # 그 한 줄이 문서·단계·배치를 동시에 답해야 하기 때문이다.
    short = event.name.split(" · ")[-1] if depth else event.name

    if event.kind == STAGE_START:
        extra = _detail_text(detail, ("rows", "batches", "blocks", "items"))
        return f"{head}▶ {short}" + (f" ({extra})" if extra else "")

    if event.kind == STAGE_END:
        if event.failed:
            why = error or event.status
            return (f"{head}✗ {event.name} 중단 — {why} "
                    f"({format_duration(event.duration_ms)})")
        return f"{head}✓ {short} ({format_duration(event.duration_ms)})"

    if event.kind == LLM_START:
        extra = _detail_text(detail, ("rows", "blocks", "pairs", "prompt_chars"))
        return f"{head}LLM 요청" + (f" ({extra})" if extra else "")

    if event.kind == LLM_END:
        if event.failed:
            attempts = detail.get("attempts")
            tail = f", 시도 {attempts}회" if attempts else ""
            return (f"{head}✗ 실패 — {error or event.status} "
                    f"({format_duration(event.duration_ms)}{tail})")
        # 토큰을 글자 수보다 앞에 둔다 — 배치 크기를 정하는 근거가 토큰이고
        # 글자 수는 서버가 토큰을 안 줄 때의 대체재다.
        extra = _detail_text(
            detail, ("input_tokens", "output_tokens", "tok_per_sec", "output_chars"))
        # 타임아웃은 이미 늦은 신호다. 근접 경고는 **죽기 전에** 보인다.
        slow = " ⚠ 느림" if detail.get("slow") else ""
        return (f"{head}✓ 응답 ({format_duration(event.duration_ms)}"
                + (f", {extra}" if extra else "") + f"){slow}")

    if event.kind == RETRY:
        attempt, limit = detail.get("attempt"), detail.get("max")
        counter = f"재시도 {attempt}/{limit}" if attempt and limit else "재시도"
        why = error or event.status or "일시 오류"
        took = f" ({format_duration(event.duration_ms)})" if event.duration_ms else ""
        return f"{head}⚠ {why}{took} — {counter}"

    if event.kind == WAIT:
        seconds = detail.get("seconds") or 0
        why = detail.get("reason") or "대기"
        return f"{head}⏳ {why} — {float(seconds):.0f}초 대기"

    if event.kind == HTTP:
        code = detail.get("status_code")
        label = f"HTTP {code}" if code else "HTTP"
        mark = "✗" if event.failed else "·"
        took = f" ({format_duration(event.duration_ms)})" if event.duration_ms else ""
        why = f" {error}" if error else ""
        return f"{head}{mark} {label}{took}{why}"

    extra = _detail_text(detail, tuple(detail))
    return f"{head}{event.name}" + (f" — {extra}" if extra else "")


# --------------------------------------------------------------------------- #
# 기록기
# --------------------------------------------------------------------------- #
class NullTimeline:
    """아무것도 하지 않는다. 비활성·설정 전·기록 실패 강등 시 쓰인다."""

    active = False
    path = ""

    def event(self, kind: str, name: str, **kwargs: Any) -> None:  # noqa: D401
        return None

    def close(self) -> None:
        return None


class Timeline:
    """이벤트를 JSONL 로 남기고(선택) 콘솔에 실시간 출력한다.

    ``clock`` 을 주입할 수 있는 것은 테스트 때문만이 아니다 — 시각이 이 모듈의
    **산출물 그 자체**라 고정할 수 없으면 표현을 검증할 방법이 없다.
    """

    active = True

    def __init__(
        self,
        path: Union[str, Path],
        *,
        console: bool = True,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = Path(path)
        self.console = console
        self._clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ #
    def event(
        self,
        kind: str,
        name: str,
        *,
        status: str = "",
        duration_ms: int = 0,
        **detail: Any,
    ) -> Optional[TimelineEvent]:
        """이벤트 1건을 기록한다. 실패해도 예외를 올리지 않는다.

        ``detail`` 은 키워드로 받는다 — 호출부에서 ``{"rows": 30}`` 대신
        ``rows=30`` 으로 쓰게 해서 원문 같은 큰 값을 무심코 넘기기 어렵게 한다.
        """
        if not self.active:
            return None
        event = TimelineEvent(
            ts=self._clock(), kind=kind, name=name,
            status=status, duration_ms=int(duration_ms),
            detail={k: v for k, v in detail.items() if v is not None},
        )
        try:
            self._write(event)
        except Exception as exc:  # noqa: BLE001 — 기록 실패가 실행을 막지 않는다
            self._degrade(exc)
            return None
        self._announce(event)
        return event

    def close(self) -> None:
        return None

    # ------------------------------------------------------------------ #
    def _write(self, event: TimelineEvent) -> None:
        """한 줄 append + flush. 강제 종료돼도 그때까지가 유효해야 한다."""
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
            f.flush()

    def _announce(self, event: TimelineEvent) -> None:
        """화면과 로그 파일에 동시에 남긴다(``log_print`` 규약 유지).

        **화면 실패는 기록 실패가 아니다.** 콘솔이 문자를 못 받으면 화면만 끄고
        파일 기록은 계속한다 — 조용한 것과 없는 것은 다르다.
        """
        text = format_line(event)
        # ``NO_CONSOLE`` 이 없으면 화면을 끈 의미가 사라진다 — 콘솔 핸들러가 이 INFO 를
        # 대신 찍어서 ``--quiet`` 가 조용해지지 않는다(실측된 결함).
        if not self.console:
            logger.info(text, extra={NO_CONSOLE: True})
            return
        try:
            self._print(text)
        except Exception as exc:  # noqa: BLE001
            self.console = False
            logger.warning(
                "타임라인 화면 출력 실패 — 화면만 끄고 파일 기록은 계속합니다: %s", exc)
            logger.info(text, extra={NO_CONSOLE: True})

    def _print(self, text: str) -> None:
        """콘솔 인코딩에 맞춰 한 줄 출력(화면 + 로그 파일 동시)."""
        log_print(
            console_safe(text, getattr(sys.stdout, "encoding", None)),
            logger_name="contentcompare.timeline",
        )

    def _degrade(self, exc: Exception) -> None:
        """첫 실패에 경고 1회 후 no-op 으로 내려앉는다.

        매 호출마다 경고를 남기면 수십 건의 실패 로그가 진짜 원인을 덮는다
        (``TracedChat._safe_record`` 와 같은 판단).
        """
        self.active = False
        logger.warning("타임라인 기록 실패 — 이 실행에서는 기록을 끕니다: %s", exc)


# --------------------------------------------------------------------------- #
# 읽기
# --------------------------------------------------------------------------- #
def load_timeline(path: Union[str, Path]) -> list[TimelineEvent]:
    """JSONL → 이벤트 목록. **깨진 줄은 건너뛴다.**

    강제 종료로 마지막 줄이 잘리는 것은 정상 상황이다 — 그 한 줄 때문에 앞의 수백
    줄을 잃으면 이 모듈을 만든 이유가 사라진다.
    """
    file = Path(path)
    if not file.exists():
        return []
    events: list[TimelineEvent] = []
    with open(file, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                events.append(TimelineEvent.from_dict(data))
    return events


#: 증상 → 다음 조치. **이번 진단에서 사람이 코드를 읽어 도달한 결론들**이다 —
#: 같은 결론에 두 번 도달하게 만들지 않으려고 실패한 자리에 붙인다.
_HINTS: tuple[tuple[str, str], ...] = (
    ("timeout",
     "응답 생성이 timeout 을 넘겼습니다. 배치당 출력량이 원인인 경우가 많습니다 — "
     "`fact.record_batch_rows`(또는 `fact_batch_blocks`)를 줄이거나 "
     "`llm.timeout` 을 올리세요."),
    ("rate_limit",
     "요청 한도에 걸렸습니다. `llm.max_calls_per_minute` 는 기본 0(꺼짐)입니다 — "
     "사내 한도가 분당 60회면 55 정도를 권합니다."),
    ("empty_output",
     "응답이 비어 있습니다. Ollama 는 컨텍스트가 모자라면 오류가 아니라 빈 응답을 줍니다 — "
     "`llm.ollama.num_ctx`(권장 16384)를 확인하세요."),
    ("parse",
     "JSON 파싱에 실패했습니다. 전송이 아니라 응답 **모양**의 문제이므로 "
     "모델을 키우거나 프롬프트를 조이는 쪽이 조치입니다."),
    ("slow",
     "timeout 에 근접한 호출이 있습니다. 아직 죽지 않았을 뿐이므로 "
     "입력이 조금만 커지면 실패합니다 — 배치 크기를 미리 줄이세요."),
)


def diagnose(events: Iterable[TimelineEvent]) -> list[str]:
    """관측된 증상에 대한 다음 조치를 문장으로 돌려준다(없으면 빈 목록).

    **판단하지 않고 세기만 한다** — "타임아웃이 있었다"는 사실에서 나오는 조치는
    데이터와 무관하게 같기 때문이다. 어느 후보가 맞는지 같은 판단은 하지 않는다
    (``review_router`` 의 게이트가 탐지가 아니라 라우팅인 것과 같은 선 긋기다).
    """
    seen: set[str] = set()
    for event in events:
        detail = event.detail or {}
        if event.status == "timeout":
            seen.add("timeout")
        if event.status == "rate_limit":
            seen.add("rate_limit")
        if event.kind == LLM_END and not event.failed and detail.get("output_chars") == 0:
            seen.add("empty_output")
        if event.kind == RETRY and "파싱" in str(detail.get("reason") or ""):
            seen.add("parse")
        if detail.get("slow"):
            seen.add("slow")
    return [text for key, text in _HINTS if key in seen]


def stage_durations(events: Iterable[TimelineEvent]) -> list[dict]:
    """단계별 소요를 **긴 순**으로. 실행이 끝난 뒤 "어디서 오래 걸렸나"에 답한다.

    ``run_stats.json`` 의 ``stages`` 는 이름 목록뿐이라 "어디까지 갔나"는 알아도
    "어디서 오래 걸렸나"는 모른다. 이 함수가 그 공백을 메운다.

    **배치(하위 단계)는 세지 않는다** — 104행이면 배치만 수십 줄이라 요약이 요약이
    아니게 된다. 배치별 소요가 궁금하면 타임라인 원본을 본다.
    """
    rows = [
        {"name": e.name, "duration_ms": e.duration_ms, "status": e.status or "ok"}
        for e in events
        if e.kind == STAGE_END and not (e.detail or {}).get("depth")
    ]
    return sorted(rows, key=lambda r: r["duration_ms"], reverse=True)


def list_timelines(root: Union[str, Path]) -> list[Path]:
    """``_timeline`` 폴더의 실행 파일들(최신 순)."""
    folder = Path(root)
    if not folder.is_dir():
        return []
    return sorted(folder.glob("*.jsonl"), key=lambda p: p.name, reverse=True)


# --------------------------------------------------------------------------- #
# 싱글턴 (llm.tracing._TRACER 와 같은 패턴 — 새 패턴을 만들지 않는다)
# --------------------------------------------------------------------------- #
_TIMELINE: Union[Timeline, NullTimeline] = NullTimeline()


def get_timeline() -> Union[Timeline, NullTimeline]:
    return _TIMELINE


def set_timeline(line: Union[Timeline, NullTimeline]) -> None:
    global _TIMELINE
    _TIMELINE = line


def reset_timeline() -> None:
    set_timeline(NullTimeline())


def emit(kind: str, name: str, **kwargs: Any) -> None:
    """현재 타임라인에 이벤트를 흘린다 — **호출부가 쓰는 유일한 함수**.

    싱글턴을 몰라도 되고, 설정 전이면 조용히 아무 일도 하지 않는다(연결 점검 등
    파이프라인 밖 경로에서도 안전하게 불릴 수 있어야 한다).
    """
    try:
        _TIMELINE.event(kind, name, **kwargs)
    except Exception as exc:  # noqa: BLE001 — 어떤 경우에도 실행을 막지 않는다
        logger.debug("타임라인 이벤트 무시: %s", exc)


# --------------------------------------------------------------------------- #
# 설정 연결
# --------------------------------------------------------------------------- #
def timeline_enabled(config: Any) -> bool:
    return bool(getattr(getattr(config, "logging", None), "timeline", False))


def timeline_dir(config: Any) -> str:
    """기록 위치. 비어 있으면 ``<artifacts_dir>/_timeline``.

    artifacts 옆에 두는 것은 진단 산출물이 한자리에 모여야 하기 때문이다
    (``_traces``/``_runs`` 와 같은 규약). ⚠️ ``artifact_reader.RESERVED_DIRS`` 에
    ``_timeline`` 이 들어 있어야 현미경이 이 폴더를 문서로 오인하지 않는다.
    """
    explicit = str(getattr(getattr(config, "logging", None), "timeline_dir", "") or "")
    if explicit:
        return explicit
    root = str(getattr(getattr(config, "fact", None), "artifacts_dir", "artifacts"))
    return os.path.join(root, "_timeline")


def build_timeline(
    config: Any, *, console: Optional[bool] = None, label: str = ""
) -> Union[Timeline, NullTimeline]:
    """설정으로 타임라인을 만든다(끄면 :class:`NullTimeline`).

    ``console`` 을 명시하면 그것이 이긴다 — CLI 의 ``--quiet`` 가 설정보다 위다.
    """
    if not timeline_enabled(config):
        return NullTimeline()
    if console is None:
        console = bool(getattr(config.logging, "timeline_console", True))
    name = label or f"{datetime.now():%Y%m%d_%H%M%S}"
    return Timeline(Path(timeline_dir(config)) / f"{name}.jsonl", console=console)


def start_timeline(
    config: Any, *, console: Optional[bool] = None, label: str = ""
) -> str:
    """타임라인을 만들어 싱글턴에 세우고 경로를 반환한다(꺼져 있으면 빈 문자열)."""
    line = build_timeline(config, console=console, label=label)
    set_timeline(line)
    return str(getattr(line, "path", "") or "")

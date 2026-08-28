"""LLM 단계 공용 유틸 — JSON 파싱, 호출 예산, 입력 지문.

fact 파이프라인의 모든 LLM 단계(Profiler/Schema/...)가 공유한다.

- :func:`parse_json_object`: 응답 문자열 → JSON dict(코드펜스/잡음에 관대).
- :func:`fingerprint_for`: 입력 지문(캐시 무효화 판단용).
- :class:`LlmRunner`: chat 클라이언트 래퍼. 파싱 실패 1회 재시도 + **문서당 호출 예산**
  (결정 #2)을 강제한다. 단계 함수는 :meth:`LlmRunner.complete_json` 만 호출한다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any, Optional

from .. import timeline
from ..llm.tracing import current_stage

logger = logging.getLogger(__name__)


class LlmBudgetExceeded(RuntimeError):
    """문서당 LLM 호출 예산을 초과했을 때."""


def parse_json_object(raw: str) -> Optional[dict]:
    """응답 문자열 → JSON dict. 실패 시 ``None``.

    1) 통째로 ``json.loads`` 시도, 2) 실패하면 첫 ``{...}`` 블록을 추려 재시도.
    (``readers/header_detect.py`` 의 파싱 패턴과 동일.)
    """
    if not raw:
        return None
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, TypeError):
        pass
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            obj = json.loads(match.group(0))
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def fingerprint_for(*parts: str) -> str:
    """입력 지문(sha1 12자). 같은 입력이면 같은 값 → ArtifactStore 캐시 키."""
    h = hashlib.sha1()
    for p in parts:
        h.update(str(p).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:12]


_RETRY_NUDGE = (
    "\n\n[주의] 직전 응답이 JSON 파싱에 실패했습니다. 설명·마크다운 없이 "
    "올바른 JSON 객체 하나만 출력하세요."
)


class LlmRunner:
    """chat 클라이언트를 감싸 JSON 응답을 얻고 호출 예산을 강제한다."""

    def __init__(self, chat: Any, *, max_calls: int = 50, temperature: float = 0.0) -> None:
        self.chat = chat
        self.max_calls = max_calls
        self.temperature = temperature
        self.calls = 0
        self.retries = 0  # 파싱 실패로 다시 호출한 횟수(계측 — F3.5)
        self.parse_failures = 0  # JSON 파싱에 실패한 응답 수(재시도 성공분 포함)
        self.structured_calls = 0  # 스키마를 실제로 실어 보낸 호출 수

    def stats(self) -> dict[str, int]:
        """계측값 — 문서별 ``run_stats.json`` 에 실린다(F3.5).

        ``parse_failures`` 가 크면 모델/프롬프트의 JSON 준수도가 낮다는 뜻이고,
        이 분포가 F4b(Repair Loop) 설계의 입력이 된다.

        ``structured_calls`` 가 ``calls`` 보다 **적으면** 중간에 구조화 출력이 꺼진 것이다
        (서버가 스키마를 거절해 백엔드가 강등했거나, pydantic 이 없거나, 그 단계에 와이어
        모델이 없거나). 강등은 화면에 한 번만 알리므로 로그를 놓쳤을 때 여기가 증거다.
        """
        return {
            "calls": self.calls,
            "retries": self.retries,
            "parse_failures": self.parse_failures,
            "structured_calls": self.structured_calls,
        }

    def _call_kwargs(self, schema: Optional[dict]) -> dict[str, Any]:
        """``chat.complete`` 에 넘길 키워드. **조건이 안 맞으면 스키마 키를 아예 뺀다.**

        이 함수의 존재 이유가 곧 이 기능의 가장 큰 제약이다: 테스트의 가짜 chat 36개와
        ``scripts/compare_engines.py`` 가 전부 ``def complete(self, system, user, *,
        temperature=0.0)`` 이고 ``**kwargs`` 를 받는 것이 **하나도 없다.** 무조건 넘기면
        37개가 ``TypeError`` 로 동시에 깨진다. 그래서 넘길지 말지를 **받는 쪽이 스스로 밝힌
        능력**으로 정한다 — ``handles_rate_limit``/``last_usage`` 와 같은 규약이고,
        :class:`~contentcompare.llm.base.LLMClient` 독스트링이 계약서다.

        ⚠️ **플래그는 매 호출 읽는다.** ``__init__`` 으로 올리지 말 것 — 서버가 스키마를
        거절하면 백엔드가 이 실행 동안 자기를 강등하는데
        (:meth:`~contentcompare.llm.langchain_backend.LangChainBackend._disable_structured`),
        캐시해 두면 그 강등이 반영되지 않아 남은 수백 회가 전부 같은 400 에 부딪힌다.
        한 번의 ``getattr`` 은 그 위험을 살 만큼 비싸지 않다.

        래퍼(``RateLimitedChat``/``TracedChat``)에는 이 속성이 없지만 둘 다 ``__getattr__``
        로 안쪽에 위임하므로 값은 실제 백엔드에서 온다 — **위임이 제약이 아니라 장치로
        쓰이는 자리다**(메서드였다면 추적을 우회했을 그 위임이다).
        """
        kwargs: dict[str, Any] = {"temperature": self.temperature}
        if schema and getattr(self.chat, "supports_structured_output", False):
            kwargs["schema"] = schema
            self.structured_calls += 1
        return kwargs

    def complete_json(self, system: str, user: str, *, retries: int = 1,
                      schema: Optional[dict] = None) -> dict:
        """system/user 프롬프트로 chat 을 호출해 JSON dict 를 얻는다.

        파싱 실패 시 교정 지시를 덧붙여 ``retries`` 회 재시도. 예산 초과 시
        :class:`LlmBudgetExceeded`, 끝내 파싱 실패면 :class:`ValueError`.

        ``schema``(JSON Schema dict, 보통
        :func:`~contentcompare.fact.schemas.schema_for` 가 만든 것)를 주면 **백엔드가
        그것을 이해한다고 스스로 밝힌 경우에만** 서버에 모양을 강제한다(:meth:`_call_kwargs`).
        주지 않거나 백엔드가 지원을 선언하지 않으면 오늘과 **완전히 같은 호출**이 나간다.

        ⚠️ ``schema`` 는 **파싱을 대체하지 않는다.** strict 가 걸려도 ``parse_json_object``
        와 재시도는 그대로 돈다 — 폴백 경로(ollama·json_object·강등 후)에서는 여전히 모양이
        틀릴 수 있고, ``parse_failures`` 는 F4b(Repair Loop) 설계의 입력이라 계속 모아야
        한다. 오히려 **그 숫자가 0 으로 떨어지는 것이 이 기능의 성과 지표다.**
        """
        last_raw: Optional[str] = None
        for attempt in range(retries + 1):
            if self.calls >= self.max_calls:
                raise LlmBudgetExceeded(
                    f"LLM 호출 예산 초과(max_calls={self.max_calls})"
                )
            self.calls += 1
            if attempt > 0:
                self.retries += 1
            prompt = user if attempt == 0 else user + _RETRY_NUDGE
            raw = self.chat.complete(system, prompt, **self._call_kwargs(schema))
            obj = parse_json_object(raw)
            if obj is not None:
                return obj
            last_raw = raw
            self.parse_failures += 1
            logger.warning("JSON 파싱 실패(attempt %d): %r", attempt + 1, raw)
            # 전송 재시도(:mod:`contentcompare.llm.http`)와 **원인이 다르다** —
            # 이쪽은 응답이 왔는데 모양이 틀린 것이라 조치가 프롬프트·모델 쪽이다.
            # 타임라인에서 갈리지 않으면 둘을 같은 문제로 오해한다.
            timeline.emit(
                timeline.RETRY, current_stage(depth=1), status="error",
                attempt=attempt + 1, max=retries + 1, reason="JSON 파싱 실패",
                output_chars=len(raw or ""),
            )
        raise ValueError(f"LLM JSON 파싱 실패(재시도 {retries}회): {last_raw!r}")

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

    def stats(self) -> dict[str, int]:
        """계측값 — 문서별 ``run_stats.json`` 에 실린다(F3.5).

        ``parse_failures`` 가 크면 모델/프롬프트의 JSON 준수도가 낮다는 뜻이고,
        이 분포가 F4b(Repair Loop) 설계의 입력이 된다.
        """
        return {
            "calls": self.calls,
            "retries": self.retries,
            "parse_failures": self.parse_failures,
        }

    def complete_json(self, system: str, user: str, *, retries: int = 1) -> dict:
        """system/user 프롬프트로 chat 을 호출해 JSON dict 를 얻는다.

        파싱 실패 시 교정 지시를 덧붙여 ``retries`` 회 재시도. 예산 초과 시
        :class:`LlmBudgetExceeded`, 끝내 파싱 실패면 :class:`ValueError`.
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
            raw = self.chat.complete(system, prompt, temperature=self.temperature)
            obj = parse_json_object(raw)
            if obj is not None:
                return obj
            last_raw = raw
            self.parse_failures += 1
            logger.warning("JSON 파싱 실패(attempt %d): %r", attempt + 1, raw)
        raise ValueError(f"LLM JSON 파싱 실패(재시도 {retries}회): {last_raw!r}")

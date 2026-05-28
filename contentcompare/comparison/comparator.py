"""LLM 비교 실행기.

기준 항목과 후보들을 프롬프트로 만들어 LLM 에 투입하고, JSON 응답을
:class:`ComparisonResult` 로 파싱한다.
"""

from __future__ import annotations

import json
import re

from ..llm.base import LLMClient
from ..models import Candidate, ComparisonResult, DocItem, Verdict
from . import prompts


class Comparator:
    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    def compare(self, reference: DocItem, candidates: list[Candidate]) -> ComparisonResult:
        # 후보가 아예 없으면 LLM 호출 없이 NOT_FOUND.
        if not candidates:
            return ComparisonResult(
                reference=reference,
                verdict=Verdict.NOT_FOUND,
                reasoning="유사도 임계값을 넘는 후보가 없어 비교 대상을 찾지 못함.",
                candidates=[],
            )

        user = prompts.build_user_prompt(reference, candidates)
        raw = self.llm.complete(prompts.SYSTEM_PROMPT, user)
        parsed = self._parse(raw)

        verdict = self._to_verdict(parsed.get("verdict"))
        matched = [str(x) for x in parsed.get("matched_item_ids", []) or []]
        reasoning = str(parsed.get("reasoning") or "").strip() or raw.strip()

        return ComparisonResult(
            reference=reference,
            verdict=verdict,
            reasoning=reasoning,
            candidates=candidates,
            matched_item_ids=matched,
        )

    # ------------------------------------------------------------------ #
    @staticmethod
    def _parse(raw: str) -> dict:
        """LLM 응답에서 JSON 객체를 추출/파싱. 실패 시 빈 dict."""
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        # ```json ... ``` 혹은 본문 중 첫 {...} 블록을 추출 시도.
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        # TODO: 파싱 실패 시 LLM 재요청(JSON 강제) 루프 추가.
        return {}

    @staticmethod
    def _to_verdict(value) -> Verdict:
        try:
            return Verdict(str(value).lower())
        except ValueError:
            return Verdict.DIFFERENT

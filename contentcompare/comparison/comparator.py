"""LLM 비교 실행기.

기준 항목과 후보들을 프롬프트로 만들어 LLM 에 투입하고, JSON 응답을
:class:`ComparisonResult` / :class:`RecordResult` 로 파싱한다.

엑셀 레코드(행)는 **행 단위로 종합 판정**한다(요청 1번). 사용자가 제공한 도메인 지식이
있으면 프롬프트에 참고 자료로 주입한다(요청 5번). 프롬프트와 LLM 원문 응답은 디버그
로그로 남겨, 오판 원인을 추적할 수 있게 한다(요청 4번).
"""

from __future__ import annotations

import json
import logging
import re

from ..llm.base import LLMClient
from ..models import (
    Candidate,
    ComparisonResult,
    DocItem,
    FieldFinding,
    RecordItem,
    RecordResult,
    Verdict,
)
from . import prompts

logger = logging.getLogger("contentcompare.comparison")


class Comparator:
    def __init__(self, llm: LLMClient, *, knowledge: str = "") -> None:
        self.llm = llm
        self.knowledge = knowledge or ""

    def compare(self, reference: DocItem, candidates: list[Candidate]) -> ComparisonResult:
        # 후보가 아예 없으면 LLM 호출 없이 NOT_FOUND.
        if not candidates:
            return ComparisonResult(
                reference=reference,
                verdict=Verdict.NOT_FOUND,
                reasoning="유사도 임계값을 넘는 후보가 없어 비교 대상을 찾지 못함.",
                candidates=[],
            )

        user = prompts.build_user_prompt(reference, candidates, knowledge=self.knowledge)
        parsed = self._complete_json(prompts.SYSTEM_PROMPT, user)

        verdict = self._to_verdict(parsed.get("verdict"))
        matched = [str(x) for x in parsed.get("matched_item_ids", []) or []]
        reasoning = str(parsed.get("reasoning") or "").strip() or "(사유 없음)"

        return ComparisonResult(
            reference=reference,
            verdict=verdict,
            reasoning=reasoning,
            candidates=candidates,
            matched_item_ids=matched,
        )

    # ------------------------------------------------------------------ #
    def compare_record(self, record: RecordItem, candidates: list[Candidate]) -> RecordResult:
        """레코드(행)를 후보와 대조해 **행 단위로 종합 판정**한다(엑셀 hybrid, 요청 1번)."""
        # 후보가 없으면 LLM 없이 not_found.
        if not candidates:
            return RecordResult(
                record=record,
                verdict=Verdict.NOT_FOUND,
                reasoning="관련 후보가 없어 비교 대상을 찾지 못함.",
                candidates=[],
                matched_item_ids=[],
                findings=[
                    FieldFinding(field=f, found=False, note="관련 후보 없음")
                    for f in record.fields
                ],
            )

        user = prompts.build_record_prompt(record, candidates, knowledge=self.knowledge)
        parsed = self._complete_json(prompts.RECORD_SYSTEM_PROMPT, user)

        valid_cand_ids = {c.item.item_id for c in candidates}
        matched = [
            str(x) for x in (parsed.get("matched_item_ids") or []) if str(x) in valid_cand_ids
        ]
        verdict = self._to_verdict(parsed.get("verdict"))
        reasoning = str(parsed.get("reasoning") or "").strip() or "(사유 없음)"

        # findings 를 field_id → 항목 dict 로 정리.
        by_id: dict[str, dict] = {}
        for entry in (parsed.get("findings") if isinstance(parsed, dict) else None) or []:
            if isinstance(entry, dict) and entry.get("field_id"):
                by_id[str(entry["field_id"])] = entry

        findings: list[FieldFinding] = []
        for fc in record.fields:
            entry = by_id.get(fc.field_id)
            if entry is None:
                findings.append(
                    FieldFinding(field=fc, found=False, note="LLM 응답에 항목 내역 누락(검토 필요).")
                )
                continue
            findings.append(
                FieldFinding(
                    field=fc,
                    found=bool(entry.get("found")),
                    note=str(entry.get("note") or "").strip() or "(근거 없음)",
                )
            )

        # 응답 파싱이 완전히 실패하면 findings 기반으로 보수적 집계.
        if not parsed:
            verdict = self._aggregate(findings)

        return RecordResult(
            record=record,
            verdict=verdict,
            reasoning=reasoning,
            candidates=candidates,
            matched_item_ids=matched,
            findings=findings,
        )

    # ------------------------------------------------------------------ #
    @staticmethod
    def _aggregate(findings: list[FieldFinding]) -> Verdict:
        """파싱 실패 등으로 verdict 가 없을 때 findings 의 found 비율로 보수적 추정."""
        if not findings or not any(f.found for f in findings):
            return Verdict.NOT_FOUND
        return Verdict.PARTIAL  # 찾았으나 일치 여부 불명 → 보수적으로 부분일치

    def _complete_json(self, system: str, user: str) -> dict:
        """JSON 응답을 받아 파싱한다. 1회 실패 시 'JSON만 출력' 지시로 재요청."""
        logger.debug("LLM 프롬프트(system):\n%s", system)
        logger.debug("LLM 프롬프트(user):\n%s", user)
        raw = self.llm.complete(system, user)
        logger.debug("LLM 응답(원문):\n%s", raw)
        parsed = self._parse(raw)
        if parsed:
            return parsed
        logger.warning("LLM 응답 JSON 파싱 실패 → 'JSON만 출력' 지시로 재요청")
        retry_user = user + "\n\n위 형식의 JSON 객체만 출력하세요. 다른 텍스트나 코드펜스는 금지합니다."
        raw2 = self.llm.complete(system, retry_user)
        logger.debug("LLM 응답(재요청 원문):\n%s", raw2)
        return self._parse(raw2)

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
        return {}

    @staticmethod
    def _to_verdict(value) -> Verdict:
        try:
            return Verdict(str(value).lower())
        except ValueError:
            return Verdict.DIFFERENT

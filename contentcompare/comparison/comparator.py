"""LLM 비교 실행기.

기준 항목과 후보들을 프롬프트로 만들어 LLM 에 투입하고, JSON 응답을
:class:`ComparisonResult` 로 파싱한다.
"""

from __future__ import annotations

import json
import re

from ..llm.base import LLMClient
from ..models import (
    Candidate,
    ComparisonResult,
    DocItem,
    FieldResult,
    RecordItem,
    RecordResult,
    Verdict,
)
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
    def compare_record(self, record: RecordItem, candidates: list[Candidate]) -> RecordResult:
        """레코드(행)의 각 필드를 후보와 대조해 필드별로 판정한다(엑셀 hybrid)."""
        # 후보가 없으면 LLM 없이 전 필드 not_found.
        if not candidates:
            return RecordResult(
                record=record,
                candidates=[],
                fields=[
                    FieldResult(
                        field=f,
                        verdict=Verdict.NOT_FOUND,
                        reasoning="관련 후보가 없어 비교 대상을 찾지 못함.",
                    )
                    for f in record.fields
                ],
            )

        user = prompts.build_record_prompt(record, candidates)
        parsed = self._complete_json(prompts.RECORD_SYSTEM_PROMPT, user)

        # 응답을 field_id → 항목 dict 로 정리.
        by_id: dict[str, dict] = {}
        for entry in (parsed.get("fields") if isinstance(parsed, dict) else None) or []:
            if isinstance(entry, dict) and entry.get("field_id"):
                by_id[str(entry["field_id"])] = entry

        valid_cand_ids = {c.item.item_id for c in candidates}
        fields: list[FieldResult] = []
        for fc in record.fields:
            entry = by_id.get(fc.field_id)
            if entry is None:
                # 응답에 누락된 필드 → 사람이 확인하도록 different 로 표시.
                fields.append(
                    FieldResult(
                        field=fc,
                        verdict=Verdict.DIFFERENT,
                        reasoning="LLM 응답에 해당 필드 판정이 누락됨(검토 필요).",
                    )
                )
                continue
            matched = [
                str(x) for x in (entry.get("matched_item_ids") or []) if str(x) in valid_cand_ids
            ]
            reasoning = str(entry.get("reasoning") or "").strip() or "(사유 없음)"
            fields.append(
                FieldResult(
                    field=fc,
                    verdict=self._to_verdict(entry.get("verdict")),
                    reasoning=reasoning,
                    matched_item_ids=matched,
                )
            )
        return RecordResult(record=record, candidates=candidates, fields=fields)

    def _complete_json(self, system: str, user: str) -> dict:
        """JSON 응답을 받아 파싱한다. 1회 실패 시 'JSON만 출력' 지시로 재요청."""
        raw = self.llm.complete(system, user)
        parsed = self._parse(raw)
        if parsed:
            return parsed
        retry_user = user + "\n\n위 형식의 JSON 객체만 출력하세요. 다른 텍스트나 코드펜스는 금지합니다."
        raw2 = self.llm.complete(system, retry_user)
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
        # TODO: 파싱 실패 시 LLM 재요청(JSON 강제) 루프 추가.
        return {}

    @staticmethod
    def _to_verdict(value) -> Verdict:
        try:
            return Verdict(str(value).lower())
        except ValueError:
            return Verdict.DIFFERENT

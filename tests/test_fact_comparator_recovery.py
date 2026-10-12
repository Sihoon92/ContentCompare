"""F5 판정 LLM 출력 절단의 항목별 복구·강등 테스트."""

from __future__ import annotations

import json

import pytest

import contentcompare.fact.fact_comparator as comparator_module
from contentcompare.fact.fact_comparator import BY_LLM, UNKNOWN, FactComparator
from contentcompare.fact.fact_matcher import EMBED, MatchCandidate
from contentcompare.fact.fact_models import Fact, FactSet
from contentcompare.fact.fact_store import DocFacts
from contentcompare.fact.llm_stage import LlmRunner
from contentcompare.fact.record_models import Attribute
from contentcompare.llm.truncation import LengthLimitError


_COMPARE_SCHEMA = {
    "title": "compare", "type": "object", "properties": {},
    "required": [], "additionalProperties": False,
}
_VALID = {"result": "unknown", "findings": [], "reason": "복구 후 판단 보류"}


def _fact(fid: str, name: str, attr: str) -> Fact:
    return Fact(
        fact_id=fid,
        entity_name=name,
        attributes={attr: Attribute(10, "모름")},
        evidence_text=f"{name} 근거",
    )


def _inputs():
    ref = _fact("ref-1", "충전 조건", "lower_limit")
    target = _fact("target-1", "Charging condition", "minimum")
    candidates = [MatchCandidate(target, 0.8, EMBED, needs_review=True)]
    doc = DocFacts("대상.pptx", "ppt", FactSet(facts=[target]))
    return ref, candidates, doc


class _SequenceChat:
    supports_structured_output = True

    def __init__(self, *responses, retry_capable: bool = True):
        self.responses = list(responses)
        self.supports_schema_removal_retry = retry_capable
        self.calls = []

    def complete(self, system, user, *, temperature=0.0, schema=None):
        self.calls.append({"system": system, "user": user, "schema": schema})
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        if isinstance(response, dict):
            return json.dumps(response, ensure_ascii=False)
        return response


@pytest.fixture(autouse=True)
def _force_compare_schema(monkeypatch):
    monkeypatch.setattr(comparator_module, "schema_for", lambda stage: _COMPARE_SCHEMA)


def _compare(chat: _SequenceChat, *, max_calls: int = 50):
    comparator = FactComparator(runner=LlmRunner(chat, max_calls=max_calls))
    return comparator, comparator.compare(*_inputs())


def _length() -> LengthLimitError:
    return LengthLimitError("출력 길이 한도", output="{반복", output_chars=16184,
                            backend="fake")


def test_length_limit_retries_once_without_schema_and_recovers():
    """절단 뒤 schema만 제거한 한 번의 호출로 유효한 응답을 복구한다."""
    chat = _SequenceChat(_length(), _VALID)

    comparator, result = _compare(chat)

    assert [call["schema"] for call in chat.calls] == [_COMPARE_SCHEMA, None]
    assert result.result == UNKNOWN and result.decided_by == BY_LLM
    assert result.failure_reason == ""
    assert comparator.stats()["llm_schema_retries"] == 1
    assert comparator.stats()["llm_schema_recoveries"] == 1
    assert comparator.stats()["llm_failures"] == 0


def test_second_length_limit_becomes_unknown_without_third_call():
    """schema 제거 호출도 절단되면 그 항목만 unknown이며 세 번째 호출은 없다."""
    chat = _SequenceChat(_length(), _length())

    comparator, result = _compare(chat)

    assert len(chat.calls) == 2
    assert result.result == UNKNOWN
    assert result.failure_reason == "output_truncated"
    assert "schema 제거 재시도" in result.reason
    assert comparator.stats()["llm_truncations"] == 2
    assert comparator.stats()["llm_output_truncated"] == 1
    assert comparator.stats()["llm_failures"] == 1


def test_length_retry_parse_failure_does_not_start_parse_retry():
    """절단 복구 응답이 JSON이 아니어도 추가 생성 없이 parse_failure로 끝낸다."""
    chat = _SequenceChat(_length(), "JSON 아님")

    comparator, result = _compare(chat)

    assert len(chat.calls) == 2
    assert result.result == UNKNOWN and result.failure_reason == "parse_failure"
    assert comparator.runner.retries == 0
    assert comparator.stats()["llm_parse_failures"] == 1


def test_length_limit_at_last_budget_becomes_budget_unknown_without_retry():
    """첫 절단이 마지막 예산을 썼다면 재시도를 시작했다고 기록하지 않는다."""
    chat = _SequenceChat(_length())

    comparator, result = _compare(chat, max_calls=1)

    assert len(chat.calls) == 1
    assert result.result == UNKNOWN and result.failure_reason == "budget_exceeded"
    assert comparator.stats()["llm_schema_retries"] == 0
    assert comparator.stats()["llm_budget_exceeded"] == 1


def test_length_without_request_change_capability_does_not_repeat_same_request():
    """json_object/off처럼 schema 제거가 요청을 바꾸지 못하면 동일 요청을 반복하지 않는다."""
    chat = _SequenceChat(_length(), _VALID, retry_capable=False)

    comparator, result = _compare(chat)

    assert len(chat.calls) == 1
    assert result.result == UNKNOWN and result.failure_reason == "output_truncated"
    assert "제거할 JSON Schema 제약이 없어" in result.reason
    assert comparator.stats()["llm_schema_retries"] == 0


def test_initial_parse_failure_is_counted_separately_from_budget_and_length():
    """기존 파싱 재시도를 모두 써도 JSON이 아니면 parse_failure 한 건이다."""
    chat = _SequenceChat("아님", "여전히 아님")

    comparator, result = _compare(chat)

    assert len(chat.calls) == 2
    assert result.result == UNKNOWN and result.failure_reason == "parse_failure"
    assert comparator.stats()["llm_parse_failures"] == 1
    assert comparator.stats()["llm_failures"] == 1


def test_parse_then_length_still_has_only_one_schema_removal_call():
    """초기 파싱 교정 중 절단돼도 절단 이후 생성은 schema 제거 한 번뿐이다."""
    chat = _SequenceChat("JSON 아님", _length(), _VALID)

    comparator, result = _compare(chat)

    assert len(chat.calls) == 3
    assert [call["schema"] for call in chat.calls] == [
        _COMPARE_SCHEMA, _COMPARE_SCHEMA, None,
    ]
    assert result.failure_reason == ""
    assert comparator.runner.retries == 1
    assert comparator.stats()["llm_schema_retries"] == 1
    assert comparator.stats()["llm_schema_recoveries"] == 1


def test_schema_is_restored_for_the_next_comparison_after_recovery():
    """한 항목의 변형 재시도가 뒤 항목의 구조화 출력을 전역으로 끄지 않는다."""
    chat = _SequenceChat(_length(), _VALID, _VALID)
    comparator = FactComparator(runner=LlmRunner(chat))

    first = comparator.compare(*_inputs())
    second = comparator.compare(*_inputs())

    assert first.failure_reason == second.failure_reason == ""
    assert [call["schema"] for call in chat.calls] == [
        _COMPARE_SCHEMA, None, _COMPARE_SCHEMA,
    ]


@pytest.mark.parametrize(
    ("responses", "max_calls"),
    [
        ((_length(), _length()), 50),
        (("bad", "still bad"), 50),
        ((), 0),
    ],
)
def test_operational_unknown_preserves_every_candidate_for_review(responses, max_calls):
    """복수 후보 판정 실패 시 사람이 모든 후보 원문·위치를 검수할 수 있어야 한다."""
    ref, candidates, doc = _inputs()
    second = _fact("target-2", "Charge condition B", "maximum")
    candidates.append(MatchCandidate(second, 0.7, EMBED, needs_review=True))
    doc.facts.facts.append(second)
    comparator = FactComparator(
        runner=LlmRunner(_SequenceChat(*responses), max_calls=max_calls)
    )

    result = comparator.compare(ref, candidates, doc)

    assert result.failure_reason in {
        "output_truncated", "parse_failure", "budget_exceeded",
    }
    assert [fact.fact_id for fact in result.target_facts] == ["target-1", "target-2"]
    assert [target["fact_id"] for target in result.to_dict()["targets"]] == [
        "target-1", "target-2",
    ]

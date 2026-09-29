"""LlmRunner / parse_json_object / fingerprint_for 단위테스트."""

from __future__ import annotations

import pytest

from contentcompare.fact.llm_stage import (
    LlmBudgetExceeded,
    LlmRunner,
    fingerprint_for,
    parse_json_object,
)


# --------------------------------------------------------------------------- #
# parse_json_object
# --------------------------------------------------------------------------- #
def test_parse_pure_json():
    assert parse_json_object('{"a": 1}') == {"a": 1}


def test_parse_json_with_codefence_and_noise():
    raw = "다음은 결과입니다:\n```json\n{\"a\": 1, \"b\": [2,3]}\n```\n끝."
    assert parse_json_object(raw) == {"a": 1, "b": [2, 3]}


def test_parse_non_object_returns_none():
    assert parse_json_object("[1,2,3]") is None  # dict 아님
    assert parse_json_object("그냥 텍스트") is None
    assert parse_json_object("") is None


def test_fingerprint_deterministic_and_sensitive():
    a = fingerprint_for("compact", "model-x", "v1")
    b = fingerprint_for("compact", "model-x", "v1")
    c = fingerprint_for("compact", "model-y", "v1")
    assert a == b and a != c
    assert len(a) == 12


# --------------------------------------------------------------------------- #
# LlmRunner
# --------------------------------------------------------------------------- #
class _ScriptedChat:
    """미리 정한 응답을 순서대로 반환하는 가짜 chat."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.seen = []

    def complete(self, system, user, *, temperature=0.0):
        self.seen.append(user)
        return self.responses.pop(0)


def test_complete_json_success_increments_calls():
    chat = _ScriptedChat(['{"ok": true}'])
    runner = LlmRunner(chat, max_calls=5)
    assert runner.complete_json("sys", "user") == {"ok": True}
    assert runner.calls == 1


def test_complete_json_retries_then_succeeds():
    chat = _ScriptedChat(["깨진 응답", '{"ok": 1}'])
    runner = LlmRunner(chat)
    assert runner.complete_json("sys", "user") == {"ok": 1}
    assert runner.calls == 2
    assert "JSON" in chat.seen[1]  # 2회차엔 교정 지시가 붙음


def test_complete_json_fails_after_retries():
    chat = _ScriptedChat(["nope", "still nope"])
    runner = LlmRunner(chat, max_calls=5)
    with pytest.raises(ValueError):
        runner.complete_json("sys", "user")
    assert runner.calls == 2  # retries=1 → 2회 호출


def test_budget_exceeded():
    chat = _ScriptedChat(["x", "y", "z"])
    runner = LlmRunner(chat, max_calls=1)
    # 1회차 파싱 실패 후 재시도하려는데 예산(1) 소진 → 예외.
    with pytest.raises(LlmBudgetExceeded):
        runner.complete_json("sys", "user")
    assert runner.calls == 1


# --------------------------------------------------------------------------- #
# 계측(F3.5) — 모델의 JSON 준수도를 수치로 남긴다(F4b 설계 입력)
# --------------------------------------------------------------------------- #
def test_stats_counts_retries_and_parse_failures():
    chat = _ScriptedChat(["깨진 응답", '{"ok": 1}'])
    runner = LlmRunner(chat)
    runner.complete_json("sys", "user")
    assert runner.stats() == {"calls": 2, "retries": 1, "parse_failures": 1,
                              "structured_calls": 0}


def test_stats_clean_run_has_no_failures():
    runner = LlmRunner(_ScriptedChat(['{"ok": 1}']))
    runner.complete_json("sys", "user")
    assert runner.stats() == {"calls": 1, "retries": 0, "parse_failures": 0,
                              "structured_calls": 0}


# --------------------------------------------------------------------------- #
# 구조화 출력 관문 — 가짜 chat 37개를 지키는 유일한 코드
# --------------------------------------------------------------------------- #
_SCHEMA = {"title": "t", "type": "object", "properties": {},
           "required": [], "additionalProperties": False}


def test_schema_never_reaches_a_chat_that_did_not_ask_for_it():
    """``_ScriptedChat`` 은 이 파일의 다른 테스트가 쓰는 가짜와 같은 서명이다 —
    스키마를 줘도 인자가 **안 나가야** 37개가 안 깨진다."""
    chat = _ScriptedChat(['{"ok": 1}'])
    runner = LlmRunner(chat)
    assert runner.complete_json("sys", "user", schema=_SCHEMA) == {"ok": 1}
    assert runner.stats()["structured_calls"] == 0


class _SchemaAwareChat:
    """``supports_structured_output`` 을 선언한 백엔드 대역."""

    supports_structured_output = True

    def __init__(self, responses):
        self.responses = list(responses)
        self.seen = []

    def complete(self, system, user, *, temperature=0.0, schema=None):
        self.seen.append(schema)
        return self.responses.pop(0)


def test_schema_is_sent_when_the_backend_declares_support():
    chat = _SchemaAwareChat(['{"ok": 1}'])
    runner = LlmRunner(chat)
    runner.complete_json("sys", "user", schema=_SCHEMA)
    assert chat.seen == [_SCHEMA]
    assert runner.stats()["structured_calls"] == 1


def test_no_schema_argument_means_no_schema_even_for_a_capable_backend():
    chat = _SchemaAwareChat(['{"ok": 1}'])
    LlmRunner(chat).complete_json("sys", "user")
    assert chat.seen == [None]


def test_the_retry_carries_the_schema_too():
    """교정 재시도가 스키마 없이 나가면 폴백 경로로 조용히 떨어진다."""
    chat = _SchemaAwareChat(["깨진 응답", '{"ok": 1}'])
    runner = LlmRunner(chat)
    runner.complete_json("sys", "user", schema=_SCHEMA)
    assert chat.seen == [_SCHEMA, _SCHEMA]
    assert runner.stats()["structured_calls"] == 2


def test_the_flag_is_read_per_call_so_a_mid_run_downgrade_takes_effect():
    """플래그를 ``__init__`` 으로 올리는 리팩터링을 막는다 — 그러면 강등 뒤 남은 수백 회가
    전부 같은 400 에 부딪힌다."""
    class _Downgrading(_SchemaAwareChat):
        def complete(self, system, user, *, temperature=0.0, schema=None):
            self.seen.append(schema)
            self.supports_structured_output = False   # 서버가 거절했다 치고 강등
            return self.responses.pop(0)

    chat = _Downgrading(['{"ok": 1}', '{"ok": 2}'])
    runner = LlmRunner(chat)
    runner.complete_json("sys", "user", schema=_SCHEMA)
    runner.complete_json("sys", "user", schema=_SCHEMA)
    assert chat.seen == [_SCHEMA, None]
    assert runner.stats()["structured_calls"] == 1


def test_parse_failures_are_still_counted_under_structured_output():
    """strict 가 파싱을 대체하지 않는다 — 이 숫자가 0 으로 떨어지는 것이 성과 지표다."""
    chat = _SchemaAwareChat(["깨진 응답", '{"ok": 1}'])
    runner = LlmRunner(chat)
    runner.complete_json("sys", "user", schema=_SCHEMA)
    assert runner.stats()["parse_failures"] == 1

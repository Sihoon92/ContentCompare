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
    assert runner.stats() == {"calls": 2, "retries": 1, "parse_failures": 1}


def test_stats_clean_run_has_no_failures():
    runner = LlmRunner(_ScriptedChat(['{"ok": 1}']))
    runner.complete_json("sys", "user")
    assert runner.stats() == {"calls": 1, "retries": 0, "parse_failures": 0}

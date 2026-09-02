"""토큰 사용량 정규화 — 백엔드 세 곳의 다른 이름을 한 모양으로 모으는지 검증한다.

실제 응답 모양을 그대로 쓴다(로그 `logs/contentcompare_20260817_145457.log` 에서 가져온
Ollama 응답 포함) — 손으로 지어낸 모양만 통과하는 정규화는 실행에서 조용히 0 을 남긴다.
"""

from __future__ import annotations

from contentcompare.llm.usage import Usage, from_response


# --------------------------------------------------------------------------- #
# 백엔드별 응답 모양
# --------------------------------------------------------------------------- #
def test_ollama_shape():
    """Ollama 는 최상위에 ``prompt_eval_count``/``eval_count`` 로 준다."""
    data = {
        "model": "gemma4:12b",
        "message": {"role": "assistant", "content": "{}"},
        "done": True,
        "prompt_eval_count": 776,
        "eval_count": 166,
        "eval_duration": 3366185000,
    }
    assert from_response(data) == Usage(input_tokens=776, output_tokens=166)


def test_openai_shape():
    """사내/OpenAI 호환은 ``usage`` 안에 ``prompt_tokens``/``completion_tokens``."""
    data = {
        "choices": [{"message": {"content": "{}"}}],
        "usage": {"prompt_tokens": 3204, "completion_tokens": 512, "total_tokens": 3716},
    }
    assert from_response(data) == Usage(input_tokens=3204, output_tokens=512)


def test_langchain_usage_metadata_attribute():
    """langchain AIMessage 는 dict 가 아니라 **속성**으로 준다."""

    class FakeMessage:
        content = "{}"
        usage_metadata = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}

    assert from_response(FakeMessage()) == Usage(input_tokens=100, output_tokens=20)


def test_langchain_response_metadata_fallback():
    """``usage_metadata`` 가 없는 버전은 ``response_metadata.token_usage`` 로 폴백한다."""

    class FakeMessage:
        content = "{}"
        response_metadata = {
            "model_name": "gpt-4o",
            "token_usage": {"prompt_tokens": 7, "completion_tokens": 3},
        }

    assert from_response(FakeMessage()) == Usage(input_tokens=7, output_tokens=3)


def test_usage_metadata_wins_over_response_metadata():
    """둘 다 있으면 최신 규격(``usage_metadata``)이 이긴다."""

    class FakeMessage:
        usage_metadata = {"input_tokens": 11, "output_tokens": 2}
        response_metadata = {"token_usage": {"prompt_tokens": 99, "completion_tokens": 99}}

    assert from_response(FakeMessage()) == Usage(input_tokens=11, output_tokens=2)


# --------------------------------------------------------------------------- #
# 없을 때 — 추정하지 않는다
# --------------------------------------------------------------------------- #
def test_missing_usage_is_unknown():
    assert from_response({"choices": [{"message": {"content": "hi"}}]}) == Usage()
    assert not from_response({}).known


def test_non_mapping_input_is_safe():
    """문자열·None 이 들어와도 죽지 않는다(추적이 실행을 막지 않는다는 원칙)."""
    assert from_response(None) == Usage()
    assert from_response("응답") == Usage()
    assert from_response(123) == Usage()


def test_garbage_values_ignored():
    """토큰 칸에 숫자가 아닌 값이 오면 0 으로 둔다 — 추정으로 메우지 않는다."""
    assert from_response({"usage": {"prompt_tokens": None, "completion_tokens": "많음"}}) == Usage()


def test_partial_usage_is_known():
    """한쪽만 와도 미상이 아니다 — 있는 만큼 남긴다."""
    got = from_response({"prompt_eval_count": 50})
    assert got == Usage(input_tokens=50, output_tokens=0)
    assert got.known


# --------------------------------------------------------------------------- #
# 타임라인에 실을 모양
# --------------------------------------------------------------------------- #
def test_as_detail_includes_rate():
    """출력 토큰/초 — **배치 크기를 정하는 근거가 이 숫자다.**"""
    detail = Usage(input_tokens=3204, output_tokens=512).as_detail(72_000)
    assert detail["input_tokens"] == 3204
    assert detail["output_tokens"] == 512
    assert detail["tok_per_sec"] == 7.1


def test_as_detail_empty_when_unknown():
    """미상이면 **키 자체를 넣지 않는다** — 0 이 남으면 '토큰 0개'로 읽힌다."""
    assert Usage().as_detail(1000) == {}


def test_as_detail_without_duration_has_no_rate():
    """소요가 0 이면 속도를 만들지 않는다(0 으로 나누지도, 지어내지도 않는다)."""
    detail = Usage(input_tokens=10, output_tokens=5).as_detail(0)
    assert "tok_per_sec" not in detail
    assert detail["input_tokens"] == 10


def test_as_detail_no_output_tokens_has_no_rate():
    """출력 토큰이 없으면 속도도 없다 — 입력만으로 생성 속도를 말할 수 없다."""
    assert "tok_per_sec" not in Usage(input_tokens=10).as_detail(5000)

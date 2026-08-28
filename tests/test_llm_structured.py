"""strict 스키마 생성기(`llm/structured.py`) 단위테스트.

⚠️ **이 파일은 pydantic 을 쓰지 않는다.** 대상 모듈이 ``model_json_schema()`` 덕 타이핑만
요구하므로 생 dict 와 초소형 가짜로 검증한다 — 이 저장소는 파이썬 환경이 둘이고
개발용 ``.venv`` 에는 pydantic 이 없는데 테스트는 양쪽에서 돈다.
"""

from __future__ import annotations

import pytest

from contentcompare.llm.structured import (
    MAX_DEPTH,
    MAX_PROPERTIES,
    looks_like_schema_rejection,
    normalize_mode,
    response_format,
    strict_schema,
)


class _FakeModel:
    """``model_json_schema()`` 만 가진 pydantic 대역. 덕 타이핑이라 이것으로 충분하다."""

    def __init__(self, schema: dict) -> None:
        self._schema = schema

    def model_json_schema(self) -> dict:
        return self._schema


def _obj(**props) -> dict:
    return {"type": "object", "properties": dict(props)}


# --------------------------------------------------------------------------- #
# strict 보정
# --------------------------------------------------------------------------- #
def test_object_gets_additional_properties_false():
    out = strict_schema(_obj(a={"type": "string"}), name="t")
    assert out["additionalProperties"] is False


def test_every_property_becomes_required_even_with_a_default():
    """pydantic 은 기본값이 있으면 required 에서 빼는데, strict 에 선택 필드는 없다."""
    src = _obj(a={"type": "string", "default": ""}, b={"type": "integer"})
    src["required"] = ["b"]
    out = strict_schema(src, name="t")
    assert out["required"] == ["a", "b"]


def test_unsupported_keywords_are_stripped():
    src = _obj(a={"type": "string", "default": "x", "maxLength": 5, "pattern": "^a"})
    out = strict_schema(src, name="t")
    assert out["properties"]["a"] == {"type": "string"}


def test_meaningful_constraints_survive():
    """``enum``/``const`` 는 의미가 있는 제약이라 지우면 스키마가 조용히 헐거워진다."""
    src = _obj(a={"type": "string", "enum": ["x", "y"]})
    out = strict_schema(src, name="t")
    assert out["properties"]["a"]["enum"] == ["x", "y"]


def test_defs_are_not_flattened_but_are_tightened():
    """⚠️ 가장 흔한 실패 — 루트만 조이면 ``$defs`` 안에서 400 이 난다."""
    src = {
        "type": "object",
        "properties": {"item": {"$ref": "#/$defs/Item"}},
        "$defs": {"Item": _obj(name={"type": "string"})},
    }
    out = strict_schema(src, name="t")
    assert out["properties"]["item"] == {"$ref": "#/$defs/Item"}   # 평탄화 안 함
    assert out["$defs"]["Item"]["additionalProperties"] is False   # 조여짐
    assert out["$defs"]["Item"]["required"] == ["name"]


def test_nested_objects_inside_array_items_are_tightened():
    src = _obj(rows={"type": "array", "items": _obj(v={"type": "string"})})
    out = strict_schema(src, name="t")
    assert out["properties"]["rows"]["items"]["additionalProperties"] is False


def test_anyof_branches_are_tightened():
    src = _obj(v={"anyOf": [_obj(a={"type": "string"}), {"type": "null"}]})
    out = strict_schema(src, name="t")
    assert out["properties"]["v"]["anyOf"][0]["additionalProperties"] is False


# --------------------------------------------------------------------------- #
# 거부 — 런타임 400 을 단위테스트 실패로 바꾸는 것이 이 모듈의 값어치다
# --------------------------------------------------------------------------- #
def test_free_key_object_raises_with_the_path_in_the_message():
    """``dict[str, X]`` → ``properties`` 없는 object. 경로가 메시지에 있어야 고칠 수 있다."""
    src = _obj(attributes={"type": "object", "additionalProperties": {"type": "string"}})
    with pytest.raises(ValueError) as err:
        strict_schema(src, name="t")
    assert "$.properties.attributes" in str(err.value)
    assert "parse_attributes" in str(err.value)   # 조치까지 알려 준다


def test_free_key_object_inside_defs_also_raises():
    src = {
        "type": "object",
        "properties": {"r": {"$ref": "#/$defs/R"}},
        "$defs": {"R": _obj(meta={"type": "object", "additionalProperties": True})},
    }
    with pytest.raises(ValueError) as err:
        strict_schema(src, name="t")
    assert "$defs.R" in str(err.value)


def test_untyped_property_raises():
    """``Any`` 는 ``{}`` 로 나오고 서버가 거절한다."""
    with pytest.raises(ValueError) as err:
        strict_schema(_obj(v={}), name="t")
    assert "$.properties.v" in str(err.value)


def test_depth_limit():
    node: dict = _obj(leaf={"type": "string"})
    for _ in range(MAX_DEPTH + 1):
        node = _obj(child=node)
    with pytest.raises(ValueError, match="중첩"):
        strict_schema(node, name="t")


def test_property_count_limit():
    src = _obj(**{f"f{i}": {"type": "string"} for i in range(MAX_PROPERTIES + 1)})
    with pytest.raises(ValueError, match="속성"):
        strict_schema(src, name="t")


# --------------------------------------------------------------------------- #
# 계약
# --------------------------------------------------------------------------- #
def test_source_model_is_not_mutated():
    """pydantic 이 ``model_json_schema()`` 를 캐시할 수 있어 제자리 수정은 모델을 오염시킨다."""
    original = _obj(a={"type": "string", "default": "x"})
    model = _FakeModel(original)
    strict_schema(model, name="t")
    assert original["properties"]["a"] == {"type": "string", "default": "x"}
    assert "additionalProperties" not in original


def test_accepts_a_model_object_and_a_raw_dict_alike():
    src = _obj(a={"type": "string"})
    assert strict_schema(_FakeModel(src), name="t") == strict_schema(src, name="t")


def test_title_is_the_given_name():
    """봉투의 ``name`` 과 스키마 ``title`` 이 같아야 두 기록을 손으로 안 이어 붙인다."""
    assert strict_schema(_obj(a={"type": "string"}), name="record")["title"] == "record"


# --------------------------------------------------------------------------- #
# 봉투 · 모드
# --------------------------------------------------------------------------- #
def test_response_format_envelope_shapes():
    schema = strict_schema(_obj(a={"type": "string"}), name="record")
    got = response_format(schema, mode="json_schema")
    assert got["type"] == "json_schema"
    assert got["json_schema"]["name"] == "record"
    assert got["json_schema"]["strict"] is True
    assert got["json_schema"]["schema"] is schema

    assert response_format(schema, mode="json_object") == {"type": "json_object"}
    assert response_format(schema, mode="off") is None


def test_json_object_mode_ignores_a_missing_schema():
    assert response_format(None, mode="json_object") == {"type": "json_object"}


def test_auto_without_a_schema_sends_nothing_rather_than_promoting():
    """``json_object`` 로 몰래 승격하면 "왜 응답이 달라졌나"가 설정 어디에도 안 보인다."""
    assert response_format(None, mode="auto") is None
    assert response_format(None, mode="json_schema") is None


def test_normalize_mode_accepts_known_values_and_rejects_typos():
    assert normalize_mode("AUTO") == "auto"
    assert normalize_mode(" json_object ") == "json_object"
    assert normalize_mode(None) == "auto"
    with pytest.raises(ValueError, match="json-schema"):
        normalize_mode("json-schema")


# --------------------------------------------------------------------------- #
# 거절 판정 — 좁아야 한다
# --------------------------------------------------------------------------- #
class _HttpError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status


def test_schema_rejection_needs_both_status_and_marker():
    assert looks_like_schema_rejection(
        _HttpError(400, "Invalid value for 'response_format': not supported")
    )
    assert looks_like_schema_rejection(_HttpError(422, "json_schema is unsupported"))


def test_other_failures_are_not_treated_as_schema_rejection():
    """넓게 잡으면 **모든 실패의 비용이 두 배**가 된다 — 실패한 호출을 한 번 더 하니까."""
    assert not looks_like_schema_rejection(_HttpError(429, "rate limit exceeded"))
    assert not looks_like_schema_rejection(_HttpError(401, "invalid api key"))
    assert not looks_like_schema_rejection(_HttpError(400, "context length exceeded"))
    assert not looks_like_schema_rejection(TimeoutError("Request timed out."))


def test_status_is_read_from_a_nested_response_too():
    class _Wrapped(Exception):
        response = type("R", (), {"status_code": 400})()

    assert looks_like_schema_rejection(_Wrapped("bad response_format"))

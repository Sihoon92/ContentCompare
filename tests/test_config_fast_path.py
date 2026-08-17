"""fast_path 설정 로딩 — 중첩 dict 가 dataclass 로 파싱되는지.

``FactConfig(**data)`` 로만 두면 ``fast_path`` 가 **dict 인 채로** 들어가
``cfg.fact.fast_path.enforce`` 가 AttributeError 를 낸다. llm 의 ollama/internal
과 같은 중첩 파싱이 필요하다.
"""

from contentcompare.config import AppConfig, FastPathConfig


def test_defaults_to_shadow():
    """기본값이 shadow 인 것은 안전장치다 — enforce 는 LLM 호출을 늘린다."""
    cfg = AppConfig.from_dict({})
    assert cfg.fact.fast_path.enabled is True
    assert cfg.fact.fast_path.enforce is False


def test_nested_dict_is_parsed_into_dataclass():
    cfg = AppConfig.from_dict(
        {"fact": {"fast_path": {"enforce": True}, "match_top_k": 5}}
    )
    assert isinstance(cfg.fact.fast_path, FastPathConfig)
    assert cfg.fact.fast_path.enforce is True
    assert cfg.fact.fast_path.enabled is True   # 미지정 키는 기본값 유지
    assert cfg.fact.match_top_k == 5            # 형제 키가 소실되지 않는다


def test_missing_section_uses_defaults():
    cfg = AppConfig.from_dict({"fact": {"match_top_k": 7}})
    assert isinstance(cfg.fact.fast_path, FastPathConfig)
    assert cfg.fact.fast_path.enabled is True

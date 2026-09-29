"""임베딩 전용 접속 정보(``llm.embed_internal``) — chat 과 다른 주소·키를 쓸 수 있는가.

사내에서 chat 게이트웨이와 별개로 임베딩(bge-m3)이 다른 주소·다른 키로 서비스되는
경우가 있다. 예전에는 두 백엔드가 ``internal`` 하나를 공유해서 그 조합을 **표현할 방법이
아예 없었다.**

이 파일이 지키는 불변식 둘:

1. **안 적으면 오늘과 완전히 같다** — 객체 구성까지 동일해야 한다(회귀 0).
2. **적으면 반드시 갈라진다** — 주소가 다른데 한 객체를 공유하면 임베딩이 chat 주소로
   나가고, 그건 조용히 틀린 결과가 된다.
"""

from __future__ import annotations

from contentcompare.config import AppConfig, InternalConfig, LLMConfig
from contentcompare.llm.factory import build_clients

CHAT_URL = "https://chat.intra.corp/v1"
EMBED_URL = "https://api-genai.example.net/api/llm/openai/v1"


def _cfg(**embed_internal) -> AppConfig:
    raw = {
        "llm": {
            "backend": "internal",
            "chat_model": "chat-m",
            "embed_model": "/models/embedding/bge-m3",
            "rate_limit_wait": 0,          # 래퍼를 꺼서 원본 객체를 그대로 본다
            "internal": {"base_url": CHAT_URL, "api_key": "chat-key"},
        },
        "logging": {"timeline": False},    # TracedChat 래핑 제외(같은 이유)
    }
    if embed_internal:
        raw["llm"]["embed_internal"] = embed_internal
    return AppConfig.from_dict(raw)


# --------------------------------------------------------------------------- #
# 1. 안 적으면 오늘과 같다
# --------------------------------------------------------------------------- #
def test_without_override_embed_shares_the_chat_client():
    chat, embed = build_clients(_cfg())
    assert chat is embed          # 객체까지 동일 — 회귀 0


def test_without_override_the_two_configs_are_the_same_object():
    """복사가 아니라 **같은 객체**를 가리켜야, 나중에 internal 을 고쳐도 따라간다."""
    llm = AppConfig.from_dict({"llm": {"internal": {"base_url": CHAT_URL}}}).llm
    assert llm.embed_internal is llm.internal


def test_direct_construction_also_links_them():
    """⚠️ ``LLMConfig(internal=커스텀)`` 을 직접 만들 때도 이어져야 한다.

    기본값을 ``InternalConfig()`` 로 뒀다가 이 경우에 임베딩만 조용히 기본 주소를 쓰는
    결함이 났다(``test_unset_proxy_false_keeps_proxy`` 가 잡았다). 센티넬 ``None`` +
    ``__post_init__`` 이 그것을 막는다.
    """
    custom = InternalConfig(base_url=CHAT_URL, unset_proxy=False)
    llm = LLMConfig(internal=custom)
    assert llm.embed_internal is custom


# --------------------------------------------------------------------------- #
# 2. 적으면 갈라진다
# --------------------------------------------------------------------------- #
def test_override_splits_the_clients_and_uses_the_embed_url():
    chat, embed = build_clients(_cfg(base_url=EMBED_URL, api_key="embed-key"))
    assert chat is not embed
    assert chat.base_url == CHAT_URL.rstrip("/")
    assert embed.base_url == EMBED_URL.rstrip("/")


def test_override_carries_its_own_api_key():
    _chat, embed = build_clients(_cfg(base_url=EMBED_URL, api_key="embed-key"))
    assert embed._headers()["Authorization"] == "Bearer embed-key"


def test_unspecified_keys_are_inherited_from_internal():
    """``base_url`` 만 적으면 키·SSL·프록시는 chat 쪽을 물려받아야 한다.

    불리언까지 "빈 값이면 상속"으로 처리하면 ``verify_ssl: false`` 를 명시한 것과 안
    적은 것을 구분할 수 없다. YAML 에 키가 있느냐로 판단하므로 그 모호함이 없다.
    """
    cfg = _cfg(base_url=EMBED_URL)
    assert cfg.llm.embed_internal.api_key == "chat-key"      # 물려받음
    assert cfg.llm.embed_internal.base_url == EMBED_URL      # 덮어씀


def test_explicit_boolean_override_is_respected():
    cfg = _cfg(base_url=EMBED_URL, verify_ssl=True)
    assert cfg.llm.embed_internal.verify_ssl is True
    assert cfg.llm.internal.verify_ssl is False              # chat 쪽은 그대로


def test_same_values_written_out_do_not_split():
    """같은 값을 굳이 다시 적었으면 가를 이유가 없다 — 동등성으로 판단한다."""
    chat, embed = build_clients(_cfg(base_url=CHAT_URL, api_key="chat-key"))
    assert chat is embed


def test_split_works_across_different_backends():
    """chat 은 ollama(로컬), 임베딩만 사내로 보내는 조합."""
    cfg = AppConfig.from_dict({
        "llm": {
            "backend": "ollama", "embed_backend": "internal",
            "rate_limit_wait": 0,
            "internal": {"base_url": CHAT_URL},
            "embed_internal": {"base_url": EMBED_URL, "api_key": "embed-key"},
        },
        "logging": {"timeline": False},
    })
    chat, embed = build_clients(cfg)
    assert type(chat).__name__ == "OllamaBackend"
    assert embed.base_url == EMBED_URL.rstrip("/")

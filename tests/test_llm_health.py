"""LLM 연결 점검(check_llm) 테스트 — 클라이언트 주입으로 네트워크 불필요."""

from __future__ import annotations

from contentcompare.config import AppConfig
from contentcompare.llm.health import all_ok, check_llm


class OkChat:
    def complete(self, system, user, *, temperature=0.0):
        return "OK"


class OkEmbed:
    def embed(self, texts):
        return [[0.1, 0.2, 0.3] for _ in texts]


class BoomChat:
    def complete(self, system, user, *, temperature=0.0):
        raise ConnectionError("연결 거부")


class EmptyEmbed:
    def embed(self, texts):
        return [[] for _ in texts]


def _cfg():
    return AppConfig()


def test_check_all_ok():
    results = check_llm(_cfg(), chat_client=OkChat(), embed_client=OkEmbed())
    assert all_ok(results)
    # chat / embeddings 점검 항목이 포함되고 차원이 보고된다.
    detail = "\n".join(r.line() for r in results)
    assert "chat" in detail and "embeddings" in detail
    assert "차원 3" in detail


def test_check_chat_failure_reported():
    results = check_llm(_cfg(), chat_client=BoomChat(), embed_client=OkEmbed())
    assert not all_ok(results)
    chat = next(r for r in results if r.name.startswith("chat"))
    assert chat.ok is False
    assert "연결 거부" in chat.detail


def test_check_empty_embedding_is_failure():
    results = check_llm(_cfg(), chat_client=OkChat(), embed_client=EmptyEmbed())
    assert not all_ok(results)
    emb = next(r for r in results if r.name.startswith("embeddings"))
    assert emb.ok is False


def test_check_reports_backend_target():
    results = check_llm(_cfg(), chat_client=OkChat(), embed_client=OkEmbed())
    head = results[0]
    assert "백엔드=ollama" in head.name
    assert "11434" in head.detail  # ollama host 표시


def test_check_includes_proxy_status(monkeypatch):
    import os
    from contentcompare.config import AppConfig

    monkeypatch.setenv("HTTPS_PROXY", "")  # 비워둔 상태 모사
    cfg = AppConfig()
    cfg.llm.backend = "internal"
    cfg.llm.internal.unset_proxy = True
    results = check_llm(cfg, chat_client=OkChat(), embed_client=OkEmbed())
    proxy = next(r for r in results if r.name == "프록시 env")
    assert proxy.ok is True
    assert "비어있음" in proxy.detail


def test_check_proxy_flags_when_still_set(monkeypatch):
    from contentcompare.config import AppConfig

    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.corp:8080")
    cfg = AppConfig()
    cfg.llm.backend = "internal"
    cfg.llm.internal.unset_proxy = True
    # 클라이언트를 주입하면 build_clients(=disable_proxy)가 호출되지 않으므로
    # 프록시가 남아있는 상황을 점검 항목이 잡아내야 한다.
    results = check_llm(cfg, chat_client=OkChat(), embed_client=OkEmbed())
    proxy = next(r for r in results if r.name == "프록시 env")
    assert proxy.ok is False
    assert "아직 설정됨" in proxy.detail

"""프록시 전역 비우기 정책 테스트."""

from __future__ import annotations

import os

from contentcompare.config import AppConfig, disable_proxy
from contentcompare.llm.factory import build_clients

_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")


def test_disable_proxy_empties_all(monkeypatch):
    for k in _VARS:
        monkeypatch.setenv(k, "http://proxy.corp:8080")
    disable_proxy()
    assert all(os.environ[k] == "" for k in _VARS)


def test_build_clients_internal_disables_proxy_persistently(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.corp:8080")
    cfg = AppConfig()
    cfg.llm.backend = "internal"
    cfg.llm.internal.unset_proxy = True

    build_clients(cfg)
    # 호출 컨텍스트 밖에서도(복원 없이) 계속 비어 있어야 한다.
    assert os.environ["HTTPS_PROXY"] == ""
    assert os.environ["HTTP_PROXY"] == ""


def test_build_clients_langchain_disables_proxy(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.corp:8080")
    cfg = AppConfig()
    cfg.llm.backend = "langchain"
    build_clients(cfg)
    assert os.environ["HTTPS_PROXY"] == ""


def test_unset_proxy_false_keeps_proxy(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.corp:8080")
    cfg = AppConfig()
    cfg.llm.backend = "internal"
    cfg.llm.internal.unset_proxy = False
    build_clients(cfg)
    assert os.environ["HTTPS_PROXY"] == "http://proxy.corp:8080"


def test_ollama_backend_keeps_proxy(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.corp:8080")
    cfg = AppConfig()
    cfg.llm.backend = "ollama"
    build_clients(cfg)
    assert os.environ["HTTPS_PROXY"] == "http://proxy.corp:8080"

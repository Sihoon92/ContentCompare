"""LLM/임베딩 연결 점검(health check).

설정대로 백엔드를 만들어 chat 1회 + embedding 1회를 실제로 호출해보고,
성공/실패를 사람이 읽기 쉬운 결과로 돌려준다. CLI ``--check`` 와 UI 버튼이 사용한다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from ..config import AppConfig
from .base import EmbeddingClient, LLMClient
from .factory import build_clients

_PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str

    def line(self) -> str:
        mark = "✅" if self.ok else "❌"
        return f"{mark} {self.name}: {self.detail}"


def _err(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


def _trim(text: str, n: int = 80) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def check_llm(
    config: AppConfig,
    *,
    chat_client: Optional[LLMClient] = None,
    embed_client: Optional[EmbeddingClient] = None,
) -> list[CheckResult]:
    """chat/embedding 연결을 점검한다. 클라이언트를 주입하면 그것을 사용(테스트용)."""
    results: list[CheckResult] = []

    # 0) 대상 정보
    llm = config.llm
    if llm.backend in ("internal", "langchain"):
        target = config.llm.internal.base_url
    else:
        target = config.llm.ollama.host
    results.append(CheckResult(f"백엔드={llm.backend}", True, target))

    # 백엔드 생성 (internal/langchain + unset_proxy 면 이 시점에 프록시가 전역으로 비워짐)
    if chat_client is None or embed_client is None:
        try:
            built_chat, built_embed = build_clients(config)
        except Exception as exc:  # noqa: BLE001 - 사용자에게 원인 노출
            results.append(CheckResult("백엔드 생성", False, _err(exc)))
            return results
        chat_client = chat_client or built_chat
        embed_client = embed_client or built_embed

    # 0-1) 프록시 상태(실제 비어 있는지 확인)
    results.append(_proxy_result(config))

    # 1) chat 핑
    try:
        out = chat_client.complete("연결 점검입니다.", "OK 라고만 답하세요.")
        if out and out.strip():
            results.append(CheckResult(f"chat ({llm.chat_model})", True, _trim(out)))
        else:
            results.append(CheckResult(f"chat ({llm.chat_model})", False, "빈 응답"))
    except Exception as exc:  # noqa: BLE001
        results.append(CheckResult(f"chat ({llm.chat_model})", False, _err(exc)))

    # 2) embedding 핑
    try:
        vecs = embed_client.embed(["연결 테스트"])
        dim = len(vecs[0]) if vecs and vecs[0] else 0
        if dim > 0:
            results.append(CheckResult(f"embeddings ({llm.embed_model})", True, f"차원 {dim}"))
        else:
            results.append(CheckResult(f"embeddings ({llm.embed_model})", False, "빈 벡터"))
    except Exception as exc:  # noqa: BLE001
        results.append(CheckResult(f"embeddings ({llm.embed_model})", False, _err(exc)))

    return results


def all_ok(results: list[CheckResult]) -> bool:
    return all(r.ok for r in results)


def _proxy_result(config: AppConfig) -> CheckResult:
    """현재 프로세스의 프록시 환경변수 상태를 점검 결과로 만든다."""
    vals = {k: os.environ.get(k) for k in _PROXY_VARS}
    nonempty = {k: v for k, v in vals.items() if v}
    expect_empty = (
        config.llm.backend in ("internal", "langchain")
        and config.llm.internal.unset_proxy
    )
    if expect_empty:
        ok = not nonempty
        detail = "모두 비어있음 — 프록시 우회 적용됨" if ok else f"아직 설정됨: {nonempty}"
    else:
        ok = True
        detail = str(nonempty) if nonempty else "(설정 없음)"
    return CheckResult("프록시 env", ok, detail)

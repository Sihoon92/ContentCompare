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

    # 3) Langfuse(선택). 켜져 있을 때만 점검한다 — 안 쓰는 사람에게 실패 줄이 뜨면 안 된다.
    lf_result = _langfuse_result(config)
    if lf_result is not None:
        results.append(lf_result)

    return results


def _langfuse_result(config: AppConfig) -> Optional[CheckResult]:
    """LLM 입출력 추적 설정 점검. 꺼져 있으면 ``None``(줄 자체를 만들지 않는다).

    **키 값은 절대 출력하지 않는다** — 점검 결과는 화면과 로그에 그대로 남는다.
    """
    lf = config.llm.langfuse
    host, public, secret = lf.resolved()
    if not lf.enabled:
        return None
    if not (host or public or secret):
        return None  # 아무것도 설정하지 않음 = 이 기능을 안 쓴다
    if not lf.is_active():
        missing = [n for n, v in (("host", host), ("public_key", public),
                                  ("secret_key", secret)) if not v]
        return CheckResult("Langfuse", False,
                           f"설정이 불완전합니다 — 누락: {', '.join(missing)}")
    try:
        from langfuse import Langfuse  # type: ignore[import-not-found]
    except ImportError:
        return CheckResult("Langfuse", False,
                           'SDK 미설치 — pip install -e ".[langfuse]"')
    try:
        from .tracing import new_langfuse_client

        client = new_langfuse_client(lf, Langfuse)   # 사내 CA 적용 포함
        auth = getattr(client, "auth_check", None)
        if auth is not None and auth() is False:
            return CheckResult("Langfuse", False, f"인증 실패 (host={host})")
    except Exception as exc:  # noqa: BLE001 — 원인을 사용자에게 그대로 보여준다
        detail = f"{_err(exc)} (host={host})"
        # 사내 자체호스팅에서 가장 흔한 실패다. 원인 모르고 헤매지 않도록 해법을 붙인다.
        if "CERTIFICATE_VERIFY_FAILED" in str(exc) or "SSLError" in type(exc).__name__:
            detail += (
                "\n     → 먼저 `pip install truststore` 를 시도하세요 — OS 인증서 "
                "저장소를 쓰므로 브라우저로 Langfuse 웹이 열리는 PC 면 대개 이걸로 끝납니다."
                "\n       그래도 안 되면 llm.langfuse.ssl_cert 에 사내 CA 의 PEM 경로를 "
                "지정하세요(.cer 이 DER 이면 변환: "
                "openssl x509 -inform DER -in x.cer -out corp-ca.pem)."
                "\n       원인 격리: python scripts/langfuse_test.py [--insecure]"
            )
        return CheckResult("Langfuse", False, detail)
    ca = "" if not lf.ssl_cert else f", ca={os.path.basename(lf.ssl_cert)}"
    if not lf.verify_ssl and not lf.ssl_cert:
        ca = ", ⚠️ 인증서 검증 꺼짐"
    return CheckResult("Langfuse", True, f"host={host}{ca}")


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

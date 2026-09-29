"""웹 서버 설정 — `.env` 를 읽어 웹 전용 값과 :class:`AppConfig` 덮어쓰기를 만든다.

우선순위: **OS 환경변수 > `.env` > `config.yaml`(``CC_CONFIG``) > 코드 기본값** (설계 §10).
연결·비밀값만 `.env` 에 두고, 튜닝값(recall_k·배치 크기 …)은 `config.yaml` 에 남는다 —
100개가 넘는 키를 `.env` 로 옮기면 오히려 관리가 어려워진다.

`.env` 키 → 설정 필드 매핑은 :data:`ENV_MAP` **한 곳**에만 둔다. 이 저장소가 두 번 당한
"설정에는 있는데 호출 경로에는 없다" 결함을 막으려는 것이고, 매핑된 경로가 실제 필드에
닿는지는 테스트가 고정한다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

from ..config import AppConfig

# `.env` 키 → AppConfig 필드 경로. 새 키는 여기에만 더한다.
ENV_MAP: dict[str, tuple[str, ...]] = {
    "CC_LLM_BACKEND": ("llm", "backend"),
    "CC_LLM_BASE_URL": ("llm", "internal", "base_url"),
    "CC_OLLAMA_HOST": ("llm", "ollama", "host"),
    "CC_LLM_API_KEY": ("llm", "internal", "api_key"),
    "CC_CHAT_MODEL": ("llm", "chat_model"),
    "CC_EMBED_BACKEND": ("llm", "embed_backend"),
    "CC_EMBED_MODEL": ("llm", "embed_model"),
}

# 정수 웹 설정 — 잘못된 값은 조용히 기본값으로 두지 않고 키 이름과 함께 실패한다.
_INT_KEYS = {
    "CC_PORT": "port",
    "CC_JOB_RETENTION_DAYS": "retention_days",
    "CC_MAX_UPLOAD_MB": "max_upload_mb",
    "CC_STALL_WARN_MIN": "stall_warn_min",
}


@dataclass
class WebSettings:
    config_path: str = ""
    admin_password: str = ""
    host: str = "0.0.0.0"
    port: int = 8000
    jobs_dir: str = "jobs"
    logs_dir: str = "logs"
    retention_days: int = 7
    max_upload_mb: int = 200
    stall_warn_min: int = 5
    env: dict[str, str] = field(default_factory=dict)
    """병합된 원본 값. :func:`build_app_config` 가 :data:`ENV_MAP` 을 여기서 읽는다."""

    @property
    def admin_enabled(self) -> bool:
        return bool(self.admin_password)

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


def read_env(dotenv_path: str = ".env",
             environ: Optional[Mapping[str, str]] = None) -> dict[str, str]:
    """`.env` 값 위에 OS 환경변수(``CC_`` 로 시작하는 것만)를 얹는다."""
    merged: dict[str, str] = {}
    if dotenv_path and Path(dotenv_path).is_file():
        from dotenv import dotenv_values  # extra `web` — 파일이 있을 때만 필요하다

        merged.update({k: v for k, v in dotenv_values(dotenv_path).items() if v is not None})
    env = os.environ if environ is None else environ
    merged.update({k: v for k, v in env.items() if k.startswith("CC_")})
    return merged


def load_settings(dotenv_path: str = ".env",
                  environ: Optional[Mapping[str, str]] = None) -> WebSettings:
    env = read_env(dotenv_path, environ)
    settings = WebSettings(env=env)
    settings.config_path = env.get("CC_CONFIG", "").strip()
    settings.admin_password = env.get("CC_ADMIN_PASSWORD", "").strip()
    settings.host = env.get("CC_HOST", "").strip() or settings.host
    settings.jobs_dir = env.get("CC_JOBS_DIR", "").strip() or settings.jobs_dir
    for key, attr in _INT_KEYS.items():
        raw = env.get(key, "").strip()
        if not raw:
            continue
        try:
            value = int(raw)
        except ValueError:
            raise ValueError(f"{key} 는 정수여야 합니다: {raw!r}") from None
        setattr(settings, attr, value)
    return settings


def build_app_config(settings: WebSettings) -> AppConfig:
    """`config.yaml` 을 읽고 :data:`ENV_MAP` 의 값으로 덮어쓴다. 빈 값은 덮어쓰지 않는다."""
    config = AppConfig.load(settings.config_path or None)
    for key, path in ENV_MAP.items():
        value = settings.env.get(key, "").strip()
        if value:
            _assign(config, path, value)
    return config


def _assign(obj: Any, path: tuple[str, ...], value: str) -> None:
    for name in path[:-1]:
        obj = getattr(obj, name)
    if not hasattr(obj, path[-1]):
        raise AttributeError(f"설정 경로가 없습니다: {'.'.join(path)}")
    setattr(obj, path[-1], value)


def public_llm_summary(config: AppConfig) -> dict[str, str]:
    """화면·`job.json` 에 남길 LLM 정보. **API 키는 절대 넣지 않는다.**"""
    llm = config.llm
    remote = llm.backend in ("internal", "langchain")
    return {
        "backend": llm.backend,
        "chat_model": llm.chat_model,
        "embed_backend": llm.embed_backend or llm.backend,
        "embed_model": llm.embed_model,
        "base_url": llm.internal.base_url if remote else llm.ollama.host,
    }

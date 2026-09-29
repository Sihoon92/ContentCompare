"""웹 설정 — .env 우선순위와 AppConfig 매핑이 한 곳(ENV_MAP)에서 끝나는가."""

from __future__ import annotations

import pytest

from contentcompare.config import AppConfig
from contentcompare.web import settings as ws


def test_every_env_map_path_exists_on_app_config():
    """매핑된 키가 실제 필드에 닿지 않으면 '설정에는 있는데 호출 경로에는 없다'가 된다."""
    config = AppConfig()
    for key, path in ws.ENV_MAP.items():
        obj = config
        for name in path[:-1]:
            obj = getattr(obj, name)
        assert hasattr(obj, path[-1]), f"{key} → {'.'.join(path)}"


def test_missing_dotenv_file_uses_environ_only(tmp_path):
    env = ws.read_env(str(tmp_path / "없음.env"), environ={"CC_PORT": "9001", "PATH": "x"})
    assert env == {"CC_PORT": "9001"}


def test_environ_beats_dotenv(tmp_path):
    pytest.importorskip("dotenv")
    dotenv = tmp_path / ".env"
    dotenv.write_text("CC_CHAT_MODEL=from-file\nCC_PORT=9000\n", encoding="utf-8")
    settings = ws.load_settings(str(dotenv), environ={"CC_CHAT_MODEL": "from-os"})
    assert settings.env["CC_CHAT_MODEL"] == "from-os"
    assert settings.port == 9000


def test_build_app_config_applies_env_over_yaml(tmp_path):
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(
        "llm:\n  backend: ollama\n  chat_model: yaml-model\n  embed_model: yaml-embed\n",
        encoding="utf-8")
    settings = ws.load_settings(str(tmp_path / "없음.env"), environ={
        "CC_CONFIG": str(yaml_path),
        "CC_LLM_BACKEND": "langchain",
        "CC_CHAT_MODEL": "env-model",
        "CC_LLM_BASE_URL": "https://llm.example/v1",
        "CC_LLM_API_KEY": "secret-key",
        "CC_EMBED_MODEL": "",           # 빈 값은 덮어쓰지 않는다
    })
    config = ws.build_app_config(settings)
    assert config.llm.backend == "langchain"
    assert config.llm.chat_model == "env-model"
    assert config.llm.internal.base_url == "https://llm.example/v1"
    assert config.llm.internal.api_key == "secret-key"
    assert config.llm.embed_model == "yaml-embed"


def test_invalid_integer_names_the_key(tmp_path):
    with pytest.raises(ValueError, match="CC_PORT"):
        ws.load_settings(str(tmp_path / "없음.env"), environ={"CC_PORT": "abc"})


def test_admin_disabled_when_password_blank(tmp_path):
    assert not ws.load_settings(str(tmp_path / "x"), environ={}).admin_enabled
    assert ws.load_settings(str(tmp_path / "x"),
                            environ={"CC_ADMIN_PASSWORD": "pw"}).admin_enabled


def test_public_summary_never_contains_the_api_key(tmp_path):
    settings = ws.load_settings(str(tmp_path / "x"), environ={
        "CC_LLM_BACKEND": "internal", "CC_LLM_API_KEY": "secret-key"})
    summary = ws.public_llm_summary(ws.build_app_config(settings))
    assert "secret-key" not in str(summary)
    assert summary["backend"] == "internal"


def test_upload_limit_in_bytes(tmp_path):
    settings = ws.load_settings(str(tmp_path / "x"), environ={"CC_MAX_UPLOAD_MB": "3"})
    assert settings.max_upload_bytes == 3 * 1024 * 1024

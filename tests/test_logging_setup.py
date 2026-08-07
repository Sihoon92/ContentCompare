"""로그 파일 저장 유틸 테스트."""

from __future__ import annotations

import logging
import os

from contentcompare import logging_setup


def test_setup_creates_file_and_captures_logs(tmp_path):
    path = logging_setup.setup_logging(str(tmp_path), force_new=True)
    assert os.path.exists(path)
    logging.getLogger("contentcompare.test").info("테스트 메시지 ABC")
    for h in logging.getLogger().handlers:
        h.flush()
    text = logging_setup.read_log_text()
    assert "테스트 메시지 ABC" in text


def test_log_print_writes_to_file_and_stdout(tmp_path, capsys):
    logging_setup.setup_logging(str(tmp_path), force_new=True)
    logging_setup.log_print("화면+로그 메시지 XYZ")
    for h in logging.getLogger().handlers:
        h.flush()
    # 화면(stdout)에 출력되고
    assert "화면+로그 메시지 XYZ" in capsys.readouterr().out
    # 로그 파일에도 기록된다(요청 4번).
    assert "화면+로그 메시지 XYZ" in logging_setup.read_log_text()


def test_setup_is_idempotent(tmp_path):
    p1 = logging_setup.setup_logging(str(tmp_path), force_new=True)
    p2 = logging_setup.setup_logging(str(tmp_path))  # force_new 아님 → 같은 경로
    assert p1 == p2


def test_third_party_noise_is_filtered_but_ours_is_not(tmp_path):
    logging_setup.setup_logging(str(tmp_path), force_new=True)
    logging.getLogger("urllib3.connectionpool").debug("소켓 잡음 NOISE")
    logging.getLogger("openai._base_client").debug("요청 덤프 NOISE")
    logging.getLogger("contentcompare.llm").debug("프롬프트 원문 KEEP")
    logging.getLogger("urllib3.connectionpool").warning("진짜 경고 KEEP")
    for h in logging.getLogger().handlers:
        h.flush()
    text = logging_setup.read_log_text()
    assert "NOISE" not in text
    assert "프롬프트 원문 KEEP" in text  # 우리 코드의 DEBUG 는 그대로 남는다
    assert "진짜 경고 KEEP" in text  # 서드파티도 WARNING 이상은 남는다


def test_noise_filter_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv(logging_setup.NOISE_ENV, "1")
    logging_setup.setup_logging(str(tmp_path), force_new=True)
    logging.getLogger("urllib3.connectionpool").debug("HTTP 계층 디버깅 ALL")
    for h in logging.getLogger().handlers:
        h.flush()
    assert "HTTP 계층 디버깅 ALL" in logging_setup.read_log_text()


def test_config_can_add_and_lift_quiet_loggers(tmp_path):
    logging_setup.setup_logging(str(tmp_path), force_new=True)
    # 기본 목록에 없던 로거를 추가로 숨기고, 기본 목록의 urllib3 은 도로 연다.
    logging_setup.apply_logger_overrides(quiet=["pptx"], verbose=["urllib3"])
    logging.getLogger("pptx.parts").debug("추가로 숨긴 것 NOISE")
    logging.getLogger("urllib3.connectionpool").debug("골라서 연 것 KEEP")
    for h in logging.getLogger().handlers:
        h.flush()
    text = logging_setup.read_log_text()
    assert "NOISE" not in text
    assert "골라서 연 것 KEEP" in text


def test_verbose_wins_over_quiet_for_same_name(tmp_path):
    """디버깅하려고 연 것을 조용히 덮지 않는다."""
    logging_setup.setup_logging(str(tmp_path), force_new=True)
    logging_setup.apply_logger_overrides(quiet=["httpcore"], verbose=["httpcore"])
    logging.getLogger("httpcore").debug("양쪽에 있으면 보인다 KEEP")
    for h in logging.getLogger().handlers:
        h.flush()
    assert "양쪽에 있으면 보인다 KEEP" in logging_setup.read_log_text()


def test_config_carries_logging_section():
    from contentcompare.config import AppConfig

    config = AppConfig.from_dict({"logging": {"quiet_extra": ["pptx"], "verbose_extra": ["urllib3"]}})
    assert config.logging.quiet_extra == ["pptx"]
    assert config.logging.verbose_extra == ["urllib3"]
    # 섹션이 없어도 기본값으로 동작한다(기존 config.yaml 호환).
    assert AppConfig.from_dict({}).logging.quiet_extra == []


def test_force_new_rotates_file(tmp_path):
    import time

    p1 = logging_setup.setup_logging(str(tmp_path), force_new=True)
    time.sleep(1.1)  # 파일명이 초 단위라 1초 이상 대기
    p2 = logging_setup.setup_logging(str(tmp_path), force_new=True)
    assert p1 != p2

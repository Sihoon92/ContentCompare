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


# --------------------------------------------------------------------------- #
# 콘솔 핸들러 — 화면에 몇 번 나오는가
#
# 기존 테스트가 이 결함을 못 잡은 이유는 **stdout 만 봤고 콘솔 핸들러가 아예 없었기**
# 때문이다. 중복은 stderr 로 나갔다. 여기서는 실제로 핸들러를 붙여 놓고, 그 출력을
# 우리가 가진 버퍼로 받아 ``print`` 쪽(stdout)과 **합쳐서** 센다.
# --------------------------------------------------------------------------- #
import io

import pytest


class Screen:
    """화면에 나간 전부 — ``print``(stdout) + 콘솔 핸들러(주입한 버퍼)."""

    def __init__(self, capsys, buffer: io.StringIO) -> None:
        self._capsys, self._buffer = capsys, buffer

    def text(self) -> str:
        for h in logging.getLogger().handlers:
            try:
                h.flush()
            except ValueError:      # pytest 캡처 스트림이 닫힌 경우 — 우리 것과 무관
                pass
        return self._capsys.readouterr().out + self._buffer.getvalue()


@pytest.fixture
def screen(tmp_path, capsys):
    """실행 환경과 같은 순서로 로깅을 세운다: 콘솔 먼저, 그다음 파일."""
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    logging_setup._CONSOLE = None

    buffer = io.StringIO()
    logging_setup.setup_console(level=logging.WARNING, stream=buffer)
    logging_setup.setup_logging(str(tmp_path), force_new=True)  # 루트를 DEBUG 로 낮춘다
    yield Screen(capsys, buffer)

    for h in list(root.handlers):
        root.removeHandler(h)
    for h in saved_handlers:
        root.addHandler(h)
    root.setLevel(saved_level)
    logging_setup._CONSOLE = None


def test_log_print_appears_on_screen_exactly_once(screen):
    """신고된 증상 — 같은 줄이 두 번 나오면 안 된다."""
    logging_setup.log_print("타임라인 한 줄 QQQ")

    assert screen.text().count("타임라인 한 줄 QQQ") == 1
    assert "타임라인 한 줄 QQQ" in logging_setup.read_log_text()  # 파일에는 남는다


def test_log_print_stays_single_even_when_console_is_verbose(screen):
    """``--verbose``/``--check`` 로 콘솔이 INFO 여도 한 번이다.

    **레벨로 막는 게 아니라는 것을 고정한다** — 예전 방어책("level 을 INFO 로 두면
    WARNING 콘솔이 걸러 준다")이 바로 이 상황에서 무너졌다.
    """
    logging_setup.setup_console(level=logging.INFO)
    logging_setup.log_print("verbose 한 줄 RRR")

    assert screen.text().count("verbose 한 줄 RRR") == 1


def test_debug_payloads_do_not_leak_to_screen(screen):
    """프롬프트·HTTP 페이로드(DEBUG)는 화면에 안 나오고 파일에만 남는다."""
    logging.getLogger("contentcompare.llm").debug("POST http://x payload=%s", "비밀 프롬프트 SSS")

    assert "비밀 프롬프트 SSS" not in screen.text()
    assert "비밀 프롬프트 SSS" in logging_setup.read_log_text()


def test_real_warnings_still_reach_the_screen(screen):
    """필터가 **진짜 경고까지** 삼키면 안 된다 — 조용한 실패가 이 프로젝트의 최대 적이다."""
    logging.getLogger("contentcompare.timeline").warning("타임라인 기록 실패 TTT")

    assert "타임라인 기록 실패 TTT" in screen.text()


def test_setup_console_is_idempotent(screen):
    """두 번 불러도 핸들러는 하나 — 두 개면 그 자체로 중복 출력이다."""
    again = logging_setup.setup_console(level=logging.INFO)
    # pytest 자신의 ``LogCaptureHandler`` 도 ``StreamHandler`` 라 개수만 세면 섞인다.
    # **우리 것**은 ``ConsoleFilter`` 를 달고 있다는 사실로 가려낸다.
    ours = [h for h in logging.getLogger().handlers
            if any(isinstance(f, logging_setup.ConsoleFilter) for f in h.filters)]
    assert len(ours) == 1
    assert again is ours[0]
    assert again.level == logging.INFO  # 레벨은 갱신된다

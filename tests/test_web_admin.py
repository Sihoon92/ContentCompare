"""관리자 — 비밀번호 잠금, 세션 만료, 허용된 로그만, 레벨·검색·페이지."""

from __future__ import annotations

import pytest

from contentcompare.web import admin as A


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_disabled_without_password():
    auth = A.AdminAuth("")
    assert not auth.enabled
    with pytest.raises(A.AuthError) as exc:
        auth.login("")
    assert exc.value.reason == A.DISABLED


def test_login_verify_logout():
    auth = A.AdminAuth("pw")
    with pytest.raises(A.AuthError) as exc:
        auth.login("틀림")
    assert exc.value.reason == A.WRONG
    token = auth.login("pw")
    assert auth.verify(token) and not auth.verify("가짜") and not auth.verify(None)
    auth.logout(token)
    assert not auth.verify(token)


def test_lockout_rejects_even_the_right_password():
    clock = _Clock()
    auth = A.AdminAuth("pw", clock=clock, max_failures=5, lockout_s=60)
    for _ in range(5):
        with pytest.raises(A.AuthError):
            auth.login("x")
    with pytest.raises(A.AuthError) as exc:
        auth.login("pw")
    assert exc.value.reason == A.LOCKED
    clock.now += 61
    assert auth.verify(auth.login("pw"))


def test_session_expires():
    clock = _Clock()
    auth = A.AdminAuth("pw", clock=clock, session_ttl_s=10)
    token = auth.login("pw")
    clock.now += 11
    assert not auth.verify(token)


def _tree(tmp_path):
    logs, jobs = tmp_path / "logs", tmp_path / "jobs"
    logs.mkdir()
    (logs / "server.log").write_text("x\n", encoding="utf-8")
    (logs / "메모.txt").write_text("x", encoding="utf-8")
    job = jobs / "20260929-140211-a3f9"
    job.mkdir(parents=True)
    (job / "contentcompare_20260929_140211.log").write_text("y\n", encoding="utf-8")
    (job / "console.log").write_text("z\n", encoding="utf-8")
    (jobs / "잡동사니").mkdir()
    (jobs / "잡동사니" / "a.log").write_text("n\n", encoding="utf-8")
    return logs, jobs


def test_sources_cover_server_and_job_logs_only(tmp_path):
    logs, jobs = _tree(tmp_path)
    ids = sorted(s.id for s in A.list_log_sources(logs, jobs))
    assert ids == ["job/20260929-140211-a3f9/console.log",
                   "job/20260929-140211-a3f9/contentcompare_20260929_140211.log",
                   "logs/server.log"]
    assert "path" not in A.list_log_sources(logs, jobs)[0].to_dict()


@pytest.mark.parametrize("bad", [
    "logs/../x.log", "logs/메모.txt", "job/잡동사니/a.log", "job/20260929-140211-a3f9/../../x.log",
    "logs/없음.log", "etc/passwd", "logs"])
def test_resolve_rejects_anything_outside(tmp_path, bad):
    logs, jobs = _tree(tmp_path)
    with pytest.raises(KeyError):
        A.resolve_source(bad, logs, jobs)


def test_resolve_accepts_listed(tmp_path):
    logs, jobs = _tree(tmp_path)
    assert A.resolve_source("logs/server.log", logs, jobs) == logs / "server.log"


LOG = """\
2026-09-29 14:02:11,001 INFO contentcompare: 시작
2026-09-29 14:02:12,002 DEBUG contentcompare.llm: 프롬프트 원문
2026-09-29 14:02:13,003 ERROR contentcompare.fact: 실패
Traceback (most recent call last):
  File "x.py", line 1
ValueError: 파싱
2026-09-29 14:02:14,004 WARNING contentcompare: 느림
"""


def test_records_attach_traceback_lines(tmp_path):
    path = tmp_path / "a.log"
    path.write_bytes(LOG.encode("utf-8"))
    records = A.read_records(path)
    assert [r["level"] for r in records] == ["INFO", "DEBUG", "ERROR", "WARNING"]
    assert records[2]["text"].endswith("ValueError: 파싱")


def test_console_log_lines_are_separate_records(tmp_path):
    path = tmp_path / "console.log"
    path.write_bytes("작업 시작\n[F2] 배치 1/3\n".encode("utf-8"))
    assert A.read_records(path) == [{"level": "", "text": "작업 시작"},
                                    {"level": "", "text": "[F2] 배치 1/3"}]


def test_filter_by_level_and_query(tmp_path):
    path = tmp_path / "a.log"
    path.write_bytes(LOG.encode("utf-8"))
    records = A.read_records(path)
    assert [r["level"] for r in A.filter_records(records, min_level="WARNING")] == [
        "ERROR", "WARNING"]
    assert len(A.filter_records(records, query="파싱")) == 1
    console = [{"level": "", "text": "작업 시작"}]
    assert A.filter_records(console, min_level="INFO") == console
    assert A.filter_records(console, min_level="WARNING") == []


def test_page_tail_and_before():
    records = [{"level": "INFO", "text": str(i)} for i in range(10)]
    last = A.page(records, tail=3)
    assert [r["text"] for r in last["records"]] == ["7", "8", "9"]
    assert (last["start"], last["end"], last["total"]) == (7, 10, 10)
    earlier = A.page(records, tail=3, before=last["start"])
    assert [r["text"] for r in earlier["records"]] == ["4", "5", "6"]


def test_read_records_tail_drops_partial_first_line(tmp_path):
    path = tmp_path / "big.log"
    path.write_bytes(("A" * 50 + "\n" + LOG).encode("utf-8"))
    records = A.read_records(path, max_bytes=len(LOG.encode("utf-8")) + 10)
    assert records[0]["text"].startswith("2026-09-29 14:02:11,001")

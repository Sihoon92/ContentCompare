"""작업 모델·저장소 — 디스크의 job.json 이 상태의 원본이다."""

from __future__ import annotations

from datetime import datetime

import pytest

from contentcompare.web import jobs as J


def _job(job_id="20260929-140211-a3f9", **kw):
    return J.Job(id=job_id, engine="fact", **kw)


def test_job_id_format_and_validation():
    jid = J.new_job_id(datetime(2026, 9, 29, 14, 2, 11), rand="a3f9")
    assert jid == "20260929-140211-a3f9"
    assert J.is_job_id(jid)
    assert J.is_job_id(J.new_job_id())
    for bad in ("", "../x", "20260929-140211-A3F9", "20260929-140211-a3f9/..", "x" * 20):
        assert not J.is_job_id(bad)


def test_save_load_round_trip(tmp_path):
    store = J.JobStore(tmp_path)
    job = _job(requester="홍길동", targets=["targets/a.docx"], llm={"backend": "ollama"})
    store.save(job)
    loaded = store.load(job.id)
    assert loaded == job
    assert (tmp_path / job.id / "job.json").is_file()
    assert not (tmp_path / job.id / "job.json.tmp").exists()


def test_bad_id_never_touches_the_disk(tmp_path):
    store = J.JobStore(tmp_path)
    with pytest.raises(ValueError):
        store.dir("..\\..\\windows")
    assert store.load("../x") is None


def test_list_is_oldest_first_and_skips_junk(tmp_path):
    store = J.JobStore(tmp_path)
    store.save(_job("20260929-140300-0002"))
    store.save(_job("20260929-140200-0001"))
    (tmp_path / "잡동사니").mkdir()
    (tmp_path / "20260929-140400-0003").mkdir()          # job.json 없음
    broken = tmp_path / "20260929-140500-0004"
    broken.mkdir()
    (broken / "job.json").write_text("{깨짐", encoding="utf-8")
    assert [j.id for j in store.list()] == ["20260929-140200-0001", "20260929-140300-0002"]


def test_from_dict_ignores_unknown_keys():
    job = J.Job.from_dict({"id": "20260929-140211-a3f9", "engine": "rag", "미래필드": 1})
    assert job.engine == "rag" and job.state == J.QUEUED


def test_purge_expired_removes_only_old_finished_jobs(tmp_path):
    store = J.JobStore(tmp_path)
    day = 86400.0
    store.save(_job("20260901-000000-0001", state=J.SUCCEEDED, finished_ts=1 * day))
    store.save(_job("20260901-000000-0002", state=J.FAILED, finished_ts=9 * day))
    store.save(_job("20260901-000000-0003", state=J.QUEUED))
    store.save(_job("20260901-000000-0004", state=J.RUNNING, started_ts=1 * day))
    removed = J.purge_expired(store, now=10 * day, days=7)
    assert removed == ["20260901-000000-0001"]
    assert [j.id for j in store.list()] == [
        "20260901-000000-0002", "20260901-000000-0003", "20260901-000000-0004"]
    assert J.purge_expired(store, now=10 * day, days=0) == []

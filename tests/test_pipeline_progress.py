"""RAG 파이프라인 진행률 — 준비(읽기·인덱싱) 1 + 판정 1."""

from __future__ import annotations

import pytest

from contentcompare import pipeline as rag_mod
from contentcompare import progress as prog
from contentcompare.config import AppConfig
from contentcompare.models import DocItem, DocType


@pytest.fixture(autouse=True)
def mem():
    reporter = prog.MemoryProgress()
    prog.set_reporter(reporter)
    yield reporter
    prog.reset_reporter()


class _Embedder:
    def embed(self, texts, *, kind="passage"):
        return [[1.0, float(len(t) % 3)] for t in texts]


def _items(doc: str, n: int) -> list[DocItem]:
    return [DocItem(item_id=f"{doc}#{i}", doc_id=doc, doc_type=DocType.WORD,
                    text=f"{doc} 내용 {i}", source_label=f"{doc}#{i}") for i in range(n)]


class _Reader:
    def __init__(self, fail: str = ""):
        self.fail = fail

    def read(self, path):
        if path == self.fail:
            raise OSError("열 수 없음")
        return _items(path, 3)


class _StubComparator:
    def compare(self, ref, candidates):
        return ref.item_id

    def compare_record(self, ref, candidates):
        return ref.item_id


def _pipe(tmp_path, monkeypatch, reader):
    monkeypatch.setattr(rag_mod, "build_clients", lambda cfg: (object(), _Embedder()))
    monkeypatch.setattr(rag_mod, "get_reader", lambda path, config, llm=None: reader)
    monkeypatch.setattr(rag_mod, "close_all_office", lambda: None)
    cfg = AppConfig()
    cfg.similarity.cache_dir = str(tmp_path / "emb")
    cfg.knowledge.enabled = False
    pipe = rag_mod.ComparePipeline(cfg)
    pipe.comparator = _StubComparator()
    return pipe


def test_rag_plan_parts_and_steps(tmp_path, monkeypatch, mem):
    _pipe(tmp_path, monkeypatch, _Reader()).run("기준.xlsx", ["A.docx"])
    plan = [e for e in mem.events if e["ev"] == "plan"][0]
    assert [u["key"] for u in plan["units"]] == ["rag:prepare", "rag:judge"]
    parts = [e["name"] for e in mem.events if e["ev"] == "part"]
    assert parts == ["기준.xlsx", "A.docx", "인덱싱"]
    steps = [(e["done"], e["total"]) for e in mem.events if e["ev"] == "step"]
    assert steps == [(0, 3), (1, 3), (2, 3), (3, 3)]
    snap = prog.summarize(mem.events)
    assert snap.fraction == 1.0 and snap.finished == 2


def test_rag_read_failure_closes_both_units(tmp_path, monkeypatch, mem):
    with pytest.raises(OSError):
        _pipe(tmp_path, monkeypatch, _Reader(fail="A.docx")).run("기준.xlsx", ["A.docx"])
    snap = prog.summarize(mem.events)
    states = {u.key: (u.state, u.error) for u in snap.units}
    assert states == {"rag:prepare": (prog.FAILED, "OSError"),
                      "rag:judge": (prog.SKIPPED, "건너뜀")}
    assert snap.fraction == 1.0

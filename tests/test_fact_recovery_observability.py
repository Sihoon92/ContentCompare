"""F5 복구 실패의 로그·타임라인·산출물 계측 테스트."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import contentcompare.fact.fact_comparator as comparator_module
from contentcompare.config import AppConfig, FactConfig
from contentcompare.fact.fact_comparator import FactComparator
from contentcompare.fact.fact_matcher import EMBED, MatchCandidate
from contentcompare.fact.fact_models import Fact, FactSet
from contentcompare.fact.fact_store import DocFacts, FactStore
from contentcompare.fact.llm_stage import LlmRunner
from contentcompare.fact.pipeline import FactPipeline
from contentcompare.fact.record_models import Attribute
from contentcompare.llm.truncation import LengthLimitError


_SCHEMA = {
    "title": "compare", "type": "object", "properties": {},
    "required": [], "additionalProperties": False,
}
_OK = json.dumps({"result": "unknown", "findings": [], "reason": "판단 보류"},
                 ensure_ascii=False)


def _length() -> LengthLimitError:
    return LengthLimitError("length limit was reached", output="반복원문-비밀",
                            output_chars=16184, backend="fake")


class _SequenceChat:
    supports_structured_output = True
    supports_schema_removal_retry = True

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def complete(self, system, user, *, temperature=0.0, schema=None):
        self.calls += 1
        value = self.responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


def _fact(fid: str, *, ref: bool) -> Fact:
    attrs = (
        {"lower_limit": Attribute(1, "V"), "upper_limit": Attribute(2, "V")}
        if ref else {"target_value": Attribute(1, "V")}
    )
    return Fact(fact_id=fid, entity_name="공칭전압", attributes=attrs,
                search_text="공칭전압", evidence_text=f"{fid} 근거")


def _one_comparison(chat, *, max_calls=50):
    ref, target = _fact("ref-1", ref=True), _fact("target-1", ref=False)
    comparator = FactComparator(runner=LlmRunner(chat, max_calls=max_calls))
    result = comparator.compare(
        ref, [MatchCandidate(target, 1.0, EMBED)],
        DocFacts("대상.pptx", "ppt", FactSet(facts=[target])),
    )
    return comparator, result


@pytest.fixture(autouse=True)
def _force_schema(monkeypatch):
    monkeypatch.setattr(comparator_module, "schema_for", lambda stage: _SCHEMA)


@pytest.mark.parametrize(
    ("responses", "max_calls", "failure_reason"),
    [
        ([_length(), _length()], 50, "output_truncated"),
        (["bad", "still bad"], 50, "parse_failure"),
        ([], 0, "budget_exceeded"),
    ],
)
def test_unknown_failure_uses_the_same_reason_in_result_log_and_timeline(
    responses, max_calls, failure_reason, monkeypatch, capsys
):
    events = []
    monkeypatch.setattr(comparator_module.timeline, "emit",
                        lambda kind, name, **detail: events.append((kind, name, detail)))

    comparator, result = _one_comparison(_SequenceChat(responses), max_calls=max_calls)

    output = capsys.readouterr().out
    unknown = [detail for _kind, _name, detail in events
               if detail.get("action") == "unknown"]
    assert result.failure_reason == failure_reason
    assert result.to_dict()["failure_reason"] == failure_reason
    assert failure_reason in output
    assert len(unknown) == 1 and unknown[0]["failure_reason"] == failure_reason
    assert unknown[0]["reference_fact_id"] == "ref-1"
    assert unknown[0]["candidate_ids"] == ["target-1"]
    assert "반복원문-비밀" not in json.dumps(events, ensure_ascii=False)
    stats = comparator.stats()
    assert stats["llm_failures"] == (
        stats["llm_budget_exceeded"]
        + stats["llm_parse_failures"]
        + stats["llm_output_truncated"]
    )


class _BufferedScreen:
    """flush 전에는 사용자에게 보이지 않는 리다이렉트 터미널을 흉내 낸다."""

    encoding = "utf-8"

    def __init__(self):
        self.pending = ""
        self.visible = ""

    def write(self, text):
        self.pending += text
        return len(text)

    def flush(self):
        self.visible += self.pending
        self.pending = ""


def test_unknown_demotion_is_flushed_to_terminal_with_review_identifiers(monkeypatch):
    """강등 순간에 원인과 재검토 식별자가 버퍼 밖으로 즉시 나와야 한다."""
    screen = _BufferedScreen()
    monkeypatch.setattr(sys, "stdout", screen)
    monkeypatch.setattr(comparator_module.timeline, "emit", lambda *args, **kwargs: None)

    _one_comparison(_SequenceChat([]), max_calls=0)

    assert "F5 unknown 강등" in screen.visible
    assert "failure_reason=budget_exceeded" in screen.visible
    assert "reference_fact_id=ref-1" in screen.visible
    assert "target_doc=대상.pptx" in screen.visible
    assert "candidate_ids=target-1" in screen.visible


class _CallTwelveTruncates(_SequenceChat):
    def __init__(self):
        super().__init__([])

    def complete(self, system, user, *, temperature=0.0, schema=None):
        self.calls += 1
        if self.calls in (12, 13):
            raise _length()
        return _OK


class _Embedder:
    def embed(self, texts, *, kind="passage"):
        return [[1.0, 0.0] for _ in texts]


def _store(count: int = 13) -> FactStore:
    store = FactStore()
    refs = [_fact(f"ref-{i}", ref=True) for i in range(1, count + 1)]
    target = _fact("target-1", ref=False)
    store.add(DocFacts("기준.xlsx", "excel", FactSet(facts=refs)), is_reference=True)
    store.add(DocFacts("대상.docx", "word", FactSet(facts=[target])))
    return store


def _pipeline(tmp_path, chat, *, save=True):
    config = AppConfig()
    config.fact = FactConfig(
        artifacts_dir=str(tmp_path / "artifacts"), save_artifacts=save,
        use_concept_graph=False, match_min_score=0.0, match_top_k=1,
    )
    return FactPipeline(config, chat=chat, embedder=_Embedder())


def test_twelfth_f5_call_truncation_does_not_abort_run(tmp_path):
    """실측 순번을 재현해 실패 항목 다음 비교와 리포트까지 진행되는지 고정한다."""
    root = tmp_path / "artifacts" / "기준_xlsx"
    root.mkdir(parents=True)
    (root / "run_stats.json").write_text(
        json.dumps({"path": "기준.xlsx", "llm": {"calls": 7}}, ensure_ascii=False),
        encoding="utf-8",
    )
    chat = _CallTwelveTruncates()

    result = _pipeline(tmp_path, chat)._compare_from_store(
        _store(), "기준.xlsx", ["대상.docx"]
    )

    assert len(result.comparisons) == 13 and chat.calls == 14
    assert result.comparisons[11].failure_reason == "output_truncated"
    assert result.comparisons[12].failure_reason == ""
    assert result.compare_stats["llm_truncations"] == 2
    assert result.compare_stats["llm_schema_retries"] == 1
    assert result.compare_stats["llm_failures"] == 1
    assert "# 문서 비교 리포트" in result.markdown

    comparison = json.loads((root / "comparison_result.json").read_text(encoding="utf-8"))
    run_stats = json.loads((root / "run_stats.json").read_text(encoding="utf-8"))
    assert comparison["stats"] == result.compare_stats
    assert run_stats["comparison"] == result.compare_stats
    assert run_stats["llm"] == {"calls": 7} and run_stats["path"] == "기준.xlsx"


def test_corrupt_run_stats_is_not_overwritten(tmp_path, caplog):
    root = tmp_path / "artifacts" / "기준_xlsx"
    root.mkdir(parents=True)
    path = root / "run_stats.json"
    path.write_text("{broken", encoding="utf-8")
    pipe = _pipeline(tmp_path, _CallTwelveTruncates())

    pipe._save_comparison_stats(DocFacts("기준.xlsx"), {"llm_failures": 1})

    assert path.read_text(encoding="utf-8") == "{broken"
    assert "run_stats" in caplog.text


def test_save_artifacts_false_does_not_create_comparison_stats(tmp_path):
    pipe = _pipeline(tmp_path, _CallTwelveTruncates(), save=False)

    pipe._save_comparison_stats(DocFacts("기준.xlsx"), {"llm_failures": 1})

    assert not (tmp_path / "artifacts").exists()


def test_run_stats_write_failure_keeps_the_previous_file(tmp_path, monkeypatch, caplog):
    """비교 계측 임시 파일 쓰기가 실패해도 기존 추출 계측은 바이트 그대로 남는다."""
    root = tmp_path / "artifacts" / "기준_xlsx"
    root.mkdir(parents=True)
    path = root / "run_stats.json"
    original = '{"path": "기준.xlsx", "llm": {"calls": 7}}'
    path.write_text(original, encoding="utf-8")
    real_write_text = Path.write_text

    def fail_after_partial_write(self, data, *args, **kwargs):
        if self.name == "run_stats.json.tmp":
            real_write_text(self, "partial", encoding="utf-8")
            raise OSError("disk full")
        return real_write_text(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_after_partial_write)

    _pipeline(tmp_path, _CallTwelveTruncates())._save_comparison_stats(
        DocFacts("기준.xlsx"), {"llm_failures": 1}
    )

    assert path.read_text(encoding="utf-8") == original
    assert not (root / "run_stats.json.tmp").exists()
    assert "run_stats" in caplog.text

"""F5 다중 후보(1:N) 통합 판정 테스트.

핵심 계약: **후보가 2건 이상이면 코드가 ``candidates[0]`` 으로 축약해 확정하지 않는다.**
동명 fact 들은 recall 점수로 갈리지 않으므로 그 위에서 내린 판정은 원리적으로 임의
선택이다. 지금까지 그것이 조용히 확정됐다.

설계: ``docs/superpowers/specs/2026-08-11-multi-candidate-fact-comparison-design.md``
"""

from __future__ import annotations

import json

from contentcompare.fact.fact_comparator import (
    BY_CODE,
    BY_LLM,
    MATCH,
    MISMATCH,
    UNKNOWN,
    FactComparator,
)
from contentcompare.fact.fact_matcher import EMBED, EXACT, MatchCandidate
from contentcompare.fact.fact_models import Fact, FactSet
from contentcompare.fact.fact_store import DocFacts
from contentcompare.fact.llm_stage import LlmRunner
from contentcompare.fact.record_models import Attribute


def _fact(fid: str, name: str, evidence: str = "근거", **attrs) -> Fact:
    parsed = {}
    for k, v in attrs.items():
        parsed[k] = Attribute(*v) if isinstance(v, tuple) else Attribute(v, "")
    return Fact(fact_id=fid, entity_name=name, attributes=parsed, evidence_text=evidence)


def _cands(*facts: Fact, score: float = 0.9, method: str = EMBED) -> list[MatchCandidate]:
    return [MatchCandidate(f, score, method) for f in facts]


def _target(*facts: Fact) -> DocFacts:
    return DocFacts("규격서.docx", "word", FactSet(facts=list(facts)))


class _CountingChat:
    def __init__(self, response: dict):
        self.response = response
        self.calls = 0
        self.prompts: list[str] = []

    def complete(self, system, user, *, temperature=0.0):
        self.calls += 1
        self.prompts.append(user)
        return json.dumps(self.response, ensure_ascii=False)


def _comparator(response: dict | None = None, **kw) -> tuple[FactComparator, _CountingChat]:
    chat = _CountingChat(response or {"result": "unknown", "reason": "판단 보류"})
    return FactComparator(runner=LlmRunner(chat), **kw), chat


# --------------------------------------------------------------------------- #
# 실제 사례 — 엑셀 1행(충전환경온도) ↔ 워드 4구간
# --------------------------------------------------------------------------- #
def _four_ranges() -> list[Fact]:
    """워드가 조건 구간별로 나눠 적은 같은 이름의 fact 4건."""
    return [
        _fact("t1", "charge temperature range",
              "Charge temperature range 0~10C: 0.2C", current=("0.2", "C")),
        _fact("t2", "charge temperature range",
              "Charge temperature range 10~45C: 1C", current=("1", "C")),
        _fact("t3", "charge temperature range",
              "Charge temperature range 45~50C: 0.5C", current=("0.5", "C")),
        _fact("t4", "charge temperature range",
              "Charge temperature range 50~60C: 0.2C", current=("0.2", "C")),
    ]


# --------------------------------------------------------------------------- #
# 라우팅 — N≥2 는 코드가 확정하지 않는다
# --------------------------------------------------------------------------- #
def test_two_candidates_go_to_llm_even_when_code_says_match():
    """코드 판정이 ``match`` 여도, 후보가 2건이면 그 match 는 임의 선택 위에 있다."""
    ref = _fact("r1", "충전환경온도", current=("0.2", "C"))
    tgt = _four_ranges()
    cmp_, chat = _comparator({
        "result": "mismatch",
        "mismatch_attributes": ["current"],
        "reason": "10~45C 구간이 다릅니다.",
    })

    out = cmp_.compare(ref, _cands(*tgt), _target(*tgt))

    assert chat.calls == 1, "후보 4건인데 코드가 candidates[0] 로 확정해 버렸다"
    assert out.result == MISMATCH and out.decided_by == BY_LLM


def test_single_candidate_still_decided_by_code():
    """회귀 방어 — 후보 1건이면 기존 경로 그대로(LLM 0회)."""
    ref = _fact("r1", "충전환경온도", current=("0.2", "C"))
    tgt = _fact("t1", "charge temperature range", "0~10C: 0.2C", current=("0.2", "C"))
    cmp_, chat = _comparator()

    out = cmp_.compare(ref, _cands(tgt, method=EXACT, score=1.0), _target(tgt))

    assert out.result == MATCH and out.decided_by == BY_CODE and chat.calls == 0


def test_compare_code_never_calls_llm_regardless_of_candidate_count():
    """``compare_code`` 의 LLM 0회 계약은 후보 수와 무관하다."""
    ref = _fact("r1", "충전환경온도", current=("0.2", "C"))
    tgt = _four_ranges()
    cmp_, chat = _comparator()

    probe = cmp_.compare_code(ref, _cands(*tgt), _target(*tgt))

    assert chat.calls == 0
    assert len(probe.candidates) == 4


# --------------------------------------------------------------------------- #
# 종합 판정 — 후보 N건이 함께 하나의 규격을 이룬다
# --------------------------------------------------------------------------- #
def _findings_response() -> dict:
    """4구간 중 10~45C 만 어긋난 응답."""
    return {
        "result": "mismatch",
        "findings": [
            {"fact_id": "t1", "result": "match", "quote": "Charge temperature range 0~10C: 0.2C",
             "reason": "일치"},
            {"fact_id": "t2", "result": "mismatch", "mismatch_attributes": ["current"],
             "quote": "Charge temperature range 10~45C: 1C", "reason": "기준 1C vs 대상 0.7C"},
            {"fact_id": "t3", "result": "match", "quote": "Charge temperature range 45~50C: 0.5C",
             "reason": "일치"},
            {"fact_id": "t4", "result": "match", "quote": "Charge temperature range 50~60C: 0.2C",
             "reason": "일치"},
        ],
        "reason": "4구간 중 3구간 일치",
    }


def test_findings_are_carried_for_every_candidate():
    ref = _fact("r1", "충전환경온도", current=("0.2", "C"))
    tgt = _four_ranges()
    cmp_, _ = _comparator(_findings_response())

    out = cmp_.compare(ref, _cands(*tgt), _target(*tgt))

    assert out.result == MISMATCH
    assert [f.fact_id for f in out.findings] == ["t1", "t2", "t3", "t4"]
    assert [f.fact_id for f in out.target_facts] == ["t1", "t2", "t3", "t4"]
    assert out.candidate_count == 4


def test_aggregate_mismatch_attributes_is_union_of_findings():
    ref = _fact("r1", "충전환경온도", current=("0.2", "C"))
    tgt = _four_ranges()
    cmp_, _ = _comparator(_findings_response())

    out = cmp_.compare(ref, _cands(*tgt), _target(*tgt))

    assert out.mismatch_attributes == ["current"]


def test_representative_target_fact_is_the_problem_one():
    """리포트 대표는 **문제가 있는 쪽**이어야 사람이 먼저 볼 곳을 찾는다."""
    ref = _fact("r1", "충전환경온도", current=("0.2", "C"))
    tgt = _four_ranges()
    cmp_, _ = _comparator(_findings_response())

    out = cmp_.compare(ref, _cands(*tgt), _target(*tgt))

    assert out.target_fact is not None and out.target_fact.fact_id == "t2"


# --------------------------------------------------------------------------- #
# 프롬프트 — 1:N 을 금지하지 않는다
# --------------------------------------------------------------------------- #
def test_compare_prompt_asks_for_findings_and_drops_single_choice():
    """후보를 다 보여주면서 '하나만 고르라'고 하면 프롬프트가 1:N 을 막는다."""
    from contentcompare.fact.prompts import COMPARE_SYSTEM

    assert "findings" in COMPARE_SYSTEM
    assert "target_fact_id" not in COMPARE_SYSTEM


def test_compare_prompt_json_schema_is_parseable_with_findings():
    """스키마는 후보 수와 무관하게 항상 같다 — 파서 분기를 만들지 않기 위해서다."""
    from contentcompare.fact.prompts import COMPARE_SYSTEM

    block = COMPARE_SYSTEM[COMPARE_SYSTEM.index("{"):COMPARE_SYSTEM.rindex("}") + 1]
    schema = json.loads(block)
    assert isinstance(schema["findings"], list) and schema["findings"]
    assert set(schema["findings"][0]) >= {"fact_id", "result", "quote", "reason"}


# --------------------------------------------------------------------------- #
# 코드가 하는 검증 — 제안은 LLM, 차단은 코드
# --------------------------------------------------------------------------- #
def test_finding_outside_candidates_is_dropped_and_counted():
    """후보 밖 id 는 할루시네이션이므로 버린다. 드롭은 결과에 안 남으니 계측이 필요하다."""
    ref = _fact("r1", "충전환경온도", current=("0.2", "C"))
    tgt = _four_ranges()
    response = _findings_response()
    response["findings"].append(
        {"fact_id": "없는id", "result": "mismatch", "quote": "지어낸 원문", "reason": "?"}
    )
    cmp_, _ = _comparator(response)

    out = cmp_.compare(ref, _cands(*tgt), _target(*tgt))

    assert [f.fact_id for f in out.findings] == ["t1", "t2", "t3", "t4"]
    assert cmp_.dropped_findings == 1


def test_quote_not_in_evidence_is_flagged_but_verdict_kept():
    """인용 검증 실패는 드롭이 아니다 — 드롭하면 종합 판정이 통째로 날아간다."""
    ref = _fact("r1", "충전환경온도", current=("0.2", "C"))
    tgt = _four_ranges()
    response = _findings_response()
    response["findings"][2]["quote"] = "문서에 없는 문장"
    cmp_, _ = _comparator(response)

    out = cmp_.compare(ref, _cands(*tgt), _target(*tgt))

    verified = {f.fact_id: f.quote_verified for f in out.findings}
    assert verified == {"t1": True, "t2": True, "t3": False, "t4": True}
    assert out.result == MISMATCH, "인용 하나가 어긋났다고 판정을 버리면 안 된다"
    assert cmp_.quote_unverified == 1


def test_quote_matches_after_whitespace_collapse():
    """정규화 규약은 ``fact_extractor._norm`` 과 같다 — 공백만 병합한다."""
    tgt = _fact("t1", "charge temperature range", "Charge temperature  range\n0~10C: 0.2C",
                current=("0.2", "C"))
    ref = _fact("r1", "충전환경온도", current=("0.2", "C"))
    cmp_, _ = _comparator({
        "result": "match",
        "findings": [{"fact_id": "t1", "result": "match",
                      "quote": "Charge temperature range 0~10C: 0.2C", "reason": "일치"}],
        "reason": "일치",
    })

    out = cmp_.compare(ref, _cands(tgt), _target(tgt), ref_low_confidence=True)

    assert out.findings[0].quote_verified is True


def test_all_findings_dropped_degrades_to_unknown():
    """근거가 하나도 남지 않은 판정은 확정할 수 없다."""
    ref = _fact("r1", "충전환경온도", current=("0.2", "C"))
    tgt = _four_ranges()
    cmp_, _ = _comparator({
        "result": "match",
        "findings": [{"fact_id": "허구1", "result": "match", "quote": "x", "reason": "?"}],
        "reason": "일치",
    })

    out = cmp_.compare(ref, _cands(*tgt), _target(*tgt))

    assert out.result == UNKNOWN and out.decided_by == BY_LLM
    assert not out.findings


def test_empty_findings_leaves_legitimate_missing_alone():
    """finding 을 애초에 안 낸 것은 ``missing`` 의 정상 형태다 — 강등하면 안 된다."""
    ref = _fact("r1", "충전환경온도", current=("0.2", "C"))
    tgt = _four_ranges()
    cmp_, _ = _comparator({"result": "missing", "findings": [], "reason": "대응 항목 없음"})

    out = cmp_.compare(ref, _cands(*tgt), _target(*tgt))

    assert out.result == "missing" and out.target_fact is None


# --------------------------------------------------------------------------- #
# 산출물 — 후보별 내역이 artifacts 에 남아야 사람이 검수한다
# --------------------------------------------------------------------------- #
def test_to_dict_carries_findings_and_candidate_count():
    ref = _fact("r1", "충전환경온도", current=("0.2", "C"))
    tgt = _four_ranges()
    cmp_, _ = _comparator(_findings_response())

    payload = cmp_.compare(ref, _cands(*tgt), _target(*tgt)).to_dict()

    assert payload["candidate_count"] == 4
    assert [f["fact_id"] for f in payload["findings"]] == ["t1", "t2", "t3", "t4"]
    assert payload["findings"][1]["result"] == MISMATCH
    assert payload["findings"][1]["quote_verified"] is True


def test_llm_off_with_two_candidates_holds_unknown():
    """LLM 을 못 쓰면 코드 판정으로 되돌아가지 않고 보류한다 — 되돌아가면 오판이 남는다."""
    ref = _fact("r1", "충전환경온도", current=("0.2", "C"))
    tgt = _four_ranges()
    cmp_ = FactComparator(runner=None, use_llm=False)

    out = cmp_.compare(ref, _cands(*tgt), _target(*tgt))

    assert out.result == UNKNOWN
    assert out.decided_by == BY_CODE


# --------------------------------------------------------------------------- #
# 예산 고갈 — 1:N 라우팅이 호출을 늘렸으므로 더 잘 생긴다
# --------------------------------------------------------------------------- #
def test_budget_exhaustion_is_counted_apart_from_parse_failures():
    """둘을 한 숫자에 섞으면 조치가 갈린다 — 하나는 예산, 하나는 모델/프롬프트다.

    예산 고갈은 **그 뒤 모든 항목**을 쓸어가는 계통 오류라, 고립된 파싱 실패와
    같은 칸에 두면 "왜 갑자기 전부 보류인가"를 사후에 설명할 수 없다.
    """
    from contentcompare.fact.llm_stage import LlmRunner

    ref = _fact("r1", "충전환경온도", current=("0.2", "C"))
    tgt = _four_ranges()
    chat = _CountingChat({"result": "match", "reason": "일치"})
    cmp_ = FactComparator(runner=LlmRunner(chat, max_calls=0))

    out = cmp_.compare(ref, _cands(*tgt), _target(*tgt))

    assert out.result == UNKNOWN
    assert cmp_.llm_budget_exceeded == 1
    assert cmp_.llm_failures == 1, "기존 키는 총계로 유지해야 소비자가 안 깨진다"


def test_parse_failure_is_not_counted_as_budget_exhaustion():
    ref = _fact("r1", "충전환경온도", current=("0.2", "C"))
    tgt = _four_ranges()

    class _BadJson:
        calls = 0

        def complete(self, system, user, *, temperature=0.0):
            return "JSON 이 아님"

    from contentcompare.fact.llm_stage import LlmRunner

    cmp_ = FactComparator(runner=LlmRunner(_BadJson()))
    cmp_.compare(ref, _cands(*tgt), _target(*tgt))

    assert cmp_.llm_budget_exceeded == 0 and cmp_.llm_failures == 1


# --------------------------------------------------------------------------- #
# 리포트 — 기준 1행 = 1줄을 유지하고 내역을 아래에 붙인다
# --------------------------------------------------------------------------- #
def _rendered(response: dict) -> str:
    from contentcompare.report.fact_report import render_fact_markdown

    ref = _fact("r1", "충전환경온도", current=("0.2", "C"))
    tgt = _four_ranges()
    cmp_, _ = _comparator(response)
    out = cmp_.compare(ref, _cands(*tgt), _target(*tgt))
    return render_fact_markdown(
        [out], reference_doc="기준.xlsx", target_docs=["규격서.docx"]
    )


def test_report_lists_every_candidate_finding():
    md = _rendered(_findings_response())

    assert md.count("Charge temperature range") >= 4, "4구간 내역이 리포트에 없다"
    assert "기준 1C vs 대상 0.7C" in md
    # 요약은 기준 1행 = 1줄을 유지한다
    assert md.count("| 1 | 충전환경온도 |") == 1


def test_report_flags_unverified_quotes():
    """인용 검증 실패는 사람이 원문을 확인하도록 표시로 남긴다."""
    response = _findings_response()
    response["findings"][2]["quote"] = "문서에 없는 문장"

    md = _rendered(response)

    assert "⚠️" in md and "문서에 없는 문장" in md


def test_report_omits_finding_block_for_single_candidate():
    """후보 1건이면 기존 리포트와 같아야 한다 — 없던 절이 생기면 회귀다."""
    from contentcompare.report.fact_report import render_fact_markdown

    ref = _fact("r1", "충전환경온도", current=("0.2", "C"))
    tgt = _fact("t1", "charge temperature range", "0~10C: 0.2C", current=("0.2", "C"))
    cmp_, _ = _comparator()
    out = cmp_.compare(ref, _cands(tgt, method=EXACT, score=1.0), _target(tgt))

    md = render_fact_markdown([out], reference_doc="기준.xlsx", target_docs=["규격서.docx"])

    assert "후보별 내역" not in md


# --------------------------------------------------------------------------- #
# 파이프라인 계측 — 이 변경이 없앤 오판의 양을 드러낸다
# --------------------------------------------------------------------------- #
class _ScriptedChat:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.calls = 0

    def complete(self, system, user, *, temperature=0.0):
        self.calls += 1
        return self.responses.pop(0) if self.responses else "{}"


class _FakeEmbedder:
    def embed(self, texts, kind="passage"):
        return [[1.0, 0.0] for _ in texts]


def _pipeline_store():
    """기준 1행 ↔ 대상 조건 구간 2건.

    이름을 다르게 두는 것은 의도적이다 — ``FactMatcher`` 의 이름 완전일치 경로는
    **조기 종료**라 후보를 1건만 만든다(``fact_matcher.py`` 의 ``EXACT`` 분기). 실제
    사례(한국어 기준 ↔ 영어 대상)도 완전일치가 원리적으로 불가능해 임베딩 recall 을
    타므로, 이 테스트가 그 경로를 재현한다.
    """
    from contentcompare.fact.fact_store import FactStore

    def _f(fid: str, name: str, value: str) -> Fact:
        return Fact(fact_id=fid, entity_name=name, search_text=name,
                    evidence_text=f"{name} {value}A", source={"block_id": "b01"},
                    attributes={"current": Attribute(value, "A")})

    store = FactStore()
    store.add(DocFacts(doc_name="기준.xlsx", facts=FactSet(
        facts=[_f("fact-row-1", "충전전류", "0.2")])), is_reference=True)
    store.add(DocFacts(doc_name="규격서.docx", facts=FactSet(facts=[
        _f("fact-word-1", "charge current low", "0.2"),
        _f("fact-word-2", "charge current room", "1.0"),
    ])))
    return store


_CONCEPT_RESPONSE = json.dumps({"pairs": [
    {"left_fact_id": "fact-row-1", "right_fact_id": "fact-word-1", "relation": "same_as",
     "left_text": "충전전류 0.2A", "right_text": "charge current low 0.2A", "reason": "같은 항목"},
    {"left_fact_id": "fact-row-1", "right_fact_id": "fact-word-2", "relation": "same_as",
     "left_text": "충전전류 0.2A", "right_text": "charge current room 1.0A", "reason": "같은 항목"},
]}, ensure_ascii=False)


def _run_pipeline(tmp_path, chat):
    from contentcompare.config import AppConfig, FactConfig
    from contentcompare.fact.pipeline import FactPipeline

    cfg = AppConfig()
    cfg.fact = FactConfig(
        artifacts_dir=str(tmp_path / "artifacts"),
        ontology_path=str(tmp_path / "없음.yaml"),
    )
    pipe = FactPipeline(cfg, chat=chat, embedder=_FakeEmbedder())
    return pipe._compare_from_store(_pipeline_store(), "기준.xlsx", ["규격서.docx"])


def test_pipeline_stats_expose_the_overridden_count(tmp_path):
    """``multi_candidate_overridden`` 이 이 작업의 성과 지표다 — 0 이어도 유효한 정보다."""
    chat = _ScriptedChat([_CONCEPT_RESPONSE, json.dumps({
        "result": "mismatch",
        "findings": [
            {"fact_id": "fact-word-1", "result": "match",
             "quote": "charge current low 0.2A", "reason": "일치"},
            {"fact_id": "fact-word-2", "result": "mismatch",
             "mismatch_attributes": ["current"],
             "quote": "charge current room 1.0A", "reason": "다름"},
        ],
        "reason": "2건 중 1건이 다릅니다",
    }, ensure_ascii=False)])

    stats = _run_pipeline(tmp_path, chat).compare_stats

    assert stats["multi_candidate_comparisons"] == 1
    assert stats["multi_candidate_overridden"] == 1, "코드 match → 최종 mismatch 가 안 잡혔다"
    assert stats["quote_unverified"] == 0
    assert stats["dropped_findings"] == 0


def test_pipeline_counts_unverified_quotes(tmp_path):
    chat = _ScriptedChat([_CONCEPT_RESPONSE, json.dumps({
        "result": "match",
        "findings": [
            {"fact_id": "fact-word-1", "result": "match", "quote": "지어낸 문장", "reason": "?"},
            {"fact_id": "fact-word-2", "result": "match",
             "quote": "charge current room 1.0A", "reason": "일치"},
        ],
        "reason": "일치",
    }, ensure_ascii=False)])

    stats = _run_pipeline(tmp_path, chat).compare_stats

    assert stats["quote_unverified"] == 1

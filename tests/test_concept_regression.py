"""F7 실측 회귀 — 2026-08-03 라이브에서 실제로 잘못 이어졌던 쌍을 고정한다.

근거: docs/FACT_F3_5_LIVE_REPORT.md §9.4, docs/FACT_F7_DESIGN.md §1.
이 세 쌍은 임베딩 점수가 0.6084~0.6944 로 정답(0.7656)과 섞여 있어 임계값으로는
가를 수 없었다. 개념 층이 이것을 막는지 확인한다.
"""

import json

from contentcompare.fact.concept_builder import build_concept_graph
from contentcompare.fact.concept_models import DIFFERS_BY, SAME_AS
from contentcompare.fact.fact_models import Fact, FactSet
from contentcompare.fact.fact_store import DocFacts, FactStore
from contentcompare.fact.llm_stage import LlmRunner
from contentcompare.fact.ontology import load_ontology
from contentcompare.fact.record_models import Attribute

# 실측 오매칭 3쌍 + 정답 1쌍.
BAD_PAIRS = [
    ("1개월저장온도", "표준환경온도", "측정조건"),
    ("평가환경온도", "평가 환경 습도", "물리량"),
    ("충전환경온도", "정격 충전 전압", "물리량"),
]


class _RelationChat:
    """쌍마다 미리 정한 관계를 돌려주는 chat."""

    def __init__(self, relations):
        self.relations = relations
        self.calls = 0

    def complete(self, system, user, *, temperature=0.0):
        self.calls += 1
        pairs = []
        for (left_id, right_id), (relation, axis) in self.relations.items():
            if left_id in user and right_id in user:
                pairs.append({
                    "left_fact_id": left_id, "right_fact_id": right_id,
                    "relation": relation, "axis": axis, "reason": "테스트",
                    "left_text": "", "right_text": "",
                })
        return json.dumps({"pairs": pairs}, ensure_ascii=False)


class _FakeEmbedder:
    """모든 쌍을 후보로 만든다 — recall 이 넉넉해도 개념 층이 막는지 보려는 것."""

    def embed(self, texts, kind="passage"):
        return [[1.0, 0.0] for _ in texts]


class _GroupEmbedder:
    """의도한 3쌍만 후보가 되도록 그룹별 직교 벡터를 준다.

    모든 벡터를 같게 주면 3x3 전부가 후보가 되어, 온톨로지가 덮지 않는 6쌍이
    LLM 으로 가고 chat.calls == 0 이 성립하지 않는다 — 그러면 이 테스트가
    검증하려는 '온톨로지가 LLM 을 건너뛴다'를 증명하지 못한다.
    """

    def embed(self, texts, kind="passage"):
        out = []
        for t in texts:
            if "저장" in t or "표준" in t:
                out.append([1.0, 0.0, 0.0])
            elif "평가" in t or "습도" in t:
                out.append([0.0, 1.0, 0.0])
            else:  # 충전 / 전압
                out.append([0.0, 0.0, 1.0])
        return out


def _fact(fact_id, name, evidence) -> Fact:
    return Fact(fact_id=fact_id, entity_name=name, search_text=f"{name} {evidence}",
                evidence_text=evidence,
                attributes={"target_value": Attribute(value=1, unit="")})


def _store() -> FactStore:
    store = FactStore()
    store.add(DocFacts(doc_name="기준.xlsx", facts=FactSet(facts=[
        _fact("r1", "1개월저장온도", "-10.0, 35.0, 80.0"),
        _fact("r2", "평가환경온도", "21.0, 24.0, 28.0"),
        _fact("r3", "충전환경온도", "0.0, 25.0, 45.0"),
    ])), is_reference=True)
    store.add(DocFacts(doc_name="규격서.docx", facts=FactSet(facts=[
        _fact("w1", "표준환경온도", "표준환경온도, 21 ~ 29, ℃"),
        _fact("w2", "평가 환경 습도", "평가 환경 습도, 33 ~ 53, %RH"),
        _fact("w3", "정격 충전 전압", "정격 충전 전압, 4.55, V"),
    ])))
    return store


def test_measured_false_matches_are_not_linked():
    relations = {
        ("r1", "w1"): (DIFFERS_BY, "측정조건"),
        ("r2", "w2"): (DIFFERS_BY, "물리량"),
        ("r3", "w3"): (DIFFERS_BY, "물리량"),
    }
    runner = LlmRunner(_RelationChat(relations), max_calls=10)
    graph = build_concept_graph(_store(), embedder=_FakeEmbedder(), runner=runner)
    for ref_id in ("r1", "r2", "r3"):
        assert graph.partners("기준.xlsx", ref_id, "규격서.docx") == [], ref_id


def test_promoted_ontology_blocks_them_without_llm(tmp_path):
    """온톨로지에 승격하면 LLM 없이도 막힌다 — 재현성 장치."""
    lines = ["differs_by:"]
    for left, right, axis in BAD_PAIRS:
        lines.append(f'  - names: ["{left}", "{right}"]\n    axis: "{axis}"')
    path = tmp_path / "ontology.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    chat = _RelationChat({})
    graph = build_concept_graph(
        _store(), embedder=_GroupEmbedder(), runner=LlmRunner(chat, max_calls=10),
        ontology=load_ontology(str(path)),
    )
    assert chat.calls == 0
    assert graph.stats["pairs_from_ontology"] == 3
    for ref_id in ("r1", "r2", "r3"):
        assert graph.partners("기준.xlsx", ref_id, "규격서.docx") == []


class _NeverSimilarEmbedder:
    """모든 텍스트가 직교 — 유사도 recall 이 어떤 쌍도 만들지 못한다.

    "유사도로는 못 잇는 진짜 동의어"의 최소 재현이다. 이 임베더를 쓰면 온톨로지
    보강이 recall **밖에서** 돌지 않는 한 쌍 자체가 생기지 않는다.
    """

    def __init__(self) -> None:
        self._axis: dict[str, int] = {}

    def embed(self, texts, kind="passage"):
        out = []
        for t in texts:
            self._axis.setdefault(t, len(self._axis))
            vec = [0.0] * 32
            vec[self._axis[t] % 32] = 1.0
            out.append(vec)
        return out


def _synonym_store() -> FactStore:
    store = FactStore()
    store.add(DocFacts(doc_name="기준.xlsx", facts=FactSet(facts=[
        _fact("r9", "고객 표준 버전", "배터리승인규격 ver 4.7")])), is_reference=True)
    store.add(DocFacts(doc_name="규격서.docx", facts=FactSet(facts=[
        _fact("w9", "문서 기준 규격", "본 규격은 배터리승인규격 ver 4.7 을 따른다")])))
    return store


def test_true_synonym_is_linked_when_promoted(tmp_path):
    """고객 표준 버전 ↔ 문서 기준 규격 — 슬롯으로는 못 잇는 진짜 동의어.

    임베더를 직교로 두고 recall 임계까지 올려, **유사도로는 후보조차 되지 않는**
    상황에서 승격이 작동하는지 본다. 온톨로지 조회가 recall 뒤에 있으면 실패한다.
    """
    path = tmp_path / "ontology.yaml"
    path.write_text('same_as:\n  - names: ["고객 표준 버전", "문서 기준 규격"]\n',
                    encoding="utf-8")

    graph = build_concept_graph(_synonym_store(), embedder=_NeverSimilarEmbedder(),
                                runner=None, min_score=0.99,
                                ontology=load_ontology(str(path)))
    partners = graph.partners("기준.xlsx", "r9", "규격서.docx")
    assert [m.fact_id for m in partners] == ["w9"]
    assert graph.edges[0].relation == SAME_AS


def test_synonym_stays_unlinked_without_promotion(tmp_path):
    """대조군 — 온톨로지가 없으면 같은 설정에서 쌍조차 만들어지지 않는다.

    위 테스트가 '보강 덕분에' 통과한 것임을 증명한다(유사도가 우연히 이어준 게 아니다).
    """
    graph = build_concept_graph(_synonym_store(), embedder=_NeverSimilarEmbedder(),
                                runner=None, min_score=0.99)
    assert graph.edges == []
    assert graph.partners("기준.xlsx", "r9", "규격서.docx") == []

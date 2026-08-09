"""영어 대상 문서 실측 회귀 — 2026-08-05 라이브에서 실제로 무너진 조건을 고정한다.

근거: 기준 엑셀(한국어) ↔ ``samples/spec_en.docx`` 실행에서 20건 중 9건이 뒤집혔고
전부 놓치는 방향이었다(실제 불일치 `공칭전압 3.89 vs 3.85V` 포함). 원인은 언어가
아니라 언어가 드러낸 잠재 결함이다:

1. 언어가 다르면 ``norm_name`` 완전일치가 0건 → 모든 연결이 LLM 경로로 몰린다
2. 근거 검증은 LLM 이 제안한 ``same_as`` 에만 걸린다(설계 §2.3)
3. 엑셀 단일값 fact 의 ``evidence_text`` 는 ``"3.89"`` — **토큰 1개**
4. ``EVIDENCE_MIN_TOKENS = 3`` 에 걸려 강등 → 연결 소멸 → ``missing``

한국어 문서에서는 같은 행이 ``BY_CODE``(이름 완전일치)로 이어져 근거 검증을
건너뛰었기 때문에 이 결함이 가려져 있었다. 이 파일은 그 상황을 가짜 LLM/임베더로
재현한다 — 네트워크·Office 불필요(기존 회귀 테스트 규약).
"""

import json

from contentcompare.fact.concept_builder import build_concept_graph
from contentcompare.fact.concept_models import FactRef
from contentcompare.fact.fact_comparator import MISMATCH, FactComparator
from contentcompare.fact.fact_matcher import ConceptMatcher
from contentcompare.fact.fact_models import Fact, FactSet
from contentcompare.fact.fact_store import DocFacts, FactStore
from contentcompare.fact.llm_stage import LlmRunner
from contentcompare.fact.record_models import Attribute

REF_DOC = "자표준문서.xlsx"
EN_DOC = "spec_en.docx"

# 실측 그대로 — 기준은 한국어 항목명 + 단일값 원문, 대상은 영어 문장.
# 이름이 겹치지 않으므로 코드 완전일치 경로가 **하나도** 성립하지 않는다.
PAIRS = [
    # (ref_id, 기준 항목, 기준 원문, 기준 값,
    #  tgt_id, 대상 항목, 대상 원문, 대상 값)
    ("r-voltage", "공칭전압", "3.89", 3.89,
     "w-voltage", "Nominal voltage", "The nominal voltage is 3.85V.", 3.85),
    ("r-capacity", "공칭용량", "1150.0", 1150.0,
     "w-capacity", "Nominal capacity", "Nominal capacity, 1150, mAh", 1150.0),
]


def _fact(fact_id: str, name: str, evidence: str, value: float, unit: str = "") -> Fact:
    return Fact(
        fact_id=fact_id, entity_name=name, evidence_text=evidence,
        search_text=f"{name} {evidence}",
        attributes={"target_value": Attribute(value=value, unit=unit)},
    )


def _store() -> FactStore:
    store = FactStore()
    store.add(DocFacts(doc_name=REF_DOC, facts=FactSet(facts=[
        _fact(rid, name, ev, val) for rid, name, ev, val, *_ in PAIRS
    ])), is_reference=True)
    store.add(DocFacts(doc_name=EN_DOC, facts=FactSet(facts=[
        _fact(tid, name, ev, val, "V" if "voltage" in name.lower() else "mAh")
        for *_, tid, name, ev, val in PAIRS
    ])))
    return store


class _SameAsChat:
    """실측의 LLM 응답 재현 — ``same_as`` + **양쪽 원문 전체 인용**.

    실제 라이브에서 LLM 은 정확히 인용했다. 게이트가 그것을 거부한 것이 결함이었다.
    """

    def __init__(self, right_quote: str = "", quote_names: bool = False) -> None:
        self.right_quote = right_quote
        """대상 쪽 인용을 바꿔치기한다(부분 인용 대조군용). 비우면 원문 전체."""
        self.quote_names = quote_names
        """참이면 값이 아니라 **항목명**을 인용한다(2026-08-06 실측에서 관찰된 형태)."""
        self.calls = 0

    def complete(self, system, user, *, temperature=0.0):
        self.calls += 1
        pairs = []
        for ref_id, ref_name, ref_ev, _, tgt_id, tgt_name, tgt_ev, _ in PAIRS:
            if ref_id not in user or tgt_id not in user:
                continue
            left = ref_name if self.quote_names else ref_ev
            right = tgt_name if self.quote_names else (self.right_quote or tgt_ev)
            pairs.append({
                "left_fact_id": ref_id, "right_fact_id": tgt_id,
                "relation": "same_as", "axis": "",
                "left_text": left,
                "right_text": right,
                "reason": "같은 항목의 한국어/영어 표기",
            })
        return json.dumps({"pairs": pairs}, ensure_ascii=False)


class _AllSimilarEmbedder:
    """모든 쌍을 후보로 올린다 — recall 이 아니라 게이트를 보려는 것."""

    def embed(self, texts, kind="passage"):
        return [[1.0, 0.0] for _ in texts]


def _graph(chat=None):
    return build_concept_graph(
        _store(), embedder=_AllSimilarEmbedder(),
        runner=LlmRunner(chat or _SameAsChat(), max_calls=10),
    )


# --------------------------------------------------------------------- #
def test_no_exact_name_match_so_every_link_goes_through_the_llm_path():
    """이 회귀의 전제 — 코드 확정 경로가 하나도 없어야 게이트가 노출된다."""
    graph = _graph()
    assert graph.stats["pairs_by_code"] == 0
    assert graph.stats["pairs_by_llm"] == graph.stats["pairs_considered"]


def test_single_value_quote_survives_the_evidence_gate():
    """단일값 원문을 전체 인용한 연결이 살아남는다(수정 전에는 100% 강등됐다)."""
    graph = _graph()
    assert graph.stats["rejected_evidence"] == 0
    assert graph.stats["same_as"] == len(PAIRS)
    for ref_id, *_ in PAIRS:
        assert graph.partners(REF_DOC, ref_id, EN_DOC), ref_id


def test_real_value_mismatch_is_reported():
    """이 작업의 성공 기준 — 영어 문서에서도 3.89 vs 3.85V 를 찾아낸다.

    수정 전에는 개념 연결이 사라져 ``missing`` 으로 보고됐다.
    """
    store = _store()
    graph = _graph()
    ref_doc, target = store.reference, store.targets[0]
    matcher = ConceptMatcher(graph, ref_doc.doc_name, target.doc_name,
                             target.facts.facts)
    comparator = FactComparator(use_llm=False)  # 코드만으로 판정되는지 본다

    ref = next(f for f in ref_doc.facts.facts if f.fact_id == "r-voltage")
    result = comparator.compare(ref, matcher.search(ref), target)
    assert result.result == MISMATCH
    assert result.mismatch_attributes == ["target_value"]


def test_entity_name_quotes_link_the_pair():
    """LLM 이 값이 아니라 **항목명**을 인용해도 연결이 살아남는다.

    2026-08-06 실측에서 관찰된 형태다. LLM 의 사유가 "공칭용량의 영문 표기는
    Nominal capacity 입니다" 였으니 이름을 인용한 것은 주장에 맞는 근거였는데,
    영어 이름이 2 토큰이라 하한에 걸려 20건 중 이 쌍이 통째로 죽었다.
    한국어 쪽(``공칭용량``)은 bigram 으로 4 토큰이 되어 통과했다 — 언어 편향이다.
    """
    graph = _graph(_SameAsChat(quote_names=True))
    assert graph.stats["rejected_evidence"] == 0
    assert graph.stats["same_as"] == len(PAIRS)
    for ref_id, *_ in PAIRS:
        assert graph.partners(REF_DOC, ref_id, EN_DOC), ref_id


def test_partial_quote_from_rich_source_is_still_rejected():
    """대조군 — 게이트를 푼 것이 '아무 인용이나 통과'는 아니다.

    ``Nominal capacity, 1150, mAh`` 에서 ``1150`` 만 인용하면 그 토큰이 원문에
    **실재해 커버리지는 100%** 다 — 오직 토큰 하한만이 이것을 막는다(용량 쌍).
    전압 쌍은 인용이 원문에 아예 없어 커버리지에서 걸린다. 두 방어선이 모두
    살아 있는지 한 번에 본다. 하한 단독 검증은
    ``test_concept_assembler.test_partial_quote_from_rich_source_is_rejected`` 참고.
    """
    graph = _graph(_SameAsChat(right_quote="1150"))
    assert graph.stats["same_as"] == 0
    assert graph.stats["rejected_evidence"] == len(PAIRS)
    capacity = graph.edge_of(FactRef(REF_DOC, "r-capacity"),
                             FactRef(EN_DOC, "w-capacity"))
    assert capacity.rejected_by == "evidence"

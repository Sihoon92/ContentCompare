"""F5 후보 검색 테스트 — 가짜 임베더 주입(네트워크 불필요)."""

from __future__ import annotations

from contentcompare.fact.fact_matcher import (
    BM25_METHOD,
    EMBED,
    EXACT,
    FactMatcher,
    norm_name,
)
from contentcompare.fact.fact_models import Fact
from contentcompare.fact.record_models import Attribute
from contentcompare.fact.fact_store import DocFacts, FactStore
from contentcompare.fact.fact_models import FactSet


def _fact(fid: str, name: str, text: str = "", **attrs) -> Fact:
    return Fact(
        fact_id=fid,
        entity_name=name,
        attributes={k: Attribute(v, "") for k, v in attrs.items()},
        search_text=text or name,
        evidence_text=text or name,
    )


class _FakeEmbedder:
    """텍스트 → 고정 벡터. 사전에 없으면 직교에 가까운 기본 벡터를 준다."""

    def __init__(self, table: dict[str, list[float]], default=(0.0, 0.0, 1.0)):
        self.table = table
        self.default = list(default)
        self.seen: list[tuple[str, str]] = []

    def embed(self, texts, *, kind="passage"):
        out = []
        for t in texts:
            self.seen.append((kind, t))
            out.append(list(self.table.get(t, self.default)))
        return out


# --------------------------------------------------------------------------- #
# 정규화 · 완전일치
# --------------------------------------------------------------------------- #
def test_norm_name_absorbs_spacing_and_symbols():
    assert norm_name("정격 충전 전압") == norm_name("정격충전전압")
    assert norm_name("1개월 저장(온도)") == norm_name("1개월저장온도")
    assert norm_name("ＡＢＣ") == "abc"  # 전각 → 반각


def test_exact_match_wins_without_embedding_call():
    embedder = _FakeEmbedder({})
    m = FactMatcher([_fact("t1", "충전 환경 온도")], embedder=embedder)
    embedder.seen.clear()
    cands = m.search(_fact("r1", "충전환경온도"))
    assert [c.method for c in cands] == [EXACT]
    assert cands[0].score == 1.0 and cands[0].needs_review is False
    assert embedder.seen == []  # 완전일치는 임베딩을 부르지 않는다


# --------------------------------------------------------------------------- #
# 임베딩 랭킹 + 임계값
# --------------------------------------------------------------------------- #
def _embed_matcher(**kw):
    targets = [
        _fact("t1", "Standard ambient temperature", "Standard ambient temperature 21 29"),
        _fact("t2", "충전 규격 상세조건", "충전 규격 상세조건 4.55"),
    ]
    table = {
        "Standard ambient temperature 21 29": [1.0, 0.0, 0.0],
        "충전 규격 상세조건 4.55": [0.0, 1.0, 0.0],
        "표준환경온도 21 25 29": [0.9, 0.1, 0.0],   # 교차언어 — t1 과 유사
        "경계항목": [0.7, 0.7, 0.0],                 # t1 과 코사인 ≈0.707 (경계 구간)
        "무관항목": [0.0, 0.0, 1.0],                 # 둘 다와 직교
    }
    return FactMatcher(targets, embedder=_FakeEmbedder(table), **kw)


def test_embedding_finds_cross_language_match():
    m = _embed_matcher()
    cands = m.search(_fact("r1", "표준환경온도", "표준환경온도 21 25 29"))
    assert cands[0].fact.fact_id == "t1"
    assert cands[0].method == EMBED and cands[0].score > 0.9


def test_below_min_score_yields_no_candidate():
    """명백히 무관하면 후보를 만들지 않는다 — 이것이 missing 판정의 근거다."""
    m = _embed_matcher(min_score=0.65)
    assert m.search(_fact("r1", "무관항목", "무관항목")) == []


def test_borderline_score_is_flagged_for_review():
    """점수만으로는 정답/오매칭이 갈리지 않으므로(실측 0.697 vs 0.700)
    경계 구간은 코드가 단정하지 않고 검토 대상으로 표시한다."""
    m = _embed_matcher(min_score=0.65, review_score=0.75)
    borderline = m.search(_fact("r1", "경계항목", "경계항목"))
    assert 0.65 <= borderline[0].score < 0.75 and borderline[0].needs_review is True
    # 충분히 높은 점수는 검토 표시가 붙지 않는다.
    strong = m.search(_fact("r2", "표준환경온도", "표준환경온도 21 25 29"))
    assert strong[0].needs_review is False


def test_query_and_passage_kinds_are_distinguished():
    """e5 계열 교차언어 검색 규약 — 대상은 passage, 검색어는 query 로 임베딩한다."""
    m = _embed_matcher()
    m.search(_fact("r1", "표준환경온도", "표준환경온도 21 25 29"))
    kinds = {kind for kind, _ in m.embedder.seen}
    assert kinds == {"passage", "query"}


# --------------------------------------------------------------------------- #
# BM25 폴백(임베딩 백엔드 없음)
# --------------------------------------------------------------------------- #
def test_bm25_used_when_no_embedder():
    targets = [
        _fact("t1", "1개월 저장 조건", "1개월 저장 조건 -10 35 80"),
        _fact("t2", "적용 규격", "적용 규격 SEC Req"),
    ]
    m = FactMatcher(targets, top_k=2, bm25_min_score=0.1)
    cands = m.search(_fact("r1", "1개월저장온도", "1개월저장온도 -10 35 80"))
    assert cands[0].fact.fact_id == "t1" and cands[0].method == BM25_METHOD


def test_empty_target_set_returns_nothing():
    assert FactMatcher([]).search(_fact("r1", "x")) == []


# --------------------------------------------------------------------------- #
# FactStore
# --------------------------------------------------------------------------- #
def test_store_tracks_reference_targets_and_low_confidence():
    store = FactStore()
    store.add(DocFacts("기준.xlsx", "excel", FactSet(facts=[_fact("r1", "a")])), is_reference=True)
    target = DocFacts("발표.pptx", "ppt", FactSet(facts=[_fact("t1", "a"), _fact("t2", "b")]),
                      low_confidence_ids={"t2"})
    store.add(target)

    assert store.ready is True
    assert store.summary() == {
        "reference": "기준.xlsx", "reference_facts": 1, "targets": {"발표.pptx": 2},
    }
    assert target.is_low_confidence(_fact("t2", "b")) is True
    assert target.is_low_confidence(_fact("t1", "a")) is False


def test_store_not_ready_without_targets():
    store = FactStore()
    store.add(DocFacts("기준.xlsx", "excel", FactSet(facts=[_fact("r1", "a")])), is_reference=True)
    assert store.ready is False

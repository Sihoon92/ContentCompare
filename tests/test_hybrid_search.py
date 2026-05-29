"""Phase 2 하이브리드 검색 테스트: BM25 / RRF / MMR / 캐시 / HybridIndex."""

from __future__ import annotations

from contentcompare.models import DocItem, DocType
from contentcompare.similarity import (
    BM25,
    CachedEmbedder,
    HybridIndex,
    mmr_select,
    reciprocal_rank_fusion,
    tokenize,
)


class FakeEmbedder:
    """어휘 존재 여부로 결정적 벡터를 만드는 테스트용 임베더."""

    VOCAB = ["매출", "영업이익", "직원", "2023", "2024", "억원", "명", "제품"]

    def __init__(self):
        self.calls: list[list[str]] = []

    def embed(self, texts):
        self.calls.append(list(texts))
        return [[1.0 if w in t else 0.0 for w in self.VOCAB] for t in texts]


def _item(item_id, text, doc_id="doc"):
    return DocItem(item_id, doc_id, DocType.WORD, text, item_id)


# --------------------------------------------------------------------------- #
# 토크나이저
# --------------------------------------------------------------------------- #
def test_tokenize_splits_korean_english_numbers():
    toks = tokenize("매출 2023 ABC-12")
    assert "매출" in toks and "2023" in toks and "abc" in toks and "12" in toks


def test_tokenize_adds_korean_bigrams():
    toks = tokenize("영업이익")
    assert "영업이익" in toks and "영업" in toks and "이익" in toks


# --------------------------------------------------------------------------- #
# BM25
# --------------------------------------------------------------------------- #
def test_bm25_ranks_exact_term_match_higher():
    bm = BM25()
    bm.index([tokenize("매출 100 억원"), tokenize("직원 500 명")])
    scores = bm.scores(tokenize("직원"))
    assert scores[1] > scores[0]


# --------------------------------------------------------------------------- #
# RRF
# --------------------------------------------------------------------------- #
def test_rrf_rewards_agreement_across_rankings():
    # doc 2 는 두 순위 모두 상위 → 융합 1위여야 한다.
    fused = reciprocal_rank_fusion([[1, 2, 3], [2, 3, 1]], k=60)
    assert fused[0][0] == 2


# --------------------------------------------------------------------------- #
# MMR
# --------------------------------------------------------------------------- #
def test_mmr_per_doc_cap_limits_one_document():
    # 모두 같은 doc "A" 3개 + doc "B" 1개. cap=1 이면 A 1개만.
    vecs = {0: [1.0, 0.0], 1: [1.0, 0.0], 2: [1.0, 0.0], 3: [0.0, 1.0]}
    docs = {0: "A", 1: "A", 2: "A", 3: "B"}
    rel = {0: 0.9, 1: 0.8, 2: 0.7, 3: 0.1}
    chosen = mmr_select(
        list(vecs), rel, vec_of=lambda i: vecs[i], doc_of=lambda i: docs[i],
        lambda_=0.7, top_k=4, per_doc_cap=1,
    )
    assert sum(1 for i in chosen if docs[i] == "A") == 1
    assert 3 in chosen


def test_mmr_prefers_diverse_when_lambda_low():
    # doc0/doc1 동일 벡터(중복), doc2 직교. 다양성 우선이면 2가 1보다 먼저.
    vecs = {0: [1.0, 0.0], 1: [1.0, 0.0], 2: [0.0, 1.0]}
    rel = {0: 1.0, 1: 0.95, 2: 0.5}
    chosen = mmr_select(
        list(vecs), rel, vec_of=lambda i: vecs[i], doc_of=lambda i: str(i),
        lambda_=0.2, top_k=2, per_doc_cap=None,
    )
    assert chosen == [0, 2]


# --------------------------------------------------------------------------- #
# 임베딩 캐시
# --------------------------------------------------------------------------- #
def test_cached_embedder_reuses_disk(tmp_path):
    base = FakeEmbedder()
    cache_dir = str(tmp_path / "emb")
    texts = ["매출 2023", "직원 명"]

    e1 = CachedEmbedder(base, cache_dir, model_name="bge-m3")
    v1 = e1.embed(texts)
    assert len(base.calls) == 1  # 최초엔 백엔드 호출

    # 새 인스턴스(=재실행 모사)도 디스크 캐시에서 읽어 백엔드를 안 부른다.
    e2 = CachedEmbedder(base, cache_dir, model_name="bge-m3")
    v2 = e2.embed(texts)
    assert v2 == v1
    assert len(base.calls) == 1  # 추가 호출 없음


def test_cached_embedder_passthrough_without_dir():
    base = FakeEmbedder()
    e = CachedEmbedder(base, "", model_name="m")
    e.embed(["매출"])
    e.embed(["매출"])
    assert len(base.calls) == 2  # 캐시 비활성 → 매번 호출


# --------------------------------------------------------------------------- #
# HybridIndex 통합
# --------------------------------------------------------------------------- #
def test_hybrid_index_finds_lexical_match_beyond_embedding():
    # 코드 "REV-2023" 는 임베딩 어휘에 없지만 BM25 로 잡혀야 한다.
    idx = HybridIndex(FakeEmbedder(), fusion="rrf")
    idx.add([
        _item("a", "직원 500 명", doc_id="d1"),
        _item("b", "특이코드 REV-2023 매출", doc_id="d2"),
    ])
    hits = idx.search("REV-2023", recall_k=10, top_k=2)
    assert hits
    assert hits[0].item.item_id == "b"


def test_hybrid_index_respects_per_doc_cap():
    idx = HybridIndex(FakeEmbedder(), fusion="rrf", per_doc_cap=1)
    idx.add([
        _item("a1", "매출 2023 억원", doc_id="same"),
        _item("a2", "매출 2024 억원", doc_id="same"),
        _item("b1", "매출 제품 직원", doc_id="other"),
    ])
    hits = idx.search("매출 억원 제품", recall_k=10, top_k=3)
    from_same = [h for h in hits if h.item.doc_id == "same"]
    assert len(from_same) <= 1


def test_hybrid_index_empty_returns_nothing():
    idx = HybridIndex(FakeEmbedder())
    assert idx.search("아무거나", recall_k=5, top_k=3) == []

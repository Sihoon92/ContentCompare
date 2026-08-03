"""로컬 ONNX 임베딩 백엔드 테스트 — 세션/토크나이저 주입으로 실제 모델 불필요.

평균 풀링 + L2 정규화 수치를 손계산과 대조한다.
"""

from __future__ import annotations

import os

import pytest

from contentcompare.config import LLMConfig
from contentcompare.llm.local_onnx import LocalOnnxEmbedding, _mean_pool_normalize


class Enc:
    def __init__(self, ids, attention_mask):
        self.ids = ids
        self.attention_mask = attention_mask


class FakeTokenizer:
    def __init__(self, encs):
        self._encs = encs
        self.seen = None

    def encode_batch(self, texts):
        self.seen = list(texts)
        return self._encs


class Inp:
    def __init__(self, name):
        self.name = name


class FakeSession:
    """get_inputs/run 흉내. run 은 feed 를 무시하고 미리 정한 last_hidden 을 반환."""

    def __init__(self, last_hidden, input_names=("input_ids", "attention_mask")):
        self._lh = last_hidden
        self._names = input_names
        self.feed = None

    def get_inputs(self):
        return [Inp(n) for n in self._names]

    def run(self, _outputs, feed):
        self.feed = feed
        return [self._lh]


def _cfg(path="/tmp", prefix=""):
    cfg = LLMConfig(backend="onnx")
    cfg.embed_model_path = path
    cfg.embed_prefix = prefix
    return cfg


# --------------------------------------------------------------------------- #
# 풀링/정규화 단위
# --------------------------------------------------------------------------- #
def test_mean_pool_normalize_basic():
    # 두 토큰 평균 = [3,4], L2 정규화 → [0.6,0.8]
    out = _mean_pool_normalize([[3.0, 4.0], [3.0, 4.0]], [1, 1])
    assert out == pytest.approx([0.6, 0.8])


def test_mean_pool_excludes_padding():
    # 두 번째 토큰은 mask=0 → 무시. 평균 = 첫 토큰 [3,4] → [0.6,0.8]
    out = _mean_pool_normalize([[3.0, 4.0], [100.0, 100.0]], [1, 0])
    assert out == pytest.approx([0.6, 0.8])


# --------------------------------------------------------------------------- #
# 백엔드 통합(주입)
# --------------------------------------------------------------------------- #
def test_embed_with_injected_session(tmp_path):
    encs = [Enc([5, 6], [1, 1])]
    tok = FakeTokenizer(encs)
    sess = FakeSession(last_hidden=[[[3.0, 4.0], [3.0, 4.0]]])  # (B=1,T=2,H=2)
    be = LocalOnnxEmbedding(_cfg(str(tmp_path)), session=sess, tokenizer=tok)
    out = be.embed(["문장"])
    assert len(out) == 1
    assert out[0] == pytest.approx([0.6, 0.8])


def test_embed_applies_prefix(tmp_path):
    encs = [Enc([1], [1])]
    tok = FakeTokenizer(encs)
    sess = FakeSession(last_hidden=[[[1.0, 0.0]]])
    be = LocalOnnxEmbedding(_cfg(str(tmp_path), prefix="query: "), session=sess, tokenizer=tok)
    be.embed(["안녕"])
    assert tok.seen == ["query: 안녕"]


def test_embed_applies_query_passage_prefix_by_kind(tmp_path):
    """e5 접두어 분리: kind=query/passage 에 따라 다른 접두어가 붙는다."""
    cfg = _cfg(str(tmp_path))
    cfg.embed_query_prefix = "query: "
    cfg.embed_passage_prefix = "passage: "

    tok = FakeTokenizer([Enc([1], [1])])
    sess = FakeSession(last_hidden=[[[1.0, 0.0]]])
    be = LocalOnnxEmbedding(cfg, session=sess, tokenizer=tok)

    be.embed(["최대충전전류"], kind="query")
    assert tok.seen == ["query: 최대충전전류"]

    be.embed(["maximum charging current"], kind="passage")
    assert tok.seen == ["passage: maximum charging current"]


def test_embed_kind_prefix_falls_back_to_common(tmp_path):
    """query/passage 전용 접두어가 비면 공통 embed_prefix 로 폴백한다."""
    cfg = _cfg(str(tmp_path), prefix="common: ")  # 전용값 미설정
    tok = FakeTokenizer([Enc([1], [1])])
    sess = FakeSession(last_hidden=[[[1.0, 0.0]]])
    be = LocalOnnxEmbedding(cfg, session=sess, tokenizer=tok)

    be.embed(["x"], kind="query")
    assert tok.seen == ["common: x"]


def test_embed_feeds_only_declared_inputs(tmp_path):
    encs = [Enc([7, 8], [1, 1])]
    sess = FakeSession(
        last_hidden=[[[1.0, 0.0], [1.0, 0.0]]],
        input_names=("input_ids", "attention_mask", "token_type_ids"),
    )
    be = LocalOnnxEmbedding(_cfg(str(tmp_path)), session=sess, tokenizer=FakeTokenizer(encs))
    be.embed(["x"])
    assert set(sess.feed) == {"input_ids", "attention_mask", "token_type_ids"}
    # numpy 배열일 수 있으므로 list 로 변환 후 비교(배열 == 는 요소별 비교라 단정 불가).
    assert [list(row) for row in sess.feed["token_type_ids"]] == [[0, 0]]  # zeros, input 과 같은 길이


def test_missing_path_raises():
    be = LocalOnnxEmbedding(_cfg(""))
    with pytest.raises(RuntimeError, match="embed_model_path"):
        be.embed(["x"])


def test_factory_routes_onnx():
    from contentcompare.config import AppConfig
    from contentcompare.llm.factory import build_clients

    cfg = AppConfig()
    cfg.llm.backend = "ollama"
    cfg.llm.embed_backend = "onnx"
    chat, embed = build_clients(cfg)
    assert isinstance(embed, LocalOnnxEmbedding)
    assert chat is not embed

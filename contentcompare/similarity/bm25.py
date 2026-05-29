"""경량 BM25 (Okapi) 구현 — 의존성 없음.

어휘 매칭(고유명사, 코드/ID, 정확한 단어)에서 임베딩을 보완한다. 코퍼스 전체를
색인한 뒤 쿼리에 대한 문서별 점수를 반환한다.
"""

from __future__ import annotations

import math
from collections import Counter


class BM25:
    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self._docs: list[Counter] = []
        self._doc_len: list[int] = []
        self._df: Counter = Counter()
        self._idf: dict[str, float] = {}
        self._avgdl: float = 0.0

    def index(self, tokenized_docs: list[list[str]]) -> None:
        """토큰화된 문서들로 색인을 (재)구축한다."""
        self._docs = [Counter(toks) for toks in tokenized_docs]
        self._doc_len = [sum(tf.values()) for tf in self._docs]
        n = len(self._docs)
        self._avgdl = (sum(self._doc_len) / n) if n else 0.0

        self._df = Counter()
        for tf in self._docs:
            self._df.update(tf.keys())

        # idf = ln(1 + (N - df + 0.5) / (df + 0.5)) — 항상 양수.
        self._idf = {
            term: math.log(1 + (n - df + 0.5) / (df + 0.5))
            for term, df in self._df.items()
        }

    def scores(self, query_tokens: list[str]) -> list[float]:
        """쿼리에 대한 문서별 BM25 점수(색인 순서)."""
        out = [0.0] * len(self._docs)
        if not self._docs or self._avgdl == 0:
            return out
        q_terms = set(query_tokens)
        for i, tf in enumerate(self._docs):
            dl = self._doc_len[i]
            score = 0.0
            for term in q_terms:
                f = tf.get(term)
                if not f:
                    continue
                idf = self._idf.get(term, 0.0)
                denom = f + self.k1 * (1 - self.b + self.b * dl / self._avgdl)
                score += idf * (f * (self.k1 + 1)) / denom
            out[i] = score
        return out

    def __len__(self) -> int:
        return len(self._docs)

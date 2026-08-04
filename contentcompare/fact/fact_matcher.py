"""F5 후보 검색 — 기준 fact 에 대응하는 대상 fact 를 찾는다.

전략과 임계값은 **F3.5 spike 실측**으로 정했다(`docs/FACT_F3_5_LIVE_REPORT.md` §6):

1. 정규화 ``entity_name`` **완전일치** — 실측 15~50% 를 공짜로 확정.
   대상 문서의 LLM 은 같은 항목을 '정격충전전압'→'정격 충전 전압' 처럼 띄어 쓰므로
   공백·기호를 지운 키로 맞춘다.
2. 나머지는 **임베딩 코사인** — BM25 가 놓치는 한↔영 매칭
   (`표준환경온도` ↔ `Standard ambient temperature`)이 어휘 매칭으로는 원리적으로
   불가능한데 임베딩은 recall@1 100% 였다.
3. **BM25 는 융합 상대가 아니라 폴백**이다. RRF 단순 융합은 실측에서 이득이 없었고
   (BM25 의 오답 1위가 희석되지 않음), 임베딩 단독이 더 정확했다. 임베딩 백엔드가
   없을 때(오프라인·테스트)만 BM25 로 순위를 매긴다.

**임계값의 한계를 알고 쓴다.** 실측에서 정답 최저 0.697 vs 오매칭 최고 0.700 으로
두 분포가 겹쳤다 — 같은 대상 fact 에 대한 두 쿼리라 점수로는 원리적으로 못 가른다.
그래서 여기서는 명백히 무관한 것만 자르고(``min_score``), 애매한 구간
(``review_score`` 미만)은 :attr:`MatchCandidate.needs_review` 로 표시해 Comparator 가
속성 대조/LLM 으로 판단하게 넘긴다. **점수는 1차 필터일 뿐 판정 근거가 아니다.**
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Optional

from ..similarity.bm25 import BM25
from ..similarity.tokenize import tokenize
from .fact_models import Fact

_NON_WORD = re.compile(r"[^0-9a-z가-힣]+")

EXACT = "exact"
EMBED = "embed"
BM25_METHOD = "bm25"
NONE = "none"
CONCEPT = "concept"


def norm_name(name: str) -> str:
    """entity_name 비교용 정규화 — 전각/공백/기호 차이를 없앤다."""
    return _NON_WORD.sub("", unicodedata.normalize("NFKC", name or "").lower())


def fact_text(fact: Fact) -> str:
    """검색에 쓰는 문자열 — ``search_text`` 가 비면 근거 원문으로 폴백."""
    return fact.search_text or fact.evidence_text or fact.entity_name


@dataclass
class MatchCandidate:
    fact: Fact
    score: float
    method: str
    needs_review: bool = False
    """코드 단독 판정을 신뢰하면 안 되는 후보. **생산자에 따라 의미가 다르다.**

    - :class:`FactMatcher`(유사도 경로): 점수가 경계 구간(``review_score`` 미만)이다.
    - :class:`ConceptMatcher`(F7 개념 경로): **연결의 확정 주체**가 LLM 이다. 즉
      아직 아무도 확인하지 않은 연결 위에서는 값 판정도 LLM 이 한 번 더 본다
      (설계 §F5 Matcher 변경 표). 코드/온톨로지 확정 연결은 ``False``.

    개념 경로에서 ``score`` 는 진단용 recall 점수일 뿐이므로 이 필드를
    ``score < review_score`` 로 되돌리면 안 된다 — 점수는 판정 근거가 아니다.
    """


class FactMatcher:
    """대상 문서 fact 를 색인해 기준 fact 의 후보를 돌려준다."""

    def __init__(
        self,
        targets: list[Fact],
        *,
        embedder: Any = None,
        top_k: int = 3,
        min_score: float = 0.65,
        review_score: float = 0.75,
        bm25_min_score: float = 3.0,
    ) -> None:
        self.targets = targets
        self.top_k = top_k
        self.min_score = min_score
        self.review_score = review_score
        self.bm25_min_score = bm25_min_score
        self.embedder = embedder

        self._by_name: dict[str, int] = {}
        for i, f in enumerate(targets):
            self._by_name.setdefault(norm_name(f.entity_name), i)

        self._bm25 = BM25()
        self._bm25.index([tokenize(fact_text(f)) for f in targets])

        self._vectors: Optional[list[list[float]]] = None
        if embedder is not None and targets:
            # 대상=본문이므로 passage 접두어(e5 계열 교차언어 검색 규약).
            self._vectors = [
                _normalize(v)
                for v in embedder.embed([fact_text(f) for f in targets], kind="passage")
            ]

    # ------------------------------------------------------------------ #
    def search(self, ref: Fact) -> list[MatchCandidate]:
        """기준 fact 의 후보를 점수 내림차순으로 반환(없으면 빈 리스트)."""
        if not self.targets:
            return []

        key = norm_name(ref.entity_name)
        if key and key in self._by_name:
            return [MatchCandidate(self.targets[self._by_name[key]], 1.0, EXACT)]

        if self._vectors is not None:
            ranked = self._embed_rank(ref)
            method, cutoff = EMBED, self.min_score
        else:
            ranked = _rank(self._bm25.scores(tokenize(fact_text(ref))), self.top_k)
            method, cutoff = BM25_METHOD, self.bm25_min_score

        out = []
        for idx, score in ranked:
            if score < cutoff:
                continue  # 명백히 무관 — missing 판정의 근거가 된다
            out.append(MatchCandidate(
                self.targets[idx], score, method,
                needs_review=score < self.review_score,
            ))
        return out

    def _embed_rank(self, ref: Fact) -> list[tuple[int, float]]:
        query = _normalize(self.embedder.embed([fact_text(ref)], kind="query")[0])
        sims = [_dot(query, vec) for vec in (self._vectors or [])]
        return _rank(sims, self.top_k)


def _rank(scores: list[float], top_k: int) -> list[tuple[int, float]]:
    return sorted(enumerate(scores), key=lambda x: x[1], reverse=True)[:top_k]


def _normalize(vec: list[float]) -> list[float]:
    norm = sum(v * v for v in vec) ** 0.5
    return [v / norm for v in vec] if norm else list(vec)


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


class ConceptMatcher:
    """개념 그래프에서 후보를 가져온다 — 유사도도, 임계값도 쓰지 않는다(F7).

    ``FactMatcher`` 는 F7 에서 **후보 쌍 생성(recall)** 전용으로 남고, 비교에 쓰는
    후보는 이 클래스가 만든다. 연결이 없으면 빈 리스트를 돌려주고, 그것이 곧
    ``missing`` 판정의 근거가 된다.
    """

    def __init__(
        self,
        graph: Any,
        reference_doc: str,
        target_doc: str,
        target_facts: list[Fact],
    ) -> None:
        self.graph = graph
        self.reference_doc = reference_doc
        self.target_doc = target_doc
        self._by_id = {f.fact_id: f for f in target_facts}

    def search(self, ref: Fact) -> list[MatchCandidate]:
        """같은 개념에 속한 대상 fact 를 **점수 내림차순**으로 반환한다.

        정렬은 :meth:`FactMatcher.search` 와 같은 규약이다 —
        :meth:`FactComparator.compare` 가 ``candidates[0]`` 을 "가장 좋은 후보"로
        취급하므로, 한 개념 노드에 같은 대상 문서 fact 가 둘 이상 들어갔을 때
        멤버 등록 순서를 그대로 돌려주면 임의의 fact 가 1위가 된다.
        """
        from .concept_models import BY_CODE, BY_ONTOLOGY, FactRef

        out: list[MatchCandidate] = []
        for member in self.graph.partners(self.reference_doc, ref.fact_id, self.target_doc):
            fact = self._by_id.get(member.fact_id)
            if fact is None:
                continue
            edge = self.graph.edge_of(
                FactRef(self.reference_doc, ref.fact_id),
                FactRef(self.target_doc, member.fact_id),
            )
            confirmed = bool(edge) and (
                edge.promoted or edge.decided_by in (BY_CODE, BY_ONTOLOGY)
            )
            out.append(MatchCandidate(
                fact=fact,
                score=edge.recall_score if edge else 0.0,
                method=CONCEPT,
                needs_review=not confirmed,
            ))
        out.sort(key=lambda c: c.score, reverse=True)  # 동점이면 멤버 순서 유지(안정 정렬)
        return out

    # ------------------------------------------------------------------ #
    def explain_missing(self, ref: Fact) -> str:
        """후보가 없을 때 사람에게 줄 사유 — **왜 비교하지 않았는지**.

        F7 에서 ``missing`` 은 대부분 "개념이 ``same_as`` 로 이어지지 않았다"이지
        "유사도가 임계에 못 미쳤다"가 아니다. 그 쌍을 막은 ``differs_by`` 엣지가
        있으면 축·사유까지 실어, 사람이 리포트만 보고도 원인을 알 수 있게 한다.
        """
        from .concept_models import DIFFERS_BY

        base = ("개념이 같다고 판정되지 않아 비교하지 않았습니다"
                "(개념 그래프에 이 항목과 이어진 대상 fact 가 없습니다).")
        blocked = []
        for edge in self.graph.edges:
            if edge.relation != DIFFERS_BY:
                continue
            other = self._counterpart(edge, ref.fact_id)
            if other is None:
                continue
            axis = f"{edge.axis} 이(가) 다름" if edge.axis else "다른 개념"
            detail = f" — {edge.reason}" if edge.reason else ""
            blocked.append(f"'{self._label(other)}'({axis}){detail}")
        if blocked:
            return f"{base} 다른 개념으로 차단된 후보: " + " / ".join(blocked)
        return base

    def _counterpart(self, edge: Any, ref_fact_id: str) -> Any:
        """``edge`` 가 (기준 fact ↔ 이 대상 문서) 쌍이면 대상 쪽 참조를 준다."""
        if (edge.left.doc == self.reference_doc and edge.left.fact_id == ref_fact_id
                and edge.right.doc == self.target_doc):
            return edge.right
        if (edge.right.doc == self.reference_doc and edge.right.fact_id == ref_fact_id
                and edge.left.doc == self.target_doc):
            return edge.left
        return None

    def _label(self, ref: Any) -> str:
        fact = self._by_id.get(ref.fact_id)
        return fact.entity_name if fact is not None else ref.fact_id

"""가짜(fake) LLM/임베딩 백엔드로 파이프라인 핵심 로직을 검증하는 스모크 테스트.

Office/Ollama 없이 임베딩 검색 → LLM 비교 → 리포트 흐름을 확인한다.
"""

from __future__ import annotations

import json

from contentcompare.comparison import Comparator
from contentcompare.models import DocItem, DocType, Verdict
from contentcompare.report import render_markdown
from contentcompare.similarity import VectorIndex


class FakeEmbedder:
    """단어 집합 기반의 결정적 임베딩(테스트용). 차원=고정 어휘."""

    VOCAB = ["매출", "영업이익", "직원수", "2023", "2024", "억원", "명"]

    def embed(self, texts, *, kind="passage"):
        vecs = []
        for t in texts:
            vecs.append([1.0 if w in t else 0.0 for w in self.VOCAB])
        return vecs


class FakeLLM:
    """후보 중 첫 번째를 매칭하고, 텍스트가 같으면 same/다르면 different 로 답하는 가짜 LLM."""

    def complete(self, system, user, *, temperature=0.0):
        # user 프롬프트에서 기준 내용과 첫 후보 내용을 대충 비교(테스트 목적).
        verdict = "same" if "동일내용" in user else "different"
        # 첫 후보 item_id 추출.
        import re

        ids = re.findall(r"item_id: (\S+)", user)
        matched = [ids[1]] if len(ids) > 1 else []
        return json.dumps(
            {"verdict": verdict, "matched_item_ids": matched, "reasoning": "테스트 사유"}
        )


def _item(item_id, text):
    return DocItem(
        item_id=item_id,
        doc_id="doc",
        doc_type=DocType.WORD,
        text=text,
        source_label=item_id,
    )


def test_vector_index_search_returns_similar():
    idx = VectorIndex(FakeEmbedder())
    idx.add(
        [
            _item("a", "2023 매출 100 억원"),
            _item("b", "직원수 500 명"),
        ]
    )
    hits = idx.search("매출 억원", top_k=2, min_score=0.1)
    assert hits
    assert hits[0].item.item_id == "a"


def test_comparator_not_found_when_no_candidates():
    cmp = Comparator(FakeLLM())
    result = cmp.compare(_item("ref", "동일내용 매출"), [])
    assert result.verdict == Verdict.NOT_FOUND


def test_report_renders():
    cmp = Comparator(FakeLLM())
    idx = VectorIndex(FakeEmbedder())
    idx.add([_item("t1", "2023 매출 억원")])
    ref = _item("ref", "동일내용 2023 매출 억원")
    cands = idx.search(ref.text, top_k=3, min_score=0.1)
    result = cmp.compare(ref, cands)
    md = render_markdown([result], reference_doc="ref.xlsx", target_docs=["t.docx"])
    assert "# 문서 비교 리포트" in md
    assert "요약" in md

"""교차언어 이중언어 보강(translate) 테스트 — 세그먼트 배치 번역."""

from __future__ import annotations

import json
import re

from contentcompare.models import DocItem, DocType, FieldClaim, RecordItem
from contentcompare.translate import BilingualAugmenter, _needs_translation, _segments

# 가짜 번역 사전(한→영). 사전에 없으면 원문 그대로 반환.
_GLOSS = {
    "충전 환경 온도": "charging ambient temperature",
    "제품명": "product name",
    "배터리 셀": "battery cell",
    "순번": "index",
    "온도 기준은 박스 표면 기준이다": "temperature is based on box surface",
}


class BatchLLM:
    """번호 매겨진 줄을 받아 JSON 배열로 번역해 주는 가짜 LLM(호출/배치 기록)."""

    def __init__(self):
        self.calls = 0
        self.batches: list[list[str]] = []

    def complete(self, system, user, *, temperature=0.0):
        self.calls += 1
        lines = [
            re.sub(r"^\s*\d+\.\s*", "", ln)
            for ln in user.strip().splitlines()
            if re.match(r"^\s*\d+\.", ln)
        ]
        self.batches.append(lines)
        return json.dumps([_GLOSS.get(ln, ln) for ln in lines], ensure_ascii=False)


def _record(text, headers, values=None, key_context=""):
    values = values or [""] * len(headers)
    fields = [
        FieldClaim(field_id=f"f{i}", header=h, value_raw=v, value_norm=v, cell_ref="A1")
        for i, (h, v) in enumerate(zip(headers, values))
    ]
    return RecordItem(
        item_id="r1", doc_id="ref.xlsx", doc_type=DocType.EXCEL,
        text=text, source_label="lbl", key_context=key_context, fields=fields,
    )


# --------------------------------------------------------------------------- #
# 언어 판별 / index_text
# --------------------------------------------------------------------------- #
def test_needs_translation_en_pivot():
    assert _needs_translation("충전 환경 온도", "en")
    assert not _needs_translation("Charging temperature", "en")


def test_needs_translation_ko_pivot():
    assert _needs_translation("Charging temperature", "ko")
    assert not _needs_translation("충전 환경 온도", "ko")


def test_index_text_fallback():
    it = DocItem("i", "d", DocType.WORD, text="원문", source_label="s")
    assert it.index_text == "원문"
    it.search_text = "원문 | translated"
    assert it.index_text == "원문 | translated"


# --------------------------------------------------------------------------- #
# 세그먼트 추출
# --------------------------------------------------------------------------- #
def test_segments_include_headers_values_and_keys():
    rec = _record("제품명=배터리 셀 | 충전 환경 온도=25",
                  ["제품명", "충전 환경 온도"], values=["배터리 셀", "25"],
                  key_context="[제품명=배터리 셀]")
    segs = _segments(rec)
    assert "충전 환경 온도" in segs   # 헤더
    assert "배터리 셀" in segs        # 텍스트 셀 값
    assert "25" in segs              # 숫자 값(추출은 되되 번역 단계에서 걸러짐)


# --------------------------------------------------------------------------- #
# 보강 — 값까지 번역
# --------------------------------------------------------------------------- #
def test_augment_translates_header_and_text_value():
    llm = BatchLLM()
    rec = _record("충전 환경 온도=배터리 셀", ["충전 환경 온도"], values=["배터리 셀"])
    BilingualAugmenter(llm, "en").augment([rec])
    # 헤더와 텍스트 값 모두 번역돼 붙는다.
    assert "charging ambient temperature" in rec.search_text
    assert "battery cell" in rec.search_text
    assert rec.search_text.startswith("충전 환경 온도=배터리 셀 | ")
    assert rec.text == "충전 환경 온도=배터리 셀"   # 원문 불변


def test_numeric_values_not_translated():
    llm = BatchLLM()
    rec = _record("순번=25", ["순번"], values=["25"])
    BilingualAugmenter(llm, "en").augment([rec])
    # 숫자 '25' 는 번역 대상에서 제외(배치에 안 들어감).
    assert all("25" not in s for batch in llm.batches for s in batch)
    assert "index" in rec.search_text  # 헤더는 번역됨


def test_batched_single_call_with_cache():
    # 헤더는 모든 행에서 동일 → 유니크 세그먼트만 1배치로 번역(호출 1회).
    llm = BatchLLM()
    rows = [_record(f"순번={i} | 충전 환경 온도=20", ["순번", "충전 환경 온도"],
                    values=[str(i), "20"]) for i in range(5)]
    BilingualAugmenter(llm, "en").augment(rows)
    assert llm.calls == 1   # 5행이지만 유니크 헤더 2개 → 한 배치 한 호출
    assert llm.batches[0] == ["순번", "충전 환경 온도"]
    for r in rows:
        assert "index" in r.search_text and "charging ambient temperature" in r.search_text


def test_batch_size_splits_calls():
    llm = BatchLLM()
    # 유니크 텍스트 값 4개를 batch_size=2 로 → 2호출.
    vals = ["배터리 셀", "제품명", "충전 환경 온도", "온도 기준은 박스 표면 기준이다"]
    rows = [_record(f"항목={v}", ["항목"], values=[v]) for v in vals]
    # 헤더 '항목'(번역사전에 없음, CJK라 번역대상) + 값 4개 = 유니크 5개 → batch2 → 3호출.
    BilingualAugmenter(llm, "en", batch_size=2).augment(rows)
    assert llm.calls == 3


def test_skip_english_items_no_calls():
    llm = BatchLLM()
    it = DocItem("i", "t.docx", DocType.WORD,
                 text="Charging ambient temperature is documented.", source_label="s")
    BilingualAugmenter(llm, "en").augment([it])
    assert it.search_text == ""
    assert llm.calls == 0


def test_cache_persists_across_augment_calls():
    llm = BatchLLM()
    aug = BilingualAugmenter(llm, "en")
    aug.augment([_record("충전 환경 온도=1", ["충전 환경 온도"], values=["1"])])
    first = llm.calls
    aug.augment([_record("충전 환경 온도=2", ["충전 환경 온도"], values=["2"])])
    assert llm.calls == first   # 두 번째는 캐시 → 추가 호출 없음


# --------------------------------------------------------------------------- #
# 안전성
# --------------------------------------------------------------------------- #
def test_batch_parse_failure_falls_back_to_individual():
    class BadThenGood:
        def __init__(self):
            self.calls = 0
        def complete(self, system, user, *, temperature=0.0):
            self.calls += 1
            # 배치(여러 줄)면 깨진 응답 → 폴백, 단건이면 평문 번역.
            if "\n" in user.strip():
                return "그냥 설명문 (JSON 아님)"
            return "charging ambient temperature"
    llm = BadThenGood()
    rec = _record("충전 환경 온도=배터리", ["충전 환경 온도"], values=["배터리"])
    BilingualAugmenter(llm, "en").augment([rec])
    # 배치 실패 후 개별 번역으로 최소한 헤더는 붙는다.
    assert "charging ambient temperature" in rec.search_text


def test_translation_failure_is_safe():
    class Boom:
        def complete(self, system, user, *, temperature=0.0):
            raise RuntimeError("down")
    rec = _record("충전 환경 온도=25", ["충전 환경 온도"], values=["25"])
    BilingualAugmenter(Boom(), "en").augment([rec])
    assert rec.search_text == ""   # 실패 시 보강 생략(검색은 원문으로)

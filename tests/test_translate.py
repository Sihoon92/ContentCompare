"""교차언어 이중언어 보강(translate) 테스트."""

from __future__ import annotations

from contentcompare.models import DocItem, DocType, FieldClaim, RecordItem
from contentcompare.translate import BilingualAugmenter, _needs_translation


class FixedLLM:
    """system 무관하게 지정 응답을 주는 가짜 번역 LLM(호출 수 기록)."""

    def __init__(self, reply="charging ambient temperature"):
        self.reply = reply
        self.calls = 0
        self.seen = []

    def complete(self, system, user, *, temperature=0.0):
        self.calls += 1
        self.seen.append(user)
        return self.reply


def _record(text, headers, key_context=""):
    fields = [
        FieldClaim(field_id=f"f{i}", header=h, value_raw="", value_norm="", cell_ref="A1")
        for i, h in enumerate(headers)
    ]
    return RecordItem(
        item_id="r1", doc_id="ref.xlsx", doc_type=DocType.EXCEL,
        text=text, source_label="lbl", key_context=key_context, fields=fields,
    )


# --------------------------------------------------------------------------- #
# 언어 판별
# --------------------------------------------------------------------------- #
def test_needs_translation_en_pivot():
    assert _needs_translation("충전 환경 온도", "en")          # 한글 → 영어 필요
    assert not _needs_translation("Charging temperature", "en")  # 이미 영어


def test_needs_translation_ko_pivot():
    assert _needs_translation("Charging temperature", "ko")    # 영어 → 한국어 필요
    assert not _needs_translation("충전 환경 온도", "ko")        # 이미 한국어


# --------------------------------------------------------------------------- #
# index_text 폴백
# --------------------------------------------------------------------------- #
def test_index_text_fallback():
    it = DocItem("i", "d", DocType.WORD, text="원문", source_label="s")
    assert it.index_text == "원문"
    it.search_text = "원문 | translated"
    assert it.index_text == "원문 | translated"


# --------------------------------------------------------------------------- #
# 보강
# --------------------------------------------------------------------------- #
def test_augment_korean_record_appends_translation():
    llm = FixedLLM("charging ambient temperature")
    rec = _record("충전 환경 온도=25", ["충전 환경 온도"])
    BilingualAugmenter(llm, "en").augment([rec])
    assert rec.search_text == "충전 환경 온도=25 | charging ambient temperature"
    assert "원문" not in rec.search_text
    # 판정용 원문은 그대로.
    assert rec.text == "충전 환경 온도=25"


def test_augment_skips_english_target():
    llm = FixedLLM("...")
    it = DocItem("i", "t.docx", DocType.WORD, text="Charging ambient temperature is 25C", source_label="s")
    BilingualAugmenter(llm, "en").augment([it])
    assert it.search_text == ""   # 이미 영어 → 번역 생략
    assert llm.calls == 0


def test_record_translates_headers_only_and_caches():
    # 헤더는 모든 행에서 반복 → 캐시로 1회만 번역(값은 번역 안 함).
    llm = FixedLLM("index, charging ambient temperature")
    rows = [_record(f"순번={i} | 충전 환경 온도={20+i}", ["순번", "충전 환경 온도"]) for i in range(5)]
    BilingualAugmenter(llm, "en").augment(rows)
    assert llm.calls == 1   # 헤더 집합이 동일 → 캐시 히트
    for r in rows:
        assert r.search_text.endswith("| index, charging ambient temperature")
    # 번역에 보낸 것은 헤더 목록뿐(값 '20' 등은 미포함).
    assert llm.seen[0] == "순번, 충전 환경 온도"


def test_key_context_labels_included():
    llm = FixedLLM("product, charging ambient temperature")
    rec = _record("제품명=A | 충전 환경 온도=25", ["충전 환경 온도"], key_context="[제품명=A]")
    BilingualAugmenter(llm, "en").augment([rec])
    # 키 라벨 '제품명'도 번역 대상 용어에 포함.
    assert llm.seen[0] == "충전 환경 온도, 제품명"


def test_noop_when_translation_equals_source():
    llm = FixedLLM("충전 환경 온도")  # 번역이 원문과 동일 → 보강 안 함
    rec = _record("충전 환경 온도=25", ["충전 환경 온도"])
    BilingualAugmenter(llm, "en").augment([rec])
    assert rec.search_text == ""


def test_translation_failure_is_safe():
    class Boom:
        def complete(self, system, user, *, temperature=0.0):
            raise RuntimeError("down")
    rec = _record("충전 환경 온도=25", ["충전 환경 온도"])
    BilingualAugmenter(Boom(), "en").augment([rec])
    assert rec.search_text == ""   # 실패 시 보강 생략(검색은 원문으로)

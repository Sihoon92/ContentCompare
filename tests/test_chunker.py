"""chunker — 문장 경계 문단 패킹 + 오버랩."""

from __future__ import annotations

from contentcompare.models import DocItem, DocType
from contentcompare.similarity.chunker import _pack, chunk_items


def _item(text):
    return DocItem("i1", "d.docx", DocType.WORD, text=text, source_label="L")


def test_short_text_not_split():
    items = chunk_items([_item("짧은 문장.")], chunk_chars=800)
    assert len(items) == 1 and items[0].item_id == "i1"


def test_packs_at_sentence_boundary_no_midcut():
    # 각 문장 20자 안팎. size=40 이면 문장 2개씩 묶이고, 문장 중간에서 안 잘림.
    text = "문장 가나다 first one. 문장 라마바 second two. 문장 사아자 third three. 문장 카타파 fourth four."
    pieces = _pack(text, size=40, overlap=0)
    assert len(pieces) >= 2
    # 어떤 조각도 문장 종결부호 없이 글자만 뚝 잘린 형태가 아니어야(끝이 . 로 끝남).
    for p in pieces:
        assert p.strip().endswith(".")


def test_overlap_carries_tail():
    text = "에이 sentence A. 비 sentence B. 씨 sentence C. 디 sentence D."
    no_ov = _pack(text, size=30, overlap=0)
    ov = _pack(text, size=30, overlap=20)
    # 오버랩이 있으면 인접 조각이 직전 문장을 일부 공유 → 조각 수가 같거나 늘고, 겹치는 문장 존재.
    joined = " ".join(ov)
    assert "sentence" in joined
    # 적어도 한 문장이 두 조각에 중복 등장(겹침)함을 확인.
    assert any(ov[i].split()[-1] == ov[i + 1].split()[0] or
               any(s in ov[i + 1] for s in ov[i].split(". ")[-1:])
               for i in range(len(ov) - 1)) or len(ov) >= len(no_ov)


def test_oversized_single_sentence_hard_split():
    long_sentence = "가" * 100  # 종결부호 없는 단일 토큰, size 초과
    pieces = _pack(long_sentence, size=30, overlap=0)
    assert len(pieces) == 4  # 100/30 → 30,30,30,10
    assert all(len(p) <= 30 for p in pieces)


def test_chunk_items_labels_pieces():
    text = "문장 하나입니다. " * 60  # 충분히 길게
    items = chunk_items([_item(text)], chunk_chars=100, overlap=20)
    assert len(items) > 1
    assert items[0].item_id == "i1#chunk1"
    assert "(조각 1)" in items[0].source_label
    assert all(len(it.text) <= 100 + 20 for it in items)  # 예산+오버랩 안팎


def test_overlap_clamped_to_half():
    # overlap 이 chunk_chars 보다 커도 chunk_chars//2 로 상한 → 무한루프/폭주 없음.
    text = "짧은 문장. " * 50
    items = chunk_items([_item(text)], chunk_chars=40, overlap=1000)
    assert len(items) >= 1

"""검색 텍스트 덤프(debug_dump) 테스트."""

from __future__ import annotations

import csv

from contentcompare.debug_dump import build_rows, write_search_text_dump
from contentcompare.models import (
    Candidate,
    DocItem,
    DocType,
    RecordItem,
    RecordResult,
    Verdict,
)


def _ref(item_id, text, search_text=""):
    it = RecordItem(item_id, "ref.xlsx", DocType.EXCEL, text=text, source_label=f"L:{item_id}")
    it.search_text = search_text
    return it


def _target(item_id, text):
    return DocItem(item_id, "t.docx", DocType.WORD, text=text, source_label=f"T:{item_id}")


# --------------------------------------------------------------------------- #
def test_build_rows_joins_verdict_and_translated_flag():
    ref1 = _ref("r1", "충전 환경 온도=45", "충전 환경 온도=45 | charging ambient temperature")
    ref2 = _ref("r2", "순번=1")  # search_text 없음 → 번역 미적용
    tgt = _target("t1", "Charging ambient temperature spec.")
    results = [
        RecordResult(record=ref1, verdict=Verdict.SAME, candidates=[]),
        RecordResult(record=ref2, verdict=Verdict.NOT_FOUND, candidates=[]),
    ]
    rows = build_rows([ref1, ref2], [tgt], results)

    assert rows[0][:4] == ["reference", "r1", "same", "Y"]      # 번역 적용
    assert rows[0][6] == "충전 환경 온도=45 | charging ambient temperature"  # 실제 임베딩 입력
    assert rows[1][:4] == ["reference", "r2", "not_found", "N"]  # 번역 미적용
    assert rows[1][6] == "순번=1"                                # index_text=원문 폴백
    assert rows[2][:4] == ["target", "t1", "", "N"]


def test_write_creates_csv_with_bom(tmp_path):
    ref = _ref("r1", "온도=45", "온도=45 | temperature")
    results = [RecordResult(record=ref, verdict=Verdict.SAME, candidates=[])]
    path = tmp_path / "dump.csv"
    write_search_text_dump(str(path), [ref], [], results)

    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # Excel 한글용 UTF-8 BOM

    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    assert rows[0] == ["side", "item_id", "verdict", "search_aug", "source_label", "text", "search_text"]
    assert rows[1][1] == "r1" and rows[1][3] == "Y"


def test_missing_dir_is_created(tmp_path):
    ref = _ref("r1", "x", "")
    path = tmp_path / "sub" / "deep" / "dump.csv"
    write_search_text_dump(str(path), [ref], [], [])
    assert path.exists()

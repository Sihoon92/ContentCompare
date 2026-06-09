"""검색 텍스트(search_text) 디버그 덤프.

교차언어 번역 보강이 제대로 됐는지 확인하기 위해, 각 항목이 **임베딩/BM25 에 실제로
넣은 문자열**(:attr:`DocItem.index_text` = 원문 + 번역)을 verdict 와 함께 CSV 로 남긴다.

'못 찾은(not_found) 행'의 search_text 를 보면 번역이 비었는지/틀렸는지 바로 추적할 수 있다.
CSV 는 Excel 에서 바로 열리도록 UTF-8 BOM 으로 저장한다.
"""

from __future__ import annotations

import csv
import logging
import os

logger = logging.getLogger(__name__)

_HEADER = ["side", "item_id", "verdict", "search_aug", "source_label", "text", "search_text"]


def build_rows(reference_items, target_items, results) -> list[list[str]]:
    """기준/대상 항목을 한 표로 모은다. 기준 항목엔 판정(verdict)을 조인."""
    verdict_by_id = {}
    for r in results or []:
        try:
            verdict_by_id[r.reference.item_id] = r.verdict.value
        except AttributeError:  # 결과 형태가 예상과 다르면 건너뜀
            continue

    rows: list[list[str]] = []
    for it in reference_items or []:
        rows.append(_row("reference", it, verdict_by_id.get(it.item_id, "")))
    for it in target_items or []:
        rows.append(_row("target", it, ""))
    return rows


def _row(side, item, verdict) -> list[str]:
    # search_text 가 채워졌고 원문과 다르면 검색용 보강(값-전용/번역 등)이 적용된 것.
    search_aug = bool(item.search_text) and item.search_text != item.text
    return [
        side,
        item.item_id,
        verdict,
        "Y" if search_aug else "N",
        item.source_label,
        item.text,
        item.index_text,  # 임베딩/BM25 에 실제로 들어간 문자열(원문 | 번역)
    ]


def write_search_text_dump(path, reference_items, target_items, results) -> str:
    """덤프 CSV 를 path 에 쓴다. 디렉터리는 없으면 만든다. 경로를 반환."""
    rows = build_rows(reference_items, target_items, results)
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(_HEADER)
        writer.writerows(rows)
    n_tr = sum(1 for r in rows if r[3] == "Y")
    logger.info("검색 텍스트 덤프 저장: %s (%d행, 번역 적용 %d행)", path, len(rows), n_tr)
    return path

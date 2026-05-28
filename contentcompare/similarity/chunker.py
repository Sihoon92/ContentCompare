"""DocItem 청킹.

임베딩 품질을 위해 너무 긴 항목을 적정 길이로 분할한다. (엑셀 row 처럼 짧은 항목은
그대로 통과.) 분할된 조각은 원본 item_id 를 보존하되 ``#chunkN`` 을 덧붙인다.
"""

from __future__ import annotations

from dataclasses import replace

from ..models import DocItem


def chunk_items(items: list[DocItem], chunk_chars: int) -> list[DocItem]:
    """각 DocItem 의 text 가 chunk_chars 를 넘으면 분할한 새 리스트를 반환한다."""
    out: list[DocItem] = []
    for item in items:
        if len(item.text) <= chunk_chars:
            out.append(item)
            continue
        for i, piece in enumerate(_split(item.text, chunk_chars), start=1):
            out.append(
                replace(
                    item,
                    item_id=f"{item.item_id}#chunk{i}",
                    text=piece,
                    source_label=f"{item.source_label} (조각 {i})",
                )
            )
    return out


def _split(text: str, size: int) -> list[str]:
    """문장 경계를 가볍게 존중하며 size 단위로 자른다."""
    # TODO: 문장/단락 경계 기반 정교한 분할 + 오버랩 추가.
    return [text[i : i + size] for i in range(0, len(text), size)]

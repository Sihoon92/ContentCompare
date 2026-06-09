"""DocItem 청킹.

너무 긴 항목을 적정 길이로 분할한다(엑셀 row 처럼 짧은 항목은 그대로 통과).

분할은 **문장 경계를 존중하는 문단 패킹**이다: 문장들을 ``chunk_chars`` 예산까지
욕심껏 채우고, 예산을 넘으면 끊되 다음 조각은 직전 문장 일부를 ``overlap`` 만큼
이어받아(겹침) 경계에 걸친 내용이 한쪽에서만 끊기지 않게 한다. 한 문장이 예산보다
길면 어쩔 수 없이 글자 단위로 자른다(폴백). 분할 조각은 원본 item_id 에 ``#chunkN`` 을 붙인다.
"""

from __future__ import annotations

import re
from dataclasses import replace

from ..models import DocItem

# 문장 종결 부호(한/영/CJK) 뒤 공백에서 끊는다.
_SENT_SPLIT = re.compile(r"(?<=[.!?。…?!])\s+")


def chunk_items(items: list[DocItem], chunk_chars: int, *, overlap: int = 0) -> list[DocItem]:
    """각 DocItem 의 text 가 chunk_chars 를 넘으면 분할한 새 리스트를 반환한다.

    overlap: 인접 조각이 겹쳐 가질 글자 수(검색 경계 손실 완화). chunk_chars//2 로 상한.
    """
    overlap = max(0, min(overlap, max(0, chunk_chars // 2)))
    out: list[DocItem] = []
    for item in items:
        if len(item.text) <= chunk_chars:
            out.append(item)
            continue
        for i, piece in enumerate(_pack(item.text, chunk_chars, overlap), start=1):
            out.append(
                replace(
                    item,
                    item_id=f"{item.item_id}#chunk{i}",
                    text=piece,
                    source_label=f"{item.source_label} (조각 {i})",
                )
            )
    return out


def _sentences(text: str) -> list[str]:
    """문단(줄바꿈)과 문장 종결부호 기준으로 문장 단위 리스트를 만든다."""
    units: list[str] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        units.extend(p.strip() for p in _SENT_SPLIT.split(line) if p.strip())
    return units


def _pack(text: str, size: int, overlap: int) -> list[str]:
    """문장들을 size 예산까지 채워 조각을 만든다(겹침 overlap)."""
    units = _sentences(text)
    if not units:  # 문장 분해 불가 → 글자 단위 폴백
        return [text[i : i + size] for i in range(0, len(text), size)]

    chunks: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for u in units:
        if len(u) > size:  # 한 문장이 예산보다 김 → 현재 조각 비우고 글자 단위로
            if cur:
                chunks.append(" ".join(cur))
                cur, cur_len = [], 0
            for j in range(0, len(u), size):
                chunks.append(u[j : j + size])
            continue
        if cur and cur_len + len(u) + 1 > size:
            chunks.append(" ".join(cur))
            cur, cur_len = _overlap_tail(cur, overlap)
        cur.append(u)
        cur_len += len(u) + 1
    if cur:
        chunks.append(" ".join(cur))
    return chunks


def _overlap_tail(units: list[str], overlap: int) -> tuple[list[str], int]:
    """직전 조각의 끝 문장들을 overlap 글자 예산만큼 이어받는다."""
    if overlap <= 0:
        return [], 0
    keep: list[str] = []
    klen = 0
    for u in reversed(units):
        if keep and klen + len(u) + 1 > overlap:
            break
        keep.insert(0, u)
        klen += len(u) + 1
    return keep, klen

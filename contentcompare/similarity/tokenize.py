"""경량 토크나이저 (의존성 없음).

한글/영문/숫자를 토큰으로 뽑고, 한글 다글자 토큰은 bigram 으로 보강해
조사·부분일치에 강하게 만든다. 추후 형태소 분석기로 교체할 수 있도록
모듈 단위 함수 하나로 단순하게 유지한다.
"""

from __future__ import annotations

import re

_WORD_RE = re.compile(r"[0-9]+(?:[.,][0-9]+)*|[a-z]+|[가-힣]+")
_HANGUL_RE = re.compile(r"[가-힣]+")


def tokenize(text: str) -> list[str]:
    """텍스트를 검색용 토큰 리스트로 변환한다(소문자화)."""
    if not text:
        return []
    tokens: list[str] = []
    for tok in _WORD_RE.findall(text.lower()):
        tokens.append(tok)
        # 한글 3글자 이상은 bigram 보강(예: "영업이익" → 영업/업이/이익).
        if len(tok) >= 3 and _HANGUL_RE.fullmatch(tok):
            tokens.extend(tok[i : i + 2] for i in range(len(tok) - 1))
    return tokens

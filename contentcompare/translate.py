"""교차언어(예: 한↔영) 검색을 위한 이중언어 보강.

기준/대상 문서의 언어가 다르면 검색이 실패한다. 두 가지 이유다:
  1. BM25(어휘 매칭)는 교차언어에서 공유 토큰이 없어 사실상 0점이다.
  2. 임베딩만으로는 짧은 기술 용어의 교차언어 정렬이 약할 수 있다.

이 모듈은 **검색용 텍스트(:attr:`DocItem.search_text`)에만** 번역본을 덧붙여
임베딩과 BM25 두 채널 모두가 공통 언어(pivot) 신호를 받게 한다. 원문 :attr:`text`
는 건드리지 않으므로 판정(LLM 비교)은 원문 기준으로 이뤄진다(오역이 근거가 되지 않음).

비용 통제:
  - 이미 pivot 언어로 보이는 항목은 번역을 건너뛴다(한↔영 휴리스틱).
  - 엑셀 레코드는 '값'이 아니라 반복되는 **헤더/키 라벨**만 번역한다.
  - 동일 문자열은 캐시해 한 번만 번역한다(헤더는 모든 행에서 반복되므로 사실상 1회).
"""

from __future__ import annotations

import logging
import re

from .models import DocItem, RecordItem

logger = logging.getLogger(__name__)

# CJK(한/중/일) 글자. pivot 언어 판별 휴리스틱에 사용.
_CJK_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")
_HANGUL_RE = re.compile(r"[가-힯]")
_LATIN_RE = re.compile(r"[A-Za-z]")

_LANG_NAME = {"en": "영어(English)", "ko": "한국어(Korean)"}


def _needs_translation(text: str, pivot: str) -> bool:
    """이미 pivot 언어로 보이면 False(번역 생략). 한↔영만 정밀 판별, 그 외엔 항상 True."""
    p = (pivot or "").lower()
    if p.startswith("en"):
        return bool(_CJK_RE.search(text))  # CJK 가 있으면 영어로 번역 필요
    if p.startswith("ko"):
        # 라틴 문자가 있고 한글이 없으면 한국어로 번역.
        return bool(_LATIN_RE.search(text)) and not _HANGUL_RE.search(text)
    return True


def _record_terms(item: RecordItem) -> str:
    """레코드에서 번역할 식별 용어(헤더 + 키 라벨)만 모은다. 값은 제외(비용·노이즈)."""
    terms: list[str] = []
    for f in item.fields:
        if f.header and f.header not in terms:
            terms.append(f.header)
    for label in re.findall(r"([^\[\],=]+)=", item.key_context or ""):
        lbl = label.strip()
        if lbl and lbl not in terms:
            terms.append(lbl)
    return ", ".join(terms)


class BilingualAugmenter:
    """LLM 번역으로 :attr:`DocItem.search_text` 에 pivot 언어 번역본을 덧붙인다."""

    def __init__(self, llm, pivot_language: str = "en") -> None:
        self.llm = llm
        self.pivot = pivot_language or "en"
        self._cache: dict[str, str] = {}
        self._calls = 0

    # ------------------------------------------------------------------ #
    def augment(self, items: list[DocItem]) -> None:
        """items 각각의 search_text 를 '원문 | 번역' 으로 채운다(필요한 것만)."""
        for it in items:
            extra = self._extra_for(it)
            if extra:
                it.search_text = f"{it.text} | {extra}"

    def _extra_for(self, item: DocItem) -> str:
        # 레코드는 반복 헤더/키 라벨만(저비용), 그 외는 본문 전체.
        if isinstance(item, RecordItem) and item.fields:
            src = _record_terms(item)
        else:
            src = item.text
        src = (src or "").strip()
        if not src or not _needs_translation(src, self.pivot):
            return ""
        return self._translate(src)

    def _translate(self, text: str) -> str:
        cached = self._cache.get(text)
        if cached is not None:
            return cached
        lang = _LANG_NAME.get(self.pivot.lower(), self.pivot)
        system = (
            f"다음 텍스트를 {lang}로 번역하세요. 번역 결과만 출력하고 설명·따옴표·코드펜스는 "
            "붙이지 마세요. 고유명사·코드·숫자·단위는 그대로 두세요."
        )
        try:
            out = (self.llm.complete(system, text) or "").strip()
            self._calls += 1
        except Exception as exc:  # noqa: BLE001 - 번역 실패는 보강 생략으로
            logger.warning("검색용 번역 실패(%r): %s", text[:40], exc)
            out = ""
        # 번역이 원문과 같거나 비면 보강 의미 없음 → 빈 값 캐시.
        if not out or out == text:
            out = ""
        self._cache[text] = out
        return out

"""교차언어(예: 한↔영) 검색을 위한 이중언어 보강.

기준/대상 문서의 언어가 다르면 검색이 약하다. BM25(어휘)는 공유 토큰이 없어 0점이고,
임베딩도 짧은 기술 용어의 교차언어 정렬이 약해 top-k 를 놓친다. 이 모듈은 **검색용
텍스트(:attr:`DocItem.search_text`)에만** 번역본을 덧붙여 임베딩과 BM25 두 채널 모두가
공통 언어(pivot) 신호를 받게 한다. 원문 :attr:`text` 는 그대로라 판정(LLM 비교)은 원문
기준으로 이뤄진다(오역이 근거가 되지 않음).

번역 단위와 비용:
  - 번역은 **세그먼트** 단위다: 엑셀은 헤더 + 키 라벨/값 + 텍스트 셀 값, 그 외는 본문 전체.
  - 숫자·코드처럼 번역이 불필요한 값은 건너뛴다(pivot 언어 휴리스틱).
  - 전 항목의 **유니크 세그먼트만 모아 배치로 번역**하고 캐시한다. 헤더처럼 반복되는
    문자열은 한 번만 번역되고, LLM 호출은 (유니크 수 / batch_size) 회로 줄어든다.
"""

from __future__ import annotations

import json
import logging
import re

from .models import DocItem, RecordItem

logger = logging.getLogger(__name__)

# CJK(한/중/일) 글자. pivot 언어 판별 휴리스틱에 사용.
_CJK_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")
_HANGUL_RE = re.compile(r"[가-힯]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_KV_RE = re.compile(r"([^\[\],=]+)=([^\[\],]+)")  # key_context 의 "라벨=값" 추출

_LANG_NAME = {"en": "영어(English)", "ko": "한국어(Korean)"}


def _needs_translation(text: str, pivot: str) -> bool:
    """이미 pivot 언어로 보이면 False(번역 생략). 한↔영만 정밀 판별, 그 외엔 항상 True."""
    p = (pivot or "").lower()
    if p.startswith("en"):
        return bool(_CJK_RE.search(text))  # CJK 가 있으면 영어로 번역 필요
    if p.startswith("ko"):
        return bool(_LATIN_RE.search(text)) and not _HANGUL_RE.search(text)
    return True


def _segments(item: DocItem) -> list[str]:
    """항목에서 번역 후보 문자열들을 뽑는다(중복 포함, 필터링은 호출측)."""
    if isinstance(item, RecordItem) and item.fields:
        segs: list[str] = []
        for f in item.fields:
            if f.header:
                segs.append(str(f.header).strip())
            val = str(f.value_norm or "").strip()
            if val:
                segs.append(val)
        for label, val in _KV_RE.findall(item.key_context or ""):
            segs.append(label.strip())
            segs.append(val.strip())
        return [s for s in segs if s]
    return [item.text.strip()] if item.text and item.text.strip() else []


class BilingualAugmenter:
    """LLM 배치 번역으로 :attr:`DocItem.search_text` 에 pivot 언어 번역본을 덧붙인다."""

    def __init__(self, llm, pivot_language: str = "en", *, batch_size: int = 40) -> None:
        self.llm = llm
        self.pivot = pivot_language or "en"
        self.batch_size = max(1, batch_size)
        self._cache: dict[str, str] = {}  # 출발문자열 → 번역(보강 불필요/실패는 "")

    # ------------------------------------------------------------------ #
    def augment(self, items: list[DocItem]) -> None:
        """items 의 search_text 를 '원문 | 번역들' 로 채운다(필요한 세그먼트만, 배치 번역)."""
        # 1) 번역이 필요한 유니크 세그먼트 수집(캐시·중복 제외).
        pending: list[str] = []
        seen: set[str] = set()
        for it in items:
            for seg in _segments(it):
                if seg in self._cache or seg in seen:
                    continue
                seen.add(seg)
                if _needs_translation(seg, self.pivot):
                    pending.append(seg)

        # 2) 배치 번역 → 캐시 채움.
        self._translate_batched(pending)

        # 3) 항목별로 번역본을 모아 부착.
        for it in items:
            extras: list[str] = []
            for seg in _segments(it):
                tr = self._cache.get(seg)
                if tr and tr not in extras:
                    extras.append(tr)
            if extras:
                it.search_text = f"{it.text} | {' '.join(extras)}"

    # ------------------------------------------------------------------ #
    def _translate_batched(self, sources: list[str]) -> None:
        for start in range(0, len(sources), self.batch_size):
            chunk = sources[start : start + self.batch_size]
            out = self._call_batch(chunk)
            if out is not None and len(out) == len(chunk):
                for src, tr in zip(chunk, out):
                    self._cache[src] = self._clean(src, tr)
            else:  # 배치 파싱 실패 → 개별 번역으로 폴백(정확성 우선).
                logger.warning("배치 번역 파싱 실패(%d건) → 개별 번역 폴백", len(chunk))
                for src in chunk:
                    self._cache[src] = self._translate_one(src)

    def _call_batch(self, chunk: list[str]) -> list[str] | None:
        """여러 문자열을 한 번의 LLM 호출로 번역. JSON 배열을 반환받아 파싱(실패 시 None)."""
        lang = _LANG_NAME.get(self.pivot.lower(), self.pivot)
        system = (
            f"다음 줄들을 각각 {lang}로 번역하세요. 각 줄은 독립 항목입니다.\n"
            "출력은 번역문만 담은 JSON 문자열 배열로, 입력과 같은 순서·같은 개수여야 합니다.\n"
            "고유명사·코드·숫자·단위는 그대로 두세요. 설명·코드펜스 금지.\n"
            '예: 입력 2개 → ["translation1","translation2"]'
        )
        user = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(chunk))
        try:
            raw = self.llm.complete(system, user)
        except Exception as exc:  # noqa: BLE001 - 호출 실패는 폴백으로
            logger.warning("배치 번역 LLM 호출 실패: %s", exc)
            return None
        return _parse_str_array(raw)

    def _translate_one(self, text: str) -> str:
        """단건 번역(배치 폴백). 실패/원문동일이면 ""(보강 생략)."""
        lang = _LANG_NAME.get(self.pivot.lower(), self.pivot)
        system = (
            f"다음 텍스트를 {lang}로 번역하세요. 번역 결과만 출력하고 설명·따옴표·코드펜스는 "
            "붙이지 마세요. 고유명사·코드·숫자·단위는 그대로 두세요."
        )
        try:
            raw = self.llm.complete(system, text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("단건 번역 실패(%r): %s", text[:40], exc)
            return ""
        return self._clean(text, raw)

    @staticmethod
    def _clean(src: str, translated) -> str:
        """번역 결과 정돈: 비었거나 원문과 같으면 보강 의미 없음 → ""."""
        out = str(translated or "").strip().strip("\"'")
        return "" if (not out or out == src) else out


def _parse_str_array(raw: str) -> list[str] | None:
    """LLM 응답에서 문자열 JSON 배열을 추출/파싱. 실패 시 None."""
    if not raw:
        return None
    for candidate in (raw, _extract_array(raw)):
        if candidate is None:
            continue
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, list):
            return [str(x) for x in data]
    return None


def _extract_array(raw: str) -> str | None:
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    return match.group(0) if match else None

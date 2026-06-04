"""도메인 지식(knowledge) 메모에서 '비교 제외' 대상 컬럼명을 추출한다.

사용자가 ``knowledge/`` 의 Markdown 에 자연어로
``순번, 중분류 CODE, 소분류 CODE 는 비교에서 제외`` 처럼 적으면, 이를 해석해
제외할 컬럼명 목록을 만든다. 추출된 목록은 :class:`~contentcompare.config.ExcelConfig`
의 ``skip_columns`` 에 합쳐져 **리더 단계에서 결정적으로 제거**된다(LLM 판정 전에
필드 자체가 사라지므로 가장 확실한 제외 보장).

LLM 이 있으면 LLM 으로 정확히 추출하고, 없거나 호출에 실패하면 규칙 기반(섹션/줄
스캔)으로 폴백한다. 제외를 암시하는 표현이 전혀 없으면 LLM 을 호출하지 않는다(비용 절약).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# 제외 의도를 암시하는 키워드(한/영). 게이트 + 규칙 기반 스캔에 공용.
_EXCLUDE_KEYWORDS = (
    "제외",
    "비교 안",
    "비교하지",
    "비교에서 빼",
    "제거",
    "무시",
    "뺀다",
    "빼고",
    "exclude",
    "skip",
    "ignore",
)

# 컬럼 나열 구분자: 쉼표/슬래시/가운뎃점/한국어 나열조사 등.
_SPLIT_RE = re.compile(r"\s*(?:,|/|·|、|및|와|과|그리고)\s*")

# 인라인 한 줄 패턴: "<나열> (는/은…)? (비교…)? (제외|무시…)"
_INLINE_RE = re.compile(
    r"^(.+?)\s*(?:은|는|이|가|을|를)?\s*"
    r"(?:비교(?:에서|\s*대상에서)?|판정에서|대조에서)?\s*"
    r"(?:" + "|".join(re.escape(k) for k in _EXCLUDE_KEYWORDS) + r")",
)

SYSTEM_PROMPT = """\
당신은 사용자의 '도메인 지식 메모'에서 **비교(대조)에서 제외할 컬럼(항목) 이름**만
정확히 골라내는 추출기입니다.

[고를 것]
- 사용자가 특정 컬럼을 비교 대상에서 빼달라고 한 경우(예: '제외', '비교 안 함',
  '무시', '제거', 'exclude', 'skip'), 그 컬럼명을 메모에 적힌 표기 그대로 추출합니다.

[고르지 말 것]
- 용어 정의, 동의어/표기 규칙, 일반 배경지식은 제외 대상이 아닙니다.
- 비교 방법에 대한 일반 설명(예: '단위가 달라도 같게 본다')도 컬럼 제외가 아닙니다.

반드시 아래 JSON 만 출력하세요(설명·마크다운·코드펜스 금지):
{"excluded_columns": ["<컬럼명>", ...]}
제외 지시가 없으면 {"excluded_columns": []} 를 출력하세요."""

_USER_TEMPLATE = """\
다음은 사용자가 작성한 도메인 지식 메모입니다.

------------------------------------------------------------
{knowledge}
------------------------------------------------------------

이 메모에서 '비교에서 제외할 컬럼 이름'만 JSON 으로 추출하세요."""


def has_exclusion_hint(text: str) -> bool:
    """제외를 암시하는 키워드가 하나라도 있으면 True(LLM 호출 여부 게이트)."""
    low = (text or "").lower()
    return any(k.lower() in low for k in _EXCLUDE_KEYWORDS)


def detect_excluded_columns(knowledge: str, llm=None) -> list[str]:
    """knowledge 텍스트에서 비교 제외 컬럼명 목록을 추출. 없으면 빈 리스트.

    1) 제외 암시 키워드가 없으면 즉시 [](LLM 미호출).
    2) llm 이 있으면 LLM 으로 추출, 실패 시 규칙 기반 폴백.
    3) llm 이 없으면 규칙 기반.
    """
    if not knowledge or not knowledge.strip():
        return []
    if not has_exclusion_hint(knowledge):
        return []

    if llm is not None:
        names = _llm_extract(knowledge, llm)
        if names is not None:
            return names
        logger.warning("제외 컬럼 LLM 추출 실패 → 규칙 기반으로 폴백")
    return _rule_based(knowledge)


# --------------------------------------------------------------------------- #
# LLM 추출
# --------------------------------------------------------------------------- #
def _llm_extract(knowledge: str, llm) -> Optional[list[str]]:
    """LLM 으로 추출. 호출/파싱 실패 시 None(폴백 신호)."""
    user = _USER_TEMPLATE.format(knowledge=knowledge.strip())
    try:
        raw = llm.complete(SYSTEM_PROMPT, user)
    except Exception as exc:  # noqa: BLE001 - 호출 실패는 폴백으로
        logger.warning("제외 컬럼 추출 LLM 호출 실패: %s", exc)
        return None

    parsed = _parse_json(raw)
    if not isinstance(parsed, dict) or "excluded_columns" not in parsed:
        logger.warning("제외 컬럼 추출 응답 파싱 실패: %r", raw)
        return None
    raw_list = parsed.get("excluded_columns") or []
    if not isinstance(raw_list, list):
        return None
    return _clean_names(str(x) for x in raw_list)


def _parse_json(raw: str) -> Optional[dict]:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        pass
    match = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return None


# --------------------------------------------------------------------------- #
# 규칙 기반 폴백
# --------------------------------------------------------------------------- #
def _rule_based(text: str) -> list[str]:
    """LLM 없이 제외 컬럼명을 best-effort 로 추출.

    (a) 제외 의미의 섹션 헤더(예: '# 비교 제외 항목') 아래의 불릿 줄들, 그리고
    (b) 한 줄 인라인 나열(예: '순번, 중분류 CODE 는 비교에서 제외')을 모두 처리한다.
    """
    names: list[str] = []
    in_exclude_section = False
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            continue

        # 섹션 헤더(# ...) 추적: 제외 의미면 이후 불릿을 수집.
        if stripped.startswith("#"):
            header = stripped.lstrip("#").strip()
            in_exclude_section = has_exclusion_hint(header)
            continue

        body = stripped.lstrip("-*•·").strip()
        # (a) 제외 섹션 안의 불릿/줄은 그 자체가 컬럼 나열. 단, 줄에 '…제외' 문구가
        #     섞여 있으면 키워드 앞부분만 나열로 본다.
        if in_exclude_section:
            names.extend(_clean_names(_SPLIT_RE.split(_names_portion(body))))
            continue
        # (b) 인라인: '<나열> ... 제외' 형태면 키워드 앞부분을 나열로 본다.
        if has_exclusion_hint(body):
            m = _INLINE_RE.match(body)
            if m:
                names.extend(_clean_names(_SPLIT_RE.split(m.group(1))))

    # 순서 유지 중복 제거.
    out: list[str] = []
    for n in names:
        if n not in out:
            out.append(n)
    return out


def _names_portion(body: str) -> str:
    """줄에 제외 키워드 문구가 섞여 있으면 키워드 앞의 '컬럼 나열'부분만 돌려준다."""
    if has_exclusion_hint(body):
        m = _INLINE_RE.match(body)
        if m:
            return m.group(1)
    return body


def _clean_names(tokens) -> list[str]:
    """토큰들을 컬럼명으로 정돈: 따옴표/괄호/꼬리 조사 제거, 빈 값·과도한 길이 제외."""
    out: list[str] = []
    for tok in tokens:
        t = str(tok).strip().strip("'\"`[]()（）「」")
        t = re.sub(r"\s*(?:은|는|이|가|을|를|도)$", "", t).strip()
        if t and len(t) <= 40 and t not in out:
            out.append(t)
    return out


# --------------------------------------------------------------------------- #
# 제외 후보 이름 → 실제 헤더 인덱스 해석 (리더가 헤더를 알 때 호출)
# --------------------------------------------------------------------------- #
_RESOLVE_SYSTEM = """\
당신은 사용자의 '비교 제외 요청 컬럼명'을 실제 엑셀 헤더에 매칭하는 전문가입니다.
제외하려는 이름(오타·부분명칭·동의어가 있을 수 있음)과 실제 헤더 목록(번호 포함)이 주어집니다.

[매칭 규칙]
- 오타로 보이면 가장 가까운 헤더로 매칭합니다(예: '중분류 CODD' → '중분류 CODE').
- 멀티헤더는 '>'로 결합돼 있습니다. 부분명칭이면 끝부분이 일치하는 헤더로 봅니다
  (예: '중분류' → '항목>중분류').
- 동의어/표기 차이도 같은 항목이면 매칭합니다(예: '코드' ↔ 'CODE').
- **명확히 일치하는 헤더가 없으면 -1** 을 주세요(엉뚱한 컬럼을 제외하지 않도록 보수적으로).

반드시 아래 JSON 만 출력하세요(설명·마크다운 금지):
{"matches": [{"request": "<요청 그대로>", "header_index": <정수, 없으면 -1>}]}"""

_RESOLVE_USER = """\
[실제 엑셀 헤더]
{headers_block}

[제외 요청]
{requests_block}

각 제외 요청을 위 실제 헤더 번호에 매칭해 JSON 으로 답하세요."""


def _norm(s) -> str:
    """공백 제거 + 소문자화(정규화 비교용)."""
    return re.sub(r"\s+", "", str(s)).lower()


def _match_deterministic(hint: str, headers: list[str]) -> Optional[int]:
    """LLM 없이 확실히 매칭되는 경우만 인덱스 반환(정규화 정확/멀티헤더 leaf)."""
    h = _norm(hint)
    if not h:
        return None
    for i, head in enumerate(headers):  # 공백/대소문자 무시 정확 일치
        if _norm(head) == h:
            return i
    for i, head in enumerate(headers):  # 멀티헤더 leaf('>' 뒤 마지막 조각)
        if _norm(str(head).split(">")[-1]) == h:
            return i
    return None


def resolve_exclusions(hints: list[str], headers: list[str], *, llm=None) -> list[int]:
    """제외 후보 이름들을 실제 헤더 인덱스로 해석한다(0-based, 정렬·중복 제거).

    1) 정규화 정확/leaf 로 확실한 것은 LLM 없이 매칭.
    2) 남은 애매한 이름(오타·동의어)은 llm 이 있으면 헤더 목록과 함께 LLM 으로 매칭.
       llm 이 없으면 미해결로 남겨 제외하지 않는다(안전).
    """
    if not hints or not headers:
        return []
    resolved: set[int] = set()
    unresolved: list[str] = []
    for hint in hints:
        idx = _match_deterministic(hint, headers)
        if idx is not None:
            resolved.add(idx)
        else:
            unresolved.append(hint)

    if unresolved and llm is not None:
        resolved |= _llm_match(unresolved, headers, llm)
    elif unresolved:
        logger.warning("제외 후보를 헤더에 매칭 못함(LLM 없음): %s", unresolved)
    return sorted(resolved)


def _llm_match(requests: list[str], headers: list[str], llm) -> set[int]:
    """애매한 제외 요청들을 LLM 으로 실제 헤더 인덱스에 매칭. 실패 시 빈 set."""
    headers_block = "\n".join(f"{i}: {h}" for i, h in enumerate(headers))
    requests_block = "\n".join(f"- {r}" for r in requests)
    user = _RESOLVE_USER.format(headers_block=headers_block, requests_block=requests_block)
    try:
        raw = llm.complete(_RESOLVE_SYSTEM, user)
    except Exception as exc:  # noqa: BLE001 - 실패는 미해결로
        logger.warning("제외 헤더 매칭 LLM 호출 실패: %s", exc)
        return set()

    parsed = _parse_json(raw)
    if not isinstance(parsed, dict):
        logger.warning("제외 헤더 매칭 응답 파싱 실패: %r", raw)
        return set()
    out: set[int] = set()
    for entry in parsed.get("matches") or []:
        if not isinstance(entry, dict):
            continue
        try:
            idx = int(entry.get("header_index"))
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(headers):
            logger.info("제외 매칭: '%s' → 헤더[%d]='%s'", entry.get("request"), idx, headers[idx])
            out.add(idx)
    return out

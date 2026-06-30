"""semantic_role 어휘 사전 — 컬럼의 *의미 역할* 표준 어휘.

양식이 달라도(하한치/Lower/Min …) **같은 표준 역할로 매핑**되어 fact 비교가 되도록
하는 핵심 장치(결정 #3: 정량 규격 중심, 확장 가능).

- :data:`SEMANTIC_ROLES`: 허용되는 표준 역할 집합.
- :data:`ROLE_SYNONYMS`: 역할 → 동의어 키워드(한/영).
- :func:`guess_role`: 헤더 문자열 → 추정 역할(코드 힌트). **LLM 의 결정을 대체하지
  않고**, 프롬프트 힌트와 검증/폴백에 쓴다.
- :func:`normalize_role`: 임의 문자열을 표준 역할로 보정(미허용 → ``unknown``).
"""

from __future__ import annotations

import re
from typing import Optional

# 표준 역할(canonical). 우선순위 순서대로 매칭한다(구체적인 것 먼저).
ENTITY_NAME = "entity_name"
ENTITY_CATEGORY = "entity_category"
ENTITY_SUBCATEGORY = "entity_subcategory"
QUANT_LOWER = "quantitative_lower_bound"
QUANT_TARGET = "quantitative_target"
QUANT_UPPER = "quantitative_upper_bound"
UNIT = "unit"
QUALITATIVE = "qualitative_spec"
METADATA = "metadata"
UNKNOWN = "unknown"

SEMANTIC_ROLES = (
    ENTITY_NAME,
    ENTITY_CATEGORY,
    ENTITY_SUBCATEGORY,
    QUANT_LOWER,
    QUANT_TARGET,
    QUANT_UPPER,
    UNIT,
    QUALITATIVE,
    METADATA,
    UNKNOWN,
)

# 역할 → 동의어. 매칭은 정규화된 헤더에 대해 '부분 포함'으로 한다.
ROLE_SYNONYMS: dict[str, list[str]] = {
    QUANT_LOWER: ["하한치", "하한값", "하한", "최소값", "최소", "min", "lower", "lsl"],
    QUANT_UPPER: ["상한치", "상한값", "상한", "최대값", "최대", "max", "upper", "usl"],
    QUANT_TARGET: ["중심치", "중심", "기준값", "기준", "표준값", "target", "nominal", "typ", "center"],
    UNIT: ["단위", "unit", "uom"],
    ENTITY_SUBCATEGORY: ["중분류", "소분류", "subcategory", "subcat"],
    ENTITY_CATEGORY: ["대분류", "category", "구분", "분류"],
    ENTITY_NAME: ["항목명", "항목", "명칭", "이름", "특성", "name", "item", "parameter", "param"],
    QUALITATIVE: ["조건", "설명", "비고", "remark", "note", "정성", "description", "특기"],
    METADATA: ["작성일", "버전", "version", "date", "작성자", "author", "순번", "번호", "no"],
}

# guess_role 매칭 우선순위(구체/정량 → 일반).
_PRIORITY = (
    QUANT_LOWER, QUANT_UPPER, QUANT_TARGET, UNIT,
    ENTITY_SUBCATEGORY, ENTITY_CATEGORY, ENTITY_NAME,
    QUALITATIVE, METADATA,
)


def guess_role(header: str) -> Optional[str]:
    """헤더 문자열 → 추정 표준 역할. 매칭 실패 시 ``None``.

    매칭 규칙:
    - 영문 동의어(min/max/...)는 **단어 경계**(``\\b``)로 매칭한다. 'nominal' 안의
      'min' 같은 부분단어 오매칭을 막기 위함.
    - 한글 동의어(하한치/단위/...)는 공백 제거 후 **부분 포함**으로 매칭한다.

    LLM 힌트/검증용 코드 추정이며 권위 있는 결정은 아니다.
    """
    h = str(header or "").lower().strip()
    if not h:
        return None
    despaced = re.sub(r"\s+", "", h)
    for role in _PRIORITY:
        for syn in ROLE_SYNONYMS[role]:
            s = syn.lower()
            if s.isascii():
                if re.search(rf"\b{re.escape(s)}\b", h):
                    return role
            elif s in despaced:
                return role
    return None


def normalize_role(role: Optional[str]) -> str:
    """임의 문자열을 표준 역할로 보정. 허용되지 않으면 ``unknown``."""
    if role in SEMANTIC_ROLES:
        return role  # type: ignore[return-value]
    return UNKNOWN

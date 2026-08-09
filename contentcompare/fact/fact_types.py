"""fact_type 어휘 + semantic_role → attribute 표준 이름 매핑 (F3).

fact 는 문서 타입이 달라도 같은 어휘로 모여야 비교된다(계획 결정 #3). 이 모듈은
그 표준 어휘를 한곳에 고정한다:

- :data:`FACT_TYPES`: 허용되는 fact 종류. 미허용 값은 :func:`normalize_fact_type`
  으로 ``descriptive`` 강등.
- :data:`ROLE_TO_ATTR`: F1 의 정량 **경계** semantic_role 을 fact ``attributes`` 의
  canonical 이름(lower_limit/target_value/upper_limit)으로 매핑. 그 외 값/정성 컬럼은
  field_name 을 이름으로 쓴다(결정 G-2 — 다중/일반 속성 무손실).
"""

from __future__ import annotations

from typing import Optional

from .semantic_roles import QUANT_LOWER, QUANT_TARGET, QUANT_UPPER

# fact_type 어휘(최소 집합에서 시작, 확장 가능 — 결정 F3-5).
FT_QUANTITATIVE = "quantitative_spec"
FT_QUALITATIVE = "qualitative_statement"
FT_DESCRIPTIVE = "descriptive"

FACT_TYPES = (FT_QUANTITATIVE, FT_QUALITATIVE, FT_DESCRIPTIVE)

# 규격 경계 attribute 의 canonical 이름(결정 F3-4).
ATTR_LOWER = "lower_limit"
ATTR_TARGET = "target_value"
ATTR_UPPER = "upper_limit"

# semantic_role(F1 어휘) → attribute canonical 이름. 규격 경계만 고정하고, 그 외 값/정성
# 컬럼은 field_name 을 이름으로 쓴다(설계 결정 G-2 — 다중/일반 속성 무손실).
ROLE_TO_ATTR: dict[str, str] = {
    QUANT_LOWER: ATTR_LOWER,
    QUANT_TARGET: ATTR_TARGET,
    QUANT_UPPER: ATTR_UPPER,
}


def normalize_fact_type(fact_type: Optional[str]) -> str:
    """허용 어휘면 그대로, 아니면 ``descriptive`` 로 강등."""
    return fact_type if fact_type in FACT_TYPES else FT_DESCRIPTIVE

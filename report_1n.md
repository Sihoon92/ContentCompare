# 문서 비교 리포트 (fact 엔진)

- 생성 시각: 2026-08-13 21:48:32
- 기준 문서: `samples/자표준문서.xlsx`
- 대상 문서: `samples/spec_en.docx`

- 총 24건 판정 — ❌ 불일치 7건, ❓ 판단보류 0건, ⚪ 대상에 없음 16건, ✅ 일치 1건
- 판정 주체: 코드 16건 / LLM 8건 (LLM 위임률 33%)

## 요약

| # | 기준 항목 | 대상 문서 | 판정 | 어긋난 속성 | 사유 |
|---|---|---|---|---|---|
| 1 | 기본사양 | spec_en.docx | ❌ 불일치 | nominal_capacity, current, voltage, charge_rate_1, charge_rate_2, charge_rate_3, charge_rate_4, min_temp, max_temp | 후보들은 기준의 quantitative_lower_bound=1150.0과 직접적으로 일치하는 속성을 찾지 못했으며, 대부분… |
| 2 | 기본사양 | spec_en.docx | ❌ 불일치 | nominal_capacity, current | 후보들은 기준 항목의 '공칭용량'과 관련된 값들을 다루고 있으나, 단위 및 측정 대상이 달라 직접적인 일치 여부를 판단하기 … |
| 3 | 기본사양 | spec_en.docx | ❌ 불일치 | nominal_capacity, current | 후보 중 일부는 기준의 수치와 일치하지만 단위가 다르거나, 기준에 명시되지 않은 정보를 포함하고 있어 불일치가 발생했습니다.… |
| 4 | 기본사양 | spec_en.docx | ❌ 불일치 | voltage, charge_rate_1, charge_rate_2, charge_rate_3, charge_rate_4 | 후보 중 fact-word-2는 전압 값이 기준과 일치하지만 다른 항목을 다루고 있으며, fact-word-4와 fact-w… |
| 5 | 기본사양 | spec_en.docx | ❌ 불일치 | current | 후보 중 fact-word-11이 기준의 quantitative_lower_bound=1150.0에 대해 직접적으로 일치하는… |
| 6 | 기본사양 | spec_en.docx | ❌ 불일치 | voltage, charge_rate_1, charge_rate_2, charge_rate_3, charge_rate_4 | 일부 후보들이 기준의 특정 값과 일치하지만, 다른 항목들은 단위나 관계 설정이 불분명하여 명확한 일치 여부를 판단하기 어렵습… |
| 7 | 충전온도범위(-5~5℃) | spec_en.docx | ❌ 불일치 | charge_temp_range_1, charge_rate_1 | 후보 fact-word-7이 기준 항목의 충전온도범위(-5~5℃)와 관련된 정보를 포함하고 있지만, 여러 온도 및 전류 범위… |
| 8 | 기본사양 | spec_en.docx | ⚪ 대상에 없음 | - | 개념이 같다고 판정되지 않아 비교하지 않았습니다(개념 그래프에 이 항목과 이어진 대상 fact 가 없습니다). 다른 개념으로… |
| 9 | 기본사양 | spec_en.docx | ⚪ 대상에 없음 | - | 개념이 같다고 판정되지 않아 비교하지 않았습니다(개념 그래프에 이 항목과 이어진 대상 fact 가 없습니다). 다른 개념으로… |
| 10 | 기본사양 | spec_en.docx | ⚪ 대상에 없음 | - | 개념이 같다고 판정되지 않아 비교하지 않았습니다(개념 그래프에 이 항목과 이어진 대상 fact 가 없습니다). 다른 개념으로… |
| 11 | 기본사양 | spec_en.docx | ⚪ 대상에 없음 | - | 개념이 같다고 판정되지 않아 비교하지 않았습니다(개념 그래프에 이 항목과 이어진 대상 fact 가 없습니다). 다른 개념으로… |
| 12 | 기본사양 | spec_en.docx | ⚪ 대상에 없음 | - | 개념이 같다고 판정되지 않아 비교하지 않았습니다(개념 그래프에 이 항목과 이어진 대상 fact 가 없습니다). 다른 개념으로… |
| 13 | 기본사양 | spec_en.docx | ⚪ 대상에 없음 | - | 개념이 같다고 판정되지 않아 비교하지 않았습니다(개념 그래프에 이 항목과 이어진 대상 fact 가 없습니다). 다른 개념으로… |
| 14 | 기본사양 | spec_en.docx | ⚪ 대상에 없음 | - | 개념이 같다고 판정되지 않아 비교하지 않았습니다(개념 그래프에 이 항목과 이어진 대상 fact 가 없습니다). |
| 15 | 기본사양 | spec_en.docx | ⚪ 대상에 없음 | - | 개념이 같다고 판정되지 않아 비교하지 않았습니다(개념 그래프에 이 항목과 이어진 대상 fact 가 없습니다). |
| 16 | 기본사양 | spec_en.docx | ⚪ 대상에 없음 | - | 개념이 같다고 판정되지 않아 비교하지 않았습니다(개념 그래프에 이 항목과 이어진 대상 fact 가 없습니다). |
| 17 | 기본사양 | spec_en.docx | ⚪ 대상에 없음 | - | 개념이 같다고 판정되지 않아 비교하지 않았습니다(개념 그래프에 이 항목과 이어진 대상 fact 가 없습니다). |
| 18 | 기본사양 | spec_en.docx | ⚪ 대상에 없음 | - | 개념이 같다고 판정되지 않아 비교하지 않았습니다(개념 그래프에 이 항목과 이어진 대상 fact 가 없습니다). |
| 19 | 기본사양 | spec_en.docx | ⚪ 대상에 없음 | - | 개념이 같다고 판정되지 않아 비교하지 않았습니다(개념 그래프에 이 항목과 이어진 대상 fact 가 없습니다). |
| 20 | 기본사양 | spec_en.docx | ⚪ 대상에 없음 | - | 개념이 같다고 판정되지 않아 비교하지 않았습니다(개념 그래프에 이 항목과 이어진 대상 fact 가 없습니다). |
| 21 | 기본사양 | spec_en.docx | ⚪ 대상에 없음 | - | 개념이 같다고 판정되지 않아 비교하지 않았습니다(개념 그래프에 이 항목과 이어진 대상 fact 가 없습니다). |
| 22 | 충전온도범위(12~15℃) | spec_en.docx | ⚪ 대상에 없음 | - | 개념이 같다고 판정되지 않아 비교하지 않았습니다(개념 그래프에 이 항목과 이어진 대상 fact 가 없습니다). |
| 23 | 충전온도범위(15~45℃) | spec_en.docx | ⚪ 대상에 없음 | - | 개념이 같다고 판정되지 않아 비교하지 않았습니다(개념 그래프에 이 항목과 이어진 대상 fact 가 없습니다). |
| 24 | 충전온도범위(5~12℃) | spec_en.docx | ✅ 일치 | charge_temp_range_2 | 후보 fact-word-7의 charge_temp_range_2가 기준의 충전온도범위(5~12℃)와 일치하므로 일치합니다. … |

## 상세

### 1. 기본사양 — ❌ 불일치 (nominal_capacity, current, voltage, charge_rate_1, charge_rate_2, charge_rate_3, charge_rate_4, min_temp, max_temp)

**대상 문서**: spec_en.docx

| | 값 | 근거 원문 | 위치 |
|---|---|---|---|
| 기준 | quantitative_lower_bound=1150.0 | “배터리승인규격 ver 4.7 SEC Req. ver.4.7” | 데이터!B3:P3 |
| spec_en.docx | nominal_capacity=1150 mAh | “['Nominal capacity', '1150', 'mAh']” | 블록 w_b012 |

**판단 근거**: 후보들은 기준의 quantitative_lower_bound=1150.0과 직접적으로 일치하는 속성을 찾지 못했으며, 대부분 다른 물리량(전압, 전류, 온도)을 다루고 있어 명확한 비교가 불가능합니다. (근거 신뢰도가 낮아 검토 대상입니다.)

**후보별 내역**

- ❌ 불일치 (nominal_capacity) · 기준의 quantitative_lower_bound=1150.0과 비교했을 때, nominal_capacity는 단위(mAh)가 다르므로 일치하지 않습니다. — “'Nominal capacity', '1150', 'mAh'”
- ❌ 불일치 (current) · 기준에는 전류에 대한 직접적인 제약 조건이 없으나, fact-word-11의 최대 방전 전류(1150 mA)와 비교했을 때 값이 다릅니다. — “'Standard charging current', '230', 'mA'”
- ❌ 불일치 (voltage) · 기준의 quantitative_lower_bound=1150.0과 직접적인 비교 대상이 아니며, 다른 속성(예: 최대/최소 전압)으로 간접적으로 관련될 수 있습니다. — “The nominal voltage is 3.85V.”
- ❌ 불일치 (current) · 기준의 quantitative_lower_bound=1150.0과 비교했을 때, 이 값은 일치하지만 속성(최대 방전 전류 vs 하한값)이 다릅니다. — “'Maximum discharging current', '1150', 'mA'”
- ❌ 불일치 (voltage) · 기준에는 명시된 quantitative_lower_bound가 전압이 아니므로 직접적인 비교는 어렵습니다. — “The rated charging voltage is 4.55V.”
- ❌ 불일치 (voltage) · 기준에는 명시된 quantitative_lower_bound가 전압이 아니므로 직접적인 비교는 어렵습니다. — “The discharge cut-off voltage shall be 3.0V.”
- ❓ 판단보류 (current) · 기준의 quantitative_lower_bound=1150.0과 비교할 수 있는 직접적인 관계나 단위 정보가 부족합니다. — “The maximum charging current shall not exceed 1.495A.”
- ❓ 판단보류 (charge_rate_1, charge_rate_2, charge_rate_3, charge_rate_4) · 기준에는 온도나 전류율에 대한 명시적인 하한값이 없으므로 비교가 불확실합니다. — “Charge temperature ranges: -5~5℃, 0.1C(4.55V) 5~12℃, 0.3C(4.55V) 12~1…”
- ❓ 판단보류 (min_temp, max_temp) · 기준에는 온도 범위에 대한 하한값이 없으므로 비교가 불확실합니다. — “['Standard ambient temperature', '21 ~ 29 (center 25)', '°C']”

<sub>판정 주체: llm · 매칭: concept 0.539</sub>

### 2. 기본사양 — ❌ 불일치 (nominal_capacity, current)

**대상 문서**: spec_en.docx

| | 값 | 근거 원문 | 위치 |
|---|---|---|---|
| 기준 | quantitative_lower_bound=1150.0 | “공칭용량” | 데이터!B4:O4 |
| spec_en.docx | nominal_capacity=1150 mAh | “['Nominal capacity', '1150', 'mAh']” | 블록 w_b012 |

**판단 근거**: 후보들은 기준 항목의 '공칭용량'과 관련된 값들을 다루고 있으나, 단위 및 측정 대상이 달라 직접적인 일치 여부를 판단하기 어렵습니다. (근거 신뢰도가 낮아 검토 대상입니다.)

**후보별 내역**

- ❌ 불일치 (nominal_capacity) · 기준의 quantitative_lower_bound=1150.0과 비교했을 때, 단위가 다르고 값이 같으므로 일치하지 않습니다. — “Nominal capacity=1150 mAh 근거 원문: ['Nominal capacity', '1150', 'mAh']” ⚠️ 인용을 원문에서 확인하지 못했습니다
- ❌ 불일치 (current) · 기준 항목에는 전류에 대한 명시적인 하한선이 없으며, 후보 값은 기준과 직접적으로 비교하기 어렵습니다. — “current=230 mA 근거 원문: ['Standard charging current', '230', 'mA']” ⚠️ 인용을 원문에서 확인하지 못했습니다
- ❌ 불일치 (current) · 기준 항목은 '공칭용량'에 대한 하한선(quantitative_lower_bound=1150.0)을 제시하고 있으나, 이 후보는 최대 방전 전류를 나타내므로 직접적인 비교가 어렵습니다. — “current=1150 mA 근거 원문: ['Maximum discharging current', '1150', 'mA']” ⚠️ 인용을 원문에서 확인하지 못했습니다

<sub>판정 주체: llm · 매칭: concept 0.539</sub>

### 3. 기본사양 — ❌ 불일치 (nominal_capacity, current)

**대상 문서**: spec_en.docx

| | 값 | 근거 원문 | 위치 |
|---|---|---|---|
| 기준 | quantitative_lower_bound=1150.0 | “정격용량” | 데이터!B5:O5 |
| spec_en.docx | nominal_capacity=1150 mAh | “['Nominal capacity', '1150', 'mAh']” | 블록 w_b012 |

**판단 근거**: 후보 중 일부는 기준의 수치와 일치하지만 단위가 다르거나, 기준에 명시되지 않은 정보를 포함하고 있어 불일치가 발생했습니다. (근거 신뢰도가 낮아 검토 대상입니다.)

**후보별 내역**

- ❌ 불일치 (nominal_capacity) · 기준의 quantitative_lower_bound(1150.0)와 후보의 nominal_capacity(1150 mAh)는 동일한 값을 나타내지만 단위가 다릅니다. — “Nominal capacity=1150 mAh ['Nominal capacity', '1150', 'mAh']” ⚠️ 인용을 원문에서 확인하지 못했습니다
- ❌ 불일치 (current) · 기준에는 전류에 대한 명시적인 하한선이 없으므로, 이 후보는 기준과 직접적으로 비교하기 어렵습니다. — “Standard Charging Current=230 mA ['Standard charging current', '230',…” ⚠️ 인용을 원문에서 확인하지 못했습니다
- ❌ 불일치 (current) · 기준의 quantitative_lower_bound(1150.0)와 후보의 current(1150 mA)는 동일한 값을 나타내지만 단위가 다릅니다. — “Maximum Discharging Current=1150 mA ['Maximum discharging current', '…” ⚠️ 인용을 원문에서 확인하지 못했습니다

<sub>판정 주체: llm · 매칭: concept 0.539</sub>

### 4. 기본사양 — ❌ 불일치 (voltage, charge_rate_1, charge_rate_2, charge_rate_3, charge_rate_4)

**대상 문서**: spec_en.docx

| | 값 | 근거 원문 | 위치 |
|---|---|---|---|
| 기준 | quantitative_lower_bound=4.55 | “정격충전전압” | 데이터!B6:O6 |
| spec_en.docx | voltage=4.55 V | “The rated charging voltage is 4.55V.” | 블록 w_b005 |

**판단 근거**: 후보 중 fact-word-2는 전압 값이 기준과 일치하지만 다른 항목을 다루고 있으며, fact-word-4와 fact-word-7은 기준의 '정격충전전압'에 대한 직접적인 정보가 아니므로 불일치하거나 불확실합니다. (근거 신뢰도가 낮아 검토 대상입니다.)

**후보별 내역**

- ❌ 불일치 (voltage) · 후보의 전압 값(4.55V)은 기준의 하한 경계값(4.55)과 일치하지만, 속성 이름이 다릅니다. — “The rated charging voltage is 4.55V.”
- ❌ 불일치 (voltage) · 후보의 공칭 전압(3.85V)은 기준 값과 다릅니다. — “The nominal voltage is 3.85V.”
- ❓ 판단보류 (charge_rate_1, charge_rate_2, charge_rate_3, charge_rate_4) · 후보의 온도 범위와 충전 속도 정보는 기준 항목에 직접적으로 일치하지 않으며, 일부 충전 속도 값은 기준과 연관성이 불분명합니다. — “Charge temperature ranges: -5~5℃, 0.1C(4.55V) 5~12℃, 0.3C(4.55V) 12~1…”

<sub>판정 주체: llm · 매칭: concept 0.608</sub>

### 5. 기본사양 — ❌ 불일치 (current)

**대상 문서**: spec_en.docx

| | 값 | 근거 원문 | 위치 |
|---|---|---|---|
| 기준 | quantitative_lower_bound=1150.0 | “최대방전전류” | 데이터!B13:O13 |
| spec_en.docx | current=1150 mA | “['Maximum discharging current', '1150', 'mA']” | 블록 w_b012 |

**판단 근거**: 후보 중 fact-word-11이 기준의 quantitative_lower_bound=1150.0에 대해 직접적으로 일치하는 값을 제공하므로 match로 판단됩니다. (근거 신뢰도가 낮아 검토 대상입니다.)

<sub>판정 주체: llm · 매칭: concept 0.489</sub>

### 6. 기본사양 — ❌ 불일치 (voltage, charge_rate_1, charge_rate_2, charge_rate_3, charge_rate_4)

**대상 문서**: spec_en.docx

| | 값 | 근거 원문 | 위치 |
|---|---|---|---|
| 기준 | quantitative_lower_bound=3.0 | “방전종지전압” | 데이터!B14:O14 |
| spec_en.docx | voltage=3.85 V | “The nominal voltage is 3.85V.” | 블록 w_b007 |

**판단 근거**: 일부 후보들이 기준의 특정 값과 일치하지만, 다른 항목들은 단위나 관계 설정이 불분명하여 명확한 일치 여부를 판단하기 어렵습니다. (근거 신뢰도가 낮아 검토 대상입니다.)

**후보별 내역**

- ✅ 일치 (voltage) · 후보의 전압 값은 기준의 하한값과 일치합니다. — “The discharge cut-off voltage shall be 3.0V.”
- ❌ 불일치 (voltage) · 후보의 공칭 전압 값은 기준의 하한값과 일치하지 않습니다. — “The nominal voltage is 3.85V.”
- ❓ 판단보류 (voltage) · 후보의 전압 값은 기준과 직접적인 비교가 불분명합니다. — “The rated charging voltage is 4.55V.”
- ❓ 판단보류 (charge_rate_1, charge_rate_2, charge_rate_3, charge_rate_4) · 후보의 온도 범위와 속도 정보는 기준과 직접적으로 일치하지 않아 비교가 어렵습니다. — “Charge temperature ranges: -5~5℃, 0.1C(4.55V) 5~12℃, 0.3C(4.55V) 12~1…”

<sub>판정 주체: llm · 매칭: concept 0.555</sub>

### 7. 충전온도범위(-5~5℃) — ❌ 불일치 (charge_temp_range_1, charge_rate_1)

**대상 문서**: spec_en.docx

| | 값 | 근거 원문 | 위치 |
|---|---|---|---|
| 기준 | quantitative_lower_bound=-5.0 ℃, upper_limit=5.0 ℃ | “충전전류 0.1C, 충전전압 4.55V” | 데이터!F23:J23 |
| spec_en.docx | charge_temp_range_1=-5~5 ℃, charge_rate_1=0.1C, charge_temp_range_2=5~12 ℃, charge_rate_2=0.3C, charge_temp_range_3=12~15 ℃, charge_rate_3=0.7C, charge_temp_range_4=15~45 ℃, charge_rate_4=1.2C | “Charge temperature ranges: -5~5℃, 0.1C(4.55V) 5~12℃, 0.3C(4…” | 블록 w_b010 |

**판단 근거**: 후보 fact-word-7이 기준 항목의 충전온도범위(-5~5℃)와 관련된 정보를 포함하고 있지만, 여러 온도 및 전류 범위가 제시되어 기준과 정확히 일치하지 않아 불일치가 발생합니다. (근거 신뢰도가 낮아 검토 대상입니다.)

<sub>판정 주체: llm · 매칭: concept 0.745</sub>

### 8. 기본사양 — ⚪ 대상에 없음

**대상 문서**: spec_en.docx

| | 값 | 근거 원문 | 위치 |
|---|---|---|---|
| 기준 | quantitative_lower_bound=3.89 | “공칭전압” | 데이터!B7:O7 |
| spec_en.docx | - | (대응 내용 없음) | - |

**판단 근거**: 개념이 같다고 판정되지 않아 비교하지 않았습니다(개념 그래프에 이 항목과 이어진 대상 fact 가 없습니다). 다른 개념으로 차단된 후보: 'Nominal Voltage'(물리량 이(가) 다름) — 공칭전압과 Nominal Voltage는 서로 다른 물리량을 나타냅니다. / 'Nominal Capacity'(물리량 이(가) 다름) — 공칭전압과 Nominal Capacity는 서로 다른 물리량을 나타냅니다. / 'Standard Charging Current'(물리량 이(가) 다름) — 공칭전압과 Standard Charging Current는 서로 다른 물리량을 나타냅니다.

<sub>판정 주체: code · 매칭: none 0.000</sub>

### 9. 기본사양 — ⚪ 대상에 없음

**대상 문서**: spec_en.docx

| | 값 | 근거 원문 | 위치 |
|---|---|---|---|
| 기준 | - | “deltaOCV” | 데이터!B8:O8 |
| spec_en.docx | - | (대응 내용 없음) | - |

**판단 근거**: 개념이 같다고 판정되지 않아 비교하지 않았습니다(개념 그래프에 이 항목과 이어진 대상 fact 가 없습니다). 다른 개념으로 차단된 후보: 'Nominal Capacity'(물리량 이(가) 다름) — deltaOCV와 Nominal Capacity는 서로 다른 물리량을 나타냅니다. / 'Nominal Voltage'(물리량 이(가) 다름) — deltaOCV와 Nominal Voltage는 서로 다른 물리량을 나타냅니다. / 'Standard Charging Current'(물리량 이(가) 다름) — deltaOCV와 Standard Charging Current는 서로 다른 물리량을 나타냅니다. / 'Specification Conformance'(문서버전 이(가) 다름) — deltaOCV와 Specification Conformance는 서로 다른 항목을 나타냅니다.

<sub>판정 주체: code · 매칭: none 0.000</sub>

### 10. 기본사양 — ⚪ 대상에 없음

**대상 문서**: spec_en.docx

| | 값 | 근거 원문 | 위치 |
|---|---|---|---|
| 기준 | - | “충전방법” | 데이터!B9:O9 |
| spec_en.docx | - | (대응 내용 없음) | - |

**판단 근거**: 개념이 같다고 판정되지 않아 비교하지 않았습니다(개념 그래프에 이 항목과 이어진 대상 fact 가 없습니다). 다른 개념으로 차단된 후보: 'Nominal Capacity'(충전방법 이(가) 다름) — 충전방법과 Nominal Capacity는 서로 다른 항목을 나타냅니다. / 'Nominal Voltage'(물리량 이(가) 다름) — 충전방법과 Nominal Voltage는 서로 다른 물리량을 나타냅니다. / 'Standard Charging Current'(물리량 이(가) 다름) — 충전방법과 Standard Charging Current는 서로 다른 물리량을 나타냅니다. / 'Specification Conformance'(문서버전 이(가) 다름) — 충전방법과 Specification Conformance는 서로 다른 항목을 나타냅니다. / 'Rated Capacity'(물리량 이(가) 다름) — 충전방법과 Rated Capacity는 서로 다른 물리량을 나타냅니다.

<sub>판정 주체: code · 매칭: none 0.000</sub>

### 11. 기본사양 — ⚪ 대상에 없음

**대상 문서**: spec_en.docx

| | 값 | 근거 원문 | 위치 |
|---|---|---|---|
| 기준 | - | “충전방법(SOC28->SOC64)” | 데이터!B10:O10 |
| spec_en.docx | - | (대응 내용 없음) | - |

**판단 근거**: 개념이 같다고 판정되지 않아 비교하지 않았습니다(개념 그래프에 이 항목과 이어진 대상 fact 가 없습니다). 다른 개념으로 차단된 후보: 'Nominal Capacity'(물리량 이(가) 다름) — 충전방법(SOC28->SOC64)과 Nominal Capacity는 서로 다른 물리량을 나타냅니다. / 'Nominal Voltage'(물리량 이(가) 다름) — 충전방법(SOC28->SOC64)과 Nominal Voltage는 서로 다른 물리량을 나타냅니다. / 'Standard Charging Current'(물리량 이(가) 다름) — 충전방법(SOC28->SOC64)과 Standard Charging Current는 서로 다른 물리량을 나타냅니다. / 'Specification Conformance'(문서버전 이(가) 다름) — 충전방법(SOC28->SOC64)과 Specification Conformance는 서로 다른 항목을 나타냅니다. / 'Rated Capacity'(물리량 이(가) 다름) — 충전방법(SOC28->SOC64)과 Rated Capacity는 서로 다른 물리량을 나타냅니다.

<sub>판정 주체: code · 매칭: none 0.000</sub>

### 12. 기본사양 — ⚪ 대상에 없음

**대상 문서**: spec_en.docx

| | 값 | 근거 원문 | 위치 |
|---|---|---|---|
| 기준 | quantitative_lower_bound=230.0 | “표준충전전류” | 데이터!B11:O11 |
| spec_en.docx | - | (대응 내용 없음) | - |

**판단 근거**: 개념이 같다고 판정되지 않아 비교하지 않았습니다(개념 그래프에 이 항목과 이어진 대상 fact 가 없습니다). 다른 개념으로 차단된 후보: 'Nominal Capacity'(측정대상 이(가) 다름) — 왼쪽은 전류에 대한 정보이고 오른쪽은 용량에 대한 정보입니다. / 'Nominal Voltage'(측정대상 이(가) 다름) — 왼쪽은 전류에 대한 정보이고 오른쪽은 전압에 대한 정보입니다. / 'Specification Conformance'(문서버전 이(가) 다름) — 왼쪽은 전류에 대한 정보이고 오른쪽은 버전 준수에 대한 정보입니다. / 'Discharge Cut-off Voltage'(측정대상 이(가) 다름) — 왼쪽은 전류에 대한 정보이고 오른쪽은 전압에 대한 정보입니다.

<sub>판정 주체: code · 매칭: none 0.000</sub>

### 13. 기본사양 — ⚪ 대상에 없음

**대상 문서**: spec_en.docx

| | 값 | 근거 원문 | 위치 |
|---|---|---|---|
| 기준 | quantitative_lower_bound=1495.0 | “최대충전전류” | 데이터!B12:O12 |
| spec_en.docx | - | (대응 내용 없음) | - |

**판단 근거**: 개념이 같다고 판정되지 않아 비교하지 않았습니다(개념 그래프에 이 항목과 이어진 대상 fact 가 없습니다). 다른 개념으로 차단된 후보: 'Nominal Capacity'(측정대상 이(가) 다름) — 왼쪽은 전류에 대한 정보이고 오른쪽은 용량에 대한 정보입니다. / 'Nominal Voltage'(측정대상 이(가) 다름) — 왼쪽은 전류에 대한 정보이고 오른쪽은 전압에 대한 정보입니다. / 'Rated Charging Voltage'(측정대상 이(가) 다름) — 왼쪽은 전류에 대한 정보이고 오른쪽은 전압에 대한 정보입니다.

<sub>판정 주체: code · 매칭: none 0.000</sub>

### 14. 기본사양 — ⚪ 대상에 없음

**대상 문서**: spec_en.docx

| | 값 | 근거 원문 | 위치 |
|---|---|---|---|
| 기준 | quantitative_lower_bound=25.0 | - | 데이터!F15 |
| spec_en.docx | - | (대응 내용 없음) | - |

**판단 근거**: 개념이 같다고 판정되지 않아 비교하지 않았습니다(개념 그래프에 이 항목과 이어진 대상 fact 가 없습니다).

<sub>판정 주체: code · 매칭: none 0.000</sub>

### 15. 기본사양 — ⚪ 대상에 없음

**대상 문서**: spec_en.docx

| | 값 | 근거 원문 | 위치 |
|---|---|---|---|
| 기준 | quantitative_lower_bound=43.0 | - | 데이터!F16 |
| spec_en.docx | - | (대응 내용 없음) | - |

**판단 근거**: 개념이 같다고 판정되지 않아 비교하지 않았습니다(개념 그래프에 이 항목과 이어진 대상 fact 가 없습니다).

<sub>판정 주체: code · 매칭: none 0.000</sub>

### 16. 기본사양 — ⚪ 대상에 없음

**대상 문서**: spec_en.docx

| | 값 | 근거 원문 | 위치 |
|---|---|---|---|
| 기준 | quantitative_lower_bound=85.0 | - | 데이터!F17 |
| spec_en.docx | - | (대응 내용 없음) | - |

**판단 근거**: 개념이 같다고 판정되지 않아 비교하지 않았습니다(개념 그래프에 이 항목과 이어진 대상 fact 가 없습니다).

<sub>판정 주체: code · 매칭: none 0.000</sub>

### 17. 기본사양 — ⚪ 대상에 없음

**대상 문서**: spec_en.docx

| | 값 | 근거 원문 | 위치 |
|---|---|---|---|
| 기준 | quantitative_lower_bound=90.0 | - | 데이터!F18 |
| spec_en.docx | - | (대응 내용 없음) | - |

**판단 근거**: 개념이 같다고 판정되지 않아 비교하지 않았습니다(개념 그래프에 이 항목과 이어진 대상 fact 가 없습니다).

<sub>판정 주체: code · 매칭: none 0.000</sub>

### 18. 기본사양 — ⚪ 대상에 없음

**대상 문서**: spec_en.docx

| | 값 | 근거 원문 | 위치 |
|---|---|---|---|
| 기준 | quantitative_lower_bound=24.0 | - | 데이터!F19 |
| spec_en.docx | - | (대응 내용 없음) | - |

**판단 근거**: 개념이 같다고 판정되지 않아 비교하지 않았습니다(개념 그래프에 이 항목과 이어진 대상 fact 가 없습니다).

<sub>판정 주체: code · 매칭: none 0.000</sub>

### 19. 기본사양 — ⚪ 대상에 없음

**대상 문서**: spec_en.docx

| | 값 | 근거 원문 | 위치 |
|---|---|---|---|
| 기준 | - | - | 데이터!F20 |
| spec_en.docx | - | (대응 내용 없음) | - |

**판단 근거**: 개념이 같다고 판정되지 않아 비교하지 않았습니다(개념 그래프에 이 항목과 이어진 대상 fact 가 없습니다).

<sub>판정 주체: code · 매칭: none 0.000</sub>

### 20. 기본사양 — ⚪ 대상에 없음

**대상 문서**: spec_en.docx

| | 값 | 근거 원문 | 위치 |
|---|---|---|---|
| 기준 | - | - | 데이터!F21 |
| spec_en.docx | - | (대응 내용 없음) | - |

**판단 근거**: 개념이 같다고 판정되지 않아 비교하지 않았습니다(개념 그래프에 이 항목과 이어진 대상 fact 가 없습니다).

<sub>판정 주체: code · 매칭: none 0.000</sub>

### 21. 기본사양 — ⚪ 대상에 없음

**대상 문서**: spec_en.docx

| | 값 | 근거 원문 | 위치 |
|---|---|---|---|
| 기준 | - | - | 데이터!F22 |
| spec_en.docx | - | (대응 내용 없음) | - |

**판단 근거**: 개념이 같다고 판정되지 않아 비교하지 않았습니다(개념 그래프에 이 항목과 이어진 대상 fact 가 없습니다).

<sub>판정 주체: code · 매칭: none 0.000</sub>

### 22. 충전온도범위(12~15℃) — ⚪ 대상에 없음

**대상 문서**: spec_en.docx

| | 값 | 근거 원문 | 위치 |
|---|---|---|---|
| 기준 | quantitative_lower_bound=12.0 ℃, upper_limit=15.0 ℃ | “충전전류 0.7C, 충전전압 4.55V” | 데이터!F25:J25 |
| spec_en.docx | - | (대응 내용 없음) | - |

**판단 근거**: 개념이 같다고 판정되지 않아 비교하지 않았습니다(개념 그래프에 이 항목과 이어진 대상 fact 가 없습니다).

<sub>판정 주체: code · 매칭: none 0.000</sub>

### 23. 충전온도범위(15~45℃) — ⚪ 대상에 없음

**대상 문서**: spec_en.docx

| | 값 | 근거 원문 | 위치 |
|---|---|---|---|
| 기준 | quantitative_lower_bound=15.0 ℃, upper_limit=45.0 ℃ | “충전전류 1.2C, 충전전압 4.20V” | 데이터!F26:J26 |
| spec_en.docx | - | (대응 내용 없음) | - |

**판단 근거**: 개념이 같다고 판정되지 않아 비교하지 않았습니다(개념 그래프에 이 항목과 이어진 대상 fact 가 없습니다).

<sub>판정 주체: code · 매칭: none 0.000</sub>

### 24. 충전온도범위(5~12℃) — ✅ 일치 (charge_temp_range_2)

**대상 문서**: spec_en.docx

| | 값 | 근거 원문 | 위치 |
|---|---|---|---|
| 기준 | quantitative_lower_bound=5.0 ℃, upper_limit=12.0 ℃ | “충전전류 0.3C, 충전전압 4.55V” | 데이터!F24:J24 |
| spec_en.docx | charge_temp_range_1=-5~5 ℃, charge_rate_1=0.1C, charge_temp_range_2=5~12 ℃, charge_rate_2=0.3C, charge_temp_range_3=12~15 ℃, charge_rate_3=0.7C, charge_temp_range_4=15~45 ℃, charge_rate_4=1.2C | “Charge temperature ranges: -5~5℃, 0.1C(4.55V) 5~12℃, 0.3C(4…” | 블록 w_b010 |

**판단 근거**: 후보 fact-word-7의 charge_temp_range_2가 기준의 충전온도범위(5~12℃)와 일치하므로 일치합니다. (근거 신뢰도가 낮아 검토 대상입니다.)

<sub>판정 주체: llm · 매칭: concept 0.770</sub>

## 실행 정보

- comparisons: 24
- decided_by_llm: 8
- llm_calls: 8
- llm_failures: 0
- multi_candidate_comparisons: 8
- multi_candidate_overridden: 8
- quote_unverified: 6
- dropped_findings: 0
- concept: {'same_as': 26, 'differs_by': 24, 'unknown': 70, 'rejected_evidence': 17, 'rejected_differs_by': 3, 'pairs_considered': 120, 'pairs_from_ontology': 0, 'pairs_by_code': 0, 'pairs_by_llm': 120, 'llm_calls': 6, 'budget_exhausted_pairs': 0}
- fast_path_rate: 0.0
- secondary_review_rate: 1.0
- unsafe_match_rate: 1.0
- enforce_new_llm_count: 0
- enforce_new_llm_rate: 0.0
- review_reasons: {'code_missing': 16, 'code_unknown': 2, 'low_confidence': 8, 'partial_attribute_coverage': 2, 'duplicate_entity_facts': 8}
- mean_attribute_coverage: 0.75
- result_changed_count: 8
- code_overridden_count: 6


## ⚠ 검토 필요 — 같은 항목인지 판정하지 못한 쌍

확인 후 `knowledge/ontology.yaml` 에 `same_as` 또는 `differs_by` 로 적어두면
다음 실행부터 이 쌍은 다시 묻지 않습니다.

| 기준 항목 | 대상 항목 | 거부 사유 | 사유 |
|---|---|---|---|
| 기본사양 | Standard Charging Current | - | LLM 응답에 이 쌍이 없었습니다 |
| 기본사양 | Nominal Voltage | - | LLM 응답에 이 쌍이 없었습니다 |
| 기본사양 | Specification Conformance | - | LLM 응답에 이 쌍이 없었습니다 |
| 기본사양 | Nominal Voltage | - | LLM 응답에 이 쌍이 없었습니다 |
| 기본사양 | Specification Conformance | - | LLM 응답에 이 쌍이 없었습니다 |
| 기본사양 | Nominal Capacity | - | LLM 응답에 이 쌍이 없었습니다 |
| 기본사양 | Standard Charging Current | - | LLM 응답에 이 쌍이 없었습니다 |
| 기본사양 | Nominal Voltage | - | LLM 응답에 이 쌍이 없었습니다 |
| 기본사양 | Maximum Discharging Current | - | LLM 응답에 이 쌍이 없었습니다 |
| 기본사양 | Maximum Charging Current | - | LLM 응답에 이 쌍이 없었습니다 |
| 기본사양 | Specification Conformance | - | LLM 응답에 이 쌍이 없었습니다 |
| 기본사양 | Rated Charging Voltage | - | LLM 응답에 이 쌍이 없었습니다 |
| 기본사양 | Rated Capacity | - | LLM 응답에 이 쌍이 없었습니다 |
| 기본사양 | Standard Charging Current | differs_by 제약 위반 | [거부됨: differs_by 제약 위반] 거부된 주장: 두 항목은 동일한 값(230 mA)을 나타냅니다. |
| 기본사양 | Specification Conformance | differs_by 제약 위반 | [거부됨: differs_by 제약 위반] 거부된 주장: 두 항목은 동일한 버전 준수 정보를 나타냅니다. |
| 기본사양 | Standard Charging Current | differs_by 제약 위반 | [거부됨: differs_by 제약 위반] 거부된 주장: 두 항목은 동일한 전류 값(230 mA)을 나타냅니다. |
| 기본사양 | Maximum Discharging Current | - | LLM 응답에 이 쌍이 없었습니다 |
| 기본사양 | Specification Conformance | - | LLM 응답에 이 쌍이 없었습니다 |
| 기본사양 | Standard Charging Current | 근거 인용이 원문에 없음 | [거부됨: 근거 인용이 원문에 없음] 거부된 주장: 두 항목은 동일한 값(표준 충전 전류)을 나타냅니다. |
| 기본사양 | Nominal Capacity | 근거 인용이 원문에 없음 | [거부됨: 근거 인용이 원문에 없음] 거부된 주장: 두 항목은 동일한 값(공칭 용량)을 나타냅니다. |
| 기본사양 | Specification Conformance | - | LLM 응답에 이 쌍이 없었습니다 |
| 기본사양 | Nominal Voltage | - | LLM 응답에 이 쌍이 없었습니다 |
| 기본사양 | Rated Charging Voltage | - | LLM 응답에 이 쌍이 없었습니다 |
| 기본사양 | Specification Conformance | 근거 인용이 원문에 없음 | [거부됨: 근거 인용이 원문에 없음] 거부된 주장: 두 항목은 동일한 규격 준수 정보를 나타냅니다. |
| 기본사양 | Discharge Cut-off Voltage | - | LLM 응답에 이 쌍이 없었습니다 |
| 기본사양 | Standard Charging Current | - | LLM 응답에 이 쌍이 없었습니다 |
| 기본사양 | Nominal Voltage | - | LLM 응답에 이 쌍이 없었습니다 |
| 기본사양 | Nominal Capacity | - | LLM 응답에 이 쌍이 없었습니다 |
| 기본사양 | Specification Conformance | 근거 인용이 원문에 없음 | [거부됨: 근거 인용이 원문에 없음] 거부된 주장: 두 항목은 동일한 규격 준수 정보를 나타냅니다. |
| 기본사양 | Nominal Voltage | - | LLM 응답에 이 쌍이 없었습니다 |
| 기본사양 | Maximum Charging Current | - | LLM 응답에 이 쌍이 없었습니다 |
| 기본사양 | Nominal Capacity | - | LLM 응답에 이 쌍이 없었습니다 |
| 기본사양 | Standard Charging Current | - | LLM 응답에 이 쌍이 없었습니다 |
| 기본사양 | Standard Charging Current | 근거 인용이 원문에 없음 | [거부됨: 근거 인용이 원문에 없음] 거부된 주장: 두 항목은 동일한 값(표준 충전 전류)을 나타냅니다. |
| 기본사양 | Specification Conformance | - | LLM 응답에 이 쌍이 없었습니다 |
| 기본사양 | Nominal Capacity | 근거 인용이 원문에 없음 | [거부됨: 근거 인용이 원문에 없음] 거부된 주장: 두 항목은 동일한 값(공칭 용량)을 나타냅니다. |
| 기본사양 | Nominal Voltage | - | LLM 응답에 이 쌍이 없었습니다 |
| 기본사양 | Maximum Charging Current | - | LLM 응답에 이 쌍이 없었습니다 |
| 기본사양 | Specification Conformance | 근거 인용이 원문에 없음 | [거부됨: 근거 인용이 원문에 없음] 거부된 주장: 두 항목은 동일한 규격 버전을 참조하고 있습니다. |
| 기본사양 | Standard Charging Current | - | LLM 응답에 이 쌍이 없었습니다 |
| 기본사양 | Nominal Capacity | 근거 인용이 원문에 없음 | [거부됨: 근거 인용이 원문에 없음] 거부된 주장: 두 항목은 동일한 충전 전류 값을 참조하고 있습니다. |
| 기본사양 | Standard Ambient Temperature Range | - | LLM 응답에 이 쌍이 없었습니다 |
| 기본사양 | Nominal Voltage | - | LLM 응답에 이 쌍이 없었습니다 |
| 기본사양 | Nominal Capacity | 근거 인용이 원문에 없음 | [거부됨: 근거 인용이 원문에 없음] 거부된 주장: 두 항목은 동일한 공칭 용량 값을 참조하고 있습니다. |
| 기본사양 | Nominal Voltage | - | LLM 응답에 이 쌍이 없었습니다 |
| 기본사양 | Standard Charging Current | - | LLM 응답에 이 쌍이 없었습니다 |
| 기본사양 | Specification Conformance | - | LLM 응답에 이 쌍이 없었습니다 |
| 기본사양 | Rated Capacity | - | LLM 응답에 이 쌍이 없었습니다 |
| 기본사양 | Nominal Capacity | - | LLM 응답에 이 쌍이 없었습니다 |
| 기본사양 | Nominal Voltage | 근거 인용이 원문에 없음 | [거부됨: 근거 인용이 원문에 없음] 거부된 주장: 두 항목은 동일한 공칭 전압 값을 참조하고 있습니다. |
| 기본사양 | Standard Charging Current | 근거 인용이 원문에 없음 | [거부됨: 근거 인용이 원문에 없음] 거부된 주장: 두 항목은 동일한 표준 충전 전류 값을 참조하고 있습니다. |
| 기본사양 | Specification Conformance | 근거 인용이 원문에 없음 | [거부됨: 근거 인용이 원문에 없음] 거부된 주장: 두 항목은 동일한 규격 버전을 참조하고 있습니다. |
| 기본사양 | Rated Capacity | 근거 인용이 원문에 없음 | [거부됨: 근거 인용이 원문에 없음] 거부된 주장: 두 항목은 동일한 정격 용량 값을 참조하고 있습니다. |
| 기본사양 | Nominal Capacity | 근거 인용이 원문에 없음 | [거부됨: 근거 인용이 원문에 없음] 거부된 주장: 두 항목은 동일한 공칭 용량 값을 참조하고 있습니다. |
| 기본사양 | Nominal Voltage | 근거 인용이 원문에 없음 | [거부됨: 근거 인용이 원문에 없음] 거부된 주장: 두 항목은 동일한 공칭 전압 값을 참조하고 있습니다. |
| 기본사양 | Standard Charging Current | 근거 인용이 원문에 없음 | [거부됨: 근거 인용이 원문에 없음] 거부된 주장: 두 항목은 동일한 표준 충전 전류 값을 참조하고 있습니다. |
| 기본사양 | Specification Conformance | - | LLM 응답에 이 쌍이 없었습니다 |
| 기본사양 | Rated Capacity | 근거 인용이 원문에 없음 | [거부됨: 근거 인용이 원문에 없음] 거부된 주장: 두 항목은 동일한 정격 용량 값을 참조하고 있습니다. |
| 충전온도범위(-5~5℃) | Maximum Charging Current | - | LLM 응답에 이 쌍이 없었습니다 |
| 충전온도범위(5~12℃) | Rated Charging Voltage | - | LLM 응답에 이 쌍이 없었습니다 |
| 충전온도범위(12~15℃) | Charge Temperature Ranges and Rates | - | LLM 응답에 이 쌍이 없었습니다 |
| 충전온도범위(12~15℃) | Standard Ambient Temperature Range | - | LLM 응답에 이 쌍이 없었습니다 |
| 충전온도범위(12~15℃) | Rated Charging Voltage | - | LLM 응답에 이 쌍이 없었습니다 |
| 충전온도범위(12~15℃) | Nominal Capacity | - | LLM 응답에 이 쌍이 없었습니다 |
| 충전온도범위(12~15℃) | Nominal Voltage | - | LLM 응답에 이 쌍이 없었습니다 |
| 충전온도범위(15~45℃) | Charge Temperature Ranges and Rates | - | LLM 응답에 이 쌍이 없었습니다 |
| 충전온도범위(15~45℃) | Rated Charging Voltage | - | LLM 응답에 이 쌍이 없었습니다 |
| 충전온도범위(15~45℃) | Standard Ambient Temperature Range | - | LLM 응답에 이 쌍이 없었습니다 |
| 충전온도범위(15~45℃) | Maximum Charging Current | - | LLM 응답에 이 쌍이 없었습니다 |
| 충전온도범위(15~45℃) | Nominal Voltage | - | LLM 응답에 이 쌍이 없었습니다 |
# 골든셋 (사람 라벨 정답)

fact 파이프라인의 라이브 검증(F3.5) · 매칭 spike(F5) · 엔진 벤치마크(F6)가 공통으로 쓰는 정답 데이터.

## 파일

| 파일 | 기준 문서 | 대상 문서 | 항목 수 |
|---|---|---|---|
| `자표준_골든셋.jsonl` | `samples/자표준문서.xlsx` | `samples/자표준_규격서.docx`, `samples/자표준_발표.pptx` | 27 (match 17 / mismatch 3 / missing 6 / unknown 1) |

대상 문서와 이 정답은 `scripts/make_synthetic_targets.py` 의 **같은 CASES 테이블**에서 함께 생성된다.
문서만 바꾸고 정답을 안 고치는 드리프트가 구조적으로 불가능하다. 재생성:

```bash
python scripts/make_synthetic_targets.py
```

## 레코드 스키마 (JSON Lines)

```json
{"id": "g-ppt-02",
 "entity_name": "충전환경온도",
 "target_doc": "자표준_발표.pptx",
 "reference": {"doc": "자표준문서.xlsx", "row": 17, "cell_range": "E17:H17",
               "attributes": {"lower_limit": -5, "target_value": 35, "upper_limit": 85}},
 "target_text": "충전환경온도 | -5 | 35 | 80 | ℃",
 "expected": "mismatch",
 "mismatch_attributes": ["upper_limit"],
 "reason": "기준 상한 85 vs 대상 80"}
```

- 단위는 **entity × 대상문서** 1건 — `FACT_PIPELINE_PLAN.md` §6.2 의 비교 결과 스키마와 1:1 대응한다.
- `expected` 는 `match | mismatch | missing | unknown` 4분류.
- `mismatch_attributes` 는 `mismatch` 일 때 어긋난 속성 이름(= `mismatch_type` 후보).

## 라벨 기준

기준 문서(`자표준문서.xlsx`)는 **단위 열(K)이 전부 비어 있고**, 단일값 항목이 `F(하한치)` 와 `G(중심치)` 에
섞여 들어가 있다. 실문서의 지저분함을 그대로 둔 채 다음 약속으로 라벨했다.

| 상황 | 라벨 |
|---|---|
| 값이 같고 대상 단위가 그 항목의 상식적 단위 | `match` |
| 값 자체가 다름 | `mismatch` (+ 어긋난 속성 기록) |
| 값은 같아 보이나 **단위 스케일이 달라**(1495 vs 1.495A) 기준 단위를 모르면 등가 확정 불가 | `unknown` |
| 대상 문서에 항목 자체가 없음 | `missing` |

표기 차이(3 vs 3.0), 한↔영 표기(`Standard ambient temperature`), 본문/스피커노트 분리 기재는
**정답에 영향을 주지 않는다** — 그걸 뚫고 같은 값을 찾아내는 게 파이프라인의 일이다.

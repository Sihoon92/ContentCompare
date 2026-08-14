# 골든셋 (사람 라벨 정답)

fact 파이프라인의 라이브 검증(F3.5) · 매칭 spike(F5) · 엔진 벤치마크(F6)가 공통으로 쓰는 정답 데이터.

## 파일

| 파일 | 기준 문서 | 대상 문서 | 항목 수 |
|---|---|---|---|
| `자표준_골든셋.jsonl` | `samples/자표준문서.xlsx` (3~26행) | `samples/자표준_규격서.docx`, `samples/자표준_발표.pptx` | 27 (match 17 / mismatch 3 / missing 6 / unknown 1) |
| `spec_en_골든셋.jsonl` | `samples/자표준문서.xlsx` (3~106행 전체) | `samples/spec_en.docx` | 104 (match 52 / mismatch 14 / missing 28 / unknown 10) |

대상 문서와 이 정답은 각각 **하나의 CASES 테이블**에서 함께 생성된다.
문서만 바꾸고 정답을 안 고치는 드리프트가 구조적으로 불가능하다. 재생성:

```bash
python scripts/make_synthetic_targets.py   # 자표준_규격서.docx + 자표준_발표.pptx + 자표준_골든셋
python scripts/make_en_sample.py           # 자표준문서.xlsx 27~106행 값 + spec_en.docx + spec_en_골든셋
```

`make_en_sample.py` 는 **기준 엑셀의 값까지 같은 테이블에서 쓴다** — 대상 문서·정답만이 아니라
기준까지 한 출처로 묶어야 세 쪽이 어긋날 수 없다. 단 **3~26행은 쓰지 않는다**: 그 행의 값과
아래 라벨 전제에 `자표준_골든셋.jsonl` 27건이 걸려 있어, 값을 채우면 그 27건을 재라벨해야 한다.

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

기준 문서(`자표준문서.xlsx`)는 **3~26행과 27~106행의 성격이 다르다**. 둘을 섞어 읽지 말 것.

| 행 | 성격 | 라벨에 미치는 영향 |
|---|---|---|
| 3~26 | 손으로 만든 원래 픽스처. **단위 열(K)이 전부 비어 있고** 단일값이 `F(하한치)`·`G(중심치)` 에 섞여 있다 | 실문서의 지저분함이 그대로 남아 있어 `unknown` 이 잘 나온다 |
| 27~106 | `make_en_sample.py` 가 채운다. 단위(K)·Level(L)·비고(M)·Method(N)가 모두 있다 | 단위가 있어 스케일 모호성이 없다 — `unknown` 은 **대상 문서 쪽**이 모호할 때만 |

아래는 두 구간에 공통으로 적용한 약속이다.

| 상황 | 라벨 |
|---|---|
| 값이 같고 대상 단위가 그 항목의 상식적 단위 | `match` |
| 값 자체가 다름 | `mismatch` (+ 어긋난 속성 기록) |
| 값은 같아 보이나 **단위 스케일이 달라**(1495 vs 1.495A) 기준 단위를 모르면 등가 확정 불가 | `unknown` |
| 대상이 **수치 없이 정성 서술만** 하거나("no significant capacity loss"), **단위를 빼먹었거나**("approximately 3"), **환산 불가한 다른 척도**로 적음(기준 7% vs 대상 0.30mm) | `unknown` |
| **대상 문서가 자기모순** — 같은 항목을 두 곳에서 다르게 말하고 그중 하나가 기준과 어긋남 | `mismatch` (+ 어긋난 속성) |
| 대상 문서에 항목 자체가 없음 | `missing` |

자기모순을 `mismatch` 로 두는 이유: 문서 어딘가에 맞는 값이 있다고 해서 틀린 값이 사라지지 않는다.
`match` 로 두면 파이프라인이 **맞는 쪽만 찾고 끝내도** 만점을 받아, 1:N 후보를 다 보게 만든
`FactComparator.finalize` 의 효과를 측정할 수 없다. `spec_en.docx` 는 이 상황을 네 군데 심어 뒀다
(공칭전압 3.85↔3.89 · 표준용량 typ. 1180↔1185 · 12~15 구간 0.7C↔0.8C · 15~45 구간 1.2C↔1.3C/1.1C/0.8C).

앞의 `unknown` 은 **기준**이 모호한 경우(3~26행), 뒤의 `unknown` 은 **대상**이 모호한 경우다.
둘 다 "값을 대조할 수 없다"는 같은 결론이지만 고치는 곳이 다르다 — 전자는 기준 문서의 단위 열,
후자는 대상 문서의 서술이다.

표기 차이(3 vs 3.0), 한↔영 표기(`Standard ambient temperature`), 본문/스피커노트 분리 기재는
**정답에 영향을 주지 않는다** — 그걸 뚫고 같은 값을 찾아내는 게 파이프라인의 일이다.

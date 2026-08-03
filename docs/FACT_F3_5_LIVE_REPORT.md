# Phase F3.5 라이브 검증 리포트

> 실행일: 2026-08-03 · 모델: ollama `gemma4:12b` (chat) / `bge-m3` (embedding)
> 상위 계획: [`FACT_PIPELINE_PLAN.md`](FACT_PIPELINE_PLAN.md) §9 Phase F3.5
> 목적: F2/F3 의 **미체크 라이브 DoD** 를 닫고, F4a/F4b/F5 설계를 **사변이 아닌 실측**으로 고정한다.

## 0. 한 줄 결론

F0~F3 파이프라인은 실문서 + 실제 LLM 으로 **끝까지 동작한다**(3문서 전부 성공, 기준 20행 → 20 fact).
다만 **(a) Ollama 컨텍스트 소진으로 인한 빈 응답**이 블로커였고, **(b) 대상 문서 fact 의 무증상 누락**과
**(c) entity_name 표기 흔들림**이 실측으로 확인되어 F4a·F5 의 설계를 바꾼다.

---

## 1. 실행

```bash
python scripts/make_synthetic_targets.py     # 대상 문서 + 골든셋 생성

contentcompare --config config/config.yaml --engine fact -v \
  --reference samples/자표준문서.xlsx \
  --targets samples/자표준_규격서.docx samples/자표준_발표.pptx
```

| 문서 | 결과 | 단계 | LLM 호출 | 산출 fact |
|---|---|---|---|---|
| `자표준문서.xlsx` (데이터 20행) | ✅ | 7 | 3 (profile/schema/record) | 20 |
| `자표준_규격서.docx` (블록 12) | ✅ | 4 | 1 | 10 |
| `자표준_발표.pptx` (도형·노트 14) | ✅ | 4 | 1 | 12 |

- **캐시 재실행**: 같은 명령 재실행 시 3문서 모두 `LLM 0회` (지문 캐싱 정상, 결정 #2 검증).
- **에러 격리**: 대상에 없는 경로를 끼워 실행 → 그 문서만 `❌ com_error: 파일을 찾을 수 없습니다`,
  앞뒤 문서는 정상 완료, 종료코드 1. 로드맵이 지정한 `LlmBudgetExceeded`/`ValueError` 만 잡았다면
  **놓쳤을 예외**(COM)라, 격리 범위를 `Exception` 으로 넓힌 판단이 실측으로 옳았다.

---

## 2. 발견 ①(블로커·해결) — Ollama 컨텍스트 소진 = 오류 아닌 **빈 응답**

첫 실행에서 3문서 모두 `document_profile` 다음 단계에서 실패했다.

```
ValueError: LLM JSON 파싱 실패(재시도 1회): ''
```

직접 호출로 원인 확정:

```
done_reason = "length",  prompt_eval_count = 2177,  eval_count = 1919   # 합계 = 4096
message.content  = ""
message.thinking = "*   `B1`: '순번' (metadata) ..."   ← 컨텍스트를 여기에 다 씀
```

`gemma4:12b` 는 **thinking 모델**이고 Ollama 기본 `num_ctx` 는 4096 이다. 프롬프트가 조금만 커지면
사고 토큰이 남은 컨텍스트를 소진해 **`content` 가 빈 문자열**로 돌아온다 — HTTP 200, 오류 없음.
소형 샘플(`기준.xlsx` 3행)에서는 재현되지 않아 F3 단위테스트로는 절대 잡을 수 없는 종류다.

**조치**
- `llm.ollama.num_ctx`(기본 0=미지정) / `llm.ollama.think`(기본 None=미지정) 설정 추가 — 미지정 시 기존 동작 유지.
- 빈 응답이면 원인을 설명하는 `LLMRequestError` 로 승격(“num_ctx 를 늘리거나 think:false”).
  그대로 두면 상위에는 `파싱 실패: ''` 로만 보여 원인 추적이 사실상 불가능하다.
- `config.example.yaml`/`config.yaml` 에 `num_ctx: 16384`, `think: false` 반영.

**부수 효과**: `think: false` 로 스키마 유도 1회가 **219초 → 26초**(8배).

---

## 3. 발견 ② — 스키마 유도는 정확, 다만 역할 배정에 잡음

`table_profile` 은 완벽했다. 2행 헤더를 정확히 인식:

```
header_start_row=1, header_rows=2, data_start_row=3, header_depth=2
F=quantitative_lower_bound  G=quantitative_target  H=quantitative_upper_bound
```

`records` 도 **20행 → 20 record, 좌표 누락 0**, 카테고리가 빈 15~22행에 `기본사양` 이 **carry-over** 로
정확히 채워졌다(F2 배치 경계 처리 검증).

반면 `column_schema` 에는 다음 잡음이 있다 — **F4a validator 검사 항목의 근거**가 된다.

| 관찰 | 내용 | 영향 |
|---|---|---|
| 역할 뒤바뀜 | `D(중분류)`→`entity_name`, `E(소분류)`→`entity_subcategory` (실제 항목명은 E) | 결과적으로 `display_name` 은 정상이라 무해했으나 우연에 가깝다 |
| 역할 중복 | `quantitative_lower_bound` 가 `F` 와 `I` 에 동시 부여, `entity_subcategory` 가 `E/O/P` 3중 | 정량 필드 해석이 갈릴 수 있음 |
| 코드값 오염 | `O` 열의 코드 `C001D001` 이 `subcategory` 로 승격 | entity_path 에 의미 없는 코드가 섞임 |

---

## 4. 발견 ③ — 드롭 계측이 0인데 fact 가 사라진다 (무증상 recall 손실)

Word 대상에서 `고객 표준 버전` 문장이 fact 로 나오지 않았는데, 드롭 카운터는 전부 0이었다.
**LLM 이 애초에 뽑지 않은 블록은 드롭이 아니기 때문**이다. 그래서 계측을 하나 더 넣었다:

```
blocks_in / blocks_cited / blocks_uncited_samples   # 입력 블록 대비 근거로 인용된 블록
```

| 문서 | 입력 블록 | 인용 | 미인용 |
|---|---|---|---|
| `자표준_규격서.docx` | 12 | 6 | `w_b001~003`(제목·안내), `w_b010`(소제목), `w_b012`(단서) + **`w_b004`(고객 표준 버전 ← 진짜 손실)** |
| `자표준_발표.pptx` | 14 | 10 | 슬라이드 제목 4개(정상) |

미인용 대부분은 제목·보일러플레이트라 정상이지만, **정성 규격 문장 1건은 실제 누락**이다.
이 손실은 F5 에서 그대로 `missing` 오판이 된다 — 로드맵이 F3.5 에서 계측하라고 지목한 바로 그 실패다.

---

## 5. 발견 ④ — entity_name 표기가 흔들린다

대상 문서의 fact 이름은 기준과 글자 그대로 같지 않다.

| 기준(Excel) | Word | PPT |
|---|---|---|
| 정격충전전압 | 정격 충전 전압 | 충전 전압 |
| 최대충전전류 | 최대 충전 전류 | (없음) |
| 1개월저장온도 | — | 1개월 저장 조건 |
| 표준환경온도 | 표준환경온도 | Standard ambient temperature |
| 고객 표준 버전 | (누락) | 적용 규격 |
| 공칭전압 | 공칭전압 | **`충전 규격 상세조건` fact 의 attribute** 로 흡수 |

정규화(공백·기호 제거) 후 **완전일치율은 Word 50%(10/20), PPT 15%(3/20)** 에 그친다.
마지막 행이 특히 중요하다 — PPT 스피커노트의 `공칭전압 3.89V` 는 별도 fact 가 아니라
`충전 규격 상세조건` 이라는 슬라이드 제목성 entity 의 속성으로 들어갔다. **entity 단위 매칭만으로는
구조적으로 못 찾는 케이스**가 실제로 존재한다.

---

## 6. F5 매칭 spike 실측 (`scripts/spike_fact_match.py`, 무 LLM)

```bash
python scripts/spike_fact_match.py --ref artifacts/자표준문서_xlsx/facts.json \
  --target artifacts/자표준_발표_pptx/facts.json --golden golden/자표준_골든셋.jsonl --embed
```

| 대상 | 완전일치 | BM25 recall@1 / @3 | 임베딩 recall@1 / @3 | RRF recall@1 / @3 | 오매칭(대상에 없음) |
|---|---|---|---|---|---|
| `자표준_규격서.docx` | 10/20 | **100% / 100%** | 100% / 100% | 100% / 100% | 3/3 |
| `자표준_발표.pptx` | 3/20 | 80% / 90% | **100% / 100%** | 80% / 90% | 3/3 |

**결론 — F5 검색 전략은 이 수치로 확정한다.**

1. **완전일치만으로는 불가** (15~50%). 폴백 검색은 선택이 아니라 필수.
2. **임베딩은 필수**다. BM25 가 놓친 유일한 케이스가 `표준환경온도 ↔ Standard ambient temperature`
   (교차언어)로, 어휘 매칭으로는 원리적으로 불가능하다. `bge-m3` 는 이를 top1 으로 맞혔다.
3. **RRF 단순 융합은 이득이 없었다**(80%, BM25 와 동일). 2랭커·소규모에서는 BM25 의 오답 1위가
   희석되지 않는다 → **임베딩 우선 + BM25 보조(가중)** 또는 임베딩 단독으로 시작할 것.
4. **점수 임계값이 반드시 필요하다.** 대상에 없는 항목 3건 전부에 후보가 붙었다(3/3 오매칭).
   BM25 점수는 정답 5.1~14.5 vs 오매칭 1.87~3.56 으로 분리 가능하므로, 임계값 컷 + `unknown` 강등이
   `missing` 판정의 전제다. 임계값 없이 F5 를 만들면 **모든 missing 이 오탐 mismatch 로 바뀐다.**

---

## 7. 다음 Phase 로 넘기는 것

### F4a (결정적 Validator) — 실측 기반 검사 항목

| # | 검사 | 이번 실측 근거 |
|---|---|---|
| 1 | 수치가 있는데 `unit` 이 빈 fact | 기준 문서 20 fact **전부** 해당(원본 단위 열이 비어 있음) → 대량 발생 예상, `low_confidence` 태깅 정책 필요 |
| 2 | `lower ≤ target ≤ upper` | 이번 데이터에서 위반 0 — 검사는 값싸므로 유지 |
| 3 | `semantic_role` 중복 부여 | `F`/`I` 가 동시에 `quantitative_lower_bound` (§3) |
| 4 | 블록 커버리지(미인용 입력) | Word 6/12, PPT 10/14 — 제목류 제외 후 남는 미인용은 검토 대상 (§4) |
| 5 | `evidence_text` 실재 | 이번엔 전건 실재(육안 확인). 공백 정규화 후 부분일치 규칙 유지 |

### F4b (Repair Loop) — 관측된 실패 모드

- **정성 문장에서 fact 미추출** (`w_b004`) — 정량 값이 없는 문장을 LLM 이 건너뛴다.
- **entity_name 을 슬라이드/문단 제목으로 대체**하고 실제 항목을 attribute 로 흡수
  (`충전 규격 상세조건.공칭전압`). repair 대상: entity 승격.
- JSON 준수도는 양호(`num_ctx` 해결 후 `parse_failures = 0`) → **JSON 교정형 repair 는 우선순위가 낮다.**

### F5 (Comparator)

- §6 의 4개 결론(임베딩 필수 · 임계값 필수 · RRF 재검토 · 완전일치 선행).
- attribute 키 불일치 실사례: `최대충전전류` 는 기준 `target_value=1495`, Word `upper_limit=1.495A`
  → 키도 스케일도 다름. 계획 §6.2 의 `unknown` 조건("attributes 키가 겹치지 않음")이 그대로 발생한다.

### 남은 위험

- 골든셋의 대상 문서는 **합성**이라 실문서의 지저분함(스캔 표, 병합 셀, 각주)은 미검증.
  실무 문서를 확보하면 같은 방식으로 골든셋을 확장할 것.

---

## 8. DoD 정리

- [x] **F2 라이브 DoD** ([`FACT_F2_DESIGN.md`](FACT_F2_DESIGN.md) §8): `자표준문서.xlsx` → `records.json`,
      entity/attributes/source 정합, carry-over 동작 확인.
- [x] **F3 라이브 DoD** ([`FACT_F3_DESIGN.md`](FACT_F3_DESIGN.md) §8): Excel/Word/PPT 3경로 모두 `facts.json` 생성,
      schema 정합(entity/attributes/source/evidence), source 검증 동작(할루시네이션 id 0건).
- [x] 에러 격리 + 계측(`run_stats.json`) 추가 및 실증.
- [x] 골든셋 27항목(match 17 / mismatch 3 / missing 6 / unknown 1) — `golden/자표준_골든셋.jsonl`.
- [x] F5 매칭 spike 실측 완료.

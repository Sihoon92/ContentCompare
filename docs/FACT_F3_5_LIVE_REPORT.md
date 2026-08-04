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

---

## 9. 후속 — F4a/F5/F6 구현 후 실측 (2026-08-03)

end-to-end 가 완성돼 `--engine fact` 가 리포트까지 만든다. 위 §6 의 spike 예측이 실제 판정에서
어떻게 나타났는지 기록한다.

### 9.1 골든셋 정확도

| 단계 | 정확도(27항목) | 비고 |
|---|---|---|
| 최초 구현 | 23/27 (85%) | 오답 4건 전부 LLM 판정 |
| + 단일속성 교차키 규칙 | 24/27 (89%) | 텍스트에 과적용돼 신규 오답 1건 발생 |
| + 텍스트는 '같을 때만' 단정 | **25/27 (93%)** | 최종 |

**남은 오답 2건**(둘 다 설명 가능한 한계):
1. `고객 표준 버전 → missing`(정답 match) — **F3 추출 누락의 전파**다. Word 의 `w_b004`
   문장이 fact 로 안 나와 대상 fact 자체가 없었다(§4). F5 의 판단은 주어진 fact 기준으로는 옳다.
   §4 에서 "이 손실은 F5 에서 그대로 missing 오판이 된다"고 예측한 그대로 실현됐다.
2. `최대충전전류 → match`(정답 unknown) — LLM 이 1495 = 1.495A 로 단정했다. 프롬프트에
   "값이 배수 관계라 단위에 따라 달라지면 unknown" 을 명시했는데 따르지 않았다. **F4b 대상**.

### 9.2 코드가 판정을 못 하게 만든 실제 원인 (설계 교정 2건)

- **속성 키 불일치**: 기준 문서가 공칭용량 1150 을 *하한치 열*에 적어 `lower_limit` 이 됐고
  대상은 `target_value` 였다. 값은 같은데 키가 달라 코드가 판정을 포기했다.
  → 양쪽 다 **속성이 하나뿐**이면 키 이름은 원본 열 위치의 흔적일 뿐이므로 값으로 판정한다.
  단, `lower_limit` vs `upper_limit` 처럼 의미가 반대인 쌍은 제외.
- **텍스트 값의 '다름'은 불일치가 아니다**: "배터리승인규격 ver 4.7 SEC Req. ver.4.7" 과
  "SEC Req. ver.4.7" 은 문자열이 달라도 같은 규격이다.
  → 문자열은 **같을 때만** 코드가 단정하고, 다르면 LLM 에 넘긴다.

### 9.3 판정 주체 — LLM 은 얼마나 필요했나

40건 중 **코드 28건 / LLM 12건(30%)**. 값·단위가 정규화돼 있으면 대부분은 코드가 끝낸다는
가설이 실측으로 확인됐다. LLM 이 필요했던 곳은 속성 키가 다르거나, 단위 등가가 불명하거나,
검색 점수가 경계인 경우다.

### 9.4 새로 관찰된 위험 — 오매칭이 mismatch 로 보고된다

리포트에서 `평가환경온도`(기준 21/24/28)가 Word 의 `표준환경온도`(21/25/29)와 매칭돼
**불일치로 보고**됐다. 실제로는 Word 에 평가환경온도가 없으므로 `missing` 이 맞다.
검색 점수 0.716(경계 구간)이라 LLM 에 갔는데, LLM 이 `missing` 대신 `mismatch` 를 골랐다.

§6 에서 예고한 "무관한 fact 가 top1 을 차지해 mismatch 오판" 이 실제로 발생한 사례다.
`missing` 은 "없다"는 신호라도 주지만 틀린 짝의 `mismatch` 는 사람을 잘못된 수정으로 유도하므로
더 위험하다. → **F4b 프롬프트 1순위 대상**(경계 점수일 때 missing 을 우선 고려하도록).

### 9.5 엔진 비교

`FACT_PIPELINE_PLAN.md` §1.1.1 참조. 요약: fact 89~95% vs rag 47~53%, 시간·호출 수도 fact 가
더 적었다(계획의 "비용 높음/느림" 예상과 반대 — 호출 단위가 행 단위가 아니라 배치이기 때문).

## 10. F7 개념 그래프 도입 후 실측 (2026-08-04)

§9.4 에서 관찰한 "경계 점수 오매칭이 mismatch 로 보고된다" 위험을 F7(`FACT_F7_DESIGN.md`)로
해결했다는 것을 이번 실행으로 확인했다. 실행: `python scripts/compare_engines.py --config
config/config.yaml --reference samples/자표준문서.xlsx --targets samples/자표준_규격서.docx
samples/자표준_발표.pptx --golden golden/자표준_골든셋.jsonl --engines fact` (**기존
`artifacts/` 를 지우지 않고 그대로 재사용** — F3/F3.5/F4a 추출·검증 단계는 이번 실행의
코드로 새로 생성된 것이 아니라 이전 실행이 남긴 캐시다. 이번 실행에서 실제로 새로 돈
것은 F7 개념 단계와 F5 비교 단계뿐이다).

### 10.1 §9.4 위험은 해소됐다

2026-08-03 라이브에서 실제로 잘못 이어졌던 3쌍(임베딩 0.6084~0.6944, 정답 0.7656 과 섞여
임계값으로 못 가르던 것들)을 `artifacts/자표준문서_xlsx/comparison_result.json` 에서 직접
확인했다. `concept_recall_min` 을 0.3 / 0.45 / 0.6 어느 값으로 둬도 세 항목 모두 대상
문서(`자표준_규격서.docx`)에서 **`missing`** 으로 보고됐다(더 이상 무관한 항목과
`match`/`mismatch` 로 엮이지 않는다):

| 기준 항목 | 대상 문서 결과 | 비고 |
|---|---|---|
| `1개월저장온도` | `missing` | 예전엔 `표준환경온도`(오답)와 엮였다 |
| `평가환경온도` | `missing` | 예전엔 `표준환경온도`(오답, §9.4 사례)와 엮였다 |
| `충전환경온도` | `missing` | 예전엔 `정격 충전 전압`(오답)과 엮였다 |

이 세 항목은 `knowledge/ontology.yaml` 에 `differs_by` 로 승격돼 있어 `concept_graph.json` 의
해당 엣지가 `"decided_by": "ontology"`, `"promoted": true` 로 남는다(LLM 호출 없이 코드가
직접 차단). `고객 표준 버전` ↔ `문서 기준 규격`(같은 온톨로지 파일의 `same_as` 항목)도
`concept_graph.json` 에서 `"decided_by": "ontology"` 로 확인됐다 — 값 비교 단계(F5
Comparator)가 그 위에서 LLM 을 한 번 더 쓴 것은 "연결해도 되는가"가 아니라 "값이 같은가"를
판단한 것이라 별개다(설계 §0 "값 판정은 F5 Comparator 의 몫" 그대로).

전체 40건의 비교 결과(19 기준 항목 × 2 대상 문서)를 훑어봐도 주제가 다른 두 항목이
`match`/`mismatch` 로 보고된 사례는 관찰되지 않았다 — F7 의 존재 이유(설계 §11 DoD #2)가
이번 실측에서 성립했다.

### 10.2 골든셋 정확도와 `concept_recall_min` 민감도

`scripts/compare_engines.py` 는 골든셋 27항목을 기준 항목 단위로 접어 **19개**만 채점한다
(§9.1 의 27항목 카운트와 단위가 다르다 — 여기서는 스크립트의 19항목 기준을 그대로 쓴다).

| `concept_recall_min` | 정확도 | 소요 시간 | LLM 호출(전체) | `stats.concept.llm_calls` |
|---|---|---|---|---|
| 0.3(기본) | 17/19 (89%) | 349초 | 14회 | 7 |
| 0.45 | 17/19 (89%) | 329초 | 14회 | (미기록, 0.3 과 오답 동일) |
| 0.6 | **18/19 (95%)** | 180초 | 11회 | 3 |

설계 §11 DoD #3 은 "`concept_recall_min` 을 바꿔도 채점 결과가 변하지 않는다"를 요구한다.
**부분적으로만 성립한다**:

- 세 회귀 쌍이 다시 엮이는 일은 세 값 모두에서 없었다(§10.1) — F7 이 막으려던 핵심 실패
  모드는 값에 무관하게 막힌다.
- 그러나 **골든셋 총점 자체는 값에 따라 달라졌다**. `3개월저장온도`(PPT 대상, 정답
  `mismatch`)가 `concept_recall_min=0.3/0.45` 에서는 `missing`(오답)으로, `0.6` 에서는
  올바르게 `3개월 저장 조건`과 이어져 `mismatch`(정답)로 나왔다. 원인으로 보이는 것: 임계값이
  낮을수록 후보 쌍이 늘어 `concept_batch_pairs`(20개) 배치의 구성이 달라지고, 같은 쌍이라도
  배치에 함께 들어가는 다른 쌍들이 달라지면 LLM 의 판정이 달라질 수 있다(배치 프롬프트가
  공유되므로 개별 쌍 판정이 배치 구성에서 완전히 독립적이지 않다). 이건 F7 설계가 없애려 한
  "무관한 쌍이 mismatch 로 잘못 이어지는" 실패 모드가 아니라, **정답 쌍을 찾는 재현성**의
  문제이고, F4b(교정 루프)·배치 구성 전략의 후속 과제로 남긴다.
- 남은 오답 `최대충전전류`(정답 `unknown`, 판정 `match`)는 §9.1 에서부터 있던 기존 한계
  (1495 = 1.495A 단정)로, F7 과 무관하며 세 임계값 모두에서 재현됐다.

### 10.3 재현성(온톨로지 승격 후 LLM 호출 감소) — 부분 검증

`knowledge/ontology.yaml` 에 미리 승격해 둔 4개 관계(회귀 3쌍 + 진짜 동의어 1쌍)는 실제로
LLM 을 건너뛰었다. **실측치와 출처**: `concept_recall_min=0.3` 실행의 값은 그 실행 직후
`artifacts/자표준문서_xlsx/concept_graph.json` 의 `stats` 를 읽어 확인한 것이고(이후
`0.45`/`0.6` 실행이 같은 경로를 덮어써 그 시점 파일은 더는 없다), `0.6` 실행의 값은 이
문서를 쓰는 시점까지 그 파일이 마지막 실행(=0.6) 결과 그대로 남아 있어(이후 재실행 없음)
같은 파일에서 다시 읽어 확인했다:

| `concept_recall_min` | `pairs_considered` | `pairs_from_ontology` |
|---|---|---|
| 0.3 | 148 | 6 |
| 0.45 | (미기록) | (미기록) |
| 0.6 | 65 | 4 |

값은 후보 쌍 수에 비례해 달라진다(임계값이 높을수록 후보가 줄어 온톨로지 적중 수도
준다). `0.45` 실행은 골든셋 정확도만 기록하고 `stats.concept` 를 따로 저장해 두지
않아 이 표에는 넣지 않는다 — 범위(예: "65~148")로 뭉뚱그리면 관측하지 않은 값처럼
보이므로, 실제로 관측한 두 값만 남긴다.

다만 설계 §11 DoD #4("재실행 시 개념 단계 LLM 호출이 0~1회")를 **이 실측
문서셋 전체 규모로는 확인하지 않았다** — `knowledge/ontology.yaml` 은 브리프 지시대로
2026-08-03 에 실측 확인된 4개 관계만 담고 있고, 이 문서셋에는 그 밖에도 후보 쌍이
훨씬 많아(위 표) 대부분은 여전히 LLM 이 처음 판단한다 — 이는 결함이
아니라 "사람이 검토해 승격할수록 줄어든다"는 온톨로지의 점진적 설계 그대로다. DoD #4 는
`tests/test_concept_regression.py::test_promoted_ontology_blocks_them_without_llm` 에서
**단위 테스트 수준으로는 확인됐다**(온톨로지가 후보를 전부 덮으면 `chat.calls == 0`).

### 10.4 이번 작업에서 발견되고 고쳐진 결함 — 단위 테스트가 못 잡는 이음매

Task 11 의 end-to-end 회귀 테스트(`tests/test_concept_regression.py`)를 작성하며 실제
결함을 하나 찾았다: `concept_builder.resolve_known()` 이 만드는 온톨로지 확정 `same_as`
엣지는 인용문(`left_text`/`right_text`)을 채우지 않는데(사람은 항목명 쌍만 적을 뿐 원문을
인용하지 않는다), `concept_assembler.assemble()` 의 근거 검증은 `decided_by` 를 가리지 않고
모든 `same_as` 에 적용돼 빈 인용문을 이유로 그 엣지를 100% `unknown` 으로 강등시키고
있었다. 결과적으로 **온톨로지로 `same_as` 를 승격하는 기능 자체가 무력화**돼 있었다 — 설계
§3.2 의 대표 예시(`고객 표준 버전` ↔ `문서 기준 규격`)가 실제로는 연결되지 않는 상태였다.

`concept_assembler.py`(Task 3)와 `concept_builder.py`(Task 4)는 각자의 단위 테스트를 전부
통과했지만, 둘 다 "온톨로지로 승격된 `same_as` 엣지가 `assemble()` 을 실제로 통과하는 경로"는
테스트하지 않았다 — Task 3 의 승격 테스트는 `differs_by` 만, Task 4 의 온톨로지 테스트는
`resolve_known()` 반환값만 확인하고 `assemble()` 까지 넘기지 않았다. 두 모듈의 **경계에서만
드러나는 결함**이라 각자의 단위 테스트로는 원리적으로 잡을 수 없었고, 실제 사용 시나리오를
그대로 재현하는 end-to-end 회귀 테스트(`test_true_synonym_is_linked_when_promoted`)가 처음
잡아냈다. 수정은 커밋 `1520007`(근거 검증을 `decided_by == BY_LLM` 인 `same_as` 에만
적용 — 사람·코드가 확정한 연결은 LLM 의 주장이 아니므로 검증 대상이 아니다)이며, 이후
§10.1 의 실측으로 재확인했다.

**교훈**: 모듈 경계를 넘는 조합(승격 + 조립처럼 서로 다른 Task 가 만든 함수가 이어지는 지점)은
각 Task 의 단위 테스트만으로 보장되지 않는다. Phase 마지막에 실제 시나리오를 그대로 감싸는
end-to-end 회귀 테스트를 두는 것이 이런 이음매 결함을 잡는 유일한 안전장치였다.

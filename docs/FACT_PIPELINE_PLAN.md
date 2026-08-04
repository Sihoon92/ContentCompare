# Fact 기반 비교 파이프라인 구현 계획 (Fact Pipeline Plan)

> 작성일: 2026-06-28 · 보완: 2026-07-02 (설계 검토 반영 — §5 F4a/F4b 분할, §6.2 `unknown` 추가, §9 Phase F3.5 신설, 결정 #7)
>            · 2026-08-03 (F3 라이브 검증·F3.5 완료 — §9) · 2026-08-03 (F4a/F5/F6 완료 + §1 비교표 실측 반영)
> 상태: **end-to-end 동작** — F0~F6 완료(F4b 보류, §9). `--engine fact` 가 리포트까지 만든다.
> 실측 근거: [`FACT_F3_5_LIVE_REPORT.md`](FACT_F3_5_LIVE_REPORT.md)(추출) · §1.1.1(엔진 비교).
> 선행 설계 문서: [`DESIGN.md`](DESIGN.md), [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md)
> (위 두 문서는 **현행(구) 방식**을 다루며 Phase 1~5 완료 상태다. 본 문서는 그와 **별개의 신규 방식**이다.)

## 0. 이 문서의 목적

문서 간 내용 정합성 비교를, 단순 RAG(임베딩 top-k)가 아니라
**Multi-document Schema Induction + Fact Normalization + Consistency Checking**
방식으로 재설계하기 위한 구현 계획이다.

핵심 원칙 3가지:

1. **파일을 LLM에 바로 주지 않는다.** 코드가 먼저 `raw json`을 만들고, LLM은 그 raw를 보고 구조를 추론한다.
2. **비교 단위는 chunk가 아니라 fact다.** 모든 문서(Excel/Word/PPT)를 공통 fact schema로 정규화한 뒤 fact끼리 비교한다.
3. **검증기 + Repair Loop로 LLM 산출물을 코드가 검증·교정한다.**

### 제약: 현행 방식과 공존 (코드 보존)

> 🔒 **현행 임베딩 top-k 방식(`pipeline.py` 및 `readers/`·`similarity/`·`comparison/`)의 코드는 변경하지 않는다.**
> 신규 fact 파이프라인은 **별도 모듈/별도 엔진**으로 추가하며, 사용자가 두 방식을 **선택·비교**할 수 있게 한다.
> 두 방식의 결과를 같은 입력으로 나란히 비교하는 **벤치마크 하니스**(§8)를 포함한다.

---

## 1. 두 방식 비교 (현행 RAG vs 신규 Fact)

이 절은 "왜 신규 방식이 필요한가"와 "각 방식이 언제 유리한가"를 고정한다.

### 1.1 한눈 비교표

| 관점 | 현행: Embedding top-k RAG | 신규: Fact Schema Induction |
|------|---------------------------|------------------------------|
| 비교 단위 | 엑셀 행(`RecordItem`) ↔ 대상 문서 **청크** | 정규화 **fact ↔ fact** |
| 구조 인식 | 엑셀만 hybrid 분해(코드 규칙), Word/PPT는 평문 단락·도형 | 모든 문서 `raw json` → **LLM 구조 추론** |
| 헤더/컬럼 의미 | 헤더명 **그대로** 사용 (auto_header는 헤더행만 추정) | `column_schema`의 **semantic_role**로 정규화 |
| 단위/동의어 처리 | LLM + `knowledge/*.md`에 의존 (코드 등가변환 없음) | fact `attributes`에 `value+unit`, 정규화 규칙·검증 |
| 누락 위험 | 표현이 다르면(한↔영, 표기차) 임베딩 검색에서 누락 가능 | entity 기반 **다중 검색** + fact 매칭으로 완화 |
| 정보 분산 | 여러 문장/표에 흩어지면 top-k 청크 하나로 부족 | fact가 여러 shape/block을 **병합**(예: PPT 본문+주석) |
| 비교 방향 | **비대칭** (엑셀=기준 query, 나머지=코퍼스) | **대칭** (모든 문서가 fact화 → 교차 비교) |
| 추적성 | `source_label`(행/단락) | `source` + `evidence_text` + **중간 JSON 전부** 보존 |
| 검증 | JSON 파싱 실패 시 1회 재요청 | **rule validator**(F4a) + repair loop(F4b 미구현) |
| LLM 호출 수 | ≈ 기준 행 수 × 1 | 문서당 다단계 + 비교 위임분 |
| 중간 산출물 | 없음(리포트만) | 10종 JSON(§7) — 디버깅·개선 가능 |
| 구현 난이도 | **완료** | 높음(다단계 + 검증) — F4b 제외 완료 |
| 적합 상황 | 빠른 1:N 항목 존재 확인, 비용 민감 | 양식 상이 문서의 **정밀 정합성 검증**, 추적성 요구 |

### 1.1.1 실측 결과 (2026-08-03, `scripts/compare_engines.py`)

같은 입력(`자표준문서.xlsx` 20행 ↔ 합성 docx/pptx)에 두 엔진을 돌려 골든셋 27항목을
기준 항목 19개로 접어 채점했다. 모델은 ollama `gemma4:12b` + `bge-m3`.

| 항목 | rag | fact |
|---|---|---|
| 정확도(mismatch-first 채점) | 10/19 (**53%**) | 17/19 (**89%**) |
| 정확도(match-first 채점) | 9/19 (47%) | 18/19 (95%) |
| 소요 시간 | 297~462초 | **298초**(캐시 없음) / 62초(재실행) |
| LLM 호출 | 21회 | **18회**(캐시 없음) / 11~12회(재실행) |
| 판정 건수 | 20 (기준 행 × 1) | 40 (기준 행 × 대상 문서) |
| 판정 주체 | 전부 LLM | 코드 28 / LLM 12 (**LLM 위임률 30%**) |

**설계 예상과 달랐던 점**: 계획은 fact 방식이 "비용 높음 / 느림"이 될 것으로 봤으나
실측은 **오히려 더 싸고 빨랐다**. 이유는 호출 단위가 다르기 때문이다 — RAG 는 기준 행마다
1회 호출하는 반면(20행 = 20회), fact 는 문서당 배치로 3회 정도만 쓰고 비교는 **코드가 70%를
처리**한다. 문서가 커질수록(행 수 증가) 이 격차는 벌어진다.

RAG 의 오답은 대부분 `missing` 오판(내용이 있는데 못 찾음)과 `mismatch` 오판이었다 —
양식이 다른 문서에서 임베딩 top-k 가 대응 내용을 놓치는 구조적 한계다.

> ⚠️ 표본은 합성 대상 문서 기반 27항목이다. 실무 문서(스캔 표·병합 셀·각주)에서는
> 다시 재보아야 한다.

### 1.2 같은 예시로 본 차이

입력(세 문서가 같은 사실을 다르게 표현):
```
Excel: 충전환경온도 / 하한치 -5 / 중심치 25 / 상한치 55 / 단위 도씨
Word : 충전환경온도는 -5℃에서 55℃ 범위로 관리하며 중심치는 25℃로 한다.
PPT  : 충전환경온도: -5~55℃, 중심치 25℃  (+ 주석: 0.1C, 4.55V 조건 기준)
```

- **현행**: 엑셀 행 `"항목=충전환경온도 | 하한치=-5 | ..."`을 임베딩해 Word/PPT 청크 top-k를 찾고 LLM이 비교.
  - 위험: PPT의 값이 본문+주석에 나뉘면 청크 하나로 부족. 영어 표현이면 검색 누락 가능. 단위(도씨 vs ℃)는 LLM 판단에 의존.
- **신규**: 세 문서 각각을 `fact`로 정규화 →
  ```json
  { "entity_name": "충전환경온도",
    "attributes": { "lower_limit": {"value": -5, "unit": "℃"},
                    "target_value": {"value": 25, "unit": "℃"},
                    "upper_limit": {"value": 55, "unit": "℃"} } }
  ```
  세 fact의 `entity_name`으로 묶고 `attributes`를 키별로 대조 → 단위 정규화(도씨≡℃) 후 `match`/`mismatch(upper_limit)` 판정. PPT 본문+주석은 하나의 fact로 병합.

---

## 2. 신규 파이프라인 전체 구조

```
[Input] Excel / Word / PPT
   ↓ Document Loader (파일타입 감지)           ✅ 재사용 가능
   ↓ Raw Extractor                            🟡 Excel/Word 완료, PPT 신규
physical_raw.json
   ↓ Raw Compactor                            🟡 Excel/Word 완료, PPT 신규
compact_raw.json
   ↓ Document Profiler (LLM)                   ✅ F1
document_profile.json
   ↓ Schema Inducer (LLM)                      ✅ F1
table_profile.json + column_schema.json
   ↓ Record Normalizer (LLM)                   ✅ F2 (Excel)
records.json
   ↓ Fact Extractor (Excel 코드 / Word·PPT LLM) ✅ F3 (핵심)
facts.json
   ↓ Rule Validator (코드)                     ✅ F4a
validation_report.json
   ↓ Repair Loop (LLM)                         ⏸ F4b 보류
(facts.json 교정)
   ↓ Fact Store → Matcher → Comparator         ✅ F5 (코드 우선 + LLM 위임)
comparison_result.json
   ↓ Report Generator (fact 전용 렌더)          ✅ F6
[Output] 검토 리포트 (--out / reports/)
```

상태 범례: ✅ 구현 완료 · ⏸ 보류.

---

## 3. 현행 코드 자산 매핑 (무엇을 재사용/확장/신규하는가)

| 신규 파이프라인 단계 | 기존 코드 | 처리 방침 |
|---|---|---|
| Document Loader | `raw/extract.py` `_EXT_MAP`, `readers/base.py` `get_reader` | 재사용 (PPT 분기 추가) |
| Raw Extractor (Excel/Word) | `raw/excel_raw.py`, `raw/word_raw.py`, `raw/models.py` | **그대로 재사용** |
| Raw Extractor (PPT) | — | **신규** (`raw/ppt_raw.py` + `RawPptDocument`) |
| Raw Compactor | `raw/compact.py` | Excel/Word 재사용, PPT 분기 추가 |
| Schema Inducer(헤더행) | `readers/header_detect.py` | 로직 참고/이관 (현재는 구 `excel_reader.auto_header` 전용) |
| LLM/임베딩 백엔드 | `llm/` (`factory.build_clients`, `http.py`) | **그대로 재사용** |
| COM 안전 정리 | `readers/com_util.py` | **그대로 재사용** |
| 리포트 렌더 | `report/markdown_report.py`, `report/store.py` | 확장(fact 결과용 렌더 추가) |
| 현행 비교 엔진 전체 | `pipeline.py`, `similarity/`, `comparison/`, `readers/` | 🔒 **변경 없음(보존)** |

> 신규 코드는 별도 패키지 **`contentcompare/fact/`** 에 둔다(아래 §6). 기존 패키지는 건드리지 않는다.

---

## 4. 단계별 상세 (입력 → 산출물 → 담당 → 검증)

각 단계는 `중간 산출물 JSON`을 반드시 디스크에 남긴다(추적·재실행용).

### Step 1. Document Profiler (LLM)
- 입력: `compact_raw.json`
- 출력: `document_profile.json` — `doc_type`, `main_purpose`, `main_structures[]`(type/range/purpose), `confidence`
- 목적: 문서 목적·주요 구조(표 후보) 파악. "이 문서는 표준 규격 리스트다, row 하나가 규격 항목이다" 수준.

### Step 2. Schema Inducer (LLM) — Table Profile + Column Schema
- 입력: `compact_raw.json` + `document_profile.json`
- 출력:
  - `table_profile.json` — `header_structure`(header_rows, data_start_row, header_depth), **`row_grain`**(row 하나의 의미, primary_entity_columns)
  - `column_schema.json` — 컬럼별 `field_name`, **`semantic_role`**(예: `quantitative_lower_bound`), `data_type`, `raw_header[]`
- 기존: `header_detect.py`가 `header_start`/`header_rows`만 추정 → **확장 필요**(row_grain, column role, semantic_role).
- semantic_role 표준 어휘 사전을 본 단계에서 **확정**(하한치/Lower/Min → `quantitative_lower_bound` 등) — 문서 양식이 달라도 비교되게 하는 핵심.

### Step 3. Record Normalizer (LLM)
- 입력: `compact_raw.json`(raw row/table/block) + `column_schema.json`
- 출력: `records.json` — `record_id`, `entity{category/mid/sub/display_name}`, `quantitative_spec`, `qualitative_spec`, `metadata`, **`source{sheet,row,cell_range}`**
- 원칙: `source`를 **반드시** 유지(불일치 추적용).
- 주의: 현행 `excel_reader._build_record`는 코드가 기계적으로 `RecordItem`을 만들지만, **여기선 LLM이 schema 기반으로 의미 정규화**한다(개념이 다름).

### Step 4. Fact Extractor (LLM) — 핵심
- 입력: `records.json`(Excel) / `compact_raw.json`(Word·PPT 블록·도형)
- 출력: `facts.json` — `fact_id`, `fact_type`, **`entity_name`**, **`entity_path[]`**, **`attributes{name:{value,unit}}`**, `search_text`, `source`, **`evidence_text`**, `confidence`
- Word/PPT도 **동일한 fact schema**로 변환 → 이후 비교가 단순해짐.
- `evidence_text`는 raw에 실제 존재하는 문구만(지어내기 금지) — §5 source 검증과 연동.

### Step 5. Rule Validator (코드) — F4a
- 입력: `facts.json` + (records/raw)
- 출력: `validation_report.json` — check별 pass/fail + reason, overall
- 검사 항목(§5 상세).

### Step 6. Repair Loop (LLM) — F4b
- 입력: 실패한 fact + validator 피드백(error/reason/suggestion)
- 출력: 교정된 `facts.json`
- 종료 조건: 전부 pass 또는 최대 반복 횟수 도달(설정).

### Step 7. Fact Store
- 모든 문서의 fact를 한곳에 적재(메모리/JSON). 이후 Comparator의 입력.

### Step 8. Comparator (fact ↔ fact)
- §6 비교 설계 참조. 출력: `comparison_result.json`.

### Step 9. Report Generator
- `comparison_result.json` → 사람이 보는 리포트. `report/`의 markdown 렌더 확장.

---

## 5. 검증기(Validator) + Repair Loop 설계

> 🔎 **F4 는 두 하위 단계로 분할한다 (2026-07-02 결정).**
> - **F4a — 결정적 Validator (코드, 선행)**: §5.1 은 실패 데이터 없이 확정 가능한 수학적/구조적 불변식이므로 먼저 구현한다. Phase F3.5 라이브 검증(§9)의 **계측 도구**를 겸한다.
> - **F4b — Repair Loop (LLM, 후행)**: repair 프롬프트는 실제 모델의 최빈 실패 모드에 맞춰야 효과가 있다. F4a 를 라이브 facts 에 돌린 `validation_report` 의 **실패 분포를 본 뒤** 설계한다. §5.2 예시는 후보일 뿐이며 분포 확인 전에 확정하지 않는다.

### 5.1 검사 항목
- **Header 검증**: header row 아래 data row 존재? header는 문자열 비율 높은가? merged cell이 상위 header로 해석됐는가?
- **정량규격 검증**: `lower <= target <= upper`? `spec_expression`이 lower/upper와 일치(예 `25+-30 → -5..55`)? 수치 있는데 unit 비었나? 숫자형 컬럼에 문자열 과다?
- **Source 검증**: fact 값이 raw json에 실제 존재? source cell/block/shape 연결? `evidence_text`와 fact 값 일치?
  - `evidence_text` 실재 검사는 naive substring 금지 — LLM 이 공백/개행/셀 구분자를 바꿔 옮기는 경향이 있으므로 **공백 축약·정규화 후 부분일치**로 판정한다. 실패해도 fact 를 즉시 버리지 않고 `low_confidence` 태깅으로 사람 검수 대상에 남긴다(§6.2 `unknown` 연동).

### 5.2 Repair 예시
```
validator: { "error": "entity_name_too_broad",
             "reason": "Column D=중분류, E=소분류. 가장 구체적 비교대상은 E의 충전환경온도",
             "suggestion": "entity_name을 충전환경온도로 수정" }
→ LLM: { "entity_name": "충전환경온도",
         "entity_path": ["기본사양","기본사양","충전환경온도"] }
```

---

## 6. fact 비교(Comparator) 설계

### 6.1 후보 검색 (entity 중심 다중 검색)
fact의 `entity_name`/`search_text`로 후보 fact를 찾는다. 검색어 확장 예:
`충전환경온도 / 충전 환경 온도 / charging temperature / -5 / 25 / 55 / ℃ / 도씨`.
방식: entity name 매칭 + keyword + embedding + (선택)LLM rerank. — 현행 `similarity/`의 임베딩·BM25를 **fact 단위로 재사용** 가능.

### 6.2 fact 간 비교 결과
```json
{ "result": "mismatch", "mismatch_type": "upper_limit",
  "entity_name": "충전환경온도",
  "excel_value": {"upper_limit": 55, "unit": "도씨"},
  "ppt_value":   {"upper_limit": 50, "unit": "℃"},
  "excel_evidence": {"evidence_text": "충전환경온도 | 상한치 55 | 도씨",
                     "source": {"sheet": "규격", "row": 12, "cell_range": "B12:F12"}},
  "ppt_evidence":   {"evidence_text": "충전환경온도: -5~50℃",
                     "source": {"slide_no": 3, "shape_id": 7}},
  "reason": "동일 항목의 상한치가 Excel 55도씨, PPT 50℃로 불일치",
  "recommended_action": "PPT 상한치 50℃가 최신 기준인지 확인 필요" }
```
- `match` / `mismatch(타입)` / `missing`(한쪽에만 존재) / **`unknown`(판단보류)** 4분류 + 단위 정규화 체크. (2026-07-02: `unknown` 추가)
- **`unknown` 판정 조건**: 후보 fact 는 찾았으나 attributes 키가 겹치지 않음 · 단위가 등가 사전에 없는 조합 · 매칭 스코어가 경계값 · fact `confidence` 낮음(F4a `low_confidence` 태깅 포함). 현행 RAG 경로의 판단보류 원칙(`comparison/prompts.py` 의 `unknown`: 확신이 없으면 보류하고 이유를 설명)을 fact 경로에서도 1급 상태로 유지한다.
- **양측 evidence 인용 필수**: 모든 결과는 기준/대상 fact 의 `evidence_text`+`source` 를 나란히 실어, 사람이 원문 대조로 검수(할루시네이션 확인)할 수 있게 한다 — RAG 경로의 evidence 인용 원칙 승계. fact 는 양쪽 모두 F3 에서 코드 검증된 source 를 갖고 있으므로 RAG 보다 검수 속성이 강화된다.

---

## 7. 중간 산출물 (반드시 저장)

| 파일 | 생성 단계 | 용도 |
|---|---|---|
| `physical_raw.json` | Raw Extractor | 원본 물리 구조(추적 기준점) |
| `compact_raw.json` | Raw Compactor | LLM 입력 |
| `document_profile.json` | Profiler | 문서 목적/구조 |
| `table_profile.json` | Schema Inducer | header/row_grain |
| `column_schema.json` | Schema Inducer | 컬럼 semantic_role |
| `records.json` | Record Normalizer | 행 정규화 |
| `facts.json` | Fact Extractor | 비교 단위 |
| `validation_report.json` | Validator(F4a) | 검사별 pass/fail |
| `comparison_result.json` | Comparator(F5) | 최종 비교(양측 evidence 포함) |
| `run_stats.json` | 파이프라인 | 문서별 계측(호출/드롭/커버리지) |

저장 위치 제안: `artifacts/<문서명>/<단계>.json` (설정으로 on/off).

---

## 8. 현행 방식과 공존 + 비교 하니스

### 8.1 엔진 선택
- 신규 진입점: `contentcompare/fact/pipeline.py`의 `FactPipeline`(현행 `ComparePipeline`과 별개).
- 선택 수단(택1, 구현 시 결정):
  - CLI: `contentcompare --engine fact|rag ...` (기본 `rag` = 현행 유지)
  - config: `engine: rag | fact`
- 두 엔진 모두 **공통 결과 인터페이스**로 수렴시켜 리포트/Streamlit이 동일하게 렌더.

### 8.2 비교(벤치마크) 하니스
- `scripts/compare_engines.py`(신규): 같은 입력에 rag·fact 두 엔진을 모두 실행하고
  항목별 verdict 차이, 누락/추가, 소요시간·LLM 호출수를 표로 출력 → §1 비교표를 **실측치로 채운다**.

---

## 9. 구현 Phase 로드맵

> 각 Phase 종료 시: 단위테스트 통과 + 중간 산출물 샘플 첨부. 현행 코드 무수정 원칙 유지.

- **Phase F0 — 기반 정비** ✅ **완료(2026-06-29)** ([상세 설계: `FACT_F0_DESIGN.md`](FACT_F0_DESIGN.md))
  - PPT Raw Extractor (`raw/ppt_raw.py` + `RawPptDocument`/`compact` 분기). slide_no/shape/position/style/note 포함(spec 5.3).
  - `fact/` 패키지 골격(`FactPipeline`/`ArtifactStore`/`make_pipeline`) + 엔진 선택 스위치 CLI `--engine`(§8.1) + artifacts 저장 유틸.
  - 실문서(xlsx/pptx/docx) 라이브 검증 완료. 차트/이미지 제외(결정 #6), 노트는 본문 placeholder만.
- **Phase F1 — Schema Induction (LLM)** ✅ **완료(2026-06-29)** ([상세 설계: `FACT_F1_DESIGN.md`](FACT_F1_DESIGN.md))
  - Document Profiler, Table Profile, Column Schema(semantic_role 사전 포함). `header_detect.py` 로직 흡수·확장.
  - `LlmRunner`(호출 예산·JSON 재시도) + ArtifactStore 캐싱(재실행 0비용). Excel 표 중심(Word/PPT 는 profiler 만).
  - 라이브(gemma4:12b) 검증 완료. 검증: 헤더/스키마 sanity check.
- **Phase F2 — Record Normalizer** ✅ **완료(2026-06-30)** ([상세 설계: `FACT_F2_DESIGN.md`](FACT_F2_DESIGN.md))
  - Excel primary 시트의 데이터 행을 LLM 이 의미 정규화 → `records.json`. 행 배치(`record_batch_rows`=30)+carry-over, source 좌표는 코드가 채움.
  - 미구현 경계가 F3 로 이동. Word/PPT 는 F3 에서 블록→fact 직행.
- **Phase F3 — Fact Extractor** → `facts.json` (Excel/Word/PPT 공통 schema). **핵심 마일스톤.** ✅ **완료(2026-08-03 라이브 검증)** ([상세 설계: `FACT_F3_DESIGN.md`](FACT_F3_DESIGN.md))
  - Excel: F2 `records` → `facts` **코드 결정적 변환**(무 LLM). Word/PPT: `compact_raw` 블록/도형 → `facts` **LLM 추출**(F1·F2 건너뜀).
  - 공통 스키마(entity_name/entity_path/attributes{value,unit}/source/evidence_text). `source_ids` 는 코드가 배치 실제 id 와 대조 검증(할루시네이션 방지).
  - 미구현 경계가 **F4** 로 이동. 라이브(gemma4:12b) 검증은 F2 `records` 라이브 검증과 함께 대기.
- **Phase F3.5 — 라이브 검증 + 골든셋 + 매칭 spike** ✅ **완료(2026-08-03)** ([결과: `FACT_F3_5_LIVE_REPORT.md`](FACT_F3_5_LIVE_REPORT.md))
  - **에러 격리**: `FactPipeline.run` 에 문서 단위 try/except + summary `status`/`error`/`stats`, CLI 문서별 성공/실패 표·종료코드. 격리 범위는 `Exception` 으로 넓혔다 — 실측 최빈 실패가 **COM 예외**라 로드맵의 `LlmBudgetExceeded`/`ValueError` 만으로는 목적을 달성하지 못했다.
  - **계측**: `LlmRunner`(호출/재시도/파싱실패), records(`rows_in`/`records_out`/좌표 미해결), facts(사유별 드롭 + **블록 커버리지**) → 문서별 `run_stats.json`. 커버리지는 실측 중 추가했다 — 드롭 카운터가 0인데 fact 가 사라지는 무증상 손실(LLM 이 애초에 안 뽑은 블록)이 관찰됐기 때문.
  - **F2/F3 라이브 검증 완료**: `자표준문서.xlsx`(20행) + 합성 대상 docx/pptx 로 3경로 전부 성공. 블로커였던 **Ollama 컨텍스트 소진 → 빈 응답**은 `llm.ollama.num_ctx`/`think` + 원인 설명 에러로 해결.
  - **골든셋 27항목**(match 17/mismatch 3/missing 6/unknown 1) — `golden/자표준_골든셋.jsonl`. 대상 문서와 정답을 `scripts/make_synthetic_targets.py` 의 **같은 CASES 테이블**에서 생성해 드리프트를 차단. F6 벤치마크에 그대로 재사용.
  - **F5 매칭 spike 실측**(`scripts/spike_fact_match.py`): entity_name 완전일치 Word 50%/PPT 15%, BM25 recall@1 100%/80%, **임베딩 100%/100%**, RRF 단순융합은 이득 없음, 대상에 없는 항목은 **3/3 오매칭**. → F5 는 **임베딩 필수 + 점수 임계값 필수**로 확정(리포트 §6).
- **Phase F4a — Rule Validator** ✅ **완료(2026-08-03)** — `fact/validator.py`.
  검사 6종(`quant_bounds`/`unit_missing`/`evidence_missing`/`source_unresolvable`/`no_attributes`/`role_duplicated`),
  `error` 는 버리지 않고 `low_confidence` 로 표시해 F5 의 `unknown` 근거로 넘긴다.
  실측: 기준 문서 20 fact 에 error 0 · warn 20(단위 없음 16, 속성 없음 3, 역할 중복 1).
- **Phase F4b — Repair Loop** ⏸ **보류** — F3.5·F4a 실측에서 JSON 준수도가 양호해(`parse_failures=0`)
  교정 루프의 우선순위가 낮다. 착수 여부는 §1.1.1 의 오답 유형을 보고 판단한다(§7 참고).
- **Phase F5 — Fact Store + Matcher + Comparator** ✅ **완료(2026-08-03)** → `comparison_result.json`.
  **하이브리드 판정**: 코드가 값·단위를 결정적으로 대조하고 애매한 것만 LLM 에 위임(실측 위임률 30%).
  검색은 exact → 임베딩(임계 `match_min_score`) — spike 실측대로 BM25 는 폴백으로만 둔다.
- **Phase F6 — Report + 벤치마크 하니스** ✅ **완료(2026-08-03)** —
  `report/fact_report.py`(양측 원문+좌표 인용) + `scripts/compare_engines.py`(§1.1.1 실측).
- **Phase F7 — 개념 그래프** ✅ **완료(2026-08-04)** ([상세 설계: `FACT_F7_DESIGN.md`](FACT_F7_DESIGN.md))
  F5 의 유사도 임계값 매칭을 개념 그래프 조회로 대체 — 코사인 점수는 "무관함"을 표현할 수
  없다는 실측(§F3.5 `FACT_F3_5_LIVE_REPORT.md` §9.4)에 대한 해법. 후보 쌍(임베딩/BM25) →
  온톨로지 조회(`knowledge/ontology.yaml`) → LLM 판정(`same_as`/`differs_by`/`unknown`) →
  근거 검증·병합 순으로 동작하며, `same_as` 로 이어지지 않으면 비교하지 않는다. 라이브
  재검증: `FACT_F3_5_LIVE_REPORT.md` §10(2026-08-03 오매칭 3쌍이 재현되지 않음을 확인,
  `concept_recall_min` 민감도 등 남은 한계도 §10.2 에 기록).

의존 순서: F0 → F1 → F2 → F3 → **F3.5** → F4a → F5 → F6 → **F7** → (F4b 보류).

---

## 10. 결정 사항 (2026-06-28 확정)

| # | 질문 | 결정 | 근거/메모 |
|---|------|------|-----------|
| 1 | 엔진 선택 방식 | **CLI `--engine fact\|rag` (기본 `rag`)** | 기존 사용성 보존, 명시적 옵트인, 벤치마크 하니스와 정합 |
| 2 | LLM 비용 통제 | **단계별 산출물 캐싱 + 문서당 호출 예산** | profile/schema/record/fact를 파일해시 캐싱(재실행 0비용), 현행 `CachedEmbedder` 패턴 재사용 |
| 3 | semantic_role 사전 범위 | **정량 규격(하한/중심/상한·단위) 중심으로 시작, 도메인 확장 여지** | 초기 스코프 최소화, 사전은 확장 가능한 매핑 테이블로 |
| 4 | fact 매칭 방향 | **엑셀 기준 1:N (점진 전환), N:N 확장 여지 설계** | 현행과 결과 대응이 쉬워 비교가 명확, Fact Store/Comparator는 N:N 가능하게 추상화 |
| 5 | artifacts 보존 정책 | **항상 저장 (`artifacts/<문서>/<단계>.json`)** | 추적·오류원인 분석이 본 설계의 핵심 가치. 용량은 `.gitignore`로 관리 |
| 6 | PPT 차트/이미지 텍스트 | **초기 제외 (텍스트박스/표/스피커노트만)** | OCR/차트데이터 추출은 후순위 Phase로 분리 |
| 7 | agent 프레임워크(LangGraph 등) 도입 여부 | **도입 안 함 (2026-07-02)** | 워크플로우가 정적 DAG(분기=doc_type 1개)이고 유일한 반복(F4b repair)은 유계(`max_repair_iters`) for-loop 로 충분 — 프레임워크의 그래프/상태 보일러플레이트가 그 루프보다 큼. ArtifactStore 지문 캐싱이 이미 체크포인트/재개 역할(산출물이 사람이 읽는 순수 JSON = 결정 #5 의 추적성에 더 부합). 소형 로컬 LLM 은 agentic planning/tool-calling 이 약점 — "LLM 은 단일 목적 JSON 산출만, 제어 흐름은 100% 코드" 원칙 유지. agent 루프의 비결정적 호출 수는 문서당 예산(결정 #2)·지문 캐싱과 상충, 의존성 무게는 코어 최소 정책과 상충. **재평가 트리거**: (a) LLM 판단 의존 분기가 3~4개를 넘어 조합이 문서마다 달라질 때 (b) F4b 구현 후 repair 성공률이 낮아 fact 별 다단계·다전략 repair 가 실측으로 필요할 때 (c) tool-calling 신뢰 가능한 대형 모델로 이전될 때 — 충족 시에도 명시적 상태기계 코드 우선, 프레임워크는 그 다음 |

> 위 결정에 따라 §6(1:N 우선·N:N 추상화), §7(항상 저장), §8.1(CLI `--engine`)을 적용한다.
> 변경 시 본 표를 갱신한다.
> 2026-07-02 설계 검토 보완: 결정 #7 추가, §5 F4a/F4b 분할, §6.2 `unknown`+양측 evidence 인용, §9 Phase F3.5 신설.

---

## 11. 한 줄 요약

> 현행은 "임베딩으로 유사 문구를 찾아 LLM이 비교"하는 RAG 방식(보존)이고,
> 신규는 "각 문서를 LLM이 스스로 구조화하여 공통 fact schema로 정규화한 뒤
> fact 단위로 정합성을 검증"하는 방식이다. 두 방식을 선택·비교 가능하게 공존시킨다.

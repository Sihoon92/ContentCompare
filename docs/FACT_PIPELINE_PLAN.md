# Fact 기반 비교 파이프라인 구현 계획 (Fact Pipeline Plan)

> 작성일: 2026-06-28
> 상태: **계획(미착수)** — 본 문서는 "무엇을 만들지"를 고정하는 로드맵이다.
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
| 검증 | JSON 파싱 실패 시 1회 재요청 | **rule validator + repair loop** |
| LLM 호출 수 | ≈ 기준 행 수 × 1 | 문서당 다단계(profiler/schema/record/fact/repair) → **대폭 증가** |
| 비용 / 속도 | 낮음 / 빠름 | 높음 / 느림 |
| 중간 산출물 | 없음(리포트만) | 9종 JSON(§7) — 디버깅·개선 가능 |
| 구현 난이도 | **완료** | 높음(다단계 + 검증 루프) |
| 적합 상황 | 빠른 1:N 항목 존재 확인, 비용 민감 | 양식 상이 문서의 **정밀 정합성 검증**, 추적성 요구 |

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
   ↓ Document Profiler (LLM)                   ❌ 신규
document_profile.json
   ↓ Schema Inducer (LLM)                      🟡 헤더행 추정만 존재, 나머지 신규
table_profile.json + column_schema.json
   ↓ Record Normalizer (LLM)                   ❌ 신규
records.json
   ↓ Fact Extractor (LLM)                      ❌ 신규 (핵심)
facts.json
   ↓ Rule Validator (코드)                     ❌ 신규
validation_report.json
   ↓ Repair Loop (LLM)                         ❌ 신규
(facts.json 교정)
   ↓ Fact Store                               ❌ 신규
   ↓ Comparator (fact↔fact)                   🟡 다른 방식 존재, fact용 신규
comparison_result.json
   ↓ Report Generator                          ✅ 부분 재사용
[Output] 검토 리포트
```

상태 범례: ✅ 있음(재사용) · 🟡 부분 구현 · ❌ 신규.

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

### Step 5. Rule Validator (코드)
- 입력: `facts.json` + (records/raw)
- 출력: `validation_report.json` — check별 pass/fail + reason, overall
- 검사 항목(§5 상세).

### Step 6. Repair Loop (LLM)
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

### 5.1 검사 항목
- **Header 검증**: header row 아래 data row 존재? header는 문자열 비율 높은가? merged cell이 상위 header로 해석됐는가?
- **정량규격 검증**: `lower <= target <= upper`? `spec_expression`이 lower/upper와 일치(예 `25+-30 → -5..55`)? 수치 있는데 unit 비었나? 숫자형 컬럼에 문자열 과다?
- **Source 검증**: fact 값이 raw json에 실제 존재? source cell/block/shape 연결? `evidence_text`와 fact 값 일치?

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
  "reason": "동일 항목의 상한치가 Excel 55도씨, PPT 50℃로 불일치",
  "recommended_action": "PPT 상한치 50℃가 최신 기준인지 확인 필요" }
```
- `match` / `mismatch(타입)` / `missing`(한쪽에만 존재) 3분류 + 단위 정규화 체크.

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
| `validation_report.json` | Validator | 검증 결과 |
| `comparison_result.json` | Comparator | 최종 비교 |

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
- **Phase F3 — Fact Extractor** → `facts.json` (Excel/Word/PPT 공통 schema). **핵심 마일스톤.**
- **Phase F4 — Validator + Repair Loop** (정량/단위/source 검사, 재교정).
- **Phase F5 — Fact Store + Fact Comparator** → `comparison_result.json` (match/mismatch/missing).
- **Phase F6 — Report + 벤치마크 하니스** (fact 결과 렌더 + `compare_engines.py` 실측).

의존 순서: F0 → F1 → F2 → F3 → F4 → F5 → F6.

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

> 위 결정에 따라 §6(1:N 우선·N:N 추상화), §7(항상 저장), §8.1(CLI `--engine`)을 적용한다.
> 변경 시 본 표를 갱신한다.

---

## 11. 한 줄 요약

> 현행은 "임베딩으로 유사 문구를 찾아 LLM이 비교"하는 RAG 방식(보존)이고,
> 신규는 "각 문서를 LLM이 스스로 구조화하여 공통 fact schema로 정규화한 뒤
> fact 단위로 정합성을 검증"하는 방식이다. 두 방식을 선택·비교 가능하게 공존시킨다.

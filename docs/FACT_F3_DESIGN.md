# Phase F3 상세 설계 — Fact Extractor (핵심 마일스톤)

> 작성일: 2026-07-01
> 상태: **설계→구현** — F3 의 파일·JSON 스키마·프롬프트·테스트를 고정한다.
> 상위 계획: [`FACT_PIPELINE_PLAN.md`](FACT_PIPELINE_PLAN.md) (§4 Step 4, §9 Phase F3)
> 선행: [`FACT_F2_DESIGN.md`](FACT_F2_DESIGN.md) (Excel `records.json` 완료), [`FACT_F1_DESIGN.md`](FACT_F1_DESIGN.md) (profile/schema), [`FACT_F0_DESIGN.md`](FACT_F0_DESIGN.md) (raw/compact — Excel/Word/PPT)
>
> ⚠️ **개정(2026-07-01)**: Excel 경로의 `record.quantitative_spec`/`ROLE_TO_ATTR` 개별 매핑은 폐기되고, `Record.attributes` 를 **그대로 통과(pass-through)** 하도록 단순화되었다(규격표 fact 출력은 불변, 일반 속성표 무손실) — [`FACT_ATTR_GENERALIZATION_DESIGN.md`](FACT_ATTR_GENERALIZATION_DESIGN.md) 참고.

---

## 0. F3 범위와 목표

F3 은 **fact 파이프라인의 핵심 마일스톤**이다. 지금까지 문서마다 다른 산출물
(Excel=`records.json`, Word/PPT=`compact_raw.json` 만)을 냈지만, F3 은 **모든 문서를
공통 `facts.json` schema 로 정규화**한다. 이 지점부터 Excel/Word/PPT 가 같은 fact 로
모이므로, 이후 비교(F4 Validator ~ F6 Report)가 문서 타입에 무관하게 단순해진다.

핵심 발상(계획 원칙 2): **비교 단위는 chunk 가 아니라 fact**다. 하나의 fact 는
`entity_name`(무엇에 대한) + `attributes{name:{value,unit}}`(값·단위) + `source`
+ `evidence_text`(추적 근거)로 구성된다.

### In-scope
1. **Fact 데이터 모델** — `Fact`/`Attribute`/`FactSet` (`to_dict`/`from_dict`/`from_llm`,
   F1/F2 모델 패턴 재사용).
2. **Excel 경로 = 코드 결정적 변환(LLM 미사용)** — `records.json` → `facts.json`.
   F2 가 이미 LLM 으로 의미 정규화했으므로 규칙 매핑만 한다(추가 LLM 비용 0, 할루시네이션 0).
3. **Word/PPT 경로 = LLM 추출** — `compact_raw` 의 블록/도형/노트를 배치로 LLM 에 투입해
   fact 직접 추출(F1·F2 를 건너뛴다: 자유 텍스트라 column_schema 가 없음, 결정 #4).
4. **source 무결성** — Excel 은 record 좌표 승계, Word/PPT 는 LLM 이 참조한 블록/도형
   id 를 **코드가 입력 배치와 대조 검증**해 존재하는 id 만 남긴다(할루시네이션 방지).
5. **`facts.json` 저장** — `store.save("facts", ...)`, 캐싱(재실행 0비용).

### LLM 의 실제 역할 (Word/PPT 에서만)
- 흩어진 블록(본문 + 스피커노트, 표 셀 + 주석)을 **하나의 fact 로 병합**(계획 §1.2 예시).
- 자유 문장에서 `entity_name` 과 정량/정성 `attributes` 를 뽑아 표준 어휘로 명명.
- `evidence_text` 는 입력 raw 에 실제 있는 문구만(지어내기 금지).

### Out-of-scope (F4+)
- **Rule Validator(F4)·Repair Loop(F4)·Fact Store/Comparator(F5)·Report(F6)**.
- **단위 등가(도씨 ≡ ℃)·표현식 해석(`25±30` → lower/upper)·정규화는 F3 가 하지 않는다.**
  F3 는 값을 **가공 없이** attribute 로 매핑만 한다. 등가/검증은 F4, 교차 비교는 F5.
  (F2 결정 #5 와 일관 — 각 단계는 자기 책임만.)
- `search_text` 는 후보 검색용 문자열 **생성**만 하고, 실제 검색은 F5.

### 제약
- 🔒 RAG 무수정. 신규 코드는 `fact/` 에만. `readers/`·`similarity/`·`comparison/`
  ·`pipeline.py`(RAG) 변경 없음.
- LLM 없이 테스트: Excel 경로는 순수 코드라 LLM 불필요, Word/PPT 경로는 **chat 주입**
  (FakeLLM)으로 단위테스트(CLAUDE.md 규칙). COM/네트워크 불필요.
- **다중 시트/다중 표**: Excel 은 F2 records(primary 시트)에 종속. Word/PPT 는 전체
  블록/슬라이드 대상.

---

## 1. 신규/변경 파일

| 파일 | 구분 | 내용 |
|---|---|---|
| `fact/fact_models.py` | ✅ 신규 | `Attribute`/`Fact`/`FactSet` dataclass (`to_dict`/`from_dict`/`from_llm`) |
| `fact/fact_types.py` | ✅ 신규 | `FACT_TYPES` 어휘 + `ROLE_TO_ATTR`(semantic_role → attribute 이름) 매핑 + `normalize_fact_type` |
| `fact/fact_extractor.py` | ✅ 신규 | `extract_facts(compact, *, records=None, profile=None, runner=None, store=None, batch_blocks=20)` — doc_type 분기(Excel 코드 / Word·PPT LLM) |
| `fact/prompts.py` | 🔧 수정 | `FACT_SYSTEM` + `build_fact_user(batch, doc_type, profile)` + `FACT_VERSION` |
| `fact/pipeline.py` | 🔧 수정 | records(Excel)/profile(Word·PPT) 뒤 `extract_facts` 연결, 미구현 경계 → **F4** |
| `fact/__init__.py` | 🔧 수정 | `Attribute`/`Fact`/`FactSet`/`extract_facts` export |
| `config.py` `FactConfig` | 🔧 수정 | `fact_batch_blocks: int = 20` 추가 |
| `config/config.example.yaml` | 🔧 수정 | fact 섹션에 `fact_batch_blocks` 주석 1줄 |
| `tests/test_fact_fact_models.py` | ✅ 신규 | `from_llm` 관대 처리/직렬화 왕복/attribute |
| `tests/test_fact_extractor.py` | ✅ 신규 | Excel 코드 매핑(무 LLM) + Word/PPT FakeLLM 배치·source 검증·캐시 |
| `tests/test_fact_pipeline_smoke.py` | 🔧 수정 | excel/word/ppt 모두 `facts` artifact 생성, 경계 F4 |

> 신규 의존성 없음. chat 클라이언트·`LlmRunner`·`ArtifactStore` 는 F1/F2 그대로 재사용.

---

## 2. facts.json 스키마

계획 §4 Step 4(`FACT_PIPELINE_PLAN.md:148`)의 필드를 그대로 따른다.

```json
{
  "location": "sheet=StandardList",
  "facts": [
    {
      "fact_id": "fact-row-4",
      "fact_type": "quantitative_spec",
      "entity_name": "충전환경온도",
      "entity_path": ["기본사양", "충전", "충전환경온도"],
      "attributes": {
        "lower_limit":  {"value": -5, "unit": "℃"},
        "target_value": {"value": 25, "unit": "℃"},
        "upper_limit":  {"value": 55, "unit": "℃"}
      },
      "search_text": "충전환경온도 기본사양 충전 -5 25 55 ℃",
      "source": {"doc_type": "excel", "sheet": "StandardList", "row": 4, "cell_range": "D4:I4"},
      "evidence_text": "충전환경온도 -5 25 55 ℃",
      "confidence": 0.95
    }
  ]
}
```

Word/PPT fact 예 (본문+주석 병합, 계획 §1.2):

```json
{
  "fact_id": "fact-ppt-s3-1",
  "fact_type": "quantitative_spec",
  "entity_name": "충전환경온도",
  "entity_path": ["충전환경온도"],
  "attributes": {
    "lower_limit": {"value": -5, "unit": "℃"},
    "upper_limit": {"value": 55, "unit": "℃"},
    "target_value": {"value": 25, "unit": "℃"}
  },
  "search_text": "충전환경온도 -5~55℃ 중심치 25℃ 0.1C 4.55V",
  "source": {"doc_type": "ppt", "slide_no": 3, "shape_ids": ["sh5"], "from_notes": true},
  "evidence_text": "충전환경온도: -5~55℃, 중심치 25℃ (주석: 0.1C, 4.55V 조건 기준)",
  "confidence": 0.8
}
```

- `fact_id` — Excel 은 `f"fact-{record_id}"`(예 `fact-row-4`). Word/PPT 는
  `f"fact-{doc_slug}-{seq}"` 를 코드가 부여(LLM id 미신뢰).
- `fact_type` — `fact_types.FACT_TYPES` 어휘에서만(§10 결정 F3-5). 미허용 → `descriptive`.
- `entity_name` — 비교의 축. Excel = record `entity.display_name`. Word/PPT = LLM.
- `entity_path[]` — 상위 맥락. Excel = record `entity.path`. Word/PPT = LLM(없으면 `[entity_name]`).
- `attributes{name:{value,unit}}` — **가공 없는** 값+단위. name 은 표준 어휘 우선
  (`fact_types.ROLE_TO_ATTR`). 값이 없는 속성은 키 자체를 생략(빈 fact 방지).
- `search_text` — `entity_name` + `entity_path` + attribute 값/단위를 공백 결합한 검색용
  문자열(F5 후보 검색용, 코드가 조립).
- `source` — `doc_type` 별 위치(§4.3). 코드가 채우거나 검증한다.
- `evidence_text` — raw 에 실제 존재하는 문구만. F4 source 검증과 연동.
- `confidence` — 0~1. Excel 은 record confidence 승계, Word/PPT 는 LLM.

> dataclass 는 F1/F2 와 동일하게 `to_dict`/`from_dict`/`from_llm`(LLM 원본 키
> 누락·오타·미허용 값에 관대) 제공.

---

## 3. fact_models (`fact/fact_models.py`)

```python
@dataclass
class Attribute:
    value: Any = None      # number | str | None (가공 안 함)
    unit: str = ""

@dataclass
class Fact:
    fact_id: str = ""
    fact_type: str = "descriptive"
    entity_name: str = ""
    entity_path: list[str] = field(default_factory=list)
    attributes: dict[str, Attribute] = field(default_factory=dict)
    search_text: str = ""
    source: dict[str, Any] = field(default_factory=dict)   # doc_type 별 locator(§4.3)
    evidence_text: str = ""
    confidence: float = 0.0

@dataclass
class FactSet:
    location: str = ""
    facts: list[Fact] = field(default_factory=list)
```

- `from_llm` 은 LLM dict 에서 관대 추출: `attributes` 는 `{name: {value, unit}}` 형태만
  받고, 값이 `{value,unit}` dict 가 아니면 `Attribute(value=원값)` 로 보정. 빈 속성 제거.
- `fact_type` 은 `normalize_fact_type()` 로 어휘 밖 값을 `descriptive` 로 강등.
- `entity_path` 미지정 시 `[entity_name]`(비어있지 않으면).
- `source`/`fact_id`/`search_text` 는 **from_llm 단계에서 신뢰하지 않는다** — 추출기
  코드가 §4 에서 최종 결정/덮어쓴다.

---

## 4. fact_extractor (`fact/fact_extractor.py`)

```python
def extract_facts(
    compact: dict,
    *,
    records: Optional[RecordSet] = None,      # Excel: F2 산출
    profile: Optional[DocumentProfile] = None, # Word/PPT 프롬프트 맥락
    runner: Optional[LlmRunner] = None,        # Word/PPT LLM 호출
    store: Optional[ArtifactStore] = None,
    batch_blocks: int = 20,
) -> FactSet:
    doc_type = compact.get("doc_type")
    if doc_type == "excel":
        return _facts_from_records(records, store)          # 코드(무 LLM)
    return _facts_from_blocks(compact, profile, runner, store, batch_blocks)  # LLM
```

### 4.1 Excel 경로 — records → facts (코드 결정적, 무 LLM)
- 입력: F2 `RecordSet`. 각 `Record` 를 1 fact 로(1:1, 결정 #4·F3-6).
- 매핑:
  - `entity_name` = `record.entity.display_name`(빈 값이면 path 마지막).
  - `entity_path` = `record.entity.path`.
  - `attributes` = `record.quantitative_spec` 를 `ROLE_TO_ATTR` 로 전개:
    `lower`→`lower_limit`, `target`→`target_value`, `upper`→`upper_limit`(각 `{value, unit}`,
    unit 은 QuantSpec.unit 공유). `qualitative_spec` 이 있으면 `qualitative_spec`
    속성(`{value: 텍스트, unit: ""}`). `None`/빈 값 속성은 생략.
  - `fact_type` = 정량 속성 있으면 `quantitative_spec`, 정성만 있으면 `qualitative_statement`,
    둘 다 없으면 `descriptive`.
  - `search_text` = `_build_search_text(entity_name, entity_path, attributes)`.
  - `source` = `{"doc_type": "excel", **record.source.to_dict()}`(sheet/row/cell_range 승계).
  - `evidence_text`/`confidence` = record 승계. `fact_id` = `f"fact-{record.record_id}"`.
- `record.metadata` 는 비교 비대상이라 fact 로 옮기지 않는다(records.json 에 이미 보존).

### 4.2 Word/PPT 경로 — compact 블록/도형 → facts (LLM)
- 입력 단위: Word=`blocks[]`(paragraph/table), PPT=`slides[].shapes[]`(text/table)+`notes`.
- **배치**: 블록/도형을 `batch_blocks`(기본 20)씩 청크. PPT 는 슬라이드 경계를 넘지
  않게 묶고 그 슬라이드 `notes` 를 배치에 함께 제시(본문+주석 병합 유도).
  각 배치마다 `runner.complete_json(FACT_SYSTEM, build_fact_user(batch, doc_type, profile))`.
- 배치 결과(`obj["facts"]`)를 `Fact.from_llm` 으로 변환·누적. `fact_id` 는 코드가
  `f"fact-{doc_slug}-{seq}"` 로 순번 부여.
- carry-over 불필요(자유 텍스트는 상위 분류 채워내림 개념이 약함) — 대신 profile
  `main_purpose` 를 매 배치 맥락으로 제시.

### 4.3 source 무결성 (할루시네이션 방지)
- Excel: record 좌표를 그대로 승계(코드가 F2 에서 이미 채움).
- Word/PPT: LLM 이 출력한 `source_ids`(입력에 보인 블록/도형 id)를 코드가 **현재
  배치의 실제 id 집합과 교집합**만 남긴다. 존재하지 않는 id(지어낸 id)는 버린다.
  유효 id 0개면 그 fact 는 저신뢰로 표시(또는 드롭 — 결정 F3-7). 코드가 최종 `source`
  를 조립: Word `{"doc_type":"word","block_ids":[...]}`,
  PPT `{"doc_type":"ppt","slide_no":n,"shape_ids":[...],"from_notes":bool}`.

### 4.4 산출·저장·캐싱 (재실행 0비용)
- `FactSet(location=..., facts=[...])` → `store.save("facts", fact_set.to_dict())`.
- stage `facts` fingerprint =
  - Excel: `fingerprint_for(json(records.to_dict()))` — records 가 같으면 재계산 없음
    (Excel 은 무 LLM 이라 캐시는 부가 이득).
  - Word/PPT: `fingerprint_for(json(compact 블록/도형), FACT_VERSION)`.
- `store.cached_or_compute("facts", compute, fingerprint=fp)`. 예산은 기존
  `LlmRunner.max_calls`(문서당) 통제, 초과 시 `LlmBudgetExceeded`.

---

## 5. 프롬프트 요지 (`fact/prompts.py`)

Word/PPT 경로에서만 쓴다(Excel 은 코드 매핑).

- `FACT_VERSION = "fact-v1"` (입력 지문에 포함 — 변경 시 재계산).
- **FACT_SYSTEM**: "당신은 문서에서 비교 가능한 fact 를 추출하는 분석가다. 주어진 블록/
  도형(각각 id 표시)에서 entity 와 정량/정성 속성을 뽑아 JSON fact 로 만든다. 규칙:
  - 흩어진 서술(본문+주석, 표+설명)이 같은 대상이면 **하나의 fact 로 병합**한다.
  - 값·단위는 **본문에 있는 그대로** 옮긴다(단위 변환·수식 해석 금지).
  - `attributes` 이름은 가능하면 표준 어휘(`lower_limit`/`target_value`/`upper_limit`/
    `qualitative_spec`)를 쓴다.
  - `evidence_text` 는 입력에 실제 있는 문구만. 지어내지 않는다.
  - `source_ids` 에는 이 fact 의 근거가 된 블록/도형 id 만 넣는다(입력에 있는 id).
  - JSON 만 출력." + 출력 스키마(§2 facts 배열, `source_ids` 사용) 명시.
- **build_fact_user(batch, doc_type, profile)**:
  - `doc_type` 별로 블록/도형을 `id / type / text|rows` 로 나열(PPT 는 slide_no·notes 포함).
  - `profile.main_purpose` 를 "문서 맥락" 한 줄로 주입.

---

## 6. FactPipeline 연결 (`fact/pipeline.py` 수정)

```python
def _process_one(self, path):
    ...
    profile = profile_document(compact, runner, store)                 # F1
    stages = ["physical_raw", "compact_raw", "document_profile"]
    if compact.get("doc_type") == "excel":
        tp, cs = induce_schema(compact, profile, runner, store)        # F1
        stages += ["table_profile", "column_schema"]
        records = normalize_records(                                   # F2
            compact, tp, cs, runner,
            batch_rows=self.fact.record_batch_rows, store=store,
        )
        stages += ["records"]
        extract_facts(compact, records=records, store=store)           # F3 (코드)
    else:
        extract_facts(                                                 # F3 (LLM)
            compact, profile=profile, runner=runner,
            store=store, batch_blocks=self.fact.fact_batch_blocks,
        )
    stages += ["facts"]
    ...

def run(...):
    try:
        for path in docs: self._process_one(path)
        self._not_yet_implemented()   # F4(Validator)~ 미구현
    finally: close_all_office()
```

- 미구현 경계가 **F4** 로 이동(`_not_yet_implemented` 메시지 갱신:
  "Validator~Comparator 는 Phase F4~F6 에서 구현됩니다. 현재(F0~F3)는 …/facts artifacts
  저장까지 동작합니다").
- `normalize_records` 는 `RecordSet` 를 반환하지만(`record_normalizer.py:76`), 현재 F2
  연결부(`pipeline.py:107`)는 반환값을 받지 않고 버린다. F3 에서는 그 반환값을 변수로
  받아 `extract_facts(compact, records=records, ...)` 로 넘긴다(재로드·재계산 불필요).
- `--engine fact` CLI 안내 문구는 그대로 유효(artifacts 저장까지 동작).

---

## 7. config 변경 (`config.py` `FactConfig`)

```python
@dataclass
class FactConfig:
    artifacts_dir: str = "artifacts"
    save_artifacts: bool = True
    cache: bool = True
    max_llm_calls_per_doc: int = 50
    record_batch_rows: int = 30
    fact_batch_blocks: int = 20   # ✅ 신규: F3 Word/PPT fact 추출 블록 배치 크기
```

- `config/config.example.yaml` 의 fact 섹션 주석에 `fact_batch_blocks` 설명 1줄 추가.

---

## 8. 테스트 계획 (전부 COM/네트워크 불필요)

- `test_fact_fact_models.py`: `Fact.from_llm` 키 누락/타입오류 관대, `attributes` 가
  `{value,unit}` 아닌 값 보정, 빈 속성 제거, `fact_type` 강등, `entity_path` 자동 생성,
  `to_dict↔from_dict` 왕복 동일.
- `test_fact_extractor.py`:
  - **Excel(무 LLM)**: `RecordSet` 주입 → quant_spec 3필드가 `lower_limit/target_value/
    upper_limit` 로 매핑, unit 공유, qualitative 매핑, `fact_type` 판정, `search_text`
    조립, `source.doc_type=="excel"` + 좌표 승계, `fact_id=="fact-row-N"`. LLM 미호출.
  - **Word/PPT(FakeLLM)**: 배치 분할 호출·병합, `source_ids` 중 **입력에 없는 id 제거**
    (할루시네이션 방지), PPT `slide_no`/`from_notes` 채움, 캐시 히트 시 `runner.calls==0`.
  - 빈 입력/속성 없는 fact 생략.
- `test_fact_pipeline_smoke.py`(갱신): fake chat → **excel/word/ppt 모두 `facts` artifact
  생성**, 경계가 **F4** `NotImplementedError`.

**DoD**:
- [x] 신규 단위테스트 통과(fact_types/fact_models/fact_extractor/pipeline) — 2026-07-01, fact 스위트 94개 통과.
- [x] RAG 무회귀(엔진 기본 rag, 기존 스모크 유지) — RAG 스모크+engine 9개 통과.
- [x] ollama 라이브 1회(`gemma4:12b`) — **2026-08-03 완료**([결과: `FACT_F3_5_LIVE_REPORT.md`](FACT_F3_5_LIVE_REPORT.md)).
      `자표준문서.xlsx`(20행) → 20 fact, `자표준_규격서.docx` → 10 fact, `자표준_발표.pptx` → 12 fact 로
      Excel/Word/PPT 3경로 모두 schema 정합(entity/attributes/source/evidence), source 검증에서
      할루시네이션 id 0건. F2 의 미체크 라이브 DoD 도 함께 닫음.
      선행 블로커였던 **Ollama 컨텍스트 소진 → 빈 응답**은 `llm.ollama.num_ctx`/`think` 설정과
      원인 설명 에러로 해결(리포트 §2).
- [x] `FACT_PIPELINE_PLAN.md` §9 F3 진행 표기 + 미구현 경계 F4 갱신.

---

## 9. 리스크

| 항목 | 대응 |
|---|---|
| Word/PPT LLM 이 값 가공(단위 변환 등) | 프롬프트 "그대로 옮기기" 강제 + F4 source 검증(evidence_text 대조). |
| source id 할루시네이션 | 코드가 배치 실제 id 와 교집합만 채택(§4.3). 유효 0 → 저신뢰/드롭. |
| 본문+주석 병합 실패(따로 fact) | 슬라이드 단위 배치 + notes 동시 제시로 유도. 한계 시 F5 매칭에서 흡수. |
| attribute 이름 제각각(비교 저해) | 표준 어휘 강제(프롬프트) + `ROLE_TO_ATTR` 정규화. 미매칭은 원 이름 유지(F4 보정). |
| 큰 문서 토큰/예산 초과 | 블록 배치(기본 20) + `max_calls` 예산. 초과 시 `LlmBudgetExceeded` fail-fast. |
| Excel record→fact 정보손실 | 1:1 매핑·좌표/evidence 승계로 무손실. metadata 는 records.json 에 보존. |

---

## 10. 결정 사항 (2026-07-01 확정 제안)

| # | 질문 | 결정 | 근거/메모 |
|---|------|------|-----------|
| F3-1 | Excel record→fact 방식 | **코드 결정적 변환(무 LLM)** | F2 가 이미 LLM 정규화 완료. 추가 호출은 비용·할루시네이션만 늘림. |
| F3-2 | Word/PPT fact 추출 방식 | **LLM(블록/도형 배치)** | column_schema 없는 자유 텍스트. F1·F2 건너뛰고 직행(계획 §4 Step4, F1 선례). |
| F3-3 | fact schema 필드 | **계획 §4 Step4 그대로** | fact_id/fact_type/entity_name/entity_path/attributes/search_text/source/evidence_text/confidence. |
| F3-4 | attribute 이름 어휘 | **`semantic_roles` → `ROLE_TO_ATTR` 표준 매핑** | lower_limit/target_value/upper_limit/qualitative_spec. 양식 달라도 비교(결정 #3). |
| F3-5 | fact_type 어휘 | **최소 집합 시작(quantitative_spec/qualitative_statement/descriptive)** | 확장 가능. 미허용은 descriptive 강등. |
| F3-6 | granularity | **Excel 1 record=1 fact(1:1)** | 결과 대응이 쉬움(결정 #4 1:N 우선). Word/PPT 는 LLM 병합 허용. |
| F3-7 | source id 무효 처리 | **코드가 배치 id 와 교집합만; 0개면 저신뢰(또는 드롭)** | 추적 신뢰성·할루시네이션 방지(F2 §4.4 패턴). |
| F3-8 | 단위 등가/검증 | **F3 비대상(F4 로 미룸)** | F3 는 매핑만. 등가/검증 F4, 교차비교 F5(F2 결정 #5 일관). |

> 의존 순서: F0 → F1 → F2 → **F3** → F4 → F5 → F6. F3 종료 시 미구현 경계는 F4 로 이동한다.
</content>
</invoke>

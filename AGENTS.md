# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

> 코드/주석/문서가 한국어 기준입니다. 설명·리포트는 한국어로, 식별자/커맨드는 영어로 작성하세요.

## 프로젝트 개요

엑셀(기준 문서) 각 항목을 Word/PPT/Excel 등 N개의 대상 문서와 대조해 "같음/다름·출처(어디에)·사유(왜)"를 LLM 으로 판정하는 에이전트. 하이브리드 검색(임베딩+BM25 RRF)으로 후보를 찾고, 행 단위 종합 판정으로 결과를 낸다. 자세한 설계는 `docs/DESIGN.md`, 진행 현황은 `docs/IMPLEMENTATION_PLAN.md`, 사용법은 `docs/USER_GUIDE.md` 참고.

## 명령어

```bash
# 설치 (코어만: pyyaml + requests)
pip install -e .
# 환경별 추가 의존성 (조합 가능)
pip install -e ".[office,ui,dev]"   # office=xlwings/pywin32, ui=streamlit, dev=pytest
pip install -e ".[fastembed]"       # 로컬 임베딩(사내 chat 이 임베딩 미제공 시)

# 설정: 예시를 복사 후 편집 (config/config.yaml 은 .gitignore 됨)
cp config/config.example.yaml config/config.yaml

# LLM 연결만 점검 (chat 1회 + embedding 1회 실제 호출)
contentcompare --check --config config/config.yaml

# 비교 실행 (CLI)
contentcompare --config config/config.yaml \
  --reference 기준.xlsx --targets 문서A.docx 문서B.pptx --out report.md

# 웹 UI
streamlit run app/streamlit_app.py

# raw json 덤프 (문서 → physical_raw / compact_raw, COM 필요)
python scripts/dump_raw.py 문서.xlsx --compact -o out/compact.json
```

### 테스트

```bash
pytest                                              # 전체
pytest tests/test_pipeline_smoke.py                 # 파일 1개
pytest tests/test_pipeline_smoke.py::test_report_renders   # 단일 테스트
```

테스트는 `FakeLLM`/`FakeEmbedder`(예: `tests/test_pipeline_smoke.py`)로 백엔드를 대체하므로 **Office/Ollama/네트워크 없이** 모든 OS 에서 실행된다. COM 의존 코드(`readers/`, `raw/excel_raw.py`, `raw/word_raw.py`)는 실제 실행 시에만 Windows + MS Office 가 필요하다 — 새 테스트도 이 패턴(가짜 클라이언트 주입)을 따라 플랫폼 독립적으로 유지할 것.

## 아키텍처

### 비교 파이프라인 (`contentcompare/pipeline.py` — 핵심)

`ComparePipeline.run(reference, targets)` 의 5단계:
1. **읽기**: `readers.get_reader(path)` 가 확장자로 리더를 골라 문서를 `DocItem` 리스트로 변환
2. **인덱싱**: 대상 `DocItem` 들을 `chunk_items()` 로 청킹 후 `HybridIndex` 구축 (임베딩 + BM25)
3. **검색**: 기준 항목마다 `index.search(recall_k → top_k)` — RRF/cosine 융합 후 MMR 로 다양성 확보
4. **판정**: `Comparator` 가 후보들을 LLM 에 투입해 JSON verdict 파싱
5. **리포트**: `report.render_markdown()` → `--out` + `reports/` 사본 자동 저장

`run()` 은 `finally` 에서 `close_all_office()` 로 열린 COM 문서를 반드시 정리한다.

### 데이터 모델 (`models.py`)

- `DocItem`: 비교 단위(엑셀 row / 단락 / 셀 / 도형 텍스트). `text`(임베딩용) + `source_label`(사람용) + `locator`(절대 위치).
- `RecordItem(DocItem)` + `FieldClaim`: **엑셀 hybrid 분해의 핵심**. 행=검색 단위(`RecordItem`), 셀=판정 단위(`FieldClaim`). `RecordItem.fields` 가 있으면 파이프라인이 `compare_record()`(행 단위 종합 판정)로 분기, 없으면 `compare()`.
- `Verdict`: `same | different | partial | unknown | not_found`. **`unknown`(❓ 판단보류)** 은 단위 모호·지식부족으로 확신이 안 설 때 쓰는 1급 상태이며, 각 판정은 후보 원문을 그대로 인용(`evidence`)해 사람이 할루시네이션을 검수할 수 있게 한다.
- 결과: 단순 항목은 `ComparisonResult`, 엑셀 행은 `RecordResult`(`findings` = 열별 확인 내역). 둘 다 `.reference`/`.sources` 속성을 제공해 리포트·UI 가 동일하게 다룬다.

### LLM/임베딩 백엔드 (`llm/`) — 교체 가능

`factory.build_clients(config)` 가 `(chat, embed)` 튜플을 반환. `config.llm.backend`(chat) 와 `embed_backend`(비우면 chat 과 동일)를 **독립적으로** 고를 수 있어, 사내 chat 엔드포인트가 임베딩을 안 줄 때 `chat=internal` + `embed=fastembed` 처럼 분리한다.

- 백엔드: `ollama` | `internal`(requests 직접) | `langchain`(langchain-openai), 임베딩 전용: `fastembed` | `onnx`(직접 받은 ONNX 폴더)
- **프록시 정책(기획 1번)**: `internal`/`langchain` + `internal.unset_proxy=true` 면 `build_clients` 시점에 `disable_proxy()` 로 `HTTP(S)_PROXY` 를 프로세스 전역에서 영구히 비워 사내망 직결을 만든다.
- **HTTP 견고성(`llm/http.py`)**: 일시오류(연결/타임아웃/5xx)는 지수 백오프 재시도, **요청 한도(HTTP 429)는 별도 예산**으로 `rate_limit_wait`(서버 `Retry-After` 우선)만큼 대기 후 재시도.
- **e5 계열 교차언어 검색**: 검색어/본문에 다른 접두어가 필요 → `embed_query_prefix`(`query: `) / `embed_passage_prefix`(`passage: `), 미지정 시 `embed_prefix` 폴백. `config.llm.embed_prefix_for(kind)` 가 선택한다.

### 하이브리드 검색 (`similarity/`)

`HybridIndex` = 임베딩(`VectorIndex`) + `BM25` 를 `reciprocal_rank_fusion`(또는 cosine 단독)으로 융합 → `mmr_select` 로 다양성 → `per_doc_cap` 으로 한 문서 독식 방지. `CachedEmbedder` 가 `cache_dir` 에 임베딩을 디스크 캐시(모델명별)한다.

### 두 개의 추출 경로 (중요)

- **`readers/`** — 운영 경로. `DocItem` 을 만들어 `ComparePipeline` 이 사용. Excel=`xlwings`, Word/PPT=`win32com`.
- **`raw/`** — 신규 fact 경로의 입구. 문서를 *해석 없이* 관찰 가능한 물리 정보(`physical_raw` json: 셀 주소·병합·서식, Word 블록 순서, PPT 슬라이드·도형·스피커노트)로 추출 → `compact_raw` 로 압축해 LLM 입력화하는 "파일을 LLM 에 바로 주지 않는다"는 설계. Excel/Word/PPT 지원. 현행 `ComparePipeline`(RAG)과는 무관하고, **`--engine fact`(`fact/pipeline.py`)와 `scripts/dump_raw.py`** 가 사용한다.

### fact 엔진 (`fact/`, `--engine fact`)

`FactPipeline.run()` 이 문서마다 raw→compact→profile→schema(Excel)→records(Excel)→facts→검증을 돌려 `artifacts/<문서>/<단계>.json` 에 남기고, 이어서 fact 끼리 비교해 **리포트까지** 만든다(`FactRunResult`). 문서 처리는 서로 격리되어 하나가 실패해도 나머지가 계속되고, 문서별 계측이 `run_stats.json`(LLM 호출/재시도/파싱실패, record·fact 수, 드롭 사유, 블록 커버리지)에 남는다 — **오판·누락 추적이 목적이므로 이 계측을 제거하지 말 것.**

비교는 **개념 그래프(F7) → 값 대조(F5)** 2단계다(기본 `use_concept_graph: true`).

1. **F7 개념 그래프**(`concept_builder.py`/`concept_assembler.py`/`concept_models.py`): "두 fact 를 비교해도 되는가"만 답한다. 판정 규칙은 하나 — **개념이 `same_as` 로 이어져 있지 않으면 비교하지 않는다.** 유사도(임베딩/BM25)는 LLM 에 검토시킬 **후보 쌍을 좁히는 recall 용도로만** 남았고 판정에 쓰지 않는다(`concept_recall_min` 은 임계가 아니라 계산량 제한). 연결(`same_as`)은 LLM 이 제안하고 근거 인용 검증을 통과해야 성립하며, 차단(`differs_by`·모순·인용 실패)은 코드가 단독으로 한다 — **권한이 비대칭**이다. 사람이 `knowledge/ontology.yaml` 에 승격한 관계는 LLM 을 건너뛰고 영구 적용된다(recall 과 **독립적으로** 조회한다).
2. **F5 값 대조**(`fact_matcher.ConceptMatcher` → `fact_comparator.py`): 개념 그래프가 준 후보에 대해 **코드가 값·단위를 대조해 확정**하고 애매한 것만 LLM 에 위임한다. 이 순서를 뒤집지 말 것 — fact 를 `{value, unit}` 으로 정규화한 이유가 "값 비교는 코드가 한다"이다. 판정 주체는 `decided_by` 로 남는다.

`match_min_score`/`match_review_score`/`match_top_k` 는 **F7 경로에서 쓰이지 않는다**(롤백 `use_concept_graph: false` 전용). `MatchCandidate.needs_review` 는 개념 경로에서 "연결을 LLM 이 만들었는가"로 재정의된다 — 점수 경계가 아니다. 설계는 `docs/FACT_F7_DESIGN.md`. **F4b(LLM 교정 루프)만 미구현**이다.

설계·진행은 `docs/FACT_PIPELINE_PLAN.md`, 라이브 실측·한계는 `docs/FACT_F3_5_LIVE_REPORT.md`, 정답 데이터는 `golden/` 참고. 엔진 비교는 `python scripts/compare_engines.py`.

> ⚠️ Ollama 는 컨텍스트가 모자라면 오류가 아니라 **빈 응답**을 준다. fact 프롬프트는 기본 `num_ctx`(4096)를 쉽게 넘으므로 `config.llm.ollama.num_ctx`(권장 16384)를 올리고, thinking 모델이면 `think: false` 로 두는 편이 훨씬 빠르다.

### 설정 (`config.py`)

전부 `@dataclass`(`AppConfig` → `llm`/`excel`/`similarity`/`report`/`knowledge`). `AppConfig.load(path)` 가 YAML 을 읽고, 키별 의미는 `config/config.example.yaml` 의 주석이 1:1 로 설명한다. 엑셀 분해 단위는 `excel.granularity`: `row` | `field` | `hybrid`(기본, 행 검색+셀 판정).

### 도메인 지식 주입 (human-in-the-loop, 기획 5번)

`knowledge/` 의 모든 `.md` 를 `load_knowledge()` 가 합쳐 비교 프롬프트에 **항상** 참고자료로 주입(`Comparator(knowledge=...)`). 용어·동의어·표기 규칙으로 오판을 줄인다. Streamlit "📚 도메인 지식" 탭에서 편집. (단, "대상에 없는 내용을 있다고 판단"하지 않도록 프롬프트에서 한 번 더 제한.)

### 진입점

- **CLI** `cli.py`(`contentcompare` 스크립트): `--check` 연결점검 / `--reference`+`--targets` 비교.
- **Streamlit** `app/streamlit_app.py`: 사이드바=설정(백엔드/모델/검색 파라미터), 3탭=비교 실행 / 리포트 보기 / 도메인 지식. COM 은 데스크톱 세션이 필요하므로 **사용자 PC localhost** 전용. 입력은 업로드보다 **로컬 경로 직접 지정**을 권장(COM 은 실제 파일 경로 필요; 사내 보안/DRM 친화적). 비화면 로직은 `ui/runner.py` 에 분리해 streamlit 없이 단위테스트 가능.

### 로깅

`logging_setup.setup_logging()` 으로 실행 로그를 파일에 저장(파일엔 DEBUG=프롬프트/LLM 원문/HTTP 까지), `log_print()` 는 화면+로그 동시 출력. 오판 추적이 목적이므로 프롬프트·응답 로깅을 제거하지 말 것.

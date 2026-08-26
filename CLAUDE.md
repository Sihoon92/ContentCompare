# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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
- **요청 한도 대응이 두 층인 이유(`llm/ratelimit.py`)**: 위 HTTP 레벨 처리는 `requests` 를 직접 쓰는 `internal`/`ollama` 만 덮는다. `langchain` 백엔드는 SDK 가 자체 재시도를 하는데 백오프가 몇 초 수준이라 **분당 한도 회복에 못 미친다**. 그래서 `factory.build_clients` 가 `max_calls_per_minute > 0` **또는 `timeout_wait > 0`** 일 때 클라이언트를 `RateLimitedChat`/`RateLimitedEmbedder` 로 감싼다(⚠️ 후자가 빠져 있어 60초 대기 코드가 있는데도 기본 설정에서 **호출 경로에 아예 없던** 실측 결함이 있었다) — `TracedChat` 과 같은 전략이고 **추적보다 바깥**이라 대기 시간이 `duration_ms` 에 섞이지 않는다. 두 층이 겹쳐 5회×60초가 **두 겹**으로 쌓이는 것(최악 10분)을 막으려고, HTTP 레벨에서 이미 처리하는 백엔드는 `handles_rate_limit = True` 를 선언하고 래퍼가 **사후 재시도만** 건너뛴다(사전 스로틀은 적용). `RateLimiter` 는 고정 창이 아니라 **슬라이딩 창**이며(경계에서 두 배가 몰리는 것 방지) 창이 열릴 만큼만 잔다. chat 과 embed 는 같은 키를 쓰므로 서버가 합산해 세고 → **limiter 를 공유**한다(로컬 임베딩 `fastembed`/`onnx` 는 한도를 안 먹으므로 제외). 한도 예외 감지는 `openai` 를 import 하지 않고 상태코드·클래스명·메시지 마커로 **덕 타이핑**하며, 사내 게이트웨이가 429 를 안 줄 수 있어 `rate_limit_status_codes`/`rate_limit_markers` 로 넓힐 수 있다 — 첫 감지 시 예외 타입·상태코드·본문을 `log_print` 로 남기는 것이 그 조정의 근거다(60초 정지를 행으로 오해하는 것도 막는다).
- **타임아웃 대기(`llm.timeout_wait`, 기본 0=끔)**: 사내 게이트웨이가 한도 초과를 429 가 아니라 **응답을 붙들고 있는 것**으로 알리면 클라이언트에는 `APITimeoutError` 로 보이고, 그것은 `is_rate_limit` 의 세 근거(상태코드 없음·클래스명에 RateLimit 없음·메시지 `"Request timed out."`) 어디에도 안 걸린다. 그래서 `is_timeout()`(클래스명이 주(主) — `APITimeoutError`·`ReadTimeout`·`Timeout`·`TimeoutError` 가 모두 이름에 `timeout` 을 담는다)으로 **따로** 잡고 **예산도 따로** 센다(`timeout_max_retries`). 한도 마커에 `timeout` 을 끼워 넣지 말 것 — 한도는 기다리면 풀리고 생성 지연은 기다려도 안 풀려서, 뭉치면 "왜 60초를 기다렸나"를 설명할 수 없다. `handles_rate_limit` 은 **429 만** 뜻하므로 타임아웃 대기는 건너뛰지 않는다(`http.py` 의 일시오류 백오프는 2~8초라 분당 한도 회복에 못 미친다). ⚠️ **기본이 꺼짐인 이유는 대기가 SDK 자체 재시도와 곱해지기 때문이다** — timeout 120s · max_retries 3 이면 한 호출이 이미 최악 8분인데 대기 2회를 얹으면 26분이 된다. 켜면 `_warn_retry_multiplication` 이 그 산수를 그대로 출력하고 `max_retries` 를 낮추라고 안내한다. **원인 판별은 재시도 결과로 한다** — 대기 후 성공하면 한도, 계속 실패하면 생성 지연(배치 크기 문제)이고 첫 대기 때 그 안내가 함께 나간다.
- ⚠️ `log_print` 에 `level=logging.WARNING` 을 주지 말 것 — 이미 `print` 로 화면에 나가는데 CLI 의 콘솔 핸들러(기본 WARNING)가 한 번 더 찍어 **화면에 두 번** 나온다. 기본 INFO 면 파일에는 남고 콘솔에서는 걸러진다.
- **e5 계열 교차언어 검색**: 검색어/본문에 다른 접두어가 필요 → `embed_query_prefix`(`query: `) / `embed_passage_prefix`(`passage: `), 미지정 시 `embed_prefix` 폴백. `config.llm.embed_prefix_for(kind)` 가 선택한다.
- **LLM 입출력 추적(`llm/tracing.py`, 선택)**: `llm.langfuse` 에 `host`/`public_key`/`secret_key` 를 **셋 다** 채우면 켜진다(하나라도 비면 조용히 꺼짐). `build_clients` 가 chat 클라이언트를 `TracedChat` 으로 감싸는 방식이라 **fact 6단계·RAG 판정·헤더 추정이 호출부 수정 없이** 전부 잡힌다 — `comparison/`·`readers/` 가 코드 무수정 원칙이라 이 설계가 필수다. ⚠️ **래핑 조건은 추적 하나가 아니다** — 아래 실행 타임라인도 같은 래퍼를 쓰고 그쪽이 **기본 on** 이라, `build_clients` 는 Langfuse·`trace_local`·`logging.timeline` 중 **하나라도** 켜져 있으면 감싼다(셋 다 꺼야 기존과 동일 객체다). 단계 이름은 `tracing.stage()` 로 붙이고(fact 파이프라인), 없으면 호출자 모듈명으로 폴백한다. **추적 실패는 절대 실행을 막지 않는다** — 첫 실패에 경고 1회 후 no-op 으로 강등. Langfuse SDK 접촉은 `LangfuseTracer` 한 곳에 가둬 v2↔v3 교체가 이 파일 안에서 끝난다. **사내 사설 인증서**는 기본적으로 `use_os_trust_store()` 가 `truststore`(langfuse extra 에 포함)로 OS 인증서 저장소를 쓰게 해 해결한다 — Python 은 certifi 의 공인 CA 만 믿어 `CERTIFICATE_VERIFY_FAILED` 가 나지만, 브라우저로 Langfuse 웹이 열리는 PC 면 사내 루트가 이미 OS 저장소에 있다. **`SSL_CERT_FILE`/`httpx_client` 만으로는 부족하다** — httpx 는 그 환경변수를 읽지 않고 certifi 로 컨텍스트를 만들어서, SDK 가 내부에서 자기 httpx 클라이언트를 만드는 순간 우리가 넘긴 CA 를 통째로 우회한다(실측: 그래서 순수 httpx 요청은 통과하는데 `auth_check` 만 SSL 로 죽었다). `truststore` 는 `ssl.SSLContext` 자체를 갈아끼우므로 그 경로까지 덮으며, 클라이언트 **생성 전에** 주입해야 효과가 있다. `ssl_cert`(PEM 경로)는 OS 저장소에도 루트가 없는 환경의 탈출구다 — 명시하면 사람의 의도가 우선이라 truststore 대신 그 파일만 신뢰한다. `.cer` 은 DER(바이너리)인 경우가 많아 코드가 형식을 검사해 변환 명령까지 로그로 알려준다. 원인 격리는 `python scripts/langfuse_test.py`(contentcompare 를 안 타는 4단계 바이섹트, `--insecure` 로 인증서 여부 확정). ⚠️ artifacts 캐시가 적중한 단계는 LLM 을 안 불러 trace 가 없다(실행 시 안내 출력).

### 하이브리드 검색 (`similarity/`)

`HybridIndex` = 임베딩(`VectorIndex`) + `BM25` 를 `reciprocal_rank_fusion`(또는 cosine 단독)으로 융합 → `mmr_select` 로 다양성 → `per_doc_cap` 으로 한 문서 독식 방지. `CachedEmbedder` 가 `cache_dir` 에 임베딩을 디스크 캐시(모델명별)한다.

### 두 개의 추출 경로 (중요)

- **`readers/`** — 운영 경로. `DocItem` 을 만들어 `ComparePipeline` 이 사용. Excel=`xlwings`, Word/PPT=`win32com`.
- **`raw/`** — 신규 fact 경로의 입구. 문서를 *해석 없이* 관찰 가능한 물리 정보(`physical_raw` json: 셀 주소·병합·서식, Word 블록 순서, PPT 슬라이드·도형·스피커노트)로 추출 → `compact_raw` 로 압축해 LLM 입력화하는 "파일을 LLM 에 바로 주지 않는다"는 설계. Excel/Word/PPT 지원. 현행 `ComparePipeline`(RAG)과는 무관하고, **`--engine fact`(`fact/pipeline.py`)와 `scripts/dump_raw.py`** 가 사용한다.

### fact 엔진 (`fact/`, `--engine fact`)

`FactPipeline.run()` 이 문서마다 raw→compact→profile→schema(Excel)→records(Excel)→facts→검증을 돌려 `artifacts/<문서>/<단계>.json` 에 남기고, 이어서 fact 끼리 비교해 **리포트까지** 만든다(`FactRunResult`). 문서 처리는 서로 격리되어 하나가 실패해도 나머지가 계속되고, 문서별 계측이 `run_stats.json`(LLM 호출/재시도/파싱실패, record·fact 수, 드롭 사유, 블록 커버리지)에 남는다 — **오판·누락 추적이 목적이므로 이 계측을 제거하지 말 것.**

비교는 **개념 그래프(F7) → 값 대조(F5)** 2단계다(기본 `use_concept_graph: true`).

1. **F7 개념 그래프**(`concept_builder.py`/`concept_assembler.py`/`concept_models.py`): "두 fact 를 비교해도 되는가"만 답한다. 판정 규칙은 하나 — **개념이 `same_as` 로 이어져 있지 않으면 비교하지 않는다.** 유사도(임베딩/BM25)는 LLM 에 검토시킬 **후보 쌍을 좁히는 recall 용도로만** 남았고 판정에 쓰지 않는다(`concept_recall_min` 은 임계가 아니라 계산량 제한). 연결(`same_as`)은 LLM 이 제안하고 근거 인용 검증을 통과해야 성립하며, 차단(`differs_by`·모순·인용 실패)은 코드가 단독으로 한다 — **권한이 비대칭**이다. 사람이 `knowledge/ontology.yaml` 에 승격한 관계는 LLM 을 건너뛰고 영구 적용된다(recall 과 **독립적으로** 조회한다). 같은 개념의 번역어·약칭은 `aliases:` 로 묶으면 관계가 그룹 전체로 확장된다 — `differs_by` 목록에 번역어를 직접 나열하면 동의어까지 '다르다'로 선언되므로 반드시 `aliases` 를 쓸 것.
2. **F5 값 대조**(`fact_matcher.ConceptMatcher` → `fact_comparator.py`): 개념 그래프가 준 후보에 대해 **코드가 값·단위를 대조해 확정**하고 애매한 것만 LLM 에 위임한다. 이 순서를 뒤집지 말 것 — fact 를 `{value, unit}` 으로 정규화한 이유가 "값 비교는 코드가 한다"이다. 판정 주체는 `decided_by` 로 남는다.

⚠️ **비교 단계 예산 고갈은 `llm_failures` 와 갈라 센다**(`llm_budget_exceeded`). 파급 범위가 다르기 때문이다 — 파싱 실패는 그 항목 하나로 끝나지만 예산 고갈은 **그 뒤 전부**를 `unknown` 으로 쓸어간다. 한 숫자에 섞으면 "왜 갑자기 전부 보류인가"를 설명할 수 없고, 사용자는 예산이 아니라 모델을 바꾸러 간다. 그래서 개념 단계와 같은 방식으로 리포트 **맨 위에** 🚨 경고를 띄우되 조치는 다르다(`max_llm_calls_per_compare`). 아래 1:N 라우팅이 호출 수를 늘렸으므로 이 상황이 더 잘 생긴다.

**F5 다중 후보(1:N)** — 후보가 **2건 이상이면 코드가 확정하지 않고 무조건 LLM 으로 간다**(`FactComparator.finalize`). 동명·동개념 fact 는 recall 점수로 갈리지 않아 `candidates[0]` 축약이 사실상 임의 선택이기 때문이다. LLM 은 후보를 각각 대조한 `findings[]` 와 종합 `result` 하나를 내고(기준 1행 = 리포트 1줄 유지), **대표 1건은 LLM 이 아니라 코드가** 고른다(첫 mismatch → 첫 finding → `candidates[0]`). 인용이 `evidence_text` 에 없으면 드롭이 아니라 `quote_verified: false` 표시다 — 드롭하면 종합 판정이 통째로 날아간다. 단 finding 이 **전부** 드롭되면 `unknown` 으로 강등하되, 애초에 finding 이 없는 경우(정당한 `missing`)와는 구분한다. N≥2 에서 LLM 실패는 코드 판정으로 되돌아가지 않고 `unknown` 보류다 — 되돌아가면 고치려는 오판이 조용히 남는다. ⚠️ `FactMatcher` 의 **이름 완전일치 경로는 조기 종료**라 동명 대상 fact 를 1건으로 접으므로, 같은 언어 문서쌍에서는 1:N 이 애초에 생기지 않는다(설계 §13.3).

**F5 Fast Path 게이트(`fact/review_router.py`)** — 코드가 확정한 `match` 를 믿어도 되는지 채점한다. `FactComparator.compare_code()`(LLM 0회)가 만든 `ComparisonProbe` 를 `AcceptanceGate` 가 보고 `review_triggers` 를 붙이며, `finalize()` 가 최종 판정을 만든다. `compare()` 는 이 둘을 잇는 호환 래퍼라 롤백 경로는 무변경이다. 게이트를 **사후**(판정이 끝난 뒤)에 채점하지 않는 이유는 `_decide_by_llm` 이 후보를 교체할 수 있어, 사후 채점은 코드 판정 시점과 **다른 후보**를 채점하게 되기 때문이다.

**게이트는 탐지가 아니라 라우팅이다** — "후보가 2건 이상이다" 같은 셀 수 있는 사실만 확인하고 "어느 후보가 맞는가"는 판단하지 않는다. 그것은 2차 Evidence 검사의 몫이다(`docs/FACT_LINKED_GRAPH_RAG_DESIGN.md`).

⚠️ **이 단계는 비용을 줄이지 않고 늘린다.** 오늘도 코드 `match` 는 LLM 을 안 부르므로, 게이트의 실제 효과는 지금까지 조용히 확정되던 unsafe match 를 LLM 으로 보내는 것이다. 그래서 기본값이 shadow(`fact.fast_path.enforce: false`)다. 절감은 상위 설계의 Phase 2(개념 판정 LLM)와 Phase 6(Entity 별 그룹 배치)에서 나온다.

전환 비용은 **`enforce_new_llm_count`** 로 본다. `unsafe_match_rate` 를 그 용도로 읽지 말 것 — 게이트 사유 셋(`low_confidence`·`code_unknown`·`duplicate_entity_facts`)은 `finalize` 가 **이미** LLM 으로 보내는 조건(`probe.uncertain`·`code_result is None`·후보 2건 이상)과 같은 사실을 가리켜서, 켜도 늘어나지 않는다. 실측(`artifacts/자표준문서_xlsx`)에서 `unsafe_match_rate` 0.88 이 실제 순증가 10%(2건)를 3.5배 부풀렸다.

**enforce 는 켜지 않는다(2026-08-13 결정).** 위 실측에서 순증가 2건이 전부 `partial_attribute_coverage` 단독이었고, 열어 보니 **둘 다 올바른 match** 였다 — 기준 `target_value` ↔ 대상 `center_value` 로 값(25 / 43)은 같은데 속성 **이름만** 달랐다. `attribute_coverage` 가 키 겹침만 세기 때문에 생기는 오탐이다(`_compare_single_attributes` 가 이미 인정한 "키 이름은 원본 표의 열 위치에서 온다"는 문제인데, 그 예외는 양쪽 속성이 1개일 때만 적용된다). 즉 이 데이터에서 enforce 의 순효과는 정상 판정 2건을 LLM 에 보내 `unknown` 으로 뒤집힐 위험을 만드는 것뿐이다.

`attribute_coverage` 가 필요한 이유는 `_decide_by_code()` 가 **양쪽의 공통 속성만** 보기 때문이다 — 기준에 세 속성이 있고 대상에 하나뿐이어도 그 하나가 같으면 `match` 가 된다.

`match_min_score`/`match_review_score`/`match_top_k` 는 **F7 경로에서 쓰이지 않는다**(롤백 `use_concept_graph: false` 전용). `MatchCandidate.needs_review` 는 개념 경로에서 "연결을 LLM 이 만들었는가"로 재정의된다 — 점수 경계가 아니다. 설계는 `docs/FACT_F7_DESIGN.md`. **F4b(LLM 교정 루프)만 미구현**이다.

설계·진행은 `docs/FACT_PIPELINE_PLAN.md`, 라이브 실측·한계는 `docs/FACT_F3_5_LIVE_REPORT.md`, 정답 데이터는 `golden/` 참고. 엔진 비교는 `python scripts/compare_engines.py`.

### 진단 — `⚪ 대상에 없음` 의 원인 가리기

`missing` 판정의 원인은 **여섯 가지**이고 조치가 각각 다르다: ①recall 실패 ②LLM 미판정 ③근거 게이트 강등 ④F3 추출 누락 ⑤의도된 차단 ⑥F5 LLM 판정. `fact/missing_trace.py` 가 산출물만으로 이것을 가른다.

**핵심 판별**: `comparison_result.json` 의 `decided_by` 가 후보 유무를 말해 준다 — `FactComparator.compare` 는 후보가 없으면 LLM 을 부르지 않고 즉시 반환하므로 `code` 로 남고, 후보가 있는데 LLM 이 `missing` 이라 답한 경우에만 `llm` 이 된다(`_fallback` 은 `missing` 을 만들지 않는다).

분류 순서를 바꾸지 말 것 — **구체적인 것부터** 본다(`rejected` → `undecided` → `differs_by`). `differs_by` 는 후보마다 흔히 붙어서 먼저 보면 진짜 원인을 덮는다(실측 `_runs/en_word` 에서 근거 게이트 강등 8건이 전부 '차단'으로 오분류됐다).

두 진단 산출물이 ①④를 가능하게 한다. 둘 다 LLM 을 안 불러 비용이 0 이므로 **끄지 말 것**:

- `candidate_pairs.json` — 후보 랭킹 + **탈락 사유**(`cut_by`: `min_score`=임계 미달 / `top_k`=순위 밀림). 컷 당한 후보가 남지 않으면 "정답 쌍이 후보에 들어오긴 했는가"를 영영 확인할 수 없다.
- `facts_by_block.json` — 블록/행 → fact 매핑. `cited=false` 인 블록이 F3 추출 누락 후보다. **추출 결과에서 역산**하므로 캐시 히트에도 남는다.

`llm.trace_local: true` 면 LLM 프롬프트/응답 원문이 `artifacts/_traces/<실행>/` 에 남는다(`JsonlTracer`). Langfuse 와 **독립적으로** 켜지고 둘 다 켜면 `MultiTracer` 가 양쪽에 기록한다. ⚠️ 기본 off — 켜면 문서 원문이 평문으로 디스크에 남는다.

CLI: `python scripts/why_missing.py [항목명] [대상문서] [--run 라벨] [--all] [--diff 다른라벨]`.

> ⚠️ Ollama 는 컨텍스트가 모자라면 오류가 아니라 **빈 응답**을 준다. fact 프롬프트는 기본 `num_ctx`(4096)를 쉽게 넘으므로 `config.llm.ollama.num_ctx`(권장 16384)를 올리고, thinking 모델이면 `think: false` 로 두는 편이 훨씬 빠르다.

### 설정 (`config.py`)

전부 `@dataclass`(`AppConfig` → `llm`/`excel`/`similarity`/`report`/`knowledge`). `AppConfig.load(path)` 가 YAML 을 읽고, 키별 의미는 `config/config.example.yaml` 의 주석이 1:1 로 설명한다. 엑셀 분해 단위는 `excel.granularity`: `row` | `field` | `hybrid`(기본, 행 검색+셀 판정).

### 도메인 지식 주입 (human-in-the-loop, 기획 5번)

`knowledge/` 의 모든 `.md` 를 `load_knowledge()` 가 합쳐 비교 프롬프트에 **항상** 참고자료로 주입(`Comparator(knowledge=...)`). 용어·동의어·표기 규칙으로 오판을 줄인다. Streamlit "📚 도메인 지식" 탭에서 편집. (단, "대상에 없는 내용을 있다고 판단"하지 않도록 프롬프트에서 한 번 더 제한.)

### 진입점

- **CLI** `cli.py`(`contentcompare` 스크립트): `--check` 연결점검 / `--reference`+`--targets` 비교.
- **Streamlit** `app/streamlit_app.py`: 사이드바=설정(백엔드/모델/검색 파라미터), 4탭=비교 실행(엔진 `rag|fact` 선택) / 리포트 보기 / **🔬 파이프라인 현미경** / 도메인 지식. COM 은 데스크톱 세션이 필요하므로 **사용자 PC localhost** 전용. 입력은 업로드보다 **로컬 경로 직접 지정**을 권장(COM 은 실제 파일 경로 필요; 사내 보안/DRM 친화적).

**UI 3층 분리** — `ui/runner.py` 의 "streamlit 없이 단위테스트 가능" 원칙을 시각화까지 확장했다. **HTML 문자열 생성까지가 순수 함수**이고 Streamlit 은 그것을 iframe 에 넣기만 한다:

| 층 | 위치 | 책임 |
|---|---|---|
| 도메인 | `fact/artifact_reader.py` · `fact/missing_trace.py` | 실행 산출물 읽기(`RunSnapshot`) · 원인 분류 |
| 표현 | `ui/diagram.py` · `ui/graph_layout.py` · `ui/micro_world.py` | 뷰모델 → HTML/SVG 문자열 |
| 화면 | `app/streamlit_app.py` | 위젯 · `components.v1.html` 호출 |

- `ui/diagram.py` 는 `scripts/doc_diagrams.py` 에서 이식한 **공용 시각 언어**다(⚙️코드=파랑 `#1565c0` · 🤖LLM=주황 `#e65100` · 🔢임베딩=청록 `#00796b` · 👤사람=보라 `#6a1b9a`). 설명 페이지와 화면이 같은 색·배지를 쓰게 하려는 것이므로 색을 바꾸지 말 것. `scripts/doc_diagrams.py` 에는 서술 데이터와 삽입 엔진만 남았고 출력은 이식 전과 바이트 동일하다.
- 판정 라벨은 `report/fact_report.py` 의 `LABEL`/`ORDER` 가 **단일 출처**다. RAG 의 `runner.VERDICT_LABEL` 과 섞지 말 것 — 같은 이모지, 다른 뜻이다(RAG ✅=같음 / fact ✅=일치).
- 현미경은 **읽기 전용**이고 `artifacts/` 만 본다. `_runs/*` 스냅샷도 열리지만 대상 문서 폴더는 붙이지 않는다 — `fact_id` 는 실행마다 다시 매겨져 현재 폴더와 섞으면 엉뚱한 fact 를 가리킨다.

### 로깅

`logging_setup.setup_logging()` 으로 실행 로그를 파일에 저장(파일엔 DEBUG=프롬프트/LLM 원문/HTTP 까지), `log_print()` 는 화면+로그 동시 출력. 오판 추적이 목적이므로 프롬프트·응답 로깅을 제거하지 말 것.

단, **서드파티 저수준 로거**(`urllib3`·`httpcore`·`httpx`·`openai`·`matplotlib` 등 `NOISY_LOGGERS`)는 기본적으로 WARNING 으로 올려 숨긴다 — 소켓 연결/재시도/폰트 캐시 내용이라 오판 추적에 쓸모없으면서 로그의 대부분을 차지했다(실측: 최근 로그의 최다 항목이 `urllib3.connectionpool` DEBUG). 우리 코드(`contentcompare.*`)의 DEBUG 는 그대로 남으므로 위 원칙과 충돌하지 않는다.

조절 지점이 **두 층**인 것은 순서 때문이다 — `setup_logging()` 은 `AppConfig.load()` 보다 먼저 불려야 설정 파일을 읽는 동안의 로그까지 잡는다. 그래서 기본 목록은 코드 상수(`logging_setup.NOISY_LOGGERS`)로 두고, config 의 `logging.quiet_extra`/`verbose_extra` 는 그 뒤에 `apply_logger_overrides()` 로 얹는다(같은 이름이 양쪽에 있으면 **verbose 가 이긴다** — 디버깅하려고 연 것을 덮지 않기 위해). 전체를 한 번에 열려면 환경변수 `CONTENTCOMPARE_LOG_NOISY=1`.

### 실행 타임라인 (`timeline.py`) — "언제 무엇이 왜 멈췄나"

로그 파일은 **무엇이 기록됐나**를 답하지만 **언제 무슨 일이 있었나**를 한 축으로 보여 주지 못한다. 실측에서 100행 엑셀이 `records` 에서 죽었을 때, "몇 번째 배치에서 죽었나"를 `run_stats.json` 의 `llm.calls` 를 세어 역산해야 했고(profile 1 + schema 1 = 2 이므로 calls 3 이면 첫 배치), 그 사이 화면은 8분간 조용했다 — SDK 가 120초씩 네 번 재시도하는 것이 `duration_ms` 하나로 뭉쳐 보이지 않았기 때문이다. **이 모듈이 없애려는 것이 그 역산이다.**

`llm/tracing.py` 와 **역할이 다르다**. 그쪽은 *무엇을 주고받았나*(프롬프트 원문)를 남기고 기본 off 다. 이쪽은 *언제 무슨 일이 있었나*(시각·소요·회차)를 남기고 **기본 on** 이다. 그래서 **원문을 절대 담지 않는다** — 기본 on 인 경로에 원문이 들어가면 평문 유출이 기본값이 된다. 길이·회차·상태코드만 남긴다.

기록은 `artifacts/_timeline/<실행>.jsonl` 에 **append-only** 로 쌓인다(`JsonlTracer` 가 매번 `index.json` 을 다시 쓰는 것과 의도적으로 다르다 — 타임라인은 *죽는 순간*을 보려고 만드는 것이라 죽을 때 살아남아야 하고, 마지막 줄이 잘려도 앞줄은 읽힌다). ⚠️ `fact/artifact_reader.RESERVED_DIRS` 에 `_timeline` 이 들어 있어야 현미경이 이 폴더를 문서로 오인하지 않는다.

이벤트를 흘리는 지점은 **기존 코드에 이미 있던 자리**다 — 새 배선을 깔지 않았다:

| 지점 | 남기는 것 |
|---|---|
| `tracing.stage()` | 단계 시작/종료 + **예외로 빠져나가면 어느 단계인지 확정** |
| `tracing.substage()` | 반복 구간(`배치 1/4`)을 부모 이름에 잇는다 — 역산이 사라지는 자리 |
| `tracing.TracedChat` | LLM 요청/응답, 소요, 실패, `timeout` 80% 초과 시 **느림 경고** |
| `llm/http_probe.py` | **SDK 내부 재시도**(langchain 경로) — httpx event hook |
| `llm/http.py` | 전송 재시도·429 대기(internal·ollama 경로) |
| `llm/ratelimit.py` | 사전 스로틀·사후 한도 대기 |
| `fact/llm_stage.py` | JSON 파싱 재시도 — 전송 재시도와 **원인이 달라** 갈라 남긴다 |

**SDK 재시도가 httpx 훅인 이유**: openai SDK 는 `max_retries` 만큼 자체 재시도를 하는데 `TracedChat` 에는 그 4회가 한 덩어리로 보인다. 재시도는 **새 HTTP 요청**이므로 전송 계층 훅이 정확히 잡는다. ⚠️ httpx 의 `response` 훅은 **타임아웃에 불리지 않으므로**, 재시도의 증거는 "응답 없이 다음 요청이 왔다"는 사실이다(`HttpProbe.on_request` 가 직전 요청의 미완결을 보고 기록하는 구조가 여기서 나온다). 이를 위해 `langchain_backend._http_client()` 는 `verify_ssl` 값과 **무관하게 항상** httpx 클라이언트를 만든다(verify 값은 그대로라 통신 동작은 동일하고 관측만 얻는다).

**콘솔 인코딩은 선택이 아니다.** Windows PowerShell 5.1 기본이 cp949 인데 거기 `✓`·`✗`·`⚠`·`⏳`·`—` 가 없다 — 그대로 print 하면 `UnicodeEncodeError` 로 **그 줄이 통째로 사라진다**(실측: 종료 줄이 전부 유실됐고 예외는 삼켜져 조용했다). `console_safe()` 가 인코딩이 **실제로 못 쓰는 문자만** 바꾼다(cp949 는 `▶`·`│`·`├` 는 받으므로 남긴다). 그리고 **화면 실패는 기록 실패가 아니다** — 콘솔이 죽으면 화면만 끄고 파일 기록은 계속한다.

조회는 `python scripts/show_timeline.py [--errors] [--slow N] [--stage 이름] [--list]`, 화면은 Streamlit **⏱ 타임라인** 탭(`ui/timeline_view.py` — 표현층은 streamlit 무의존 순수 함수). `timeline.diagnose()` 는 관측된 증상에서 **다음 조치**를 문장으로 낸다(타임아웃 → `record_batch_rows`·`llm.timeout`, 429 → `max_calls_per_minute`, 빈 응답 → Ollama `num_ctx`) — 사람이 코드를 읽어 같은 결론에 다시 도달하지 않게 하려는 것이다.

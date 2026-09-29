# ContentCompare 사용자 매뉴얼

엑셀(기준)의 각 항목을 Word/PPT/Excel(대상)과 대조해 **같음/다름·출처·사유**를
LLM 으로 분석합니다. CLI 와 웹 UI(Streamlit) 두 가지로 쓸 수 있습니다.

## 1. 사전 준비

- **OS/Office**: Windows + MS Office(Excel/Word/PowerPoint). 문서 파싱은 COM
  자동화(xlwings/win32com)를 쓰므로 데스크톱 세션이 필요합니다.
- **Python**: 3.10 이상.
- **LLM**: 아래 중 하나
  - Ollama (로컬): `ollama serve` 후 모델 pull (`ollama pull qwen2.5:14b`, `ollama pull bge-m3`)
  - 사내 OpenAI 호환 엔드포인트: `base_url` + API 키 환경변수

## 2. 설치

```bash
python -m venv .venv && .venv\Scripts\activate
pip install -e .[office]        # 문서 파싱(필수)
pip install -e .[ui]            # 웹 UI 를 쓸 경우
copy config\config.example.yaml config\config.yaml
```

## 3. 설정 (config.yaml)

| 키 | 설명 |
|----|------|
| `llm.backend` | `ollama` 또는 `internal` |
| `llm.chat_model` / `embed_model` | 사용할 모델명 |
| `llm.max_retries` / `backoff_base` | HTTP 재시도 횟수 / 지수 백오프(2→2s,4s,8s) |
| `llm.internal.unset_proxy` | 사내 호출 시 `HTTP(S)_PROXY` 비우기(직결) |
| `llm.internal.log_proxy` | 호출 직전 적용 프록시 env 로깅(우회 실검증) |
| `excel.granularity` | `hybrid`(행검색+셀판정) / `field` / `row` |
| `excel.auto_header` | true 면 LLM 이 상위 행을 보고 헤더 시작/행수 자동 추정(대외비·멀티헤더 대응) |
| `excel.header_rows` | 다단 헤더 행 수(예: 2 → '정량규격>하한치' 결합). auto_header=false 일 때 |
| `excel.key_columns` | 행 식별 키(헤더명/인덱스). 비우면 자동 추정 |
| `excel.compare_columns` / `skip_columns` | 비교/제외 컬럼 |
| `similarity.recall_k` / `top_k` | 1차 후보 / LLM 투입 후보 수 |
| `similarity.fusion` | `rrf`(임베딩+BM25) / `cosine` |
| `similarity.cache_dir` | 임베딩 디스크 캐시 경로(재실행 비용↓) |

## 4. 실행

### 먼저 LLM 연결 점검 (권장)
비교를 돌리기 전에 chat/embedding 이 연결되는지 확인합니다.
```bash
contentcompare --config config\config.yaml --check
```
출력 예(성공):
```
✅ 백엔드=internal: https://llm.intra.corp/v1
✅ chat (사내모델): OK
✅ embeddings (bge-m3): 차원 1024
✅ 모든 점검 통과
```
실패하면 어느 단계(chat/embeddings)에서 어떤 오류인지 메시지로 보여줍니다.
웹 UI 에서는 사이드바의 **🔌 LLM 연결 테스트** 버튼으로 동일하게 확인할 수 있습니다.

> 임베딩 항목이 실패하면(예: 404) 사내 chat 엔드포인트에 embedding 모델이 없는 경우입니다.
> chat 과 embedding 은 서로 다른 모델이므로, 임베딩만 **로컬**로 분리하세요(아래).

### chat=사내 / embedding=로컬 혼합 구성 (사내 chat 이 임베딩을 안 줄 때)
사내 chat 엔드포인트가 임베딩을 제공하지 않으면, 임베딩만 로컬에서 생성합니다.
가장 간단한 건 **fastembed**(오픈소스 ONNX, 서버 불필요):
```bash
pip install -e .[fastembed]
```
```yaml
llm:
  backend: langchain          # 또는 internal — chat 은 사내
  embed_backend: fastembed    # ← 임베딩만 로컬 분리
  chat_model: /models/llm/gemma-4-31B-it
  embed_model: intfloat/multilingual-e5-large   # 다국어(한국어 포함). bge-m3 는 최신 fastembed 필요
  internal:
    base_url: https://api-gernsi.samsungsdi.net/api/llm/openai/v1
    api_key: 발급키
```
대안: `embed_backend: ollama` (로컬 Ollama 의 `bge-m3` 등 사용).

#### 오프라인(사내망에서 HuggingFace 차단 시) 임베딩 모델 직접 받기
fastembed 는 첫 실행 때 HF 에서 모델을 받습니다. 사내망에서 막히면 **인터넷 되는
PC 에서 미리 받아 폴더째 복사**하세요.

1) 인터넷 PC:
```bash
pip install fastembed
python scripts/download_embed_model.py intfloat/multilingual-e5-large ./fastembed_models
```
2) `./fastembed_models` 폴더를 사내 PC 로 복사.
3) 사내 PC config:
```yaml
llm:
  embed_backend: fastembed
  embed_model: intfloat/multilingual-e5-large
  embed_cache_dir: C:\path\to\fastembed_models   # ← 복사한 폴더
```
4) 확인(오프라인):
```bash
set EMBED_CACHE_DIR=C:\path\to\fastembed_models
python scripts\embed_test.py intfloat/multilingual-e5-large
```

#### ONNX 파일을 직접 받아 폴더로 둔 경우 (가장 단순)
HuggingFace 등에서 ONNX 모델 파일을 직접 받아 한 폴더(예: `multilingual-e5-large-onnx`)에
두었다면, fastembed 캐시 구조와 무관하게 그 폴더를 바로 가리킬 수 있습니다.

폴더에 필요한 파일: **`model.onnx`** (또는 `*.onnx` 하나) + **`tokenizer.json`**.

```bash
pip install -e .[onnx]     # onnxruntime + tokenizers + numpy
```
```yaml
llm:
  embed_backend: onnx
  embed_model_path: C:\models\multilingual-e5-large-onnx   # ← 받은 폴더
  embed_prefix: "query: "   # e5 계열 권장(없어도 동작)
```
폴더 위치는 어디든 상관없고, 경로만 맞으면 됩니다. 다운로드를 전혀 하지 않습니다.

### CLI
```bash
contentcompare ^
  --config config\config.yaml ^
  --reference "C:\data\기준.xlsx" ^
  --targets "C:\data\문서A.docx" "C:\data\문서B.pptx" ^
  --out report.md
```

### 웹 UI
여러 사람이 브라우저로 접속해 쓰는 웹 서버다. 서버 PC(Windows + Office) 한 대에서 실행한다.

1. 처음 한 번: `setup.bat`(패키지 설치 + 화면 빌드), `copy .env.example .env` 후 `.env` 에 LLM 접속 정보(`CC_LLM_BACKEND`·`CC_LLM_BASE_URL`·`CC_LLM_API_KEY`·`CC_CHAT_MODEL` …)와 관리자 비밀번호(`CC_ADMIN_PASSWORD`)를 채운다. 세부 튜닝값은 계속 `config/config.yaml`(`CC_CONFIG`)에 둔다.
2. `start.bat check` 로 준비 상태를 확인하고 `start.bat` 으로 실행한다. 창에 접속 주소(`http://<PC 이름>:8000`)가 나오고 브라우저가 열린다.
3. 다른 사람은 그 주소로 접속해 **파일(또는 폴더)을 업로드**하고 🚀 비교 실행을 누른다. 실행 창에서 진행률(%)·현재 단계·로그를 실시간으로 본다. 창을 닫아도 작업은 계속되고 '내 최근 작업'에서 다시 연다.
4. 비교는 **한 번에 1건**씩 돈다(Office·LLM 요청 한도 때문). 다른 작업이 돌고 있으면 "대기 중 · 앞에 N건"으로 보인다.
5. 끄기: 서버 창에서 Ctrl+C 를 **한 번** 누른다(최대 5초). 실행 중이던 작업은 '중단됨'으로 남고 다시 돌리지 않는다.

- 🔐 관리자: 상단 버튼 → 비밀번호 → 로그(레벨·검색·다운로드)와 작업 관리(취소·삭제). 5회 틀리면 1분 잠긴다. HTTPS 가 없으므로 **사내망 전용**이다.
- 서버 PC 에서 사람이 Office 를 함께 쓰지 말 것 — 작업이 비정상 종료되면 새로 뜬 Office 를 정리하면서 함께 닫힐 수 있다. Windows 서비스로 등록하지 말 것(Office 자동화는 로그인된 데스크톱 세션이 필요하다).
- 서버 PC 에 Node.js 가 없으면 Node 가 있는 PC 에서 `cd web && npm ci && npm run build` 로 만든 `web\dist` 폴더를 복사한다.
- 개발: `start.bat dev` — API(자동 재시작)와 화면(Vite, http://localhost:5173)을 각각 새 창으로 띄운다.
- 기존 Streamlit 화면(`streamlit run app/streamlit_app.py`)은 새 화면이 검증될 때까지 남겨 둔다(한 사람이 로컬에서 쓸 때).

## 5. 결과 보는 법

- **판정**: ✅같음 / 🟡부분일치 / ❌다름 / ⚪미발견
- 레코드(행)별로 **필드(셀) 단위** 판정과 **출처**(대상 문서 위치), **사유**가 표로 제공됩니다.
- 레코드 판정은 필드 판정의 집계입니다(모두 같음=같음, 혼재=부분일치 등).

## 6. 트러블슈팅

| 증상 | 점검 |
|------|------|
| `xlwings 가 필요합니다` | `pip install -e .[office]`, Excel 설치 여부 |
| COM 권한/실행 오류 | 데스크톱 세션에서 실행, 다른 Excel 인스턴스 종료 |
| 업로드 시 Permission denied | 사내 보안(nasca)/DRM 이 임시저장 차단 → **파일 경로 입력** 사용(원본 직접 오픈) |
| Word `Open.Close`/COM AttributeError | 문서 열기 자체가 실패(DRM/권한) 또는 gen_py 캐시 손상. `logs\` 의 `[Word] 처리 실패` 직전 로그 확인. 캐시 정리: `%LOCALAPPDATA%\Temp\gen_py` 폴더 삭제 후 재시도 |
| 내부 동작 로그 | 모든 실행은 `logs\contentcompare_<시각>.log` 에 기록(웹 UI 사이드바 '로그 보기'에서도 확인/다운로드) |
| `APITimeoutError` 가 계속 난다 | 원인이 둘이다. ①**요청 한도**를 게이트웨이가 429 대신 '응답을 붙들어' 알리는 경우 → `llm.timeout_wait: 60` (아래) ②**생성이 느린 것**(배치당 출력이 많음) → `fact.record_batch_rows`/`fact_batch_blocks` 를 줄이기. ⏱ 타임라인의 재시도 결과로 갈린다 — 대기 후 성공하면 ①, 계속 실패하면 ② |
| 사내 LLM 연결 실패 | `unset_proxy`/`base_url`/API 키 env 확인, `log_proxy: true` 로 실제 프록시 확인 |
| 타임아웃/간헐 실패 | **⏱ 타임라인 먼저 보기**(아래) — 어느 단계·몇 번째 배치·몇 번째 시도에서 났는지가 나옵니다. 배치당 출력량이 원인이면 `fact.record_batch_rows` 를 줄이는 쪽이 `timeout` 상향보다 확실합니다 |
| F5 값 대조에서 출력 절단 | 첫 요청에 JSON Schema가 적용됐다면 schema를 제거해 **1회만** 다시 판정합니다. 또 잘리거나 파싱/예산 문제가 나면 해당 항목만 `unknown`으로 남기고 다음 항목과 리포트 생성을 계속합니다. `comparison_result.json`의 `failure_reason`과 기준 문서 `run_stats.json.comparison`을 확인하세요 |
| 실행 중 화면이 조용하다 | 정상입니다 — 타임라인이 켜져 있으면 단계·재시도가 실시간으로 찍힙니다. `--quiet` 로 끌 수 있고, 꺼도 파일에는 남습니다 |
| 화면에 더 자세히 보고 싶다 | `--verbose` 로 INFO 까지 보입니다. **프롬프트·LLM 원문·HTTP 페이로드(DEBUG)는 화면에 안 나옵니다** — 로그 파일에는 항상 남으니 그쪽을 보세요(`logs/contentcompare_<시각>.log`). 서드파티 저수준 로그까지 열려면 `CONTENTCOMPARE_LOG_NOISY=1` |
| 임베딩 매번 느림 | `cache_dir` 설정 확인(파일 해시 기반 캐시 재사용) |
| 표시값과 다른 비교 | `excel.value_as_displayed`(표시문자 vs 원시값) 전환 |

### 타임아웃이 반복될 때 (`timeout_wait`)

사내 게이트웨이가 한도 초과를 **429 가 아니라 응답을 붙들고 있는 것**으로 알리면
클라이언트에는 타임아웃으로 보입니다. 그때 짧게 재시도하면 같은 벽에 다시 부딪히므로
한도가 회복될 만큼 기다렸다 다시 부릅니다.

```yaml
llm:
  timeout_wait: 60        # 0=끔(기본). 타임아웃 뒤 대기 초
  timeout_max_retries: 2
  max_retries: 1          # ⚠️ 함께 낮출 것 — 아래 참고
```

⚠️ **대기는 SDK 자체 재시도와 곱해집니다.** `timeout: 120` · `max_retries: 3` 이면 한
호출이 이미 최악 8분인데, 여기에 60초 대기 2회를 얹으면 **26분**이 됩니다. 켜면
실행 시작 시 그 산수를 그대로 알려 주니 `max_retries` 를 0~1 로 낮추세요.

**원인 판별**: 첫 대기 때 예외 실물이 함께 출력됩니다. 대기 후 재시도가 **성공하면
요청 한도**, 계속 실패하면 원인은 한도가 아니라 **생성 지연**이므로 배치 크기를
줄이는 쪽이 답입니다.

### ⏱ 실행 타임라인 — 실패했을 때 가장 먼저 볼 것

실행 중 화면에 단계·LLM 호출·재시도·대기가 시각과 함께 흐릅니다. 같은 내용이
`artifacts/_timeline/<실행>.jsonl` 에 남아 나중에 다시 볼 수 있습니다.

```
17:59:11.1 ▶ F2 records · 자표준원문.xlsx (rows=120, batches=4)
17:59:11.1   ▶ 배치 2/4 (rows=30)
17:59:11.8   │  ⚠ 응답 없음(전송 실패·타임아웃) — 재시도 1/2
17:59:12.6   ✗ F2 records · 자표준원문.xlsx · 배치 2/4 중단 — APITimeoutError (1.5s)
```

실패 줄 하나에 **어느 문서 · 어느 단계 · 몇 번째 배치 · 왜**가 함께 있습니다.

```bash
python scripts/show_timeline.py            # 최근 실행 전체
python scripts/show_timeline.py --errors   # 실패·재시도·대기만
python scripts/show_timeline.py --slow 60  # 60초 넘게 걸린 것만
python scripts/show_timeline.py --list     # 남아 있는 실행 목록
```

웹 UI 에서는 **⏱ 타임라인** 탭에서 같은 내용을 막대그래프로 봅니다.
실행이 끝나면 CLI 가 **단계별 소요**와 **다음에 볼 것**(증상별 조치)을 함께 출력합니다.

설정은 `logging.timeline`(기본 켬) / `logging.timeline_console` / `logging.timeline_dir`.
프롬프트 원문은 담기지 않습니다(길이·회차·상태코드만) — 원문이 필요하면
`llm.trace_local` 을 켜세요.

#### F5 출력 절단을 확인할 때

타임라인의 `retry_without_schema`는 실패한 F5 판정에서 JSON Schema를 제거하고 한 번 더
호출했다는 뜻입니다. 복구되면 `recovered_without_schema`, 복구되지 않으면 해당 결과에
다음 `failure_reason` 중 하나가 남습니다.

- `output_truncated`: schema 제거 호출도 출력 한도에서 절단됨
- `parse_failure`: 응답을 JSON 판정으로 해석하지 못함
- `budget_exceeded`: 비교 단계 호출 예산이 소진됨

`comparison_result.json.stats.llm_calls`는 유효한 판정 응답을 얻은 비교 수이고,
`stats.llm.calls`는 파싱 및 schema 제거 재시도를 포함한 실제 생성 횟수입니다. 복구된
절단도 `llm_truncations`에는 남지만 `llm_failures`에는 포함되지 않습니다. 토큰 한도를
높이거나 temperature를 바꾸는 것은 이 복구의 일부가 아닙니다. 같은 항목에서 같은 토큰
수로 반복 절단되면 `llm.trace_local`의 접힌 응답 원문으로 반복 생성인지 확인하세요.

#### 호출당 토큰·소요 — 배치 크기를 정하는 근거

응답 줄에 서버가 알려준 **토큰 수와 생성 속도**가 함께 남습니다.

```
17:59:11.1   ├ LLM 요청 (rows=30, prompt_chars=12345)
18:00:23.1   ├ ✓ 응답 (72.0s, input_tokens=3204, output_tokens=512, tok_per_sec=7.1, output_chars=1880) ⚠ 느림
```

`fact.record_batch_rows` 를 얼마로 줄일지는 이 숫자로 계산합니다 — 위 예에서 30행에
출력 512토큰이 72초였으니, `llm.timeout: 120` 안에 들어오려면 배치당 출력이 대략
850토큰을 넘지 않아야 합니다. **행당 약 17토큰**이므로 지금은 여유가 있고, 60행으로
올리면 한계에 닿습니다. 반대로 `tok_per_sec` 이 실행마다 크게 흔들리면 배치 크기가
아니라 서버 부하가 원인입니다.

토큰 수는 **서버가 준 값 그대로**이고, 안 주는 게이트웨이면 그 칸이 통째로 빠집니다 —
글자 수에서 토큰을 추정해 채우지 않습니다(추정과 실측이 섞이면 대조가 불가능해집니다).
그때는 같은 줄의 `prompt_chars`/`output_chars` 를 대신 씁니다.

## 7. 비용/성능 메모

- LLM 호출 수 ≈ 기준 행 수(hybrid). 필드는 호출당 묶음 처리.
- 대상 임베딩은 파일 해시 캐시로 재실행 시 0 비용.
- 검색은 numpy 브루트포스(수만 청크까지). 그 이상은 인덱스 교체 지점이
  `similarity/hybrid_index.py` 내부로 국한됩니다.

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
```bash
streamlit run app\streamlit_app.py
```
사이드바에서:
1. **config.yaml 경로** 입력 후 **📂 설정 불러오기** → 파일의 값이 위젯에 그대로 채워집니다.
2. LLM 백엔드(ollama/internal/**langchain**)·모델·base_url·api_key, **임베딩 백엔드**
   (fastembed/onnx/ollama 등)·모델/폴더 경로를 조정.
3. **🔌 LLM 연결 테스트**로 확인.

본문에서 **기준 엑셀 경로**와 **대상 폴더 경로**를 입력하고 **🚀 비교 실행** →
필드별 판정 표 확인 → 리포트 `.md` 다운로드.

> 파일 **업로드** 시 `Failed to fetch dynamically imported module` 오류가 나면(사내망/프록시
> 환경에서 종종 발생): 브라우저 강력 새로고침(Ctrl+Shift+R), `pip install -U streamlit`,
> 그래도 안 되면 **업로드 대신 경로 입력**을 사용하세요(권장). 저장소의 `.streamlit/config.toml`
> 에 우회 옵션(enableXsrfProtection/enableCORS=false)이 준비되어 있습니다.

## 5. 결과 보는 법

- **판정**: ✅같음 / 🟡부분일치 / ❌다름 / ⚪미발견
- 레코드(행)별로 **필드(셀) 단위** 판정과 **출처**(대상 문서 위치), **사유**가 표로 제공됩니다.
- 레코드 판정은 필드 판정의 집계입니다(모두 같음=같음, 혼재=부분일치 등).

## 6. 트러블슈팅

| 증상 | 점검 |
|------|------|
| `xlwings 가 필요합니다` | `pip install -e .[office]`, Excel 설치 여부 |
| COM 권한/실행 오류 | 데스크톱 세션에서 실행, 다른 Excel 인스턴스 종료 |
| 사내 LLM 연결 실패 | `unset_proxy`/`base_url`/API 키 env 확인, `log_proxy: true` 로 실제 프록시 확인 |
| 타임아웃/간헐 실패 | `timeout` 상향, `max_retries` 확인(자동 지수 백오프 재시도) |
| 임베딩 매번 느림 | `cache_dir` 설정 확인(파일 해시 기반 캐시 재사용) |
| 표시값과 다른 비교 | `excel.value_as_displayed`(표시문자 vs 원시값) 전환 |

## 7. 비용/성능 메모

- LLM 호출 수 ≈ 기준 행 수(hybrid). 필드는 호출당 묶음 처리.
- 대상 임베딩은 파일 해시 캐시로 재실행 시 0 비용.
- 검색은 numpy 브루트포스(수만 청크까지). 그 이상은 인덱스 교체 지점이
  `similarity/hybrid_index.py` 내부로 국한됩니다.

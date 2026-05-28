# ContentCompare 설계 문서

## 1. 목표

기준 문서(주로 엑셀)의 모든 항목을 N 개의 비교 대상 문서(Word/PPT/Excel)와
대조하여, 각 항목에 대해 다음을 산출한다.

- **판정(verdict)**: 같음 / 다름 / 부분일치 / 미발견
- **출처(sources)**: 같거나 관련된 내용이 어느 문서·어느 위치에 있는지
- **사유(reasoning)**: 다르다고 판단한 근거(정량/정성 차이 서술)

## 2. 전체 파이프라인

```
[기준 문서]                 [대상 문서들]
   │ Reader                    │ Reader
   ▼                           ▼
DocItem[] (기준)          DocItem[] (대상 풀)
   │                           │ chunk + embed
   │                           ▼
   │                      VectorIndex (임베딩 인덱스)
   │                           ▲
   └── 각 기준 항목마다 ───────┘ top-k 유사 후보 검색
         │
         ▼
   Comparator: (기준 항목 + 후보들) 전체를 LLM 에 투입
         │
         ▼
   ComparisonResult (verdict / sources / reasoning)
         │
         ▼
   MarkdownReport
```

핵심: **유사 내용 "검색"은 임베딩**으로 빠르게 후보를 좁히고,
**최종 "비교 판단"은 LLM** 이 기준 항목과 후보들을 한꺼번에 보고 수행한다(기획 4번).

## 3. 데이터 모델 (`models.py`)

- `DocItem`: 문서에서 추출된 하나의 비교 단위.
  - `doc_id`, `doc_type`(excel/word/ppt), `locator`(위치: 시트!행, 슬라이드 N, 단락 N 등),
    `text`(정규화된 비교용 텍스트), `raw`(원본 값/메타).
- `Candidate`: `DocItem` + `score`(임베딩 유사도).
- `Verdict`(Enum): `SAME` / `DIFFERENT` / `PARTIAL` / `NOT_FOUND`.
- `ComparisonResult`: 기준 항목 + verdict + 매칭된 후보들 + 출처 문자열 + 사유.

`locator` 를 사람이 읽을 수 있는 형태("출처")로 만들기 위해 각 Reader 가
`DocItem.source_label`(예: `문서A.docx > 2페이지 > 3번째 단락`)을 채운다.

## 4. LLM/임베딩 추상화 (`llm/`)

스위치 가능 구조의 핵심.

```python
class LLMClient(Protocol):
    def complete(self, system: str, user: str, **kw) -> str: ...

class EmbeddingClient(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...
```

- `OllamaBackend`: `OLLAMA_HOST` 의 `/api/chat`, `/api/embeddings` 사용.
- `InternalBackend`: 사내 HTTP 엔드포인트(OpenAI 호환 가정). 호출 직전
  `HTTP_PROXY=""`, `HTTPS_PROXY=""`(+소문자, `NO_PROXY`) 를 설정해 사내망 직결.
- `factory.build_clients(config)` 가 `config.llm.backend` 를 보고 적절한 백엔드를 생성.

> 프록시 처리는 `config.apply_proxy_policy()` 에서 수행하며, 컨텍스트 매니저로
> 일시 적용/복원도 지원한다(다른 외부 호출에 영향 최소화).

## 5. 문서 리더 (`readers/`)

- 공통 인터페이스 `DocumentReader.read(path) -> list[DocItem]`.
- `ExcelReader` (xlwings): 헤더 1행을 제외한 모든 row 를 순차적으로 `DocItem` 으로 변환.
  각 셀을 `헤더=값` 형태로 합쳐 한 row 를 하나의 항목으로 본다(설정으로 셀 단위 분해 가능).
- `WordReader` / `PptReader` (win32com): 단락/도형(텍스트프레임)/표 셀 단위로 추출.
- win32com / xlwings 는 **Windows + Office 필수**. 미설치 환경에서는 import 시점이 아니라
  `read()` 호출 시점에 친절한 오류를 던지도록 지연 import.

### 엑셀 전 항목 순차 비교(기획 2번)

`ExcelReader.read()` 가 header 를 식별하고 이후 모든 row 를 순서대로 yield → 파이프라인이
각 항목을 차례대로 처리. 큰 시트는 `config.excel.max_rows` 로 제한/배치 가능.

## 6. 유사 내용 검색 (`similarity/`)

- `chunker.chunk_items()`: 대상 문서의 `DocItem` 들을 임베딩 적정 길이로 분할/병합.
- `VectorIndex`:
  - `add(items)`: 임베딩 계산 후 메모리 보관(기본 numpy 코사인). FAISS 등으로 교체 가능하게 추상화.
  - `search(query_text, k)`: 코사인 유사도 top-k `Candidate` 반환.
  - 임계값(`min_score`) 미만이면 후보 없음 → 파이프라인이 `NOT_FOUND` 처리.

## 7. LLM 비교 (`comparison/`)

- `prompts.py`: 비교용 system/user 프롬프트 템플릿. LLM 에게 기준 항목과 후보들을
  주고 **JSON**(verdict, matched_source_ids, reasoning, evidence)으로 답하게 한다.
- `Comparator.compare(reference_item, candidates) -> ComparisonResult`:
  - 후보 텍스트 + 출처 라벨을 모두 프롬프트에 넣음(기획 4번).
  - LLM JSON 응답 파싱 → `ComparisonResult`.
  - 파싱 실패 시 재시도/폴백 규칙.

## 8. 리포트 (`report/`)

- `markdown_report.py`: 항목별 표 + 항목 상세 섹션.
  - 요약 표: 행 | 판정 | 출처 | 한줄 사유.
  - 상세: 기준 내용, 후보별 유사도, LLM 사유 전문.

## 9. 설정 (`config.py` / `config/config.example.yaml`)

YAML 기반 + 환경변수 오버라이드. 주요 키:

```yaml
llm:
  backend: ollama            # ollama | internal
  chat_model: qwen2.5:14b
  embed_model: bge-m3
  ollama:
    host: http://localhost:11434
  internal:
    base_url: https://llm.intra.corp/v1
    api_key_env: INTERNAL_LLM_API_KEY
    unset_proxy: true        # 사내 연결 시 프록시 비우기
    verify_ssl: false

excel:
  header_row: 1
  max_rows: null

similarity:
  top_k: 5
  min_score: 0.55
  chunk_chars: 800

report:
  format: markdown
```

## 10. 향후 작업(TODO)

- [ ] Ollama/Internal 백엔드 실제 HTTP 본문 마감 및 재시도/타임아웃.
- [ ] xlwings/win32com 실제 파싱 로직 + COM 리소스 정리(finally).
- [ ] 임베딩 캐시(문서 해시 기반)로 재실행 비용 절감.
- [ ] 대용량 문서 배치/스트리밍, 진행률 표시.
- [ ] 테스트: 가짜(fake) LLM/임베딩 백엔드로 파이프라인 단위 테스트.

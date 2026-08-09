# ContentCompare

엑셀(기준 문서)의 각 항목을 Word/PPT/Excel 등 **여러 비교 대상 문서**와 대조하여
"내용이 같은지/다른지", "같다면 어디에 있는지(출처)", "다르다면 무엇이 왜 다른지"를
LLM 으로 분석·서술해 주는 에이전트 서비스입니다.

> 현재 단계: **엔진 + 웹 UI 구현 완료**. 엑셀 hybrid 분해 → 하이브리드 검색(임베딩+BM25 RRF)
> → 필드별 LLM 판정 → 리포트, Streamlit UI, 재시도/타임아웃 HTTP 까지 구현·테스트되어 있습니다.
> (남은 환경 의존부: win32com 기반 Word/PPT 실제 파싱.) 실제 실행은 Windows + MS Office 환경이 필요합니다.
> 단계별 진행 현황은 [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md), 사용법은
> [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md) 참고.
> 차세대(fact 기반) 비교 방식 구현 계획은 [`docs/FACT_PIPELINE_PLAN.md`](docs/FACT_PIPELINE_PLAN.md) 참고(현행 방식과 비교·공존).

## 핵심 설계 포인트 (기획 대응)

| 기획 | 구현 위치 |
|------|-----------|
| 1. 로컬(사내) / Ollama LLM 스위치, 사내 연결 시 프록시 비우기 | `contentcompare/llm/` + `config.py` |
| 2. 엑셀=xlwings, Word/PPT=win32com, 엑셀 전 항목 순차 비교 | `contentcompare/readers/` |
| 3. 1개 기준문서 vs N개 대상문서, 항목별 같음/다름 + 출처/사유 | `contentcompare/pipeline.py` |
| 4. 유사 내용 검색(임베딩) → 찾은 내용 전부 LLM 에 넣어 비교 | `contentcompare/similarity/` + `contentcompare/comparison/` |

전체 설계는 [`docs/DESIGN.md`](docs/DESIGN.md) 참고.

## 구조

```
contentcompare/
├── config.py            # YAML/ENV 설정 로딩 + 프록시 제어
├── models.py            # DocItem / Candidate / RecordResult 등 데이터 모델
├── pipeline.py          # 전체 오케스트레이션 (기준→후보검색→LLM비교→리포트)
├── cli.py               # CLI 진입점
├── knowledge.py         # 사람이 작성하는 도메인 지식(human-in-the-loop) 로딩/주입
├── logging_setup.py     # 실행 로그 파일 저장 + log_print(화면+로그 동시)
├── llm/                 # LLM/임베딩 백엔드 (스위치 가능)
│   ├── base.py          #   LLMClient / EmbeddingClient 추상 인터페이스
│   ├── ollama.py        #   Ollama 백엔드
│   ├── internal.py      #   사내 HTTP 백엔드 (프록시 우회)
│   ├── http.py          #   공용 HTTP: 재시도 + 429(요청 한도) 대기 처리
│   └── factory.py       #   설정 기반 백엔드 선택
├── readers/             # 문서 리더
│   ├── base.py          #   DocumentReader 인터페이스
│   ├── excel_reader.py  #   xlwings
│   ├── word_reader.py   #   win32com
│   └── ppt_reader.py    #   win32com
├── similarity/          # 임베딩 기반 유사 내용 검색
│   ├── chunker.py
│   └── vector_index.py
├── comparison/          # LLM 기반 내용 비교 (행 단위 종합 판정)
│   ├── comparator.py
│   └── prompts.py
└── report/
    ├── markdown_report.py  # 리포트 렌더
    └── store.py            # 리포트 저장/조회 (Streamlit '리포트 보기')
```

## 주요 동작 (요청 반영)

- **행 단위 종합 판정**: 엑셀 한 행의 모든 열을 함께 종합해, 그 내용이 대상 문서에
  있는지(verdict)·어디에 있는지(출처)·왜 그렇게 판단했는지(근거)를 한 번에 판정. 열별은
  "확인됨/근거 + 후보 원문 인용" 세부 내역으로 표시.
- **판단보류(unknown) + 근거 인용**: 값이 정확히 같으면 `same`, 다르면 `different`,
  단위가 모호하거나 도메인 지식이 부족해 **확신이 안 서면 `unknown`(❓ 판단보류)** 으로 두고
  *왜 어려운지*를 근거에 적게 함. 각 항목은 판단 근거가 된 **후보 원문을 그대로 인용**해,
  사람이 할루시네이션 여부를 눈으로 검수할 수 있게 함.
- **교차언어 검색(한↔영)**: 기준 문서가 한국어, 대상 문서가 영어여도 다국어 임베딩
  (`multilingual-e5-large`, `bge-m3` 등)으로 의미 기반 후보를 찾음. e5 계열은 검색어/본문에
  서로 다른 접두어가 필요하므로, 본문은 `passage:` · 검색어는 `query:` 로 자동 분리해 임베딩
  (`llm.embed_query_prefix` / `embed_passage_prefix`, 미지정 시 `embed_prefix` 폴백).
- **요청 한도(429) 대응**: 사내 LLM 분당 한도에 맞춰 429 면 기본 60초(또는 서버 `Retry-After`)
  대기 후 재시도(`llm.rate_limit_wait` / `rate_limit_max_retries`).
- **도메인 지식 주입**: `knowledge/` 의 모든 `.md` 를 비교 프롬프트에 항상 참고자료로 주입.
  Streamlit "📚 도메인 지식" 탭에서 작성/저장.
- **리포트 보기**: 비교 결과 리포트(.md)를 `reports/` 에 자동 저장하고 Streamlit "📄 리포트
  보기" 탭에서 렌더/다운로드. CLI 도 `--out` 외에 `reports/` 사본을 남김.
- **로그**: 화면 출력(`log_print`)과 프롬프트/LLM 원문 응답/HTTP 요청까지 로그 파일에 기록.

## 빠른 시작

```bash
# 1) (선택) 가상환경
python -m venv .venv && . .venv/Scripts/activate   # Windows
pip install -e .

# 2) 설정 파일 복사 후 편집
copy config\config.example.yaml config\config.yaml

# 3) 실행
contentcompare \
  --config config/config.yaml \
  --reference "C:\data\기준.xlsx" \
  --targets "C:\data\문서A.docx" "C:\data\문서B.pptx" \
  --out report.md
```

## 웹 UI (로컬, Streamlit)

CLI 대신 브라우저에서 사용할 수 있습니다. COM 자동화 특성상 **사용자 PC의
localhost** 에서 구동합니다(Windows + MS Office 필요).

```bash
pip install -e .[ui]
streamlit run app/streamlit_app.py
```

- 사이드바: LLM 백엔드/모델, 엑셀 분해(hybrid/field/row), recall_k·top_k, 융합(rrf/cosine), 재랭킹
- 입력: 기준 엑셀 + 대상 문서를 **업로드**하거나 **로컬 경로/폴더**로 지정
- 결과: 판정 요약 메트릭 + 레코드별 **필드 판정 표** + 리포트 `.md` 다운로드

## LLM 백엔드 전환

`config.yaml` 의 `llm.backend` 값만 바꾸면 됩니다.

```yaml
llm:
  backend: ollama        # ollama | internal
```

사내(internal) 백엔드 선택 시 `internal.unset_proxy: true` 면
프로세스 환경의 `HTTP_PROXY`/`HTTPS_PROXY` 를 빈 값으로 만들어
사내망 직접 호출이 되도록 합니다. (기획 1번)

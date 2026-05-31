# ContentCompare

엑셀(기준 문서)의 각 항목을 Word/PPT/Excel 등 **여러 비교 대상 문서**와 대조하여
"내용이 같은지/다른지", "같다면 어디에 있는지(출처)", "다르다면 무엇이 왜 다른지"를
LLM 으로 분석·서술해 주는 에이전트 서비스입니다.

> 현재 단계: **엔진 + 웹 UI 구현 완료**. 엑셀 hybrid 분해 → 하이브리드 검색(임베딩+BM25 RRF)
> → 필드별 LLM 판정 → 리포트, Streamlit UI, 재시도/타임아웃 HTTP 까지 구현·테스트되어 있습니다.
> (남은 환경 의존부: win32com 기반 Word/PPT 실제 파싱.) 실제 실행은 Windows + MS Office 환경이 필요합니다.
> 단계별 진행 현황은 [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md), 사용법은
> [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md) 참고.

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
├── models.py            # DocItem / Candidate / ComparisonResult 등 데이터 모델
├── pipeline.py          # 전체 오케스트레이션 (기준→후보검색→LLM비교→리포트)
├── cli.py               # CLI 진입점
├── llm/                 # LLM/임베딩 백엔드 (스위치 가능)
│   ├── base.py          #   LLMClient / EmbeddingClient 추상 인터페이스
│   ├── ollama.py        #   Ollama 백엔드
│   ├── internal.py      #   사내 HTTP 백엔드 (프록시 우회)
│   └── factory.py       #   설정 기반 백엔드 선택
├── readers/             # 문서 리더
│   ├── base.py          #   DocumentReader 인터페이스
│   ├── excel_reader.py  #   xlwings
│   ├── word_reader.py   #   win32com
│   └── ppt_reader.py    #   win32com
├── similarity/          # 임베딩 기반 유사 내용 검색
│   ├── chunker.py
│   └── vector_index.py
├── comparison/          # LLM 기반 내용 비교
│   ├── comparator.py
│   └── prompts.py
└── report/
    └── markdown_report.py
```

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

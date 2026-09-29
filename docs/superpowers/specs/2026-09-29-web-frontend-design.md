# 웹 프론트엔드 전환 — Streamlit 을 FastAPI + React 로, 여러 명이 한 서버를 쓴다

- 작성일: 2026-09-29
- 상태: **설계 합의 완료 — 구현 전**
- 관련 코드: `app/streamlit_app.py`(대체 대상), `contentcompare/ui/`(재사용),
  `contentcompare/fact/pipeline.py`·`contentcompare/pipeline.py`(진행률 훅),
  `contentcompare/timeline.py`·`contentcompare/logging_setup.py`(작업별 격리)

## 1. 목적

지금은 Streamlit 으로 문서 비교를 시험하고 있다. 이것을 **사내 서버 한 대에서 여러 명이
브라우저로 접속해 쓰는 웹 앱**으로 바꾼다.

성공 기준:

1. Streamlit 의 화면·조작(비교 실행, 리포트, 현미경, 타임라인, 도메인 지식)이 모두 새
   화면에서 된다.
2. LLM 접속 정보는 `.env` 에서 읽고, 화면 좌측에는 **LLM 연결 테스트 버튼만** 있다.
3. 비교를 실행하면 별도 창에서 **실시간 로그**와 **전체 진행률(%)**이 보인다.
4. 여러 명이 동시에 접속해도 작업끼리 서로 망가뜨리지 않는다.
5. 관리자 버튼 → 비밀번호 → 관리자 페이지에서 로그를 확인할 수 있다.
6. `start.bat` 하나로 프론트와 백엔드가 함께 뜬다.

## 2. 결정 요약

| 항목 | 결정 | 근거 |
|---|---|---|
| 접속 구조 | 서버 PC 1대(Windows+Office) + 브라우저 **업로드** | 원격 사용자에게 서버의 경로 선택 창(tkinter)은 의미가 없다 |
| 동시 실행 | **대기열, 한 번에 1건** | PowerPoint COM 은 PC 전체에 하나뿐이고, LLM 요청 한도는 키 하나를 공유한다(§6) |
| 작업 실행 | 작업마다 **별도 worker 프로세스**, 웹 서버와는 **파일로만** 통신 | 프로세스 전역 상태(타임라인·로그·프록시·COM)를 작업 단위로 가둔다 |
| 진행률 | **2단 구조**: 시작 시 확정되는 단계 N + 진행 중 단계의 내부 비율 | fact 엔진은 전체 LLM 호출 수가 끝까지 확정되지 않는다(§7) |
| 접근 제어 | 로그인 없음(이름만 입력) + **관리자만 비밀번호** | 사내 소수 인원. 민감한 원문(DEBUG 로그)은 관리자 화면에만 둔다 |
| 기술 스택 | **FastAPI + React(Vite, TS)**, 실시간은 SSE | 기존 HTML 렌더 함수를 iframe 으로 재사용할 수 있다 |
| 캐시 | 작업마다 산출물 폴더를 따로 쓴다 — **이전 실행 캐시를 재사용하지 않는다** | 단순·안전 우선. 공용 캐시는 필요해지면 추가(§14) |
| 실행 파일 | `start.bat` (PyInstaller .exe 아님) | COM·langchain·임베딩 모델 번들이 깨지기 쉽고 사내 백신 오탐 위험 |

## 3. 범위

**포함**: 웹 서버(API·대기열·worker·SSE), React 화면(좌측 패널 + 탭 5개 + 실행 창 + 관리자
페이지), 진행률 보고 모듈과 파이프라인 훅, `.env` 로더, `start.bat`/`setup.bat` 갱신, 테스트.

**제외**(차차 논의): 사용자 로그인·권한, HTTPS, 공용 산출물 캐시, 동시 실행 2건 이상,
화면에서의 세부 설정 편집, 남은 시간(ETA) 표시, 관리자 페이지의 로그·작업 외 기능.

**유지**: `app/streamlit_app.py` 는 새 화면이 검증될 때까지 지우지 않는다. CLI 는 영향이 없다.

## 4. 전체 구조

```
브라우저 (React SPA)
  │  REST (/api/...)  +  SSE (/api/jobs/{id}/events)
  ▼
uvicorn (프로세스 1개, worker 수 1 고정) ─ FastAPI 앱 contentcompare/web/
  │   ├─ 정적 파일: web/dist
  │   ├─ JobQueue: 스케줄러 스레드 1개, 동시 실행 1
  │   └─ 조회 API: 리포트·현미경·타임라인·지식·LLM 점검·관리자
  │
  └─ subprocess ─ python -m contentcompare.web.worker <jobs/작업ID>
         └─ make_pipeline(config, engine).run(...)  ← CLI 와 같은 진입
            결과·진행률·로그를 전부 jobs/<작업ID>/ 에 파일로 남긴다
```

⚠️ uvicorn 은 **worker 1개**로 띄워야 한다. 대기열 스케줄러가 서버 프로세스 안에 있으므로
여러 개를 띄우면 스케줄러도 여러 개가 되어 동시 실행 1건 보장이 깨진다. 디스크의
`job.json` 이 상태의 원본이고, 메모리는 그것의 사본이다.

### 4.1 원칙

1. **파이프라인은 웹을 모른다.** worker 는 CLI 처럼 `make_pipeline().run()` 만 부른다.
   파이프라인이 바뀌는 곳은 진행률 훅 호출(§7.4)뿐이다.
2. **작업마다 별도 프로세스, 웹 서버와는 파일로만.** 한 작업이 죽거나 COM 찌꺼기를 남겨도
   다음 작업과 웹 서버는 무사하다. 웹 서버가 재시작돼도 진행 기록이 남는다.
3. **실행 외에는 조회 전용이라 동시에 써도 된다.** 예외는 도메인 지식 편집(§8.3).
4. **UI 3층 분리를 확장한다.** 현미경(`micro_world`)·타임라인(`timeline_view`)은 React 로
   다시 만들지 않고 서버가 `render_*_html()` 결과를 돌려주면 iframe 에 넣는다. 표를 만드는
   로직(`ui/runner.py` 의 `summary_rows` 등)도 서버가 그대로 쓰고 JSON 으로 보낸다.

### 4.2 폴더 구성

| 경로 | 역할 |
|---|---|
| `contentcompare/web/app.py` | FastAPI 앱, 라우트 조립, 정적 파일 서빙 |
| `contentcompare/web/settings.py` | `.env` 로더, `AppConfig` 에 덮어쓰기, 웹 전용 설정 |
| `contentcompare/web/jobs.py` | 작업 모델·상태 전이·대기열 스케줄러·보관 기간 정리 |
| `contentcompare/web/worker.py` | worker 프로세스 진입점(작업 하나 실행) |
| `contentcompare/web/uploads.py` | 업로드 검증·저장(확장자·크기·파일명 정규화) |
| `contentcompare/web/events.py` | 작업 파일을 읽어 SSE 로 흘리는 tail 로직 |
| `contentcompare/web/admin.py` | 관리자 인증·로그 조회 |
| `contentcompare/web/views.py` | 결과·리포트·현미경·타임라인·지식 조회 API |
| `contentcompare/web/__main__.py` | `python -m contentcompare.web` — uvicorn 기동 |
| `contentcompare/progress.py` | 진행률 보고기(웹 무관, §7) |
| `web/` | React + Vite + TS 소스, 빌드 결과 `web/dist`(git 제외 — 기존 `dist/` 규칙) |
| `start.bat` | 실행 파일(§11) |
| `.env.example` | `.env` 견본(§10) |
| `jobs/<작업ID>/` | 작업 폴더(§5.2), git 제외 |

의존성은 새 extra `web` 으로 묶는다: `fastapi`, `uvicorn`, `python-multipart`(업로드),
`python-dotenv`. SSE 는 추가 라이브러리 없이 `StreamingResponse` 로 구현한다.

## 5. 작업(Job)

### 5.1 상태 전이

```
queued ──▶ running ──▶ succeeded
   │          ├──────▶ failed        (worker 종료코드 ≠ 0 또는 예외)
   │          ├──────▶ cancelled     (요청자·관리자 취소)
   │          └──────▶ interrupted   (서버 재시작 시 실행 중이던 작업)
   └──▶ cancelled                    (대기 중 취소)
```

- 작업 ID: `YYYYMMDD-HHMMSS-xxxx`(시각 + 4자리 난수). 정렬하면 접수 순서가 된다.
- `job.json` 은 임시 파일에 쓴 뒤 교체해 원자적으로 저장한다.
- **서버 재시작**: `running` 이던 작업은 `interrupted` 로 바꾸고 자동 재실행하지 않는다
  (LLM 비용이 다시 든다). `queued` 는 대기열로 복원한다.
- **요청자 식별**: 이름(자유 입력) + 브라우저 쿠키의 무작위 ID(`cc_client`). 이 ID 로
  "내 작업"과 취소 권한을 판단한다. 로그인이 아니므로 **보안 장치가 아니라 편의 기능**이다.

### 5.2 작업 폴더

```
jobs/<작업ID>/
  job.json          상태·요청자 이름·client ID·엔진·파일 목록·시각·실제 백엔드/모델명(키 없음)
  inputs/           업로드 원본 (reference/, targets/ — 폴더 업로드의 하위 경로 보존)
  artifacts/        fact 산출물 (fact.artifacts_dir 를 여기로 돌린다)
    _timeline/timeline.jsonl   타임라인 (timeline_dir 가 artifacts_dir 를 따른다)
    _traces/                   trace_local 이 켜졌을 때만
  progress.jsonl    진행률 이벤트 (§7)
  console.log       사용자 화면용 로그 (INFO 이상, 원문 없음)
  contentcompare_*.log  전체 로그 (DEBUG, 프롬프트·원문 포함 — 관리자 전용)
  result.json       화면 표시용 결과 (판정 건수·요약 표·상세)
  report.md         리포트
```

### 5.3 worker 가 하는 일

1. `.env` 를 **스스로** 읽어 `AppConfig` 를 만든다(§10). API 키를 작업 폴더에 남기지 않기
   위해 서버가 설정을 파일로 넘기지 않는다.
2. 작업 폴더로 경로를 돌린다: `fact.artifacts_dir`, `llm.trace_dir`, 로그 폴더, 리포트 출력.
3. `setup_logging(log_dir=작업폴더)`, `setup_console(stream=console.log)`,
   `start_timeline(config, console=False, label="timeline")`, 진행률 보고기 설치.
4. Office 프로세스 목록을 기록한 뒤 `make_pipeline(config, engine).run(...)` 실행.
5. 결과를 `result.json`·`report.md` 로 쓰고 종료코드로 성패를 알린다.

서버는 종료코드와 `job.json` 을 보고 상태를 확정한다.

### 5.4 취소

- 대기 중: 대기열에서 빼고 `cancelled`.
- 실행 중: worker 프로세스 트리를 종료한 뒤, **작업 시작 전 목록에 없던** Office 프로세스
  (`EXCEL.EXE`·`WINWORD.EXE`·`POWERPNT.EXE`)만 종료한다. 사람이 쓰던 Office 는 건드리지 않는다.
- 권한: 같은 `cc_client` 쿠키를 가진 브라우저 또는 관리자.

## 6. 동시 접속 시 고려사항

| # | 위험 | 대응 |
|---|---|---|
| 1 | Office COM 은 로그인된 데스크톱 세션이 필요하다. Windows 서비스(Session 0)에서는 불안정하다 | 서버 PC 에 로그인한 계정에서 `start.bat` 을 실행한다. 서비스 등록은 하지 않는다 |
| 2 | **PowerPoint 는 `DispatchEx` 로도 PC 전체에 하나**라, 한 작업의 `Quit` 이 남의 PPT 를 닫는다 | 동시 실행 1건. 서버 PC 에서 사람이 Office 를 같이 쓰지 않도록 운영 안내 |
| 3 | LLM 요청 한도는 키 하나를 공유하는데 `RateLimiter` 는 프로세스 안에만 있다 | 동시 실행 1건이라 기존 limiter 가 유효 |
| 4 | **LLM 연결 테스트도 한도를 먹는다.** 여러 명이 누르면 실행 중 작업이 429 를 맞는다 | 결과를 30초 재사용, 동시에 1회만 실행 |
| 5 | 같은 이름 문서를 두 사람이 올리면 `artifacts/<문서명>/` 이 섞인다 | 작업 폴더 격리(§5.2). 대가로 이전 실행 캐시를 못 쓴다 |
| 6 | 타임라인·로그·`disable_proxy()` 가 프로세스 전역이다 | 작업마다 별도 프로세스 |
| 7 | 멈춘 것처럼 보이는 작업 | 진행 이벤트가 `CC_STALL_WARN_MIN`(기본 5분) 없으면 화면 경고만. **자동 종료하지 않는다** — SDK 재시도까지 합치면 호출 하나가 최악 8분까지 정상이다 |
| 8 | 사내 문서 원본이 서버에 쌓인다 | `CC_JOB_RETENTION_DAYS`(기본 7일) 지난 작업 폴더 자동 삭제(서버 기동 시 + 하루 1회), 관리자 수동 삭제 |
| 9 | 업로드 악용 | 확장자 허용 목록(`ui/runner.SUPPORTED_EXTS`), `CC_MAX_UPLOAD_MB` 상한, 파일명 정규화로 경로 탐색(`..\`) 차단, `~$` 잠금 파일 제외 |
| 10 | 여러 명이 같은 작업을 구경 | 작업당 파일 읽기 루프 하나를 구독자들이 공유 |
| 11 | 네트워크 | `0.0.0.0:CC_PORT`, Windows 방화벽 허용 필요. HTTPS 없음 — **관리자 비밀번호가 평문으로 오간다**는 전제를 문서에 명시 |
| 12 | 도메인 지식 동시 편집 | §8.3 |

## 7. 진행률

### 7.1 단계 N — 작업 시작 시 확정, 실행 중 불변

| 엔진 | 단계 목록 |
|---|---|
| fact | 문서 처리 × (기준 1 + 대상 T) → F7 개념 판정 × 1(`use_concept_graph` 일 때) → F5 값 대조 × T |
| rag | 문서 읽기·인덱싱 × 1 → 기준 항목 판정 × 1 |

예: fact, 대상 3개 → N = 4 + 1 + 3 = 8.

### 7.2 진행 중 단계의 내부 비율

| 단계 | 내부 단위 | 내부 전체가 정해지는 시점 |
|---|---|---|
| 문서 처리 | 하위 단계 — Excel: raw·profile·schema·records·facts·검증(6), Word/PPT: raw·profile·facts·검증(4). records·facts 는 배치 i/n 으로 더 쪼갠다 | 문서 시작 시(하위 단계 수), 해당 하위 단계 시작 시(배치 수) |
| F7 | 판정 배치 i/n | 후보 쌍 산출 후 |
| F5 | 기준 fact i/M | 대상 문서 대조 시작 시 |
| rag 판정 | 기준 항목 i/M | 읽기 후 |

캐시가 적중한 하위 단계는 즉시 완료로 센다.

### 7.3 계산과 규칙

- **전체 % = (완료 단계 수 + 진행 중 단계의 내부 비율) ÷ N × 100**
- **뒤로 가지 않는다.** 출력 절단으로 배치가 쪼개져도(`run_batch`) 원래 배치 번호로만 센다.
  계산값이 이전보다 작으면 이전 값을 유지한다.
- **실패한 문서도 완료로 센다.** 그 단계에 실패 표시. 문서 실패를 격리하고 계속하는 현재
  동작과 같다.
- **남은 시간은 표시하지 않는다.** 경과 시간만 표시한다(배치 속도 편차가 커서 추정이 틀린다).

### 7.4 `contentcompare/progress.py`

```python
plan(units: Iterable[Unit])                         # 단계 N 확정 (실행마다 1회, 맨 처음)
unit_start(key: str, parts: int = 1)                # 단위 시작, parts = 하위 단계 수
part(index: int, name: str)                         # 진행 중 단위의 하위 단계 진입(1부터)
step(done: int, total: int)                         # 진행 중 (하위) 단계 안의 진척
unit_done(key: str, *, ok: bool = True, error: str = "")   # 단위 완료(실패 포함)
finish_remaining(error: str = "")                   # 안 닫힌 단위 일괄 종료(실행 끝 finally)
unit(key: str, parts: int = 1)                      # unit_start … unit_done 컨텍스트 매니저
```

`part`/`step` 은 키를 받지 않는다 — 모듈이 "지금 진행 중인 단위"를 기억하므로 배치 루프 깊숙한
곳(F2·F3·F7)이 자기가 어느 단위 안에 있는지 몰라도 된다. 두 파이프라인의 `run()` 에 `progress`
라는 매개변수가 이미 있어서 모듈은 `prog` 로 가져온다.

- 기본 보고기는 **아무 일도 하지 않는다** → CLI·기존 테스트 무영향.
- worker 는 `JsonlProgress(작업폴더/progress.jsonl)` 를 설치한다. 한 줄 = 한 이벤트
  (append-only, 타임라인과 같은 이유 — 죽을 때 살아남아야 한다).
- 비율 계산·단조 보장은 **읽는 쪽**(서버)의 순수 함수 `summarize(events) -> Snapshot` 이 한다.
  쓰는 쪽은 사실만 남긴다.
- 호출 지점: fact `run()`(plan), `_process_one`(문서 하위 단계), `record_normalizer`·
  `fact_extractor` 배치 루프(F2·F3), `concept_builder.judge_pairs`(F7), `_compare_from_store`
  의 F5 루프, RAG `ComparePipeline.run()`.

**타임라인 이벤트에서 계산하지 않는 이유**: 타임라인은 사람이 읽는 진단 기록이라 배치 번호가
`"배치 3/7"` **문자열 안에만** 있고, F5·F7 반복에는 이벤트가 없다. 진행률이 이름 파싱에
기대면 이름 형식만 바꿔도 조용히 깨진다.

## 8. 화면

### 8.1 레이아웃

```
┌──────────────────────────────────────────────────────────────┐
│ 📑 ContentCompare        이름: [홍길동]        [🔐 관리자]   │
├──────────────┬───────────────────────────────────────────────┤
│ 🔌 LLM 연결  │ 🚀 비교 실행 │ 📄 리포트 │ 🔬 현미경 │ ⏱ 타임라인 │ 📚 도메인 지식 │
│    테스트    │                                               │
│ (결과 목록)  │                (탭 내용)                      │
└──────────────┴───────────────────────────────────────────────┘
```

좌측 패널에는 연결 테스트 버튼과 결과 줄(✅/❌, `CheckResult.line()`)만 둔다.

### 8.2 탭 — Streamlit 대응표

| 탭 | 동작 | Streamlit 대비 변경 |
|---|---|---|
| 🚀 비교 실행 | 엔진(rag/fact) → ① 기준 엑셀 업로드(1개, xlsx/xls/xlsm) → ② 대상 업로드(여러 파일 + **폴더 업로드**, 하위 폴더 포함) → 대상 목록·개수 → 🚀 실행 | tkinter 선택 창 → 업로드. 목록 비우기 유지. 하단에 **대기열 현황**·**내 최근 작업** 추가 |
| (실행 결과) | 판정별 건수, 요약 표, 상세(RAG: 항목 펼치기 + 열별 확인 내역·후보), 리포트 미리보기, `.md` 다운로드 | 같은 내용. 판정 라벨은 RAG=`runner.VERDICT_LABEL`, fact=`fact_report.LABEL` — **섞지 않는다** |
| 📄 리포트 | 최신순 선택, 다운로드, 본문 표시 | 작업별 `report.md` + 기존 `reports/` |
| 🔬 현미경 | 실행 선택 → 디버깅/학습, 문서·항목·대상·판정 필터, 높이 조절 | 폴더 경로 입력 → **작업 목록 선택**(+ 기존 `artifacts/`). HTML 은 `micro_world.render_*_html` 그대로 |
| ⏱ 타임라인 | 실행 선택, "실패·재시도만" | 작업별 `timeline.jsonl` + 기존 `_timeline/`. HTML 은 `render_timeline_html` 그대로 |
| 📚 도메인 지식 | 파일 선택·새 파일·편집·저장·합친 지식 미리보기 | 저장 충돌 확인 추가(§8.3) |

### 8.3 도메인 지식 동시 편집

`knowledge/` 는 모든 작업이 공유한다(의도된 동작 — 지식 개선이 다음 작업에 바로 반영된다).
저장 요청에 **편집을 시작할 때의 파일 수정 시각**을 함께 보낸다. 서버의 현재 수정 시각과
다르면 저장하지 않고 "다른 사람이 먼저 저장했습니다"와 두 내용을 돌려준다. 저장은 원자적으로
한다. 실행 중인 작업은 비교 단계 시작 시점의 지식을 쓴다.

### 8.4 실행 창

```
┌ 작업 #0412 · 홍길동 · fact ─────────────── [새 창으로] [최소화] ┐
│ 상태: ● 실행 중   (대기 중이면 "대기 중 · 앞에 2건")            │
│ ██████████████░░░░░░░░░  57%   4.6/8 단계   경과 12:30        │
│ 현재: 대상B.pptx · F3 facts · 배치 3/5                          │
│ ✓기준.xlsx ✓대상A.docx ▶대상B.pptx ·대상C.xlsx ·F7 ·F5×3        │
├─ 로그 ────────────────────────────── [자동 스크롤 ☑] ─────────┤
│ 14:02:11  ...                                                  │
├────────────────────────────────────────────────────────────────┤
│ [취소]                            (완료 시) [결과 보기] [리포트] │
└────────────────────────────────────────────────────────────────┘
```

- 🚀 실행을 누르면 페이지 위에 뜬다. "새 창으로"는 `/jobs/<작업ID>` 를 브라우저 새 창으로 연다.
- **창을 닫아도 작업은 계속된다.** "내 작업"에서 다시 연다. 다른 사람은 링크로 보기만 가능
  (취소 버튼 숨김).
- 로그는 `console.log`(INFO 이상, 원문 없음)만 보인다. DEBUG 는 관리자 전용.
- WARNING 이상 줄은 강조한다. 멈춤 경고(§6 #7)는 창 상단에 띄운다.
- SSE 는 이벤트마다 `id` 를 붙이고, 재연결 시 `Last-Event-ID` 다음부터 보낸다.

## 9. API

| 메서드·경로 | 용도 |
|---|---|
| `POST /api/llm/check` | 연결 테스트(30초 재사용, 동시 1회) |
| `POST /api/jobs` | multipart: `engine`, `requester`, `reference`, `targets[]`(상대 경로 포함), 선택 `reference_path`·`target_paths[]`(폴더 경로 보존) → 작업 ID |
| `GET /api/jobs` | 대기열 + 최근 작업(`mine` 표시) |
| `GET /api/jobs/{id}` | 상태·진행 스냅샷 |
| `POST /api/jobs/{id}/cancel` | 취소(요청자·관리자) |
| `GET /api/jobs/{id}/events` | SSE: `status`·`progress`·`log`·`stall` 이벤트 |
| `GET /api/jobs/{id}/result` | 화면 표시용 결과 JSON |
| `GET /api/reports`, `GET /api/reports/content?id=` | 리포트 목록·본문 |
| `GET /api/micro/runs`, `GET /api/micro/options?run=`, `GET /api/micro/html?run=&mode=…` | 현미경 선택지·렌더 |
| `GET /api/timelines`, `GET /api/timelines/html?run=&errors_only=` | 타임라인 목록·렌더 |
| `GET/PUT /api/knowledge/files/{name}`, `GET /api/knowledge/merged` | 도메인 지식 |
| `GET /api/admin/status`, `POST /api/admin/login`, `POST /api/admin/logout` | 관리자 세션 |
| `GET /api/admin/logs`, `GET /api/admin/logs/read?source=`, `GET /api/admin/logs/download?source=` | 로그 목록·조회(레벨·검색·꼬리 N줄·이어 읽기)·다운로드 |
| `GET /api/admin/jobs`, `POST .../cancel`, `DELETE /api/admin/jobs/{id}` | 작업 관리 |

경로 인자로 파일을 고르는 API 는 **허용된 루트 안인지** 검사한다(`jobs/`, `reports/`,
`artifacts/`, `logs/`, `knowledge/`).

## 10. 설정 — `.env`

```
CC_CONFIG=config/config.yaml   # 세부 설정 원본(선택)
CC_LLM_BACKEND=langchain       # → llm.backend
CC_LLM_BASE_URL=...            # → llm.internal.base_url (internal·langchain)
CC_OLLAMA_HOST=...             # → llm.ollama.host
CC_LLM_API_KEY=...             # → llm.internal.api_key
CC_CHAT_MODEL=...              # → llm.chat_model
CC_EMBED_BACKEND=fastembed     # → llm.embed_backend
CC_EMBED_MODEL=...             # → llm.embed_model
CC_ADMIN_PASSWORD=...          # 비우면 관리자 기능 꺼짐
CC_HOST=0.0.0.0
CC_PORT=8000
CC_JOBS_DIR=jobs
CC_JOB_RETENTION_DAYS=7
CC_MAX_UPLOAD_MB=200
CC_STALL_WARN_MIN=5
```

- 우선순위: **OS 환경변수 > `.env` > `config.yaml` > 코드 기본값.**
- 연결·비밀값만 `.env` 에 둔다. 튜닝값(recall_k, 배치 크기 …)은 `config.yaml` 에 남는다.
- 매핑은 `settings.py` 의 표 **한 곳**에만 정의한다. 새 키를 더할 때 그 표만 고치면 되게 하고,
  매핑된 키가 실제 `AppConfig` 필드에 닿는지 테스트로 고정한다(이 저장소가 두 번 당한
  "설정에는 있는데 호출 경로에는 없다" 결함을 막는다).
- 기존 `INTERNAL_LLM_API_KEY`(`api_key_env`)도 계속 동작한다.
- `.env`·`jobs/` 는 `.gitignore` 에 추가한다. `.env.example` 만 커밋한다.

## 11. 실행 파일

- `start.bat`
  1. `.venv`·`.env`·`web/dist` 확인. 없으면 무엇을 하라는 안내를 출력하고 멈춘다
     (`.env` 가 없으면 `.env.example` 복사를 안내).
  2. `.venv\Scripts\python -m contentcompare.web` 로 서버 기동(uvicorn 프로그램 방식, worker 1).
  3. 접속 주소(PC 이름·IP:포트)를 출력하고 로컬 브라우저를 연다.
  4. Ctrl+C 로 끄면 서버 종료 처리에서 실행 중인 worker 를 종료하고 `interrupted` 로 기록한다.
- `start.bat dev`: Vite 개발 서버(`/api` 를 8000 으로 프록시) + uvicorn 자동 재시작을 각각 창으로 띄운다.
- `setup.bat`: 기본 extras 에 `web` 추가, `web/` 에서 `npm ci && npm run build` 단계 추가
  (Node 가 없으면 건너뛰고 "빌드된 web/dist 를 복사하세요" 안내).
- 배치 파일은 기존 `setup.bat` 과 같이 CP949 로 저장한다.

## 12. 오류 처리

| 상황 | 처리 |
|---|---|
| 업로드 검증 실패 | 400 + 어떤 파일이 왜 거절됐는지. 작업을 만들지 않는다 |
| worker 가 예외로 종료 | `failed`, 마지막 오류 요약을 `job.json` 에, 전체는 로그에. 실행 창에 표시 |
| 일부 문서 실패(fact) | 작업은 `succeeded`, 결과 화면에 "문서 N건 처리 실패"(현재와 같음) |
| SSE 끊김 | 브라우저가 `Last-Event-ID` 로 재연결 |
| `web/dist` 없음 | 서버는 API 만 뜨고 `/` 에서 빌드 안내 페이지 |
| 관리자 비밀번호 틀림 | 401. 연속 5회 실패 시 1분 잠금 |

## 13. 테스트

- **백엔드(pytest, Office·LLM·네트워크 불필요)** — 기존 `FakeLLM` 과 같은 주입 방식.
  worker 가 호출할 파이프라인 팩토리를 주입 가능하게 해서 가짜 파이프라인으로 돈다.
  - 대기열: 접수 순서, 동시 실행 1, 대기 중·실행 중 취소, 재시작 시 `interrupted`/복원
  - 업로드: 확장자·크기·경로 탐색·`~$` 제외·폴더 하위 경로 보존
  - 관리자: 인증, 세션 만료, 잠금, 로그 레벨 필터·검색·꼬리 읽기
  - 설정: `.env` 우선순위, 매핑 키가 `AppConfig` 에 닿는지
  - SSE: 이벤트 순서, `Last-Event-ID` 재개
  - 허용 루트 밖 경로 거부
- **`progress.py`**: 단조 증가, 배치 분할 시 계산, 캐시 적중, 실패 문서, N 불변.
- **파이프라인 훅**: 가짜 LLM 으로 fact·rag 를 돌려 `plan`→`done` 이벤트가 짝 맞게 나오는지.
- **프론트**: `tsc` 타입 검사 + 빌드 통과. 진행률 표시 포맷 등 계산이 있는 부분만 vitest.
- **수동 점검(서버 PC, 실제 Office·LLM, 1회)**
  1. 다른 PC 브라우저에서 접속해 연결 테스트
  2. Excel 기준 + Word/PPT 대상 업로드(폴더 업로드 포함) → 실행 → 진행률·로그 확인
  3. 두 브라우저에서 거의 동시에 실행 → 두 번째가 "대기 중 · 앞에 1건"
  4. 실행 중 취소 → Office 프로세스가 남지 않음, 사람이 연 Office 는 유지
  5. 실행 창 닫았다 "내 작업"에서 다시 열기, 새 창으로 열기
  6. 결과·리포트·현미경·타임라인·도메인 지식 탭이 Streamlit 과 같은 내용
  7. 관리자 로그인 → 작업 DEBUG 로그 조회·검색·다운로드
  8. 서버 재시작 → 실행 중이던 작업 `interrupted`, 대기 작업 복원

## 14. 이후 논의 (이번 범위 밖)

- 공용 산출물 캐시(파일 내용 해시 기준) — 같은 문서 재실행 비용 절감
- 동시 실행 2건 이상 — COM 전역 잠금 + 프로세스 간 공유 요청 한도가 필요
- 사용자 로그인·"본인 작업만 보기"
- 관리자 페이지 확장(설정 요약, 사용량 통계 등)
- HTTPS

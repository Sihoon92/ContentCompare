# 웹 전환 3/3 — React 화면과 실행 파일 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 계획 2 의 FastAPI 서버 위에서 Streamlit 과 같은 화면·조작을 제공하는 React 화면(좌측 LLM 연결 테스트 + 탭 5개 + 실행 창 + 관리자 페이지)과, 프론트·백엔드를 한 번에 띄우는 `start.bat` 을 만든다.

**Architecture:** `web/` 에 Vite + React + TypeScript SPA 를 두고 `web/dist` 로 빌드하면 서버(`contentcompare/web/static.py`)가 그대로 서빙한다. 모든 API 호출은 `web/src/api/` 한 곳을 거치고, 계산이 있는 로직(진행률 표시·SSE 이벤트 누적·업로드 목록·오류 메시지 해석)은 `web/src/lib/` 의 순수 함수로 두어 vitest 로 시험한다. 현미경·타임라인 HTML 은 서버가 만든 것을 격리된 iframe(`srcDoc`, `sandbox="allow-scripts"`)에 넣는다.

**Tech Stack:** Node 22 / npm 10, Vite 5, React 18, TypeScript 5, react-router-dom 6, react-markdown 9 + remark-gfm 4, vitest 2. 배치 파일(cmd). 백엔드는 Python(계획 2).

**Spec:** `docs/superpowers/specs/2026-09-29-web-frontend-design.md` — §8(화면), §11(실행 파일), §13(수동 점검)을 구현한다. API 는 §9(계획 2 Task 13 에서 실제 경로로 갱신됨).

**앞선 계획:** 1/3 진행률, 2/3 백엔드(`contentcompare/web/`). 이 계획은 백엔드 API 를 **바꾸지 않는다** — 예외는 Task 8 의 개발용 앱 팩토리 `dev_app()` 하나.

## 선행 조건

- 브랜치: `claude/word-block-boundary`(계획 2 병합 완료) 위에서 `feat/web-frontend` 를 만든다.
- 이 PC 에 Node 22·npm 10 이 있다. npm 레지스트리 접근이 필요하다(Task 1 의 `npm install`). **서버 PC 에는 Node 가 없을 수 있다** — 그 경우 이 PC 에서 빌드한 `web/dist` 를 복사한다(`setup.bat` 이 안내).
- Python 테스트 기준은 계획 2 와 같다: 운영 파이썬 `python`(anaconda). `.venv` 는 fastapi 가 없어 웹 테스트를 건너뛴다.

## Global Constraints

- 화면 문구는 한국어, 식별자는 영어, 주석은 한국어. 판정 라벨 등 서버가 준 문자열은 **그대로** 표시한다(RAG·fact 라벨을 화면에서 새로 만들지 않는다).
- 의존성은 이 목록만: `react`, `react-dom`, `react-router-dom`, `react-markdown`, `remark-gfm` / 개발용 `vite`, `@vitejs/plugin-react`, `typescript`, `vitest`, `@types/react`, `@types/react-dom`. UI 프레임워크·CSS 라이브러리를 더하지 않는다(스타일은 `web/src/styles.css` 하나).
- **XSS 규칙:** `dangerouslySetInnerHTML` 금지. 사용자 입력(요청자 이름·파일명·로그)은 텍스트로만 렌더링한다(React 기본 이스케이프). 마크다운은 `react-markdown` 에 `skipHtml`. 서버가 만든 HTML(현미경·타임라인)은 **`<iframe srcDoc sandbox="allow-scripts">` 로만** 넣는다 — `allow-same-origin` 을 주지 않아 그 HTML 의 스크립트는 쿠키·API 에 접근하지 못한다.
- 모든 API 호출은 `web/src/api/endpoints.ts` 를 거치고, 응답 타입은 `web/src/api/types.ts` 가 백엔드와 1:1 로 맞춘다.
- 계산이 있는 코드는 `web/src/lib/`·`web/src/api/client.ts` 의 순수 함수로 두고 vitest 로 시험한다. 컴포넌트는 그 함수를 부르기만 한다.
- 각 프론트 태스크의 완료 조건: 저장소 루트에서 `npm --prefix web run typecheck`, `npm --prefix web test`, `npm --prefix web run build` 가 모두 통과. Python 스위트(`python -m pytest -q -p no:cacheprovider`)는 변하지 않아야 한다.
- `.bat` 파일은 **CP949 + CRLF** 로 저장한다(기존 `setup.bat` 과 같은 이유 — 한글이 cmd 콘솔에 깨지지 않게, 레이블·goto 가 LF 에서 오동작하지 않게). CP949 에 없는 문자(`—`, `✓`, `⚠` 등)를 쓰지 않는다.
- 커밋은 그 태스크의 파일만 `git add <경로>` 로 올린다. 메시지 끝에는 자기 환경이 지시하는 attribution trailer(없으면 아래 두 줄):
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_015WPCPHZGMqNLc7QQAv1ako
  ```

## Review Focus

1. **폴더 업로드의 한글·중첩 경로와 섞여 들어온 잠금 파일**(`자료/하위/규격서 v2.docx`, `~$규격서 v2.docx`, `메모.txt`) — 경로는 그대로, 잠금·미지원 파일은 건너뛰고 알린다. → Task 2 `addTargets` / `buildJobForm` 테스트
2. **로그가 수만 줄** — 실행 창이 멈추지 않도록 최근 2000줄만 유지한다. → Task 2 `applyEvent` 상한 테스트
3. **모양이 틀린 SSE 데이터·모르는 이벤트** — 무시하고 화면이 죽지 않는다. → Task 2 `applyEvent` 테스트
4. **도메인 지식 저장 충돌(409)의 본문 해석** — 서버 내용과 mtime 을 꺼내 "불러오기/덮어쓰기"를 제공한다. → Task 6 `knowledgeConflict` 테스트
5. **서버 오류 본문의 여러 모양**(문자열 `detail`, `{message}` 객체, 검증 오류 목록) — 사람이 읽을 한 줄로 보인다. → Task 1 `detailMessage` 테스트

---

## 파일 구조

| 파일 | 책임 | 태스크 |
|---|---|---|
| `web/package.json`, `web/package-lock.json`, `web/tsconfig.json`, `web/vite.config.ts`, `web/index.html` | 빌드·개발 서버(`/api` 프록시)·테스트 설정 | 1 |
| `web/src/main.tsx`, `web/src/App.tsx`, `web/src/styles.css` | 진입점·라우팅·스타일 | 1 (App 은 3·7 에서 라우트 추가) |
| `web/src/api/types.ts` | 백엔드 응답 타입 | 1 |
| `web/src/api/client.ts` (+`.test.ts`) | fetch 래퍼, `ApiError`, `detailMessage`, `errorText`, `fetchText` | 1 |
| `web/src/api/endpoints.ts` | 엔드포인트 함수 전부 | 1 |
| `web/src/lib/requester.ts` | 요청자 이름(localStorage) | 1 |
| `web/src/components/Header.tsx`, `LlmPanel.tsx` | 상단 바·좌측 LLM 점검 | 1 |
| `web/src/pages/MainPage.tsx`, `web/src/tabs/index.ts` | 좌측 패널 + 탭 틀(탭 목록은 3~6 에서 추가) | 1 |
| `web/src/lib/progress.ts`, `format.ts`, `jobEvents.ts`, `uploads.ts` (+`.test.ts`) | 진행률 문구·상태 문구·SSE 누적·업로드 목록 | 2 |
| `web/src/hooks/useJobStream.ts` | EventSource → `applyEvent` | 3 |
| `web/src/components/JobView.tsx`, `JobWindow.tsx`, `LogPanel.tsx`, `ResultView.tsx`, `DataTable.tsx`, `Markdown.tsx` | 실행 창·결과 | 3 |
| `web/src/pages/JobPage.tsx` | `/jobs/:id` 새 창 | 3 |
| `web/src/tabs/RunTab.tsx`, `web/src/components/JobList.tsx` | 🚀 비교 실행 탭 | 4 |
| `web/src/components/HtmlFrame.tsx`, `web/src/tabs/ReportTab.tsx`, `MicroTab.tsx`, `TimelineTab.tsx` | 📄·🔬·⏱ 탭 | 5 |
| `web/src/lib/knowledge.ts` (+`.test.ts`), `web/src/tabs/KnowledgeTab.tsx` | 📚 탭 | 6 |
| `web/src/pages/AdminPage.tsx`, `web/src/components/LogViewer.tsx`, `AdminJobs.tsx` | 관리자 페이지 | 7 |
| `contentcompare/web/__main__.py` (+`tests/test_web_main.py`) | `dev_app()` 팩토리 | 8 |
| `start.bat`, `setup.bat` | 실행·설치 | 8 |
| `docs/USER_GUIDE.md`, `docs/WEB_MANUAL_CHECK.md`, `CLAUDE.md`, `.gitignore` | 문서·제외 | 1(.gitignore), 8 |

---

### Task 1: 프론트 뼈대 — 빌드 설정·API 클라이언트·상단 바·LLM 점검

**Files:**
- Create: `web/package.json`, `web/tsconfig.json`, `web/vite.config.ts`, `web/index.html`
- Create: `web/src/main.tsx`, `web/src/App.tsx`, `web/src/styles.css`
- Create: `web/src/api/types.ts`, `web/src/api/client.ts`, `web/src/api/client.test.ts`, `web/src/api/endpoints.ts`
- Create: `web/src/lib/requester.ts`
- Create: `web/src/components/Header.tsx`, `web/src/components/LlmPanel.tsx`
- Create: `web/src/pages/MainPage.tsx`, `web/src/tabs/index.ts`
- Modify: `.gitignore` (`web/node_modules/`)
- Generated: `web/package-lock.json` (Step 2 의 `npm install` 이 만든다 — 커밋한다)

**Interfaces:**
- Consumes: 계획 2 의 API(§9).
- Produces (이후 모든 태스크가 쓴다):
  - `types.ts`: `JobState`, `Job`, `JobDetail`, `JobList`, `SubmitResult`, `UnitState`, `ProgressCurrent`, `Snapshot`, `CheckLine`, `LlmCheck`, `CountItem`, `Row`, `RagDetail`, `RagResult`, `FactResult`, `JobResult`, `ReportItem`, `MicroRun`, `MicroOptions`, `MicroHtmlParams`, `MicroHtml`, `TimelineItem`, `KnowledgeFileInfo`, `KnowledgeList`, `KnowledgeFile`, `KnowledgeConflict`, `AdminStatus`, `LogSource`, `LogRecord`, `LogPage`, `AdminJob`
  - `client.ts`: `class ApiError(status: number, message: string, body: unknown)`, `detailMessage(body: unknown, fallback: string): string`, `errorText(e: unknown): string`, `request<T>(method, path, body?): Promise<T>`, `fetchText(path): Promise<string>`
  - `endpoints.ts`: `checkLlm`, `listJobs`, `getJob`, `submitJob`, `cancelJob`, `getResult`, `getJobReport`, `jobReportUrl`, `jobEventsUrl`, `listReports`, `getReport`, `listMicroRuns`, `getMicroOptions`, `getMicroHtml`, `listTimelines`, `getTimelineHtml`, `listKnowledge`, `getKnowledge`, `saveKnowledge`, `getMergedKnowledge`, `adminStatus`, `adminLogin`, `adminLogout`, `adminLogSources`, `readAdminLog`, `adminLogDownloadUrl`, `adminJobs`, `adminCancel`, `adminDelete`
  - `requester.ts`: `loadRequester(): string`, `useRequester(): [string, (v: string) => void]`
  - `tabs/index.ts`: `interface TabDef { key: string; title: string; Component: ComponentType }`, `TABS: TabDef[]`(이 태스크에서는 빈 배열)

- [ ] **Step 1: 설정 파일 작성**

`web/package.json`:

```json
{
  "name": "contentcompare-web",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc --noEmit && vite build",
    "typecheck": "tsc --noEmit",
    "test": "vitest run"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-markdown": "^9.0.1",
    "react-router-dom": "^6.26.2",
    "remark-gfm": "^4.0.0"
  },
  "devDependencies": {
    "@types/react": "^18.3.11",
    "@types/react-dom": "^18.3.1",
    "@vitejs/plugin-react": "^4.3.2",
    "typescript": "^5.6.3",
    "vite": "^5.4.9",
    "vitest": "^2.1.3"
  }
}
```

`web/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "jsx": "react-jsx",
    "strict": true,
    "noEmit": true,
    "isolatedModules": true,
    "skipLibCheck": true,
    "types": ["vite/client"]
  },
  "include": ["src", "vite.config.ts"]
}
```

`web/vite.config.ts`:

```ts
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// 개발 모드(start.bat dev)에서는 Vite(5173)가 화면을, uvicorn(8000)이 API 를 맡는다.
// /api 를 프록시해 브라우저 입장에서는 같은 출처가 된다(쿠키·SSE 가 그대로 동작).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://localhost:8000", changeOrigin: true } },
  },
  build: { outDir: "dist", emptyOutDir: true },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
```

`web/index.html`:

```html
<!doctype html>
<html lang="ko">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>ContentCompare</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

`.gitignore` — `jobs/` 줄 **아래**에 추가:

```
web/node_modules/
```

- [ ] **Step 2: 의존성 설치**

Run: `npm --prefix web install`
Expected: `added N packages` 와 함께 `web/package-lock.json` 이 생긴다. 경고는 무방, 오류(ERR!)는 없어야 한다.

- [ ] **Step 3: 실패하는 테스트** — `web/src/api/client.test.ts`

```ts
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, detailMessage, errorText, request } from "./client";

describe("detailMessage", () => {
  it("문자열 detail", () => {
    expect(detailMessage({ detail: "알 수 없는 엔진입니다" }, "대체")).toBe("알 수 없는 엔진입니다");
  });
  it("객체 detail 의 message(지식 저장 충돌)", () => {
    const body = { detail: { message: "다른 사람이 먼저 저장했습니다.", current: { content: "", mtime: 1 } } };
    expect(detailMessage(body, "대체")).toBe("다른 사람이 먼저 저장했습니다.");
  });
  it("검증 오류 목록", () => {
    expect(detailMessage({ detail: [{ msg: "field required" }, { msg: "bad" }] }, "대체"))
      .toBe("field required; bad");
  });
  it("모양을 모르면 대체 문구", () => {
    expect(detailMessage(null, "대체")).toBe("대체");
    expect(detailMessage("본문", "대체")).toBe("대체");
    expect(detailMessage({ detail: 3 }, "대체")).toBe("대체");
  });
});

describe("request", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("JSON 응답을 돌려준다", async () => {
    vi.stubGlobal("fetch", vi.fn(async (_url: string, _init?: RequestInit) =>
      new Response(JSON.stringify({ ok: true }), { status: 200 })));
    await expect(request("GET", "/api/x")).resolves.toEqual({ ok: true });
  });

  it("오류면 서버 메시지를 담은 ApiError", async () => {
    vi.stubGlobal("fetch", vi.fn(async (_url: string, _init?: RequestInit) =>
      new Response(JSON.stringify({ detail: "작업을 찾을 수 없습니다." }), { status: 404 })));
    const err = (await request("GET", "/api/x").catch((e: unknown) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(404);
    expect(err.message).toBe("작업을 찾을 수 없습니다.");
    expect(errorText(err)).toBe("작업을 찾을 수 없습니다.");
  });

  it("JSON 이 아닌 오류 본문은 상태코드 문구", async () => {
    vi.stubGlobal("fetch", vi.fn(async (_url: string, _init?: RequestInit) =>
      new Response("Internal Server Error", { status: 500 })));
    const err = (await request("GET", "/api/x").catch((e: unknown) => e)) as ApiError;
    expect(err.message).toBe("요청 실패 (500)");
  });

  it("객체 본문은 JSON 으로, FormData 는 그대로 보낸다", async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) =>
      new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await request("PUT", "/api/k", { a: 1 });
    const json = fetchMock.mock.calls[0][1] as RequestInit;
    expect(json.body).toBe('{"a":1}');
    expect((json.headers as Record<string, string>)["Content-Type"]).toBe("application/json");

    const form = new FormData();
    form.append("engine", "fact");
    await request("POST", "/api/jobs", form);
    const multipart = fetchMock.mock.calls[1][1] as RequestInit;
    expect(multipart.body).toBe(form);
    expect(multipart.headers).toBeUndefined();
  });
});
```

- [ ] **Step 4: 실패 확인**

Run: `npm --prefix web test`
Expected: FAIL — `Failed to resolve import "./client"`(파일 없음)

- [ ] **Step 5: 구현 — API 계층**

`web/src/api/types.ts`:

```ts
// 백엔드(contentcompare/web/) 응답과 1:1. 필드를 바꾸면 서버 쪽도 함께 본다.

export type JobState = "queued" | "running" | "succeeded" | "failed" | "cancelled" | "interrupted";
export type Engine = "rag" | "fact";

export interface Job {
  id: string;
  engine: Engine;
  requester: string;
  reference: string;
  targets: string[];
  state: JobState;
  created_ts: number;
  started_ts: number;
  finished_ts: number;
  error: string;
  llm: Record<string, string>;
  mine: boolean;
  position: number | null;
}

export interface UnitState {
  key: string;
  label: string;
  kind: string;
  state: "pending" | "running" | "done" | "failed" | "skipped";
  error: string;
}

export interface ProgressCurrent {
  key: string;
  label: string;
  part_name: string;
  part_index: number;
  parts: number;
  step_done: number;
  step_total: number;
}

export interface Snapshot {
  units: UnitState[];
  total: number;
  finished: number;
  fraction: number;
  percent: number;
  units_done: number;
  current: ProgressCurrent | null;
  started_ts: number;
  last_ts: number;
  last_seq: number;
}

export interface JobDetail extends Job {
  progress: Snapshot;
}

export interface JobList {
  jobs: Job[];
  running: string | null;
}

export interface SubmitResult {
  job: Job;
  skipped: string[];
}

export interface CheckLine {
  name: string;
  ok: boolean;
  detail: string;
  line: string;
}

export interface LlmCheck {
  ok: boolean;
  results: CheckLine[];
  cached: boolean;
}

export interface CountItem {
  key: string;
  label: string;
  n: number;
}

export type Row = Record<string, string | number | boolean | null>;

export interface RagDetail {
  index: number;
  label: string;
  verdict: string;
  source_label: string;
  reference_text: string;
  is_record: boolean;
  sources: string[];
  reasoning: string;
  fields: Row[];
  candidates: { score: number; source_label: string; matched: boolean }[];
}

export interface RagResult {
  engine: "rag";
  counts: CountItem[];
  summary: Row[];
  details: RagDetail[];
}

export interface FactResult {
  engine: "fact";
  counts: CountItem[];
  summary: Row[];
  failed_docs: { name: string; error: string }[];
  compare_stats: Record<string, unknown>;
}

export type JobResult = RagResult | FactResult;

export interface ReportItem {
  id: string;
  name: string;
  mtime: number;
}

export interface MicroRun {
  id: string;
  label: string;
  snapshot: boolean;
}

export interface MicroOptions {
  reference_doc: string;
  target_docs: string[];
  learn_docs: string[];
  facts: Record<string, { id: string; name: string }[]>;
  capabilities: string[];
  problems: string[];
  result_choices: { key: string; label: string }[];
  default_results: string[];
}

export interface MicroHtmlParams {
  run: string;
  mode: "debug" | "learn";
  target?: string;
  results?: string;
  doc?: string;
  fact?: string;
  theme?: "light" | "dark";
}

export interface MicroHtml {
  html: string;
  height: number;
  notes: string[];
  unavailable: boolean;
}

export interface TimelineItem {
  id: string;
  label: string;
  mtime: number;
}

export interface KnowledgeFileInfo {
  name: string;
  mtime: number;
  size: number;
}

export interface KnowledgeList {
  files: KnowledgeFileInfo[];
  enabled: boolean;
  template: string;
}

export interface KnowledgeFile {
  name: string;
  content: string;
  mtime: number;
}

export interface KnowledgeConflict {
  message: string;
  current: { content: string; mtime: number };
}

export interface AdminStatus {
  enabled: boolean;
  logged_in: boolean;
}

export interface LogSource {
  id: string;
  label: string;
  size: number;
  mtime: number;
}

export interface LogRecord {
  level: string;
  text: string;
}

export interface LogPage {
  records: LogRecord[];
  start: number;
  end: number;
  total: number;
}

export interface AdminJob extends Omit<Job, "mine"> {
  client_id: string;
}
```

`web/src/api/client.ts`:

```ts
// API 호출의 유일한 출입구. 서버 오류는 사람이 읽을 한 줄(detailMessage)로 바꿔 ApiError 로 던진다.

export class ApiError extends Error {
  constructor(public status: number, message: string, public body: unknown) {
    super(message);
    this.name = "ApiError";
  }
}

/** FastAPI 오류 본문 → 한 줄. detail 이 문자열·{message} 객체·검증 오류 목록일 수 있다. */
export function detailMessage(body: unknown, fallback: string): string {
  if (!body || typeof body !== "object" || !("detail" in body)) return fallback;
  const detail = (body as { detail: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => (item && typeof item === "object" && "msg" in item
        ? String((item as { msg: unknown }).msg)
        : String(item)))
      .join("; ");
  }
  if (detail && typeof detail === "object") {
    const message = (detail as { message?: unknown }).message;
    if (typeof message === "string") return message;
  }
  return fallback;
}

export function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

function parse(text: string): unknown {
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

export async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, credentials: "same-origin" };
  if (body instanceof FormData) {
    init.body = body; // 경계(boundary)는 브라우저가 붙인다 — Content-Type 을 직접 넣지 말 것
  } else if (body !== undefined) {
    init.body = JSON.stringify(body);
    init.headers = { "Content-Type": "application/json" };
  }
  const res = await fetch(path, init);
  const data = parse(await res.text());
  if (!res.ok) throw new ApiError(res.status, detailMessage(data, `요청 실패 (${res.status})`), data);
  return data as T;
}

/** 본문을 문자열 그대로 받는다(리포트 markdown). */
export async function fetchText(path: string): Promise<string> {
  const res = await fetch(path, { credentials: "same-origin" });
  const text = await res.text();
  if (!res.ok) {
    const data = parse(text);
    throw new ApiError(res.status, detailMessage(data, `요청 실패 (${res.status})`), data);
  }
  return text;
}
```

`web/src/api/endpoints.ts`:

```ts
import { fetchText, request } from "./client";
import type {
  AdminJob, AdminStatus, JobDetail, JobList, JobResult, KnowledgeFile, KnowledgeList, LlmCheck,
  LogPage, LogSource, MicroHtml, MicroHtmlParams, MicroOptions, MicroRun, ReportItem, SubmitResult,
  TimelineItem, Job,
} from "./types";

type Query = Record<string, string | number | boolean | null | undefined>;

function qs(params: Query): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null) search.set(key, String(value));
  }
  return search.toString();
}

const job = (id: string) => `/api/jobs/${encodeURIComponent(id)}`;

// --- LLM 점검 ---------------------------------------------------------------
export const checkLlm = () => request<LlmCheck>("POST", "/api/llm/check");

// --- 작업 ---------------------------------------------------------------------
export const listJobs = () => request<JobList>("GET", "/api/jobs");
export const getJob = (id: string) => request<JobDetail>("GET", job(id));
export const submitJob = (form: FormData) => request<SubmitResult>("POST", "/api/jobs", form);
export const cancelJob = (id: string) => request<{ job: Job }>("POST", `${job(id)}/cancel`);
export const getResult = (id: string) =>
  request<{ result: JobResult; report: boolean }>("GET", `${job(id)}/result`);
export const getJobReport = (id: string) => fetchText(`${job(id)}/report`);
export const jobReportUrl = (id: string) => `${job(id)}/report`;
export const jobEventsUrl = (id: string) => `${job(id)}/events`;

// --- 조회 ---------------------------------------------------------------------
export const listReports = () => request<{ reports: ReportItem[] }>("GET", "/api/reports");
export const getReport = (id: string) =>
  request<{ id: string; markdown: string }>("GET", `/api/reports/content?${qs({ id })}`);
export const listMicroRuns = () => request<{ runs: MicroRun[] }>("GET", "/api/micro/runs");
export const getMicroOptions = (run: string) =>
  request<MicroOptions>("GET", `/api/micro/options?${qs({ run })}`);
export const getMicroHtml = (p: MicroHtmlParams) =>
  request<MicroHtml>("GET", `/api/micro/html?${qs({ ...p })}`);
export const listTimelines = () => request<{ timelines: TimelineItem[] }>("GET", "/api/timelines");
export const getTimelineHtml = (run: string, errorsOnly: boolean) =>
  request<{ html: string }>("GET", `/api/timelines/html?${qs({ run, errors_only: errorsOnly })}`);

// --- 도메인 지식 ------------------------------------------------------------------
const knowledge = (name: string) => `/api/knowledge/files/${encodeURIComponent(name)}`;
export const listKnowledge = () => request<KnowledgeList>("GET", "/api/knowledge/files");
export const getKnowledge = (name: string) => request<KnowledgeFile>("GET", knowledge(name));
export const saveKnowledge = (name: string, content: string, baseMtime: number | null) =>
  request<{ name: string; mtime: number }>("PUT", knowledge(name), { content, base_mtime: baseMtime });
export const getMergedKnowledge = () => request<{ text: string }>("GET", "/api/knowledge/merged");

// --- 관리자 -------------------------------------------------------------------
export const adminStatus = () => request<AdminStatus>("GET", "/api/admin/status");
export const adminLogin = (password: string) => request<{ ok: boolean }>("POST", "/api/admin/login", { password });
export const adminLogout = () => request<{ ok: boolean }>("POST", "/api/admin/logout");
export const adminLogSources = () => request<{ sources: LogSource[] }>("GET", "/api/admin/logs");
export const readAdminLog = (p: { source: string; level: string; q: string; tail: number; before?: number }) =>
  request<LogPage>("GET", `/api/admin/logs/read?${qs(p)}`);
export const adminLogDownloadUrl = (source: string) => `/api/admin/logs/download?${qs({ source })}`;
export const adminJobs = () => request<{ jobs: AdminJob[] }>("GET", "/api/admin/jobs");
export const adminCancel = (id: string) => request<{ ok: boolean }>("POST", `/api/admin/jobs/${encodeURIComponent(id)}/cancel`);
export const adminDelete = (id: string) => request<{ ok: boolean }>("DELETE", `/api/admin/jobs/${encodeURIComponent(id)}`);
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `npm --prefix web test`
Expected: `8 passed`

- [ ] **Step 7: 구현 — 화면 틀**

`web/src/lib/requester.ts`:

```ts
import { useEffect, useState } from "react";

// 로그인이 없으므로 요청자 이름은 이 브라우저에만 기억한다(작업 목록 표시용).
const KEY = "cc_requester";

export function loadRequester(): string {
  try {
    return localStorage.getItem(KEY) ?? "";
  } catch {
    return ""; // 사생활 보호 모드 등 저장소를 못 쓰면 빈 이름으로 동작한다
  }
}

export function useRequester(): [string, (value: string) => void] {
  const [name, setName] = useState(loadRequester);
  useEffect(() => {
    try {
      localStorage.setItem(KEY, name);
    } catch {
      // 저장하지 못해도 화면은 계속 동작한다
    }
  }, [name]);
  return [name, setName];
}
```

`web/src/components/Header.tsx`:

```tsx
import { Link } from "react-router-dom";
import { useRequester } from "../lib/requester";

export default function Header() {
  const [name, setName] = useRequester();
  return (
    <header className="app-header">
      <Link to="/" className="brand">📑 ContentCompare</Link>
      <label className="requester">
        이름
        <input value={name} maxLength={40} placeholder="요청자 이름" onChange={(e) => setName(e.target.value)} />
      </label>
      <Link to="/admin" className="button secondary">🔐 관리자</Link>
    </header>
  );
}
```

`web/src/components/LlmPanel.tsx`:

```tsx
import { useState } from "react";
import { errorText } from "../api/client";
import { checkLlm } from "../api/endpoints";
import type { LlmCheck } from "../api/types";

// 좌측 패널에는 이 버튼만 둔다(설계 §8.1). 접속 정보는 서버의 .env 에서 온다.
export default function LlmPanel() {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<LlmCheck | null>(null);
  const [error, setError] = useState("");

  async function run() {
    setBusy(true);
    setError("");
    try {
      setResult(await checkLlm());
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <aside className="side-panel">
      <button className="button primary wide" disabled={busy} onClick={run}>
        {busy ? "점검 중..." : "🔌 LLM 연결 테스트"}
      </button>
      {error && <p className="error">{error}</p>}
      {result && (
        <div className="check-result">
          <ul>
            {result.results.map((r, i) => (
              <li key={i} className={r.ok ? "ok" : "bad"}>{r.line}</li>
            ))}
          </ul>
          <p className="muted">
            {result.ok ? "모두 ✅ 면 비교 실행 준비 완료" : "실패 항목의 메시지를 확인하세요"}
            {result.cached ? " (30초 안의 점검 결과를 다시 보여 줍니다)" : ""}
          </p>
        </div>
      )}
    </aside>
  );
}
```

`web/src/tabs/index.ts`:

```ts
import type { ComponentType } from "react";

export interface TabDef {
  key: string;
  title: string;
  Component: ComponentType;
}

// 탭은 Streamlit 과 같은 순서로 채운다: 🚀 비교 실행 · 📄 리포트 · 🔬 현미경 · ⏱ 타임라인 · 📚 도메인 지식.
export const TABS: TabDef[] = [];
```

`web/src/pages/MainPage.tsx`:

```tsx
import { useState } from "react";
import LlmPanel from "../components/LlmPanel";
import { TABS } from "../tabs";

export default function MainPage() {
  const [active, setActive] = useState(TABS[0]?.key ?? "");
  return (
    <div className="layout">
      <LlmPanel />
      <main className="main">
        <nav className="tabs">
          {TABS.map((t) => (
            <button key={t.key} className={t.key === active ? "tab active" : "tab"} onClick={() => setActive(t.key)}>
              {t.title}
            </button>
          ))}
        </nav>
        {TABS.length === 0 && <p className="muted">표시할 탭이 없습니다.</p>}
        {/* 탭을 숨기기만 하고 내리지 않는다 — 고른 파일·입력한 내용이 탭을 옮겨도 남게(Streamlit 과 같다). */}
        {TABS.map((t) => (
          <section key={t.key} className="tab-body" hidden={t.key !== active}>
            <t.Component />
          </section>
        ))}
      </main>
    </div>
  );
}
```

`web/src/App.tsx`:

```tsx
import { Link, Route, Routes } from "react-router-dom";
import Header from "./components/Header";
import MainPage from "./pages/MainPage";

function NotFound() {
  return (
    <div className="page">
      <p>없는 화면입니다.</p>
      <Link to="/">처음으로</Link>
    </div>
  );
}

export default function App() {
  return (
    <>
      <Header />
      <Routes>
        <Route path="/" element={<MainPage />} />
        <Route path="*" element={<NotFound />} />
      </Routes>
    </>
  );
}
```

`web/src/main.tsx`:

```tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
);
```

`web/src/styles.css`:

```css
:root {
  --bg: #f6f7f9;
  --panel: #ffffff;
  --text: #1f2328;
  --muted: #6b7280;
  --line: #e3e6ea;
  --primary: #1565c0;
  --danger: #c62828;
  --warn: #b26a00;
  --ok: #2e7d32;
  --code-bg: #111827;
  --code-text: #e5e7eb;
  --radius: 8px;
  font-family: system-ui, "Malgun Gothic", sans-serif;
  color: var(--text);
  background: var(--bg);
}
* { box-sizing: border-box; }
body { margin: 0; }
a { color: var(--primary); }
h3 { margin: 18px 0 8px; font-size: 16px; }
h4 { margin: 14px 0 6px; font-size: 14px; }

.app-header { display: flex; align-items: center; gap: 16px; padding: 10px 20px; background: var(--panel); border-bottom: 1px solid var(--line); }
.brand { margin-right: auto; font-weight: 700; font-size: 18px; color: var(--text); text-decoration: none; }
.requester { display: flex; align-items: center; gap: 6px; font-size: 14px; }

.layout { display: grid; grid-template-columns: 260px 1fr; gap: 16px; padding: 16px 20px; }
@media (max-width: 900px) { .layout { grid-template-columns: 1fr; } }
.side-panel, .main, .page { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: 16px; }
.page { margin: 16px 20px; }

.tabs { display: flex; flex-wrap: wrap; gap: 4px; border-bottom: 1px solid var(--line); margin-bottom: 16px; }
.tab { border: none; background: none; padding: 8px 14px; cursor: pointer; font-size: 14px; border-bottom: 2px solid transparent; }
.tab.active { border-bottom-color: var(--primary); color: var(--primary); font-weight: 600; }

.button { display: inline-flex; align-items: center; gap: 6px; padding: 7px 14px; border: 1px solid var(--line); border-radius: 6px; background: var(--panel); color: var(--text); cursor: pointer; font-size: 14px; text-decoration: none; }
.button.primary { background: var(--primary); border-color: var(--primary); color: #fff; }
.button.danger { background: var(--danger); border-color: var(--danger); color: #fff; }
.button.link { border: none; background: none; padding: 0; color: var(--primary); }
.button.wide { width: 100%; justify-content: center; }
.button:disabled { opacity: 0.6; cursor: default; }

.row { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin: 8px 0; }
.field { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; margin-bottom: 8px; }
.label { font-weight: 600; }
.muted { color: var(--muted); font-size: 13px; }
.error { color: var(--danger); }
.warn { color: var(--warn); }

select, input:not([type]), input[type="text"], input[type="password"], input[type="number"], input[type="search"], textarea {
  padding: 6px 8px; border: 1px solid var(--line); border-radius: 6px; font: inherit;
}
textarea { width: 100%; font-family: ui-monospace, Consolas, monospace; font-size: 13px; }

.file-list { max-height: 200px; overflow: auto; margin: 6px 0; padding: 8px; border-radius: 6px; background: var(--bg); font-size: 13px; }
.table-wrap { overflow: auto; }
.table { width: 100%; border-collapse: collapse; font-size: 13px; }
.table th, .table td { padding: 6px 8px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
.table th { background: var(--bg); }

.check-result ul { list-style: none; margin: 12px 0 4px; padding: 0; font-size: 13px; }
.check-result li { padding: 2px 0; word-break: break-all; }
.check-result .bad { color: var(--danger); }

.modal-backdrop { position: fixed; inset: 0; z-index: 10; display: flex; align-items: flex-start; justify-content: center; padding: 32px 16px; overflow: auto; background: rgba(0, 0, 0, 0.35); }
.modal { width: min(1000px, 100%); padding: 16px; border-radius: var(--radius); background: var(--panel); }
.modal-toolbar { display: flex; justify-content: flex-end; gap: 8px; }
.job-mini { position: fixed; right: 16px; bottom: 16px; z-index: 10; display: flex; gap: 12px; padding: 8px 12px; border: 1px solid var(--line); border-radius: var(--radius); background: var(--panel); box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15); }

.job-status { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; }
.badge { padding: 2px 8px; border: 1px solid var(--line); border-radius: 999px; background: var(--bg); font-size: 13px; }
.badge.state-running { background: #e3f2fd; border-color: #90caf9; }
.badge.state-succeeded { background: #e8f5e9; border-color: #a5d6a7; }
.badge.state-failed, .badge.state-interrupted { background: #ffebee; border-color: #ef9a9a; }
.progress { height: 14px; margin: 12px 0 6px; overflow: hidden; border: 1px solid var(--line); border-radius: 999px; background: var(--bg); }
.progress-bar { height: 100%; background: var(--primary); transition: width 0.4s; }
.progress-meta { display: flex; flex-wrap: wrap; gap: 16px; font-size: 14px; }
.current { margin: 6px 0; font-size: 14px; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0; }
.chip { padding: 2px 8px; border: 1px solid var(--line); border-radius: 999px; background: var(--bg); font-size: 12px; }
.chip-done { color: var(--ok); }
.chip-running { color: var(--primary); font-weight: 600; }
.chip-failed { color: var(--danger); }
.chip-skipped, .chip-pending { color: var(--muted); }

.log-panel { margin: 12px 0; overflow: hidden; border: 1px solid var(--line); border-radius: var(--radius); }
.log-head { display: flex; justify-content: space-between; padding: 6px 10px; background: var(--bg); font-size: 13px; }
.log-body { height: 280px; margin: 0; padding: 8px 10px; overflow: auto; background: var(--code-bg); color: var(--code-text); font-size: 12px; white-space: pre-wrap; word-break: break-all; }
.log-line.warn { color: #fbbf24; }

.counts { display: flex; flex-wrap: wrap; gap: 12px; margin: 8px 0; }
.count { display: flex; flex-direction: column; min-width: 90px; padding: 8px 14px; border: 1px solid var(--line); border-radius: var(--radius); }
.count strong { font-size: 20px; }
details { margin: 6px 0; }
summary { cursor: pointer; }
.markdown { line-height: 1.6; }
.markdown table { border-collapse: collapse; }
.markdown th, .markdown td { padding: 4px 8px; border: 1px solid var(--line); }
.html-frame { width: 100%; border: 1px solid var(--line); border-radius: var(--radius); background: #fff; }

.log-view { max-height: 600px; margin: 8px 0; padding: 8px 10px; overflow: auto; border-radius: 6px; background: var(--code-bg); color: var(--code-text); font-size: 12px; white-space: pre-wrap; word-break: break-all; }
.lv-ERROR, .lv-CRITICAL { color: #f87171; }
.lv-WARNING { color: #fbbf24; }
.lv-DEBUG { color: #9ca3af; }
.admin-section { margin-top: 20px; }
.conflict { margin: 8px 0; padding: 10px; border: 1px solid var(--warn); border-radius: 6px; background: #fff8e1; }
```

- [ ] **Step 8: 전체 확인**

Run: `npm --prefix web run typecheck && npm --prefix web test && npm --prefix web run build`
Expected: 타입 오류 0, `8 passed`, `web/dist/index.html` 과 `web/dist/assets/*.js` 생성.

Run: `python -m pytest -q -p no:cacheprovider tests/test_web_admin_routes.py`
Expected: 전부 PASS(서버 코드는 무변경)

- [ ] **Step 9: 커밋**

```bash
git add web/package.json web/package-lock.json web/tsconfig.json web/vite.config.ts web/index.html web/src .gitignore
git commit -m "feat(web-ui): 프론트 뼈대 — Vite+React, API 클라이언트, 상단 바, LLM 연결 테스트"
```

---

### Task 2: 계산 로직 — 진행률 문구·상태 문구·SSE 누적·업로드 목록

**Files:**
- Create: `web/src/lib/progress.ts`, `web/src/lib/progress.test.ts`
- Create: `web/src/lib/format.ts`, `web/src/lib/format.test.ts`
- Create: `web/src/lib/jobEvents.ts`, `web/src/lib/jobEvents.test.ts`
- Create: `web/src/lib/uploads.ts`, `web/src/lib/uploads.test.ts`

**Interfaces:**
- Consumes: Task 1 `types.ts`.
- Produces:
  - `progress.ts`: `progressSummary(s: Snapshot | null): string`, `currentText(c: ProgressCurrent | null): string`, `unitChip(u: UnitState): { text: string; state: UnitState["state"]; title: string }`, `formatElapsed(seconds: number): string`
  - `format.ts`: `STATE_LABEL`, `FINAL_STATES`, `isFinal(state: string): boolean`, `statusText(job: { state: JobState; position: number | null }): string`, `shortId(id: string): string`, `formatClock(ts: number): string`, `formatDateTime(ts: number): string`
  - `jobEvents.ts`: `MAX_LINES = 2000`, `interface StreamStatus`, `interface StreamState { status; progress: Snapshot | null; lines: string[]; stalled: boolean; idleS: number; ended: boolean; notFound: boolean }`, `initialStream(): StreamState`, `applyEvent(state, type: string, data: unknown): StreamState`
  - `uploads.ts`: `SUPPORTED_EXTS`, `REFERENCE_EXTS`, `interface PickedFile { file: File; path: string }`, `relativePathOf(file: File): string`, `isReferenceName(name: string): boolean`, `addTargets(existing: PickedFile[], files: File[]): { items: PickedFile[]; skipped: string[] }`, `buildJobForm(engine: Engine, requester: string, reference: File, targets: PickedFile[]): FormData`

- [ ] **Step 1: 실패하는 테스트 4개**

`web/src/lib/progress.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import type { Snapshot } from "../api/types";
import { currentText, formatElapsed, progressSummary, unitChip } from "./progress";

const snap = (over: Partial<Snapshot> = {}): Snapshot => ({
  units: [], total: 8, finished: 4, fraction: 0.575, percent: 57, units_done: 4.6,
  current: null, started_ts: 0, last_ts: 0, last_seq: 0, ...over,
});

describe("progressSummary", () => {
  it("단계 수를 소수 한 자리로", () => expect(progressSummary(snap())).toBe("4.6 / 8 단계"));
  it("계획 전이면 준비 중", () => {
    expect(progressSummary(null)).toBe("준비 중");
    expect(progressSummary(snap({ total: 0 }))).toBe("준비 중");
  });
});

describe("currentText", () => {
  it("단위·하위 단계·진척", () => {
    expect(currentText({ key: "doc:2", label: "대상B.pptx", part_name: "F3 facts", part_index: 3,
      parts: 4, step_done: 3, step_total: 5 })).toBe("대상B.pptx · F3 facts · 3/5");
  });
  it("하위 단계·진척이 없으면 생략", () => {
    expect(currentText({ key: "concept", label: "F7 개념 판정", part_name: "", part_index: 0,
      parts: 1, step_done: 0, step_total: 0 })).toBe("F7 개념 판정");
  });
  it("없으면 빈 문자열", () => expect(currentText(null)).toBe(""));
});

describe("unitChip", () => {
  it("상태 기호와 실패 사유", () => {
    const chip = unitChip({ key: "doc:1", label: "깨진.xlsx", kind: "doc", state: "failed", error: "OSError" });
    expect(chip).toEqual({ text: "✗ 깨진.xlsx", state: "failed", title: "깨진.xlsx: OSError" });
  });
  it("진행 중·대기", () => {
    expect(unitChip({ key: "a", label: "A", kind: "doc", state: "running", error: "" }).text).toBe("▶ A");
    expect(unitChip({ key: "b", label: "B", kind: "doc", state: "pending", error: "" }).title).toBe("B");
  });
});

describe("formatElapsed", () => {
  it("분:초", () => expect(formatElapsed(750)).toBe("12:30"));
  it("한 시간이 넘으면 시:분:초", () => expect(formatElapsed(3725)).toBe("1:02:05"));
  it("음수·소수는 0 이상 정수", () => {
    expect(formatElapsed(-3)).toBe("0:00");
    expect(formatElapsed(59.9)).toBe("0:59");
  });
});
```

`web/src/lib/format.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { formatClock, isFinal, shortId, statusText } from "./format";

describe("statusText", () => {
  it("대기 중이면 앞에 몇 건", () => {
    expect(statusText({ state: "queued", position: 2 })).toBe("대기 중 · 앞에 2건");
    expect(statusText({ state: "queued", position: 0 })).toBe("대기 중 · 곧 시작");
    expect(statusText({ state: "queued", position: null })).toBe("대기 중 · 곧 시작");
  });
  it("나머지 상태", () => {
    expect(statusText({ state: "running", position: null })).toBe("실행 중");
    expect(statusText({ state: "interrupted", position: null })).toBe("중단됨");
  });
});

describe("isFinal", () => {
  it("끝난 상태만 참", () => {
    expect(isFinal("succeeded")).toBe(true);
    expect(isFinal("cancelled")).toBe(true);
    expect(isFinal("running")).toBe(false);
    expect(isFinal("")).toBe(false);
  });
});

describe("shortId / formatClock", () => {
  it("작업 ID 의 마지막 조각", () => expect(shortId("20260929-140211-a3f9")).toBe("a3f9"));
  it("시각은 HH:MM, 0 은 -", () => {
    expect(formatClock(0)).toBe("-");
    expect(formatClock(new Date(2026, 8, 29, 9, 5).getTime() / 1000)).toBe("09:05");
  });
});
```

`web/src/lib/jobEvents.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { MAX_LINES, applyEvent, initialStream } from "./jobEvents";

describe("applyEvent", () => {
  it("로그 줄을 이어 붙인다", () => {
    let s = applyEvent(initialStream(), "log", { lines: ["첫 줄"] });
    s = applyEvent(s, "log", { lines: ["둘", "셋"] });
    expect(s.lines).toEqual(["첫 줄", "둘", "셋"]);
  });

  it(`최근 ${MAX_LINES}줄만 남긴다`, () => {
    const many = Array.from({ length: MAX_LINES + 500 }, (_, i) => `줄 ${i}`);
    const s = applyEvent(initialStream(), "log", { lines: many });
    expect(s.lines).toHaveLength(MAX_LINES);
    expect(s.lines[0]).toBe("줄 500");
    expect(s.lines[MAX_LINES - 1]).toBe(`줄 ${MAX_LINES + 499}`);
  });

  it("진행·상태·멈춤·끝", () => {
    const progress = { units: [], total: 1, finished: 0, fraction: 0, percent: 0, units_done: 0,
      current: null, started_ts: 0, last_ts: 0, last_seq: 3 };
    let s = applyEvent(initialStream(), "progress", progress);
    expect(s.progress?.last_seq).toBe(3);
    s = applyEvent(s, "status", { state: "failed", error: "ValueError: x", started_ts: 10, finished_ts: 20 });
    expect(s.status).toEqual({ state: "failed", error: "ValueError: x", started_ts: 10, finished_ts: 20 });
    s = applyEvent(s, "stall", { stalled: true, idle_s: 400 });
    expect([s.stalled, s.idleS]).toEqual([true, 400]);
    s = applyEvent(s, "end", { state: "failed" });
    expect([s.ended, s.notFound]).toEqual([true, false]);
    expect(applyEvent(initialStream(), "end", { reason: "not_found" }).notFound).toBe(true);
  });

  it("모르는 이벤트·모양이 틀린 데이터는 무시하거나 안전하게", () => {
    const s0 = initialStream();
    expect(applyEvent(s0, "unknown", { a: 1 })).toBe(s0);
    expect(applyEvent(s0, "log", "문자열")).toBe(s0);
    expect(applyEvent(s0, "log", { lines: "줄 아님" })).toBe(s0);
    expect(applyEvent(s0, "status", null).status.state).toBe("");
    expect(applyEvent(s0, "stall", 42).stalled).toBe(false);
  });
});
```

`web/src/lib/uploads.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { addTargets, buildJobForm, isReferenceName, relativePathOf } from "./uploads";

const file = (name: string, relative = "") => {
  const f = new File(["x"], name);
  if (relative) Object.defineProperty(f, "webkitRelativePath", { value: relative });
  return f;
};

describe("relativePathOf / isReferenceName", () => {
  it("폴더 업로드면 상대 경로, 아니면 이름", () => {
    expect(relativePathOf(file("a.docx", "자료/a.docx"))).toBe("자료/a.docx");
    expect(relativePathOf(file("a.docx"))).toBe("a.docx");
  });
  it("기준은 Excel 만, 잠금 파일 제외", () => {
    expect(isReferenceName("기준.xlsx")).toBe(true);
    expect(isReferenceName("기준.XLSM")).toBe(true);
    expect(isReferenceName("기준.docx")).toBe(false);
    expect(isReferenceName("~$기준.xlsx")).toBe(false);
  });
});

describe("addTargets", () => {
  it("폴더 경로를 보존하고 잠금·미지원 파일은 건너뛴다", () => {
    const r = addTargets([], [
      file("규격서 v2.docx", "자료/하위/규격서 v2.docx"),
      file("~$규격서 v2.docx", "자료/하위/~$규격서 v2.docx"),
      file("메모.txt", "자료/메모.txt"),
      file("발표.PPTX"),
    ]);
    expect(r.items.map((i) => i.path)).toEqual(["자료/하위/규격서 v2.docx", "발표.PPTX"]);
    expect(r.skipped).toEqual(["자료/하위/~$규격서 v2.docx", "자료/메모.txt"]);
  });
  it("같은 경로를 다시 고르면 한 번만", () => {
    const first = addTargets([], [file("a.docx")]);
    const again = addTargets(first.items, [file("a.docx"), file("b.docx")]);
    expect(again.items.map((i) => i.path)).toEqual(["a.docx", "b.docx"]);
  });
});

describe("buildJobForm", () => {
  it("상대 경로는 target_paths 로, 파일 이름은 마지막 조각으로", () => {
    const targets = addTargets([], [file("규격서 v2.docx", "자료/규격서 v2.docx"), file("발표.pptx")]).items;
    const form = buildJobForm("fact", "  홍길동 ", file("기준.xlsx"), targets);
    expect(form.get("engine")).toBe("fact");
    expect(form.get("requester")).toBe("홍길동");
    expect(form.get("reference_path")).toBe("기준.xlsx");
    expect(form.getAll("target_paths")).toEqual(["자료/규격서 v2.docx", "발표.pptx"]);
    expect((form.getAll("targets") as File[]).map((f) => f.name)).toEqual(["규격서 v2.docx", "발표.pptx"]);
  });
});
```

- [ ] **Step 2: 실패 확인**

Run: `npm --prefix web test`
Expected: FAIL — 네 파일이 각각 `Failed to resolve import`

- [ ] **Step 3: 구현**

`web/src/lib/progress.ts`:

```ts
import type { ProgressCurrent, Snapshot, UnitState } from "../api/types";

// 진행률 계산은 서버(progress.summarize)가 한다. 여기서는 보여 줄 문장만 만든다.

export function progressSummary(s: Snapshot | null): string {
  if (!s || s.total === 0) return "준비 중";
  return `${s.units_done.toFixed(1)} / ${s.total} 단계`;
}

export function currentText(c: ProgressCurrent | null): string {
  if (!c) return "";
  const parts = [c.label];
  if (c.part_name) parts.push(c.part_name);
  if (c.step_total > 0) parts.push(`${c.step_done}/${c.step_total}`);
  return parts.join(" · ");
}

const MARK: Record<UnitState["state"], string> = {
  done: "✓", running: "▶", failed: "✗", skipped: "–", pending: "·",
};

export function unitChip(u: UnitState): { text: string; state: UnitState["state"]; title: string } {
  return {
    text: `${MARK[u.state] ?? "·"} ${u.label}`,
    state: u.state,
    title: u.error ? `${u.label}: ${u.error}` : u.label,
  };
}

export function formatElapsed(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}
```

`web/src/lib/format.ts`:

```ts
import type { JobState } from "../api/types";

export const STATE_LABEL: Record<JobState, string> = {
  queued: "대기 중",
  running: "실행 중",
  succeeded: "완료",
  failed: "실패",
  cancelled: "취소됨",
  interrupted: "중단됨",
};

export const FINAL_STATES: readonly JobState[] = ["succeeded", "failed", "cancelled", "interrupted"];

export function isFinal(state: string): boolean {
  return (FINAL_STATES as readonly string[]).includes(state);
}

export function statusText(job: { state: JobState; position: number | null }): string {
  if (job.state === "queued") {
    const ahead = job.position ?? 0;
    return ahead > 0 ? `대기 중 · 앞에 ${ahead}건` : "대기 중 · 곧 시작";
  }
  return STATE_LABEL[job.state] ?? job.state;
}

export function shortId(id: string): string {
  return id.split("-").pop() ?? id;
}

const pad = (n: number) => String(n).padStart(2, "0");

export function formatClock(ts: number): string {
  if (!ts) return "-";
  const d = new Date(ts * 1000);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function formatDateTime(ts: number): string {
  if (!ts) return "-";
  const d = new Date(ts * 1000);
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
```

`web/src/lib/jobEvents.ts`:

```ts
import type { JobState, Snapshot } from "../api/types";

// SSE 이벤트를 실행 창 상태로 누적한다. 서버가 보내는 이벤트: log · progress · status · stall · end.
// 모양이 틀린 데이터는 무시한다 — 화면이 한 이벤트 때문에 죽으면 안 된다.

export const MAX_LINES = 2000;

export interface StreamStatus {
  state: JobState | "";
  error: string;
  started_ts: number;
  finished_ts: number;
}

export interface StreamState {
  status: StreamStatus;
  progress: Snapshot | null;
  lines: string[];
  stalled: boolean;
  idleS: number;
  ended: boolean;
  notFound: boolean;
}

export function initialStream(): StreamState {
  return {
    status: { state: "", error: "", started_ts: 0, finished_ts: 0 },
    progress: null,
    lines: [],
    stalled: false,
    idleS: 0,
    ended: false,
    notFound: false,
  };
}

function asRecord(data: unknown): Record<string, unknown> {
  return data && typeof data === "object" && !Array.isArray(data) ? (data as Record<string, unknown>) : {};
}

export function applyEvent(state: StreamState, type: string, data: unknown): StreamState {
  const d = asRecord(data);
  switch (type) {
    case "log": {
      if (!Array.isArray(d.lines) || d.lines.length === 0) return state;
      const lines = state.lines.concat(d.lines.map(String));
      return { ...state, lines: lines.length > MAX_LINES ? lines.slice(lines.length - MAX_LINES) : lines };
    }
    case "progress":
      if (!Array.isArray(d.units)) return state;
      return { ...state, progress: d as unknown as Snapshot };
    case "status":
      return {
        ...state,
        status: {
          state: String(d.state ?? "") as JobState | "",
          error: String(d.error ?? ""),
          started_ts: Number(d.started_ts ?? 0) || 0,
          finished_ts: Number(d.finished_ts ?? 0) || 0,
        },
      };
    case "stall":
      return { ...state, stalled: d.stalled === true, idleS: Number(d.idle_s ?? 0) || 0 };
    case "end":
      return { ...state, ended: true, notFound: d.reason === "not_found" };
    default:
      return state;
  }
}
```

`web/src/lib/uploads.ts`:

```ts
import type { Engine } from "../api/types";

// 서버(contentcompare/web/uploads.py)와 같은 규칙. 서버가 최종 판정하지만, 관계없는 파일까지
// 올리지 않도록 화면에서도 먼저 거른다.
export const SUPPORTED_EXTS = [".xlsx", ".xls", ".xlsm", ".docx", ".doc", ".pptx", ".ppt"];
export const REFERENCE_EXTS = [".xlsx", ".xls", ".xlsm"];

export interface PickedFile {
  file: File;
  path: string;
}

function extOf(name: string): string {
  const i = name.lastIndexOf(".");
  return i >= 0 ? name.slice(i).toLowerCase() : "";
}

function baseOf(path: string): string {
  return path.split("/").pop() ?? path;
}

/** 폴더 업로드면 `자료/하위/a.docx`, 파일 선택이면 이름. */
export function relativePathOf(file: File): string {
  const relative = (file as File & { webkitRelativePath?: string }).webkitRelativePath;
  return relative ? relative : file.name;
}

export function isReferenceName(name: string): boolean {
  return REFERENCE_EXTS.includes(extOf(name)) && !baseOf(name).startsWith("~$");
}

export function addTargets(existing: PickedFile[], files: File[]): { items: PickedFile[]; skipped: string[] } {
  const items = [...existing];
  const seen = new Set(existing.map((p) => p.path));
  const skipped: string[] = [];
  for (const file of files) {
    const path = relativePathOf(file);
    if (baseOf(path).startsWith("~$") || !SUPPORTED_EXTS.includes(extOf(path))) {
      skipped.push(path);
      continue;
    }
    if (seen.has(path)) continue;
    seen.add(path);
    items.push({ file, path });
  }
  return { items, skipped };
}

/**
 * 폼을 만든다. 상대 경로는 `target_paths` 로 따로 보내고 파일 이름은 마지막 조각만 쓴다 —
 * 브라우저마다 multipart filename 에 한글·폴더 경로를 싣는 방식이 달라서다(계획 2 Review Focus #1).
 */
export function buildJobForm(engine: Engine, requester: string, reference: File, targets: PickedFile[]): FormData {
  const form = new FormData();
  form.append("engine", engine);
  form.append("requester", requester.trim());
  form.append("reference", reference, reference.name);
  form.append("reference_path", reference.name);
  for (const t of targets) {
    form.append("targets", t.file, baseOf(t.path));
    form.append("target_paths", t.path);
  }
  return form;
}
```

- [ ] **Step 4: 통과 확인**

Run: `npm --prefix web run typecheck && npm --prefix web test`
Expected: 타입 오류 0, 모든 테스트 PASS(Task 1 의 8건 포함)

- [ ] **Step 5: 커밋**

```bash
git add web/src/lib
git commit -m "feat(web-ui): 계산 로직 — 진행률 문구, 상태 문구, SSE 누적(2000줄 상한), 업로드 목록"
```

---

### Task 3: 실행 창 — SSE 구독, 진행률·로그·취소, 결과, `/jobs/:id` 새 창

**Files:**
- Create: `web/src/hooks/useJobStream.ts`
- Create: `web/src/components/JobView.tsx`, `JobWindow.tsx`, `LogPanel.tsx`, `ResultView.tsx`, `DataTable.tsx`, `Markdown.tsx`
- Create: `web/src/pages/JobPage.tsx`
- Modify: `web/src/App.tsx` (라우트 `/jobs/:id`)

**Interfaces:**
- Consumes: Task 1 `endpoints`(`getJob`, `cancelJob`, `getResult`, `getJobReport`, `jobReportUrl`, `jobEventsUrl`), `errorText`, 타입; Task 2 `applyEvent`, `initialStream`, `StreamState`, `isFinal`, `statusText`, `shortId`, `progressSummary`, `currentText`, `unitChip`, `formatElapsed`.
- Produces:
  - `useJobStream(jobId: string): StreamState`
  - `<JobView jobId />` — 실행 창 본문(모달과 새 창이 함께 쓴다)
  - `<JobWindow jobId onClose />` — 모달(새 창으로·최소화·닫기)
  - `<DataTable rows />`, `<Markdown text />`, `<ResultView jobId result hasReport />`, `<LogPanel lines />`

- [ ] **Step 1: 구현** (이 태스크는 화면 조립이다 — 계산은 Task 2 의 테스트된 함수를 부른다)

`web/src/hooks/useJobStream.ts`:

```ts
import { useEffect, useReducer } from "react";
import { jobEventsUrl } from "../api/endpoints";
import { applyEvent, initialStream, type StreamState } from "../lib/jobEvents";

type Action = { type: string; data: unknown };
const EVENT_TYPES = ["log", "progress", "status", "stall", "end"] as const;

function reducer(state: StreamState, action: Action): StreamState {
  return action.type === "reset" ? initialStream() : applyEvent(state, action.type, action.data);
}

/**
 * 작업 이벤트 구독. EventSource 는 끊기면 스스로 다시 붙고 마지막 이벤트 id(Last-Event-ID)를
 * 실어 보내므로 서버가 본 줄을 또 보내지 않는다. 404 처럼 서버가 연결을 거절하면 브라우저가
 * 연결을 닫는다(CLOSED) — 그때는 끝난 것으로 본다.
 */
export function useJobStream(jobId: string): StreamState {
  const [state, dispatch] = useReducer(reducer, undefined, initialStream);
  useEffect(() => {
    dispatch({ type: "reset", data: null });
    if (!jobId) return;
    const source = new EventSource(jobEventsUrl(jobId));
    for (const type of EVENT_TYPES) {
      source.addEventListener(type, (event) => {
        let data: unknown;
        try {
          data = JSON.parse((event as MessageEvent<string>).data);
        } catch {
          return;
        }
        dispatch({ type, data });
        if (type === "end") source.close();
      });
    }
    source.onerror = () => {
      if (source.readyState === EventSource.CLOSED) dispatch({ type: "end", data: {} });
    };
    return () => source.close();
  }, [jobId]);
  return state;
}
```

`web/src/components/DataTable.tsx`:

```tsx
import type { Row } from "../api/types";

// 서버가 만든 표(ui.runner 의 행)를 그대로 그린다. 열 이름도 서버가 정한다.
export default function DataTable({ rows }: { rows: Row[] }) {
  if (rows.length === 0) return <p className="muted">표시할 행이 없습니다.</p>;
  const columns: string[] = [];
  for (const row of rows) {
    for (const key of Object.keys(row)) if (!columns.includes(key)) columns.push(key);
  }
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>{columns.map((c) => <th key={c}>{c}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              {columns.map((c) => <td key={c}>{row[c] == null ? "" : String(row[c])}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

`web/src/components/Markdown.tsx`:

```tsx
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// 리포트 본문에는 문서 원문이 들어가므로 HTML 은 해석하지 않는다(skipHtml).
export default function Markdown({ text }: { text: string }) {
  return (
    <div className="markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml>{text}</ReactMarkdown>
    </div>
  );
}
```

`web/src/components/LogPanel.tsx`:

```tsx
import { useEffect, useRef, useState } from "react";

const WARN = /(ERROR|WARNING|실패|주의|⚠)/;

export default function LogPanel({ lines }: { lines: string[] }) {
  const [follow, setFollow] = useState(true);
  const box = useRef<HTMLPreElement>(null);
  useEffect(() => {
    if (follow && box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [lines, follow]);
  return (
    <div className="log-panel">
      <div className="log-head">
        <span>로그</span>
        <label>
          <input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> 자동 스크롤
        </label>
      </div>
      <pre ref={box} className="log-body">
        {lines.length === 0 && <span className="muted">아직 로그가 없습니다.</span>}
        {lines.map((line, i) => (
          <div key={i} className={WARN.test(line) ? "log-line warn" : "log-line"}>{line}</div>
        ))}
      </pre>
    </div>
  );
}
```

`web/src/components/ResultView.tsx`:

```tsx
import { useEffect, useState } from "react";
import { getJobReport, jobReportUrl } from "../api/endpoints";
import type { JobResult } from "../api/types";
import DataTable from "./DataTable";
import Markdown from "./Markdown";

// Streamlit 의 show_results / show_fact_results 와 같은 내용. 라벨은 서버가 준 것을 그대로 쓴다.
export default function ResultView({ jobId, result, hasReport }: { jobId: string; result: JobResult; hasReport: boolean }) {
  const [markdown, setMarkdown] = useState("");
  useEffect(() => {
    if (!hasReport) return;
    getJobReport(jobId).then(setMarkdown).catch(() => setMarkdown(""));
  }, [jobId, hasReport]);

  return (
    <section className="result">
      <h3>결과</h3>
      <div className="counts">
        {result.counts.map((c) => (
          <div key={c.key} className="count"><span>{c.label}</span><strong>{c.n}</strong></div>
        ))}
      </div>

      {result.engine === "fact" && result.failed_docs.length > 0 && (
        <div className="error">
          <p>문서 {result.failed_docs.length}건 처리 실패 — 나머지는 계속 처리했습니다.</p>
          <ul>{result.failed_docs.map((d) => <li key={d.name}>{d.name}: {d.error}</li>)}</ul>
        </div>
      )}

      <h4>요약</h4>
      <DataTable rows={result.summary} />

      {result.engine === "rag" && (
        <>
          <h4>상세</h4>
          {result.details.map((d) => (
            <details key={d.index}>
              <summary>{d.index}. {d.source_label} — {d.label}</summary>
              <p><strong>기준 내용</strong>: {d.reference_text}</p>
              {d.is_record ? (
                <>
                  <p><strong>출처(어디에)</strong>: {d.sources.length ? d.sources.join("; ") : "-"}</p>
                  <p><strong>종합 근거(왜)</strong>: {d.reasoning}</p>
                  {d.fields.length > 0 && <DataTable rows={d.fields} />}
                </>
              ) : (
                <p><strong>판단 근거</strong>: {d.reasoning}</p>
              )}
              {d.candidates.length > 0 && (
                <>
                  <p><strong>검색된 후보</strong></p>
                  <ul>
                    {d.candidates.map((c, i) => (
                      <li key={i}>({c.score.toFixed(3)}) {c.source_label}{c.matched ? " ⟵ 매칭" : ""}</li>
                    ))}
                  </ul>
                </>
              )}
            </details>
          ))}
        </>
      )}

      {result.engine === "fact" && <p className="muted">판정이 이상하면 🔬 파이프라인 현미경 탭에서 원인을 추적하세요.</p>}

      {hasReport && (
        <>
          <div className="row">
            <a className="button secondary" href={jobReportUrl(jobId)}>📥 리포트(.md) 다운로드</a>
          </div>
          <details>
            <summary>📄 리포트(Markdown) 미리보기</summary>
            <Markdown text={markdown} />
          </details>
        </>
      )}
    </section>
  );
}
```

`web/src/components/JobView.tsx`:

```tsx
import { useEffect, useState } from "react";
import { errorText } from "../api/client";
import { cancelJob, getJob, getResult } from "../api/endpoints";
import type { JobDetail, JobResult, JobState } from "../api/types";
import { useJobStream } from "../hooks/useJobStream";
import { isFinal, shortId, statusText } from "../lib/format";
import { currentText, formatElapsed, progressSummary, unitChip } from "../lib/progress";
import LogPanel from "./LogPanel";
import ResultView from "./ResultView";

export default function JobView({ jobId }: { jobId: string }) {
  const stream = useJobStream(jobId);
  const [job, setJob] = useState<JobDetail | null>(null);
  const [result, setResult] = useState<{ result: JobResult; report: boolean } | null>(null);
  const [error, setError] = useState("");
  const [now, setNow] = useState(() => Date.now() / 1000);

  const state = (stream.status.state || job?.state || "") as JobState | "";
  const final = isFinal(state);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => window.clearInterval(timer);
  }, []);

  // 요청자·순번·'내 작업' 여부는 SSE 에 없으므로 따로 가져오고, 끝나기 전에는 순번 때문에 몇 초마다 다시 본다.
  useEffect(() => {
    let alive = true;
    const load = () => getJob(jobId)
      .then((j) => { if (alive) { setJob(j); setError(""); } })
      .catch((e) => { if (alive) setError(errorText(e)); });
    load();
    const timer = window.setInterval(() => { if (!final) load(); }, 3000);
    return () => { alive = false; window.clearInterval(timer); };
  }, [jobId, final]);

  useEffect(() => {
    if (!final) return;
    getResult(jobId).then(setResult).catch(() => setResult(null));
  }, [jobId, final]);

  async function cancel() {
    try {
      await cancelJob(jobId);
    } catch (e) {
      setError(errorText(e));
    }
  }

  if (stream.notFound) return <p className="error">작업을 찾을 수 없습니다(보관 기간이 지나 삭제되었을 수 있습니다).</p>;

  const progress = stream.progress ?? job?.progress ?? null;
  const percent = progress?.percent ?? 0;
  const started = stream.status.started_ts || job?.started_ts || 0;
  const finished = stream.status.finished_ts || job?.finished_ts || 0;
  const elapsed = started ? (finished || now) - started : 0;
  const failure = stream.status.error || job?.error || "";

  return (
    <div className="job-view">
      <h2>
        작업 #{shortId(jobId)}{job ? ` · ${job.requester || "이름 없음"} · ${job.engine}` : ""}
      </h2>
      <div className="job-status">
        <span className={`badge state-${state}`}>
          {job && state ? statusText({ state, position: job.position }) : "불러오는 중"}
        </span>
        {stream.stalled && !final && (
          <span className="warn">⚠ {Math.floor(stream.idleS / 60)}분째 진행 기록이 없습니다(LLM 응답을 기다리는 중일 수 있습니다).</span>
        )}
      </div>

      <div className="progress"><div className="progress-bar" style={{ width: `${percent}%` }} /></div>
      <div className="progress-meta">
        <strong>{percent}%</strong>
        <span>{progressSummary(progress)}</span>
        <span>경과 {formatElapsed(elapsed)}</span>
      </div>
      {progress?.current && <p className="current">현재: {currentText(progress.current)}</p>}
      {progress && progress.units.length > 0 && (
        <div className="chips">
          {progress.units.map((u) => {
            const chip = unitChip(u);
            return <span key={u.key} className={`chip chip-${chip.state}`} title={chip.title}>{chip.text}</span>;
          })}
        </div>
      )}

      {final && failure && <p className="error">{failure}</p>}
      {error && <p className="error">{error}</p>}

      <LogPanel lines={stream.lines} />

      <div className="row">
        {job?.mine && !final && <button className="button danger" onClick={cancel}>취소</button>}
      </div>

      {result && <ResultView jobId={jobId} result={result.result} hasReport={result.report} />}
    </div>
  );
}
```

`web/src/components/JobWindow.tsx`:

```tsx
import { useState } from "react";
import { shortId } from "../lib/format";
import JobView from "./JobView";

// 🚀 실행을 누르면 뜨는 창(설계 §8.4). 닫아도 작업은 계속된다.
export default function JobWindow({ jobId, onClose }: { jobId: string; onClose: () => void }) {
  const [minimized, setMinimized] = useState(false);
  if (minimized) {
    return (
      <div className="job-mini">
        <span>작업 #{shortId(jobId)}</span>
        <button className="button link" onClick={() => setMinimized(false)}>열기</button>
        <button className="button link" onClick={onClose}>닫기</button>
      </div>
    );
  }
  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true">
      <div className="modal">
        <div className="modal-toolbar">
          <button className="button secondary" onClick={() => window.open(`/jobs/${jobId}`, "_blank", "noopener")}>새 창으로</button>
          <button className="button secondary" onClick={() => setMinimized(true)}>최소화</button>
          <button className="button secondary" onClick={onClose}>닫기</button>
        </div>
        <p className="muted">창을 닫아도 작업은 계속됩니다. '내 최근 작업'에서 다시 열 수 있습니다.</p>
        <JobView jobId={jobId} />
      </div>
    </div>
  );
}
```

`web/src/pages/JobPage.tsx`:

```tsx
import { Link, useParams } from "react-router-dom";
import JobView from "../components/JobView";

// 실행 창을 브라우저 새 창으로 분리한 화면. 링크를 받은 다른 사람도 볼 수 있다(취소는 요청자만).
export default function JobPage() {
  const { id = "" } = useParams();
  return (
    <div className="page">
      <Link to="/">← 처음으로</Link>
      <JobView jobId={id} />
    </div>
  );
}
```

`web/src/App.tsx` — import 에 `import JobPage from "./pages/JobPage";` 추가, `<Route path="/" .../>` **아래**에:

```tsx
        <Route path="/jobs/:id" element={<JobPage />} />
```

- [ ] **Step 2: 확인**

Run: `npm --prefix web run typecheck && npm --prefix web test && npm --prefix web run build`
Expected: 타입 오류 0, 테스트 전부 PASS, 빌드 성공

- [ ] **Step 3: 커밋**

```bash
git add web/src/hooks web/src/components/JobView.tsx web/src/components/JobWindow.tsx web/src/components/LogPanel.tsx web/src/components/ResultView.tsx web/src/components/DataTable.tsx web/src/components/Markdown.tsx web/src/pages/JobPage.tsx web/src/App.tsx
git commit -m "feat(web-ui): 실행 창 — SSE 진행률·로그·취소, 결과·리포트, 새 창 /jobs/:id"
```

---

### Task 4: 🚀 비교 실행 탭 — 업로드·제출·내 작업·대기열

**Files:**
- Create: `web/src/tabs/RunTab.tsx`, `web/src/components/JobList.tsx`
- Modify: `web/src/tabs/index.ts` (탭 등록)

**Interfaces:**
- Consumes: Task 1 `listJobs`, `submitJob`, `errorText`, `loadRequester`; Task 2 `addTargets`, `buildJobForm`, `isReferenceName`, `SUPPORTED_EXTS`, `PickedFile`, `statusText`, `shortId`, `formatClock`; Task 3 `JobWindow`.
- Produces: 탭 `{ key: "run", title: "🚀 비교 실행", Component: RunTab }`.

- [ ] **Step 1: 구현**

`web/src/components/JobList.tsx`:

```tsx
import type { Job } from "../api/types";
import { formatClock, shortId, statusText } from "../lib/format";

export default function JobList({ title, jobs, empty, onOpen }: {
  title: string;
  jobs: Job[];
  empty: string;
  onOpen: (id: string) => void;
}) {
  return (
    <section className="job-list">
      <h3>{title}</h3>
      {jobs.length === 0 ? (
        <p className="muted">{empty}</p>
      ) : (
        <table className="table">
          <thead>
            <tr><th>작업</th><th>요청자</th><th>엔진</th><th>상태</th><th>접수</th><th></th></tr>
          </thead>
          <tbody>
            {jobs.map((j) => (
              <tr key={j.id}>
                <td>#{shortId(j.id)}</td>
                <td>{j.requester || "이름 없음"}</td>
                <td>{j.engine}</td>
                <td>{statusText(j)}</td>
                <td>{formatClock(j.created_ts)}</td>
                <td><button className="button link" onClick={() => onOpen(j.id)}>열기</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
```

`web/src/tabs/RunTab.tsx`:

```tsx
import { useEffect, useRef, useState } from "react";
import { errorText } from "../api/client";
import { listJobs, submitJob } from "../api/endpoints";
import type { Engine, Job } from "../api/types";
import JobList from "../components/JobList";
import JobWindow from "../components/JobWindow";
import { loadRequester } from "../lib/requester";
import { SUPPORTED_EXTS, addTargets, buildJobForm, isReferenceName, type PickedFile } from "../lib/uploads";

export default function RunTab() {
  const [engine, setEngine] = useState<Engine>("rag");
  const [reference, setReference] = useState<File | null>(null);
  const [targets, setTargets] = useState<PickedFile[]>([]);
  const [skipped, setSkipped] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [jobs, setJobs] = useState<Job[]>([]);
  const [openJob, setOpenJob] = useState<string | null>(null);
  const folderInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    // 폴더 선택은 표준 속성이 아니라 JSX 타입에 없다 — DOM 에 직접 붙인다.
    folderInput.current?.setAttribute("webkitdirectory", "");
  }, []);

  useEffect(() => {
    let alive = true;
    const load = () => listJobs().then((r) => { if (alive) setJobs(r.jobs); }).catch(() => undefined);
    load();
    const timer = window.setInterval(load, 3000);
    return () => { alive = false; window.clearInterval(timer); };
  }, []);

  function pickReference(files: FileList | null) {
    const file = files?.[0] ?? null;
    if (file && !isReferenceName(file.name)) {
      setError(`기준 문서는 Excel 이어야 합니다: ${file.name}`);
      return;
    }
    setError("");
    setReference(file);
  }

  function pickTargets(files: FileList | null) {
    if (!files) return;
    const r = addTargets(targets, Array.from(files));
    setTargets(r.items);
    setSkipped(r.skipped);
  }

  async function submit() {
    if (!reference) { setError("기준 엑셀을 선택하세요."); return; }
    if (targets.length === 0) { setError("대상 문서를 하나 이상 선택하세요."); return; }
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const r = await submitJob(buildJobForm(engine, loadRequester(), reference, targets));
      setOpenJob(r.job.id);
      if (r.skipped.length > 0) setNotice(`서버가 건너뛴 파일 ${r.skipped.length}개: ${r.skipped.join(", ")}`);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  const mine = jobs.filter((j) => j.mine);
  const active = jobs.filter((j) => j.state === "queued" || j.state === "running");

  return (
    <div className="run-tab">
      <div className="field">
        <span className="label">엔진</span>
        <label><input type="radio" checked={engine === "rag"} onChange={() => setEngine("rag")} /> rag</label>
        <label><input type="radio" checked={engine === "fact"} onChange={() => setEngine("fact")} /> fact</label>
      </div>
      <p className="muted">rag=하이브리드 검색 후 LLM 종합 판정 / fact=문서를 fact 로 정규화한 뒤 개념 그래프로 짝을 찾아 코드가 값 대조</p>

      <h3>1) 기준 엑셀</h3>
      <input type="file" accept=".xlsx,.xls,.xlsm" onChange={(e) => pickReference(e.target.files)} />

      <h3>2) 대상 문서들</h3>
      <div className="row">
        <label className="button secondary">
          📁 파일 선택(여러 개)
          <input hidden type="file" multiple accept={SUPPORTED_EXTS.join(",")}
            onChange={(e) => { pickTargets(e.target.files); e.target.value = ""; }} />
        </label>
        <label className="button secondary">
          📂 폴더 선택
          <input hidden ref={folderInput} type="file" multiple
            onChange={(e) => { pickTargets(e.target.files); e.target.value = ""; }} />
        </label>
        <button className="button secondary" onClick={() => { setTargets([]); setSkipped([]); }}>🗑️ 목록 비우기</button>
      </div>
      {targets.length > 0 && (
        <>
          <p className="muted">대상 {targets.length}개</p>
          <pre className="file-list">{targets.map((t) => t.path).join("\n")}</pre>
        </>
      )}
      {skipped.length > 0 && <p className="muted">지원하지 않거나 Office 잠금 파일이라 건너뜀: {skipped.join(", ")}</p>}

      <div className="row">
        <button className="button primary" disabled={busy} onClick={submit}>{busy ? "업로드 중..." : "🚀 비교 실행"}</button>
      </div>
      {error && <p className="error">{error}</p>}
      {notice && <p className="muted">{notice}</p>}

      <JobList title="내 최근 작업" jobs={mine} empty="아직 이 브라우저에서 실행한 작업이 없습니다." onOpen={setOpenJob} />
      <JobList title="대기열" jobs={active} empty="대기 중이거나 실행 중인 작업이 없습니다." onOpen={setOpenJob} />

      {openJob && <JobWindow jobId={openJob} onClose={() => setOpenJob(null)} />}
    </div>
  );
}
```

`web/src/tabs/index.ts` — `import RunTab from "./RunTab";` 추가, `TABS` 를:

```ts
export const TABS: TabDef[] = [
  { key: "run", title: "🚀 비교 실행", Component: RunTab },
];
```

- [ ] **Step 2: 확인**

Run: `npm --prefix web run typecheck && npm --prefix web test && npm --prefix web run build`
Expected: 모두 통과

- [ ] **Step 3: 커밋**

```bash
git add web/src/tabs/RunTab.tsx web/src/tabs/index.ts web/src/components/JobList.tsx
git commit -m "feat(web-ui): 비교 실행 탭 — 파일·폴더 업로드, 제출, 내 작업·대기열"
```

---

### Task 5: 📄 리포트·🔬 현미경·⏱ 타임라인 탭

**Files:**
- Create: `web/src/components/HtmlFrame.tsx`
- Create: `web/src/tabs/ReportTab.tsx`, `web/src/tabs/MicroTab.tsx`, `web/src/tabs/TimelineTab.tsx`
- Modify: `web/src/tabs/index.ts`

**Interfaces:**
- Consumes: Task 1 `listReports`, `getReport`, `listMicroRuns`, `getMicroOptions`, `getMicroHtml`, `listTimelines`, `getTimelineHtml`, `errorText`, 타입; Task 3 `Markdown`.
- Produces: `<HtmlFrame title html height />`; 탭 `report`·`micro`·`timeline`.

- [ ] **Step 1: 구현**

`web/src/components/HtmlFrame.tsx`:

```tsx
// 서버가 만든 자족 HTML(현미경·타임라인)을 격리해서 띄운다. allow-same-origin 을 주지 않으므로
// 그 HTML 의 스크립트는 이 사이트의 쿠키·API 에 접근하지 못한다 — 산출물에는 문서 원문이 들어 있다.
export default function HtmlFrame({ title, html, height }: { title: string; html: string; height: number }) {
  return <iframe className="html-frame" title={title} srcDoc={html} sandbox="allow-scripts" style={{ height }} />;
}
```

`web/src/tabs/ReportTab.tsx`:

```tsx
import { useCallback, useEffect, useState } from "react";
import { errorText } from "../api/client";
import { getReport, listReports } from "../api/endpoints";
import type { ReportItem } from "../api/types";
import Markdown from "../components/Markdown";

export default function ReportTab() {
  const [items, setItems] = useState<ReportItem[]>([]);
  const [picked, setPicked] = useState("");
  const [markdown, setMarkdown] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(() => {
    listReports()
      .then((r) => {
        setItems(r.reports);
        setPicked((prev) => (prev && r.reports.some((i) => i.id === prev) ? prev : r.reports[0]?.id ?? ""));
        setError("");
      })
      .catch((e) => setError(errorText(e)));
  }, []);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (!picked) { setMarkdown(""); return; }
    getReport(picked).then((r) => setMarkdown(r.markdown)).catch((e) => setError(errorText(e)));
  }, [picked]);

  function download() {
    const name = picked.split("/").pop() || "report";
    const url = URL.createObjectURL(new Blob([markdown], { type: "text/markdown;charset=utf-8" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = name.endsWith(".md") ? name : `report_${name}.md`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div>
      <div className="row">
        <select value={picked} onChange={(e) => setPicked(e.target.value)}>
          {items.map((i) => <option key={i.id} value={i.id}>{i.name}</option>)}
        </select>
        <button className="button secondary" onClick={load}>새로고침</button>
        <button className="button secondary" disabled={!markdown} onClick={download}>📥 이 리포트 다운로드</button>
      </div>
      {items.length === 0 && <p className="muted">아직 저장된 리포트가 없습니다. 비교를 실행하면 여기에 나타납니다.</p>}
      {error && <p className="error">{error}</p>}
      <Markdown text={markdown} />
    </div>
  );
}
```

`web/src/tabs/MicroTab.tsx`:

```tsx
import { useCallback, useEffect, useState } from "react";
import { errorText } from "../api/client";
import { getMicroHtml, getMicroOptions, listMicroRuns } from "../api/endpoints";
import type { MicroHtml, MicroOptions, MicroRun } from "../api/types";
import HtmlFrame from "../components/HtmlFrame";

export default function MicroTab() {
  const [runs, setRuns] = useState<MicroRun[]>([]);
  const [run, setRun] = useState("");
  const [options, setOptions] = useState<MicroOptions | null>(null);
  const [mode, setMode] = useState<"debug" | "learn">("debug");
  const [target, setTarget] = useState("");
  const [results, setResults] = useState<string[]>([]);
  const [doc, setDoc] = useState("");
  const [fact, setFact] = useState("");
  const [view, setView] = useState<MicroHtml | null>(null);
  const [height, setHeight] = useState(900);
  const [error, setError] = useState("");

  const loadRuns = useCallback(() => {
    listMicroRuns()
      .then((r) => {
        setRuns(r.runs);
        setRun((prev) => (prev && r.runs.some((x) => x.id === prev) ? prev : r.runs[0]?.id ?? ""));
      })
      .catch((e) => setError(errorText(e)));
  }, []);

  useEffect(() => { loadRuns(); }, [loadRuns]);

  useEffect(() => {
    if (!run) { setOptions(null); setView(null); return; }
    getMicroOptions(run)
      .then((o) => {
        setOptions(o);
        setTarget("");
        setResults(o.default_results);
        const firstDoc = o.learn_docs[0] ?? o.reference_doc;
        setDoc(firstDoc);
        setFact(o.facts[firstDoc]?.[0]?.id ?? "");
        setError("");
      })
      .catch((e) => setError(errorText(e)));
  }, [run]);

  useEffect(() => {
    if (!run || !options) return;
    getMicroHtml({ run, mode, target, results: results.join(","), doc, fact, theme: "light" })
      .then((v) => {
        setView(v);
        if (v.height) setHeight(Math.min(3000, Math.max(400, v.height)));
      })
      .catch((e) => setError(errorText(e)));
  }, [run, options, mode, target, results, doc, fact]);

  function toggleResult(key: string) {
    setResults((prev) => (prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]));
  }

  function pickDoc(name: string) {
    setDoc(name);
    setFact(options?.facts[name]?.[0]?.id ?? "");
  }

  if (runs.length === 0) {
    return (
      <div>
        <p className="muted">아직 fact 엔진 실행이 없습니다. fact 엔진으로 비교를 한 번 실행하면 이 탭이 채워집니다.</p>
        <button className="button secondary" onClick={loadRuns}>새로고침</button>
        {error && <p className="error">{error}</p>}
      </div>
    );
  }

  return (
    <div>
      <div className="row">
        <select value={run} onChange={(e) => setRun(e.target.value)}>
          {runs.map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}
        </select>
        <button className="button secondary" onClick={loadRuns}>새로고침</button>
        <label><input type="radio" checked={mode === "debug"} onChange={() => setMode("debug")} /> 🔎 디버깅</label>
        <label><input type="radio" checked={mode === "learn"} onChange={() => setMode("learn")} /> 📚 학습</label>
      </div>
      <p className="muted">디버깅=이 판정이 왜 이렇게 나왔나 / 학습=파이프라인이 어떻게 도는가</p>

      {options && mode === "debug" && (
        <div className="row">
          <select value={target} onChange={(e) => setTarget(e.target.value)}>
            <option value="">(전체)</option>
            {options.target_docs.map((d) => <option key={d} value={d}>{d}</option>)}
          </select>
          {options.result_choices.map((c) => (
            <label key={c.key}>
              <input type="checkbox" checked={results.includes(c.key)} onChange={() => toggleResult(c.key)} /> {c.label}
            </label>
          ))}
        </div>
      )}

      {options && mode === "learn" && (
        <div className="row">
          <select value={doc} onChange={(e) => pickDoc(e.target.value)}>
            {(options.learn_docs.length ? options.learn_docs : [options.reference_doc]).map((d) => (
              <option key={d} value={d}>{d}</option>
            ))}
          </select>
          <select value={fact} onChange={(e) => setFact(e.target.value)}>
            {(options.facts[doc] ?? []).map((f) => <option key={f.id} value={f.id}>{f.name} ({f.id})</option>)}
          </select>
        </div>
      )}

      {error && <p className="error">{error}</p>}
      {view?.unavailable ? (
        <div className="warn">
          <p>이 실행에는 단계별 산출물이 없어 학습 모드를 쓸 수 없습니다(스냅샷은 비교 결과 3종만 보관합니다).</p>
          <ul>{view.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
        </div>
      ) : (
        view && (
          <>
            <label className="row">
              화면 높이
              <input type="range" min={400} max={3000} step={100} value={height}
                onChange={(e) => setHeight(Number(e.target.value))} />
              {height}px
            </label>
            <HtmlFrame title="파이프라인 현미경" html={view.html} height={height} />
          </>
        )
      )}
    </div>
  );
}
```

`web/src/tabs/TimelineTab.tsx`:

```tsx
import { useCallback, useEffect, useState } from "react";
import { errorText } from "../api/client";
import { getTimelineHtml, listTimelines } from "../api/endpoints";
import type { TimelineItem } from "../api/types";
import HtmlFrame from "../components/HtmlFrame";

export default function TimelineTab() {
  const [items, setItems] = useState<TimelineItem[]>([]);
  const [picked, setPicked] = useState("");
  const [errorsOnly, setErrorsOnly] = useState(false);
  const [html, setHtml] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(() => {
    listTimelines()
      .then((r) => {
        setItems(r.timelines);
        setPicked((prev) => (prev && r.timelines.some((i) => i.id === prev) ? prev : r.timelines[0]?.id ?? ""));
      })
      .catch((e) => setError(errorText(e)));
  }, []);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (!picked) { setHtml(""); return; }
    getTimelineHtml(picked, errorsOnly).then((r) => setHtml(r.html)).catch((e) => setError(errorText(e)));
  }, [picked, errorsOnly]);

  return (
    <div>
      <p className="muted">
        단계 시작·종료, LLM 호출, 재시도, 한도 대기를 한 시간축에 놓는다. 실패했을 때 '어느 문서 · 어느 단계 · 몇 번째 배치 · 왜'를 여기서 읽는다.
      </p>
      <div className="row">
        <select value={picked} onChange={(e) => setPicked(e.target.value)}>
          {items.map((i) => <option key={i.id} value={i.id}>{i.label}</option>)}
        </select>
        <label><input type="checkbox" checked={errorsOnly} onChange={(e) => setErrorsOnly(e.target.checked)} /> 실패·재시도만</label>
        <button className="button secondary" onClick={load}>새로고침</button>
      </div>
      {items.length === 0 && <p className="muted">타임라인이 없습니다. 비교를 한 번 실행하면 여기에 나타납니다.</p>}
      {error && <p className="error">{error}</p>}
      {html && <HtmlFrame title="타임라인" html={html} height={720} />}
    </div>
  );
}
```

`web/src/tabs/index.ts` — import 3개 추가, `TABS` 를:

```ts
export const TABS: TabDef[] = [
  { key: "run", title: "🚀 비교 실행", Component: RunTab },
  { key: "report", title: "📄 리포트 보기", Component: ReportTab },
  { key: "micro", title: "🔬 파이프라인 현미경", Component: MicroTab },
  { key: "timeline", title: "⏱ 타임라인", Component: TimelineTab },
];
```

- [ ] **Step 2: 확인**

Run: `npm --prefix web run typecheck && npm --prefix web test && npm --prefix web run build`
Expected: 모두 통과

- [ ] **Step 3: 커밋**

```bash
git add web/src/components/HtmlFrame.tsx web/src/tabs/ReportTab.tsx web/src/tabs/MicroTab.tsx web/src/tabs/TimelineTab.tsx web/src/tabs/index.ts
git commit -m "feat(web-ui): 리포트·현미경·타임라인 탭 — 서버 HTML 은 격리된 iframe 으로만"
```

---

### Task 6: 📚 도메인 지식 탭 — 편집·저장 충돌 처리

**Files:**
- Create: `web/src/lib/knowledge.ts`, `web/src/lib/knowledge.test.ts`
- Create: `web/src/tabs/KnowledgeTab.tsx`
- Modify: `web/src/tabs/index.ts`

**Interfaces:**
- Consumes: Task 1 `listKnowledge`, `getKnowledge`, `saveKnowledge`, `getMergedKnowledge`, `ApiError`, `errorText`, `KnowledgeConflict`, `KnowledgeList`.
- Produces: `knowledgeConflict(body: unknown): KnowledgeConflict | null`; 탭 `knowledge`.

- [ ] **Step 1: 실패하는 테스트** — `web/src/lib/knowledge.test.ts`

```ts
import { describe, expect, it } from "vitest";
import { knowledgeConflict } from "./knowledge";

describe("knowledgeConflict", () => {
  it("409 본문에서 서버 내용과 mtime 을 꺼낸다", () => {
    const body = { detail: { message: "다른 사람이 먼저 저장했습니다.", current: { content: "서버 내용", mtime: 12.5 } } };
    expect(knowledgeConflict(body)).toEqual({
      message: "다른 사람이 먼저 저장했습니다.",
      current: { content: "서버 내용", mtime: 12.5 },
    });
  });
  it("충돌 모양이 아니면 null", () => {
    expect(knowledgeConflict({ detail: "파일 이름만 쓸 수 있습니다." })).toBeNull();
    expect(knowledgeConflict({ detail: { message: "x", current: { content: "c" } } })).toBeNull();
    expect(knowledgeConflict(null)).toBeNull();
  });
});
```

- [ ] **Step 2: 실패 확인**

Run: `npm --prefix web test`
Expected: FAIL — `Failed to resolve import "./knowledge"`

- [ ] **Step 3: 구현**

`web/src/lib/knowledge.ts`:

```ts
import type { KnowledgeConflict } from "../api/types";

/** PUT /api/knowledge/files/{name} 의 409 본문 → 충돌 정보. 모양이 다르면 null. */
export function knowledgeConflict(body: unknown): KnowledgeConflict | null {
  if (!body || typeof body !== "object") return null;
  const detail = (body as { detail?: unknown }).detail;
  if (!detail || typeof detail !== "object") return null;
  const { message, current } = detail as { message?: unknown; current?: unknown };
  if (typeof message !== "string" || !current || typeof current !== "object") return null;
  const { content, mtime } = current as { content?: unknown; mtime?: unknown };
  if (typeof content !== "string" || typeof mtime !== "number") return null;
  return { message, current: { content, mtime } };
}
```

`web/src/tabs/KnowledgeTab.tsx`:

```tsx
import { useCallback, useEffect, useState } from "react";
import { ApiError, errorText } from "../api/client";
import { getKnowledge, getMergedKnowledge, listKnowledge, saveKnowledge } from "../api/endpoints";
import type { KnowledgeConflict, KnowledgeList } from "../api/types";
import { knowledgeConflict } from "../lib/knowledge";

const NEW = "__new__";

export default function KnowledgeTab() {
  const [list, setList] = useState<KnowledgeList | null>(null);
  const [pick, setPick] = useState(NEW);
  const [name, setName] = useState("domain.md");
  const [content, setContent] = useState("");
  const [baseMtime, setBaseMtime] = useState<number | null>(null);
  const [conflict, setConflict] = useState<KnowledgeConflict | null>(null);
  const [merged, setMerged] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  const loadList = useCallback(async () => {
    const l = await listKnowledge();
    setList(l);
    return l;
  }, []);

  useEffect(() => {
    loadList().then((l) => setContent(l.template)).catch((e) => setError(errorText(e)));
  }, [loadList]);

  useEffect(() => {
    setConflict(null);
    setNotice("");
    if (pick === NEW) {
      setName("domain.md");
      setContent(list?.template ?? "");
      setBaseMtime(null);
      return;
    }
    getKnowledge(pick)
      .then((f) => { setName(f.name); setContent(f.content); setBaseMtime(f.mtime); setError(""); })
      .catch((e) => setError(errorText(e)));
    // list 가 바뀔 때(저장 후 목록 갱신)마다 편집 중인 내용을 덮지 않도록 일부러 pick 에만 반응한다.
  }, [pick]);

  async function save(mtime: number | null) {
    setError("");
    setNotice("");
    try {
      const r = await saveKnowledge(name, content, mtime);
      setBaseMtime(r.mtime);
      setConflict(null);
      setNotice(`저장됨: ${r.name}`);
      await loadList();
      setPick(r.name);
      setMerged(null);
    } catch (e) {
      const c = e instanceof ApiError && e.status === 409 ? knowledgeConflict(e.body) : null;
      if (c) setConflict(c);
      else setError(errorText(e));
    }
  }

  function loadServerCopy() {
    if (!conflict) return;
    setContent(conflict.current.content);
    setBaseMtime(conflict.current.mtime);
    setConflict(null);
  }

  function toggleMerged(open: boolean) {
    if (open && merged === null) getMergedKnowledge().then((r) => setMerged(r.text)).catch((e) => setError(errorText(e)));
  }

  return (
    <div>
      <p className="muted">
        LLM 이 모르는 사내 용어·암묵지를 Markdown 으로 정리하면, 비교 분석 시 참고 자료로 항상 주입됩니다(예: formation = 배터리 화성 공정).
      </p>
      {list && !list.enabled && <p className="warn">현재 설정에서 도메인 지식 주입이 꺼져 있습니다(config: knowledge.enabled).</p>}

      <div className="row">
        <select value={pick} onChange={(e) => setPick(e.target.value)}>
          <option value={NEW}>+ 새 파일 만들기</option>
          {list?.files.map((f) => <option key={f.name} value={f.name}>{f.name}</option>)}
        </select>
        {pick === NEW && (
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="새 파일 이름(.md)" />
        )}
      </div>

      <textarea rows={18} value={content} onChange={(e) => setContent(e.target.value)} />

      <div className="row">
        <button className="button primary" onClick={() => save(baseMtime)}>💾 저장</button>
        {notice && <span className="muted">{notice}</span>}
      </div>
      {error && <p className="error">{error}</p>}

      {conflict && (
        <div className="conflict">
          <p>{conflict.message}</p>
          <div className="row">
            <button className="button secondary" onClick={loadServerCopy}>서버에 저장된 내용 불러오기</button>
            <button className="button danger" onClick={() => save(conflict.current.mtime)}>내 내용으로 덮어쓰기</button>
          </div>
          <details>
            <summary>서버에 저장된 내용 보기</summary>
            <pre className="file-list">{conflict.current.content}</pre>
          </details>
        </div>
      )}

      <details onToggle={(e) => toggleMerged((e.target as HTMLDetailsElement).open)}>
        <summary>🔎 비교 시 주입될 전체 지식 미리보기</summary>
        <pre className="file-list">{merged ?? "불러오는 중..."}</pre>
      </details>
    </div>
  );
}
```

`web/src/tabs/index.ts` — `import KnowledgeTab from "./KnowledgeTab";` 추가, `TABS` 끝에:

```ts
  { key: "knowledge", title: "📚 도메인 지식", Component: KnowledgeTab },
```

- [ ] **Step 4: 확인**

Run: `npm --prefix web run typecheck && npm --prefix web test && npm --prefix web run build`
Expected: 모두 통과(`knowledge.test.ts` 2건 포함)

- [ ] **Step 5: 커밋**

```bash
git add web/src/lib/knowledge.ts web/src/lib/knowledge.test.ts web/src/tabs/KnowledgeTab.tsx web/src/tabs/index.ts
git commit -m "feat(web-ui): 도메인 지식 탭 — 편집·저장, 동시 저장 충돌은 불러오기/덮어쓰기"
```

---

### Task 7: 관리자 페이지 — 로그인·로그 확인·작업 관리

**Files:**
- Create: `web/src/pages/AdminPage.tsx`, `web/src/components/LogViewer.tsx`, `web/src/components/AdminJobs.tsx`
- Modify: `web/src/App.tsx` (라우트 `/admin`)

**Interfaces:**
- Consumes: Task 1 `adminStatus`, `adminLogin`, `adminLogout`, `adminLogSources`, `readAdminLog`, `adminLogDownloadUrl`, `adminJobs`, `adminCancel`, `adminDelete`, `errorText`, 타입; Task 2 `isFinal`, `formatDateTime`, `STATE_LABEL`.
- Produces: `/admin` 화면.

- [ ] **Step 1: 구현**

`web/src/components/LogViewer.tsx`:

```tsx
import { useCallback, useEffect, useState } from "react";
import { errorText } from "../api/client";
import { adminLogDownloadUrl, adminLogSources, readAdminLog } from "../api/endpoints";
import type { LogPage, LogSource } from "../api/types";

const LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"];

// 작업 로그(DEBUG)에는 프롬프트·LLM 원문이 있다 — 그래서 이 화면만 비밀번호로 잠근다.
export default function LogViewer() {
  const [sources, setSources] = useState<LogSource[]>([]);
  const [source, setSource] = useState("");
  const [level, setLevel] = useState("INFO");
  const [query, setQuery] = useState("");
  const [tail, setTail] = useState(500);
  const [page, setPage] = useState<LogPage | null>(null);
  const [follow, setFollow] = useState(true);
  const [error, setError] = useState("");

  const loadSources = useCallback(() => {
    adminLogSources()
      .then((r) => {
        setSources(r.sources);
        setSource((prev) => (prev && r.sources.some((s) => s.id === prev) ? prev : r.sources[0]?.id ?? ""));
      })
      .catch((e) => setError(errorText(e)));
  }, []);

  const loadTail = useCallback(() => {
    if (!source) return;
    readAdminLog({ source, level, q: query, tail })
      .then((p) => { setPage(p); setError(""); })
      .catch((e) => setError(errorText(e)));
  }, [source, level, query, tail]);

  useEffect(() => { loadSources(); }, [loadSources]);
  useEffect(() => { loadTail(); }, [loadTail]);
  useEffect(() => {
    if (!follow) return;
    const timer = window.setInterval(loadTail, 3000);
    return () => window.clearInterval(timer);
  }, [follow, loadTail]);

  function loadEarlier() {
    if (!page || page.start === 0) return;
    setFollow(false); // 앞쪽을 보는 중에는 새 줄로 바꾸지 않는다
    readAdminLog({ source, level, q: query, tail, before: page.start })
      .then((p) => setPage({ ...p, records: [...p.records, ...page.records], end: page.end }))
      .catch((e) => setError(errorText(e)));
  }

  return (
    <section className="admin-section">
      <h3>로그</h3>
      <div className="row">
        <select value={source} onChange={(e) => setSource(e.target.value)}>
          {sources.map((s) => <option key={s.id} value={s.id}>{s.label} ({Math.ceil(s.size / 1024)} KB)</option>)}
        </select>
        <select value={level} onChange={(e) => setLevel(e.target.value)}>
          {LEVELS.map((l) => <option key={l} value={l}>{l} 이상</option>)}
        </select>
        <input type="search" placeholder="검색어" value={query} onChange={(e) => setQuery(e.target.value)} />
        <label>줄 수 <input type="number" min={50} max={5000} step={50} value={tail}
          onChange={(e) => setTail(Number(e.target.value) || 500)} /></label>
        <label><input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> 새 줄 따라가기</label>
        <button className="button secondary" onClick={() => { loadSources(); loadTail(); }}>새로고침</button>
        {source && <a className="button secondary" href={adminLogDownloadUrl(source)}>다운로드</a>}
      </div>
      {error && <p className="error">{error}</p>}
      {page && (
        <>
          <p className="muted">{page.total}줄 중 {page.start + 1}–{page.end}</p>
          {page.start > 0 && <button className="button link" onClick={loadEarlier}>더 보기(앞쪽)</button>}
          <pre className="log-view">
            {page.records.map((r, i) => <div key={i} className={r.level ? `lv-${r.level}` : ""}>{r.text}</div>)}
          </pre>
        </>
      )}
    </section>
  );
}
```

`web/src/components/AdminJobs.tsx`:

```tsx
import { useCallback, useEffect, useState } from "react";
import { errorText } from "../api/client";
import { adminCancel, adminDelete, adminJobs } from "../api/endpoints";
import type { AdminJob } from "../api/types";
import { STATE_LABEL, formatDateTime, isFinal } from "../lib/format";

export default function AdminJobs() {
  const [jobs, setJobs] = useState<AdminJob[]>([]);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    adminJobs().then((r) => { setJobs(r.jobs); setError(""); }).catch((e) => setError(errorText(e)));
  }, []);

  useEffect(() => {
    load();
    const timer = window.setInterval(load, 5000);
    return () => window.clearInterval(timer);
  }, [load]);

  async function act(fn: () => Promise<unknown>) {
    try {
      await fn();
      load();
    } catch (e) {
      setError(errorText(e));
    }
  }

  return (
    <section className="admin-section">
      <h3>작업 관리</h3>
      {error && <p className="error">{error}</p>}
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr><th>작업</th><th>요청자</th><th>엔진</th><th>상태</th><th>접수</th><th>종료</th><th>오류</th><th></th></tr>
          </thead>
          <tbody>
            {jobs.map((j) => (
              <tr key={j.id}>
                <td>{j.id}</td>
                <td>{j.requester || "이름 없음"}</td>
                <td>{j.engine}</td>
                <td>{STATE_LABEL[j.state] ?? j.state}</td>
                <td>{formatDateTime(j.created_ts)}</td>
                <td>{formatDateTime(j.finished_ts)}</td>
                <td>{j.error}</td>
                <td>
                  {isFinal(j.state) ? (
                    <button className="button link" onClick={() => {
                      if (window.confirm(`작업 ${j.id} 의 폴더(업로드 원본·결과)를 지웁니다.`)) act(() => adminDelete(j.id));
                    }}>삭제</button>
                  ) : (
                    <button className="button link" onClick={() => act(() => adminCancel(j.id))}>취소</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
```

`web/src/pages/AdminPage.tsx`:

```tsx
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { errorText } from "../api/client";
import { adminLogin, adminLogout, adminStatus } from "../api/endpoints";
import type { AdminStatus } from "../api/types";
import AdminJobs from "../components/AdminJobs";
import LogViewer from "../components/LogViewer";

export default function AdminPage() {
  const [status, setStatus] = useState<AdminStatus | null>(null);
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  const refresh = useCallback(() => {
    adminStatus().then(setStatus).catch((e) => setError(errorText(e)));
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  async function login(e: FormEvent) {
    e.preventDefault();
    setError("");
    try {
      await adminLogin(password);
      setPassword("");
      refresh();
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function logout() {
    await adminLogout().catch(() => undefined);
    refresh();
  }

  return (
    <div className="page">
      <div className="row">
        <Link to="/">← 처음으로</Link>
        {status?.logged_in && <button className="button secondary" onClick={logout}>로그아웃</button>}
      </div>
      <h2>관리자</h2>
      {error && <p className="error">{error}</p>}
      {status && !status.enabled && (
        <p className="warn">관리자 기능이 설정되지 않았습니다. 서버의 .env 에 CC_ADMIN_PASSWORD 를 넣고 서버를 다시 시작하세요.</p>
      )}
      {status?.enabled && !status.logged_in && (
        <form className="row" onSubmit={login}>
          <input type="password" placeholder="관리자 비밀번호" value={password} onChange={(e) => setPassword(e.target.value)} />
          <button className="button primary" type="submit">로그인</button>
        </form>
      )}
      {status?.logged_in && (
        <>
          <LogViewer />
          <AdminJobs />
        </>
      )}
    </div>
  );
}
```

`web/src/App.tsx` — `import AdminPage from "./pages/AdminPage";` 추가, `/jobs/:id` 라우트 **아래**에:

```tsx
        <Route path="/admin" element={<AdminPage />} />
```

- [ ] **Step 2: 확인**

Run: `npm --prefix web run typecheck && npm --prefix web test && npm --prefix web run build`
Expected: 모두 통과

- [ ] **Step 3: 커밋**

```bash
git add web/src/pages/AdminPage.tsx web/src/components/LogViewer.tsx web/src/components/AdminJobs.tsx web/src/App.tsx
git commit -m "feat(web-ui): 관리자 페이지 — 로그인, 로그 레벨·검색·앞쪽 더 보기·다운로드, 작업 취소·삭제"
```

---

### Task 8: 실행 파일과 문서 — `start.bat`·`setup.bat`·`dev_app`·사용 안내·수동 점검

**Files:**
- Modify: `contentcompare/web/__main__.py` (`dev_app()` 추가)
- Create: `tests/test_web_main.py`
- Create: `start.bat`
- Modify: `setup.bat` (전체 재작성 — CP949)
- Modify: `docs/USER_GUIDE.md` (「### 웹 UI」 절 교체)
- Create: `docs/WEB_MANUAL_CHECK.md`
- Modify: `CLAUDE.md` (「### 진입점」의 Streamlit 항목, 「### 웹 서버」 절에 화면 항목)

**Interfaces:**
- Consumes: 계획 2 `contentcompare.web.__main__`(`configure_server_logging`, `create_app`, `load_settings`, `DOTENV_ENV`, `UVICORN_OPTIONS`), `web/dist`(Task 1~7).
- Produces: `dev_app() -> FastAPI`(uvicorn `--factory` 용), `start.bat [dev|check]`, `setup.bat`(기본 extras 에 `web`, 화면 빌드 단계).

- [ ] **Step 1: 실패하는 테스트** — `tests/test_web_main.py`

```python
"""start.bat dev 가 쓰는 dev_app 팩토리 — .env 경로를 worker 에 물려주고 앱을 만든다."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("fastapi")
from contentcompare.web import __main__ as web_main  # noqa: E402


def test_dev_app_reads_env_file_and_passes_it_down(tmp_path, monkeypatch):
    pytest.importorskip("dotenv")
    env_file = tmp_path / "dev.env"
    env_file.write_text(f"CC_JOBS_DIR={tmp_path / 'jobs'}\n", encoding="utf-8")
    monkeypatch.setenv(web_main.DOTENV_ENV, str(env_file))
    seen = {}

    def fake_logging(logs_dir):
        seen["logs_dir"] = logs_dir
        return logs_dir / "server.log"

    def fake_create_app(settings):
        seen["settings"] = settings
        return "APP"

    monkeypatch.setattr(web_main, "configure_server_logging", fake_logging)
    monkeypatch.setattr(web_main, "create_app", fake_create_app)

    assert web_main.dev_app() == "APP"
    assert seen["settings"].jobs_dir == str(tmp_path / "jobs")
    assert os.environ[web_main.DOTENV_ENV] == str(env_file.resolve())
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_web_main.py -q -p no:cacheprovider`
Expected: FAIL — `AttributeError: module 'contentcompare.web.__main__' has no attribute 'dev_app'`

- [ ] **Step 3: 구현 — `dev_app`**

`contentcompare/web/__main__.py` — `main()` 함수 **위**에 추가:

```python
def dev_app():
    """``start.bat dev`` 용 — ``uvicorn --factory --reload`` 가 부르는 인자 없는 팩토리.

    ``--reload`` 는 앱을 import 문자열로만 받아서 ``main()`` 의 인자 처리를 거치지 않는다.
    같은 일을 여기서 한다: `.env` 경로를 worker 에 물려주고 서버 로그를 켠 뒤 앱을 만든다.
    """
    dotenv = os.environ.get(DOTENV_ENV, ".env")
    settings = load_settings(dotenv)
    os.environ[DOTENV_ENV] = str(Path(dotenv).resolve())
    configure_server_logging(Path(settings.logs_dir))
    return create_app(settings)
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_web_main.py tests/test_web_admin_routes.py -q -p no:cacheprovider`
Expected: 전부 PASS

- [ ] **Step 5: `start.bat` 작성** — 먼저 UTF-8 로 쓰고 Step 7 에서 CP949+CRLF 로 바꾼다

```bat
@echo off
rem ============================================================
rem  ContentCompare 웹 서버 실행 (Windows)
rem    start.bat          서버 실행(빌드된 화면 web\dist) + 브라우저 열기
rem    start.bat dev      개발 모드: API(uvicorn 자동 재시작) + 화면(Vite) 을 각각 새 창으로
rem    start.bat check    실행 준비만 점검하고 끝낸다
rem  파이썬: .venv 가 있으면 그것을, 없으면 PATH 의 python 을 쓴다.
rem          다른 인터프리터를 쓰려면 CC_PYTHON 에 경로를 넣는다(예: set CC_PYTHON=python).
rem  끄기: 이 창에서 Ctrl+C 를 한 번 누른다(실행 중인 작업은 interrupted 로 남는다).
rem  주의: Office 자동화는 로그인된 데스크톱 세션이 필요하다. Windows 서비스로 등록하지 말 것.
rem ============================================================
rem  (이 파일은 CP949/ANSI + CRLF 로 저장되어야 한다)
setlocal
cd /d "%~dp0"
set "MODE=%~1"

rem --- 1) 파이썬 고르기 ------------------------------------------
set "PY="
if defined CC_PYTHON set "PY=%CC_PYTHON%"
if not defined PY if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo [ERROR] Python 을 찾을 수 없습니다. setup.bat 을 먼저 실행하세요.
    goto :fail
)

rem --- 2) 웹 서버 의존성 --------------------------------------------
"%PY%" -c "import fastapi, uvicorn, python_multipart, dotenv" >nul 2>nul
if errorlevel 1 (
    echo [ERROR] 웹 서버 의존성이 없습니다. python: %PY%
    echo         setup.bat 을 실행하거나: "%PY%" -m pip install -e ".[web]"
    goto :fail
)

rem --- 3) .env ------------------------------------------------------
if not exist ".env" (
    echo [ERROR] .env 가 없습니다. 아래로 만든 뒤 LLM 접속 정보와 관리자 비밀번호를 채우세요.
    echo         copy .env.example .env
    goto :fail
)

set "PORT=8000"
for /f "tokens=1,* delims==" %%a in ('findstr /b /c:"CC_PORT=" .env 2^>nul') do set "PORT=%%b"

if /i "%MODE%"=="dev" goto :dev

rem --- 4) 빌드된 화면 --------------------------------------------------
if not exist "web\dist\index.html" (
    echo [ERROR] 화면이 빌드되지 않았습니다^(web\dist^).
    echo         setup.bat 을 실행하거나, Node.js 가 있는 PC 에서 빌드한 web\dist 폴더를 복사하세요.
    goto :fail
)

if /i "%MODE%"=="check" (
    echo [OK] 실행 준비가 끝났습니다. python: %PY% / 포트: %PORT%
    goto :ok
)

echo ContentCompare 웹 서버를 시작합니다. 끄려면 이 창에서 Ctrl+C 를 한 번 누르세요.
if not defined CC_NO_BROWSER (
    start "" /min powershell -NoProfile -Command "Start-Sleep -Seconds 3; Start-Process 'http://localhost:%PORT%'"
)
"%PY%" -m contentcompare.web
goto :ok

:dev
where npm >nul 2>nul
if errorlevel 1 (
    echo [ERROR] npm 이 없습니다. 개발 모드에는 Node.js 가 필요합니다.
    goto :fail
)
if not exist "web\node_modules" (
    pushd web
    call npm ci
    popd
)
echo 개발 모드: API http://localhost:8000 / 화면 http://localhost:5173
start "ContentCompare API (dev)" "%PY%" -m uvicorn contentcompare.web.__main__:dev_app --factory --reload --reload-dir contentcompare --port 8000 --timeout-graceful-shutdown 5
start "ContentCompare UI (dev)" cmd /k "cd /d web && npm run dev"
if not defined CC_NO_BROWSER (
    start "" /min powershell -NoProfile -Command "Start-Sleep -Seconds 5; Start-Process 'http://localhost:5173'"
)
goto :ok

:ok
endlocal
exit /b 0

:fail
echo.
echo 실행을 중단했습니다.
if not defined CC_NO_PAUSE pause
endlocal
exit /b 1
```

- [ ] **Step 6: `setup.bat` 재작성** — 전체를 아래 내용(UTF-8)으로 쓰고 Step 7 에서 변환

```bat
@echo off
rem ============================================================
rem  ContentCompare 개발환경 준비 스크립트 (Windows)
rem  - .venv 가상환경 생성(없을 때만) -> 활성화 -> 패키지 설치 -> 화면 빌드
rem  사용법:
rem    setup.bat                 (기본: office,ui,dev,fastembed,langchain,web 전부 설치)
rem    setup.bat all             (기본 + onnx 까지 모두)
rem    setup.bat office,ui       (원하는 extras 만 골라서)
rem    setup.bat core            (코어 의존성만: pyyaml+requests, 화면 빌드 안 함)
rem ============================================================
rem  (이 파일은 CP949/ANSI + CRLF 로 저장되어야 한글이 콘솔에 정상 출력됩니다)
setlocal
cd /d "%~dp0"

rem  %* 로 받아야 콤마가 인자 구분자로 잘리지 않는다. 공백 구분도 콤마로 정규화.
set "EXTRAS=%*"
if "%EXTRAS%"=="" set "EXTRAS=office,ui,dev,fastembed,langchain,web"
set "EXTRAS=%EXTRAS: =,%"
if /i "%EXTRAS%"=="all" set "EXTRAS=office,ui,dev,fastembed,langchain,web,onnx"

rem --- 1) 파이썬 인터프리터 찾기 (py 런처 우선) ---------------
set "PY_CMD="
where py >nul 2>nul && set "PY_CMD=py -3"
if not defined PY_CMD (
    where python >nul 2>nul && set "PY_CMD=python"
)
if not defined PY_CMD (
    echo [ERROR] Python 을 찾을 수 없습니다. Python 3.10 이상을 설치한 뒤 다시 실행하세요.
    goto :fail
)

rem --- 2) 가상환경 생성 ---------------------------------------
if exist ".venv\Scripts\python.exe" (
    echo [1/5] 기존 .venv 를 재사용합니다.
) else (
    echo [1/5] .venv 가상환경 생성 중... ^(%PY_CMD%^)
    %PY_CMD% -m venv .venv
    if errorlevel 1 (
        echo [ERROR] 가상환경 생성 실패.
        goto :fail
    )
)

rem --- 3) 활성화 ----------------------------------------------
echo [2/5] 가상환경 활성화...
call ".venv\Scripts\activate.bat"
if errorlevel 1 (
    echo [ERROR] 가상환경 활성화 실패.
    goto :fail
)
python -c "import sys; print('     python:', sys.version.split()[0], '-', sys.executable)"

rem --- 4) pip 최신화 ------------------------------------------
echo [3/5] pip 업그레이드...
python -m pip install --upgrade pip
if errorlevel 1 (
    echo [ERROR] pip 업그레이드 실패.
    goto :fail
)

rem --- 5) 패키지 설치 -----------------------------------------
if /i "%EXTRAS%"=="core" (
    echo [4/5] 패키지 설치: 코어만 ^(pyyaml, requests^)
    python -m pip install -e .
) else (
    echo [4/5] 패키지 설치: extras = %EXTRAS%
    python -m pip install -e ".[%EXTRAS%]"
)
if errorlevel 1 (
    echo [ERROR] 패키지 설치 실패. ^(office extras 는 Windows + MS Office 환경에서만 설치됩니다^)
    goto :fail
)

rem --- 6) 화면 빌드(web\dist) ------------------------------------
if /i "%EXTRAS%"=="core" goto :done
where npm >nul 2>nul
if errorlevel 1 (
    echo [5/5] npm 이 없어 화면 빌드를 건너뜁니다. Node.js 가 있는 PC 에서 빌드한 web\dist 폴더를 복사하세요.
    goto :done
)
echo [5/5] 화면 빌드: npm ci, npm run build
pushd web
call npm ci
if errorlevel 1 (
    popd
    echo [ERROR] npm ci 실패.
    goto :fail
)
call npm run build
if errorlevel 1 (
    popd
    echo [ERROR] 화면 빌드 실패.
    goto :fail
)
popd

:done
echo.
echo ============================================================
echo  완료. 새 터미널에서는 아래로 가상환경을 켜세요.
echo    PowerShell : .\.venv\Scripts\Activate.ps1
echo    cmd        : .venv\Scripts\activate.bat
echo.
echo  다음 단계 ^(설정 파일이 없다면^):
echo    copy config\config.example.yaml config\config.yaml
echo    copy .env.example .env      ^(LLM 접속 정보, 관리자 비밀번호^)
echo    start.bat check             ^(실행 준비 점검^)
echo    start.bat                   ^(웹 서버 실행^)
echo ============================================================
if not defined CC_NO_PAUSE pause
endlocal
exit /b 0

:fail
echo.
echo 설치가 중단되었습니다.
if not defined CC_NO_PAUSE pause
endlocal
exit /b 1
```

- [ ] **Step 7: 배치 파일을 CP949 + CRLF 로 변환하고 확인**

Run:
```bash
python -c "import pathlib,sys; [pathlib.Path(p).write_bytes(pathlib.Path(p).read_text(encoding='utf-8').replace('\r\n','\n').replace('\n','\r\n').encode('cp949')) for p in sys.argv[1:]]" start.bat setup.bat
python -c "import sys; [print(p, open(p,'rb').read().decode('cp949').count('\r\n'), open(p,'rb').read().count(b'\n')) for p in sys.argv[1:]]" start.bat setup.bat
```
Expected: 첫 명령은 오류 없이 끝난다(CP949 에 없는 문자가 있으면 `UnicodeEncodeError` — 그 문자를 바꿀 것). 두 번째 명령은 파일마다 두 숫자(CRLF 수, LF 수)가 **같게** 출력한다.

Run: `cmd //c "set CC_NO_PAUSE=1&& set CC_PYTHON=python&& start.bat check"`
Expected: 이 PC 의 상태에 맞는 한 줄이 한글이 깨지지 않고 출력된다 — `.env` 가 없으면 `[ERROR] .env 가 없습니다...`, 있고 `web\dist` 가 있으면 `[OK] 실행 준비가 끝났습니다. python: python / 포트: 8000`. 종료코드는 각각 1/0.

- [ ] **Step 8: 문서**

`docs/USER_GUIDE.md` — 「### 웹 UI」 절의 본문 전체를 다음으로 교체(제목 줄은 유지):

```markdown
여러 사람이 브라우저로 접속해 쓰는 웹 서버다. 서버 PC(Windows + Office) 한 대에서 실행한다.

1. 처음 한 번: `setup.bat`(패키지 설치 + 화면 빌드), `copy .env.example .env` 후 `.env` 에 LLM 접속 정보(`CC_LLM_BACKEND`·`CC_LLM_BASE_URL`·`CC_LLM_API_KEY`·`CC_CHAT_MODEL` …)와 관리자 비밀번호(`CC_ADMIN_PASSWORD`)를 채운다. 세부 튜닝값은 계속 `config/config.yaml`(`CC_CONFIG`)에 둔다.
2. `start.bat check` 로 준비 상태를 확인하고 `start.bat` 으로 실행한다. 창에 접속 주소(`http://<PC 이름>:8000`)가 나오고 브라우저가 열린다.
3. 다른 사람은 그 주소로 접속해 **파일(또는 폴더)을 업로드**하고 🚀 비교 실행을 누른다. 실행 창에서 진행률(%)·현재 단계·로그를 실시간으로 본다. 창을 닫아도 작업은 계속되고 '내 최근 작업'에서 다시 연다.
4. 비교는 **한 번에 1건**씩 돈다(Office·LLM 요청 한도 때문). 다른 작업이 돌고 있으면 "대기 중 · 앞에 N건"으로 보인다.
5. 끄기: 서버 창에서 Ctrl+C 를 **한 번** 누른다(최대 5초). 실행 중이던 작업은 '중단됨'으로 남고 다시 돌리지 않는다.

- 🔐 관리자: 상단 버튼 → 비밀번호 → 로그(레벨·검색·다운로드)와 작업 관리(취소·삭제). 5회 틀리면 1분 잠긴다. HTTPS 가 없으므로 **사내망 전용**이다.
- 서버 PC 에서 사람이 Office 를 함께 쓰지 말 것 — 작업이 비정상 종료되면 새로 뜬 Office 를 정리하면서 함께 닫힐 수 있다. Windows 서비스로 등록하지 말 것(Office 자동화는 로그인된 데스크톱 세션이 필요하다).
- 서버 PC 에 Node.js 가 없으면 Node 가 있는 PC 에서 `npm --prefix web ci && npm --prefix web run build` 로 만든 `web\dist` 폴더를 복사한다.
- 개발: `start.bat dev` — API(자동 재시작)와 화면(Vite, http://localhost:5173)을 각각 새 창으로 띄운다.
- 기존 Streamlit 화면(`streamlit run app/streamlit_app.py`)은 새 화면이 검증될 때까지 남겨 둔다(한 사람이 로컬에서 쓸 때).
```

`docs/WEB_MANUAL_CHECK.md`:

```markdown
# 웹 화면 수동 점검 (서버 PC, 실제 Office·LLM)

설계서 `docs/superpowers/specs/2026-09-29-web-frontend-design.md` §13 의 수동 점검을 실제로 한 번 돌리는 체크리스트다. 자동 테스트가 볼 수 없는 것(실제 Office COM, 브라우저 폴더 업로드, 사내망 접속, Ctrl+C)을 본다.

준비: `setup.bat` → `.env` 작성 → `start.bat check` 가 `[OK]`.

- [ ] 1. 다른 PC 브라우저에서 `http://<서버 PC 이름>:8000` 접속 → 좌측 🔌 LLM 연결 테스트가 모두 ✅
- [ ] 2. Excel 기준 + Word/PPT 대상을 **파일 선택**과 **폴더 선택(한글 하위 폴더 포함)**으로 올려 실행 → 진행률 %·현재 단계·로그가 움직이고 끝나면 결과·리포트가 보인다
- [ ] 3. 두 브라우저(또는 PC)에서 거의 동시에 실행 → 두 번째가 "대기 중 · 앞에 1건", 첫 작업이 끝나면 이어서 시작
- [ ] 4. 실행 중 취소 → 작업 관리자에 EXCEL/WINWORD/POWERPNT 가 남지 않음. 점검 전에 사람이 직접 연 Office 창은 그대로
- [ ] 5. 실행 창 닫기 → '내 최근 작업'에서 다시 열기 / "새 창으로" → `/jobs/<ID>` 가 따로 열림 / 다른 사람이 그 링크를 열면 취소 버튼이 없다
- [ ] 6. 📄 리포트·🔬 현미경(디버깅·학습)·⏱ 타임라인·📚 도메인 지식 탭이 Streamlit 과 같은 내용을 보인다. 도메인 지식을 두 창에서 동시에 편집·저장 → 늦은 쪽에 충돌 안내
- [ ] 7. 🔐 관리자 로그인 → 작업의 DEBUG 로그 조회·레벨 필터·검색·앞쪽 더 보기·다운로드. 틀린 비밀번호 5회 → 1분 잠금
- [ ] 8. 실행 창을 연 채로 서버 창에서 Ctrl+C **한 번** → 5초 안에 꺼지고, 다시 `start.bat` → 그 작업은 '중단됨', 대기 작업은 순서대로 이어서 실행
- [ ] 9. `start.bat` 을 한 번 더 실행(이미 실행 중) → "이미 같은 jobs 폴더로 실행 중인 서버가 있습니다" 로 두 번째가 멈추고 첫 서버의 작업은 영향이 없다
- [ ] 10. (알려진 한계 확인) 서버 창의 X 로 닫으면 worker 도 함께 죽고 남은 Office 는 정리되지 않을 수 있다 — 결과를 기록해 후속 과제로 남긴다
```

`CLAUDE.md` — 「### 진입점」의 `- **Streamlit** …` 항목 **첫 문장 앞**에 `(기존·단일 사용자용 — 웹 서버가 검증될 때까지 유지) ` 를 넣는다. 「### 웹 서버 (`contentcompare/web/`)」 절 끝에 추가:

```markdown
- **화면은 `web/`(Vite + React + TS)이고 빌드 결과 `web/dist` 를 서버가 서빙한다**(`static.py`). 계산이 있는 로직은 `web/src/lib/`·`web/src/api/client.ts` 의 순수 함수로 두고 vitest 로 시험한다(`npm --prefix web test`). 모든 API 호출은 `web/src/api/endpoints.ts` 를 거친다.
- ⚠️ **XSS 규칙**: `dangerouslySetInnerHTML` 금지. 요청자 이름·파일명·로그는 텍스트로만, 마크다운은 `skipHtml`, 서버가 만든 HTML(현미경·타임라인)은 `<iframe srcDoc sandbox="allow-scripts">` 로만 — `allow-same-origin` 을 주면 산출물 안의 문서 원문이 쿠키·API 에 닿는다.
- 실행: `start.bat`(점검 `start.bat check`, 개발 `start.bat dev` = uvicorn `--factory contentcompare.web.__main__:dev_app --reload` + Vite). `.bat` 은 CP949 + CRLF 로 저장한다. 수동 점검은 `docs/WEB_MANUAL_CHECK.md`.
```

- [ ] **Step 9: 전체 확인**

Run: `python -m pytest -q -p no:cacheprovider`
Expected: 전부 PASS(계획 2 기준 1366 + 이 태스크 1건)

Run: `npm --prefix web run typecheck && npm --prefix web test && npm --prefix web run build`
Expected: 모두 통과

- [ ] **Step 10: 커밋**

```bash
git add contentcompare/web/__main__.py tests/test_web_main.py start.bat setup.bat docs/USER_GUIDE.md docs/WEB_MANUAL_CHECK.md CLAUDE.md
git commit -m "feat(web): start.bat·setup.bat — 한 번에 실행, 개발 모드, 화면 빌드, 사용 안내와 수동 점검표"
```

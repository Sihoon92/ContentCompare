# 웹 전환 2/3 — 백엔드 웹 서버(작업 대기열·worker·API·관리자) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 여러 명이 브라우저로 접속해 문서를 업로드하고, 한 번에 1건씩 비교를 실행하고, 진행률·로그를 실시간으로 받고, 결과·리포트·현미경·타임라인·도메인 지식을 조회하고, 관리자가 로그를 볼 수 있는 FastAPI 서버를 만든다.

**Architecture:** uvicorn 프로세스 1개가 API 를 서빙하고, 스케줄러 스레드가 디스크의 `jobs/<ID>/job.json` 을 원본 삼아 대기열을 1건씩 돌린다. 작업은 `python -m contentcompare.web.worker <작업폴더>` 별도 프로세스로 실행되고, 서버와는 **파일로만** 통신한다(`console.log`·`progress.jsonl`·`result.json`·`error.json`). 실시간 전달은 그 파일을 읽어 SSE 로 흘린다. 파이프라인 코드는 바꾸지 않는다.

**Tech Stack:** Python 3.10+, FastAPI(0.129), uvicorn, python-multipart, python-dotenv, pytest + FastAPI `TestClient`(httpx).

**Spec:** `docs/superpowers/specs/2026-09-29-web-frontend-design.md` — §4~§6, §8.3, §9~§13 을 구현한다. 화면(§8.1·8.2·8.4)과 `start.bat`(§11)은 계획 3.

**앞선 계획:** 1/3 진행률(`contentcompare/progress.py`, 커밋 `84308fe`~`72f4c95`). 이 계획은 `prog.JsonlProgress`·`prog.load_events`·`prog.summarize`·`Snapshot.to_dict()`·`prog.set_reporter`·`prog.reset_reporter` 를 쓴다.

**후속 계획:** 3/3 React 화면 + `start.bat` 은 Task 11~13 의 API 를 쓴다.

## 선행 조건

- 브랜치: `claude/word-block-boundary`(계획 1 병합 완료) 위에서 `feat/web-backend` 를 만든다.
- 운영 파이썬(anaconda, `python`)에 **`python-multipart` 가 없다.** Task 2 Step 1 에서 설치한다. `fastapi`·`uvicorn`·`python-dotenv`·`httpx` 는 이미 있다.
- `.venv` 에는 `fastapi`·`httpx` 가 없다. 그래서 FastAPI 를 쓰는 테스트 파일은 `pytest.importorskip` 로 건너뛰게 만들고, **이 계획의 "통과" 기준은 운영 파이썬(`python -m pytest`)이다.** `.venv` 는 기존 기준선(`test_timeline_wiring.py` 5건 실패)만 유지하면 된다.

## Global Constraints

- 코드 주석·독스트링·문서는 한국어, 식별자는 영어.
- **파이프라인(`fact/`·`pipeline.py`·`comparison/`·`readers/`)은 수정하지 않는다.** worker 는 CLI 처럼 `make_pipeline(config, engine).run(reference, targets)` 만 부른다. 예외: Task 1 의 `progress.summarize` 방어(계획 1 리뷰에서 미뤄 둔 항목).
- 새 의존성은 extra `web` 에만 둔다: `fastapi`, `uvicorn`, `python-multipart`, `python-dotenv`. 코어 의존성(`pyyaml`·`requests`)은 늘리지 않는다. `contentcompare/web/__init__.py` 는 FastAPI 를 import 하지 않는다(worker 가 그 패키지를 쓴다).
- 설정 우선순위: **OS 환경변수 > `.env` > `config.yaml`(`CC_CONFIG`) > 코드 기본값.** `.env` 키 → `AppConfig` 필드 매핑은 `settings.ENV_MAP` **한 곳**에만 둔다.
- **API 키를 작업 폴더에 남기지 않는다.** worker 는 `.env` 를 스스로 읽는다. `job.json` 에는 `public_llm_summary()`(키 제외)만 기록한다.
- 동시 실행은 **1건**. uvicorn 은 worker 1개로 띄운다. 디스크의 `job.json` 이 상태의 원본이다.
- 사용자 화면용 로그(`console.log`)에는 INFO 이상만, 프롬프트·원문이 담기는 DEBUG 로그(`contentcompare_*.log`)는 **관리자 API 로만** 연다.
- 경로를 받는 모든 API 는 허용된 루트(`jobs/`·`reports/`·`artifacts/`·`logs/`·`knowledge/`) 안인지 검사하고, 작업 ID 는 정규식 `^\d{8}-\d{6}-[0-9a-f]{4}$` 로만 받는다.
- 작업은 자동 종료하지 않는다(멈춤은 경고만). 서버 재시작 시 실행 중이던 작업은 `interrupted` 로 두고 다시 돌리지 않는다.
- 테스트는 Office·LLM·네트워크 없이 돈다. Windows 전용 호출(`tasklist`·`taskkill`)은 주입해서 가짜로 시험한다.
- 커밋은 그 태스크의 파일만 `git add <경로>` 로 올린다. 메시지 끝에:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_015WPCPHZGMqNLc7QQAv1ako
  ```

## Review Focus

1. **한글 파일명·폴더 업로드 경로**(`자료/규격서 v2.docx`) — 브라우저·httpx 가 multipart `filename` 을 어떻게 싣든 서버는 그 상대 경로를 그대로 보존해야 한다. → Task 11 `test_submit_uses_explicit_relative_paths_for_korean_names`(파일명 대신 `target_paths` 폼 필드를 우선)
2. **worker 가 강제 종료된 뒤 남은 반쪽 줄**(`console.log` 끝에 개행 없는 줄) — SSE 는 그 줄을 반쯤 보내지 않고 기다려야 하며, 개행 없는 거대한 줄에서 영영 멈추지도 않아야 한다. → Task 9 `test_partial_line_waits_until_newline`, `test_oversized_line_without_newline_is_flushed`
3. **같은 초에 두 사람이 제출** — 작업 ID 가 겹치면 한 사람의 업로드가 다른 사람 폴더를 덮는다. → Task 11 `test_job_id_collision_is_retried`
4. **서버 재시작 직후 대기열** — 실행 중이던 작업은 `interrupted`, 대기 작업은 순서를 유지한 채 이어서 돈다. → Task 7 `test_recover_marks_running_interrupted_and_keeps_queue_order`
5. **관리자 비밀번호 무차별 대입** — 5회 틀리면 맞는 비밀번호도 1분간 거절. → Task 10 `test_lockout_rejects_even_the_right_password`

---

## 파일 구조

| 파일 | 책임 | 태스크 |
|---|---|---|
| `contentcompare/progress.py` | `summarize` 가 모양이 틀린 이벤트를 건너뛰게(수정) | 1 |
| `contentcompare/web/__init__.py` | 패키지 표지(FastAPI import 금지) | 2 |
| `contentcompare/web/settings.py` | `.env` 읽기, `WebSettings`, `ENV_MAP`, `build_app_config`, `public_llm_summary` | 2 |
| `contentcompare/web/jobs.py` | `Job` 모델, 상태 상수, `JobStore`, `new_job_id`, `purge_expired` | 3 |
| `contentcompare/web/uploads.py` | 업로드 이름 정규화·검증, 크기 제한 저장 | 4 |
| `contentcompare/web/results.py` | 파이프라인 결과 → 화면용 JSON, RAG 리포트 markdown | 5 |
| `contentcompare/web/worker.py` | 작업 1건 실행(별도 프로세스 진입점) | 6 |
| `contentcompare/web/scheduler.py` | 대기열 1건 실행, 취소, 재시작 복구, 백그라운드 스레드 | 7 |
| `contentcompare/web/launcher.py` | worker 프로세스 실행·종료, Office 프로세스 정리 | 8 |
| `contentcompare/web/events.py` | `console.log`·`progress.jsonl` 꼬리 읽기 → SSE | 9 |
| `contentcompare/web/admin.py` | 관리자 인증(잠금), 로그 소스 목록·읽기·필터 | 10 |
| `contentcompare/web/app.py` | FastAPI 앱 조립, 작업·LLM 점검 라우트, 쿠키 | 11 |
| `contentcompare/web/views.py` | 리포트·현미경·타임라인·도메인 지식 라우트 | 12 |
| `contentcompare/web/admin_routes.py` | 관리자 라우트 | 13 |
| `contentcompare/web/static.py` | 빌드된 화면(`web/dist`) 서빙, 미빌드 안내 | 13 |
| `contentcompare/web/__main__.py` | `python -m contentcompare.web` — 서버 기동 | 13 |
| `.env.example`, `pyproject.toml`, `.gitignore` | 견본·의존성·제외 | 2 |
| `tests/web_fake_factory.py` | worker 통합 테스트용 가짜 파이프라인 팩토리(테스트 수집 대상 아님) | 8 |
| `tests/test_web_*.py` | 태스크별 테스트 | 2~13 |
| `CLAUDE.md`, 설계서 §9 | 웹 서버 절, API 경로 갱신 | 13 |

---

### Task 1: `progress.summarize` — 모양이 틀린 이벤트를 건너뛴다

계획 1 리뷰의 미룬 항목. 서버가 `summarize` 를 부르므로 이벤트 하나 때문에 API 가 500 을 내면 안 된다.

**Files:**
- Modify: `contentcompare/progress.py` (`summarize` 를 이벤트 적용 함수 `_apply` 로 분리)
- Modify: `tests/test_progress.py` (테스트 1건 추가)

**Interfaces:**
- Consumes: 계획 1 의 `Snapshot`, `UnitState`, `_fraction`.
- Produces: `summarize(events)` 의 계약 강화 — 어떤 이벤트도 예외를 올리지 않는다.

- [ ] **Step 1: 실패하는 테스트** — `tests/test_progress.py` 끝에 추가

```python
def test_malformed_events_are_skipped_not_raised():
    """파싱은 됐지만 모양이 틀린 이벤트 하나가 화면 전체(서버 API)를 죽이면 안 된다."""
    events = [
        {"seq": 1, "ev": "plan",
         "units": [{"key": "a", "label": "A", "kind": "doc"}, "깨진항목"]},
        {"seq": 2, "ev": "unit_start", "key": "a", "parts": "abc"},
        {"seq": "x", "ev": "step", "key": "a", "done": 1, "total": 2},
        {"seq": 3, "ts": "abc", "ev": "unit_start", "key": "a"},
        "문자열 이벤트",
        {"seq": 4, "ev": "unit_start", "key": "a"},
        {"seq": 5, "ev": "unit_done", "key": "a"},
    ]
    snap = prog.summarize(events)
    assert snap.total == 1
    assert snap.units[0].state == prog.DONE
    assert snap.fraction == 1.0
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_progress.py::test_malformed_events_are_skipped_not_raised -q -p no:cacheprovider`
Expected: FAIL — `AttributeError: 'str' object has no attribute 'get'`

- [ ] **Step 3: 구현** — `contentcompare/progress.py` 의 `summarize` 함수 전체를 아래로 교체

```python
def summarize(events: Iterable[dict]) -> Snapshot:
    """이벤트 → 진행 상태. **진행률은 이전 최댓값 아래로 내려가지 않는다.**

    배치가 출력 절단으로 쪼개져 다시 세거나 하위 단계가 넘어가며 배치 수가 초기화되어도
    화면의 막대가 뒤로 가지 않게 하는 곳이 여기 한 군데다.

    **모양이 틀린 이벤트는 건너뛴다.** 우리 쓰기 코드는 그런 값을 만들지 않지만, 웹 서버가
    이 함수를 매 요청 부르므로 손상된 줄 하나가 API 를 500 으로 만들면 안 된다.
    """
    snap = Snapshot()
    index: dict[str, UnitState] = {}
    run: dict = {}
    best = 0.0
    for ev in events:
        try:
            run, best = _apply(ev, snap, index, run, best)
        except (TypeError, ValueError, AttributeError, KeyError):
            continue
    snap.fraction = best
    if run:
        snap.current = run["key"]
        snap.current_label = index[run["key"]].label
        snap.part_name = run["name"]
        snap.part_index = run["index"]
        snap.parts = run["parts"]
        snap.step_done = run["done"]
        snap.step_total = run["total"]
    return snap


def _apply(ev: dict, snap: Snapshot, index: dict[str, UnitState], run: dict,
           best: float) -> tuple[dict, float]:
    """이벤트 하나를 반영하고 ``(진행 중 단위, 최댓값)`` 을 돌려준다.

    예외가 나면 호출자가 그 이벤트만 버린다. 그래서 **상태를 바꾸기 전에 값을 모두 계산**한다
    (``unit_start`` 가 ``parts`` 변환에 실패했는데 상태만 RUNNING 이 되면 안 된다).
    """
    kind = ev.get("ev")
    ts = float(ev.get("ts") or 0.0)
    seq = int(ev.get("seq") or 0)
    if ts:
        snap.started_ts = snap.started_ts or ts
        snap.last_ts = ts
    snap.last_seq = max(snap.last_seq, seq)
    if kind == "plan":
        snap.units = []
        index.clear()
        for u in ev.get("units") or []:
            key = str(u.get("key") or "")
            if not key or key in index:
                continue  # 겹치는 키는 첫 것만 센다
            state = UnitState(key, str(u.get("label") or key), str(u.get("kind") or ""))
            index[key] = state
            snap.units.append(state)
        return {}, 0.0
    key = str(ev.get("key") or "")
    state = index.get(key)
    if kind == "unit_start" and state is not None and state.state not in FINISHED:
        new_run = {"key": key, "parts": max(1, int(ev.get("parts") or 1)),
                   "index": 0, "name": "", "done": 0, "total": 0}
        state.state = RUNNING
        run = new_run
    elif kind == "part" and key and run.get("key") == key:
        run = {**run, "index": int(ev.get("index") or 0),
               "name": str(ev.get("name") or ""), "done": 0, "total": 0}
    elif kind == "step" and key and run.get("key") == key:
        run = {**run, "done": int(ev.get("done") or 0), "total": int(ev.get("total") or 0)}
    elif kind == "unit_done" and state is not None and state.state not in FINISHED:
        state.state = DONE if ev.get("ok", True) else FAILED
        state.error = str(ev.get("error") or "")
        if run.get("key") == key:
            run = {}
    elif kind == "finish_remaining":
        for u in snap.units:
            if u.state == RUNNING:
                u.state, u.error = FAILED, u.error or "중단"
            elif u.state == PENDING:
                u.state, u.error = SKIPPED, str(ev.get("error") or "")
        run = {}
    return run, max(best, _fraction(snap.units, run))
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_progress.py tests/test_fact_pipeline_progress.py tests/test_pipeline_progress.py -q -p no:cacheprovider`
Expected: 전부 PASS(기존 진행률 테스트 무변화)

- [ ] **Step 5: 커밋**

```bash
git add contentcompare/progress.py tests/test_progress.py
git commit -m "fix(progress): summarize 가 모양이 틀린 이벤트를 건너뛴다 — 서버 API 보호

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_015WPCPHZGMqNLc7QQAv1ako"
```

---

### Task 2: 설정 — `.env` 로더와 `AppConfig` 덮어쓰기

**Files:**
- Create: `contentcompare/web/__init__.py`
- Create: `contentcompare/web/settings.py`
- Create: `.env.example`
- Modify: `pyproject.toml` (`[project.optional-dependencies]` 에 `web` 추가)
- Modify: `.gitignore` (`.env`, `jobs/`)
- Create: `tests/test_web_settings.py`

**Interfaces:**
- Consumes: `contentcompare.config.AppConfig.load(path|None)`.
- Produces:
  - `ENV_MAP: dict[str, tuple[str, ...]]`
  - `@dataclass WebSettings(config_path="", admin_password="", host="0.0.0.0", port=8000, jobs_dir="jobs", logs_dir="logs", retention_days=7, max_upload_mb=200, stall_warn_min=5, env: dict[str,str])`, 프로퍼티 `admin_enabled: bool`, `max_upload_bytes: int`
  - `read_env(dotenv_path=".env", environ=None) -> dict[str, str]`
  - `load_settings(dotenv_path=".env", environ=None) -> WebSettings`
  - `build_app_config(settings: WebSettings) -> AppConfig`
  - `public_llm_summary(config: AppConfig) -> dict[str, str]` — 키 `backend`, `chat_model`, `embed_backend`, `embed_model`, `base_url`(API 키 없음)

- [ ] **Step 1: 의존성 설치(운영 파이썬)**

Run: `python -m pip install "python-multipart>=0.0.9"`
Expected: `Successfully installed python-multipart-...`(이미 있으면 `Requirement already satisfied`)

- [ ] **Step 2: 실패하는 테스트** — `tests/test_web_settings.py`

```python
"""웹 설정 — .env 우선순위와 AppConfig 매핑이 한 곳(ENV_MAP)에서 끝나는가."""

from __future__ import annotations

import pytest

from contentcompare.config import AppConfig
from contentcompare.web import settings as ws


def test_every_env_map_path_exists_on_app_config():
    """매핑된 키가 실제 필드에 닿지 않으면 '설정에는 있는데 호출 경로에는 없다'가 된다."""
    config = AppConfig()
    for key, path in ws.ENV_MAP.items():
        obj = config
        for name in path[:-1]:
            obj = getattr(obj, name)
        assert hasattr(obj, path[-1]), f"{key} → {'.'.join(path)}"


def test_missing_dotenv_file_uses_environ_only(tmp_path):
    env = ws.read_env(str(tmp_path / "없음.env"), environ={"CC_PORT": "9001", "PATH": "x"})
    assert env == {"CC_PORT": "9001"}


def test_environ_beats_dotenv(tmp_path):
    pytest.importorskip("dotenv")
    dotenv = tmp_path / ".env"
    dotenv.write_text("CC_CHAT_MODEL=from-file\nCC_PORT=9000\n", encoding="utf-8")
    settings = ws.load_settings(str(dotenv), environ={"CC_CHAT_MODEL": "from-os"})
    assert settings.env["CC_CHAT_MODEL"] == "from-os"
    assert settings.port == 9000


def test_build_app_config_applies_env_over_yaml(tmp_path):
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(
        "llm:\n  backend: ollama\n  chat_model: yaml-model\n  embed_model: yaml-embed\n",
        encoding="utf-8")
    settings = ws.load_settings(str(tmp_path / "없음.env"), environ={
        "CC_CONFIG": str(yaml_path),
        "CC_LLM_BACKEND": "langchain",
        "CC_CHAT_MODEL": "env-model",
        "CC_LLM_BASE_URL": "https://llm.example/v1",
        "CC_LLM_API_KEY": "secret-key",
        "CC_EMBED_MODEL": "",           # 빈 값은 덮어쓰지 않는다
    })
    config = ws.build_app_config(settings)
    assert config.llm.backend == "langchain"
    assert config.llm.chat_model == "env-model"
    assert config.llm.internal.base_url == "https://llm.example/v1"
    assert config.llm.internal.api_key == "secret-key"
    assert config.llm.embed_model == "yaml-embed"


def test_invalid_integer_names_the_key(tmp_path):
    with pytest.raises(ValueError, match="CC_PORT"):
        ws.load_settings(str(tmp_path / "없음.env"), environ={"CC_PORT": "abc"})


def test_admin_disabled_when_password_blank(tmp_path):
    assert not ws.load_settings(str(tmp_path / "x"), environ={}).admin_enabled
    assert ws.load_settings(str(tmp_path / "x"),
                            environ={"CC_ADMIN_PASSWORD": "pw"}).admin_enabled


def test_public_summary_never_contains_the_api_key(tmp_path):
    settings = ws.load_settings(str(tmp_path / "x"), environ={
        "CC_LLM_BACKEND": "internal", "CC_LLM_API_KEY": "secret-key"})
    summary = ws.public_llm_summary(ws.build_app_config(settings))
    assert "secret-key" not in str(summary)
    assert summary["backend"] == "internal"


def test_upload_limit_in_bytes(tmp_path):
    settings = ws.load_settings(str(tmp_path / "x"), environ={"CC_MAX_UPLOAD_MB": "3"})
    assert settings.max_upload_bytes == 3 * 1024 * 1024
```

- [ ] **Step 3: 실패 확인**

Run: `python -m pytest tests/test_web_settings.py -q -p no:cacheprovider`
Expected: FAIL — `ModuleNotFoundError: No module named 'contentcompare.web'`

- [ ] **Step 4: 구현**

`contentcompare/web/__init__.py`:

```python
"""웹 서버(FastAPI) — 여러 명이 브라우저로 쓰는 비교 서비스.

⚠️ 여기서 FastAPI 를 import 하지 말 것. worker 프로세스(:mod:`.worker`)가 이 패키지를 쓰는데,
worker 는 웹 의존성 없이도 떠야 한다(설계 §4).
"""
```

`contentcompare/web/settings.py`:

```python
"""웹 서버 설정 — `.env` 를 읽어 웹 전용 값과 :class:`AppConfig` 덮어쓰기를 만든다.

우선순위: **OS 환경변수 > `.env` > `config.yaml`(``CC_CONFIG``) > 코드 기본값** (설계 §10).
연결·비밀값만 `.env` 에 두고, 튜닝값(recall_k·배치 크기 …)은 `config.yaml` 에 남는다 —
100개가 넘는 키를 `.env` 로 옮기면 오히려 관리가 어려워진다.

`.env` 키 → 설정 필드 매핑은 :data:`ENV_MAP` **한 곳**에만 둔다. 이 저장소가 두 번 당한
"설정에는 있는데 호출 경로에는 없다" 결함을 막으려는 것이고, 매핑된 경로가 실제 필드에
닿는지는 테스트가 고정한다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

from ..config import AppConfig

# `.env` 키 → AppConfig 필드 경로. 새 키는 여기에만 더한다.
ENV_MAP: dict[str, tuple[str, ...]] = {
    "CC_LLM_BACKEND": ("llm", "backend"),
    "CC_LLM_BASE_URL": ("llm", "internal", "base_url"),
    "CC_OLLAMA_HOST": ("llm", "ollama", "host"),
    "CC_LLM_API_KEY": ("llm", "internal", "api_key"),
    "CC_CHAT_MODEL": ("llm", "chat_model"),
    "CC_EMBED_BACKEND": ("llm", "embed_backend"),
    "CC_EMBED_MODEL": ("llm", "embed_model"),
}

# 정수 웹 설정 — 잘못된 값은 조용히 기본값으로 두지 않고 키 이름과 함께 실패한다.
_INT_KEYS = {
    "CC_PORT": "port",
    "CC_JOB_RETENTION_DAYS": "retention_days",
    "CC_MAX_UPLOAD_MB": "max_upload_mb",
    "CC_STALL_WARN_MIN": "stall_warn_min",
}


@dataclass
class WebSettings:
    config_path: str = ""
    admin_password: str = ""
    host: str = "0.0.0.0"
    port: int = 8000
    jobs_dir: str = "jobs"
    logs_dir: str = "logs"
    retention_days: int = 7
    max_upload_mb: int = 200
    stall_warn_min: int = 5
    env: dict[str, str] = field(default_factory=dict)
    """병합된 원본 값. :func:`build_app_config` 가 :data:`ENV_MAP` 을 여기서 읽는다."""

    @property
    def admin_enabled(self) -> bool:
        return bool(self.admin_password)

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


def read_env(dotenv_path: str = ".env",
             environ: Optional[Mapping[str, str]] = None) -> dict[str, str]:
    """`.env` 값 위에 OS 환경변수(``CC_`` 로 시작하는 것만)를 얹는다."""
    merged: dict[str, str] = {}
    if dotenv_path and Path(dotenv_path).is_file():
        from dotenv import dotenv_values  # extra `web` — 파일이 있을 때만 필요하다

        merged.update({k: v for k, v in dotenv_values(dotenv_path).items() if v is not None})
    env = os.environ if environ is None else environ
    merged.update({k: v for k, v in env.items() if k.startswith("CC_")})
    return merged


def load_settings(dotenv_path: str = ".env",
                  environ: Optional[Mapping[str, str]] = None) -> WebSettings:
    env = read_env(dotenv_path, environ)
    settings = WebSettings(env=env)
    settings.config_path = env.get("CC_CONFIG", "").strip()
    settings.admin_password = env.get("CC_ADMIN_PASSWORD", "").strip()
    settings.host = env.get("CC_HOST", "").strip() or settings.host
    settings.jobs_dir = env.get("CC_JOBS_DIR", "").strip() or settings.jobs_dir
    for key, attr in _INT_KEYS.items():
        raw = env.get(key, "").strip()
        if not raw:
            continue
        try:
            value = int(raw)
        except ValueError:
            raise ValueError(f"{key} 는 정수여야 합니다: {raw!r}") from None
        setattr(settings, attr, value)
    return settings


def build_app_config(settings: WebSettings) -> AppConfig:
    """`config.yaml` 을 읽고 :data:`ENV_MAP` 의 값으로 덮어쓴다. 빈 값은 덮어쓰지 않는다."""
    config = AppConfig.load(settings.config_path or None)
    for key, path in ENV_MAP.items():
        value = settings.env.get(key, "").strip()
        if value:
            _assign(config, path, value)
    return config


def _assign(obj: Any, path: tuple[str, ...], value: str) -> None:
    for name in path[:-1]:
        obj = getattr(obj, name)
    if not hasattr(obj, path[-1]):
        raise AttributeError(f"설정 경로가 없습니다: {'.'.join(path)}")
    setattr(obj, path[-1], value)


def public_llm_summary(config: AppConfig) -> dict[str, str]:
    """화면·`job.json` 에 남길 LLM 정보. **API 키는 절대 넣지 않는다.**"""
    llm = config.llm
    remote = llm.backend in ("internal", "langchain")
    return {
        "backend": llm.backend,
        "chat_model": llm.chat_model,
        "embed_backend": llm.embed_backend or llm.backend,
        "embed_model": llm.embed_model,
        "base_url": llm.internal.base_url if remote else llm.ollama.host,
    }
```

`.env.example`:

```
# ContentCompare 웹 서버 설정. 이 파일을 .env 로 복사해 채운다(.env 는 git 에 올라가지 않는다).
# 우선순위: OS 환경변수 > .env > config.yaml(CC_CONFIG) > 코드 기본값

# 세부 설정 원본(선택). 튜닝값(recall_k, 배치 크기 등)은 여기에 둔다.
CC_CONFIG=config/config.yaml

# LLM 연결
CC_LLM_BACKEND=langchain
CC_LLM_BASE_URL=https://llm.intra.corp/v1
CC_LLM_API_KEY=
CC_CHAT_MODEL=
CC_OLLAMA_HOST=
CC_EMBED_BACKEND=fastembed
CC_EMBED_MODEL=

# 관리자 페이지 비밀번호. 비우면 관리자 기능이 꺼진다.
# ⚠️ HTTPS 가 없으므로 사내망 전용이다(비밀번호가 평문으로 오간다).
CC_ADMIN_PASSWORD=

# 서버
CC_HOST=0.0.0.0
CC_PORT=8000
CC_JOBS_DIR=jobs
CC_JOB_RETENTION_DAYS=7
CC_MAX_UPLOAD_MB=200
CC_STALL_WARN_MIN=5
```

`pyproject.toml` — `[project.optional-dependencies]` 의 `langfuse = [...]` 블록 **위**에 추가:

```toml
# 웹 서버(contentcompare.web). worker 프로세스는 이것 없이도 뜬다.
web = [
    "fastapi>=0.110",
    "uvicorn>=0.29",
    "python-multipart>=0.0.9",
    "python-dotenv>=1.0",
]
```

`.gitignore` — `artifacts/` 줄 **아래**에 추가:

```
# 웹 서버: 비밀값(.env)과 작업 폴더(업로드 원본·산출물)
.env
jobs/
```

- [ ] **Step 5: 통과 확인**

Run: `python -m pytest tests/test_web_settings.py -q -p no:cacheprovider`
Expected: 8 passed

- [ ] **Step 6: 커밋**

```bash
git add contentcompare/web/__init__.py contentcompare/web/settings.py tests/test_web_settings.py .env.example pyproject.toml .gitignore
git commit -m "feat(web): .env 설정 로더 — 키 매핑은 ENV_MAP 한 곳에서

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_015WPCPHZGMqNLc7QQAv1ako"
```

---

### Task 3: 작업 모델과 디스크 저장소

**Files:**
- Create: `contentcompare/web/jobs.py`
- Create: `tests/test_web_jobs.py`

**Interfaces:**
- Consumes: 없음.
- Produces:
  - 상태 상수 `QUEUED`, `RUNNING`, `SUCCEEDED`, `FAILED`, `CANCELLED`, `INTERRUPTED`, `FINAL_STATES`; `ENGINES = ("rag", "fact")`
  - `new_job_id(now: datetime | None = None, rand: str | None = None) -> str`, `is_job_id(value: str) -> bool`
  - `@dataclass Job(id, engine, requester="", client_id="", reference="", targets=[], state=QUEUED, created_ts=0.0, started_ts=0.0, finished_ts=0.0, error="", llm={})`, `is_final`, `to_dict()`, `Job.from_dict(data)`
  - `JobStore(root)`: `.root: Path`, `.dir(job_id) -> Path`(잘못된 ID 면 `ValueError`), `.save(job)`, `.load(job_id) -> Job | None`, `.list() -> list[Job]`(ID 오름차순), `.delete(job_id)`
  - `purge_expired(store, *, now: float, days: int) -> list[str]`

- [ ] **Step 1: 실패하는 테스트** — `tests/test_web_jobs.py`

```python
"""작업 모델·저장소 — 디스크의 job.json 이 상태의 원본이다."""

from __future__ import annotations

from datetime import datetime

import pytest

from contentcompare.web import jobs as J


def _job(job_id="20260929-140211-a3f9", **kw):
    return J.Job(id=job_id, engine="fact", **kw)


def test_job_id_format_and_validation():
    jid = J.new_job_id(datetime(2026, 9, 29, 14, 2, 11), rand="a3f9")
    assert jid == "20260929-140211-a3f9"
    assert J.is_job_id(jid)
    assert J.is_job_id(J.new_job_id())
    for bad in ("", "../x", "20260929-140211-A3F9", "20260929-140211-a3f9/..", "x" * 20):
        assert not J.is_job_id(bad)


def test_save_load_round_trip(tmp_path):
    store = J.JobStore(tmp_path)
    job = _job(requester="홍길동", targets=["targets/a.docx"], llm={"backend": "ollama"})
    store.save(job)
    loaded = store.load(job.id)
    assert loaded == job
    assert (tmp_path / job.id / "job.json").is_file()
    assert not (tmp_path / job.id / "job.json.tmp").exists()


def test_bad_id_never_touches_the_disk(tmp_path):
    store = J.JobStore(tmp_path)
    with pytest.raises(ValueError):
        store.dir("..\\..\\windows")
    assert store.load("../x") is None


def test_list_is_oldest_first_and_skips_junk(tmp_path):
    store = J.JobStore(tmp_path)
    store.save(_job("20260929-140300-0002"))
    store.save(_job("20260929-140200-0001"))
    (tmp_path / "잡동사니").mkdir()
    (tmp_path / "20260929-140400-0003").mkdir()          # job.json 없음
    broken = tmp_path / "20260929-140500-0004"
    broken.mkdir()
    (broken / "job.json").write_text("{깨짐", encoding="utf-8")
    assert [j.id for j in store.list()] == ["20260929-140200-0001", "20260929-140300-0002"]


def test_from_dict_ignores_unknown_keys():
    job = J.Job.from_dict({"id": "20260929-140211-a3f9", "engine": "rag", "미래필드": 1})
    assert job.engine == "rag" and job.state == J.QUEUED


def test_purge_expired_removes_only_old_finished_jobs(tmp_path):
    store = J.JobStore(tmp_path)
    day = 86400.0
    store.save(_job("20260901-000000-0001", state=J.SUCCEEDED, finished_ts=1 * day))
    store.save(_job("20260901-000000-0002", state=J.FAILED, finished_ts=9 * day))
    store.save(_job("20260901-000000-0003", state=J.QUEUED))
    store.save(_job("20260901-000000-0004", state=J.RUNNING, started_ts=1 * day))
    removed = J.purge_expired(store, now=10 * day, days=7)
    assert removed == ["20260901-000000-0001"]
    assert [j.id for j in store.list()] == [
        "20260901-000000-0002", "20260901-000000-0003", "20260901-000000-0004"]
    assert J.purge_expired(store, now=10 * day, days=0) == []
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_web_jobs.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'jobs' from 'contentcompare.web'`

- [ ] **Step 3: 구현** — `contentcompare/web/jobs.py`

```python
"""작업(Job) 모델과 디스크 저장소.

**디스크의 `job.json` 이 상태의 원본이다**(설계 §4). 서버 메모리는 그 사본일 뿐이라,
서버가 재시작돼도 대기열·결과가 그대로 남는다. 작업 ID 는 정규식으로만 받아 경로 조작을
원천 차단한다 — ID 가 곧 폴더 이름이기 때문이다.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

QUEUED = "queued"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
CANCELLED = "cancelled"
INTERRUPTED = "interrupted"
FINAL_STATES = (SUCCEEDED, FAILED, CANCELLED, INTERRUPTED)

ENGINES = ("rag", "fact")

_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")


def new_job_id(now: Optional[datetime] = None, rand: Optional[str] = None) -> str:
    """``YYYYMMDD-HHMMSS-xxxx`` — 정렬하면 접수 순서가 된다."""
    now = now or datetime.now()
    return f"{now:%Y%m%d-%H%M%S}-{rand or secrets.token_hex(2)}"


def is_job_id(value: str) -> bool:
    return bool(_ID.match(value or ""))


@dataclass
class Job:
    id: str
    engine: str
    requester: str = ""
    client_id: str = ""
    reference: str = ""
    """``inputs/`` 기준 상대 경로. 예: ``reference/기준.xlsx``."""
    targets: list[str] = field(default_factory=list)
    """``inputs/`` 기준 상대 경로. 예: ``targets/자료/규격서.docx``(폴더 구조 보존)."""
    state: str = QUEUED
    created_ts: float = 0.0
    started_ts: float = 0.0
    finished_ts: float = 0.0
    error: str = ""
    llm: dict = field(default_factory=dict)
    """:func:`~contentcompare.web.settings.public_llm_summary` — API 키 없음."""

    @property
    def is_final(self) -> bool:
        return self.state in FINAL_STATES

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Job":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})


class JobStore:
    def __init__(self, root: Union[str, Path]) -> None:
        self.root = Path(root)

    def dir(self, job_id: str) -> Path:
        if not is_job_id(job_id):
            raise ValueError(f"잘못된 작업 ID: {job_id!r}")
        return self.root / job_id

    def save(self, job: Job) -> None:
        """임시 파일에 쓴 뒤 교체한다 — 읽는 쪽이 반쯤 쓴 파일을 보지 않게."""
        d = self.dir(job.id)
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / "job.json.tmp"
        tmp.write_text(json.dumps(job.to_dict(), ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, d / "job.json")

    def load(self, job_id: str) -> Optional[Job]:
        try:
            path = self.dir(job_id) / "job.json"
        except ValueError:
            return None
        if not path.is_file():
            return None
        try:
            return Job.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            return None

    def list(self) -> list[Job]:
        if not self.root.is_dir():
            return []
        found = []
        for p in sorted(self.root.iterdir()):
            if p.is_dir() and is_job_id(p.name):
                job = self.load(p.name)
                if job is not None:
                    found.append(job)
        return found

    def delete(self, job_id: str) -> None:
        shutil.rmtree(self.dir(job_id), ignore_errors=True)


def purge_expired(store: JobStore, *, now: float, days: int) -> list[str]:
    """끝난 지 ``days`` 일이 지난 작업 폴더를 지운다(사내 문서 원본이 쌓이지 않게, 설계 §6 #8).

    대기·실행 중인 작업은 건드리지 않는다. ``days <= 0`` 이면 아무것도 지우지 않는다.
    """
    if days <= 0:
        return []
    cutoff = now - days * 86400
    removed = []
    for job in store.list():
        if job.is_final and job.finished_ts and job.finished_ts < cutoff:
            store.delete(job.id)
            removed.append(job.id)
    return removed
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_web_jobs.py -q -p no:cacheprovider`
Expected: 6 passed

- [ ] **Step 5: 커밋**

```bash
git add contentcompare/web/jobs.py tests/test_web_jobs.py
git commit -m "feat(web): 작업 모델과 디스크 저장소 — job.json 이 상태의 원본

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_015WPCPHZGMqNLc7QQAv1ako"
```

---

### Task 4: 업로드 검증과 저장

**Files:**
- Create: `contentcompare/web/uploads.py`
- Create: `tests/test_web_uploads.py`

**Interfaces:**
- Consumes: `contentcompare.ui.runner.SUPPORTED_EXTS`.
- Produces:
  - `REFERENCE_EXTS = (".xlsx", ".xls", ".xlsm")`
  - `class UploadError(ValueError)`
  - `safe_relpath(name: str) -> str` — `/` 구분 상대 경로
  - `@dataclass UploadPlan(reference: str, targets: list[str], skipped: list[str])`
  - `plan_upload(reference_name: str, target_names: list[str]) -> UploadPlan`
  - `save_stream(src: BinaryIO, dest: Path, max_bytes: int, *, chunk_size: int = 1 << 20) -> int`

- [ ] **Step 1: 실패하는 테스트** — `tests/test_web_uploads.py`

```python
"""업로드 — 경로 조작 차단, 폴더 구조 보존, 이름 충돌 거절, 크기 제한."""

from __future__ import annotations

import io

import pytest

from contentcompare.web import uploads as U


@pytest.mark.parametrize("raw, expected", [
    ("기준.xlsx", "기준.xlsx"),
    ("자료\\하위\\규격서 v2.docx", "자료/하위/규격서 v2.docx"),
    ("C:\\Users\\x\\a.docx", "Users/x/a.docx"),
    ("/abs/./b.pptx", "abs/b.pptx"),
])
def test_safe_relpath_normalizes(raw, expected):
    assert U.safe_relpath(raw) == expected


@pytest.mark.parametrize("raw", ["", "   /", "../a.docx", "a/../../b.docx"])
def test_safe_relpath_rejects(raw):
    with pytest.raises(U.UploadError):
        U.safe_relpath(raw)


def test_plan_keeps_folders_and_skips_lock_and_unsupported_files():
    plan = U.plan_upload("기준.xlsx", [
        "자료/규격서.docx", "자료/~$규격서.docx", "자료/메모.txt", "발표.pptx"])
    assert plan.reference == "기준.xlsx"
    assert plan.targets == ["자료/규격서.docx", "발표.pptx"]
    assert plan.skipped == ["자료/~$규격서.docx", "자료/메모.txt"]


def test_reference_must_be_excel():
    with pytest.raises(U.UploadError, match="Excel"):
        U.plan_upload("기준.docx", ["a.docx"])


def test_no_supported_target_is_an_error():
    with pytest.raises(U.UploadError, match="대상"):
        U.plan_upload("기준.xlsx", ["메모.txt"])


def test_same_basename_is_rejected_case_insensitive():
    """이름이 같으면 artifacts/<문서명>/ 이 겹쳐 결과가 조용히 섞인다."""
    with pytest.raises(U.UploadError, match="규격.docx"):
        U.plan_upload("기준.xlsx", ["a/규격.docx", "b/규격.DOCX"])
    with pytest.raises(U.UploadError, match="기준.xlsx"):
        U.plan_upload("기준.xlsx", ["사본/기준.xlsx"])


def test_save_stream_writes_and_counts(tmp_path):
    dest = tmp_path / "inputs" / "targets" / "자료" / "a.docx"
    n = U.save_stream(io.BytesIO(b"abc" * 1000), dest, max_bytes=10_000, chunk_size=128)
    assert n == 3000 and dest.read_bytes() == b"abc" * 1000


def test_save_stream_over_limit_removes_partial_file(tmp_path):
    dest = tmp_path / "big.xlsx"
    with pytest.raises(U.UploadError, match="큽니다"):
        U.save_stream(io.BytesIO(b"x" * 5000), dest, max_bytes=4096, chunk_size=1024)
    assert not dest.exists()
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_web_uploads.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'uploads'`

- [ ] **Step 3: 구현** — `contentcompare/web/uploads.py`

```python
"""업로드 이름 검증과 저장(설계 §6 #9).

- 경로 조작 차단: ``..`` 금지, 드라이브·선행 ``/`` 제거. 폴더 업로드의 **하위 경로는 보존**한다.
- Office 잠금 파일(``~$``)과 지원하지 않는 확장자는 **건너뛰고 알린다**(폴더째 올리면 섞여
  들어오는 것이 정상이라 거절하면 불편만 크다).
- **이름이 같은 문서는 거절한다.** 산출물 폴더가 문서 이름으로 정해져(``artifacts/<문서명>/``)
  같은 이름 둘이면 결과가 조용히 섞인다 — 실패보다 나쁘다. Windows 처럼 대소문자를 무시한다.
"""

from __future__ import annotations

import os
import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

from ..ui.runner import SUPPORTED_EXTS

REFERENCE_EXTS = (".xlsx", ".xls", ".xlsm")


class UploadError(ValueError):
    """사용자에게 그대로 보여 줄 수 있는 업로드 거절 사유."""


@dataclass
class UploadPlan:
    reference: str
    targets: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def safe_relpath(name: str) -> str:
    text = (name or "").replace("\\", "/")
    text = re.sub(r"^[A-Za-z]:", "", text.strip())
    parts = [p for p in text.split("/") if p not in ("", ".")]
    if not parts:
        raise UploadError(f"파일 이름이 비어 있습니다: {name!r}")
    if any(p == ".." for p in parts):
        raise UploadError(f"경로에 '..' 를 쓸 수 없습니다: {name!r}")
    return "/".join(parts)


def _ext(rel: str) -> str:
    return os.path.splitext(posixpath.basename(rel))[1].lower()


def plan_upload(reference_name: str, target_names: list[str]) -> UploadPlan:
    ref = safe_relpath(reference_name)
    ref_base = posixpath.basename(ref)
    if ref_base.startswith("~$"):
        raise UploadError(f"기준 문서가 Office 잠금 파일입니다: {ref_base}")
    if _ext(ref) not in REFERENCE_EXTS:
        raise UploadError(
            f"기준 문서는 Excel({', '.join(REFERENCE_EXTS)})이어야 합니다: {ref_base}")

    plan = UploadPlan(reference=ref)
    for raw in target_names:
        rel = safe_relpath(raw)
        if posixpath.basename(rel).startswith("~$") or _ext(rel) not in SUPPORTED_EXTS:
            plan.skipped.append(rel)
        else:
            plan.targets.append(rel)
    if not plan.targets:
        raise UploadError(f"대상 문서가 없습니다(지원 확장자: {', '.join(SUPPORTED_EXTS)}).")

    seen = {ref_base.lower(): ref}
    clashes = []
    for rel in plan.targets:
        key = posixpath.basename(rel).lower()
        if key in seen:
            clashes.append(f"{seen[key]} ↔ {rel}")
        else:
            seen[key] = rel
    if clashes:
        raise UploadError(
            "같은 이름의 문서가 있습니다 — 산출물 폴더가 겹쳐 결과가 섞이므로 이름을 바꿔 "
            "다시 올리세요: " + "; ".join(clashes))
    return plan


def save_stream(src: BinaryIO, dest: Path, max_bytes: int, *,
                chunk_size: int = 1 << 20) -> int:
    """``src`` 를 ``dest`` 로 복사한다. 한도를 넘으면 쓰던 파일을 지우고 :class:`UploadError`."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    try:
        with open(dest, "wb") as out:
            while True:
                chunk = src.read(chunk_size)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise UploadError(
                        f"파일이 너무 큽니다(한도 {max_bytes / (1024 * 1024):.0f}MB): {dest.name}")
                out.write(chunk)
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    return total
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_web_uploads.py -q -p no:cacheprovider`
Expected: 14 passed

- [ ] **Step 5: 커밋**

```bash
git add contentcompare/web/uploads.py tests/test_web_uploads.py
git commit -m "feat(web): 업로드 검증 — 경로 조작 차단, 폴더 보존, 같은 이름 거절

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_015WPCPHZGMqNLc7QQAv1ako"
```

---

### Task 5: 결과 → 화면용 JSON

**Files:**
- Create: `contentcompare/web/results.py`
- Create: `tests/test_web_results.py`

**Interfaces:**
- Consumes: `ui.runner.verdict_counts`, `summary_rows`, `field_rows`, `VERDICT_LABEL`, `fact_verdict_counts`, `fact_summary_rows`, `FACT_LABEL`; `report.render_markdown`.
- Produces:
  - `rag_payload(results: list) -> dict` — 키 `engine="rag"`, `counts: [{key,label,n}]`, `summary: list[dict]`, `details: [{index,label,verdict,source_label,reference_text,is_record,sources,reasoning,fields,candidates:[{score,source_label,matched}]}]`
  - `fact_payload(result: FactRunResult) -> dict` — 키 `engine="fact"`, `counts`, `summary`, `failed_docs: [{name,error}]`, `compare_stats: dict`
  - `rag_markdown(results, reference: str, targets: list[str]) -> str`

- [ ] **Step 1: 실패하는 테스트** — `tests/test_web_results.py`

```python
"""결과 JSON — 두 엔진의 라벨을 섞지 않고 JSON 으로 직렬화된다."""

from __future__ import annotations

import json

from contentcompare.fact.pipeline import FactRunResult
from contentcompare.models import (
    Candidate, ComparisonResult, DocItem, DocType, FieldClaim, FieldFinding,
    RecordItem, RecordResult, Verdict,
)
from contentcompare.web import results as R


def _item(item_id, text="내용"):
    return DocItem(item_id=item_id, doc_id="d", doc_type=DocType.WORD, text=text,
                   source_label=f"라벨 {item_id}")


def test_rag_payload_lists_counts_details_and_candidates():
    simple = ComparisonResult(
        reference=_item("ref-1", "매출 100억"), verdict=Verdict.SAME, reasoning="같음",
        candidates=[Candidate(_item("t-1"), 0.91234), Candidate(_item("t-2"), 0.5)],
        matched_item_ids=["t-1"])
    claim = FieldClaim(field_id="f1", header="하한치", value_raw=-5, value_norm="-5",
                       cell_ref="F2")
    record = RecordResult(
        record=RecordItem(item_id="row-2", doc_id="d", doc_type=DocType.EXCEL,
                          text="충전온도", source_label="기준 2행", fields=[claim]),
        verdict=Verdict.DIFFERENT, reasoning="다름",
        findings=[FieldFinding(field=claim, found=False, note="없음")])
    payload = R.rag_payload([simple, record])
    json.dumps(payload, ensure_ascii=False)
    counts = {c["key"]: c["n"] for c in payload["counts"]}
    assert counts["same"] == 1 and counts["different"] == 1
    first, second = payload["details"]
    assert first["label"] == "✅ 같음" and not first["is_record"]
    assert first["candidates"] == [
        {"score": 0.912, "source_label": "라벨 t-1", "matched": True},
        {"score": 0.5, "source_label": "라벨 t-2", "matched": False}]
    assert first["sources"] == ["라벨 t-1"]
    assert second["is_record"] and second["fields"][0]["항목(열)"] == "하한치"
    assert len(payload["summary"]) == 2


def test_fact_payload_uses_fact_labels_and_failed_docs():
    result = FactRunResult(
        summaries=[{"path": "C:/x/기준.xlsx", "status": "ok"},
                   {"path": "C:/x/깨진.docx", "status": "error", "error": "OSError: 열 수 없음"}],
        comparisons=[{"result": "match", "entity_name": "충전온도", "target_doc": "a.docx",
                      "reason": "일치"},
                     {"result": "missing", "entity_name": "보관온도", "target_doc": "a.docx",
                      "reason": "없음"}],
        compare_stats={"comparisons": 2, "llm": {"calls": 0}},
        markdown="# 리포트")
    payload = R.fact_payload(result)
    json.dumps(payload, ensure_ascii=False)
    assert payload["engine"] == "fact"
    counts = {c["key"]: c["n"] for c in payload["counts"]}
    assert counts["match"] == 1 and counts["missing"] == 1
    assert payload["failed_docs"] == [{"name": "깨진.docx", "error": "OSError: 열 수 없음"}]
    assert payload["summary"][0]["판정"]  # 리포트와 같은 라벨(단일 출처)
    assert payload["compare_stats"]["comparisons"] == 2


def test_rag_markdown_uses_basenames():
    md = R.rag_markdown([], "C:/jobs/x/inputs/reference/기준.xlsx",
                        ["C:/jobs/x/inputs/targets/자료/a.docx"])
    assert "기준.xlsx" in md and "C:/jobs" not in md
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_web_results.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'results'`

- [ ] **Step 3: 구현** — `contentcompare/web/results.py`

```python
"""파이프라인 결과 → 화면용 JSON.

표를 만드는 로직은 :mod:`contentcompare.ui.runner` 를 그대로 쓴다(UI 3층 분리 — 화면이
바뀌어도 집계는 한 곳). 판정 라벨은 RAG=``runner.VERDICT_LABEL``, fact=``runner.FACT_LABEL``
이며 **섞지 않는다** — 같은 이모지, 다른 뜻이다.
"""

from __future__ import annotations

import json
import os
from typing import Any

from ..models import RecordResult, Verdict
from ..report import render_markdown
from ..ui import runner


def rag_payload(results: list) -> dict:
    counts = runner.verdict_counts(results)
    details = []
    for i, r in enumerate(results, start=1):
        is_record = isinstance(r, RecordResult)
        details.append({
            "index": i,
            "label": runner.VERDICT_LABEL[r.verdict],
            "verdict": r.verdict.value,
            "source_label": r.reference.source_label,
            "reference_text": r.reference.text,
            "is_record": is_record,
            "sources": list(r.sources),
            "reasoning": r.reasoning,
            "fields": runner.field_rows(r) if is_record else [],
            "candidates": [
                {"score": round(float(c.score), 3),
                 "source_label": c.item.source_label,
                 "matched": c.item.item_id in r.matched_item_ids}
                for c in r.candidates
            ],
        })
    return _jsonable({
        "engine": "rag",
        "counts": [{"key": v.value, "label": runner.VERDICT_LABEL[v], "n": counts[v]}
                   for v in Verdict],
        "summary": runner.summary_rows(results),
        "details": details,
    })


def fact_payload(result: Any) -> dict:
    counts = runner.fact_verdict_counts(result.comparisons)
    return _jsonable({
        "engine": "fact",
        "counts": [{"key": k, "label": runner.FACT_LABEL.get(k, k), "n": n}
                   for k, n in counts.items()],
        "summary": runner.fact_summary_rows(result.comparisons),
        "failed_docs": [
            {"name": os.path.basename(str(s.get("path", ""))), "error": str(s.get("error", ""))}
            for s in result.failed_docs
        ],
        "compare_stats": result.compare_stats or {},
    })


def rag_markdown(results: list, reference: str, targets: list[str]) -> str:
    """RAG 리포트. 경로는 파일 이름만 — 서버의 작업 폴더 경로를 화면에 흘리지 않는다."""
    return render_markdown(
        results,
        reference_doc=os.path.basename(reference),
        target_docs=[os.path.basename(t) for t in targets],
    )


def _jsonable(obj: Any) -> Any:
    """JSON 으로 못 바꾸는 값(enum·Path 등)은 문자열로 — 응답이 직렬화에서 죽지 않게."""
    return json.loads(json.dumps(obj, ensure_ascii=False, default=str))
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_web_results.py -q -p no:cacheprovider`
Expected: 3 passed

- [ ] **Step 5: 커밋**

```bash
git add contentcompare/web/results.py tests/test_web_results.py
git commit -m "feat(web): 결과 JSON — 집계는 ui.runner 를 그대로, 두 엔진 라벨은 섞지 않는다

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_015WPCPHZGMqNLc7QQAv1ako"
```

---

### Task 6: worker — 작업 1건 실행

**Files:**
- Create: `contentcompare/web/worker.py`
- Create: `tests/test_web_worker.py`

**Interfaces:**
- Consumes: Task 2 `WebSettings`, `load_settings`, `build_app_config`; Task 3 `Job`; Task 5 `rag_payload`, `fact_payload`, `rag_markdown`; `fact.engine.make_pipeline`; `logging_setup.setup_console/setup_logging/apply_logger_overrides/log_print`; `timeline.start_timeline`; `llm.tracing.get_tracer/trace_run/run_metadata`; 계획 1 `prog.JsonlProgress/set_reporter/reset_reporter`.
- Produces:
  - `FACTORY_ENV = "CC_PIPELINE_FACTORY"` — `"모듈:이름"` 형식의 파이프라인 팩토리(통합 테스트용 주입구)
  - `DOTENV_ENV = "CC_DOTENV"` — 서버가 쓰는 `.env` 경로를 worker 에 물려주는 환경변수
  - `run_job(job_dir: Path, settings: WebSettings, *, factory=None) -> int` — 0 성공, 1 실패
  - `main(argv: list[str] | None = None) -> int`
  - 작업 폴더에 남기는 파일: `result.json`, `report.md`, `error.json`(`{"error": str}`), `progress.jsonl`, `contentcompare_*.log`, `artifacts/`(`_timeline/timeline.jsonl` 포함)

- [ ] **Step 1: 실패하는 테스트** — `tests/test_web_worker.py`

```python
"""worker — 파이프라인을 CLI 처럼 부르고 결과를 작업 폴더에 파일로 남긴다.

로깅·타임라인은 프로세스 전역이라 여기서는 가짜로 막는다(실제 프로세스 격리는
test_web_launcher.py 의 통합 테스트가 본다).
"""

from __future__ import annotations

import json

import pytest

from contentcompare import progress as prog
from contentcompare import timeline
from contentcompare.fact.pipeline import FactRunResult
from contentcompare.web import worker
from contentcompare.web.jobs import Job, JobStore
from contentcompare.web.settings import WebSettings


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(worker, "setup_console", lambda **kw: None)
    monkeypatch.setattr(worker, "setup_logging", lambda **kw: "")
    yield
    timeline.reset_timeline()
    prog.reset_reporter()


def _job_dir(tmp_path, engine="fact"):
    store = JobStore(tmp_path / "jobs")
    job = Job(id="20260929-140211-a3f9", engine=engine,
              reference="reference/기준.xlsx", targets=["targets/자료/a.docx"])
    store.save(job)
    return store.dir(job.id)


class _FakeFact:
    def __init__(self, markdown="# 리포트"):
        self.markdown = markdown
        self.seen = None

    def run(self, reference, targets):
        self.seen = (reference, targets)
        prog.plan([prog.Unit("doc:0", "기준.xlsx", prog.DOC)])
        return FactRunResult(
            summaries=[{"path": reference, "status": "ok"}],
            comparisons=[{"result": "match", "entity_name": "충전온도",
                          "target_doc": "a.docx", "reason": "일치"}],
            markdown=self.markdown)


def test_fact_job_writes_result_report_and_progress(tmp_path):
    job_dir = _job_dir(tmp_path)
    fake = _FakeFact()
    code = worker.run_job(job_dir, WebSettings(), factory=lambda config, engine: fake)
    assert code == 0
    ref, targets = fake.seen
    assert ref.endswith("inputs\\reference\\기준.xlsx") or ref.endswith("inputs/reference/기준.xlsx")
    assert targets[0].replace("\\", "/").endswith("inputs/targets/자료/a.docx")
    payload = json.loads((job_dir / "result.json").read_text(encoding="utf-8"))
    assert payload["engine"] == "fact" and payload["summary"]
    assert (job_dir / "report.md").read_text(encoding="utf-8") == "# 리포트"
    assert prog.load_events(job_dir / "progress.jsonl")[0]["ev"] == "plan"
    assert not (job_dir / "error.json").exists()


def test_worker_points_artifacts_into_the_job_folder(tmp_path):
    job_dir = _job_dir(tmp_path)
    seen = {}

    def factory(config, engine):
        seen["artifacts"] = config.fact.artifacts_dir
        seen["report"] = config.report.output_dir
        seen["engine"] = engine
        return _FakeFact()

    worker.run_job(job_dir, WebSettings(), factory=factory)
    assert seen == {"artifacts": str(job_dir / "artifacts"), "report": str(job_dir),
                    "engine": "fact"}


def test_fact_without_report_is_a_failure(tmp_path):
    job_dir = _job_dir(tmp_path)
    code = worker.run_job(job_dir, WebSettings(),
                          factory=lambda config, engine: _FakeFact(markdown=""))
    assert code == 1
    assert "리포트" in json.loads((job_dir / "error.json").read_text(encoding="utf-8"))["error"]
    assert (job_dir / "result.json").is_file()   # 실패한 문서 목록은 보여 줘야 한다


def test_pipeline_exception_is_recorded(tmp_path):
    job_dir = _job_dir(tmp_path)

    def boom(config, engine):
        raise RuntimeError("설정 폭발")

    assert worker.run_job(job_dir, WebSettings(), factory=boom) == 1
    error = json.loads((job_dir / "error.json").read_text(encoding="utf-8"))["error"]
    assert error == "RuntimeError: 설정 폭발"


def test_rag_job_renders_markdown(tmp_path):
    job_dir = _job_dir(tmp_path, engine="rag")

    class _FakeRag:
        def run(self, reference, targets):
            return []

    assert worker.run_job(job_dir, WebSettings(), factory=lambda c, e: _FakeRag()) == 0
    assert json.loads((job_dir / "result.json").read_text(encoding="utf-8"))["engine"] == "rag"
    assert "기준.xlsx" in (job_dir / "report.md").read_text(encoding="utf-8")


def test_main_requires_exactly_one_argument():
    assert worker.main([]) == 2
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_web_worker.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'worker'`

- [ ] **Step 3: 구현** — `contentcompare/web/worker.py`

```python
"""작업 하나를 실행하는 별도 프로세스 — ``python -m contentcompare.web.worker <작업폴더>``.

- **파이프라인은 웹을 모른다**(설계 §4.1). 여기서도 CLI 처럼 ``make_pipeline().run()`` 만 부른다.
- **프로세스가 작업 단위다.** 타임라인·로그 파일·``disable_proxy()``·Office COM 이 모두
  프로세스 전역이라, 작업마다 새 프로세스를 띄워 서로를 가둔다.
- 화면용 로그: ``log_print``(stdout)와 콘솔 핸들러(stderr, INFO)를 서버가 ``console.log`` 로
  받는다. 프롬프트·원문은 DEBUG 라 작업 폴더의 ``contentcompare_*.log`` 에만 남는다.
- **API 키를 작업 폴더에 남기지 않는다.** 서버가 설정을 파일로 넘기지 않고 worker 가
  `.env` 를 스스로 읽는다.
- 성패는 종료코드로 알린다(0 성공, 1 실패). 실패 사유는 ``error.json`` 한 줄.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Callable, Optional

from .. import progress as prog
from ..fact.engine import make_pipeline
from ..llm.tracing import get_tracer, run_metadata, trace_run
from ..logging_setup import apply_logger_overrides, log_print, setup_console, setup_logging
from ..timeline import start_timeline
from . import results
from .jobs import Job
from .settings import WebSettings, build_app_config, load_settings

FACTORY_ENV = "CC_PIPELINE_FACTORY"
DOTENV_ENV = "CC_DOTENV"
"""서버가 쓰는 `.env` 경로. 서버가 설정하고 worker 가 물려받는다(서버 ``--env`` 와 일치시키려고)."""

logger = logging.getLogger(__name__)


def run_job(job_dir: Path, settings: WebSettings, *,
            factory: Optional[Callable[[Any, str], Any]] = None) -> int:
    job_dir = Path(job_dir)
    job = Job.from_dict(json.loads((job_dir / "job.json").read_text(encoding="utf-8")))

    config = build_app_config(settings)
    # 산출물·추적·리포트를 작업 폴더로 돌린다 — 같은 이름 문서를 두 사람이 올려도 안 섞인다.
    config.fact.artifacts_dir = str(job_dir / "artifacts")
    config.llm.trace_dir = str(job_dir / "artifacts" / "_traces")
    config.report.output_dir = str(job_dir)

    setup_console(level=logging.INFO)
    setup_logging(log_dir=str(job_dir), force_new=True)
    apply_logger_overrides(config.logging.quiet_extra, config.logging.verbose_extra)
    start_timeline(config, console=False, label="timeline")
    prog.set_reporter(prog.JsonlProgress(job_dir / "progress.jsonl"))

    reference = str(job_dir / "inputs" / job.reference)
    targets = [str(job_dir / "inputs" / t) for t in job.targets]
    log_print(f"작업 {job.id} 시작 · 엔진 {job.engine} · 대상 {len(targets)}개 · "
              f"모델 {config.llm.chat_model}")
    try:
        pipeline = (factory or make_pipeline)(config, job.engine)
        tracer = get_tracer(config)
        with trace_run(tracer, f"contentcompare {job.engine} (web)",
                       run_metadata(config, job.engine, reference, targets)):
            outcome = pipeline.run(reference, targets)
        if job.engine == "fact":
            payload = results.fact_payload(outcome)
            markdown = outcome.markdown
        else:
            payload = results.rag_payload(outcome)
            markdown = results.rag_markdown(outcome, reference, targets)
        _write_json(job_dir / "result.json", payload)
        if not markdown:
            message = "비교할 fact 가 부족해 리포트를 만들지 못했습니다(실패한 문서를 확인하세요)."
            _write_json(job_dir / "error.json", {"error": message})
            log_print(message)
            return 1
        (job_dir / "report.md").write_text(markdown, encoding="utf-8")
        log_print(f"완료: 판정 {len(payload.get('summary', []))}건")
        return 0
    except Exception as exc:  # noqa: BLE001 — 사유를 남기고 종료코드로 알린다
        logger.exception("작업 실패")
        _write_json(job_dir / "error.json", {"error": f"{type(exc).__name__}: {exc}"})
        log_print(f"실패: {type(exc).__name__}: {exc}", level=logging.ERROR)
        return 1
    finally:
        prog.reset_reporter()


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _factory_from_env() -> Optional[Callable[[Any, str], Any]]:
    spec = os.environ.get(FACTORY_ENV, "").strip()
    if not spec:
        return None
    module, _, attr = spec.partition(":")
    return getattr(importlib.import_module(module), attr)


def main(argv: Optional[list[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("사용법: python -m contentcompare.web.worker <작업폴더>", file=sys.stderr)
        return 2
    # 서버가 --env 로 다른 파일을 쓰면 그 경로를 CC_DOTENV 로 물려준다(__main__ 참고).
    settings = load_settings(os.environ.get(DOTENV_ENV, ".env"))
    return run_job(Path(args[0]), settings, factory=_factory_from_env())


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_web_worker.py -q -p no:cacheprovider`
Expected: 6 passed

- [ ] **Step 5: 커밋**

```bash
git add contentcompare/web/worker.py tests/test_web_worker.py
git commit -m "feat(web): worker — 작업 1건을 별도 프로세스에서, 결과는 파일로

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_015WPCPHZGMqNLc7QQAv1ako"
```

---

### Task 7: 스케줄러 — 대기열 1건 실행·취소·재시작 복구

**Files:**
- Create: `contentcompare/web/scheduler.py`
- Create: `tests/test_web_scheduler.py`

**Interfaces:**
- Consumes: Task 3 `Job`, `JobStore`, 상태 상수.
- Produces:
  - `class Handle(Protocol)`: `poll() -> int | None`, `terminate() -> None`
  - `Launcher = Callable[[Job, Path], Handle]`
  - `class OfficeGuard(Protocol)`: `snapshot() -> set[int]`, `cleanup(before: set[int]) -> list[int]`
  - `class NullOfficeGuard`
  - `JobScheduler(store, launcher, *, office=None, clock=time.time, poll_interval=0.5, housekeeping=None, housekeeping_interval=3600.0)`:
    `.recover() -> list[str]`, `.submit(job) -> Job`, `.tick() -> None`, `.cancel(job_id) -> bool`, `.queued() -> list[Job]`, `.positions() -> dict[str, int]`, `.running_id -> str | None`, `.start()`, `.stop()`

- [ ] **Step 1: 실패하는 테스트** — `tests/test_web_scheduler.py`

```python
"""스케줄러 — 한 번에 1건, 접수 순서, 취소, 재시작 복구."""

from __future__ import annotations

import json
import time

from contentcompare.web import jobs as J
from contentcompare.web.scheduler import JobScheduler


class _Handle:
    def __init__(self):
        self.code = None
        self.terminated = False

    def poll(self):
        return self.code

    def terminate(self):
        self.terminated = True
        self.code = -1


class _Launcher:
    def __init__(self):
        self.handles: dict[str, _Handle] = {}

    def __call__(self, job, job_dir):
        handle = _Handle()
        self.handles[job.id] = handle
        return handle


class _Office:
    def __init__(self):
        self.snapshots = 0
        self.cleaned: list[set] = []

    def snapshot(self):
        self.snapshots += 1
        return {100}

    def cleanup(self, before):
        self.cleaned.append(set(before))
        return []


def _setup(tmp_path, clock=lambda: 1000.0):
    store = J.JobStore(tmp_path)
    launcher = _Launcher()
    office = _Office()
    sched = JobScheduler(store, launcher, office=office, clock=clock)
    return store, launcher, office, sched


def _submit(sched, n):
    job = J.Job(id=f"20260929-1402{n:02d}-000{n}", engine="fact")
    return sched.submit(job)


def test_one_at_a_time_in_submission_order(tmp_path):
    store, launcher, _, sched = _setup(tmp_path)
    a, b = _submit(sched, 1), _submit(sched, 2)
    sched.tick()
    assert list(launcher.handles) == [a.id]
    assert store.load(a.id).state == J.RUNNING
    assert sched.positions() == {b.id: 1}
    sched.tick()                                   # 아직 안 끝남 → 다음 것을 띄우지 않는다
    assert list(launcher.handles) == [a.id]


def test_success_starts_the_next_job_in_the_same_tick(tmp_path):
    store, launcher, office, sched = _setup(tmp_path)
    a, b = _submit(sched, 1), _submit(sched, 2)
    sched.tick()
    launcher.handles[a.id].code = 0
    sched.tick()
    assert store.load(a.id).state == J.SUCCEEDED
    assert store.load(b.id).state == J.RUNNING
    assert office.cleaned == []                    # 정상 종료면 Office 를 건드리지 않는다


def test_failure_reads_error_json_and_cleans_office(tmp_path):
    store, launcher, office, sched = _setup(tmp_path)
    a = _submit(sched, 1)
    sched.tick()
    (store.dir(a.id) / "error.json").write_text(
        json.dumps({"error": "ValueError: 파싱 실패"}), encoding="utf-8")
    launcher.handles[a.id].code = 1
    sched.tick()
    job = store.load(a.id)
    assert (job.state, job.error) == (J.FAILED, "ValueError: 파싱 실패")
    assert office.cleaned == [{100}]


def test_failure_without_error_json_reports_exit_code(tmp_path):
    store, launcher, _, sched = _setup(tmp_path)
    a = _submit(sched, 1)
    sched.tick()
    launcher.handles[a.id].code = 3
    sched.tick()
    assert store.load(a.id).error == "worker 종료코드 3"


def test_cancel_queued_never_launches(tmp_path):
    store, launcher, _, sched = _setup(tmp_path)
    a, b = _submit(sched, 1), _submit(sched, 2)
    assert sched.cancel(b.id)
    sched.tick()
    launcher.handles[a.id].code = 0
    sched.tick()
    assert store.load(b.id).state == J.CANCELLED
    assert list(launcher.handles) == [a.id]


def test_cancel_running_terminates_cleans_and_moves_on(tmp_path):
    store, launcher, office, sched = _setup(tmp_path)
    a, b = _submit(sched, 1), _submit(sched, 2)
    sched.tick()
    assert sched.cancel(a.id)
    assert launcher.handles[a.id].terminated
    assert store.load(a.id).state == J.CANCELLED
    assert office.cleaned == [{100}]
    sched.tick()
    assert store.load(b.id).state == J.RUNNING


def test_cancel_finished_is_refused(tmp_path):
    store, launcher, _, sched = _setup(tmp_path)
    a = _submit(sched, 1)
    sched.tick()
    launcher.handles[a.id].code = 0
    sched.tick()
    assert not sched.cancel(a.id)
    assert not sched.cancel("20260929-000000-ffff")


def test_recover_marks_running_interrupted_and_keeps_queue_order(tmp_path):
    store = J.JobStore(tmp_path)
    store.save(J.Job(id="20260929-140201-0001", engine="fact", state=J.RUNNING))
    store.save(J.Job(id="20260929-140203-0003", engine="fact", state=J.QUEUED, created_ts=3))
    store.save(J.Job(id="20260929-140202-0002", engine="fact", state=J.QUEUED, created_ts=2))
    launcher = _Launcher()
    sched = JobScheduler(store, launcher, office=_Office(), clock=lambda: 50.0)
    assert sched.recover() == ["20260929-140201-0001"]
    job = store.load("20260929-140201-0001")
    assert job.state == J.INTERRUPTED and "재시작" in job.error and job.finished_ts == 50.0
    sched.tick()
    assert list(launcher.handles) == ["20260929-140202-0002"]


def test_launcher_error_fails_the_job_and_keeps_going(tmp_path):
    store = J.JobStore(tmp_path)
    calls = []

    def launcher(job, job_dir):
        calls.append(job.id)
        if len(calls) == 1:
            raise OSError("python 을 찾을 수 없음")
        return _Handle()

    sched = JobScheduler(store, launcher, office=_Office(), clock=lambda: 1.0)
    a, b = _submit(sched, 1), _submit(sched, 2)
    sched.tick()
    assert store.load(a.id).state == J.FAILED
    assert "OSError" in store.load(a.id).error
    sched.tick()
    assert store.load(b.id).state == J.RUNNING


def test_stop_interrupts_the_running_job(tmp_path):
    store, launcher, _, sched = _setup(tmp_path)
    a = _submit(sched, 1)
    sched.tick()
    sched.stop()
    assert launcher.handles[a.id].terminated
    assert store.load(a.id).state == J.INTERRUPTED


def test_background_thread_runs_jobs_and_housekeeping(tmp_path):
    store = J.JobStore(tmp_path)
    launcher = _Launcher()
    swept = []
    sched = JobScheduler(store, launcher, office=_Office(), poll_interval=0.01,
                         housekeeping=lambda: swept.append(1), housekeeping_interval=0.0)
    a = _submit(sched, 1)
    sched.start()
    try:
        deadline = time.time() + 5
        while a.id not in launcher.handles and time.time() < deadline:
            time.sleep(0.01)
        launcher.handles[a.id].code = 0
        while store.load(a.id).state != J.SUCCEEDED and time.time() < deadline:
            time.sleep(0.01)
    finally:
        sched.stop()
    assert store.load(a.id).state == J.SUCCEEDED
    assert swept
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_web_scheduler.py -q -p no:cacheprovider`
Expected: FAIL — `ModuleNotFoundError: No module named 'contentcompare.web.scheduler'`

- [ ] **Step 3: 구현** — `contentcompare/web/scheduler.py`

```python
"""작업 대기열 — **한 번에 1건**(설계 §2·§6).

동시 실행이 1건인 이유는 둘이다. PowerPoint COM 은 ``DispatchEx`` 로도 PC 전체에 하나뿐이라
한 작업의 ``Quit`` 이 남의 PPT 를 닫고, LLM 요청 한도는 키 하나를 공유하는데 ``RateLimiter``
는 프로세스 안에만 있다. 1건이면 두 문제가 모두 사라진다.

상태의 원본은 디스크(``job.json``)다. 여기서 들고 있는 것은 "지금 도는 프로세스 핸들" 하나뿐이다.
:meth:`tick` 은 한 번의 결정적 스케줄링 단계라 테스트가 스레드 없이 부를 수 있다.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Callable, Optional, Protocol

from .jobs import CANCELLED, FAILED, INTERRUPTED, QUEUED, RUNNING, SUCCEEDED, Job, JobStore

logger = logging.getLogger(__name__)


class Handle(Protocol):
    def poll(self) -> Optional[int]: ...
    def terminate(self) -> None: ...


Launcher = Callable[[Job, Path], Handle]


class OfficeGuard(Protocol):
    def snapshot(self) -> set[int]: ...
    def cleanup(self, before: set[int]) -> list[int]: ...


class NullOfficeGuard:
    """Office 정리를 하지 않는다(Windows 가 아닌 곳·테스트)."""

    def snapshot(self) -> set[int]:
        return set()

    def cleanup(self, before: set[int]) -> list[int]:
        return []


class JobScheduler:
    def __init__(
        self,
        store: JobStore,
        launcher: Launcher,
        *,
        office: Optional[OfficeGuard] = None,
        clock: Callable[[], float] = time.time,
        poll_interval: float = 0.5,
        housekeeping: Optional[Callable[[], object]] = None,
        housekeeping_interval: float = 3600.0,
    ) -> None:
        self.store = store
        self.launcher = launcher
        self.office = office or NullOfficeGuard()
        self.clock = clock
        self.poll_interval = poll_interval
        self.housekeeping = housekeeping
        self.housekeeping_interval = housekeeping_interval
        self._lock = threading.RLock()
        self._running: Optional[tuple[str, Handle, set[int]]] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._last_housekeeping = float("-inf")

    # ------------------------------------------------------------------ #
    @property
    def running_id(self) -> Optional[str]:
        with self._lock:
            return self._running[0] if self._running else None

    def queued(self) -> list[Job]:
        jobs = [j for j in self.store.list() if j.state == QUEUED]
        return sorted(jobs, key=lambda j: (j.created_ts, j.id))

    def positions(self) -> dict[str, int]:
        """대기 작업 → 앞에 있는 작업 수(실행 중인 것 포함). 화면의 '앞에 N건'."""
        with self._lock:
            ahead = 1 if self._running else 0
            return {j.id: ahead + i for i, j in enumerate(self.queued())}

    # ------------------------------------------------------------------ #
    def recover(self) -> list[str]:
        """서버 재시작 직후: 실행 중이던 작업은 ``interrupted``. **다시 돌리지 않는다** — LLM 비용."""
        changed = []
        with self._lock:
            for job in self.store.list():
                if job.state == RUNNING:
                    job.state = INTERRUPTED
                    job.error = job.error or "서버가 재시작되어 중단되었습니다."
                    job.finished_ts = self.clock()
                    self.store.save(job)
                    changed.append(job.id)
        return changed

    def submit(self, job: Job) -> Job:
        with self._lock:
            job.state = QUEUED
            job.created_ts = job.created_ts or self.clock()
            self.store.save(job)
        return job

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self.store.load(job_id)
            if job is None or job.is_final:
                return False
            if job.state == QUEUED:
                self._close(job, CANCELLED, "사용자가 취소했습니다.")
                return True
            if self._running and self._running[0] == job_id:
                _, handle, before = self._running
                self._running = None
                handle.terminate()
                self.office.cleanup(before)
                self._close(job, CANCELLED, "사용자가 취소했습니다.")
                return True
            return False

    def tick(self) -> None:
        with self._lock:
            if self._running:
                job_id, handle, before = self._running
                code = handle.poll()
                if code is None:
                    return
                self._running = None
                self._finalize(job_id, code, before)
            waiting = self.queued()
            if not waiting:
                return
            job = waiting[0]
            before = self.office.snapshot()
            job.state = RUNNING
            job.started_ts = self.clock()
            self.store.save(job)
            try:
                handle = self.launcher(job, self.store.dir(job.id))
            except Exception as exc:  # noqa: BLE001 — 한 작업의 기동 실패가 대기열을 막으면 안 된다
                logger.exception("작업 기동 실패: %s", job.id)
                self._close(job, FAILED, f"작업을 시작하지 못했습니다: {type(exc).__name__}: {exc}")
                return
            self._running = (job.id, handle, before)
            logger.info("작업 시작: %s (%s)", job.id, job.engine)

    # ------------------------------------------------------------------ #
    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="job-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """스레드를 멈추고, 실행 중인 작업은 종료해 ``interrupted`` 로 남긴다."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None
        with self._lock:
            if self._running:
                job_id, handle, before = self._running
                self._running = None
                handle.terminate()
                self.office.cleanup(before)
                job = self.store.load(job_id)
                if job is not None:
                    self._close(job, INTERRUPTED, "서버가 종료되어 중단되었습니다.")

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
                now = self.clock()
                if self.housekeeping and now - self._last_housekeeping >= self.housekeeping_interval:
                    self._last_housekeeping = now
                    self.housekeeping()
            except Exception:  # noqa: BLE001 — 스케줄러 스레드는 죽으면 안 된다
                logger.exception("스케줄러 오류(계속 진행)")
            self._stop.wait(self.poll_interval)

    # ------------------------------------------------------------------ #
    def _finalize(self, job_id: str, code: int, before: set[int]) -> None:
        job = self.store.load(job_id)
        if job is None:
            return
        if code == 0:
            self._close(job, SUCCEEDED, "")
            logger.info("작업 완료: %s", job_id)
            return
        # 비정상 종료면 Office 가 남았을 수 있다(정상 종료는 파이프라인이 finally 에서 닫는다).
        self.office.cleanup(before)
        error = _read_error(self.store.dir(job_id)) or f"worker 종료코드 {code}"
        self._close(job, FAILED, error)
        logger.warning("작업 실패: %s — %s", job_id, error)

    def _close(self, job: Job, state: str, error: str) -> None:
        job.state = state
        job.error = error
        job.finished_ts = self.clock()
        self.store.save(job)


def _read_error(job_dir: Path) -> str:
    try:
        return str(json.loads((job_dir / "error.json").read_text(encoding="utf-8")).get("error") or "")
    except (OSError, ValueError, AttributeError):
        return ""
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_web_scheduler.py -q -p no:cacheprovider`
Expected: 11 passed

- [ ] **Step 5: 커밋**

```bash
git add contentcompare/web/scheduler.py tests/test_web_scheduler.py
git commit -m "feat(web): 작업 스케줄러 — 한 번에 1건, 취소, 재시작 시 interrupted

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_015WPCPHZGMqNLc7QQAv1ako"
```

---

### Task 8: worker 프로세스 실행과 Office 정리

**Files:**
- Create: `contentcompare/web/launcher.py`
- Create: `tests/web_fake_factory.py`
- Create: `tests/test_web_launcher.py`

**Interfaces:**
- Consumes: Task 3 `Job`; Task 6 `worker`(프로세스로), `FACTORY_ENV`; Task 7 `NullOfficeGuard`.
- Produces:
  - `OFFICE_IMAGES = ("EXCEL.EXE", "WINWORD.EXE", "POWERPNT.EXE")`
  - `ProcessHandle(proc, log_file)`: `poll()`, `terminate()`, `pid`
  - `process_launcher(python: str = sys.executable, *, extra_env: dict | None = None, cwd: str | None = None) -> Launcher`
  - `parse_tasklist_csv(text: str) -> set[int]`
  - `WindowsOfficeGuard(run=subprocess.run)`, `default_office_guard()`

- [ ] **Step 1: 테스트용 가짜 팩토리** — `tests/web_fake_factory.py` (이름이 `test_` 로 시작하지 않아 수집되지 않는다)

```python
"""worker 통합 테스트용 가짜 파이프라인. ``CC_PIPELINE_FACTORY=web_fake_factory:make`` 로 주입한다."""

from __future__ import annotations

import time

from contentcompare import progress as prog
from contentcompare.fact.pipeline import FactRunResult
from contentcompare.logging_setup import log_print


class _Fake:
    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay

    def run(self, reference, targets):
        log_print("가짜 파이프라인 실행 중")
        prog.plan([prog.Unit("doc:0", "기준.xlsx", prog.DOC)])
        prog.unit_start("doc:0")
        time.sleep(self.delay)
        prog.unit_done("doc:0")
        return FactRunResult(
            summaries=[{"path": reference, "status": "ok"}],
            comparisons=[{"result": "match", "entity_name": "충전온도",
                          "target_doc": "a.docx", "reason": "일치"}],
            markdown="# 가짜 리포트")


def make(config, engine):
    return _Fake()


def make_slow(config, engine):
    return _Fake(delay=120.0)
```

- [ ] **Step 2: 실패하는 테스트** — `tests/test_web_launcher.py`

```python
"""worker 프로세스 — 실제 서브프로세스로 돌려 console.log·결과 파일·종료를 본다."""

from __future__ import annotations

import os
import time
from pathlib import Path

from contentcompare.web import launcher as L
from contentcompare.web.jobs import Job, JobStore

TESTS_DIR = str(Path(__file__).resolve().parent)


def _job(tmp_path):
    store = JobStore(tmp_path / "jobs")
    job = Job(id="20260929-140211-a3f9", engine="fact",
              reference="reference/기준.xlsx", targets=["targets/a.docx"])
    store.save(job)
    return job, store.dir(job.id)


def _launch(tmp_path, factory):
    job, job_dir = _job(tmp_path)
    env = {L.FACTORY_ENV: f"web_fake_factory:{factory}",
           "PYTHONPATH": TESTS_DIR + os.pathsep + os.environ.get("PYTHONPATH", "")}
    # cwd 를 비운 폴더로 — 저장소의 .env·knowledge 를 읽지 않게.
    handle = L.process_launcher(extra_env=env, cwd=str(tmp_path))(job, job_dir)
    return handle, job_dir


def _wait(handle, timeout=90.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        code = handle.poll()
        if code is not None:
            return code
        time.sleep(0.2)
    raise AssertionError("worker 가 끝나지 않았다")


def test_worker_process_writes_console_log_and_results(tmp_path):
    handle, job_dir = _launch(tmp_path, "make")
    assert _wait(handle) == 0
    console = (job_dir / "console.log").read_text(encoding="utf-8")
    assert "가짜 파이프라인 실행 중" in console
    assert (job_dir / "result.json").is_file()
    assert (job_dir / "report.md").read_text(encoding="utf-8") == "# 가짜 리포트"
    assert (job_dir / "progress.jsonl").is_file()


def test_terminate_stops_a_running_worker(tmp_path):
    handle, job_dir = _launch(tmp_path, "make_slow")
    deadline = time.time() + 60
    while not (job_dir / "progress.jsonl").exists() and time.time() < deadline:
        time.sleep(0.2)
    handle.terminate()
    assert handle.poll() is not None
    assert not (job_dir / "result.json").exists()


def test_parse_tasklist_csv_picks_office_images_only():
    text = ('"EXCEL.EXE","1234","Console","1","50,000 K"\n'
            '"notepad.exe","99","Console","1","1 K"\n'
            '"POWERPNT.EXE","5678","Console","1","9 K"\n'
            '"WINWORD.EXE","abc","Console","1","9 K"\n')
    assert L.parse_tasklist_csv(text) == {1234, 5678}


def test_windows_office_guard_kills_only_new_processes():
    calls = []

    class _Result:
        def __init__(self, stdout=""):
            self.stdout = stdout

    def run(cmd, **kw):
        calls.append(cmd)
        if cmd[0] == "tasklist":
            return _Result('"EXCEL.EXE","10","C","1","1 K"\n"POWERPNT.EXE","20","C","1","1 K"\n')
        return _Result()

    guard = L.WindowsOfficeGuard(run=run)
    assert guard.snapshot() == {10, 20}
    assert guard.cleanup({10}) == [20]
    assert ["taskkill", "/PID", "20", "/F"] in calls
    assert not any(c[:3] == ["taskkill", "/PID", "10"] for c in calls)


def test_office_guard_survives_missing_tasklist():
    def run(cmd, **kw):
        raise OSError("tasklist 없음")

    guard = L.WindowsOfficeGuard(run=run)
    assert guard.snapshot() == set()
    assert guard.cleanup(set()) == []
```

- [ ] **Step 3: 실패 확인**

Run: `python -m pytest tests/test_web_launcher.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'launcher'`

- [ ] **Step 4: 구현** — `contentcompare/web/launcher.py`

```python
"""worker 프로세스 실행·종료와 Office 프로세스 정리.

- worker 의 stdout/stderr 를 작업 폴더의 ``console.log`` 로 받는다 — 이것이 실행 창의 로그다.
  ``PYTHONIOENCODING=utf-8`` 로 cp949 콘솔에서 한글·기호 줄이 사라지는 문제(CLAUDE.md
  「콘솔 인코딩」)를 피하고, ``PYTHONUNBUFFERED=1`` 로 줄이 바로 파일에 닿게 한다.
- 종료는 **프로세스 트리**째(``taskkill /T``) — worker 가 띄운 Office 자식까지.
- Office 정리(설계 §5.4): 작업 시작 전 목록에 **없던** ``EXCEL/WINWORD/POWERPNT`` 만 죽인다.
  ⚠️ 작업 중 서버 PC 에서 사람이 새로 연 Office 도 "없던 것"이라 함께 닫힌다 — 그래서
  서버 PC 에서 사람이 Office 를 같이 쓰지 않는 것이 운영 전제다(설계 §6 #2).
"""

from __future__ import annotations

import csv
import io
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

from .jobs import Job
from .scheduler import Launcher, NullOfficeGuard
from .worker import FACTORY_ENV

logger = logging.getLogger(__name__)

OFFICE_IMAGES = ("EXCEL.EXE", "WINWORD.EXE", "POWERPNT.EXE")

__all__ = ["FACTORY_ENV", "OFFICE_IMAGES", "ProcessHandle", "process_launcher",
           "parse_tasklist_csv", "WindowsOfficeGuard", "default_office_guard"]


class ProcessHandle:
    def __init__(self, proc: subprocess.Popen, log_file) -> None:
        self.proc = proc
        self._log = log_file

    @property
    def pid(self) -> int:
        return self.proc.pid

    def poll(self) -> Optional[int]:
        code = self.proc.poll()
        if code is not None:
            self._close()
        return code

    def terminate(self) -> None:
        if self.proc.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                               capture_output=True)
            else:
                self.proc.kill()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        self._close()

    def _close(self) -> None:
        if not self._log.closed:
            self._log.close()


def process_launcher(python: str = sys.executable, *, extra_env: Optional[dict] = None,
                     cwd: Optional[str] = None) -> Launcher:
    def launch(job: Job, job_dir: Path) -> ProcessHandle:
        log = open(job_dir / "console.log", "ab")
        env = dict(os.environ)
        env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"})
        env.update(extra_env or {})
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        try:
            proc = subprocess.Popen(
                [python, "-m", "contentcompare.web.worker", str(Path(job_dir).resolve())],
                stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                cwd=cwd, env=env, creationflags=flags,
            )
        except Exception:
            log.close()
            raise
        return ProcessHandle(proc, log)

    return launch


def parse_tasklist_csv(text: str) -> set[int]:
    pids: set[int] = set()
    for row in csv.reader(io.StringIO(text)):
        if len(row) >= 2 and row[0].upper() in OFFICE_IMAGES:
            try:
                pids.add(int(row[1]))
            except ValueError:
                continue
    return pids


class WindowsOfficeGuard:
    def __init__(self, run: Callable = subprocess.run) -> None:
        self._run = run

    def snapshot(self) -> set[int]:
        try:
            out = self._run(["tasklist", "/FO", "CSV", "/NH"],
                            capture_output=True, text=True, timeout=15).stdout
        except (OSError, subprocess.SubprocessError):
            return set()
        return parse_tasklist_csv(out or "")

    def cleanup(self, before: set[int]) -> list[int]:
        killed = []
        for pid in sorted(self.snapshot() - set(before)):
            try:
                self._run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=15)
            except (OSError, subprocess.SubprocessError):
                continue
            killed.append(pid)
        if killed:
            logger.warning("남은 Office 프로세스 정리: %s", killed)
        return killed


def default_office_guard():
    return WindowsOfficeGuard() if os.name == "nt" else NullOfficeGuard()
```

- [ ] **Step 5: 통과 확인**

Run: `python -m pytest tests/test_web_launcher.py -q -p no:cacheprovider`
Expected: 5 passed(서브프로세스 2건은 수 초 걸린다)

- [ ] **Step 6: 커밋**

```bash
git add contentcompare/web/launcher.py tests/web_fake_factory.py tests/test_web_launcher.py
git commit -m "feat(web): worker 프로세스 실행 — console.log 로 화면 로그, 트리째 종료, 새 Office 만 정리

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_015WPCPHZGMqNLc7QQAv1ako"
```

---

### Task 9: 실시간 이벤트(SSE)

**Files:**
- Create: `contentcompare/web/events.py`
- Create: `tests/test_web_events.py`

**Interfaces:**
- Consumes: Task 3 `Job`, `RUNNING`; 계획 1 `prog.load_events`, `prog.summarize`.
- Produces:
  - `read_complete_lines(path: Path, offset: int, *, max_bytes: int = 262144) -> tuple[list[str], int]`
  - `format_sse(event: str, data: dict, event_id: str = "") -> str`
  - `@dataclass Cursor(log: int = 0, seq: int = 0, state: str = "", stalled: bool = False)`, `.to_id() -> str`(`"<log>.<seq>"`), `Cursor.parse(value: str | None) -> Cursor`
  - `collect(job, job_dir, cursor, *, now: float, stall_after_s: float) -> tuple[list[tuple[str, dict]], Cursor]`
  - `stream(load_job, job_dir, cursor, *, stall_after_s, poll_s=0.5, sleep=time.sleep, clock=time.time, heartbeat_s=15.0) -> Iterator[str]`
  - SSE 이벤트 이름: `log`(`{"lines": [...]}`), `progress`(`Snapshot.to_dict()`), `status`(`{"state","error","started_ts","finished_ts"}`), `stall`(`{"stalled": bool, "idle_s": int}`), `end`(`{"state"}` 또는 `{"reason": "not_found"}`)

- [ ] **Step 1: 실패하는 테스트** — `tests/test_web_events.py`

```python
"""SSE — 완성된 줄만, 재연결은 이어서, 멈춤은 한 번만 알린다."""

from __future__ import annotations

import json

from contentcompare.web import events as E
from contentcompare.web.jobs import FAILED, RUNNING, SUCCEEDED, Job


def _job(state=RUNNING, started=100.0):
    return Job(id="20260929-140211-a3f9", engine="fact", state=state, started_ts=started)


def test_partial_line_waits_until_newline(tmp_path):
    log = tmp_path / "console.log"
    log.write_bytes("첫 줄\n둘째 줄 쓰는 중".encode("utf-8"))
    lines, offset = E.read_complete_lines(log, 0)
    assert lines == ["첫 줄"]
    with open(log, "ab") as f:
        f.write(" 끝\n".encode("utf-8"))
    lines, offset = E.read_complete_lines(log, offset)
    assert lines == ["둘째 줄 쓰는 중 끝"]
    assert E.read_complete_lines(log, offset) == ([], offset)


def test_oversized_line_without_newline_is_flushed(tmp_path):
    log = tmp_path / "console.log"
    log.write_bytes(b"x" * 100)
    lines, offset = E.read_complete_lines(log, 0, max_bytes=40)
    assert lines == ["x" * 40] and offset == 40


def test_truncated_file_restarts_from_zero(tmp_path):
    log = tmp_path / "console.log"
    log.write_bytes(b"a\n")  # write_text 는 Windows 에서 \r\n 으로 바꿔 바이트 위치가 어긋난다
    assert E.read_complete_lines(log, 999) == (["a"], 2)


def test_missing_file_is_quiet(tmp_path):
    assert E.read_complete_lines(tmp_path / "없음.log", 0) == ([], 0)


def test_format_sse_is_one_data_line():
    text = E.format_sse("log", {"lines": ["가\n나"]}, "3.1")
    assert text == 'id: 3.1\nevent: log\ndata: {"lines": ["가\\n나"]}\n\n'


def test_cursor_round_trip_and_garbage():
    assert E.Cursor.parse(E.Cursor(log=12, seq=5).to_id()) == E.Cursor(log=12, seq=5)
    assert E.Cursor.parse("쓰레기") == E.Cursor()
    assert E.Cursor.parse(None) == E.Cursor()


def test_first_collect_sends_progress_and_status(tmp_path):
    (tmp_path / "console.log").write_bytes("시작\n".encode("utf-8"))
    items, cursor = E.collect(_job(), tmp_path, E.Cursor(), now=101.0, stall_after_s=300)
    kinds = [k for k, _ in items]
    assert kinds == ["log", "progress", "status"]
    assert items[0][1] == {"lines": ["시작"]}
    assert cursor.state == RUNNING and cursor.log == len("시작\n".encode("utf-8"))
    again, _ = E.collect(_job(), tmp_path, cursor, now=102.0, stall_after_s=300)
    assert again == []


def test_stall_is_announced_once_and_cleared(tmp_path):
    (tmp_path / "console.log").write_bytes(b"")
    import os
    os.utime(tmp_path / "console.log", (100.0, 100.0))
    _, cursor = E.collect(_job(), tmp_path, E.Cursor(), now=101.0, stall_after_s=300)
    items, cursor = E.collect(_job(), tmp_path, cursor, now=500.0, stall_after_s=300)
    assert items == [("stall", {"stalled": True, "idle_s": 400})]
    items, cursor = E.collect(_job(), tmp_path, cursor, now=600.0, stall_after_s=300)
    assert items == []
    items, _ = E.collect(_job(state=SUCCEEDED), tmp_path, cursor, now=700.0, stall_after_s=300)
    assert ("stall", {"stalled": False, "idle_s": 600}) in items


def test_stream_ends_after_final_state(tmp_path):
    (tmp_path / "console.log").write_bytes("끝\n".encode("utf-8"))
    job = _job(state=FAILED)
    job.error = "ValueError: x"
    chunks = list(E.stream(lambda: job, tmp_path, E.Cursor(), stall_after_s=300,
                           sleep=lambda s: None, clock=lambda: 200.0))
    events = [c.split("\n")[1] for c in chunks if c.startswith("id:")]
    assert events == ["event: log", "event: progress", "event: status", "event: end"]
    status = json.loads(chunks[2].split("data: ", 1)[1])
    assert status["state"] == FAILED and status["error"] == "ValueError: x"


def test_stream_resumes_after_last_event_id(tmp_path):
    (tmp_path / "console.log").write_bytes("본 줄\n새 줄\n".encode("utf-8"))
    seen = len("본 줄\n".encode("utf-8"))
    chunks = list(E.stream(lambda: _job(state=SUCCEEDED), tmp_path,
                           E.Cursor.parse(f"{seen}.0"), stall_after_s=300,
                           sleep=lambda s: None, clock=lambda: 200.0))
    logs = [json.loads(c.split("data: ", 1)[1]) for c in chunks if "event: log" in c]
    assert logs == [{"lines": ["새 줄"]}]


def test_stream_for_a_deleted_job_ends(tmp_path):
    chunks = list(E.stream(lambda: None, tmp_path, E.Cursor(), stall_after_s=300,
                           sleep=lambda s: None))
    assert chunks == [E.format_sse("end", {"reason": "not_found"})]
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_web_events.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'events'`

- [ ] **Step 3: 구현** — `contentcompare/web/events.py`

```python
"""작업 폴더의 파일 → SSE(Server-Sent Events).

worker 는 서버와 파일로만 말한다(설계 §4.1). 여기서 ``console.log``(화면 로그)·
``progress.jsonl``(진행률)·``job.json``(상태)을 짧은 간격으로 읽어 이벤트로 흘린다.

- **완성된 줄만 보낸다.** worker 가 쓰는 도중이거나 강제 종료로 끝이 잘린 줄은 개행이 올 때까지
  기다린다. 단, 개행 없는 거대한 줄에서 영영 멈추지 않도록 ``max_bytes`` 를 넘으면 흘린다.
- **재연결은 이어서.** 이벤트 ``id`` 가 ``"<로그 바이트 위치>.<진행 seq>"`` 라, 브라우저가
  ``Last-Event-ID`` 로 다시 붙으면 본 줄을 또 보내지 않는다.
- **멈춤은 상태가 바뀔 때만 알린다**(설계 §6 #7). 활동 = 진행 이벤트·로그 파일 갱신·시작 시각
  중 가장 최근. 자동 종료는 하지 않는다.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Iterator, Optional

from .. import progress as prog
from .jobs import RUNNING, Job


def read_complete_lines(path: Path, offset: int, *,
                        max_bytes: int = 256 * 1024) -> tuple[list[str], int]:
    try:
        size = path.stat().st_size
    except OSError:
        return [], offset
    if size < offset:
        offset = 0  # 파일이 새로 쓰였다
    if size == offset:
        return [], offset
    with open(path, "rb") as f:
        f.seek(offset)
        data = f.read(min(size - offset, max_bytes))
    end = data.rfind(b"\n")
    if end < 0:
        if len(data) < max_bytes:
            return [], offset  # 아직 줄이 완성되지 않았다
        return [data.decode("utf-8", errors="replace")], offset + len(data)
    chunk = data[:end + 1]
    return chunk.decode("utf-8", errors="replace").splitlines(), offset + len(chunk)


def format_sse(event: str, data: dict, event_id: str = "") -> str:
    head = f"id: {event_id}\n" if event_id else ""
    return f"{head}event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@dataclass
class Cursor:
    log: int = 0
    seq: int = 0
    state: str = ""
    stalled: bool = False

    def to_id(self) -> str:
        return f"{self.log}.{self.seq}"

    @classmethod
    def parse(cls, value: Optional[str]) -> "Cursor":
        try:
            log, seq = str(value).split(".")
            return cls(log=int(log), seq=int(seq))
        except (ValueError, AttributeError):
            return cls()


def collect(job: Job, job_dir: Path, cursor: Cursor, *, now: float,
            stall_after_s: float) -> tuple[list[tuple[str, dict]], Cursor]:
    out: list[tuple[str, dict]] = []
    cur = replace(cursor)
    log_path = job_dir / "console.log"
    lines, cur.log = read_complete_lines(log_path, cursor.log)
    if lines:
        out.append(("log", {"lines": lines}))
    snap = prog.summarize(prog.load_events(job_dir / "progress.jsonl"))
    if snap.last_seq != cursor.seq or not cursor.state:
        cur.seq = snap.last_seq
        out.append(("progress", snap.to_dict()))
    if job.state != cursor.state:
        cur.state = job.state
        out.append(("status", {"state": job.state, "error": job.error,
                               "started_ts": job.started_ts, "finished_ts": job.finished_ts}))
    try:
        log_mtime = os.path.getmtime(log_path)
    except OSError:
        log_mtime = 0.0
    idle = now - max(snap.last_ts, log_mtime, job.started_ts)
    stalled = job.state == RUNNING and idle > stall_after_s
    if stalled != cursor.stalled:
        cur.stalled = stalled
        out.append(("stall", {"stalled": stalled, "idle_s": int(idle)}))
    return out, cur


def stream(load_job: Callable[[], Optional[Job]], job_dir: Path, cursor: Cursor, *,
           stall_after_s: float, poll_s: float = 0.5,
           sleep: Callable[[float], None] = time.sleep,
           clock: Callable[[], float] = time.time,
           heartbeat_s: float = 15.0) -> Iterator[str]:
    quiet = 0.0
    while True:
        job = load_job()
        if job is None:
            yield format_sse("end", {"reason": "not_found"})
            return
        items, cursor = collect(job, job_dir, cursor, now=clock(), stall_after_s=stall_after_s)
        for event, data in items:
            yield format_sse(event, data, cursor.to_id())
        if job.is_final and not any(event == "log" for event, _ in items):
            # 끝난 뒤에도 남은 로그 줄이 없어질 때까지 한 바퀴 더 돈다.
            yield format_sse("end", {"state": job.state}, cursor.to_id())
            return
        if items:
            quiet = 0.0
        else:
            quiet += poll_s
            if quiet >= heartbeat_s:
                yield ": keepalive\n\n"  # 프록시가 조용한 연결을 끊지 않게
                quiet = 0.0
        sleep(poll_s)
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_web_events.py -q -p no:cacheprovider`
Expected: 11 passed

- [ ] **Step 5: 커밋**

```bash
git add contentcompare/web/events.py tests/test_web_events.py
git commit -m "feat(web): 실시간 이벤트 — 완성된 줄만, Last-Event-ID 로 이어서, 멈춤은 경고만

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_015WPCPHZGMqNLc7QQAv1ako"
```

---

### Task 10: 관리자 인증과 로그 읽기

**Files:**
- Create: `contentcompare/web/admin.py`
- Create: `tests/test_web_admin.py`

**Interfaces:**
- Consumes: Task 3 `is_job_id`.
- Produces:
  - `class AuthError(Exception)` — `.reason` ∈ `DISABLED`, `LOCKED`, `WRONG`
  - `AdminAuth(password, *, clock=time.time, session_ttl_s=8*3600, max_failures=5, lockout_s=60)`: `.enabled`, `.login(password) -> str`, `.verify(token) -> bool`, `.logout(token)`
  - `@dataclass LogSource(id, label, path, size, mtime)`, `.to_dict()`(경로 제외)
  - `list_log_sources(logs_dir: Path, jobs_root: Path) -> list[LogSource]` — ID `logs/<파일>`, `job/<작업ID>/<파일>`
  - `resolve_source(source_id, logs_dir, jobs_root) -> Path`(허용 밖이면 `KeyError`)
  - `LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")`
  - `read_records(path, *, max_bytes=5_000_000) -> list[dict]` — `{"level": str, "text": str}`
  - `filter_records(records, *, min_level="DEBUG", query="") -> list[dict]`
  - `page(records, *, tail=500, before=None) -> dict` — `{"records","start","end","total"}`

- [ ] **Step 1: 실패하는 테스트** — `tests/test_web_admin.py`

```python
"""관리자 — 비밀번호 잠금, 세션 만료, 허용된 로그만, 레벨·검색·페이지."""

from __future__ import annotations

import pytest

from contentcompare.web import admin as A


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_disabled_without_password():
    auth = A.AdminAuth("")
    assert not auth.enabled
    with pytest.raises(A.AuthError) as exc:
        auth.login("")
    assert exc.value.reason == A.DISABLED


def test_login_verify_logout():
    auth = A.AdminAuth("pw")
    with pytest.raises(A.AuthError) as exc:
        auth.login("틀림")
    assert exc.value.reason == A.WRONG
    token = auth.login("pw")
    assert auth.verify(token) and not auth.verify("가짜") and not auth.verify(None)
    auth.logout(token)
    assert not auth.verify(token)


def test_lockout_rejects_even_the_right_password():
    clock = _Clock()
    auth = A.AdminAuth("pw", clock=clock, max_failures=5, lockout_s=60)
    for _ in range(5):
        with pytest.raises(A.AuthError):
            auth.login("x")
    with pytest.raises(A.AuthError) as exc:
        auth.login("pw")
    assert exc.value.reason == A.LOCKED
    clock.now += 61
    assert auth.verify(auth.login("pw"))


def test_session_expires():
    clock = _Clock()
    auth = A.AdminAuth("pw", clock=clock, session_ttl_s=10)
    token = auth.login("pw")
    clock.now += 11
    assert not auth.verify(token)


def _tree(tmp_path):
    logs, jobs = tmp_path / "logs", tmp_path / "jobs"
    logs.mkdir()
    (logs / "server.log").write_text("x\n", encoding="utf-8")
    (logs / "메모.txt").write_text("x", encoding="utf-8")
    job = jobs / "20260929-140211-a3f9"
    job.mkdir(parents=True)
    (job / "contentcompare_20260929_140211.log").write_text("y\n", encoding="utf-8")
    (job / "console.log").write_text("z\n", encoding="utf-8")
    (jobs / "잡동사니").mkdir()
    (jobs / "잡동사니" / "a.log").write_text("n\n", encoding="utf-8")
    return logs, jobs


def test_sources_cover_server_and_job_logs_only(tmp_path):
    logs, jobs = _tree(tmp_path)
    ids = sorted(s.id for s in A.list_log_sources(logs, jobs))
    assert ids == ["job/20260929-140211-a3f9/console.log",
                   "job/20260929-140211-a3f9/contentcompare_20260929_140211.log",
                   "logs/server.log"]
    assert "path" not in A.list_log_sources(logs, jobs)[0].to_dict()


@pytest.mark.parametrize("bad", [
    "logs/../x.log", "logs/메모.txt", "job/잡동사니/a.log", "job/20260929-140211-a3f9/../../x.log",
    "logs/없음.log", "etc/passwd", "logs"])
def test_resolve_rejects_anything_outside(tmp_path, bad):
    logs, jobs = _tree(tmp_path)
    with pytest.raises(KeyError):
        A.resolve_source(bad, logs, jobs)


def test_resolve_accepts_listed(tmp_path):
    logs, jobs = _tree(tmp_path)
    assert A.resolve_source("logs/server.log", logs, jobs) == logs / "server.log"


LOG = """\
2026-09-29 14:02:11,001 INFO contentcompare: 시작
2026-09-29 14:02:12,002 DEBUG contentcompare.llm: 프롬프트 원문
2026-09-29 14:02:13,003 ERROR contentcompare.fact: 실패
Traceback (most recent call last):
  File "x.py", line 1
ValueError: 파싱
2026-09-29 14:02:14,004 WARNING contentcompare: 느림
"""


def test_records_attach_traceback_lines(tmp_path):
    path = tmp_path / "a.log"
    path.write_bytes(LOG.encode("utf-8"))
    records = A.read_records(path)
    assert [r["level"] for r in records] == ["INFO", "DEBUG", "ERROR", "WARNING"]
    assert records[2]["text"].endswith("ValueError: 파싱")


def test_console_log_lines_are_separate_records(tmp_path):
    path = tmp_path / "console.log"
    path.write_bytes("작업 시작\n[F2] 배치 1/3\n".encode("utf-8"))
    assert A.read_records(path) == [{"level": "", "text": "작업 시작"},
                                    {"level": "", "text": "[F2] 배치 1/3"}]


def test_filter_by_level_and_query(tmp_path):
    path = tmp_path / "a.log"
    path.write_bytes(LOG.encode("utf-8"))
    records = A.read_records(path)
    assert [r["level"] for r in A.filter_records(records, min_level="WARNING")] == [
        "ERROR", "WARNING"]
    assert len(A.filter_records(records, query="파싱")) == 1
    console = [{"level": "", "text": "작업 시작"}]
    assert A.filter_records(console, min_level="INFO") == console
    assert A.filter_records(console, min_level="WARNING") == []


def test_page_tail_and_before():
    records = [{"level": "INFO", "text": str(i)} for i in range(10)]
    last = A.page(records, tail=3)
    assert [r["text"] for r in last["records"]] == ["7", "8", "9"]
    assert (last["start"], last["end"], last["total"]) == (7, 10, 10)
    earlier = A.page(records, tail=3, before=last["start"])
    assert [r["text"] for r in earlier["records"]] == ["4", "5", "6"]


def test_read_records_tail_drops_partial_first_line(tmp_path):
    path = tmp_path / "big.log"
    path.write_bytes(("A" * 50 + "\n" + LOG).encode("utf-8"))
    records = A.read_records(path, max_bytes=len(LOG.encode("utf-8")) + 10)
    assert records[0]["text"].startswith("2026-09-29 14:02:11,001")
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_web_admin.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'admin'`

- [ ] **Step 3: 구현** — `contentcompare/web/admin.py`

```python
"""관리자 — 비밀번호 인증(무차별 대입 잠금)과 로그 읽기.

로그인이 없는 서비스에서 관리자 화면만 잠그는 이유: 작업 로그(``contentcompare_*.log``)는 DEBUG
까지 담아 **프롬프트와 LLM 원문 = 다른 사람의 문서 내용**이 있다(설계 §2 접근 제어).
⚠️ HTTPS 가 없어 비밀번호가 평문으로 오간다 — 사내망 전용이라는 전제(설계 §6 #11).

로그는 허용된 두 곳만 연다: ``logs/*.log``(서버 로그·과거 CLI 로그), ``jobs/<ID>/*.log``.
"""

from __future__ import annotations

import hmac
import re
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .jobs import is_job_id

DISABLED = "disabled"
LOCKED = "locked"
WRONG = "wrong"


class AuthError(Exception):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class AdminAuth:
    def __init__(self, password: str, *, clock: Callable[[], float] = time.time,
                 session_ttl_s: float = 8 * 3600, max_failures: int = 5,
                 lockout_s: float = 60) -> None:
        self._password = password or ""
        self.clock = clock
        self.session_ttl_s = session_ttl_s
        self.max_failures = max_failures
        self.lockout_s = lockout_s
        self._sessions: dict[str, float] = {}
        self._failures = 0
        self._locked_until = 0.0
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self._password)

    def login(self, password: str) -> str:
        with self._lock:
            if not self.enabled:
                raise AuthError(DISABLED, "관리자 기능이 설정되지 않았습니다(.env 의 CC_ADMIN_PASSWORD).")
            now = self.clock()
            if now < self._locked_until:
                raise AuthError(LOCKED, f"비밀번호를 연속으로 틀려 잠겼습니다. "
                                        f"{int(self._locked_until - now) + 1}초 뒤 다시 시도하세요.")
            if not hmac.compare_digest((password or "").encode("utf-8"),
                                       self._password.encode("utf-8")):
                self._failures += 1
                if self._failures >= self.max_failures:
                    self._failures = 0
                    self._locked_until = now + self.lockout_s
                raise AuthError(WRONG, "비밀번호가 틀렸습니다.")
            self._failures = 0
            token = secrets.token_urlsafe(32)
            self._sessions[token] = now + self.session_ttl_s
            return token

    def verify(self, token: Optional[str]) -> bool:
        if not token:
            return False
        with self._lock:
            expires = self._sessions.get(token)
            if expires is None:
                return False
            if self.clock() >= expires:
                del self._sessions[token]
                return False
            return True

    def logout(self, token: Optional[str]) -> None:
        with self._lock:
            self._sessions.pop(token or "", None)


# --------------------------------------------------------------------------- #
# 로그
# --------------------------------------------------------------------------- #
LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
# logging_setup.setup_logging 의 형식: "%(asctime)s %(levelname)s %(name)s: %(message)s"
_HEAD = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} (DEBUG|INFO|WARNING|ERROR|CRITICAL) ")


@dataclass
class LogSource:
    id: str
    label: str
    path: Path
    size: int
    mtime: float

    def to_dict(self) -> dict:
        return {"id": self.id, "label": self.label, "size": self.size, "mtime": self.mtime}


def _source(source_id: str, label: str, path: Path) -> LogSource:
    st = path.stat()
    return LogSource(source_id, label, path, st.st_size, st.st_mtime)


def list_log_sources(logs_dir: Path, jobs_root: Path) -> list[LogSource]:
    out: list[LogSource] = []
    if logs_dir.is_dir():
        for p in logs_dir.glob("*.log"):
            out.append(_source(f"logs/{p.name}", p.name, p))
    if jobs_root.is_dir():
        for d in jobs_root.iterdir():
            if d.is_dir() and is_job_id(d.name):
                for p in d.glob("*.log"):
                    out.append(_source(f"job/{d.name}/{p.name}", f"작업 {d.name} · {p.name}", p))
    out.sort(key=lambda s: s.mtime, reverse=True)
    return out


def resolve_source(source_id: str, logs_dir: Path, jobs_root: Path) -> Path:
    parts = (source_id or "").split("/")
    if len(parts) == 2 and parts[0] == "logs":
        base, name = logs_dir, parts[1]
    elif len(parts) == 3 and parts[0] == "job" and is_job_id(parts[1]):
        base, name = jobs_root / parts[1], parts[2]
    else:
        raise KeyError(source_id)
    if name != Path(name).name or name in ("", ".", "..") or not name.endswith(".log"):
        raise KeyError(source_id)
    path = base / name
    if not path.is_file():
        raise KeyError(source_id)
    return path


def read_records(path: Path, *, max_bytes: int = 5_000_000) -> list[dict]:
    """로그 → 레코드. 형식 있는 로그는 이어지는 줄(traceback)을 앞 레코드에 붙인다.

    파일이 크면 **끝부분만** 읽는다(관리자는 대개 최근 것을 본다. 전체는 다운로드로).
    """
    size = path.stat().st_size
    with open(path, "rb") as f:
        if size > max_bytes:
            f.seek(size - max_bytes)
            data = f.read()
            data = data[data.find(b"\n") + 1:]  # 잘린 첫 줄은 버린다
        else:
            data = f.read()
    lines = data.decode("utf-8", errors="replace").splitlines()
    structured = any(_HEAD.match(line) for line in lines[:200])
    records: list[dict] = []
    for line in lines:
        m = _HEAD.match(line)
        if not structured or m or not records:
            records.append({"level": m.group(1) if m else "", "text": line})
        else:
            records[-1]["text"] += "\n" + line
    return records


def filter_records(records: list[dict], *, min_level: str = "DEBUG",
                   query: str = "") -> list[dict]:
    """레벨 하한과 검색어. 레벨이 없는 줄(``console.log``)은 INFO 로 본다."""
    floor = LEVELS.index(min_level) if min_level in LEVELS else 0
    q = (query or "").strip().lower()
    out = []
    for r in records:
        level = LEVELS.index(r["level"]) if r["level"] in LEVELS else LEVELS.index("INFO")
        if level < floor:
            continue
        if q and q not in r["text"].lower():
            continue
        out.append(r)
    return out


def page(records: list[dict], *, tail: int = 500, before: Optional[int] = None) -> dict:
    end = len(records) if before is None else max(0, min(before, len(records)))
    start = max(0, end - max(1, tail))
    return {"records": records[start:end], "start": start, "end": end, "total": len(records)}
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_web_admin.py -q -p no:cacheprovider`
Expected: 18 passed

- [ ] **Step 5: 커밋**

```bash
git add contentcompare/web/admin.py tests/test_web_admin.py
git commit -m "feat(web): 관리자 인증(5회 잠금)과 로그 읽기 — 허용된 폴더만, 레벨·검색·페이지

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_015WPCPHZGMqNLc7QQAv1ako"
```

---

### Task 11: FastAPI 앱 — 작업·LLM 점검 라우트

**Files:**
- Create: `contentcompare/web/app.py`
- Create: `tests/test_web_app_jobs.py`

**Interfaces:**
- Consumes: Task 2~10 전부(`WebSettings`, `build_app_config`, `public_llm_summary`, `Job`, `JobStore`, `ENGINES`, `new_job_id`, `purge_expired`, `plan_upload`, `safe_relpath`, `save_stream`, `UploadError`, `JobScheduler`, `process_launcher`, `default_office_guard`, `Cursor`, `stream`, `AdminAuth`), `llm.health.check_llm`, 계획 1 `prog.load_events`·`summarize`.
- Produces:
  - `CLIENT_COOKIE = "cc_client"`, `ADMIN_COOKIE = "cc_admin"`
  - `LlmChecker(check: Callable[[], list], *, clock=time.time, ttl_s=30.0)`, `.run() -> dict`(`{"ok","results":[{name,ok,detail,line}],"cached"}`)
  - `@dataclass AppState(settings, config, store, scheduler, checker, auth)`
  - `create_app(settings, *, config=None, store=None, scheduler=None, checker=None, auth=None, background=True, static_dir=None) -> FastAPI` — `app.state.cc` 에 `AppState`
  - `client_id(request, response) -> str`(의존성), `is_admin(state, request) -> bool`, `job_view(job, cid, positions) -> dict`(`client_id` 제외, `mine`, `position` 포함)
  - 라우트: `POST /api/llm/check`, `POST /api/jobs`(폼: `engine`, `requester`, `reference`, `targets[]`, 선택 `reference_path`, `target_paths[]`), `GET /api/jobs`, `GET /api/jobs/{id}`, `POST /api/jobs/{id}/cancel`, `GET /api/jobs/{id}/events`, `GET /api/jobs/{id}/result`, `GET /api/jobs/{id}/report`

- [ ] **Step 1: 실패하는 테스트** — `tests/test_web_app_jobs.py`

```python
"""작업 API — 업로드·대기열·내 작업·취소 권한·SSE·결과."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("python_multipart")
from fastapi.testclient import TestClient  # noqa: E402

from contentcompare.web import app as web_app  # noqa: E402
from contentcompare.web import jobs as J  # noqa: E402
from contentcompare.web.admin import AdminAuth  # noqa: E402
from contentcompare.web.scheduler import JobScheduler  # noqa: E402
from contentcompare.web.settings import WebSettings  # noqa: E402


class _Checker:
    def __init__(self):
        self.calls = 0

    def run(self):
        self.calls += 1
        return {"ok": True, "results": [], "cached": self.calls > 1}


def _app(tmp_path, **settings_kw):
    settings = WebSettings(jobs_dir=str(tmp_path / "jobs"), **settings_kw)
    store = J.JobStore(settings.jobs_dir)
    scheduler = JobScheduler(store, lambda job, d: None)
    app = web_app.create_app(settings, store=store, scheduler=scheduler, checker=_Checker(),
                             auth=AdminAuth("pw"), background=False,
                             static_dir=tmp_path / "없는dist")
    return app, store


def _files(ref=("ref.xlsx", b"R"), targets=(("a.docx", b"A"),)):
    files = [("reference", (ref[0], ref[1], "application/octet-stream"))]
    files += [("targets", (name, data, "application/octet-stream")) for name, data in targets]
    return files


def _submit(client, **kw):
    data = {"engine": "fact", "requester": "홍길동", **kw.pop("data", {})}
    return client.post("/api/jobs", data=data, files=kw.pop("files", _files()))


def test_client_cookie_is_issued_once(tmp_path):
    app, _ = _app(tmp_path)
    client = TestClient(app)
    client.get("/api/jobs")
    first = client.cookies.get(web_app.CLIENT_COOKIE)
    client.get("/api/jobs")
    assert first and client.cookies.get(web_app.CLIENT_COOKIE) == first


def test_llm_check_goes_through_the_checker(tmp_path):
    app, _ = _app(tmp_path)
    client = TestClient(app)
    assert client.post("/api/llm/check").json() == {"ok": True, "results": [], "cached": False}
    assert client.post("/api/llm/check").json()["cached"] is True


def test_llm_checker_caches_and_serializes(tmp_path):
    calls = []

    class _R:
        name, ok, detail = "chat", True, "ok"

        def line(self):
            return "✅ chat: ok"

    clock = [0.0]
    checker = web_app.LlmChecker(lambda: calls.append(1) or [_R()], clock=lambda: clock[0])
    first = checker.run()
    assert first["ok"] and first["results"][0]["line"] == "✅ chat: ok" and not first["cached"]
    assert checker.run()["cached"] and len(calls) == 1
    clock[0] = 31.0
    assert not checker.run()["cached"] and len(calls) == 2


def test_submit_saves_files_and_queues(tmp_path):
    app, store = _app(tmp_path)
    client = TestClient(app)
    res = _submit(client, files=_files(targets=(("a.docx", b"A"), ("~$a.docx", b"L"))))
    assert res.status_code == 200, res.text
    body = res.json()
    job = store.load(body["job"]["id"])
    assert job.state == J.QUEUED and job.requester == "홍길동"
    assert job.reference == "reference/ref.xlsx" and job.targets == ["targets/a.docx"]
    assert (store.dir(job.id) / "inputs" / "targets" / "a.docx").read_bytes() == b"A"
    assert body["skipped"] == ["~$a.docx"]
    assert body["job"]["mine"] is True and "client_id" not in body["job"]


def test_submit_uses_explicit_relative_paths_for_korean_names(tmp_path):
    app, store = _app(tmp_path)
    client = TestClient(app)
    res = _submit(client, data={"reference_path": "기준.xlsx",
                                "target_paths": ["자료/규격서 v2.docx"]},
                  files=_files(ref=("x.xlsx", b"R"), targets=(("y.docx", b"A"),)))
    assert res.status_code == 200, res.text
    job = store.load(res.json()["job"]["id"])
    assert job.reference == "reference/기준.xlsx"
    assert job.targets == ["targets/자료/규격서 v2.docx"]
    assert (store.dir(job.id) / "inputs" / "targets" / "자료" / "규격서 v2.docx").is_file()


def test_path_count_mismatch_is_rejected(tmp_path):
    app, _ = _app(tmp_path)
    res = _submit(TestClient(app), data={"target_paths": ["a.docx", "b.docx"]})
    assert res.status_code == 400


@pytest.mark.parametrize("data, files, fragment", [
    ({"engine": "gpt"}, None, "엔진"),
    ({}, _files(ref=("ref.docx", b"R")), "Excel"),
    ({}, _files(targets=(("a/규격.docx", b"1"), ("b/규격.docx", b"2"))), "같은 이름"),
])
def test_bad_submissions_leave_nothing_behind(tmp_path, data, files, fragment):
    app, store = _app(tmp_path)
    kw = {"data": data}
    if files:
        kw["files"] = files
    res = _submit(TestClient(app), **kw)
    assert res.status_code == 400 and fragment in res.json()["detail"]
    assert store.list() == []
    assert not any((tmp_path / "jobs").glob("*")) if (tmp_path / "jobs").exists() else True


def test_oversized_upload_is_rejected_and_cleaned(tmp_path):
    app, store = _app(tmp_path, max_upload_mb=1)
    big = b"x" * (1024 * 1024 + 1)
    res = _submit(TestClient(app), files=_files(targets=(("a.docx", big),)))
    assert res.status_code == 400 and "큽니다" in res.json()["detail"]
    assert store.list() == []


def test_job_id_collision_is_retried(tmp_path, monkeypatch):
    app, store = _app(tmp_path)
    ids = iter(["20260929-140211-aaaa", "20260929-140211-aaaa", "20260929-140211-bbbb"])
    monkeypatch.setattr(web_app, "new_job_id", lambda: next(ids))
    client = TestClient(app)
    first = _submit(client).json()["job"]["id"]
    second = _submit(client).json()["job"]["id"]
    assert (first, second) == ("20260929-140211-aaaa", "20260929-140211-bbbb")


def test_list_marks_mine_and_positions(tmp_path):
    app, _ = _app(tmp_path)
    me, other = TestClient(app), TestClient(app)
    a = _submit(me).json()["job"]["id"]
    b = _submit(other).json()["job"]["id"]
    listing = me.get("/api/jobs").json()
    views = {j["id"]: j for j in listing["jobs"]}
    assert views[a]["mine"] and not views[b]["mine"]
    assert views[a]["position"] == 0 and views[b]["position"] == 1
    assert listing["running"] is None


def test_cancel_is_owner_or_admin_only(tmp_path):
    app, store = _app(tmp_path)
    owner, stranger = TestClient(app), TestClient(app)
    job_id = _submit(owner).json()["job"]["id"]
    assert stranger.post(f"/api/jobs/{job_id}/cancel").status_code == 403
    assert owner.post(f"/api/jobs/{job_id}/cancel").json()["job"]["state"] == J.CANCELLED
    assert owner.post(f"/api/jobs/{job_id}/cancel").status_code == 409


def test_get_job_includes_progress(tmp_path):
    app, store = _app(tmp_path)
    client = TestClient(app)
    job_id = _submit(client).json()["job"]["id"]
    (store.dir(job_id) / "progress.jsonl").write_text(
        json.dumps({"seq": 1, "ts": 1.0, "ev": "plan",
                    "units": [{"key": "doc:0", "label": "ref.xlsx", "kind": "doc"}]}) + "\n",
        encoding="utf-8")
    body = client.get(f"/api/jobs/{job_id}").json()
    assert body["progress"]["total"] == 1 and body["state"] == J.QUEUED


def test_unknown_or_malformed_job_is_404(tmp_path):
    app, _ = _app(tmp_path)
    client = TestClient(app)
    for bad in ("20260929-000000-ffff", "bad-id"):
        assert client.get(f"/api/jobs/{bad}").status_code == 404
        assert client.get(f"/api/jobs/{bad}/result").status_code == 404


def test_events_stream_for_a_finished_job(tmp_path):
    app, store = _app(tmp_path)
    client = TestClient(app)
    job_id = _submit(client).json()["job"]["id"]
    job = store.load(job_id)
    job.state = J.SUCCEEDED
    store.save(job)
    (store.dir(job_id) / "console.log").write_text("완료\n", encoding="utf-8")
    res = client.get(f"/api/jobs/{job_id}/events")
    assert res.headers["content-type"].startswith("text/event-stream")
    assert "event: log" in res.text and "event: end" in res.text


def test_result_and_report(tmp_path):
    app, store = _app(tmp_path)
    client = TestClient(app)
    job_id = _submit(client).json()["job"]["id"]
    assert client.get(f"/api/jobs/{job_id}/result").status_code == 404
    (store.dir(job_id) / "result.json").write_text('{"engine": "fact"}', encoding="utf-8")
    assert client.get(f"/api/jobs/{job_id}/result").json() == {
        "result": {"engine": "fact"}, "report": False}
    (store.dir(job_id) / "report.md").write_text("# 리포트", encoding="utf-8")
    res = client.get(f"/api/jobs/{job_id}/report")
    assert res.text == "# 리포트" and "attachment" in res.headers["content-disposition"]
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_web_app_jobs.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'app' from 'contentcompare.web'`

- [ ] **Step 3: 구현** — `contentcompare/web/app.py`

```python
"""FastAPI 앱 조립 — 작업·LLM 점검 라우트와 공용 의존성.

조회 라우트는 :mod:`.views`, 관리자는 :mod:`.admin_routes`, 정적 화면은 :mod:`.static` 에 있다
(Task 12·13). 여기서는 "상태 하나(:class:`AppState`)를 만들어 라우터들에 나눠 준다"만 한다.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import shutil
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import PlainTextResponse, StreamingResponse

from .. import progress as prog
from ..config import AppConfig
from ..llm.health import check_llm
from .admin import AdminAuth
from .events import Cursor, stream
from .jobs import ENGINES, Job, JobStore, new_job_id, purge_expired
from .launcher import default_office_guard, process_launcher
from .scheduler import JobScheduler
from .settings import WebSettings, build_app_config, public_llm_summary
from .uploads import UploadError, plan_upload, safe_relpath, save_stream

logger = logging.getLogger(__name__)

CLIENT_COOKIE = "cc_client"
ADMIN_COOKIE = "cc_admin"
_CLIENT_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


class LlmChecker:
    """LLM 연결 점검 — **30초 재사용, 동시에 1회**(설계 §6 #4).

    점검도 요청 한도를 먹는다. 여러 명이 동시에 누르면 실행 중인 작업이 429 를 맞는다.
    """

    def __init__(self, check: Callable[[], list], *, clock: Callable[[], float] = time.time,
                 ttl_s: float = 30.0) -> None:
        self._check = check
        self.clock = clock
        self.ttl_s = ttl_s
        self._lock = threading.Lock()
        self._cached: Optional[dict] = None
        self._at = 0.0

    def run(self) -> dict:
        with self._lock:
            now = self.clock()
            if self._cached is not None and now - self._at < self.ttl_s:
                return {**self._cached, "cached": True}
            results = self._check()
            payload = {
                "ok": all(r.ok for r in results),
                "results": [{"name": r.name, "ok": r.ok, "detail": r.detail, "line": r.line()}
                            for r in results],
            }
            self._cached, self._at = payload, now
            return {**payload, "cached": False}


@dataclass
class AppState:
    settings: WebSettings
    config: AppConfig
    store: JobStore
    scheduler: JobScheduler
    checker: Any
    auth: AdminAuth


def client_id(request: Request, response: Response) -> str:
    """브라우저 식별 쿠키 — '내 작업'·취소 권한용. **보안 장치가 아니라 편의 기능**이다."""
    cid = request.cookies.get(CLIENT_COOKIE, "")
    if not _CLIENT_RE.match(cid or ""):
        cid = secrets.token_urlsafe(16)
        response.set_cookie(CLIENT_COOKIE, cid, max_age=365 * 86400, httponly=True,
                            samesite="lax")
    return cid


def is_admin(state: AppState, request: Request) -> bool:
    return state.auth.verify(request.cookies.get(ADMIN_COOKIE))


def job_view(job: Job, cid: str, positions: dict[str, int]) -> dict:
    data = job.to_dict()
    data.pop("client_id", None)
    data["mine"] = job.client_id == cid
    data["position"] = positions.get(job.id)
    return data


def create_app(
    settings: WebSettings,
    *,
    config: Optional[AppConfig] = None,
    store: Optional[JobStore] = None,
    scheduler: Optional[JobScheduler] = None,
    checker: Any = None,
    auth: Optional[AdminAuth] = None,
    background: bool = True,
    static_dir: Optional[Path] = None,
) -> FastAPI:
    config = config or build_app_config(settings)
    store = store or JobStore(settings.jobs_dir)

    def housekeeping() -> None:
        removed = purge_expired(store, now=time.time(), days=settings.retention_days)
        if removed:
            logger.info("보관 기간이 지난 작업 %d건 삭제", len(removed))

    scheduler = scheduler or JobScheduler(
        store, process_launcher(), office=default_office_guard(), housekeeping=housekeeping)
    checker = checker or LlmChecker(lambda: check_llm(build_app_config(settings)))
    auth = auth or AdminAuth(settings.admin_password)
    state = AppState(settings, config, store, scheduler, checker, auth)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if background:
            recovered = scheduler.recover()
            if recovered:
                logger.warning("재시작으로 중단 처리한 작업: %s", recovered)
            scheduler.start()
        yield
        if background:
            scheduler.stop()

    app = FastAPI(title="ContentCompare", lifespan=lifespan)
    app.state.cc = state
    _register_job_routes(app, state)
    return app


def _job_or_404(state: AppState, job_id: str) -> Job:
    job = state.store.load(job_id)
    if job is None:
        raise HTTPException(404, "작업을 찾을 수 없습니다.")
    return job


def _register_job_routes(app: FastAPI, state: AppState) -> None:
    settings, store, scheduler = state.settings, state.store, state.scheduler

    @app.post("/api/llm/check")
    def llm_check() -> dict:
        return state.checker.run()

    @app.post("/api/jobs")
    def submit_job(
        engine: str = Form(...),
        requester: str = Form(""),
        reference: UploadFile = File(...),
        targets: list[UploadFile] = File(...),
        reference_path: str = Form(""),
        target_paths: Optional[list[str]] = Form(None),
        cid: str = Depends(client_id),
    ) -> dict:
        if engine not in ENGINES:
            raise HTTPException(400, f"알 수 없는 엔진입니다: {engine} (사용 가능: {', '.join(ENGINES)})")
        # 브라우저·클라이언트가 multipart filename 을 어떻게 싣든(한글·폴더 경로) 상대 경로는
        # 폼 필드가 우선이다 — filename 은 대체값.
        if target_paths is not None and len(target_paths) != len(targets):
            raise HTTPException(400, "target_paths 개수가 파일 개수와 다릅니다.")
        ref_name = reference_path or reference.filename or ""
        names = target_paths if target_paths is not None else [t.filename or "" for t in targets]
        try:
            plan = plan_upload(ref_name, names)
        except UploadError as exc:
            raise HTTPException(400, str(exc)) from None

        job_id = new_job_id()
        while store.dir(job_id).exists():  # 같은 초·같은 난수 — 남의 폴더를 덮지 않는다
            job_id = new_job_id()
        job_dir = store.dir(job_id)
        by_name = {safe_relpath(n): f for n, f in zip(names, targets)}
        try:
            job_dir.mkdir(parents=True)
            save_stream(reference.file, job_dir / "inputs" / "reference" / plan.reference,
                        settings.max_upload_bytes)
            for rel in plan.targets:
                save_stream(by_name[rel].file, job_dir / "inputs" / "targets" / rel,
                            settings.max_upload_bytes)
        except UploadError as exc:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise HTTPException(400, str(exc)) from None

        job = Job(id=job_id, engine=engine, requester=requester.strip()[:40], client_id=cid,
                  reference=f"reference/{plan.reference}",
                  targets=[f"targets/{rel}" for rel in plan.targets],
                  llm=public_llm_summary(state.config))
        scheduler.submit(job)
        logger.info("작업 접수: %s (%s, 대상 %d개, 요청자 %s)", job.id, engine,
                    len(plan.targets), job.requester or "-")
        return {"job": job_view(job, cid, scheduler.positions()), "skipped": plan.skipped}

    @app.get("/api/jobs")
    def list_jobs(cid: str = Depends(client_id)) -> dict:
        positions = scheduler.positions()
        recent = sorted(store.list(), key=lambda j: j.id, reverse=True)[:50]
        return {"jobs": [job_view(j, cid, positions) for j in recent],
                "running": scheduler.running_id}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str, cid: str = Depends(client_id)) -> dict:
        job = _job_or_404(state, job_id)
        snap = prog.summarize(prog.load_events(store.dir(job_id) / "progress.jsonl"))
        return {**job_view(job, cid, scheduler.positions()), "progress": snap.to_dict()}

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str, request: Request, cid: str = Depends(client_id)) -> dict:
        job = _job_or_404(state, job_id)
        if job.client_id != cid and not is_admin(state, request):
            raise HTTPException(403, "이 작업을 요청한 브라우저 또는 관리자만 취소할 수 있습니다.")
        if not scheduler.cancel(job_id):
            raise HTTPException(409, "이미 끝난 작업입니다.")
        return {"job": job_view(store.load(job_id), cid, scheduler.positions())}

    @app.get("/api/jobs/{job_id}/events")
    def job_events(job_id: str, request: Request) -> StreamingResponse:
        _job_or_404(state, job_id)
        cursor = Cursor.parse(request.headers.get("last-event-id"))
        gen = stream(lambda: store.load(job_id), store.dir(job_id), cursor,
                     stall_after_s=settings.stall_warn_min * 60)
        return StreamingResponse(gen, media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no"})

    @app.get("/api/jobs/{job_id}/result")
    def job_result(job_id: str) -> dict:
        _job_or_404(state, job_id)
        path = store.dir(job_id) / "result.json"
        if not path.is_file():
            raise HTTPException(404, "아직 결과가 없습니다.")
        return {"result": json.loads(path.read_text(encoding="utf-8")),
                "report": (store.dir(job_id) / "report.md").is_file()}

    @app.get("/api/jobs/{job_id}/report")
    def job_report(job_id: str) -> PlainTextResponse:
        _job_or_404(state, job_id)
        path = store.dir(job_id) / "report.md"
        if not path.is_file():
            raise HTTPException(404, "리포트가 없습니다.")
        return PlainTextResponse(
            path.read_text(encoding="utf-8"), media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="report_{job_id}.md"'})
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_web_app_jobs.py -q -p no:cacheprovider`
Expected: 17 passed

- [ ] **Step 5: 커밋**

```bash
git add contentcompare/web/app.py tests/test_web_app_jobs.py
git commit -m "feat(web): 작업 API — 업로드·대기열·내 작업·취소 권한·SSE·결과

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_015WPCPHZGMqNLc7QQAv1ako"
```

---

### Task 12: 조회 라우트 — 리포트·현미경·타임라인·도메인 지식

**Files:**
- Create: `contentcompare/web/views.py`
- Modify: `contentcompare/web/app.py` (`create_app` 에서 라우터 포함)
- Create: `tests/test_web_views.py`

**Interfaces:**
- Consumes: Task 11 `AppState`; `report.list_reports/read_report`; `fact.artifact_reader.list_runs/load_snapshot`; `ui.micro_world.render_learn_html/render_debug_html/RESULT_ORDER/RESULT_LABEL`; `timeline.list_timelines/load_timeline/timeline_dir/ERROR_STATUSES`; `ui.timeline_view.render_timeline_html`; `knowledge.TEMPLATE/load_knowledge`.
- Produces: `build_views_router(state) -> APIRouter`. 라우트:
  - `GET /api/reports` → `{"reports":[{id,name,mtime}]}`, `GET /api/reports/content?id=` → `{"id","markdown"}`
  - `GET /api/micro/runs` → `{"runs":[{id,label,snapshot}]}`, `GET /api/micro/options?run=` → `{reference_doc,target_docs,learn_docs,facts:{doc:[{id,name}]},capabilities,problems,result_choices:[{key,label}],default_results}`, `GET /api/micro/html?run=&mode=debug|learn&target=&results=a,b&doc=&fact=&theme=light|dark` → `{"html","height","notes","unavailable"}`
  - `GET /api/timelines` → `{"timelines":[{id,label}]}`, `GET /api/timelines/html?run=&errors_only=` → `{"html"}`
  - `GET /api/knowledge/files` → `{"files":[{name,mtime,size}],"enabled","template"}`, `GET /api/knowledge/files/{name}`, `PUT /api/knowledge/files/{name}` 본문 `{"content","base_mtime"}`(충돌 409 `{"detail":{"message","current":{"content","mtime"}}}`), `GET /api/knowledge/merged` → `{"text"}`
  - ID 규칙: 리포트 `job/<작업ID>`·`reports/<파일>`, 현미경 `job/<작업ID>/<라벨>`·`artifacts/<라벨>`, 타임라인 `job/<작업ID>/<파일stem>`·`legacy/<파일stem>` — **목록에 있는 것만** 연다

- [ ] **Step 1: 실패하는 테스트** — `tests/test_web_views.py`

```python
"""조회 API — 목록에 있는 것만 연다, 지식 저장은 충돌을 알린다."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("python_multipart")
from fastapi.testclient import TestClient  # noqa: E402

from contentcompare.config import AppConfig  # noqa: E402
from contentcompare.web import app as web_app  # noqa: E402
from contentcompare.web import jobs as J  # noqa: E402
from contentcompare.web.admin import AdminAuth  # noqa: E402
from contentcompare.web.scheduler import JobScheduler  # noqa: E402
from contentcompare.web.settings import WebSettings  # noqa: E402

JOB_ID = "20260929-140211-a3f9"


def _app(tmp_path):
    settings = WebSettings(jobs_dir=str(tmp_path / "jobs"))
    config = AppConfig()
    config.report.output_dir = str(tmp_path / "reports")
    config.fact.artifacts_dir = str(tmp_path / "artifacts")
    config.knowledge.dir = str(tmp_path / "knowledge")
    store = J.JobStore(settings.jobs_dir)
    store.save(J.Job(id=JOB_ID, engine="fact", requester="홍길동", state=J.SUCCEEDED))
    app = web_app.create_app(settings, config=config, store=store,
                             scheduler=JobScheduler(store, lambda j, d: None),
                             checker=object(), auth=AdminAuth("pw"), background=False,
                             static_dir=tmp_path / "없는dist")
    return TestClient(app), store, config


def test_reports_list_job_and_legacy_and_read(tmp_path):
    client, store, config = _app(tmp_path)
    (store.dir(JOB_ID) / "report.md").write_text("# 작업 리포트", encoding="utf-8")
    legacy = tmp_path / "reports"
    legacy.mkdir()
    (legacy / "report_old.md").write_text("# 옛 리포트", encoding="utf-8")
    ids = [r["id"] for r in client.get("/api/reports").json()["reports"]]
    assert set(ids) == {f"job/{JOB_ID}", "reports/report_old.md"}
    assert client.get("/api/reports/content", params={"id": f"job/{JOB_ID}"}).json()[
        "markdown"] == "# 작업 리포트"
    for bad in ("reports/../x.md", "job/../../x", "reports/없음.md", "etc/passwd"):
        assert client.get("/api/reports/content", params={"id": bad}).status_code == 404


def _make_fact_run(root):
    """fact 파이프라인 스모크로 실제 산출물(comparison_result.json 포함)을 만든다."""
    from test_fact_pipeline_smoke import _excel_or_ppt, _pipe, _ppt_chat
    _pipe(root, extractor=_excel_or_ppt, chat=_ppt_chat()).run("기준.xlsx", ["발표.pptx"])


def test_micro_runs_options_and_html(tmp_path):
    client, store, _ = _app(tmp_path)
    _make_fact_run(store.dir(JOB_ID) / "artifacts")
    runs = client.get("/api/micro/runs").json()["runs"]
    run_id = next(r["id"] for r in runs if r["id"].startswith(f"job/{JOB_ID}/"))
    options = client.get("/api/micro/options", params={"run": run_id}).json()
    assert options["reference_doc"] == "기준.xlsx"
    assert options["target_docs"] == ["발표.pptx"]
    assert options["default_results"] == ["mismatch", "unknown", "missing"]
    debug = client.get("/api/micro/html", params={
        "run": run_id, "mode": "debug", "results": "mismatch,unknown,missing"}).json()
    assert "<" in debug["html"] and debug["height"] > 0
    learn = client.get("/api/micro/html", params={
        "run": run_id, "mode": "learn", "doc": "기준.xlsx"}).json()
    assert learn["html"] or learn["unavailable"]
    assert client.get("/api/micro/options", params={"run": "artifacts/../x"}).status_code == 404


def test_timelines_list_and_render(tmp_path):
    client, store, _ = _app(tmp_path)
    tl = store.dir(JOB_ID) / "artifacts" / "_timeline"
    tl.mkdir(parents=True)
    (tl / "timeline.jsonl").write_text(
        json.dumps({"ts": 1.0, "kind": "stage_start", "name": "F2 records · 기준.xlsx"}) + "\n",
        encoding="utf-8")
    items = client.get("/api/timelines").json()["timelines"]
    assert items[0]["id"] == f"job/{JOB_ID}/timeline"
    html = client.get("/api/timelines/html",
                      params={"run": items[0]["id"], "errors_only": "false"}).json()["html"]
    assert "F2 records" in html
    assert client.get("/api/timelines/html", params={"run": "legacy/없음"}).status_code == 404


def test_knowledge_create_read_conflict_update(tmp_path):
    client, _, _ = _app(tmp_path)
    listing = client.get("/api/knowledge/files").json()
    assert listing["files"] == [] and listing["template"]
    created = client.put("/api/knowledge/files/용어.md",
                         json={"content": "formation = 화성", "base_mtime": None}).json()
    got = client.get("/api/knowledge/files/용어.md").json()
    assert got["content"] == "formation = 화성" and got["mtime"] == created["mtime"]
    stale = client.put("/api/knowledge/files/용어.md",
                       json={"content": "덮어쓰기", "base_mtime": created["mtime"] - 100})
    assert stale.status_code == 409
    assert stale.json()["detail"]["current"]["content"] == "formation = 화성"
    ok = client.put("/api/knowledge/files/용어.md",
                    json={"content": "formation = 배터리 화성 공정",
                          "base_mtime": created["mtime"]})
    assert ok.status_code == 200
    assert "배터리 화성 공정" in client.get("/api/knowledge/merged").json()["text"]


def test_knowledge_new_file_with_existing_name_conflicts(tmp_path):
    client, _, _ = _app(tmp_path)
    client.put("/api/knowledge/files/a.md", json={"content": "1", "base_mtime": None})
    assert client.put("/api/knowledge/files/a.md",
                      json={"content": "2", "base_mtime": None}).status_code == 409


def test_knowledge_name_gets_md_and_rejects_paths(tmp_path):
    client, _, _ = _app(tmp_path)
    saved = client.put("/api/knowledge/files/노트", json={"content": "x", "base_mtime": None})
    assert saved.json()["name"] == "노트.md"
    assert client.get("/api/knowledge/files/..%2Fx.md").status_code in (400, 404)
    assert client.get("/api/knowledge/files/.hidden.md").status_code == 400
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_web_views.py -q -p no:cacheprovider`
Expected: FAIL — `/api/reports` 가 404(라우트 없음)

- [ ] **Step 3: 구현** — `contentcompare/web/views.py`

```python
"""조회 라우트 — 리포트·현미경·타임라인·도메인 지식(설계 §8.2).

모두 **읽기 전용이라 동시에 써도 된다**. 예외는 도메인 지식 편집 하나라 저장할 때 편집 시작
시각(파일 mtime)을 대조해 충돌을 알린다(설계 §8.3).

파일을 여는 모든 경로는 **목록 함수가 돌려준 것만** 연다 — ID 를 경로로 조립하지 않는다.
그래서 ``..`` 같은 조작은 "목록에 없음"으로 끝난다. 현미경·타임라인 HTML 은 기존 순수 함수
(:mod:`contentcompare.ui.micro_world`·:mod:`contentcompare.ui.timeline_view`) 결과를 그대로
돌려주고 화면이 iframe 에 넣는다(UI 3층 분리).
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import knowledge as kb
from ..fact.artifact_reader import RunRef, list_runs, load_snapshot
from ..report import list_reports, read_report
from ..timeline import ERROR_STATUSES, list_timelines, load_timeline, timeline_dir
from ..ui import micro_world
from ..ui.timeline_view import render_timeline_html

DEFAULT_RESULTS = ["mismatch", "unknown", "missing"]


class KnowledgeSave(BaseModel):
    content: str
    base_mtime: Optional[float] = None


def build_views_router(state) -> APIRouter:
    router = APIRouter()
    store, config = state.store, state.config
    knowledge_lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # 리포트
    # ------------------------------------------------------------------ #
    def report_index() -> dict[str, tuple[Path, str]]:
        out: dict[str, tuple[Path, str]] = {}
        for job in store.list():
            path = store.dir(job.id) / "report.md"
            if path.is_file():
                out[f"job/{job.id}"] = (path, f"{job.id} · {job.requester or '이름 없음'} · {job.engine}")
        for raw in list_reports(config.report.output_dir):
            path = Path(raw)
            out[f"reports/{path.name}"] = (path, path.name)
        return out

    @router.get("/api/reports")
    def reports() -> dict:
        items = [{"id": rid, "name": name, "mtime": path.stat().st_mtime}
                 for rid, (path, name) in report_index().items()]
        items.sort(key=lambda i: i["mtime"], reverse=True)
        return {"reports": items}

    @router.get("/api/reports/content")
    def report_content(id: str) -> dict:
        found = report_index().get(id)
        if found is None:
            raise HTTPException(404, "리포트를 찾을 수 없습니다.")
        return {"id": id, "markdown": read_report(str(found[0]))}

    # ------------------------------------------------------------------ #
    # 현미경
    # ------------------------------------------------------------------ #
    def micro_index() -> dict[str, tuple[RunRef, str]]:
        out: dict[str, tuple[RunRef, str]] = {}
        for job in sorted(store.list(), key=lambda j: j.id, reverse=True):
            for ref in list_runs(store.dir(job.id) / "artifacts"):
                out[f"job/{job.id}/{ref.label}"] = (ref, f"{job.id} · {job.requester or '이름 없음'} · {ref.label}")
        for ref in list_runs(config.fact.artifacts_dir):
            label = ref.label + (" (스냅샷)" if ref.is_snapshot else "")
            out[f"artifacts/{ref.label}"] = (ref, label)
        return out

    def snapshot_or_404(run: str):
        found = micro_index().get(run)
        if found is None:
            raise HTTPException(404, "실행을 찾을 수 없습니다.")
        return load_snapshot(found[0])

    @router.get("/api/micro/runs")
    def micro_runs() -> dict:
        return {"runs": [{"id": rid, "label": label, "snapshot": ref.is_snapshot}
                         for rid, (ref, label) in micro_index().items()]}

    @router.get("/api/micro/options")
    def micro_options(run: str) -> dict:
        snap = snapshot_or_404(run)
        learn_docs = [d for d in snap.docs if snap.doc(d) and "facts" in snap.doc(d).available]
        facts = {}
        for doc in learn_docs:
            items = snap.facts_of(doc)
            facts[doc] = [{"id": fid, "name": items[fid].get("entity_name") or fid}
                          for fid in items]
        return {
            "reference_doc": snap.reference_doc,
            "target_docs": list(snap.target_docs),
            "learn_docs": learn_docs,
            "facts": facts,
            "capabilities": sorted(snap.capabilities),
            "problems": list(snap.problems),
            "result_choices": [{"key": r, "label": micro_world.RESULT_LABEL[r][0]}
                               for r in micro_world.RESULT_ORDER],
            "default_results": DEFAULT_RESULTS,
        }

    @router.get("/api/micro/html")
    def micro_html(run: str, mode: str = "debug", target: str = "",
                   results: str = ",".join(DEFAULT_RESULTS), doc: str = "", fact: str = "",
                   theme: str = "light") -> dict:
        snap = snapshot_or_404(run)
        theme = theme if theme in ("light", "dark") else "light"
        if mode == "learn":
            if "learn" not in snap.capabilities:
                return {"html": "", "height": 0, "notes": list(snap.problems), "unavailable": True}
            rendered = micro_world.render_learn_html(snap, doc_name=doc, fact_id=fact, theme=theme)
        else:
            wanted = [r for r in results.split(",") if r]
            rendered = micro_world.render_debug_html(snap, target_doc=target, results=wanted,
                                                     theme=theme)
        return {"html": rendered.html, "height": rendered.height,
                "notes": list(rendered.notes), "unavailable": False}

    # ------------------------------------------------------------------ #
    # 타임라인
    # ------------------------------------------------------------------ #
    def timeline_index() -> dict[str, tuple[Path, str]]:
        out: dict[str, tuple[Path, str]] = {}
        for job in store.list():
            for path in list_timelines(store.dir(job.id) / "artifacts" / "_timeline"):
                out[f"job/{job.id}/{path.stem}"] = (path, f"{job.id} · {job.requester or '이름 없음'}")
        for path in list_timelines(timeline_dir(config)):
            out[f"legacy/{path.stem}"] = (path, path.stem)
        return out

    @router.get("/api/timelines")
    def timelines() -> dict:
        items = [{"id": tid, "label": label, "mtime": path.stat().st_mtime}
                 for tid, (path, label) in timeline_index().items()]
        items.sort(key=lambda i: i["mtime"], reverse=True)
        return {"timelines": items}

    @router.get("/api/timelines/html")
    def timeline_html(run: str, errors_only: bool = False) -> dict:
        found = timeline_index().get(run)
        if found is None:
            raise HTTPException(404, "타임라인을 찾을 수 없습니다.")
        path, label = found
        events = load_timeline(path)
        if errors_only:
            events = [e for e in events
                      if e.status in ERROR_STATUSES or e.kind in ("retry", "wait")]
        return {"html": render_timeline_html(events, title=label)}

    # ------------------------------------------------------------------ #
    # 도메인 지식
    # ------------------------------------------------------------------ #
    kdir = Path(config.knowledge.dir)

    def knowledge_name(name: str) -> str:
        raw = (name or "").strip()
        base = os.path.basename(raw)
        if not base or base != raw or base.startswith(".") or "\\" in raw:
            raise HTTPException(400, "파일 이름만 쓸 수 있습니다(폴더·숨김 파일 불가).")
        return base if base.lower().endswith(".md") else base + ".md"

    @router.get("/api/knowledge/files")
    def knowledge_files() -> dict:
        files = []
        for raw in kb.list_knowledge_files(str(kdir)):
            st = Path(raw).stat()
            files.append({"name": Path(raw).name, "mtime": st.st_mtime, "size": st.st_size})
        return {"files": files, "enabled": config.knowledge.enabled, "template": kb.TEMPLATE}

    @router.get("/api/knowledge/files/{name}")
    def knowledge_file(name: str) -> dict:
        path = kdir / knowledge_name(name)
        if not path.is_file():
            raise HTTPException(404, "지식 파일이 없습니다.")
        return {"name": path.name, "content": path.read_text(encoding="utf-8", errors="replace"),
                "mtime": path.stat().st_mtime}

    @router.put("/api/knowledge/files/{name}")
    def knowledge_save(name: str, body: KnowledgeSave) -> dict:
        path = kdir / knowledge_name(name)
        with knowledge_lock:
            current = path.stat().st_mtime if path.is_file() else None
            if current is not None and (body.base_mtime is None
                                        or abs(current - body.base_mtime) > 1e-3):
                raise HTTPException(409, {
                    "message": "다른 사람이 먼저 저장했습니다. 현재 내용을 확인한 뒤 다시 저장하세요.",
                    "current": {"content": path.read_text(encoding="utf-8", errors="replace"),
                                "mtime": current},
                })
            kdir.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(body.content, encoding="utf-8")
            os.replace(tmp, path)
            return {"name": path.name, "mtime": path.stat().st_mtime}

    @router.get("/api/knowledge/merged")
    def knowledge_merged() -> dict:
        return {"text": kb.load_knowledge(str(kdir), max_chars=config.knowledge.max_chars)}

    return router
```

`contentcompare/web/app.py` — import 블록에 추가:

```python
from .views import build_views_router
```

`create_app` 의 `_register_job_routes(app, state)` 줄 **아래**에 추가:

```python
    app.include_router(build_views_router(state))
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_web_views.py tests/test_web_app_jobs.py -q -p no:cacheprovider`
Expected: 전부 PASS

- [ ] **Step 5: 커밋**

```bash
git add contentcompare/web/views.py contentcompare/web/app.py tests/test_web_views.py
git commit -m "feat(web): 조회 API — 리포트·현미경·타임라인은 목록에 있는 것만, 지식 저장은 충돌 알림

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_015WPCPHZGMqNLc7QQAv1ako"
```

---

### Task 13: 관리자 라우트·정적 화면·서버 기동

**Files:**
- Create: `contentcompare/web/admin_routes.py`
- Create: `contentcompare/web/static.py`
- Create: `contentcompare/web/__main__.py`
- Modify: `contentcompare/web/app.py` (관리자 라우터·정적 화면 포함)
- Modify: `CLAUDE.md` (「### 진입점」 절에 웹 서버 항목, 새 절 「### 웹 서버」)
- Modify: `docs/superpowers/specs/2026-09-29-web-frontend-design.md` (§9 API 표를 실제 경로로)
- Create: `tests/test_web_admin_routes.py`

**Interfaces:**
- Consumes: Task 10 `AdminAuth`, `AuthError`, `DISABLED/LOCKED/WRONG`, `list_log_sources`, `resolve_source`, `read_records`, `filter_records`, `page`; Task 11 `AppState`, `ADMIN_COOKIE`, `create_app`, `job_view`.
- Produces:
  - `build_admin_router(state) -> APIRouter` — `GET /api/admin/status`, `POST /api/admin/login`(`{"password"}`), `POST /api/admin/logout`, `GET /api/admin/logs`, `GET /api/admin/logs/read?source=&level=&q=&tail=&before=`, `GET /api/admin/logs/download?source=`, `GET /api/admin/jobs`, `POST /api/admin/jobs/{id}/cancel`, `DELETE /api/admin/jobs/{id}`
  - `mount_static(app, static_dir: Path | None) -> None` — `web/dist` 가 있으면 SPA 서빙(`/api/*` 제외), 없으면 `/` 에 빌드 안내
  - `access_urls(host: str, port: int) -> list[str]`, `configure_server_logging(logs_dir: Path) -> Path`, `main(argv=None) -> int`

- [ ] **Step 1: 실패하는 테스트** — `tests/test_web_admin_routes.py`

```python
"""관리자 API·정적 화면·접속 주소."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("python_multipart")
from fastapi.testclient import TestClient  # noqa: E402

from contentcompare.web import app as web_app  # noqa: E402
from contentcompare.web import jobs as J  # noqa: E402
from contentcompare.web.__main__ import access_urls  # noqa: E402
from contentcompare.web.admin import AdminAuth  # noqa: E402
from contentcompare.web.scheduler import JobScheduler  # noqa: E402
from contentcompare.web.settings import WebSettings  # noqa: E402

JOB_ID = "20260929-140211-a3f9"
LOG = ("2026-09-29 14:02:11,001 INFO contentcompare: 시작\n"
       "2026-09-29 14:02:12,002 DEBUG contentcompare.llm: 프롬프트 원문\n")


def _app(tmp_path, password="pw", static_dir=None):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "server.log").write_bytes(LOG.encode("utf-8"))
    settings = WebSettings(jobs_dir=str(tmp_path / "jobs"), logs_dir=str(logs),
                           admin_password=password)
    store = J.JobStore(settings.jobs_dir)
    store.save(J.Job(id=JOB_ID, engine="fact", client_id="someone-else-000000",
                     state=J.SUCCEEDED, finished_ts=1.0))
    (store.dir(JOB_ID) / "contentcompare_x.log").write_bytes(LOG.encode("utf-8"))
    app = web_app.create_app(settings, store=store,
                             scheduler=JobScheduler(store, lambda j, d: None),
                             checker=object(), auth=AdminAuth(password), background=False,
                             static_dir=static_dir or tmp_path / "없는dist")
    return TestClient(app), store


def _login(client):
    assert client.post("/api/admin/login", json={"password": "pw"}).status_code == 200


def test_status_and_disabled(tmp_path):
    client, _ = _app(tmp_path, password="")
    assert client.get("/api/admin/status").json() == {"enabled": False, "logged_in": False}
    assert client.post("/api/admin/login", json={"password": ""}).status_code == 403


def test_wrong_password_then_lockout(tmp_path):
    client, _ = _app(tmp_path)
    for _ in range(5):
        assert client.post("/api/admin/login", json={"password": "x"}).status_code == 401
    assert client.post("/api/admin/login", json={"password": "pw"}).status_code == 429


def test_logs_need_login(tmp_path):
    client, _ = _app(tmp_path)
    assert client.get("/api/admin/logs").status_code == 401
    assert client.get("/api/admin/logs/read", params={"source": "logs/server.log"}).status_code == 401


def test_logs_list_read_filter_download(tmp_path):
    client, _ = _app(tmp_path)
    _login(client)
    assert client.get("/api/admin/status").json()["logged_in"] is True
    ids = {s["id"] for s in client.get("/api/admin/logs").json()["sources"]}
    assert ids == {"logs/server.log", f"job/{JOB_ID}/contentcompare_x.log"}
    page = client.get("/api/admin/logs/read",
                      params={"source": f"job/{JOB_ID}/contentcompare_x.log",
                              "level": "INFO"}).json()
    assert [r["level"] for r in page["records"]] == ["INFO"] and page["total"] == 1
    everything = client.get("/api/admin/logs/read",
                            params={"source": "logs/server.log", "q": "프롬프트"}).json()
    assert everything["total"] == 1
    download = client.get("/api/admin/logs/download", params={"source": "logs/server.log"})
    assert download.text == LOG
    assert client.get("/api/admin/logs/read",
                      params={"source": "logs/../x.log"}).status_code == 404


def test_logout_revokes(tmp_path):
    client, _ = _app(tmp_path)
    _login(client)
    client.post("/api/admin/logout")
    assert client.get("/api/admin/logs").status_code == 401


def test_admin_can_cancel_someone_elses_job(tmp_path):
    client, store = _app(tmp_path)
    queued = J.Job(id="20260929-140300-0001", engine="fact", client_id="other-client-0000000")
    store.save(queued)
    assert client.post(f"/api/jobs/{queued.id}/cancel").status_code == 403
    _login(client)
    assert client.post(f"/api/jobs/{queued.id}/cancel").status_code == 200


def test_admin_jobs_list_and_delete(tmp_path):
    client, store = _app(tmp_path)
    _login(client)
    jobs = client.get("/api/admin/jobs").json()["jobs"]
    assert jobs[0]["id"] == JOB_ID and jobs[0]["client_id"] == "someone-else-000000"
    queued = J.Job(id="20260929-140300-0001", engine="fact")
    store.save(queued)
    assert client.delete(f"/api/admin/jobs/{queued.id}").status_code == 409
    assert client.delete(f"/api/admin/jobs/{JOB_ID}").status_code == 200
    assert store.load(JOB_ID) is None


def test_not_built_page_when_dist_is_missing(tmp_path):
    client, _ = _app(tmp_path)
    res = client.get("/")
    assert res.status_code == 200 and "setup.bat" in res.text


def test_spa_serving_and_api_404(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<div id=root></div>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    client, _ = _app(tmp_path, static_dir=dist)
    assert client.get("/").text == "<div id=root></div>"
    assert client.get("/jobs/20260929-140211-a3f9").text == "<div id=root></div>"
    assert client.get("/assets/app.js").text == "console.log(1)"
    assert client.get("/api/없는경로").status_code == 404
    assert client.get("/..%2F..%2Fpyproject.toml").text == "<div id=root></div>"


def test_access_urls():
    assert access_urls("10.0.0.5", 8000) == ["http://10.0.0.5:8000"]
    urls = access_urls("0.0.0.0", 8123)
    assert urls[0] == "http://localhost:8123"
    assert all(u.endswith(":8123") for u in urls)
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_web_admin_routes.py -q -p no:cacheprovider`
Expected: FAIL — `ModuleNotFoundError: No module named 'contentcompare.web.__main__'`

- [ ] **Step 3: 구현** — `contentcompare/web/admin_routes.py`

```python
"""관리자 라우트 — 로그인·로그 조회·작업 관리(설계 §9·§12).

로그인은 HttpOnly 쿠키(``cc_admin``) 하나다. 로그 API 는 :func:`resolve_source` 가 허용한
파일만 열고, 크기가 커도 끝부분만 읽는다(전체는 다운로드).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .admin import (DISABLED, LOCKED, WRONG, AuthError, filter_records, list_log_sources,
                    page, read_records, resolve_source)

_STATUS = {DISABLED: 403, LOCKED: 429, WRONG: 401}


class LoginBody(BaseModel):
    password: str = ""


def build_admin_router(state, *, admin_cookie: str) -> APIRouter:
    router = APIRouter(prefix="/api/admin")
    logs_dir = Path(state.settings.logs_dir)
    jobs_root = state.store.root

    def require_admin(request: Request) -> None:
        if not state.auth.verify(request.cookies.get(admin_cookie)):
            raise HTTPException(401, "관리자 로그인이 필요합니다.")

    def source_or_404(source: str) -> Path:
        try:
            return resolve_source(source, logs_dir, jobs_root)
        except KeyError:
            raise HTTPException(404, "로그를 찾을 수 없습니다.") from None

    @router.get("/status")
    def status(request: Request) -> dict:
        return {"enabled": state.auth.enabled,
                "logged_in": state.auth.verify(request.cookies.get(admin_cookie))}

    @router.post("/login")
    def login(body: LoginBody, response: Response) -> dict:
        try:
            token = state.auth.login(body.password)
        except AuthError as exc:
            raise HTTPException(_STATUS[exc.reason], str(exc)) from None
        response.set_cookie(admin_cookie, token, httponly=True, samesite="strict",
                            max_age=int(state.auth.session_ttl_s))
        return {"ok": True}

    @router.post("/logout")
    def logout(request: Request, response: Response) -> dict:
        state.auth.logout(request.cookies.get(admin_cookie))
        response.delete_cookie(admin_cookie)
        return {"ok": True}

    @router.get("/logs", dependencies=[Depends(require_admin)])
    def logs() -> dict:
        return {"sources": [s.to_dict() for s in list_log_sources(logs_dir, jobs_root)]}

    @router.get("/logs/read", dependencies=[Depends(require_admin)])
    def read(source: str, level: str = "DEBUG", q: str = "", tail: int = 500,
             before: Optional[int] = None) -> dict:
        records = filter_records(read_records(source_or_404(source)), min_level=level, query=q)
        return page(records, tail=max(1, min(tail, 5000)), before=before)

    @router.get("/logs/download", dependencies=[Depends(require_admin)])
    def download(source: str) -> FileResponse:
        path = source_or_404(source)
        return FileResponse(path, filename=path.name, media_type="text/plain; charset=utf-8")

    @router.get("/jobs", dependencies=[Depends(require_admin)])
    def jobs() -> dict:
        positions = state.scheduler.positions()
        return {"jobs": [{**j.to_dict(), "position": positions.get(j.id)}
                         for j in sorted(state.store.list(), key=lambda j: j.id, reverse=True)]}

    @router.post("/jobs/{job_id}/cancel", dependencies=[Depends(require_admin)])
    def cancel(job_id: str) -> dict:
        if not state.scheduler.cancel(job_id):
            raise HTTPException(409, "취소할 수 없는 작업입니다(이미 끝났거나 없음).")
        return {"ok": True}

    @router.delete("/jobs/{job_id}", dependencies=[Depends(require_admin)])
    def delete(job_id: str) -> dict:
        job = state.store.load(job_id)
        if job is None:
            raise HTTPException(404, "작업을 찾을 수 없습니다.")
        if not job.is_final:
            raise HTTPException(409, "실행 중이거나 대기 중인 작업은 먼저 취소하세요.")
        state.store.delete(job_id)
        return {"ok": True}

    return router
```

`contentcompare/web/static.py`:

```python
"""빌드된 화면(`web/dist`) 서빙 — SPA 라 `/api/*` 가 아닌 모든 경로에 index.html.

빌드가 없으면 `/` 에 안내만 띄우고 API 는 그대로 동작한다(설계 §12).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

DEFAULT_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"

NOT_BUILT_HTML = """<!doctype html><meta charset="utf-8"><title>ContentCompare</title>
<body style="font-family:sans-serif;max-width:40rem;margin:4rem auto;line-height:1.6">
<h1>화면이 아직 빌드되지 않았습니다</h1>
<p>서버(API)는 동작 중입니다. 화면을 쓰려면 저장소 폴더에서 <code>setup.bat</code> 을 실행해
<code>web/dist</code> 를 만든 뒤 서버를 다시 시작하세요.</p></body>"""


def mount_static(app: FastAPI, static_dir: Optional[Path] = None) -> None:
    dist = Path(static_dir) if static_dir else DEFAULT_DIST
    index = dist / "index.html"
    if not index.is_file():
        @app.get("/", include_in_schema=False)
        def not_built() -> HTMLResponse:
            return HTMLResponse(NOT_BUILT_HTML)
        return

    root = dist.resolve()
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path.startswith("api/") or path == "api":
            raise HTTPException(404, "없는 API 입니다.")
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and root in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(index)
```

`contentcompare/web/app.py` — import 블록에 추가:

```python
from .admin_routes import build_admin_router
from .static import mount_static
```

`create_app` 의 `app.include_router(build_views_router(state))` 줄 **아래**에 추가(정적 화면은 **반드시 마지막** — 모든 경로를 잡는 라우트라 앞에 두면 API 를 가린다):

```python
    app.include_router(build_admin_router(state, admin_cookie=ADMIN_COOKIE))
    mount_static(app, static_dir)
```

`contentcompare/web/__main__.py`:

```python
"""``python -m contentcompare.web`` — 웹 서버 기동(설계 §11).

uvicorn 은 **worker 1개**로 띄운다. 대기열 스케줄러가 서버 프로세스 안에 있어서 여럿 띄우면
"한 번에 1건"이 깨진다. Ctrl+C 로 끄면 lifespan 종료에서 실행 중인 작업을 ``interrupted``
로 남긴다.

⚠️ Office 자동화는 로그인된 데스크톱 세션이 필요하다 — Windows 서비스로 등록하지 말고
서버 PC 에 로그인한 계정에서 실행한다(설계 §6 #1).
"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
from pathlib import Path
from typing import Optional

from ..logging_setup import log_print, setup_console
from .app import create_app
from .settings import load_settings
from .worker import DOTENV_ENV

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_server_logging(logs_dir: Path) -> Path:
    """서버 로그는 `logs/server.log` 하나에 이어 쓴다(관리자 페이지의 첫 번째 소스)."""
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = logs_dir / "server.log"
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root = logging.getLogger()
    root.addHandler(handler)
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)
    setup_console(level=logging.INFO)
    return path


def access_urls(host: str, port: int) -> list[str]:
    if host not in ("0.0.0.0", "::", ""):
        return [f"http://{host}:{port}"]
    names = {socket.gethostname()}
    try:
        names.update(ip for ip in socket.gethostbyname_ex(socket.gethostname())[2]
                     if not ip.startswith("127."))
    except OSError:
        pass
    return [f"http://localhost:{port}"] + [f"http://{n}:{port}" for n in sorted(names)]


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m contentcompare.web",
                                     description="ContentCompare 웹 서버")
    parser.add_argument("--env", default=".env", help=".env 경로(기본: .env)")
    parser.add_argument("--host", default="", help="바인드 주소(기본: CC_HOST)")
    parser.add_argument("--port", type=int, default=0, help="포트(기본: CC_PORT)")
    args = parser.parse_args(argv)

    settings = load_settings(args.env)
    # worker 는 별도 프로세스라 같은 .env 를 읽도록 경로를 물려준다(launcher 가 os.environ 을 복사).
    os.environ[DOTENV_ENV] = str(Path(args.env).resolve())
    settings.host = args.host or settings.host
    settings.port = args.port or settings.port
    log_path = configure_server_logging(Path(settings.logs_dir))
    app = create_app(settings)

    log_print(f"서버 로그: {log_path}")
    for url in access_urls(settings.host, settings.port):
        log_print(f"접속 주소: {url}")
    if not settings.admin_enabled:
        log_print("관리자 기능 꺼짐: .env 의 CC_ADMIN_PASSWORD 를 설정하세요.")

    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port, workers=1, log_config=None)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_web_admin_routes.py tests/test_web_views.py tests/test_web_app_jobs.py -q -p no:cacheprovider`
Expected: 전부 PASS

- [ ] **Step 5: 기동 스모크(수동, 30초)**

Run: `python -m contentcompare.web --env 없음.env --port 8765` 를 띄운 채 다른 터미널에서 `python -c "import urllib.request as u; print(u.urlopen('http://localhost:8765/api/admin/status').read())"`
Expected: `b'{"enabled":false,"logged_in":false}'` 가 출력되고, 서버 창에 `접속 주소: http://localhost:8765` 가 보인다. Ctrl+C 로 종료했을 때 예외 없이 끝나고 `logs/server.log` 가 생긴다.

- [ ] **Step 6: 문서**

`CLAUDE.md` — 「### 진입점」 절의 Streamlit 항목 **위**에 추가:

```markdown
- **웹 서버** `python -m contentcompare.web`(FastAPI, `contentcompare/web/`): 여러 명이 브라우저로 접속해 업로드 → 대기열(한 번에 1건) → 별도 worker 프로세스 실행. 설계 `docs/superpowers/specs/2026-09-29-web-frontend-design.md`. 설정은 `.env`(견본 `.env.example`).
```

`CLAUDE.md` — 「### 진행률 (`progress.py`)」 절 **뒤**에 새 절:

```markdown
### 웹 서버 (`contentcompare/web/`)

**작업 1건 = worker 프로세스 1개, 서버와는 파일로만.** `jobs/<ID>/` 에 `job.json`(상태 원본)·`inputs/`·`artifacts/`·`console.log`(화면 로그, INFO)·`contentcompare_*.log`(DEBUG, 관리자 전용)·`progress.jsonl`·`result.json`·`error.json`·`report.md` 가 남는다. 프로세스 전역 상태(타임라인·로그·`disable_proxy()`·Office COM)를 작업 단위로 가두는 유일한 방법이라 **스레드로 바꾸지 말 것.**

- ⚠️ **동시 실행은 1건이고 uvicorn worker 도 1개다.** PowerPoint COM 은 `DispatchEx` 로도 PC 전체에 하나뿐이라 한 작업의 `Quit` 이 남의 PPT 를 닫고, `RateLimiter` 는 프로세스 안에만 있어 병렬이면 429 가 겹친다. 스케줄러가 서버 프로세스 안에 있으므로 uvicorn worker 를 늘리면 스케줄러도 늘어난다.
- **Windows 서비스로 등록하지 말 것** — Office 자동화는 로그인된 데스크톱 세션이 필요하다.
- **API 키를 작업 폴더에 남기지 않는다.** worker 가 `.env` 를 스스로 읽고, `job.json` 에는 `public_llm_summary()` 만 남는다. `.env` 키 → 설정 필드 매핑은 `settings.ENV_MAP` 한 곳이고 테스트가 경로 존재를 고정한다.
- 파일을 여는 조회 API 는 **목록 함수가 돌려준 ID 만** 연다(ID 를 경로로 조립하지 않는다). 작업 ID 는 정규식으로만 받는다.
- 업로드는 **같은 이름 문서를 거절한다** — `artifacts/<문서명>/` 이 겹쳐 결과가 조용히 섞인다. 폴더 업로드의 하위 경로는 `target_paths` 폼 필드로 받는다(multipart `filename` 은 대체값).
- 작업은 자동 종료하지 않는다(SDK 재시도까지 합치면 호출 하나가 최악 8분). 멈춤은 SSE `stall` 경고만. 서버 재시작 시 실행 중이던 작업은 `interrupted` 이고 다시 돌리지 않는다(LLM 비용).
- 테스트: FastAPI 를 쓰는 파일은 `pytest.importorskip("fastapi")` — `.venv` 에는 없어서 건너뛴다. worker 통합 테스트는 `CC_PIPELINE_FACTORY=web_fake_factory:make` 로 가짜 파이프라인을 주입해 실제 서브프로세스를 띄운다.
```

설계서 §9 — API 표에서 아래 세 줄을 교체(ID 에 `/` 가 들어가 경로 인자 대신 쿼리 인자를 쓴다):

| 기존 | 교체 |
|---|---|
| `GET /api/reports`, `GET /api/reports/{ref}` | `GET /api/reports`, `GET /api/reports/content?id=` |
| `GET /api/micro/runs`, `GET /api/micro/{run}/options`, `GET /api/micro/{run}/html` | `GET /api/micro/runs`, `GET /api/micro/options?run=`, `GET /api/micro/html?run=&mode=…` |
| `GET /api/timelines`, `GET /api/timelines/{run}/html` | `GET /api/timelines`, `GET /api/timelines/html?run=&errors_only=` |

그리고 `POST /api/jobs` 줄의 설명에 `선택 reference_path·target_paths[](폴더 경로 보존)` 를 덧붙이고, 관리자 줄에 `GET /api/admin/status`, `GET /api/admin/logs/download?source=` 를 더한다.

- [ ] **Step 7: 전체 테스트**

Run: `python -m pytest -q -p no:cacheprovider`
Expected: 전부 PASS(운영 파이썬). `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider` 는 기준선 5건 실패 외에 새 실패가 없어야 한다(FastAPI 테스트는 skip).

- [ ] **Step 8: 커밋**

```bash
git add contentcompare/web/admin_routes.py contentcompare/web/static.py contentcompare/web/__main__.py contentcompare/web/app.py tests/test_web_admin_routes.py CLAUDE.md docs/superpowers/specs/2026-09-29-web-frontend-design.md
git commit -m "feat(web): 관리자 API·정적 화면·서버 기동 — python -m contentcompare.web

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_015WPCPHZGMqNLc7QQAv1ako"
```

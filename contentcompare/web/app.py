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

from ..config import AppConfig
from ..llm.health import check_llm
from .admin import AdminAuth
from .admin_routes import build_admin_router
from .events import Cursor, astream, snapshot_for
from .instance_lock import acquire_instance_lock
from .jobs import ENGINES, Job, JobStore, new_job_id, purge_expired
from .launcher import default_office_guard, process_launcher
from .scheduler import JobScheduler
from .settings import WebSettings, build_app_config, public_llm_summary
from .static import mount_static
from .uploads import UploadError, plan_upload, resolve_under, safe_relpath, save_stream
from .views import build_views_router

logger = logging.getLogger(__name__)

CLIENT_COOKIE = "cc_client"
ADMIN_COOKIE = "cc_admin"
_CLIENT_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
LOCK_FILE = ".server.lock"


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
        if not background:
            yield
            return
        # recover() 보다 먼저 — 같은 jobs 폴더의 다른 서버가 돌리는 작업을 덮어쓰지 않게.
        lock = acquire_instance_lock(store.root / LOCK_FILE)
        try:
            recovered = scheduler.recover()
            if recovered:
                logger.warning("재시작으로 중단 처리한 작업: %s", recovered)
            scheduler.start()
            yield
        finally:
            try:
                scheduler.stop()
            finally:
                lock.release()  # worker 를 끝낸 뒤에 놓는다 — 다음 서버가 그 작업을 보기 전에

    app = FastAPI(title="ContentCompare", lifespan=lifespan)
    app.state.cc = state
    _register_job_routes(app, state)
    app.include_router(build_views_router(state))
    app.include_router(build_admin_router(state, admin_cookie=ADMIN_COOKIE))
    # 정적 화면은 반드시 마지막 — 모든 경로를 잡는 라우트라 앞에 두면 API 를 가린다.
    mount_static(app, static_dir)
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

        while True:  # 폴더를 만들어 id 를 선점한다 — exists() 확인 후 만들면 병렬 요청끼리 경쟁한다
            job_id = new_job_id()
            try:
                store.dir(job_id).mkdir(parents=True)
                break
            except FileExistsError:
                continue
        job_dir = store.dir(job_id)
        by_name = {safe_relpath(n): f for n, f in zip(names, targets)}
        try:
            save_stream(reference.file,
                        resolve_under(job_dir / "inputs" / "reference", plan.reference),
                        settings.max_upload_bytes)
            for rel in plan.targets:
                save_stream(by_name[rel].file,
                            resolve_under(job_dir / "inputs" / "targets", rel),
                            settings.max_upload_bytes)
        except UploadError as exc:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise HTTPException(400, str(exc)) from None
        except OSError as exc:  # 디스크 가득 등 — 반쯤 만든 작업 폴더를 남기지 않는다
            shutil.rmtree(job_dir, ignore_errors=True)
            logger.exception("업로드 저장 실패: %s", job_id)
            raise HTTPException(500, f"업로드를 저장하지 못했습니다: {type(exc).__name__}") from None

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
        snap = snapshot_for(store.dir(job_id) / "progress.jsonl")
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
        gen = astream(lambda: store.load(job_id), store.dir(job_id), cursor,
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

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

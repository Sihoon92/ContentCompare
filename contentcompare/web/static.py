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

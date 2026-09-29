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

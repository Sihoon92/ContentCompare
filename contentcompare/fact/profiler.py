"""Document Profiler (F1) — compact_raw → document_profile.

문서의 목적과 주요 구조(표 후보)를 LLM 으로 파악한다. 모든 doc_type 대상.
store 가 있으면 결과를 ``document_profile.json`` 으로 저장하고, 같은 입력이면
캐시(재실행 0비용)를 쓴다.
"""

from __future__ import annotations

import json
from typing import Optional

from .artifacts import ArtifactStore
from .llm_stage import LlmRunner, fingerprint_for
from .prompts import PROFILER_SYSTEM, PROFILER_VERSION, build_profiler_user
from .schema_models import DocumentProfile


def profile_document(
    compact: dict, runner: LlmRunner, store: Optional[ArtifactStore] = None
) -> DocumentProfile:
    """compact_raw dict → :class:`DocumentProfile`."""

    def compute() -> dict:
        obj = runner.complete_json(PROFILER_SYSTEM, build_profiler_user(compact))
        return DocumentProfile.from_llm(
            obj, fallback_doc_type=compact.get("doc_type", "")
        ).to_dict()

    if store is not None:
        fp = fingerprint_for(
            json.dumps(compact, sort_keys=True, ensure_ascii=False), PROFILER_VERSION
        )
        data = store.cached_or_compute("document_profile", compute, fingerprint=fp)
    else:
        data = compute()
    return DocumentProfile.from_dict(data)

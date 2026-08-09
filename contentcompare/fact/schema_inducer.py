"""Schema Inducer (F1) — compact_raw + document_profile → table_profile + column_schema.

엑셀 표의 (1) 헤더 구조, (2) row_grain, (3) 컬럼 semantic_role 을 LLM 으로 추론한다
(``readers/header_detect.py`` 의 헤더 판별 아이디어를 흡수·확장; 원본은 수정 안 함).

비용 절감을 위해 **1회 LLM 호출**로 table_profile + column_schema 를 동시에 산출하고
두 artifacts 로 나눠 저장한다(결정 #2). F1 은 데이터가 있는 **첫 시트 1개**만 처리한다.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from .artifacts import ArtifactStore
from .llm_stage import LlmRunner, fingerprint_for
from .prompts import SCHEMA_SYSTEM, SCHEMA_VERSION, build_schema_user
from .schema_models import ColumnSchema, DocumentProfile, TableProfile

logger = logging.getLogger(__name__)


def _primary_sheet(compact: dict) -> Optional[dict]:
    """데이터(rows)가 있는 첫 비숨김 시트. 없으면 None."""
    for sheet in compact.get("sheets", []):
        if sheet.get("rows") and not sheet.get("hidden"):
            return sheet
    return None


def induce_schema(
    compact: dict,
    profile: DocumentProfile,
    runner: LlmRunner,
    store: Optional[ArtifactStore] = None,
) -> tuple[TableProfile, ColumnSchema]:
    """엑셀 compact → (TableProfile, ColumnSchema). 표가 없으면 ValueError."""
    sheet = _primary_sheet(compact)
    if sheet is None:
        raise ValueError("스키마를 추론할 표(데이터 있는 시트)가 없습니다")

    sheets = compact.get("sheets", [])
    if len([s for s in sheets if s.get("rows")]) > 1:
        logger.warning("F1: 시트가 여러 개지만 첫 시트 '%s' 만 처리합니다", sheet.get("sheet_name"))

    location = f"sheet={sheet.get('sheet_name', '')}"
    fp = fingerprint_for(
        json.dumps(sheet, sort_keys=True, ensure_ascii=False),
        json.dumps(profile.to_dict(), sort_keys=True, ensure_ascii=False),
        SCHEMA_VERSION,
    )

    cs_holder: dict[str, ColumnSchema] = {}

    def compute_table_profile() -> dict:
        obj = runner.complete_json(SCHEMA_SYSTEM, build_schema_user(sheet, profile.to_dict()))
        tp = TableProfile.from_llm(obj.get("table_profile", {}), location=location)
        cs = ColumnSchema.from_llm(obj.get("column_schema", {}), location=location)
        cs_holder["cs"] = cs
        if store is not None:
            store.save("column_schema", cs.to_dict())  # 한 호출 결과의 두 번째 산출물
        return tp.to_dict()

    if store is not None:
        tp_dict = store.cached_or_compute("table_profile", compute_table_profile, fingerprint=fp)
        tp = TableProfile.from_dict(tp_dict)
        if "cs" in cs_holder:  # 방금 계산함
            cs = cs_holder["cs"]
        else:  # 캐시 히트 → 디스크에서 column_schema 로드
            loaded = store.load("column_schema")
            cs = ColumnSchema.from_dict(loaded) if loaded else ColumnSchema(location=location)
    else:
        tp = TableProfile.from_dict(compute_table_profile())
        cs = cs_holder["cs"]

    return tp, cs

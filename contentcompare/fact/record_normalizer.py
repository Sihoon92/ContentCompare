"""Record Normalizer (F2) — compact_raw + table_profile + column_schema → records.

데이터 행 전체를 LLM 이 의미 정규화해 record 리스트로 만든다. 행을 ``batch_rows`` 씩
끊어 호출하고, 배치 경계에서 상위 분류(category/subcategory)를 carry-over 로 잇는다.
``source.sheet``/``cell_range`` 는 코드가 채워 좌표 할루시네이션을 막는다(LLM 은 row 만).
시트 단위로 캐싱한다(재실행 0비용 — 결정 #2).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterator, Optional

from .artifacts import ArtifactStore
from .llm_stage import LlmRunner, fingerprint_for
from .prompts import RECORD_SYSTEM, RECORD_VERSION, build_record_user
from .record_models import Record, RecordSet
from .schema_models import ColumnSchema, TableProfile

logger = logging.getLogger(__name__)


def _primary_sheet(compact: dict) -> Optional[dict]:
    """데이터(rows)가 있는 첫 비숨김 시트(F1 schema_inducer 와 동일 규칙)."""
    for sheet in compact.get("sheets", []):
        if sheet.get("rows") and not sheet.get("hidden"):
            return sheet
    return None


def _data_start_row(tp: TableProfile) -> int:
    hs = tp.header_structure
    if hs.data_start_row is not None:
        return hs.data_start_row
    if hs.header_start_row is not None:
        return hs.header_start_row + (hs.header_rows or 1)
    return 1


def _chunks(seq: list, size: int) -> Iterator[list]:
    size = max(1, size)
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def _col_index(col: str) -> int:
    """엑셀 열문자 → 1-based 인덱스 (A=1, Z=26, AA=27). 비정상 문자는 뒤로."""
    idx = 0
    for ch in str(col).upper():
        if not ("A" <= ch <= "Z"):
            return 10 ** 9
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx


def _cell_range(row: dict, columns: list[str], r: int) -> str:
    """그 행에서 매핑된(존재하는) 열들의 최소~최대 열문자로 cell_range 생성."""
    cells = row.get("cells") or {}
    present = [c for c in columns if c in cells]
    if not present:
        return str(r)
    lo = min(present, key=_col_index)
    hi = max(present, key=_col_index)
    return f"{lo}{r}" if lo == hi else f"{lo}{r}:{hi}{r}"


def normalize_records(
    compact: dict,
    table_profile: TableProfile,
    column_schema: ColumnSchema,
    runner: LlmRunner,
    *,
    batch_rows: int = 30,
    store: Optional[ArtifactStore] = None,
) -> RecordSet:
    """엑셀 compact → :class:`RecordSet`. 데이터 행이 없으면 ValueError."""
    sheet = _primary_sheet(compact)
    if sheet is None:
        raise ValueError("정규화할 표(데이터 있는 시트)가 없습니다")
    sheet_name = sheet.get("sheet_name", "")
    start = _data_start_row(table_profile)
    data_rows = [r for r in sheet.get("rows", []) if (r.get("r") or 0) >= start]
    if not data_rows:
        raise ValueError("정규화할 데이터 행이 없습니다")

    location = f"sheet={sheet_name}"
    schema_columns = [c.column for c in column_schema.columns]
    fp = fingerprint_for(
        json.dumps(data_rows, sort_keys=True, ensure_ascii=False),
        json.dumps(column_schema.to_dict(), sort_keys=True, ensure_ascii=False),
        json.dumps(table_profile.to_dict(), sort_keys=True, ensure_ascii=False),
        str(batch_rows),
        RECORD_VERSION,
    )

    def compute() -> dict:
        records: list[Record] = []
        carry = {"category": "", "subcategory": ""}
        seq = 0  # record 전체 순번(배치 걸쳐 단조 증가) — row-less 폴백 id 생성용
        for batch in _chunks(data_rows, batch_rows):
            obj = runner.complete_json(
                RECORD_SYSTEM, build_record_user(batch, column_schema, table_profile, carry)
            )
            row_by_r = {r.get("r"): r for r in batch}
            batch_records: list[Record] = []
            for raw in (obj.get("records") or []):
                rec = Record.from_llm(raw, sheet_name=sheet_name, index=seq)
                seq += 1
                rec.source.sheet = sheet_name
                rec.source.cell_range = ""  # LLM 이 준 좌표는 신뢰하지 않음(코드가 채움)
                if rec.source.row is not None and rec.source.row in row_by_r:
                    rec.source.cell_range = _cell_range(
                        row_by_r[rec.source.row], schema_columns, rec.source.row
                    )
                batch_records.append(rec)
            # carry-over: 이 배치의 마지막 non-empty 분류를 다음 배치로 전달.
            for rec in batch_records:
                if rec.entity.category:
                    carry["category"] = rec.entity.category
                if rec.entity.subcategory:
                    carry["subcategory"] = rec.entity.subcategory
            records.extend(batch_records)
        return RecordSet(location=location, records=records).to_dict()

    if store is not None:
        data = store.cached_or_compute("records", compute, fingerprint=fp)
    else:
        data = compute()
    logger.info("[Fact] records: %s → %d records", location, len(data.get("records", [])))
    return RecordSet.from_dict(data)

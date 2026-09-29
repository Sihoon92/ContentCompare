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

from .. import progress as prog
from ..logging_setup import log_print
from ..llm.tracing import substage
from .artifacts import ArtifactStore
from .llm_stage import LlmRunner, fingerprint_for, run_batch
from .prompts import RECORD_SYSTEM, RECORD_VERSION, build_record_user
from .record_models import Record, RecordSet
from .schemas import schema_for
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


def _metadata_from_columns(names: Any, row: Optional[dict]) -> dict[str, Any]:
    """LLM 이 고른 메타 **열 이름** + 원본 행 → ``{열: 값}`` (record-v4).

    ``source.cell_range`` 를 코드가 채우는 것과 같은 대우다 — **LLM 은 어느 열을 볼지
    정하고, 값은 원문에서 온다.** 값까지 받으면 출력의 27.8%(실측 104행)를 아무도 읽지
    않을 데이터에 쓴다(``wire_models.WireRecord`` 참고).

    행에 없는 열은 버린다. 지어낸 이름에 넣을 값이 애초에 없고, ``None`` 으로 채우면
    "빈 셀"과 "없는 열"이 구별되지 않는다 — 보이지 않는 손실이 가장 나쁘다.
    """
    if not isinstance(names, list):
        return {}
    cells = (row or {}).get("cells") or {}
    out: dict[str, Any] = {}
    for name in names:
        key = _as_col(name)
        if key and key in cells:
            out[key] = cells[key]
    return out


def _as_col(name: Any) -> str:
    return name.strip() if isinstance(name, str) else ""


def normalize_records(
    compact: dict,
    table_profile: TableProfile,
    column_schema: ColumnSchema,
    runner: LlmRunner,
    *,
    batch_rows: int = 30,
    store: Optional[ArtifactStore] = None,
    stats: Optional[dict] = None,
) -> RecordSet:
    """엑셀 compact → :class:`RecordSet`. 데이터 행이 없으면 ValueError.

    ``stats`` 를 주면 계측값을 채운다(out-param, F3.5). ``rows_in`` 대비
    ``records_out`` 이 적으면 LLM 이 행을 통째로 흘린 것이고,
    ``records_without_row`` 는 좌표(cell_range) 추적에 실패한 record 수다 —
    둘 다 조용히 사라지는 손실이라 계측하지 않으면 보이지 않는다.
    """
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

    computed = {"ran": False}
    # 분할 계측. ``compute()`` 밖에 두는 이유는 캐시 히트면 아예 안 돌기 때문이다 —
    # 그때는 빈 dict 가 그대로 남아 "이번 실행에서는 분할이 없었다"를 정직하게 말한다.
    split: dict[str, int] = {}

    def compute() -> dict:
        computed["ran"] = True
        records: list[Record] = []
        carry = {"category": "", "subcategory": ""}
        seq = 0  # record 전체 순번(배치 걸쳐 단조 증가) — row-less 폴백 id 생성용
        batches = list(_chunks(data_rows, batch_rows))
        index = 0  # 아래 두 클로저가 함께 읽는다(현재 배치 번호)

        def label(rows: list, depth: int) -> str:
            """substage 이름. **깊이 0 은 오늘과 한 글자도 같다.**

            배치 번호를 이름에 넣는 것은 실패했을 때 "몇 번째에서 죽었나"를 ``run_stats``
            의 LLM 호출 수로 역산하지 않기 위해서다(실측에서 실제로 그렇게 했다).

            쪼개진 뒤에는 **행 범위**를 덧붙인다. ``a``/``b`` 접미어(3단이면 ``aa``)와 달리
            깊이가 몇이든 이름 길이가 일정하고, 이름 자체가 "무엇이 재시도됐는지"를 답한다
            — ``배치 3/7`` 만으로는 두 줄이 같은 30행을 가리키는 것처럼 보인다.
            """
            base = f"배치 {index}/{len(batches)}"
            if depth == 0 or not rows:
                return base
            return f"{base} 행 {rows[0].get('r')}-{rows[-1].get('r')}"

        def call(rows: list, name: str) -> None:
            """LLM 호출 + 후처리 + carry 갱신을 **한 덩어리로** 한다.

            후처리를 :func:`run_batch` 밖에 두면 안 된다 — carry 는 이 배치를 파싱한 뒤
            갱신되어 **다음 호출의 프롬프트**로 들어가므로, 쪼갠 두 조각을 다 부르고
            나서 병합하면 뒤 조각이 앞 조각의 분류를 못 받아 조용히 틀린다.
            """
            nonlocal seq
            with substage(name, rows=len(rows)):
                obj = runner.complete_json(
                    RECORD_SYSTEM,
                    build_record_user(rows, column_schema, table_profile, carry),
                    schema=schema_for("record"),
                )
            row_by_r = {r.get("r"): r for r in rows}
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
                # metadata 값도 코드가 원본 셀에서 채운다(record-v4). ``metadata_columns``
                # 가 없으면 손대지 않는다 — 구조화 출력을 끈 백엔드가 옛 모양(값이 담긴
                # ``metadata``)을 주면 ``from_llm`` 이 파싱한 것을 그대로 살린다.
                if isinstance(raw, dict) and "metadata_columns" in raw:
                    rec.metadata = _metadata_from_columns(
                        raw.get("metadata_columns"), row_by_r.get(rec.source.row)
                    )
                batch_records.append(rec)
            # carry-over: 이 배치의 마지막 non-empty 분류를 다음 배치로 전달.
            for rec in batch_records:
                if rec.entity.category:
                    carry["category"] = rec.entity.category
                if rec.entity.subcategory:
                    carry["subcategory"] = rec.entity.subcategory
            records.extend(batch_records)

        prog.step(0, len(batches))
        for index, batch in enumerate(batches, start=1):
            run_batch(batch, call, name=label, stats=split)
            # 원래 배치 번호로만 센다 — 쪼개진 조각은 run_batch 안에서 끝나므로 세지 않는다.
            prog.step(index, len(batches))
        return RecordSet(location=location, records=records).to_dict()

    if store is not None:
        data = store.cached_or_compute("records", compute, fingerprint=fp)
    else:
        data = compute()
    out = data.get("records", [])
    if stats is not None:
        stats.update({
            "cached": not computed["ran"],
            "rows_in": len(data_rows),
            "records_out": len(out),
            # 좌표를 못 붙인 record = LLM 이 준 row 가 배치에 없던 경우(§ _cell_range).
            "records_without_row": sum(
                1 for r in out if not (r.get("source") or {}).get("cell_range")
            ),
            "batch_rows": batch_rows,
            # 출력 크기는 **컬럼 수에 선형**이다(프롬프트가 "각 데이터 컬럼을 attributes 한
            # 항목으로"를 요구한다). 절단이 났을 때 "배치가 큰 건가 표가 넓은 건가"를 가르는
            # 값인데 지금까지 column_schema.json 을 따로 열어야만 알 수 있었다.
            "columns": len(column_schema.columns),
            **split,
        })
    if split.get("batches_split"):
        # 자동 복구가 **조용히** 성공하면 사람이 설정을 안 고치고 다음 문서에서 또
        # 실패 1회를 낭비한다. 그래서 복구했어도 화면에 한 번 말한다.
        # ⚠️ 이모지·em-dash 를 쓰지 말 것. ``log_print`` 는 생 ``print`` 라
        # ``console_safe`` 를 거치지 않아, cp949 콘솔에서 이 줄이 통째로 사라진다.
        log_print(
            f"[Fact] 주의: 출력 절단으로 배치를 {split['batches_split']}회 쪼개 "
            f"복구했습니다(최소 {split.get('min_items_used')}행까지). "
            f"컬럼 {len(column_schema.columns)}개 기준으로 "
            f"fact.record_batch_rows({batch_rows})가 큽니다."
        )
    logger.info("[Fact] records: %s → %d records", location, len(out))
    return RecordSet.from_dict(data)

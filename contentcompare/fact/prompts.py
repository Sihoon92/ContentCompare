"""F1 LLM 프롬프트 — Document Profiler / Schema Inducer.

원칙: 파일을 바로 주지 않고 compact_raw 를 보여 **LLM 이 구조를 추론**하게 한다.
출력은 항상 JSON 한 개. 어휘(semantic_role)는 사전에서만 고르도록 강제한다.
프롬프트 버전 상수는 입력 지문(캐시 무효화)에 포함된다 — 프롬프트가 바뀌면 재계산.
"""

from __future__ import annotations

import json
from typing import Any

from .semantic_roles import (
    ENTITY_CATEGORY,
    ENTITY_NAME,
    ENTITY_SUBCATEGORY,
    METADATA,
    QUALITATIVE,
    QUANT_LOWER,
    QUANT_TARGET,
    QUANT_UPPER,
    UNIT,
    UNKNOWN,
    guess_role,
)

PROFILER_VERSION = "profiler-v1"
SCHEMA_VERSION = "schema-v1"

_MAX_PREVIEW_CHARS = 6000
_MAX_SHEET_ROWS = 15


def _preview(obj: Any, max_chars: int = _MAX_PREVIEW_CHARS) -> str:
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n... (이하 생략)"
    return text


# --------------------------------------------------------------------------- #
# Document Profiler
# --------------------------------------------------------------------------- #
PROFILER_SYSTEM = """\
당신은 문서 구조 분석가입니다. 코드가 추출한 compact_raw(JSON)를 보고 문서의 목적과
주요 구조(표 후보)를 식별합니다. 내용을 지어내지 말고, 확신이 없으면 confidence 를
낮게 주세요.

반드시 아래 JSON 만 출력하세요(설명·마크다운 금지):
{
  "doc_type": "excel|word|ppt",
  "main_purpose": "<문서의 목적 한 줄>",
  "main_structures": [
    {"kind": "table|text", "location": "<예: sheet=이름 / slide=1 / block 범위>",
     "purpose": "<이 구조의 역할>", "row_grain_hint": "<표라면 행 하나의 의미>"}
  ],
  "confidence": <0~1 실수>
}"""


def build_profiler_user(compact: dict) -> str:
    return (
        "다음은 한 문서의 compact_raw(JSON)입니다.\n\n"
        f"{_preview(compact)}\n\n"
        "이 문서의 목적과 주요 구조를 위 스키마의 JSON 으로 답하세요."
    )


# --------------------------------------------------------------------------- #
# Schema Inducer (Excel 표 중심)
# --------------------------------------------------------------------------- #
_ROLE_DESCS = {
    ENTITY_NAME: "비교 대상 항목명(가장 구체적인 이름)",
    ENTITY_CATEGORY: "대분류",
    ENTITY_SUBCATEGORY: "중/소분류",
    QUANT_LOWER: "정량 하한(하한치/Min/Lower)",
    QUANT_TARGET: "정량 중심/기준(중심치/Nominal/Target)",
    QUANT_UPPER: "정량 상한(상한치/Max/Upper)",
    UNIT: "단위",
    QUALITATIVE: "정성 규격/조건/설명/비고",
    METADATA: "비교 비대상 메타(작성일/버전/순번 등)",
    UNKNOWN: "위 어느 것도 아님",
}
_ALLOWED_ROLES_TEXT = "\n".join(f"- {r}: {d}" for r, d in _ROLE_DESCS.items())

SCHEMA_SYSTEM = f"""\
당신은 엑셀 표 구조 분석가입니다. 한 시트의 compact_raw 를 보고 (1) 헤더 구조,
(2) row_grain(행 하나의 의미), (3) 컬럼별 semantic_role 을 추론합니다.

[헤더 판별 기준]
- 헤더 행은 열마다 서로 다른 짧은 라벨입니다. 모든 열이 같은 값인 행(예: '대외비')은
  배너이므로 헤더가 아닙니다. 제목/작성일/버전 같은 메타 행도 헤더가 아닙니다.
- 멀티헤더(상위 그룹 라벨이 일부 열에만 걸침)면 header_rows 를 2 이상으로 합니다.
- 행 번호는 입력에 표시된 절대 행 번호(r)를 그대로 씁니다.

[semantic_role 은 아래 어휘에서만 고릅니다]
{_ALLOWED_ROLES_TEXT}

반드시 아래 JSON 만 출력하세요(설명·마크다운 금지):
{{
  "table_profile": {{
    "header_structure": {{"header_start_row": <r>, "header_rows": <정수>=1>,
                          "data_start_row": <r>, "header_depth": <정수>=1>}},
    "row_grain": {{"description": "<행 하나의 의미>", "primary_entity_columns": ["<열문자>"]}}
  }},
  "column_schema": {{
    "columns": [
      {{"column": "<열문자>", "field_name": "<헤더명>", "semantic_role": "<위 어휘 중>",
        "data_type": "string|number|date", "raw_header": ["<원본 헤더>"]}}
    ]
  }}
}}"""


def _role_hints(sheet: dict) -> dict[str, str]:
    """미리보기 셀 텍스트 중 코드가 인식한 역할 힌트(텍스트→역할). LLM 보조용."""
    hints: dict[str, str] = {}
    for row in sheet.get("rows", [])[:_MAX_SHEET_ROWS]:
        for val in (row.get("cells") or {}).values():
            if isinstance(val, str):
                role = guess_role(val)
                if role and val not in hints:
                    hints[val] = role
    return hints


def build_schema_user(sheet: dict, profile: dict) -> str:
    rows = sheet.get("rows", [])[:_MAX_SHEET_ROWS]
    rows_block = "\n".join(f"행 {r.get('r')}: {r.get('cells')}" for r in rows)

    parts = [
        f"시트 이름: {sheet.get('sheet_name')}",
        f"크기: 약 {sheet.get('n_rows')}행 x {sheet.get('n_cols')}열",
    ]
    if sheet.get("merged_cells"):
        parts.append(f"병합 셀(헤더 힌트): {sheet['merged_cells']}")
    if sheet.get("bold_cells"):
        parts.append(f"굵은 셀(헤더 후보): {sheet['bold_cells']}")
    hints = _role_hints(sheet)
    if hints:
        parts.append("어휘 힌트(코드 추정, 참고용): " + json.dumps(hints, ensure_ascii=False))

    meta = "\n".join(parts)
    return (
        "다음은 한 엑셀 시트의 상위 행들과 구조 신호입니다.\n\n"
        f"{meta}\n\n[상위 행]\n{rows_block}\n\n"
        f"(문서 프로파일: {profile.get('main_purpose', '')})\n\n"
        "위 기준에 따라 table_profile 과 column_schema 를 JSON 으로 답하세요."
    )


# --------------------------------------------------------------------------- #
# Record Normalizer (F2) — 데이터 행 → record
# --------------------------------------------------------------------------- #
RECORD_VERSION = "record-v1"

RECORD_SYSTEM = """\
당신은 표 데이터 정규화기입니다. 주어진 열 스키마(열 → 역할)에 따라 각 데이터 행을
record(JSON)로 변환합니다.

규칙:
- display_name 은 가장 구체적인 항목 이름(소분류 우선)으로 정합니다.
- 상위 분류(category/subcategory)가 빈 칸이면 '직전까지 확정된 분류'로 채웁니다.
- 소계·합계·빈 행은 record 로 만들지 말고 제외합니다(records 에서 빼세요).
- 값은 셀에 있는 그대로 옮깁니다(단위 변환·수식 해석 금지).
- evidence_text 는 그 행에 실제로 있는 문구만 적습니다(지어내기 금지).
- source 에는 row(행 번호)만 넣습니다. sheet/cell_range 는 코드가 채웁니다.

반드시 아래 JSON 만 출력하세요(설명·마크다운 금지):
{
  "records": [
    {
      "record_id": "row-<행번호>",
      "entity": {"category": "...", "subcategory": "...", "display_name": "..."},
      "quantitative_spec": {"lower": <값|null>, "target": <값|null>, "upper": <값|null>, "unit": "..."},
      "qualitative_spec": "...",
      "metadata": {"<필드명>": "<값>"},
      "source": {"row": <행번호>},
      "evidence_text": "...",
      "confidence": <0~1 실수>
    }
  ]
}"""


def _columns_summary(column_schema: Any) -> str:
    lines = []
    for c in column_schema.columns:
        lines.append(f"- {c.column}열: {c.field_name or '(이름없음)'} → {c.semantic_role} ({c.data_type})")
    return "\n".join(lines) if lines else "(열 스키마 없음)"


def build_record_user(batch: list, column_schema: Any, table_profile: Any, carry: Any = None) -> str:
    rows_block = "\n".join(f"행 {r.get('r')}: {r.get('cells')}" for r in batch)
    parts: list[str] = []
    if getattr(table_profile, "row_grain", None) and table_profile.row_grain.description:
        parts.append(f"[행 의미] {table_profile.row_grain.description}")
    parts.append("[열 스키마(열 → 역할)]")
    parts.append(_columns_summary(column_schema))
    if carry and (carry.get("category") or carry.get("subcategory")):
        parts.append(
            "[직전까지 확정된 분류] "
            f"category={carry.get('category', '')}, subcategory={carry.get('subcategory', '')}"
        )
    parts.append("[데이터 행]")
    parts.append(rows_block)
    body = "\n".join(parts)
    return (
        "다음 엑셀 데이터 행들을 위 열 스키마에 따라 record(JSON)로 정규화하세요.\n\n"
        f"{body}\n\n"
        "각 행을 records 배열의 한 항목으로 만들되 소계/합계/빈 행은 제외하세요. "
        "값은 셀에 있는 그대로 옮기고(변환 금지), source.row 에 행 번호를 넣으세요."
    )

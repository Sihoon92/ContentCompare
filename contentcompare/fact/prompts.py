"""F1/F2 LLM 프롬프트 — Document Profiler / Schema Inducer / Record Normalizer.

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
    QUANT_VALUE,
    UNIT,
    UNKNOWN,
    guess_role,
)

PROFILER_VERSION = "profiler-v1"
SCHEMA_VERSION = "schema-v1"


def _as_str(v: Any, default: str = "") -> str:
    return v if isinstance(v, str) else (default if v is None else str(v))

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
    QUANT_VALUE: "정량 단일 값(정격전압/규격값 등 — 경계가 아닌 그 자체 값)",
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
RECORD_VERSION = "record-v2"

RECORD_SYSTEM = """\
당신은 표 데이터 정규화기입니다. 주어진 열 스키마(열 → 역할)에 따라 각 데이터 행을
record(JSON)로 변환합니다.

규칙:
- display_name 은 가장 구체적인 항목 이름(소분류 우선)으로 정합니다.
- 상위 분류(category/subcategory)가 빈 칸이면 '직전까지 확정된 분류'로 채웁니다.
- 소계·합계·빈 행은 record 로 만들지 말고 제외합니다(records 에서 빼세요).
- 각 데이터 컬럼을 attributes 의 한 속성(value+unit)으로 만듭니다. 속성 이름 규칙:
  · 규격 경계 컬럼(하한/중심/상한)은 각각 lower_limit / target_value / upper_limit.
  · 그 외 값·정성 컬럼은 그 컬럼의 이름(field_name)을 그대로 속성 이름으로(예: 정격전압, 재질).
  · 단위 컬럼이 있으면 그 값을 해당 정량 속성의 unit 에 넣습니다.
- 비교 대상이 아닌 메타(순번/작성일/버전 등)와 의미 불명 컬럼은 metadata 로 보냅니다.
- 값은 셀에 있는 그대로 옮깁니다(단위 변환·수식 해석 금지).
- evidence_text 는 그 행에 실제로 있는 문구만 적습니다(지어내기 금지).
- source 에는 row(행 번호)만 넣습니다. sheet/cell_range 는 코드가 채웁니다.

반드시 아래 JSON 만 출력하세요(설명·마크다운 금지):
{
  "records": [
    {
      "record_id": "row-<행번호>",
      "entity": {"category": "...", "subcategory": "...", "display_name": "..."},
      "attributes": {"<속성이름>": {"value": <값|null>, "unit": "<단위>"}},
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


# --------------------------------------------------------------------------- #
# Fact Extractor (F3) — Word/PPT 블록/도형 → fact (Excel 은 코드 매핑이라 미사용)
# --------------------------------------------------------------------------- #
FACT_VERSION = "fact-v2"

FACT_SYSTEM = """\
당신은 문서에서 비교 가능한 fact 를 추출하는 분석가입니다. 각 항목 앞의 [id] 가 붙은
블록/도형/노트를 보고, 비교 대상이 되는 사실을 fact(JSON)로 만듭니다.

규칙:
- 흩어진 서술(본문+스피커노트, 표+설명)이 같은 대상이면 하나의 fact 로 병합합니다.
- 값·단위는 본문에 있는 그대로 옮깁니다(단위 변환·수식 해석 금지).
- attributes 이름: 규격 경계는 lower_limit/target_value/upper_limit 로, 그 외는 그 속성의 고유 이름을 그대로 씁니다.
- evidence_text 는 입력에 실제로 있는 문구만 적습니다(지어내기 금지).
- source_ids 에는 이 fact 의 근거가 된 [id] 만 넣습니다(입력에 보인 id 만).
- 비교할 사실이 없는 장식/목차/빈 블록은 fact 로 만들지 않습니다.

반드시 아래 JSON 만 출력하세요(설명·마크다운 금지):
{
  "facts": [
    {
      "fact_type": "quantitative_spec|qualitative_statement|descriptive",
      "entity_name": "<무엇에 대한 사실인가>",
      "entity_path": ["<상위 맥락>", "..."],
      "attributes": {"<이름>": {"value": <값|null>, "unit": "<단위>"}},
      "evidence_text": "<입력에 실제 있는 근거 문구>",
      "source_ids": ["<근거 블록/도형 id>"],
      "confidence": <0~1 실수>
    }
  ]
}"""


def _profile_purpose(profile: Any) -> str:
    if profile is None:
        return ""
    if isinstance(profile, dict):
        return _as_str(profile.get("main_purpose"))
    return _as_str(getattr(profile, "main_purpose", ""))


def _render_unit(u: dict) -> str:
    uid = u.get("id")
    loc = f" slide={u['slide_no']}" if u.get("slide_no") else ""
    note = " (스피커노트)" if u.get("is_note") else ""
    if u.get("type") == "table":
        content = f"표 {u.get('rows')}"
    else:
        content = _as_str(u.get("text"))
    return f"[{uid}]{loc}{note} {content}"


def build_fact_user(units: list, doc_type: str, profile: Any = None) -> str:
    header = (
        f"다음은 {doc_type} 문서의 블록/도형입니다. 각 항목 앞의 [id] 는 "
        "source_ids 에 넣을 식별자입니다."
    )
    purpose = _profile_purpose(profile)
    if purpose:
        header += f"\n문서 맥락: {purpose}"
    body = "\n".join(_render_unit(u) for u in units)
    return (
        f"{header}\n\n{body}\n\n"
        "위에서 비교 가능한 사실을 facts 배열의 fact 로 추출하세요. 흩어진 서술이 같은 "
        "대상이면 하나로 병합하고, source_ids 에는 근거가 된 [id] 만 넣으세요."
    )


# --------------------------------------------------------------------------- #
# F5 Fact Comparator — 코드가 단정하지 못한 건만 LLM 에 넘긴다
# --------------------------------------------------------------------------- #
COMPARE_VERSION = "compare-v1"

COMPARE_SYSTEM = """\
당신은 두 문서의 사실(fact)이 서로 일치하는지 판정하는 검토자입니다. 기준 fact 하나와
대상 문서의 후보 fact 들이 주어집니다. 코드가 이미 명백한 경우는 처리했고, 당신에게는
**애매한 건만** 옵니다(단위 등가 불명, 속성 이름 불일치, 유사도 경계 등).

판정 값:
- "match": 같은 대상에 대한 같은 내용(표기·단위·언어가 달라도 실질이 같으면 match)
- "mismatch": 같은 대상인데 값이 다름 → mismatch_attributes 에 어긋난 속성 이름
- "missing": 후보 중 기준과 같은 대상을 다루는 것이 없음
- "unknown": 관련은 있어 보이나 같/다름을 **확신할 수 없음**(단위 불명·정보 부족)

원칙:
1. 확신이 없으면 match/mismatch 로 단정하지 말고 **unknown** 을 쓰고, reason 에 무엇이
   불확실한지 설명하세요. 틀린 단정보다 보류가 낫습니다.
2. 후보에 실제로 적혀 있지 않은 내용을 지어내지 마세요. 근거가 없으면 missing/unknown.
3. 기준의 단위가 비어 있으면 대상 단위를 근거로 단위를 **추측하지 마세요**. 값이 같으면
   match, 값이 배수 관계라 단위에 따라 달라진다면 unknown 입니다.
4. target_fact_id 는 후보로 제시된 id 중 하나만 쓰세요.

반드시 아래 JSON 만 출력하세요(설명·마크다운 금지):
{
  "result": "match|mismatch|missing|unknown",
  "target_fact_id": "<가장 대응하는 후보 id 또는 빈 문자열>",
  "mismatch_attributes": ["<어긋난 속성 이름>"],
  "reason": "<한국어로 판단 근거 한두 문장>"
}"""


def _render_fact(fact: Any, *, prefix: str = "") -> str:
    attrs = ", ".join(
        f"{k}={a.value}{(' ' + a.unit) if a.unit else ''}"
        for k, a in (fact.attributes or {}).items()
    ) or "(속성 없음)"
    lines = [f"{prefix}항목: {fact.entity_name}", f"{prefix}속성: {attrs}"]
    if fact.evidence_text:
        lines.append(f"{prefix}근거 원문: {fact.evidence_text}")
    return "\n".join(lines)


def build_compare_user(reference: Any, candidates: list, *, knowledge: str = "") -> str:
    """기준 fact + 후보 fact 들 → 비교 프롬프트.

    후보에는 ``[id]`` 를 붙여 LLM 이 고른 대상을 코드가 검증할 수 있게 한다.
    도메인 지식(knowledge/*.md)은 용어·동의어·표기 규칙 판단에 쓰이도록 앞에 붙인다.
    """
    parts = []
    if knowledge.strip():
        parts.append(f"[참고 자료 — 도메인 지식]\n{knowledge.strip()}\n")
    parts.append("[기준 문서의 항목]\n" + _render_fact(reference))
    parts.append("\n[대상 문서의 후보]")
    for c in candidates:
        parts.append(f"[{c.fact_id}]\n" + _render_fact(c, prefix="  "))
    parts.append(
        "\n위 후보 중 기준 항목과 같은 대상을 다루는 것이 있는지 보고 판정하세요. "
        "확신이 없으면 unknown 으로 두세요."
    )
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# F7 개념 판정
# --------------------------------------------------------------------------- #
CONCEPT_SYSTEM = """\
당신은 서로 다른 문서의 두 항목이 **같은 것을 가리키는지** 판정하는 검토자입니다.
값이 같은지는 다른 단계가 판단합니다. 당신은 "이 둘을 비교해도 되는가"만 답합니다.

판정 값:
- "same_as": 두 항목이 같은 대상에 대한 같은 주장이다. 표기·언어·단위가 달라도 됩니다.
- "differs_by": 서로 다른 항목이다. **무엇이 달라서 다른지 축(axis) 이름을 직접 지어**
  쓰세요(예: 측정조건, 기간, 물리량, 산정방식, 연결범위). 정해진 목록은 없습니다 —
  이 문서의 분야에 맞는 이름을 지으세요.
- "unknown": 판단이 서지 않는다.

원칙:
1. **값이 다른 것은 differs_by 의 근거가 아닙니다.** "21~29 와 -10~80 은 값이 다르니
   다른 항목"이라고 추론하지 마세요. 값의 차이는 우리가 찾으려는 결과이지 판단 재료가
   아닙니다. 항목이 **무엇에 대한 것인지**만 보세요.
2. "same_as" 를 쓰려면 양쪽에서 **원문을 그대로 인용**해야 합니다(left_text/right_text).
   인용할 원문이 없으면 same_as 를 쓸 수 없습니다.
3. 확신이 없으면 "unknown" 을 쓰세요. 틀린 연결은 사람을 잘못된 수정으로 유도하지만,
   놓친 연결은 검토 목록에 남아 확인됩니다.
4. 주어진 쌍마다 정확히 하나의 판정을 내고, 쌍의 id 를 그대로 옮기세요.

반드시 아래 JSON 만 출력하세요(설명·마크다운 금지):
{
  "pairs": [
    {
      "left_fact_id": "<주어진 id>",
      "right_fact_id": "<주어진 id>",
      "relation": "same_as|differs_by|unknown",
      "axis": "<differs_by 일 때 무엇이 다른지>",
      "left_text": "<same_as 일 때 왼쪽 원문 인용>",
      "right_text": "<same_as 일 때 오른쪽 원문 인용>",
      "reason": "<한국어 한 문장>"
    }
  ]
}"""


def build_concept_user(
    pairs: list,
    *,
    knowledge: str = "",
    purpose: str = "",
    ontology_summary: str = "",
) -> str:
    """개념 판정 프롬프트의 user 파트. ``pairs`` 는 ``CandidatePair`` 리스트."""
    blocks: list[str] = []
    if purpose:
        blocks.append(f"[문서 분야]\n{purpose}")
    if knowledge:
        blocks.append(f"[참고자료 — 도메인 지식]\n{knowledge}")
    if ontology_summary:
        blocks.append(f"[이미 확정된 관계 — 일관되게 판단하세요]\n{ontology_summary}")

    for i, pair in enumerate(pairs, start=1):
        blocks.append(
            f"[쌍 {i}]\n"
            f"left_fact_id: {pair.left.fact_id} (문서: {pair.left_doc})\n"
            f"{_render_fact(pair.left, prefix='  ')}\n"
            f"right_fact_id: {pair.right.fact_id} (문서: {pair.right_doc})\n"
            f"{_render_fact(pair.right, prefix='  ')}"
        )
    return "\n\n".join(blocks)

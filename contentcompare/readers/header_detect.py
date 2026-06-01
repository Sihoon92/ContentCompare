"""LLM 기반 엑셀 헤더 구조 추정.

시트 상위 N행을 LLM 에게 보여주고, 표의 **컬럼 헤더가 시작하는 행**과 **헤더가
차지하는 행 수**(멀티헤더면 2 이상)를 추정한다. '대외비' 같은 보안표기·제목·빈 행은
헤더가 아니므로 건너뛰도록 지시한다.

LLM 응답이 비정상이면 None 을 반환 → 호출 측이 규칙 기반으로 폴백한다.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class HeaderSpec:
    header_start: int  # 미리보기 rows 기준 0-based 헤더 시작 인덱스
    header_rows: int   # 헤더가 차지하는 행 수(>=1)


SYSTEM_PROMPT = """\
당신은 엑셀 표 구조 분석 전문가입니다. 주어진 상위 행들에서 '컬럼 헤더'가 시작하는
행과 헤더가 몇 행으로 이루어졌는지 판별합니다.

규칙:
- 보안표기('대외비','사내한' 등), 문서 제목, 작성일자, 완전히 빈 행은 헤더가 아닙니다(건너뜀).
- 컬럼 헤더는 각 열의 의미를 나타내는 라벨 행입니다.
- 멀티헤더(상위 그룹 라벨이 여러 열을 묶고, 그 아래 세부 라벨이 있는 경우)면
  header_rows 를 2 이상으로 합니다. 예: '정량규격'(상위) + '하한치/중심치/상한치'(하위) → 2.
- 실제 데이터(레코드)는 헤더 바로 다음 행부터 시작합니다.

반드시 아래 JSON 만 출력하세요(설명·마크다운 금지):
{"header_start": <헤더 시작 행 번호(0-based)>, "header_rows": <헤더 행 수(정수>=1)>}"""

_USER_TEMPLATE = """\
다음은 엑셀 시트의 상위 행들입니다(행 번호는 0부터, 각 행은 셀 값 목록):

{rows_block}

컬럼 헤더가 시작하는 행 번호(header_start)와 헤더 행 수(header_rows)를 JSON 으로 답하세요."""


def _parse_json(raw: str) -> Optional[dict]:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        pass
    match = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return None


def detect_header(rows: list[list[str]], llm, *, max_rows: int = 12) -> Optional[HeaderSpec]:
    """상위 행들을 LLM 에 보여 헤더 시작/행수를 추정. 실패 시 None."""
    preview = rows[:max_rows]
    if not preview:
        return None

    rows_block = "\n".join(
        f"행 {i}: {row[:15]}" for i, row in enumerate(preview)
    )
    user = _USER_TEMPLATE.format(rows_block=rows_block)

    try:
        raw = llm.complete(SYSTEM_PROMPT, user)
    except Exception as exc:  # noqa: BLE001 - LLM 호출 실패는 폴백으로
        logger.warning("헤더 추정 LLM 호출 실패: %s", exc)
        return None

    parsed = _parse_json(raw)
    if not isinstance(parsed, dict):
        logger.warning("헤더 추정 응답 파싱 실패: %r", raw)
        return None

    try:
        start = int(parsed.get("header_start"))
        count = int(parsed.get("header_rows"))
    except (TypeError, ValueError):
        logger.warning("헤더 추정 값 형식 오류: %r", parsed)
        return None

    # 범위 검증/보정.
    if start < 0 or start >= len(preview):
        logger.warning("헤더 추정 start 범위 밖(%d) → 폴백", start)
        return None
    count = max(1, count)
    if start + count > len(rows):
        count = max(1, len(rows) - start)
    return HeaderSpec(header_start=start, header_rows=count)

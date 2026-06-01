"""LLM 기반 엑셀 헤더 구조 추정.

시트 상위 N행을 LLM 에 보여주고 '컬럼 헤더가 시작하는 행'과 '헤더 행 수'를 추정한다.
LLM 이 배너('대외비')를 헤더로 오인하지 않도록:
  1) 헤더 판별 기준을 명시하고,
  2) 행별 신호(모든 열 동일값/숫자 비율 등)를 미리 계산해 힌트로 제공하고,
  3) LLM 결과가 배너 행을 가리키면 결정적으로 다음 행으로 보정한다(안전장치).
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
행 번호와 헤더가 몇 행인지 판별합니다.

[헤더 행을 정하는 기준]
1. (가장 중요) 헤더 행은 **열마다 값이 서로 다른** 라벨들로 구성됩니다.
   → 모든 열이 **같은 값 하나로 반복**되는 행(예: '대외비 | 대외비 | 대외비')은
     헤더가 아니라 '배너/보안표기'이므로 건너뜁니다.
2. 헤더 라벨은 짧은 텍스트(예: 제품명, 매출액)이며, 숫자·날짜·긴 문장이 아닙니다.
3. 헤더 바로 다음 행부터는 데이터(숫자/코드 등 행마다 값이 달라짐)입니다.
4. 문서 제목, 작성일, 'Version', 보안표기 같은 메타 행은 헤더가 아닙니다(건너뜀).
5. 멀티헤더: 상위 그룹 라벨이 **일부 열에만** 걸치고(나머지 열은 빈칸) 그 아래 세부
   라벨이 있으면 header_rows 를 2 이상으로 합니다.
   (배너와의 차이: 배너는 '모든' 열이 같은 값, 그룹 라벨은 '일부' 열만 채움)

[예시]
행 0: ['대외비','대외비','대외비']      (모든 열 동일 → 배너)
행 1: ['제품명','매출액','직원수']      (열마다 다른 라벨 → 헤더)
행 2: ['A', 1200, 50]                  (숫자 데이터)
→ {"header_start": 1, "header_rows": 1}

행 0: ['', '정량규격','정량규격','정량규격']  (일부 열 그룹 라벨)
행 1: ['제품','하한치','중심치','상한치']      (세부 라벨)
행 2: ['A', 1, 2, 3]
→ {"header_start": 0, "header_rows": 2}

반드시 아래 JSON 만 출력하세요(설명·마크다운 금지):
{"reason": "<간단한 근거>", "header_start": <0-based 정수>, "header_rows": <정수>=1>}"""

_USER_TEMPLATE = """\
다음은 엑셀 시트의 상위 행들입니다. 각 행 뒤의 '신호'는 참고용 분석입니다.

{rows_block}

위 기준에 따라 헤더 시작 행(header_start)과 헤더 행 수(header_rows)를 JSON 으로 답하세요.
모든 열이 같은 값인 배너 행은 절대 헤더로 고르지 마세요."""


# --------------------------------------------------------------------------- #
# 행 신호(결정적) — LLM 힌트 + 안전장치에 공용 사용
# --------------------------------------------------------------------------- #
def _is_numeric(text: str) -> bool:
    if not text:
        return False
    try:
        float(text.replace(",", ""))
        return True
    except ValueError:
        return False


def _is_banner(row: list[str]) -> bool:
    """모든(2개 이상) 열이 동일한 값으로 채워진 배너 행인지."""
    nonempty = [c for c in row if str(c).strip()]
    return len(row) >= 2 and len(nonempty) == len(row) and len(set(nonempty)) == 1


def _signal(row: list[str]) -> str:
    cells = [str(c).strip() for c in row]
    nonempty = [c for c in cells if c]
    if not nonempty:
        return "빈 행"
    if _is_banner(cells):
        return f"모든 열 동일값('{nonempty[0]}') → 배너(헤더 아님)"
    numeric = sum(1 for c in nonempty if _is_numeric(c))
    distinct = len(set(nonempty))
    if numeric >= max(1, len(nonempty) // 2):
        return "숫자 비중 높음 → 데이터 행 후보"
    if distinct > 1:
        return "열마다 다른 텍스트 라벨 → 헤더 후보"
    return "텍스트"


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
    """상위 행들을 LLM 에 보여 헤더 시작/행수를 추정. 실패 시 None.

    LLM 이 배너 행을 가리키면 결정적으로 다음 비배너 행으로 보정한다.
    """
    preview = [[str(c).strip() for c in row] for row in rows[:max_rows]]
    if not preview:
        return None

    rows_block = "\n".join(
        f"행 {i}: {row[:15]}  | 신호: {_signal(row)}" for i, row in enumerate(preview)
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
    if parsed.get("reason"):
        logger.info("헤더 추정 근거: %s", parsed["reason"])

    if start < 0 or start >= len(preview):
        logger.warning("헤더 추정 start 범위 밖(%d) → 폴백", start)
        return None

    # 안전장치: LLM 이 배너 행을 골랐으면 다음 비배너 행으로 보정.
    guarded = start
    while guarded < len(preview) and _is_banner(preview[guarded]):
        logger.info("헤더 추정 보정: 행 %d 는 배너 → 다음 행으로", guarded)
        guarded += 1
    if guarded >= len(preview):
        logger.warning("헤더 추정 보정 후 헤더 행이 없음 → 폴백")
        return None
    start = guarded

    count = max(1, count)
    if start + count > len(rows):
        count = max(1, len(rows) - start)
    return HeaderSpec(header_start=start, header_rows=count)

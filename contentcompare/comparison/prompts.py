"""비교용 LLM 프롬프트 템플릿.

LLM 에게 기준 항목과 후보들을 모두 제시하고, 정량/정성 내용이 같은지 판단하여
**JSON** 으로 답하게 한다(기획 3·4번).
"""

from __future__ import annotations

from ..models import Candidate, DocItem, RecordItem

SYSTEM_PROMPT = """\
당신은 문서 내용 대조 전문가입니다. 기준 항목 하나와, 다른 문서들에서 검색된 후보들이 주어집니다.
당신의 임무는 후보들 중 기준 항목과 '같은 내용'을 다루는 것을 찾아, 정량적(숫자·수치·날짜·금액)
및 정성적(의미·표현·범위) 내용이 동일한지 판정하는 것입니다.

판정(verdict) 기준:
- "same": 관련 후보가 있고 정량/정성 내용이 모두 일치
- "partial": 관련 후보가 있으나 일부만 일치(일부 수치/조건 불일치)
- "different": 관련 후보는 있으나 핵심 내용이 다름
- "not_found": 기준 항목과 관련된 후보가 없음

반드시 아래 JSON 스키마로만 답하세요(추가 설명·마크다운 금지):
{
  "verdict": "same|partial|different|not_found",
  "matched_item_ids": ["<관련 있다고 판단한 후보의 item_id 들>"],
  "reasoning": "<무엇이 같고/다른지, 왜 그렇게 판단했는지 한국어로 구체 서술. 다르면 차이 나는 수치/표현을 명시>"
}
"""

_USER_TEMPLATE = """\
[기준 항목]
- item_id: {ref_id}
- 출처: {ref_source}
- 내용: {ref_text}

[후보 목록]
{candidates_block}

위 기준 항목과 후보들을 대조해 JSON 으로 판정하세요.
"""

_CANDIDATE_TEMPLATE = "- item_id: {cid} | 유사도: {score:.3f} | 출처: {source}\n  내용: {text}"


def build_user_prompt(reference: DocItem, candidates: list[Candidate]) -> str:
    if candidates:
        block = "\n".join(
            _CANDIDATE_TEMPLATE.format(
                cid=c.item.item_id,
                score=c.score,
                source=c.item.source_label,
                text=c.item.text,
            )
            for c in candidates
        )
    else:
        block = "(검색된 후보 없음)"
    return _USER_TEMPLATE.format(
        ref_id=reference.item_id,
        ref_source=reference.source_label,
        ref_text=reference.text,
        candidates_block=block,
    )


# --------------------------------------------------------------------------- #
# 레코드(행) 단위: 필드별 판정 (엑셀 hybrid)
# --------------------------------------------------------------------------- #
RECORD_SYSTEM_PROMPT = """\
당신은 문서 내용 대조 전문가입니다. 기준 '레코드'(엑셀 한 행) 하나와, 그 행이 담은
여러 '필드'(셀=검증할 주장)들, 그리고 다른 문서들에서 검색된 후보 단락들이 주어집니다.

각 필드는 하나의 사실 주장입니다(예: 매출액=1,200). 당신의 임무는 후보 단락들을 근거로
**필드마다 개별적으로** 그 주장이 대상 문서에서 어떻게 확인되는지 판정하는 것입니다.

판정(verdict) 기준(필드별):
- "same": 후보에서 같은 항목을 찾았고 값/내용이 일치
- "partial": 같은 항목을 찾았으나 값의 일부만 일치(단위/일부 수치 차이 등)
- "different": 같은 항목을 찾았으나 값/내용이 다름
- "not_found": 그 필드(항목)에 해당하는 내용을 후보에서 찾지 못함

레코드의 키 문맥(예: [제품명=A])은 어떤 대상 부분이 이 행에 해당하는지 찾는 단서입니다.
숫자는 단위·표기를 감안해 의미가 같으면 same 으로 봅니다(예: 1,200 과 1200).

반드시 아래 JSON 스키마로만 답하세요(추가 설명·마크다운·코드펜스 금지):
{
  "fields": [
    {
      "field_id": "<주어진 필드의 field_id 그대로>",
      "verdict": "same|partial|different|not_found",
      "matched_item_ids": ["<근거가 된 후보 item_id 들>"],
      "reasoning": "<무엇이 같고/다른지 한국어로 구체 서술. 다르면 대상 값과 기준 값을 함께 명시>"
    }
  ]
}
모든 필드를 빠짐없이 포함하세요."""

_RECORD_USER_TEMPLATE = """\
[기준 레코드]
- 출처: {ref_source}
- 키 문맥: {key_context}
- 행 전체: {ref_text}

[검증할 필드 목록]
{fields_block}

[후보 단락 목록]
{candidates_block}

각 필드를 후보와 대조해 JSON 으로 판정하세요. 필드는 위 field_id 를 그대로 사용합니다.
"""

_FIELD_TEMPLATE = "- field_id: {fid} | 항목: {header} | 기준값: {value}"


def build_record_prompt(record: RecordItem, candidates: list[Candidate]) -> str:
    """레코드(행)+필드목록+후보 → 필드별 판정용 user 프롬프트."""
    fields_block = "\n".join(
        _FIELD_TEMPLATE.format(fid=f.field_id, header=f.header, value=f.value_norm)
        for f in record.fields
    )
    if candidates:
        cand_block = "\n".join(
            _CANDIDATE_TEMPLATE.format(
                cid=c.item.item_id,
                score=c.score,
                source=c.item.source_label,
                text=c.item.text,
            )
            for c in candidates
        )
    else:
        cand_block = "(검색된 후보 없음)"
    return _RECORD_USER_TEMPLATE.format(
        ref_source=record.source_label,
        key_context=record.key_context or "(없음)",
        ref_text=record.text,
        fields_block=fields_block,
        candidates_block=cand_block,
    )

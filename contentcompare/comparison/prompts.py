"""비교용 LLM 프롬프트 템플릿.

LLM 에게 기준 항목과 후보들을 모두 제시하고, 정량/정성 내용이 같은지 판단하여
**JSON** 으로 답하게 한다(기획 3·4번).

엑셀 레코드(행)는 **행 단위로 종합 판정**한다(요청 1번): 한 행의 모든 열을 함께 보고
그 내용이 대상 문서에 있는지·어디에 있는지·왜 그렇게 판단했는지를 한 번에 답한다.

사용자가 제공한 도메인 지식(:mod:`contentcompare.knowledge`)이 있으면 프롬프트 앞부분에
참고 자료로 주입한다(요청 5번).
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
- "unknown": 관련 내용은 있는 듯하나 같은지/다른지 확실히 판단하기 어려움(단위 불명·모호·지식부족)
- "not_found": 기준 항목과 관련된 후보가 없음

값이 정확히 같으면 same, 다르면 different 로 분명히 하되, 확신이 안 서면 억지로 고르지 말고
unknown 으로 두고 reasoning 에 왜 어려운지 설명하세요. 후보에 없는 내용을 지어내지 마세요.

반드시 아래 JSON 스키마로만 답하세요(추가 설명·마크다운 금지):
{
  "verdict": "same|partial|different|unknown|not_found",
  "matched_item_ids": ["<관련 있다고 판단한 후보의 item_id 들>"],
  "reasoning": "<무엇이 같고/다른지(또는 왜 판단이 어려운지), 왜 그렇게 판단했는지 한국어로 구체 서술. 다르면 차이 나는 수치/표현을 명시>"
}
"""

_USER_TEMPLATE = """\
{knowledge_block}[기준 항목]
- item_id: {ref_id}
- 출처: {ref_source}
- 내용: {ref_text}

[후보 목록]
{candidates_block}

위 기준 항목과 후보들을 대조해 JSON 으로 판정하세요.
"""

_CANDIDATE_TEMPLATE = "- item_id: {cid} | 유사도: {score:.3f} | 출처: {source}\n  내용: {text}"


def build_user_prompt(
    reference: DocItem, candidates: list[Candidate], *, knowledge: str = ""
) -> str:
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
        knowledge_block=_kb(knowledge),
        ref_id=reference.item_id,
        ref_source=reference.source_label,
        ref_text=reference.text,
        candidates_block=block,
    )


# --------------------------------------------------------------------------- #
# 레코드(행) 단위: 행 종합 판정 (엑셀 hybrid)
# --------------------------------------------------------------------------- #
RECORD_SYSTEM_PROMPT = """\
당신은 문서 내용 대조 전문가입니다. 기준 '레코드'(엑셀 한 행) 하나와, 그 행이 담은
여러 '필드'(열=항목), 그리고 다른 문서들에서 검색된 후보 단락들이 주어집니다.

한 행은 하나의 대상(제품/항목/사건 등)을 여러 열로 설명합니다. 당신의 임무는 그 행의
**모든 열을 함께 종합**하여, 이 레코드의 내용이 대상 문서에 존재하는지, 존재한다면
어디(어느 후보)에 있는지, 그리고 각 항목이 일치하는지를 **행 단위로 한 번에** 판정하는 것입니다.

먼저 키 문맥(예: [제품명=A])과 여러 열을 단서로, 이 행이 가리키는 대상이 후보들 중
어디에 서술돼 있는지 찾으세요. 그 위치를 기준으로 각 열의 값/내용이 맞는지 확인합니다.

행 전체 판정(verdict) 기준:
- "same": 이 행에 해당하는 내용을 찾았고, 핵심 항목(값/내용)이 모두 일치
- "partial": 해당 내용을 찾았으나 일부 항목만 일치(일부 수치/조건 차이, 또는 일부 항목 누락)
- "different": 해당 내용을 찾았으나 핵심 값/내용이 다름
- "unknown": 관련 내용은 있는 듯하나 같은지/다른지 **확실히 판단하기 어려움**
- "not_found": 이 행에 해당하는 내용을 후보에서 찾지 못함

판정 원칙(매우 중요):
1. 값(숫자·날짜·금액)이 **정확히 같으면 일치**, **다르면 다름**으로 분명히 표시하세요.
   숫자는 단위·표기를 감안해 의미가 같으면 일치로 봅니다(예: 1,200 과 1200, 1.2천 과 1200).
2. 다만 **단위가 불명확**하거나(예: 기준 '1200' vs 후보 '1,200억원'), 표현이 모호하거나,
   해당 분야 지식이 없어 같은 항목인지조차 확신할 수 없으면 **억지로 same/different 를 고르지 말고
   "unknown"** 으로 두세요. 그리고 reasoning 에 **왜 어려운지/모호한지를 반드시 설명**하세요
   (예: "단위가 달라 동일 여부 불명", "도메인 지식 부족으로 동일 항목인지 불확실").
3. 후보(대상 문서)에 실제로 적혀 있지 않은 내용을 추측해 채우지 마세요. 근거가 없으면 not_found 또는 unknown.

반드시 아래 JSON 스키마로만 답하세요(추가 설명·마크다운·코드펜스 금지):
{
  "verdict": "same|partial|different|unknown|not_found",
  "matched_item_ids": ["<이 행의 근거가 된 후보 item_id 들>"],
  "reasoning": "<이 행의 내용이 대상의 어디에 있는지, 왜 그렇게 판단했는지, 어떤 항목이 일치/불일치/불명인지 한국어로 종합 서술. 판단이 어려우면 왜 어려운지 구체적으로>",
  "findings": [
    {
      "field_id": "<주어진 필드의 field_id 그대로>",
      "found": true,
      "note": "<이 항목이 일치/다름/불명 중 무엇인지와 그 근거 한 줄. 다르면 대상 값과 기준 값을 함께, 불명이면 왜 모호한지>",
      "evidence": "<판단 근거가 된 후보 원문을 그대로 인용(없으면 빈 문자열). 지어내지 말 것>"
    }
  ]
}
findings 에는 주어진 모든 필드를 빠짐없이 포함하세요. evidence 는 반드시 후보 목록에 실제로 있는 문구만 인용하세요."""

_RECORD_USER_TEMPLATE = """\
{knowledge_block}[기준 레코드]
- 출처: {ref_source}
- 키 문맥: {key_context}
- 행 전체: {ref_text}

[이 행의 항목(필드) 목록]
{fields_block}

[후보 단락 목록]
{candidates_block}

이 행의 모든 항목을 종합해 행 단위로 JSON 판정하세요. findings 의 field_id 는 위 값을 그대로 사용합니다.
"""

_FIELD_TEMPLATE = "- field_id: {fid} | 항목: {header} | 기준값: {value}"


def build_record_prompt(
    record: RecordItem, candidates: list[Candidate], *, knowledge: str = ""
) -> str:
    """레코드(행)+필드목록+후보 → 행 종합 판정용 user 프롬프트."""
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
        knowledge_block=_kb(knowledge),
        ref_source=record.source_label,
        key_context=record.key_context or "(없음)",
        ref_text=record.text,
        fields_block=fields_block,
        candidates_block=cand_block,
    )


def _kb(knowledge: str) -> str:
    """지식 블록을 프롬프트 앞에 붙일 형태로(있으면 끝에 빈 줄 2개) 반환."""
    knowledge = (knowledge or "").strip()
    return f"{knowledge}\n\n" if knowledge else ""

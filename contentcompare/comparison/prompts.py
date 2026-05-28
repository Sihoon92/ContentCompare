"""비교용 LLM 프롬프트 템플릿.

LLM 에게 기준 항목과 후보들을 모두 제시하고, 정량/정성 내용이 같은지 판단하여
**JSON** 으로 답하게 한다(기획 3·4번).
"""

from __future__ import annotations

from ..models import Candidate, DocItem

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

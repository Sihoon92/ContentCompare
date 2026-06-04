"""사람이 작성하는 도메인 지식(human-in-the-loop) 로딩/저장.

LLM 은 사내 도메인 용어·암묵지를 모르기 때문에 내용 비교에서 오판할 수 있다(요청 5번).
사용자가 ``knowledge/`` 디렉터리에 Markdown 으로 정리한 지식을 비교 프롬프트에
**참고 자료**로 항상 주입한다.

지식 파일 권장 구조(템플릿은 :data:`TEMPLATE` 참고):

    # 용어 정의
    - formation: 배터리 화성(formation) 공정. 충·방전으로 SEI 막을 형성한다.

    # 동의어 / 표기 규칙
    - "매출"과 "Revenue"는 같은 항목으로 본다.

    # 비교 분석 실패 케이스
    - 단위가 'k원'과 '천원'으로 다르게 표기돼도 값이 같으면 same 으로 본다.

이 글들은 판정 근거가 아니라 **배경지식**이다. 후보(대상 문서)에 없는 사실을
지어내는 근거로 쓰지 않도록 프롬프트에서 한 번 더 못박는다.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("contentcompare.knowledge")

DEFAULT_KNOWLEDGE_DIR = "knowledge"

TEMPLATE = """\
# 용어 정의
- (예) formation: 배터리 화성(formation) 공정을 의미한다.

# 동의어 / 표기 규칙
- (예) "매출"과 "Revenue"는 같은 항목으로 취급한다.

# 비교 분석 실패 케이스
- (예) 단위가 'k원'과 '천원'으로 달라도 값이 같으면 같은 것으로 본다.

# 비교 제외 항목(컬럼)
- (예) 순번, 중분류 CODE, 소분류 CODE 는 비교에서 제외한다.
  (여기 적은 컬럼명은 비교 대상에서 자동으로 빠집니다. 엑셀 헤더와 같은 표기로 적으세요.)

# 기타 배경지식
- (자유 서술)
"""


def knowledge_dir(base: str = DEFAULT_KNOWLEDGE_DIR) -> str:
    os.makedirs(base, exist_ok=True)
    return base


def list_knowledge_files(base: str = DEFAULT_KNOWLEDGE_DIR) -> list[str]:
    """지식 디렉터리의 .md 파일 경로를 이름순으로 반환(없으면 빈 리스트)."""
    if not os.path.isdir(base):
        return []
    paths = [
        os.path.join(base, n) for n in os.listdir(base) if n.lower().endswith(".md")
    ]
    return sorted(paths)


def read_knowledge_file(path: str) -> str:
    if not path or not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def save_knowledge_file(name: str, content: str, *, base: str = DEFAULT_KNOWLEDGE_DIR) -> str:
    """지식 .md 파일을 저장하고 경로를 반환한다(없으면 새로 만들고, 있으면 덮어쓴다)."""
    directory = knowledge_dir(base)
    fname = os.path.basename(name.strip()) or "knowledge.md"
    if not fname.lower().endswith(".md"):
        fname += ".md"
    path = os.path.join(directory, fname)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    logger.info("도메인 지식 저장: %s (%d자)", path, len(content))
    return path


def load_knowledge(base: str = DEFAULT_KNOWLEDGE_DIR, *, max_chars: int = 12000) -> str:
    """디렉터리의 모든 지식 .md 를 파일명 헤더와 함께 하나로 합쳐 반환한다.

    합산 길이가 ``max_chars`` 를 넘으면 잘라낸다(프롬프트 폭주 방지).
    내용이 없으면 빈 문자열을 반환한다.
    """
    parts: list[str] = []
    for path in list_knowledge_files(base):
        text = read_knowledge_file(path).strip()
        if not text:
            continue
        parts.append(f"## [{os.path.basename(path)}]\n{text}")
    combined = "\n\n".join(parts).strip()
    if len(combined) > max_chars:
        combined = combined[:max_chars].rstrip() + "\n…(이하 생략)"
    if combined:
        logger.info("도메인 지식 %d개 파일 로드(%d자)", len(parts), len(combined))
    return combined


def knowledge_prompt_block(text: str) -> str:
    """지식 텍스트를 프롬프트에 삽입할 블록으로 감싼다. 비면 빈 문자열."""
    if not text.strip():
        return ""
    return (
        "[참고: 도메인 지식 — 사용자가 제공한 배경지식 및 비교 규칙]\n"
        "아래는 판정에 도움이 되는 배경지식과 비교 규칙입니다. 용어·동의어·표기 규칙을 해석할 때\n"
        "참고하고, 사용자가 명시한 비교 규칙(예: 특정 항목 비교 제외, 동의어 처리)이 있으면 따르세요.\n"
        "단, 배경지식을 근거로 대상 문서(후보)에 실제로 없는 내용을 '있다'고 판단하지는 마세요.\n"
        "------------------------------------------------------------\n"
        f"{text.strip()}\n"
        "------------------------------------------------------------\n"
    )

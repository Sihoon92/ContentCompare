"""Raw Extractor — 문서의 *물리 구조* 만 기계적으로 뽑아 raw json 으로 만든다.

기획(Multi-document Schema Induction)에서 가장 앞단계. 핵심 원칙:

    파일 자체를 LLM 에게 바로 주지 않는다.
    먼저 **코드** 가 raw json(physical_raw)을 만든다.
    LLM 은 그 raw json 을 보고 의미 구조를 추론한다.

따라서 이 패키지는 **해석을 하지 않는다.** 셀/단락/표/도형의 위치·원문값·병합·
스타일 같은 "보이는 정보" 만 그대로 담는다(어느 행이 헤더인지 등은 LLM 의 몫).

기존 ``readers/`` 패키지(의미 단위 ``DocItem`` 까지 생성)와 역할이 다르다. 추출
백엔드는 회사 환경에 맞춰 COM(Excel=xlwings, Word=win32com)을 쓰며, readers 와
동일하게 **COM I/O 와 순수 빌더를 분리** 해 Office 없이도 빌더 단위테스트가 가능하다.
여기서는 파일을 순수 데이터클래스(:mod:`contentcompare.raw.models`)로 변환만 한다.
"""

from __future__ import annotations

from .extract import extract_raw, raw_to_dict, raw_to_json
from .models import (
    RawCell,
    RawDocument,
    RawExcelDocument,
    RawSheet,
    RawWordBlock,
    RawWordDocument,
)

__all__ = [
    "extract_raw",
    "raw_to_dict",
    "raw_to_json",
    "RawCell",
    "RawDocument",
    "RawExcelDocument",
    "RawSheet",
    "RawWordBlock",
    "RawWordDocument",
]

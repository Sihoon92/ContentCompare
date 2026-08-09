#!/usr/bin/env python3
"""엑셀/워드 파일을 physical_raw.json (또는 compact_raw.json) 으로 덤프하는 도구.

사용법:
    python scripts/dump_raw.py 경로/문서.xlsx
    python scripts/dump_raw.py 경로/문서.docx -o out/raw.json
    python scripts/dump_raw.py 경로/문서.xlsx --compact -o out/compact.json

LLM 구조 분석 이전 단계인 "코드가 만드는 raw json" 이 어떻게 생겼는지 빠르게
검증하기 위한 스크립트다(기획: 파일을 LLM 에 바로 주지 않고 raw json 부터 만든다).
``--compact`` 는 LLM 입력용으로 압축한 compact_raw 를 출력한다.

주의: 추출은 COM(xlwings/win32com)을 쓰므로 **Windows + MS Office** 가 필요하다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 패키지 import 가능하도록 프로젝트 루트를 경로에 추가.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contentcompare.raw import compact_to_json, extract_raw, raw_to_json  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="문서 → physical_raw / compact_raw 덤프")
    parser.add_argument("path", help="입력 문서 경로 (.xlsx/.xlsm/.docx)")
    parser.add_argument(
        "-o", "--output", help="출력 json 경로(미지정 시 표준출력)", default=None
    )
    parser.add_argument(
        "--compact", action="store_true", help="LLM 입력용 compact_raw 로 출력"
    )
    args = parser.parse_args(argv)

    doc = extract_raw(args.path)
    text = compact_to_json(doc) if args.compact else raw_to_json(doc)

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"raw json 저장: {out} ({len(text):,} bytes)")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

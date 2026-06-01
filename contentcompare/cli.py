"""ContentCompare CLI 진입점.

예)
    contentcompare --config config/config.yaml \
        --reference 기준.xlsx --targets 문서A.docx 문서B.pptx --out report.md
"""

from __future__ import annotations

import argparse
import logging
import sys

from .config import AppConfig
from .llm.health import all_ok, check_llm
from .pipeline import ComparePipeline
from .report import render_markdown


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="contentcompare",
        description="엑셀 기준 문서를 여러 대상 문서와 비교합니다.",
    )
    p.add_argument("--config", help="config.yaml 경로(없으면 기본값 사용)")
    p.add_argument(
        "--check",
        action="store_true",
        help="LLM(chat+embedding) 연결만 점검하고 종료",
    )
    p.add_argument("--reference", help="기준 문서 경로(예: .xlsx)")
    p.add_argument(
        "--targets", nargs="+", help="비교 대상 문서 경로(여러 개)"
    )
    p.add_argument("--out", default="report.md", help="리포트 출력 경로(.md)")
    p.add_argument("-v", "--verbose", action="store_true", help="상세 로그")
    return p


def _progress(i: int, total: int, result) -> None:
    print(f"[{i}/{total}] {result.verdict.value:9} {result.reference.source_label}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if (args.verbose or args.check) else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    config = AppConfig.load(args.config)

    # 연결 점검 모드: chat/embedding 핑 후 종료.
    if args.check:
        print("LLM 연결 점검 중...\n")
        results = check_llm(config)
        for r in results:
            print(r.line())
        ok = all_ok(results)
        print("\n" + ("✅ 모든 점검 통과" if ok else "❌ 일부 점검 실패 — 위 메시지를 확인하세요"))
        return 0 if ok else 1

    if not args.reference or not args.targets:
        print("오류: --reference 와 --targets 가 필요합니다 (또는 --check 로 연결만 점검).")
        return 2

    pipeline = ComparePipeline(config)
    results = pipeline.run(args.reference, args.targets, progress=_progress)

    report = render_markdown(
        results, reference_doc=args.reference, target_docs=args.targets
    )
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\n완료: {len(results)}개 항목 비교 → {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

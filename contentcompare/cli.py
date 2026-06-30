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
from .fact.engine import make_pipeline
from .llm.health import all_ok, check_llm
from .logging_setup import log_print, setup_logging
from .report import render_markdown, save_report


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
    p.add_argument(
        "--engine",
        choices=["rag", "fact"],
        default="rag",
        help="비교 엔진: rag(현행 임베딩 top-k) | fact(신규 fact 파이프라인). 기본 rag",
    )
    p.add_argument("--out", default="report.md", help="리포트 출력 경로(.md)")
    p.add_argument("-v", "--verbose", action="store_true", help="상세 로그")
    return p


def _progress(i: int, total: int, result) -> None:
    log_print(f"[{i}/{total}] {result.verdict.value:9} {result.reference.source_label}")


def _fact_progress(i: int, total: int, path: str) -> None:
    log_print(f"[{i}/{total}] artifacts 저장: {path}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if (args.verbose or args.check) else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    # 실행 로그를 파일로도 저장(파일에는 DEBUG 까지 — 프롬프트/응답 포함).
    log_path = setup_logging()
    log_print(f"로그 파일: {log_path}")

    config = AppConfig.load(args.config)

    # 연결 점검 모드: chat/embedding 핑 후 종료.
    if args.check:
        log_print("LLM 연결 점검 중...\n")
        results = check_llm(config)
        for r in results:
            log_print(r.line())
        ok = all_ok(results)
        log_print("\n" + ("✅ 모든 점검 통과" if ok else "❌ 일부 점검 실패 — 위 메시지를 확인하세요"))
        return 0 if ok else 1

    if not args.reference or not args.targets:
        log_print("오류: --reference 와 --targets 가 필요합니다 (또는 --check 로 연결만 점검).")
        return 2

    pipeline = make_pipeline(config, args.engine)

    # 신규 fact 엔진(Phase F0): raw/compact artifacts 저장까지만 동작한다.
    # 비교/리포트(F1~F6)는 아직 없으므로 미구현 신호를 잡아 안내하고 종료.
    if args.engine == "fact":
        try:
            pipeline.run(args.reference, args.targets, progress=_fact_progress)
        except NotImplementedError as e:
            log_print(f"[fact 엔진 F0] {e}")
        log_print(
            "\nartifacts 저장 완료. fact 비교/리포트는 Phase F1~F6 에서 제공됩니다."
        )
        return 0

    # 현행 RAG 엔진(기본): 기존 동작 그대로.
    results = pipeline.run(args.reference, args.targets, progress=_progress)

    report = render_markdown(
        results, reference_doc=args.reference, target_docs=args.targets
    )
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(report)
    # Streamlit '리포트 보기' 에서도 열람할 수 있도록 reports/ 에 사본을 남긴다.
    saved = save_report(report)
    log_print(f"\n완료: {len(results)}개 항목 비교 → {args.out} (사본: {saved})")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

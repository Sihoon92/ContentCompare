"""ContentCompare CLI 진입점.

예)
    contentcompare --config config/config.yaml \
        --reference 기준.xlsx --targets 문서A.docx 문서B.pptx --out report.md
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from .config import AppConfig
from .fact.engine import make_pipeline
from .llm.health import all_ok, check_llm
from .llm.tracing import get_tracer, run_metadata, trace_run, tracing_enabled
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


def _ensure_out_dir(out_path: str) -> None:
    """``--out`` 의 상위 폴더를 미리 만든다(``report.md`` 처럼 폴더가 없으면 무시)."""
    parent = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(parent, exist_ok=True)


def _print_fact_summaries(summaries: list[dict]) -> int:
    """문서별 성공/실패와 계측값을 출력하고 실패 건수를 반환한다(F3.5).

    라이브 검증은 실패를 관찰하는 작업이므로, 어느 문서가 어디까지 갔는지·LLM 을
    몇 번 불렀는지·fact 를 몇 개 버렸는지를 한눈에 볼 수 있어야 한다.
    """
    failed = 0
    log_print("\n문서별 처리 결과")
    for s in summaries:
        name = os.path.basename(s.get("path", ""))
        stats = s.get("stats") or {}
        if s.get("status") == "ok":
            facts = (stats.get("facts") or {}).get("facts_out", "-")
            log_print(
                f"  ✅ {name}: {len(s.get('stages') or [])}단계 완료 "
                f"(LLM {s.get('llm_calls', 0)}회, fact {facts}개) → {s.get('artifacts_dir')}"
            )
        else:
            failed += 1
            done = ", ".join(s.get("stages") or []) or "없음"
            log_print(f"  ❌ {name}: {s.get('error')} (완료 단계: {done})")
        for key in ("records", "facts"):
            detail = stats.get(key)
            if detail:
                log_print(f"       {key}: {detail}")
        if stats.get("llm"):
            log_print(f"       llm: {stats['llm']}")
    return failed


def _warn_if_cached(config, summaries: list[dict]) -> None:
    """캐시 적중 단계는 LLM 을 부르지 않아 trace 가 없다는 것을 알린다.

    이 안내가 없으면 "추적을 켰는데 왜 비어 있지?" 로 시간을 잃는다. 원인은
    ``artifacts`` 지문 캐싱이며, 해당 문서 폴더를 지우고 다시 돌리면 채워진다.
    """
    if not tracing_enabled(config):
        return
    hits = [
        os.path.basename(str(s.get("path", "")))
        for s in summaries
        if any((s.get("stats", {}).get(k) or {}).get("cached")
               for k in ("records", "facts"))
    ]
    if hits:
        log_print(
            f"\n[추적] 캐시 적중 {len(hits)}건({', '.join(hits)}) — 해당 단계는 "
            f"LLM 을 호출하지 않아 기록이 없습니다.\n"
            f"       전체를 다시 보려면 artifacts/<문서> 폴더를 지우고 실행하세요."
        )


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

    # 출력 폴더는 **실행 전에** 만든다. 마지막 쓰기 시점에 만들면 수 분짜리 실행이
    # 끝난 뒤 FileNotFoundError 로 결과를 통째로 잃는다(out/ 은 .gitignore 대상이라
    # 새로 clone/pull 한 환경에는 존재하지 않는다).
    _ensure_out_dir(args.out)

    pipeline = make_pipeline(config, args.engine)

    # LLM 입출력 추적(Langfuse). 미설정이면 NullTracer 라 아무 일도 하지 않는다.
    tracer = get_tracer(config)
    run_name = f"contentcompare {args.engine}"
    meta = run_metadata(config, args.engine, args.reference, args.targets)

    # 신규 fact 엔진: 문서를 fact 로 정규화·검증한 뒤 fact 끼리 비교해 리포트를 만든다.
    if args.engine == "fact":
        with trace_run(tracer, run_name, meta):
            result = pipeline.run(args.reference, args.targets, progress=_fact_progress)
        failed = _print_fact_summaries(result.summaries)
        _warn_if_cached(config, result.summaries)
        if not result.markdown:
            log_print("\n비교할 fact 가 부족해 리포트를 만들지 못했습니다(위 오류를 확인하세요).")
            return 1
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(result.markdown)
        saved = save_report(result.markdown)
        stats = result.compare_stats
        log_print(
            f"\n완료: {stats.get('comparisons', 0)}건 판정 "
            f"(LLM 위임 {stats.get('decided_by_llm', 0)}건) → {args.out} (사본: {saved})"
        )
        return 1 if failed else 0

    # 현행 RAG 엔진(기본): 기존 동작 그대로.
    with trace_run(tracer, run_name, meta):
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

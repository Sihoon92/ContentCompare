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
from .logging_setup import (apply_logger_overrides, log_print, setup_console,
                            setup_logging)
from .report import render_markdown, save_report
from .timeline import (
    ERROR_STATUSES,
    diagnose,
    format_duration,
    get_timeline,
    load_timeline,
    stage_durations,
    start_timeline,
)


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
    p.add_argument(
        "--quiet",
        action="store_true",
        help="실행 타임라인을 화면에 띄우지 않는다(파일 기록은 유지)",
    )
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


def _print_stage_durations(top: int = 8) -> None:
    """단계별 소요를 긴 순으로 출력한다(타임라인이 꺼져 있으면 조용히 넘어간다).

    ``_print_fact_summaries`` 를 **대체하지 않고 덧붙인다** — 그쪽은 fact 수·드롭
    사유를 담당하고 이쪽은 시간을 담당한다. 둘을 합치면 어느 쪽도 읽히지 않는다.
    """
    path = getattr(get_timeline(), "path", "")
    if not path:
        return
    events = load_timeline(path)
    rows = stage_durations(events)
    if rows:
        log_print("\n단계별 소요 (긴 순)")
        width = max(len(r["name"]) for r in rows[:top])
        for row in rows[:top]:
            mark = "  ✗ 실패" if row["status"] in ERROR_STATUSES else ""
            log_print(f"  {row['name']:<{width}}  "
                      f"{format_duration(row['duration_ms']):>8}{mark}")

    # 관측된 증상 → 다음 조치. 실패한 자리에서 알려주지 않으면, 사람이 코드를 읽어
    # 같은 결론에 다시 도달해야 한다(이번 진단이 정확히 그랬다).
    hints = diagnose(events)
    if hints:
        log_print("\n다음에 볼 것")
        for hint in hints:
            log_print(f"  · {hint}")


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
    # ``basicConfig`` 를 쓰지 않는다 — 그쪽은 **루트 로거**의 레벨만 정하고 자신이 만든
    # 핸들러는 ``NOTSET`` 으로 두는데, 바로 아래 ``setup_logging`` 이 파일에 DEBUG 를
    # 담으려고 그 루트를 낮추는 순간 화면이 통째로 열린다(실측: 모든 줄이 두 번씩 나오고
    # 프롬프트·HTTP 페이로드까지 터미널에 쏟아졌다). 핸들러가 자기 레벨을 가지면 두 호출의
    # 순서가 더 이상 화면을 좌우하지 않는다.
    setup_console(level=logging.INFO if (args.verbose or args.check) else logging.WARNING)
    # 실행 로그를 파일로도 저장(파일에는 DEBUG 까지 — 프롬프트/응답 포함).
    log_path = setup_logging()
    log_print(f"로그 파일: {log_path}")

    config = AppConfig.load(args.config)
    # 기본 잡음 필터 위에 설정 파일의 조정을 얹는다(config 는 여기서야 읽히므로 순서가 이렇다).
    apply_logger_overrides(config.logging.quiet_extra, config.logging.verbose_extra)

    # 실행 타임라인 — **연결 점검을 포함해** 모든 LLM 호출 앞에서 세운다.
    # ``--quiet`` 는 화면만 끄고 파일 기록은 남긴다(조용한 것과 없는 것은 다르다).
    timeline_path = start_timeline(config, console=not args.quiet)
    if timeline_path:
        log_print(f"타임라인: {timeline_path}")

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
        _print_stage_durations()
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

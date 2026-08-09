"""``⚪ 대상에 없음`` 의 원인을 묻는 CLI — 화면 없이 쓰는 진단 도구.

리포트는 "대상에서 못 찾았다"까지만 말한다. 이 스크립트는 그 다음을 말한다:
recall 이 후보를 못 만든 것인지, LLM 이 판정을 못 한 것인지, 근거 게이트가 연결을
죽인 것인지, 애초에 대상 문서에서 fact 가 안 나온 것인지.

사용법::

    python scripts/why_missing.py                          # 최근 실행의 누락 전체 요약
    python scripts/why_missing.py 공칭전압                   # 항목 하나를 끝까지 추적
    python scripts/why_missing.py 공칭전압 spec_en.docx      # 대상 문서까지 지정
    python scripts/why_missing.py --run _runs/en_word --all # 특정 실행의 전건 상세
    python scripts/why_missing.py --list                    # 실행 목록
    python scripts/why_missing.py --diff _runs/en_word      # 최근 실행과 판정 비교
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contentcompare.fact.artifact_reader import (  # noqa: E402
    list_runs,
    load_run,
    load_snapshot,
)
from contentcompare.fact.missing_trace import (  # noqa: E402
    CAUSE_LABEL,
    describe,
    summarize,
    trace_all_missing,
    trace_missing,
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="'대상에 없음' 판정의 원인을 추적한다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("entity", nargs="?", default="", help="기준 문서의 항목명(부분 일치)")
    p.add_argument("target", nargs="?", default="", help="대상 문서 이름(부분 일치)")
    p.add_argument("--artifacts", default="artifacts", help="산출물 루트(기본: artifacts)")
    p.add_argument("--run", default="", help="실행 라벨(기본: 가장 최근)")
    p.add_argument("--list", action="store_true", help="실행 목록만 출력")
    p.add_argument("--all", action="store_true", help="누락 전건을 상세히 출력")
    p.add_argument("--diff", default="", help="이 실행과 판정을 비교할 다른 실행 라벨")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.list:
        return _print_runs(args.artifacts)

    snap = load_run(args.artifacts, args.run)
    if snap is None:
        print(f"'{args.artifacts}' 에서 실행을 찾지 못했습니다 "
              f"(comparison_result.json 이 있는 폴더가 없습니다).")
        return 1

    print(f"실행: {snap.label}  |  기준: {snap.reference_doc}  "
          f"|  대상: {', '.join(snap.target_docs) or '(없음)'}")
    for problem in snap.problems:
        print(f"  ⚠ {problem}")
    print()

    if args.diff:
        return _print_diff(snap, args.artifacts, args.diff)

    traces = trace_all_missing(snap)
    if not traces:
        print("이 실행에는 '대상에 없음' 판정이 없습니다.")
        return 0

    if args.entity:
        traces = [t for t in traces if args.entity in t.entity_name]
    if args.target:
        traces = [t for t in traces if args.target in t.target_doc]
    if not traces:
        print(f"조건에 맞는 누락 항목이 없습니다(항목='{args.entity}', 대상='{args.target}').")
        return 1

    # 항목을 지정했거나 --all 이면 상세, 아니면 원인별 집계.
    if args.entity or args.all:
        for trace in traces:
            print(describe(trace))
            print()
    else:
        _print_summary(traces)
    return 0


# --------------------------------------------------------------------------- #
def _print_runs(root: str) -> int:
    runs = list_runs(root)
    if not runs:
        print(f"'{root}' 에서 실행을 찾지 못했습니다.")
        return 1
    for i, ref in enumerate(runs):
        mark = " (스냅샷)" if ref.is_snapshot else ""
        print(f"  {'*' if i == 0 else ' '} {ref.label}{mark}")
    print("\n* = 가장 최근. --run <라벨> 로 고를 수 있습니다.")
    return 0


def _print_summary(traces) -> None:
    print(f"'대상에 없음' {len(traces)}건의 원인 분포\n")
    counts = summarize(traces)
    for cause, n in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {n:4d}  {CAUSE_LABEL[cause]}")
    print("\n항목별로 보려면 항목명을 인자로 주거나 --all 을 붙이세요.")


def _print_diff(snap, root: str, other_label: str) -> int:
    """두 실행의 판정을 대조한다 — ``_runs/`` 스냅샷 손비교를 대신한다."""
    other = load_run(root, other_label)
    if other is None:
        print(f"비교 대상 실행 '{other_label}' 을 찾지 못했습니다.")
        return 1

    def _index(s):
        return {(c.get("entity_name"), c.get("target_doc")): c.get("result")
                for c in s.comparisons}

    now, before = _index(snap), _index(other)
    keys = sorted(set(now) | set(before))
    changed = [(k, before.get(k), now.get(k)) for k in keys if before.get(k) != now.get(k)]

    print(f"판정 비교: {other.label}  →  {snap.label}\n")
    if not changed:
        print("  판정이 모두 같습니다.")
        return 0
    for (entity, doc), was, is_ in changed:
        print(f"  {entity} — {doc}: {was or '(없음)'} → {is_ or '(없음)'}")
    print(f"\n  변화 {len(changed)}건 / 전체 {len(keys)}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

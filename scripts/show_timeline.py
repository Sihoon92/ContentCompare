"""실행 타임라인 조회 — "언제 무엇이 왜 멈췄나"를 나중에 다시 본다.

실시간 콘솔은 흘러가 버리고, 긴 실행에서는 스크롤을 거슬러 올라가기 어렵다. 이
스크립트는 ``artifacts/_timeline/<실행>.jsonl`` 을 읽어 같은 모양으로 되돌려 준다 —
표현은 :func:`contentcompare.timeline.format_line` 하나를 공유하므로 화면에서 본 것과
글자까지 같다(다르면 대조가 안 된다).

사용법::

    python scripts/show_timeline.py                  # 최근 실행 전체
    python scripts/show_timeline.py --errors         # 실패·재시도·대기만
    python scripts/show_timeline.py --slow 60        # 60초 넘게 걸린 것만
    python scripts/show_timeline.py --stage records  # 단계 이름 부분일치
    python scripts/show_timeline.py --list           # 남아 있는 실행 목록
    python scripts/show_timeline.py --run 20260826_184201
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from contentcompare.config import AppConfig  # noqa: E402
from contentcompare.timeline import (  # noqa: E402
    ERROR_STATUSES,
    RETRY,
    WAIT,
    diagnose,
    format_duration,
    format_line,
    list_timelines,
    load_timeline,
    stage_durations,
    timeline_dir,
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="실행 타임라인 조회")
    p.add_argument("--config", default="", help="설정 파일(기록 위치를 읽는다)")
    p.add_argument("--dir", default="", help="타임라인 폴더(설정보다 우선)")
    p.add_argument("--run", default="", help="실행 라벨(기본: 가장 최근)")
    p.add_argument("--errors", action="store_true", help="실패·재시도·대기만")
    p.add_argument("--slow", type=float, default=0.0, help="N초 이상 걸린 것만")
    p.add_argument("--stage", default="", help="단계 이름 부분일치 필터")
    p.add_argument("--list", action="store_true", help="실행 목록만 출력")
    p.add_argument("--no-summary", action="store_true", help="단계별 소요 요약 생략")
    return p


def _root(args) -> str:
    if args.dir:
        return args.dir
    return timeline_dir(AppConfig.load(args.config or None))


def _pick(root: str, label: str):
    """실행 파일 하나 고르기. 라벨은 부분일치를 허용한다(전체 타임스탬프 타이핑 방지)."""
    runs = list_timelines(root)
    if not runs:
        return None
    if not label:
        return runs[0]
    for path in runs:
        if label in path.stem:
            return path
    return None


def _keep(event, args) -> bool:
    if args.stage and args.stage not in event.name:
        return False
    if args.slow and event.duration_ms < args.slow * 1000:
        return False
    if args.errors:
        # 재시도·대기는 그 자체로 성공해도 봐야 한다 — 느려진 이유가 거기 있다.
        return event.status in ERROR_STATUSES or event.kind in (RETRY, WAIT)
    return True


def main(argv: list[str] | None = None) -> int:
    # 기존 스크립트 관례 — Windows 기본 콘솔(cp949)에서 기호 하나에 죽지 않게 한다.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    args = build_parser().parse_args(argv)
    root = _root(args)
    runs = list_timelines(root)

    if args.list or not runs:
        if not runs:
            print(f"타임라인이 없습니다: {root}")
            print("  logging.timeline 이 켜져 있는지, 실행을 한 번 했는지 확인하세요.")
            return 1
        print(f"타임라인 {len(runs)}건 ({root})")
        for path in runs:
            events = load_timeline(path)
            failed = sum(1 for e in events if e.status in ERROR_STATUSES)
            when = f"{datetime.fromtimestamp(events[0].ts):%Y-%m-%d %H:%M}" if events else "-"
            mark = f"  ✗ 실패 {failed}건" if failed else ""
            print(f"  {path.stem:<20} {len(events):>5}건  {when}{mark}")
        return 0

    path = _pick(root, args.run)
    if path is None:
        print(f"실행을 찾지 못했습니다: {args.run!r} (--list 로 목록 확인)")
        return 1

    events = load_timeline(path)
    shown = [e for e in events if _keep(e, args)]
    print(f"# {path}  ({len(shown)}/{len(events)}건)\n")
    for event in shown:
        print(format_line(event))

    if args.no_summary:
        return 0

    rows = stage_durations(events)
    if rows:
        print("\n단계별 소요 (긴 순)")
        width = max(len(r["name"]) for r in rows)
        for row in rows:
            mark = "  ✗ 실패" if row["status"] in ERROR_STATUSES else ""
            print(f"  {row['name']:<{width}}  "
                  f"{format_duration(row['duration_ms']):>8}{mark}")

    failures = [e for e in events if e.status in ERROR_STATUSES
                and e.kind == "stage_end"]
    if failures:
        print(f"\n실패한 단계 {len(failures)}건 — 가장 안쪽이 원인 지점입니다")
        for event in failures:
            print(f"  {event.name}: {(event.detail or {}).get('error', event.status)}")

    hints = diagnose(events)
    if hints:
        print("\n다음에 볼 것")
        for hint in hints:
            print(f"  · {hint}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

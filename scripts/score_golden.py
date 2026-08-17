"""골든셋 × 산출물을 조인해 채점한다 — LLM 없이 산출물만 읽는다.

``compare_engines.py`` 와 다른 점이 둘 있고, 둘 다 의도된 것이다.

1. **항목 단위로 접지 않는다.** ``compare_engines.py`` 는 rag 와 비교하려고 기준 항목
   하나로 접는데(collapse), 그러면 같은 항목이 여러 행에 있는 기준 문서에서 행별
   판정이 뭉개진다. 여기서는 **기준 엑셀 row 를 키로** 1:1 조인한다 — 항목명은
   중복·표기흔들림이 있어 조인 키로 부적합하다(실측: 104건 전건 조인 성공).
2. **오답의 원인까지 붙인다.** ``missing_trace`` 를 걸어 "골든은 있다는데 엔진이
   missing" 인 건이 recall 실패인지 개념 차단인지 가른다.

세 가지를 출력한다:

- 혼동행렬 — 어느 칸에 오답이 몰렸는가
- 유실 건의 원인 분포 + **정답 상대가 후보에 있었는가** (recall 이 범인인지 판별)
- 안전장치 — 골든이 ``missing`` 인 건의 적중 수. 차단을 풀면 이 값이 먼저 무너진다.

사용법::

    python scripts/score_golden.py
    python scripts/score_golden.py --run _runs/e2b_baseline
    python scripts/score_golden.py --run _runs/prompt_v2 --detail
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contentcompare.fact.artifact_reader import load_run  # noqa: E402
from contentcompare.fact.missing_trace import (  # noqa: E402
    CAUSE_LABEL,
    trace_all_missing,
)

LABELS = ["match", "mismatch", "missing", "unknown"]

# 후보가 정답 상대인지 보는 토큰 겹침 하한. 2 는 "Weight" 하나로 붙는 오탐을 걸러내되
# 영문 항목명이 짧아 3 이면 정답까지 놓치는 지점에서 고른 값이다 — 휴리스틱이므로
# 이 판정은 참고치이고, 확정하려면 candidate_pairs.json 을 직접 볼 것.
_MIN_OVERLAP = 2
_WORD = re.compile(r"[a-z0-9]+")
_STOP = {"the", "of", "a", "and", "to", "in", "at", "for"}


def _toks(s: str) -> set[str]:
    return set(_WORD.findall((s or "").lower())) - _STOP


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="골든셋으로 fact 엔진 판정을 채점한다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--artifacts", default="artifacts", help="산출물 루트(기본: artifacts)")
    p.add_argument("--run", default="", help="실행 라벨(기본: 가장 최근)")
    p.add_argument("--golden", default="golden/spec_en_골든셋.jsonl", help="골든 jsonl")
    p.add_argument("--target", default="spec_en.docx", help="대상 문서 이름")
    p.add_argument("--detail", action="store_true", help="유실 건을 한 줄씩 출력")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    snap = load_run(args.artifacts, args.run)
    if snap is None:
        print(f"실행을 찾지 못했습니다: {args.run or '(최근)'}")
        return 1

    with open(args.golden, encoding="utf-8") as f:
        golden = [json.loads(line) for line in f if line.strip()]

    joined = _join(golden, snap.comparisons)
    if not joined:
        print("골든과 판정을 조인하지 못했습니다 — 기준 문서가 다른 실행일 수 있습니다.")
        return 1

    print(f"실행: {snap.label}  |  대상: {args.target}")
    print(f"골든 {len(golden)}건 · 판정 {len(snap.comparisons)}건 · 조인 {len(joined)}건\n")

    _print_matrix(joined)
    _print_causes(joined, snap, args.target, args.detail)
    return 0


def _join(golden: list[dict], comparisons: list[dict]) -> list[tuple[dict, dict]]:
    """기준 엑셀 row 를 키로 (골든, 판정) 을 짝짓는다."""
    by_row: dict[int, dict] = {}
    for c in comparisons:
        row = ((c.get("reference") or {}).get("source") or {}).get("row")
        if row is not None:
            by_row.setdefault(row, c)
    out = []
    for g in golden:
        row = (g.get("reference") or {}).get("row")
        if row is not None and row in by_row:
            out.append((g, by_row[row]))
    return out


def _print_matrix(joined: list[tuple[dict, dict]]) -> None:
    cm = Counter((g["expected"], c["result"]) for g, c in joined)
    print("혼동행렬  (행=골든 정답, 열=엔진 판정)")
    print(f"{'정답＼판정':<12}" + "".join(f"{x:>10}" for x in LABELS) + f"{'계':>8}")
    for e in LABELS:
        vals = [cm.get((e, p), 0) for p in LABELS]
        print(f"{e:<12}" + "".join(f"{v:>10}" for v in vals) + f"{sum(vals):>8}")

    correct = sum(v for (e, p), v in cm.items() if e == p)
    n = len(joined)
    print(f"\n정확도: {correct}/{n} ({correct * 100 // n}%)")

    # 안전장치 — 차단을 느슨하게 하면 이 값이 먼저 무너진다.
    gm = sum(1 for g, _ in joined if g["expected"] == "missing")
    hit = cm.get(("missing", "missing"), 0)
    print(f"안전장치 — 골든 missing 적중: {hit}/{gm}  (줄면 틀린 연결이 늘었다는 뜻)\n")


def _print_causes(joined, snap, target: str, detail: bool) -> None:
    traces = {t.ref_fact_id: t for t in trace_all_missing(snap, target)}
    lost = [(g, c) for g, c in joined
            if g["expected"] != "missing" and c["result"] == "missing"]

    print("=" * 66)
    print(f"골든은 있다는데 엔진이 missing — {len(lost)}건")
    print("=" * 66)
    causes = Counter()
    for _, c in lost:
        t = traces.get((c.get("reference") or {}).get("fact_id"))
        causes[t.cause if t else "(추적불가)"] += 1
    for cause, n in causes.most_common():
        print(f"  {n:>3}  {CAUSE_LABEL.get(cause, cause)}")

    in_cand, rows = _check_candidates(lost, snap, traces, target)
    print(f"\n정답 상대가 후보에 있었음: {in_cand}/{len(rows)}건"
          f"  (0 에 가까우면 recall 문제, 크면 F7 판정 문제)")

    if detail and rows:
        print(f"\n{'기준항목':<26}{'원인':<15}{'순위':>4} {'후보 1위':<38}{'점수':>7}")
        print("-" * 92)
        for name, cause, rank, cname, score in sorted(rows, key=lambda r: (r[1], -r[2])):
            print(f"{name[:25]:<26}{cause:<15}{(rank or '-'):>4} "
                  f"{cname[:37]:<38}{score:>7.3f}")


def _check_candidates(lost, snap, traces, target: str):
    """골든의 ``target_text`` 와 후보를 대조해 '정답이 후보에 있었는가'를 본다."""
    facts = snap.facts_of(target)
    in_cand, rows = 0, []
    for g, c in lost:
        fid = (c.get("reference") or {}).get("fact_id")
        t = traces.get(fid)
        if not t or t.cause not in ("blocked", "llm_undecided", "evidence_gate"):
            continue
        ranked = (snap.ranked_for(fid, target) or {}).get("ranked") or []
        want = _toks(g.get("target_text"))
        hit = None
        for i, cand in enumerate(ranked):
            tf = facts.get(cand["fact_id"], {})
            got = _toks(tf.get("evidence_text")) | _toks(cand.get("entity_name"))
            if len(want & got) >= _MIN_OVERLAP:
                hit = (i + 1, cand)
                break
        if hit:
            in_cand += 1
            rows.append((g["entity_name"], t.cause, hit[0],
                         hit[1]["entity_name"], hit[1]["score"]))
        else:
            rows.append((g["entity_name"], t.cause, 0, "(후보 중 정답 없음)", 0.0))
    return in_cand, rows


if __name__ == "__main__":
    raise SystemExit(main())

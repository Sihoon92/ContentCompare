"""두 엔진(rag · fact)을 같은 입력에 돌려 **실측으로 비교**한다 (Phase F6).

``FACT_PIPELINE_PLAN.md`` §1 의 비교표는 설계 시점의 예상이었다. 이 스크립트는 같은
기준/대상 문서에 두 엔진을 돌리고 **골든셋으로 채점**해 그 표를 실측치로 바꾼다.

채점을 위한 두 가지 정렬(스크립트에 명시해 두는 것이 중요하다):

1. **판정 단위 정렬** — fact 엔진은 (기준 항목 × 대상 문서)마다 판정하고, RAG 엔진은
   기준 항목 하나에 대해 대상 문서 전체를 묶어 한 번 판정한다. 그래서 채점은
   **기준 항목 단위로 접어서(collapse)** 한다. 접는 규칙은 ``--collapse`` 로 고른다
   (기본 ``mismatch-first`` — 아래 :data:`COLLAPSE_ORDERS` 주석 참고).
2. **어휘 정렬** — RAG 의 ``Verdict`` 를 골든 4분류로 옮긴다:
   ``same→match``, ``partial|different→mismatch``, ``unknown→unknown``,
   ``not_found→missing``.

사용:
    python scripts/compare_engines.py --config config/config.yaml \\
        --reference samples/자표준문서.xlsx \\
        --targets samples/자표준_규격서.docx samples/자표준_발표.pptx \\
        --golden golden/자표준_골든셋.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import unicodedata
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contentcompare.config import AppConfig  # noqa: E402
from contentcompare.models import Verdict  # noqa: E402

MATCH, MISMATCH, MISSING, UNKNOWN = "match", "mismatch", "missing", "unknown"

# 여러 대상 문서의 판정을 기준 항목 하나로 접는 규칙. 둘 다 방어 가능해서 고를 수 있게 뒀다.
#  - mismatch-first(기본): 문서 간 정합성 검사가 목적이므로 **불일치가 하나라도 있으면
#    그것이 보고할 사실**이다.
#  - match-first: "이 내용이 어딘가에 존재하는가"를 묻는 관점.
COLLAPSE_ORDERS = {
    "mismatch-first": (MISMATCH, MATCH, UNKNOWN, MISSING),
    "match-first": (MATCH, MISMATCH, UNKNOWN, MISSING),
}
_NON_WORD = re.compile(r"[^0-9a-z가-힣]+")

_VERDICT_TO_GOLDEN = {
    Verdict.SAME: MATCH,
    Verdict.PARTIAL: MISMATCH,
    Verdict.DIFFERENT: MISMATCH,
    Verdict.UNKNOWN: UNKNOWN,
    Verdict.NOT_FOUND: MISSING,
}


def norm(text: str) -> str:
    return _NON_WORD.sub("", unicodedata.normalize("NFKC", text or "").lower())


class CountingChat:
    """LLM 호출 수를 세는 래퍼 — 두 엔진의 비용을 같은 기준으로 비교하기 위함."""

    def __init__(self, inner):
        self.inner = inner
        self.calls = 0

    def complete(self, system, user, *, temperature=0.0):
        self.calls += 1
        return self.inner.complete(system, user, temperature=temperature)


# --------------------------------------------------------------------------- #
# 골든셋
# --------------------------------------------------------------------------- #
def load_golden(path: str, order: tuple[str, ...]) -> tuple[dict[str, str], list[dict]]:
    """골든 jsonl → (기준 항목 단위로 접은 정답, 원본 레코드)."""
    rows = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
    per_entity: dict[str, list[str]] = {}
    for r in rows:
        per_entity.setdefault(norm(r["entity_name"]), []).append(r["expected"])
    return {k: collapse(v, order) for k, v in per_entity.items()}, rows


def collapse(results: list[str], order: tuple[str, ...]) -> str:
    for level in order:
        if level in results:
            return level
    return MISSING


# --------------------------------------------------------------------------- #
# 엔진 실행
# --------------------------------------------------------------------------- #
def run_fact(config: AppConfig, reference: str, targets: list[str]) -> dict:
    from contentcompare.fact.pipeline import FactPipeline

    pipe = FactPipeline(config)
    chat = CountingChat(pipe._chat_client())
    pipe._chat = chat

    started = time.time()
    result = pipe.run(reference, targets)
    elapsed = time.time() - started

    per_entity: dict[str, list[str]] = {}
    for c in result.comparisons:
        per_entity.setdefault(norm(c.reference_fact.entity_name), []).append(c.result)
    return {
        "engine": "fact",
        "elapsed": elapsed,
        "llm_calls": chat.calls,
        "verdicts": per_entity,
        "raw_count": len(result.comparisons),
        "extra": {
            "코드 판정": sum(1 for c in result.comparisons if c.decided_by == "code"),
            "LLM 판정": sum(1 for c in result.comparisons if c.decided_by == "llm"),
        },
    }


def run_rag(config: AppConfig, reference: str, targets: list[str]) -> dict:
    from contentcompare.pipeline import ComparePipeline

    pipe = ComparePipeline(config)
    chat = CountingChat(pipe.llm)
    pipe.llm = chat
    pipe.comparator.llm = chat  # Comparator 가 들고 있는 참조도 교체해야 센다

    started = time.time()
    results = pipe.run(reference, targets)
    elapsed = time.time() - started

    return {
        "engine": "rag",
        "elapsed": elapsed,
        "llm_calls": chat.calls,
        "verdicts": _rag_verdicts(results),
        "raw_count": len(results),
        "extra": {},
    }


def _rag_verdicts(results) -> dict[str, list[str]]:
    """RAG 결과를 기준 항목 텍스트 기준으로 골든 어휘로 옮긴다.

    RAG 의 기준 항목은 엑셀 행 전체 텍스트라 골든의 항목명과 정확히 같지 않다.
    정규화 후 **항목명이 행 텍스트에 포함되는지**로 연결한다(채점 시 사용).
    """
    out: dict[str, list[str]] = {}
    for r in results:
        out.setdefault(norm(r.reference.text), []).append(
            _VERDICT_TO_GOLDEN.get(r.verdict, UNKNOWN)
        )
    return out


# --------------------------------------------------------------------------- #
# 채점
# --------------------------------------------------------------------------- #
def score(run: dict, golden: dict[str, str], order: tuple[str, ...]) -> dict:
    """골든 항목마다 엔진 판정을 찾아 채점. 못 찾으면 미검출로 센다."""
    hits, misses, confusion, unmatched = 0, [], Counter(), 0
    for entity, expected in golden.items():
        raw = run["verdicts"].get(entity)
        if raw is None:
            # RAG 는 키가 행 전체 텍스트라 포함 관계로 한 번 더 찾는다.
            raw = next((v for k, v in run["verdicts"].items() if entity and entity in k), None)
        got = collapse(raw, order) if raw else None
        if got is None:
            unmatched += 1
            misses.append((entity, expected, "(판정 없음)"))
            continue
        confusion[(expected, got)] += 1
        if got == expected:
            hits += 1
        else:
            misses.append((entity, expected, got))
    total = len(golden)
    return {
        "hits": hits, "total": total, "accuracy": hits / total if total else 0.0,
        "misses": misses, "confusion": confusion, "unmatched": unmatched,
    }


# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="rag vs fact 엔진 실측 비교")
    p.add_argument("--config", default="config/config.yaml")
    p.add_argument("--reference", required=True)
    p.add_argument("--targets", nargs="+", required=True)
    p.add_argument("--golden", required=True)
    p.add_argument("--engines", nargs="+", default=["fact", "rag"], choices=["fact", "rag"])
    p.add_argument("--out", default="out/engine_benchmark.md")
    p.add_argument("--collapse", default="mismatch-first", choices=sorted(COLLAPSE_ORDERS))
    p.add_argument("--runs-file", default="out/engine_runs.json",
                   help="엔진 판정 저장 경로(재채점을 위해 항상 저장)")
    p.add_argument("--from-runs", action="store_true",
                   help="엔진을 다시 돌리지 않고 저장된 판정으로만 재채점")
    args = p.parse_args(argv)

    order = COLLAPSE_ORDERS[args.collapse]
    config = AppConfig.load(args.config)
    golden, golden_rows = load_golden(args.golden, order)
    print(f"골든셋 {len(golden_rows)}항목 → 기준 항목 {len(golden)}개로 접어서 채점\n")

    runs = []
    if args.from_runs:
        runs = json.load(open(args.runs_file, encoding="utf-8"))
        print(f"저장된 판정으로 재채점: {args.runs_file}")
    else:
        for engine in args.engines:
            print(f"[{engine}] 실행 중...")
            try:
                run = (run_fact if engine == "fact" else run_rag)(
                    config, args.reference, args.targets
                )
            except Exception as e:  # noqa: BLE001 — 한 엔진이 죽어도 다른 쪽 결과는 남긴다
                print(f"  ❌ 실패: {type(e).__name__}: {e}")
                continue
            runs.append(run)
        os.makedirs(os.path.dirname(os.path.abspath(args.runs_file)), exist_ok=True)
        with open(args.runs_file, "w", encoding="utf-8") as f:
            json.dump(runs, f, ensure_ascii=False, indent=2)

    for run in runs:
        run["score"] = score(run, golden, order)
        s = run["score"]
        print(f"  [{run['engine']}] 정확도 {s['hits']}/{s['total']} ({s['accuracy']:.0%}) · "
              f"{run['elapsed']:.0f}초 · LLM {run['llm_calls']}회")

    _report(args.out, runs, args.reference, args.targets, len(golden), args.collapse)
    print(f"\n상세: {args.out}")
    return 0


def _report(path: str, runs: list[dict], reference: str, targets: list[str], n: int,
            collapse_rule: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    lines = [
        "# 엔진 비교 실측 (rag vs fact)",
        "",
        f"- 기준 문서: `{reference}`",
        f"- 대상 문서: {', '.join(f'`{t}`' for t in targets)}",
        f"- 채점 단위: 기준 항목 {n}개(골든셋을 항목 단위로 접음, 규칙: {collapse_rule})",
        "",
        "| 엔진 | 정확도 | 소요 시간 | LLM 호출 | 판정 건수 | 비고 |",
        "|---|---|---|---|---|---|",
    ]
    for r in runs:
        s = r["score"]
        extra = ", ".join(f"{k} {v}" for k, v in r["extra"].items()) or "-"
        lines.append(
            f"| {r['engine']} | {s['hits']}/{s['total']} ({s['accuracy']:.0%}) "
            f"| {r['elapsed']:.0f}초 | {r['llm_calls']}회 | {r['raw_count']} | {extra} |"
        )
    for r in runs:
        s = r["score"]
        lines += ["", f"## {r['engine']} — 오답 {len(s['misses'])}건", ""]
        if s["misses"]:
            lines += ["| 기준 항목 | 정답 | 판정 |", "|---|---|---|"]
            lines += [f"| {e} | {exp} | {got} |" for e, exp, got in s["misses"]]
        else:
            lines.append("(없음)")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    sys.exit(main())

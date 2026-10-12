"""골든셋 정답이 **후보 안에 들어오는 비율**을 ``--augment`` 모드별로 잰다.

``rank_candidates.py`` 는 한 항목을 본다. 그래서 "이 항목이 16위→1위가 됐다"는 알아도
**정책으로 삼을 만한가**는 답하지 못한다 — 그 보강이 다른 항목의 정답을 밀어냈을 수
있기 때문이다. §14 에서 F7 축 개정이 준수율 98.5% 를 찍고도 차단은 35건만 줄인 것이
같은 함정이다.

이 스크립트는 골든 76건(``target_text`` 가 있는 행)에 대해 **정답 대상 fact 의 순위**를
세 모드에서 각각 재고, 개선/악화를 항목 단위로 보여준다. chat LLM 은 부르지 않는다 —
recall 은 임베딩만 쓴다.

⚠️ **정답 fact 판별은 휴리스틱이다.** 골든은 ``target_text``(원문 문장)만 주고 대상
fact_id 를 주지 않으므로 토큰 겹침으로 찾는다. 무엇을 정답으로 봤는지는 반드시
``--show-answers`` 로 눈으로 확인할 것 — 판별이 틀리면 이 표 전체가 조용히 거짓이 된다.

판별은 ``evidence_text``/``entity_name`` 으로만 하므로 ``--augment``(``search_text`` 만
변경)와 무관하다. 그래야 세 모드가 같은 정답을 두고 겨룬다.

사용법::

    python scripts/sweep_recall.py
    python scripts/sweep_recall.py --modes off,full
    python scripts/sweep_recall.py --detail
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)

from contentcompare.config import AppConfig  # noqa: E402
from contentcompare.fact.artifact_reader import load_run  # noqa: E402
from contentcompare.fact.fact_matcher import FactMatcher  # noqa: E402
from rank_candidates import _augment_facts, _embedder, _facts  # noqa: E402
from score_golden import _join  # noqa: E402

MODES = ("off", "numbers", "full")

# 토큰화를 ``score_golden._toks`` 와 **일부러 다르게** 한다. 그쪽 규칙(``[a-z0-9]+``)은
# "0.1C(4.55V)" 를 ['0','1c','4','55v'] 로 쪼개서, 숫자 조각만 공유하는 엉뚱한 fact 가
# 정답으로 잡힌다(실측: 'Charging Voltage' 가 '4','55v' 겹침 2로 정답 판정). 겹침 **개수**
# 하한도 같은 이유로 못 쓴다 — 여기서는 소수를 통째로 두고 골든 토큰 대비 **비율**로 본다.
#
# ⚠️ 이 차이는 score_golden 의 "정답 상대가 후보에 있었음" 수치가 과대평가일 수 있음을
# 뜻한다. 그 수치는 §14 의 근거이므로 함께 재검토할 것(이 스크립트에서 고치지 않는다).
_TOK = re.compile(r"\d+(?:\.\d+)?|[a-z]+|[가-힣]+")
_STOP = {"the", "of", "a", "and", "to", "in", "at", "for", "c", "v"}


def _toks(s: str) -> set[str]:
    return set(_TOK.findall((s or "").lower())) - _STOP


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="골든 정답이 후보에 들어오는 비율을 보강 모드별로 잰다(chat LLM 미사용).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--config", default="config/config.yaml", help="설정 파일")
    p.add_argument("--artifacts", default="", help="산출물 루트(기본: 설정의 artifacts_dir)")
    p.add_argument("--run", default="", help="실행 라벨(기본: 가장 최근)")
    p.add_argument("--golden", default="golden/spec_en_골든셋.jsonl", help="골든 jsonl")
    p.add_argument("--target", default="", help="대상 문서 이름(기본: 첫 번째)")
    p.add_argument("--modes", default=",".join(MODES),
                   help=f"쉼표로 구분한 모드({'|'.join(MODES)})")
    p.add_argument("--detail", action="store_true",
                   help="첫 모드 대비 개선·악화 항목을 한 줄씩 출력")
    p.add_argument("--answer-min", type=float, default=0.8,
                   help="정답 fact 판정 하한(골든 토큰 겹침 비율, 기본 0.8)")
    p.add_argument("--show-answers", action="store_true",
                   help="각 골든 행에 대해 무엇을 정답 fact 로 봤는지 출력하고 끝낸다")
    return p


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    args = build_parser().parse_args(argv)
    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    if any(m not in MODES for m in modes):
        print(f"모드는 {MODES} 중에서 고르세요: {modes}")
        return 1

    cfg = AppConfig.load(args.config)
    snap = load_run(args.artifacts or cfg.fact.artifacts_dir, args.run)
    if snap is None or snap.reference is None:
        print(f"실행을 찾지 못했습니다: {args.run or '(최근)'}")
        return 1
    if snap.ref.is_snapshot:
        print("⚠ 스냅샷에는 대상 문서 facts.json 이 붙지 않습니다 — 현재 실행 폴더를 쓰세요.")
        return 1

    target_name = args.target or (snap.target_docs[0] if snap.target_docs else "")
    with open(args.golden, encoding="utf-8") as f:
        golden = [json.loads(line) for line in f if line.strip()]

    # 골든 → 기준 fact_id. comparison_result.json 이 row ↔ fact_id 를 잇는 유일한 다리다.
    tasks = []
    for g, c in _join(golden, snap.comparisons):
        if not g.get("target_text"):
            continue  # expected=missing — 정답 상대가 없으니 recall 을 잴 수 없다
        fid = (c.get("reference") or {}).get("fact_id")
        if fid:
            tasks.append((g, fid))
    if not tasks:
        print("골든과 판정을 조인하지 못했습니다 — 기준 문서가 다른 실행일 수 있습니다.")
        return 1

    top_k = cfg.fact.concept_recall_top_k
    print(f"실행: {snap.label}  |  대상: {target_name}  |  설정 top_k={top_k}")
    print(f"골든 {len(golden)}건 중 정답 상대가 있는 {len(tasks)}건으로 잰다"
          f" (나머지는 expected=missing).")

    # 정답 fact 는 evidence_text 로 정하므로 모드와 무관하다 — 한 번만 구한다.
    plain = _facts(snap, target_name)
    answers = {g["id"]: answer_ids(plain, g.get("target_text") or "", args.answer_min)
               for g, _ in tasks}
    unresolved = [g["entity_name"] for g, _ in tasks if not answers[g["id"]]]
    print(f"정답 fact 를 찾은 골든 {len(tasks) - len(unresolved)}/{len(tasks)}건"
          f" (하한 비율 {args.answer_min})\n")

    if args.show_answers:
        _print_answers(tasks, answers, {f.fact_id: f for f in plain})
        return 0
    if unresolved:
        print(f"⚠ 정답 fact 미확정 {len(unresolved)}건은 '미검출'로 셉니다: "
              f"{', '.join(unresolved[:5])}{' …' if len(unresolved) > 5 else ''}\n")

    results: dict[str, dict[str, int | None]] = {}
    for mode in modes:
        try:
            results[mode] = _measure(snap, target_name, tasks, cfg, mode, answers)
        except Exception as e:  # noqa: BLE001 — 백엔드마다 예외가 다르다
            print(f"[{mode}] 임베더 호출 실패: {type(e).__name__}: {e}")
            print("  numbers/full 은 새 문자열이라 캐시가 없습니다 — 임베더를 켜세요.")
            return 1
        _print_row(mode, results[mode], top_k, len(tasks))

    if args.detail and len(modes) > 1:
        _print_detail(results, modes, tasks, top_k)
    return 0


def _measure(snap, target_name: str, tasks, cfg, mode: str, answers) -> dict[str, int | None]:
    """모드마다 fact 를 **새로** 읽는다 — 보강은 search_text 를 제자리에서 바꾼다."""
    refs = {f.fact_id: f for f in _facts(snap, snap.reference.doc_name)}
    targets = _facts(snap, target_name)
    _augment_facts(list(refs.values()), mode)
    _augment_facts(targets, mode)

    matcher = FactMatcher(targets, embedder=_embedder(cfg),
                          top_k=len(targets), min_score=0.0, review_score=0.0)

    ranks: dict[str, int | None] = {}
    for g, fid in tasks:
        ref = refs.get(fid)
        if ref is None:
            ranks[g["id"]] = None
            continue
        rows: list[dict] = []
        matcher.search(ref, ranked_out=rows)
        ranks[g["id"]] = _rank_of_answer(rows, {a for a, _ in answers[g["id"]]})
    return ranks


def _print_answers(tasks, answers, by_id) -> None:
    """무엇을 정답으로 봤는지 — 이 표가 틀리면 recall 수치 전체가 거짓이다."""
    for g, _ in tasks:
        found = answers[g["id"]]
        print(f"■ {g['entity_name']}  [{g['expected']}]")
        print(f"    골든: {(g.get('target_text') or '')[:80]}")
        if not found:
            print("    → 정답 fact 미확정 (--answer-min 을 낮춰보세요)")
        for fid, ratio in found:
            f = by_id.get(fid)
            name = f.entity_name if f else fid
            print(f"    → {ratio:.2f}  {fid}  {name[:52]}")
        print()


def answer_ids(targets, target_text: str, min_ratio: float) -> list[tuple[str, float]]:
    """골든 원문을 담고 있는 대상 fact 들 — 겹침 **비율**이 하한 이상인 것 전부.

    하나로 좁히지 않는 이유는 F3 가 같은 내용을 **여러 fact 로** 낼 수 있어서다(실측:
    w_b010 'Charge Temperature Ranges' 와 w_b014 'Operating Protocol in different
    Temperatures' 가 같은 충전 프로토콜을 서로 다른 값으로 담았다). recall 은 "정답을
    담은 fact 가 후보에 들어왔는가"를 묻는 것이므로 그중 **가장 높은 순위**를 쓴다.

    ``evidence_text`` 로만 판정하므로 ``--augment``(``search_text`` 만 변경)와 무관하다 —
    그래야 세 모드의 비교가 공정하다.
    """
    want = _toks(target_text)
    if not want:
        return []
    scored = []
    for f in targets:
        got = _toks(f.evidence_text) | _toks(f.entity_name)
        ratio = len(want & got) / len(want)
        if ratio >= min_ratio:
            scored.append((f.fact_id, ratio))
    return sorted(scored, key=lambda x: -x[1])


def _rank_of_answer(rows, answers: set[str]) -> int | None:
    """정답 fact 중 **가장 앞선** 순위(1-base). 하나도 없으면 None."""
    for i, r in enumerate(rows, start=1):
        if r["fact_id"] in answers:
            return i
    return None


def _print_row(mode: str, ranks, top_k: int, total: int) -> None:
    found = [r for r in ranks.values() if r is not None]
    hit = sum(1 for r in found if r <= top_k)
    avg = sum(found) / len(found) if found else 0.0
    print(f"  {mode:<9} recall@{top_k}: {hit:>3}/{total}"
          f"   평균순위 {avg:>6.1f}   미검출 {total - len(found):>3}")


def _print_detail(results, modes, tasks, top_k: int) -> None:
    base, last = modes[0], modes[-1]
    names = {g["id"]: g["entity_name"] for g, _ in tasks}
    moved = []
    for gid in results[base]:
        a, b = results[base][gid], results[last].get(gid)
        if a != b:
            moved.append((gid, a, b))

    def key(x):  # 개선 폭이 큰 것부터
        a, b = (x[1] or 999), (x[2] or 999)
        return a - b

    better = [m for m in moved if (m[1] or 999) > (m[2] or 999)]
    worse = [m for m in moved if (m[1] or 999) < (m[2] or 999)]
    print(f"\n{base} → {last}  개선 {len(better)}건 · 악화 {len(worse)}건")
    print("-" * 72)
    for gid, a, b in sorted(moved, key=key, reverse=True):
        arrow = "▲" if (a or 999) > (b or 999) else "▼"
        crossed = ""
        if (a or 999) > top_k >= (b or 999):
            crossed = "  ← 후보 진입"
        elif (b or 999) > top_k >= (a or 999):
            crossed = "  ← 후보 탈락"
        print(f"  {arrow} {names.get(gid, gid)[:34]:<36}"
              f"{_fmt(a):>5} → {_fmt(b):<5}{crossed}")


def _fmt(r: int | None) -> str:
    return "없음" if r is None else str(r)


if __name__ == "__main__":
    raise SystemExit(main())

"""후보는 있었는데 **판정 내역(findings)에 안 들어온 후보**를 찾는 진단 CLI.

``why_missing.py`` 는 "후보가 0건이라 비교를 안 했다"를 설명한다. 이 스크립트는 그
반대편을 본다 — **후보는 여럿이었는데 LLM 응답에 일부만 실려, 나머지가 조용히 빠진
경우**다. 리포트의 '후보별 내역'은 LLM 이 준 것만 보여 주므로, 빠진 후보는 화면 어디에도
나타나지 않는다. 그래서 산출물끼리 대조해야 보인다.

세 가지를 한 번에 본다:

1. **findings 결손** — ``candidate_count`` vs ``len(findings)``. 개념 그래프가 이어 준
   후보 중 어느 id 가 내역에 없는지까지 이름으로 짚는다.
2. **블록 구조** — 대상 문서에서 여러 줄짜리 문단(``lines`` 2개 이상)을 훑는다. 한
   문단에 조건이 여러 개 적힌 원문이 여기서 드러난다. ⚠️ ``raw_text`` 는 양끝 공백을
   지우므로 **들여쓰기(가로 정렬)는 이 목록에 남지 않는다** — 원본에서 열을 맞춰 앞
   줄의 값을 생략한 연속행은 사람이 원문을 봐야 확인된다.
3. **줄 인용 누락** — ``facts_by_block`` 에서 ``cited=false`` 인 줄. F3 가 통째로
   버린 줄이 여기 남는다.

사용법::

    python scripts/why_findings.py                        # 최근 실행 전체
    python scripts/why_findings.py 충전환경온도             # 항목 하나만
    python scripts/why_findings.py --run _runs/en_word    # 특정 실행
    python scripts/why_findings.py --lines                # 2·3번 상세까지
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contentcompare.fact.artifact_reader import load_run  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="후보는 있었는데 findings 에 안 들어온 후보를 찾는다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("entity", nargs="?", default="", help="기준 항목명(부분 일치)")
    p.add_argument("--artifacts", default="artifacts", help="산출물 루트(기본: artifacts)")
    p.add_argument("--run", default="", help="실행 라벨(기본: 가장 최근)")
    p.add_argument("--lines", action="store_true",
                   help="여러 줄 문단과 인용 누락 줄까지 상세히 출력")
    return p


def main(argv: list[str] | None = None) -> int:
    # Windows 기본 콘솔은 cp949 라 '—' 하나에 전체가 죽는다(진단 도구가 진단을 못 하는
    # 최악의 실패). scripts/langfuse_test.py 와 같은 처리다.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    args = build_parser().parse_args(argv)

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

    _report_findings(snap, args.entity)
    if args.lines:
        print()
        _report_multiline_blocks(snap)
        print()
        _report_uncited_lines(snap)
    else:
        print("\n(--lines 를 붙이면 여러 줄 문단과 인용 누락 줄까지 봅니다.)")
    return 0


# --------------------------------------------------------------------------- #
# 1) findings 결손
# --------------------------------------------------------------------------- #
def _report_findings(snap, entity: str) -> None:
    print("=" * 72)
    print("1) findings 결손 — 후보 대비 내역이 몇 건인가")
    print("=" * 72)

    stats = snap.compare_stats or {}
    dropped = stats.get("dropped_findings")
    if dropped:
        print(f"⚠️ dropped_findings: {dropped} — LLM 이 후보 밖 id 를 지목해 버려진 내역이 "
              f"있습니다(할루시네이션). 아래 결손과 원인이 다릅니다.")
    print(f"multi_candidate_comparisons: {stats.get('multi_candidate_comparisons')} "
          f"| quote_unverified: {stats.get('quote_unverified')}\n")

    rows = [c for c in snap.comparisons
            if not entity or entity in str(c.get("entity_name") or "")]
    if not rows:
        print(f"'{entity}' 에 해당하는 비교 항목이 없습니다.")
        return

    holes = 0
    for c in rows:
        n_cand = int(c.get("candidate_count") or 0)
        findings = c.get("findings") or []
        if n_cand < 2:
            continue  # 후보 1건 이하는 findings 절 자체가 없다(리포트도 안 붙인다)
        if len(findings) == n_cand:
            continue

        holes += 1
        name = c.get("entity_name")
        print(f"❌ {name}  →  {c.get('target_doc')}")
        print(f"   후보 {n_cand}건 · 내역 {len(findings)}건 · "
              f"판정 {c.get('result')} ({c.get('decided_by')})")

        missing = _missing_candidate_ids(snap, c, findings)
        if missing:
            facts = snap.facts_of(str(c.get("target_doc") or ""))
            for fid in missing:
                f = facts.get(fid) or {}
                print(f"   · 내역에 없는 후보: {fid}  {f.get('entity_name') or '(이름 미상)'}")
                ev = " ".join(str(f.get("evidence_text") or "").split())
                if ev:
                    print(f"       근거: {ev[:100]}")
        else:
            print("   · 어느 후보가 빠졌는지 특정하지 못했습니다"
                  " (concept_graph.json 이 없거나 후보 구성이 달라졌습니다).")
        print()

    if not holes:
        print("✅ 후보 2건 이상인 항목에서 내역 결손이 없습니다"
              " (후보 수 = 내역 수).")


def _missing_candidate_ids(snap, comparison: dict, findings: list) -> list[str]:
    """개념 그래프가 이어 준 후보 중 findings 에 없는 id."""
    ref = comparison.get("reference") or {}
    ref_id = str(ref.get("fact_id") or "")
    target_doc = str(comparison.get("target_doc") or "")
    if not ref_id or not target_doc:
        return []
    partners = snap.index.partners(snap.reference_doc, ref_id, target_doc)
    seen = {str(f.get("fact_id") or "") for f in findings}
    return [p for p in partners if p not in seen]


# --------------------------------------------------------------------------- #
# 2) 여러 줄 문단
# --------------------------------------------------------------------------- #
def _report_multiline_blocks(snap) -> None:
    print("=" * 72)
    print("2) 여러 줄 문단 — 한 블록에 조건이 여럿 적힌 자리")
    print("=" * 72)
    print("⚠️ raw_text 는 양끝 공백을 지웁니다. 원본에서 열을 맞춰 앞 줄의 값을 생략한")
    print("   연속행(예: 범위를 안 쓰고 전류만 적은 줄)은 아래에서 구분되지 않습니다 —")
    print("   줄 목록을 보고 '주인이 없어 보이는 줄'을 사람이 찾아야 합니다.\n")

    found = False
    for doc_name in snap.target_docs:
        doc = snap.doc(doc_name)
        raw = doc.load("physical_raw") if doc else None
        if not raw:
            print(f"[{doc_name}] physical_raw.json 이 없습니다.")
            continue
        for b in raw.get("blocks") or []:
            lines = b.get("lines") or []
            if len(lines) < 2:
                continue
            found = True
            print(f"[{doc_name}] {b.get('block_id')} ({b.get('type')}) · {len(lines)}줄")
            for ln in lines:
                print(f"   {ln.get('line_id')}  {ln.get('raw_text')}")
            print()
    if not found:
        print("여러 줄로 쪼개진 문단이 없습니다"
              " (표만 있거나, 75bfd8e 이전 산출물이라 lines 필드가 없습니다).")


# --------------------------------------------------------------------------- #
# 3) 인용 누락 줄
# --------------------------------------------------------------------------- #
def _report_uncited_lines(snap) -> None:
    print("=" * 72)
    print("3) 인용 누락 줄 — F3 가 근거로 쓰지 않은 줄")
    print("=" * 72)

    for doc_name in snap.target_docs:
        doc = snap.doc(doc_name)
        data = doc.load("facts_by_block") if doc else None
        if not data:
            continue
        summary = data.get("summary") or {}
        if "units_in" not in summary:
            print(f"[{doc_name}] 줄 단위 계측이 없습니다(표·Excel 이거나 옛 산출물).")
            continue
        print(f"[{doc_name}] 누락 {summary.get('units_uncited')} / "
              f"{summary.get('units_in')} 줄")
        for b in data.get("blocks") or []:
            for ln in b.get("lines") or []:
                if not ln.get("cited"):
                    print(f"   {ln.get('line_id')}  {ln.get('preview')}")
        print()


if __name__ == "__main__":
    raise SystemExit(main())

"""임베딩에 **실제로 들어가는 문자열**을 모드별로 나란히 보여준다.

순위가 왜 그렇게 나오는지는 점수를 보기 전에 **무엇을 임베딩했는지**를 봐야 안다.
``search_text`` 는 원문이 아니라 ``entity_name``·``entity_path``·속성의 **값과 단위**로
다시 조립한 문자열이라(``fact_extractor._build_search_text``), 속성 값이 ``null`` 이면
그 fact 의 검색 문자열에는 숫자가 하나도 없다 — 같은 표의 옆 항목은 값이 채워져 숫자가
들어가므로 순위 경쟁이 조용히 불공정해진다.

세 모드를 한 화면에 놓으면 그 구멍이 눈으로 보인다:

- ``off``      현행. 재조립된 문자열
- ``numbers``  수치가 하나도 없는 fact 에만 근거의 숫자를 덧붙인 것
- ``full``     근거 원문 전문을 덧붙인 것

속성 **이름**(``charge_temp_range_1`` 등)은 어느 모드에서도 들어가지 않는다 —
함께 출력해 "버려지는 것"을 명시한다.

사용법::

    python scripts/show_search_text.py --grep "charge temperature"
    python scripts/show_search_text.py --grep 충전온도 --doc ref
    python scripts/show_search_text.py --doc target --limit 20
"""

from __future__ import annotations

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)

from contentcompare.config import AppConfig  # noqa: E402
from contentcompare.fact.artifact_reader import load_run  # noqa: E402
from contentcompare.fact.validator import _NUM_RE  # noqa: E402
from rank_candidates import _augment_facts, _facts  # noqa: E402

MODES = ("off", "numbers", "full")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="모드별 임베딩 입력 문자열을 출력한다(임베더도 chat 도 안 부른다).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--config", default="config/config.yaml", help="설정 파일")
    p.add_argument("--artifacts", default="", help="산출물 루트(기본: 설정의 artifacts_dir)")
    p.add_argument("--run", default="", help="실행 라벨(기본: 가장 최근)")
    p.add_argument("--target", default="", help="대상 문서 이름(기본: 첫 번째)")
    p.add_argument("--doc", choices=("both", "ref", "target"), default="both",
                   help="어느 문서의 fact 를 볼지(기본: 양쪽)")
    p.add_argument("--grep", default="", help="entity_name 부분 일치 필터")
    p.add_argument("--limit", type=int, default=10, help="출력할 fact 수(기본 10)")
    return p


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    args = build_parser().parse_args(argv)
    cfg = AppConfig.load(args.config)
    snap = load_run(args.artifacts or cfg.fact.artifacts_dir, args.run)
    if snap is None or snap.reference is None:
        print(f"실행을 찾지 못했습니다: {args.run or '(최근)'}")
        return 1
    if snap.ref.is_snapshot and args.doc != "ref":
        print("⚠ 스냅샷에는 대상 문서 facts.json 이 붙지 않습니다 — --doc ref 만 가능합니다.")
        return 1

    target_name = args.target or (snap.target_docs[0] if snap.target_docs else "")
    wanted = []
    if args.doc in ("both", "ref"):
        wanted.append((snap.reference.doc_name, "기준"))
    if args.doc in ("both", "target") and target_name:
        wanted.append((target_name, "대상"))

    print(f"실행: {snap.label}\n")
    shown = 0
    for doc_name, role in wanted:
        # 모드마다 새로 읽는다 — 보강은 search_text 를 제자리에서 바꾼다.
        variants = {}
        for mode in MODES:
            facts = _facts(snap, doc_name)
            _augment_facts(facts, mode)
            variants[mode] = {f.fact_id: f for f in facts}

        base = variants["off"]
        picked = [f for f in base.values()
                  if not args.grep or args.grep.lower() in (f.entity_name or "").lower()]
        for f in picked[:args.limit]:
            _show(f, variants, doc_name, role)
            shown += 1

    if shown == 0:
        print(f"조건에 맞는 fact 가 없습니다(--grep {args.grep!r}).")
    return 0


def _show(fact, variants, doc_name: str, role: str) -> None:
    print("=" * 96)
    print(f"[{role}] {doc_name}  ·  {fact.fact_id}  ·  {fact.entity_name}")
    print("=" * 96)

    attrs = fact.attributes or {}
    values = [a.value for a in attrs.values()]
    has_num = any(_NUM_RE.search(str(v)) for v in values)
    print(f"  속성 이름 : {list(attrs) or '(없음)'}   ← 어느 모드에서도 임베딩되지 않음")
    print(f"  속성 값   : {values or '(없음)'}"
          f"{'' if has_num else '   ⚠ 수치 없음 — off 에서는 숫자가 안 들어간다'}")
    print(f"  근거 원문 : {fact.evidence_text or '(없음)'}")
    print()
    for mode in MODES:
        text = variants[mode][fact.fact_id].search_text
        tag = "(동일)" if mode != "off" and text == variants["off"][fact.fact_id].search_text else ""
        print(f"  ▸ {mode:<8}{tag}")
        print(f"      {text}")
    print()


if __name__ == "__main__":
    raise SystemExit(main())

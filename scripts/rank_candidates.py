"""기준 항목 하나에 대해 대상 fact **전체 순위**를 다시 계산한다 — chat LLM 을 안 부른다.

``candidate_pairs.json`` 은 상위 ``top_k + RANKED_OUT_EXTRA``(=5) 건만 남긴다. 그래서
``why_findings --trace-fact`` 가 "랭킹 기록에 아예 없습니다" 라고 할 때 **16위인지
60위인지 알 수 없다** — 근소하게 밀린 것과 의미적으로 완전히 빗나간 것은 조치가
다른데(전자는 ``concept_recall_top_k``, 후자는 이름·``search_text`` 문제) 그 구분이
산출물만으로는 불가능하다.

이 스크립트는 산출물의 ``facts.json`` 과 설정된 임베더만으로 순위를 재현한다.
recall 은 임베딩만 쓰므로 F7/F5 의 chat 예산과 무관하고, ``CachedEmbedder`` 가
적중하면 비용이 0 이다. 랭킹 계산은 :class:`FactMatcher` 를 그대로 쓴다 — 여기서
따로 구현하면 두 곳이 갈려 진단이 거짓말을 한다.

``kept``/``cut_by`` 는 **설정된 실제 값**(``concept_recall_top_k``/``concept_recall_min``)
으로 다시 매긴다. 전체를 보려고 top_k 를 늘려 잡았으므로, 그대로 두면 전부 '채택'으로
보여 실행이 실제로 무엇을 버렸는지 알 수 없다.

사용법::

    python scripts/rank_candidates.py 충전온도범위
    python scripts/rank_candidates.py 충전온도범위 --limit 30
    python scripts/rank_candidates.py 충전온도 --grep charge
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contentcompare.config import AppConfig  # noqa: E402
from contentcompare.fact.artifact_reader import load_run  # noqa: E402
from contentcompare.fact.fact_matcher import (  # noqa: E402
    EXACT,
    FactMatcher,
    fact_text,
)
from contentcompare.fact.fact_models import Fact  # noqa: E402
from contentcompare.fact.validator import _NUM_RE  # noqa: E402  (수치 판정을 검증기와 일치시킨다)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="기준 항목에 대한 대상 fact 전체 순위를 재계산한다(chat LLM 미사용).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("entity", help="기준 항목명(부분 일치)")
    p.add_argument("--config", default="config/config.yaml", help="설정 파일")
    p.add_argument("--artifacts", default="", help="산출물 루트(기본: 설정의 artifacts_dir)")
    p.add_argument("--run", default="", help="실행 라벨(기본: 가장 최근)")
    p.add_argument("--target", default="", help="대상 문서 이름(기본: 첫 번째)")
    p.add_argument("--limit", type=int, default=30, help="출력할 상위 건수(기본 30)")
    p.add_argument("--grep", default="",
                   help="이 문자열을 포함하는 대상 fact 를 ◀ 로 표시하고 순위를 따로 알린다")
    p.add_argument("--augment", choices=("off", "numbers", "full"), default="off",
                   help="search_text 보강 실험(산출물은 안 바뀜). "
                        "numbers=수치 없는 fact 에 근거의 숫자만 / full=전 fact 에 근거 전문")
    return p


def main(argv: list[str] | None = None) -> int:
    # Windows 기본 콘솔은 cp949 라 '—' 하나에 전체가 죽는다. why_findings.py 와 같은 처리다.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    args = build_parser().parse_args(argv)
    cfg = AppConfig.load(args.config)
    root = args.artifacts or cfg.fact.artifacts_dir

    snap = load_run(root, args.run)
    if snap is None or snap.reference is None:
        print(f"실행을 찾지 못했습니다: {args.run or '(최근)'} (루트: {root})")
        return 1
    if snap.ref.is_snapshot:
        print("⚠ 스냅샷에는 대상 문서 facts.json 이 붙지 않습니다 — 현재 실행 폴더를 쓰세요.")
        return 1

    target_name = args.target or (snap.target_docs[0] if snap.target_docs else "")
    refs = _facts(snap, snap.reference.doc_name)
    targets = _facts(snap, target_name)
    if not targets:
        print(f"대상 fact 가 없습니다: {target_name}")
        return 1

    hits = [f for f in refs if args.entity in f.entity_name]
    if not hits:
        print(f"기준 항목을 찾지 못했습니다: {args.entity}")
        return 1

    n_ref = _augment_facts(refs, args.augment)
    n_tgt = _augment_facts(targets, args.augment)

    top_k, min_score = cfg.fact.concept_recall_top_k, cfg.fact.concept_recall_min
    print(f"실행: {snap.label}  |  기준: {snap.reference.doc_name}  |  대상: {target_name}")
    print(f"대상 fact {len(targets)}건 · 설정 top_k={top_k} min_score={min_score}")
    print("임베딩만 사용합니다(chat 호출 없음). 캐시가 비면 임베더를 한 번 호출합니다.")
    if args.augment != "off":
        print(f"⚗ search_text 보강 실험 --augment {args.augment} "
              f"(기준 {n_ref}건 · 대상 {n_tgt}건 변경, 산출물은 그대로)")
    print()

    try:
        matcher = FactMatcher(
            targets, embedder=_embedder(cfg),
            top_k=len(targets), min_score=0.0, review_score=0.0,
        )
    except Exception as e:  # noqa: BLE001 — 백엔드 종류마다 예외가 달라 넓게 잡는다
        # --augment 는 **새 문자열**이라 캐시가 없다. 실행 때와 달리 임베더가 꺼져
        # 있으면 여기서만 죽는데, 트레이스백만 보면 스크립트 버그로 오해한다.
        print(f"임베더 호출에 실패했습니다: {type(e).__name__}: {e}")
        print("  --augment off 는 캐시로 되지만 numbers/full 은 새 문자열이라 "
              "임베더가 살아 있어야 합니다.")
        print(f"  설정: backend={cfg.llm.embed_backend or cfg.llm.backend} "
              f"model={cfg.llm.embed_model}")
        return 1
    for ref in hits:
        _report(matcher, ref, top_k, min_score, args)
    return 0


def _facts(snap, doc_name: str) -> list[Fact]:
    return [Fact.from_dict(d) for d in snap.facts_of(doc_name).values()]


def _augment_facts(facts: list[Fact], mode: str) -> int:
    """``search_text`` 를 근거 원문으로 보강한다 — **실험용, 산출물은 그대로다.**

    ``_build_search_text`` 는 속성의 **값**만 넣는다. 그래서 F3 가 속성을 만들고도
    값을 ``null`` 로 두면 그 fact 의 검색 문자열에는 숫자가 하나도 없다 — 같은 표의
    옆 항목은 값이 채워져 숫자가 들어가므로 순위 경쟁이 불공정해진다.

    ``numbers`` 는 그 구멍만 메운다(수치가 하나도 없는 fact 에 근거의 숫자를 덧붙임).
    ``full`` 은 전 fact 에 근거 전문을 붙이는 상한 대조군이다 — 길어진 문장이 임베딩을
    희석해 **더 나빠질 수도** 있어서, 둘을 같이 재야 어느 쪽이 이득인지 알 수 있다.
    """
    if mode == "off":
        return 0
    changed = 0
    for f in facts:
        if not f.evidence_text:
            continue
        if mode == "full":
            extra = f.evidence_text
        else:
            has_num = any(_NUM_RE.search(str(a.value))
                          for a in (f.attributes or {}).values())
            if has_num:
                continue
            extra = " ".join(_NUM_RE.findall(f.evidence_text))
        if not extra.strip():
            continue
        f.search_text = f"{f.search_text} {extra}".strip()
        changed += 1
    return changed


def _embedder(cfg: AppConfig):
    """파이프라인과 **같은** 임베더 — 캐시까지 동일해야 점수가 재현된다."""
    from contentcompare.llm.factory import build_clients
    from contentcompare.similarity.cache import CachedEmbedder

    _chat, embed = build_clients(cfg)
    return CachedEmbedder(embed, cfg.similarity.cache_dir, cfg.llm.embed_model)


def _report(matcher: FactMatcher, ref: Fact, top_k: int, min_score: float, args) -> None:
    rows: list[dict] = []
    matcher.search(ref, ranked_out=rows)

    print("=" * 96)
    print(f"{ref.entity_name}  ({ref.fact_id})")
    print(f"  검색 문자열: {fact_text(ref)}")
    print("=" * 96)

    if rows and rows[0].get("method") == EXACT:
        print("이름 완전일치로 **조기 종료** — 나머지 후보는 계산되지 않았습니다.")
        print(f"  → {rows[0]['entity_name']} ({rows[0]['fact_id']})")
        return

    print(f"{'순위':>4} {'점수':>8}  {'실행판정':<12}{'대상 fact'}")
    print("-" * 96)
    marked = []
    for i, r in enumerate(rows[:args.limit], start=1):
        kept = i <= top_k and r["score"] >= min_score
        cut = "" if kept else ("min_score" if r["score"] < min_score else "top_k")
        flag = "✅ 채택" if kept else f"❌ {cut}"
        mark = ""
        if args.grep and args.grep.lower() in (r["entity_name"] or "").lower():
            mark = "  ◀"
            marked.append((i, r))
        print(f"{i:>4} {r['score']:>8.4f}  {flag:<12}{r['entity_name'][:52]}{mark}")

    if args.grep:
        print()
        if marked:
            for rank, r in marked:
                print(f"◀ '{args.grep}' 매칭: {rank}위 · {r['score']:.4f} · {r['fact_id']}")
        else:
            hit = next((i for i, r in enumerate(rows, 1)
                        if args.grep.lower() in (r["entity_name"] or "").lower()), None)
            if hit:
                print(f"◀ '{args.grep}' 은 {hit}위 — --limit {hit} 이상으로 봐야 보입니다.")
            else:
                print(f"◀ '{args.grep}' 을 포함한 대상 fact 가 없습니다(F3 추출 단계 문제).")


if __name__ == "__main__":
    raise SystemExit(main())

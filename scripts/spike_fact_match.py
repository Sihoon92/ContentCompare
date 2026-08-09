"""fact ↔ fact 매칭 spike (Phase F3.5, 일회성 · 기본 무 LLM).

신규 fact 방식의 **최대 가설**은 "기준 fact 에 대응하는 상대 문서의 fact 를 찾을 수
있다"이다. 못 찾으면 F5 는 전부 ``missing`` 오판을 낸다. 이 스크립트는 F5 를 만들기
전에 그 가설을 **최저 비용으로 실측**한다 — 판정 LLM 없이 검색만 돌려본다.

전략(단계적 폴백):
    1) 정규화한 ``entity_name`` 완전일치            — 공짜, 가장 정확
    2) 실패분에 BM25(어휘 매칭) top-k              — similarity/ 를 읽기 전용 재사용
    3) (--embed) 실패분에 임베딩 코사인 top-k      — 네트워크 필요, 기본 off

골든셋을 주면 **정오까지** 채점한다: 대응 fact 를 실제로 찾았는지(recall@1/@3),
그리고 대상에 없는 항목(``missing``)에 엉뚱한 후보를 붙이지 않는지(오매칭).

사용:
    python scripts/spike_fact_match.py \\
        --ref artifacts/자표준문서_xlsx/facts.json \\
        --target artifacts/자표준_발표_pptx/facts.json \\
        --golden golden/자표준_골든셋.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contentcompare.similarity.bm25 import BM25  # noqa: E402
from contentcompare.similarity.tokenize import tokenize  # noqa: E402

_NON_WORD = re.compile(r"[^0-9a-z가-힣]+")


def norm_name(name: str) -> str:
    """entity_name 비교용 정규화 — 전각/공백/기호 차이를 없앤다.

    실측에서 대상 문서 LLM 은 같은 항목을 '정격충전전압' 이 아니라 '정격 충전 전압'
    으로 뽑는 경향이 있었다. 그 정도 차이는 완전일치로 흡수하는 것이 맞다.
    """
    return _NON_WORD.sub("", unicodedata.normalize("NFKC", name or "").lower())


def load_facts(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f).get("facts") or []


def load_golden(path: str, target_doc: str) -> list[dict]:
    """대상 문서에 해당하는 골든 레코드만 로드."""
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rec = json.loads(line)
                if rec.get("target_doc") == target_doc:
                    rows.append(rec)
    return rows


def fact_text(fact: dict) -> str:
    """검색 대상 문자열 — search_text 가 비면 근거 원문으로 폴백."""
    return fact.get("search_text") or fact.get("evidence_text") or fact.get("entity_name", "")


def overlap(a: str, b: str) -> float:
    """토큰 자카드 유사도(0~1) — 골든의 target_text 로 정답 fact 를 찾을 때 쓴다."""
    ta, tb = set(tokenize(a)), set(tokenize(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


class Matcher:
    """entity_name 완전일치 → (실패분) BM25 / 임베딩 / RRF 융합.

    폴백을 하나만 고르지 않고 **전략별 순위를 모두** 내놓는다. F5 가 어떤 검색을
    써야 하는지는 추측이 아니라 이 비교표로 정해야 하기 때문이다.
    """

    def __init__(self, targets: list[dict], *, top_k: int = 3, embedder=None) -> None:
        self.targets = targets
        self.top_k = top_k
        self.by_name: dict[str, int] = {}
        for i, f in enumerate(targets):
            self.by_name.setdefault(norm_name(f.get("entity_name", "")), i)
        self.bm25 = BM25()
        self.bm25.index([tokenize(fact_text(f)) for f in targets])
        self.vectors = None
        if embedder is not None and targets:
            self.embedder = embedder
            # 대상=본문이므로 passage 접두어(e5 계열 교차언어 검색 규약)를 쓴다.
            self.vectors = _normalize_all(
                embedder.embed([fact_text(f) for f in targets], kind="passage")
            )

    def strategies(self) -> list[str]:
        return ["bm25", "embed", "rrf"] if self.vectors is not None else ["bm25"]

    def match(self, ref: dict) -> tuple[bool, dict[str, list[tuple[int, float]]]]:
        """(완전일치 여부, {전략: [(대상 인덱스, 점수), ...]})."""
        key = norm_name(ref.get("entity_name", ""))
        if key and key in self.by_name:
            hit = [(self.by_name[key], 1.0)]
            return True, {s: hit for s in self.strategies()}

        out: dict[str, list[tuple[int, float]]] = {}
        bm = _rank(self.bm25.scores(tokenize(fact_text(ref))), self.top_k)
        out["bm25"] = bm
        if self.vectors is not None:
            q = _normalize(self.embedder.embed([fact_text(ref)], kind="query")[0])
            sims = [sum(a * b for a, b in zip(q, v)) for v in self.vectors]
            out["embed"] = _rank(sims, self.top_k)
            out["rrf"] = _fuse(bm, out["embed"], self.top_k)
        return False, out


def _rank(scores: list[float], top_k: int) -> list[tuple[int, float]]:
    ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)[:top_k]
    return [(i, s) for i, s in ranked if s > 0]


def _fuse(a: list[tuple[int, float]], b: list[tuple[int, float]], top_k: int, k: int = 60):
    """Reciprocal Rank Fusion — 점수 스케일이 다른 두 순위를 순위만으로 합친다."""
    acc: dict[int, float] = {}
    for ranked in (a, b):
        for rank, (idx, _) in enumerate(ranked, start=1):
            acc[idx] = acc.get(idx, 0.0) + 1.0 / (k + rank)
    return sorted(acc.items(), key=lambda x: x[1], reverse=True)[:top_k]


def _normalize(vec: list[float]) -> list[float]:
    norm = sum(v * v for v in vec) ** 0.5
    return [v / norm for v in vec] if norm else vec


def _normalize_all(vecs: list[list[float]]) -> list[list[float]]:
    return [_normalize(v) for v in vecs]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="fact 매칭률 실측(F3.5 spike)")
    p.add_argument("--ref", required=True, help="기준 문서의 facts.json")
    p.add_argument("--target", required=True, help="대상 문서의 facts.json")
    p.add_argument("--golden", help="골든셋 jsonl(있으면 정오까지 채점)")
    p.add_argument("--target-doc", help="골든셋에서 고를 target_doc 이름(기본: 추론)")
    p.add_argument("--embed", action="store_true", help="BM25 실패분에 임베딩 폴백(네트워크 필요)")
    p.add_argument("--config", default="config/config.yaml")
    p.add_argument("--out", default="out/spike_match.md")
    p.add_argument("--top-k", type=int, default=3)
    args = p.parse_args(argv)

    refs, targets = load_facts(args.ref), load_facts(args.target)
    target_doc = args.target_doc or _guess_target_doc(args.target)

    embedder = None
    if args.embed:
        from contentcompare.config import AppConfig
        from contentcompare.llm.factory import build_clients

        _, embedder = build_clients(AppConfig.load(args.config))

    matcher = Matcher(targets, top_k=args.top_k, embedder=embedder)
    golden = load_golden(args.golden, target_doc) if args.golden else []
    truth = _resolve_truth(golden, targets)  # 골든 항목 → 정답 대상 fact 인덱스

    strategies = matcher.strategies()
    score = {s: {"hit1": 0, "hit3": 0, "fp": 0} for s in strategies}
    # 임계값 캘리브레이션용: 정답 top1 점수 vs 오매칭 top1 점수의 분포.
    # 이 두 분포가 갈리는 지점이 F5 의 match_min_score 다(missing 판정의 근거).
    calib = {s: {"hit": [], "fp": []} for s in strategies}
    rows, exact_n, judged, missing_total = [], 0, 0, 0
    for ref in refs:
        exact, ranked_by = matcher.match(ref)
        exact_n += int(exact)
        gold = truth.get(norm_name(ref.get("entity_name", "")))
        if gold is not None and gold["expected"] == "missing":
            missing_total += 1
        elif gold is not None and gold["index"] is not None:
            judged += 1

        verdicts = {}
        for s in strategies:
            ranked = ranked_by[s]
            idxs = [i for i, _ in ranked]
            if gold is None:
                verdicts[s] = ""
            elif gold["expected"] == "missing":
                # 대상에 없는 항목인데 후보를 붙였으면 오매칭(F5 의 false match 위험).
                if idxs:
                    score[s]["fp"] += 1
                    if not exact:
                        calib[s]["fp"].append(ranked[0][1])
                    verdicts[s] = f"⚠ 오매칭({ranked[0][1]:.3f})"
                else:
                    verdicts[s] = "✅ 후보없음"
            elif gold["index"] is None:
                verdicts[s] = ""
            elif idxs and idxs[0] == gold["index"]:
                score[s]["hit1"] += 1
                score[s]["hit3"] += 1
                if not exact:
                    calib[s]["hit"].append(ranked[0][1])
                verdicts[s] = f"✅ top1({ranked[0][1]:.3f})"
            elif gold["index"] in idxs:
                score[s]["hit3"] += 1
                verdicts[s] = f"△ top{idxs.index(gold['index']) + 1}"
            else:
                verdicts[s] = f"❌ 미검출({targets[gold['index']]['entity_name'][:20]})"

        primary = "rrf" if "rrf" in strategies else "bm25"
        rows.append({
            "ref": ref.get("entity_name", ""),
            "how": "exact" if exact else primary,
            "top": [targets[i]["entity_name"] for i, _ in ranked_by[primary]],
            "score": round(ranked_by[primary][0][1], 3) if ranked_by[primary] else 0.0,
            "verdicts": verdicts,
        })

    _report(args.out, args.ref, args.target, rows, strategies, exact_n, len(refs))
    print(f"기준 fact {len(refs)}개 / 대상 fact {len(targets)}개")
    print(f"  entity_name 완전일치: {exact_n}/{len(refs)} ({exact_n / max(1, len(refs)):.0%})"
          f" — 나머지는 폴백 검색으로 처리")
    for s in strategies:
        line = f"  [{s:5}]"
        if judged:
            h1, h3 = score[s]["hit1"], score[s]["hit3"]
            line += (f" recall@1 {h1}/{judged} ({h1 / judged:.0%})"
                     f" · recall@{args.top_k} {h3}/{judged} ({h3 / judged:.0%})")
        if missing_total:
            line += f" · 대상없음 {missing_total}건 중 오매칭 {score[s]['fp']}건"
        print(line)
        _print_calibration(s, calib[s])
    print(f"  상세: {args.out}")
    return 0


def _print_calibration(strategy: str, data: dict) -> None:
    """정답 top1 점수와 오매칭 top1 점수의 분포를 보여준다(임계값 결정용).

    exact 확정분은 점수가 1.0 고정이라 분포를 왜곡하므로 애초에 수집하지 않는다
    (BM25 는 점수가 1.0 을 넘으므로 점수로 걸러낼 수 없다).
    """
    hits = sorted(data["hit"])
    fps = sorted(data["fp"])
    if not hits and not fps:
        return
    parts = []
    if hits:
        parts.append(f"정답 {len(hits)}건 min={hits[0]:.3f} max={hits[-1]:.3f}")
    if fps:
        parts.append(f"오매칭 {len(fps)}건 min={fps[0]:.3f} max={fps[-1]:.3f}")
    line = f"          점수분포: {' · '.join(parts)}"
    if hits and fps:
        if hits[0] > fps[-1]:
            line += f"  → 분리 가능, 임계값 후보 {(hits[0] + fps[-1]) / 2:.3f}"
        else:
            line += "  → 겹침(단일 임계값으로 분리 불가)"
    print(line)


def _guess_target_doc(path: str) -> str:
    """artifacts/<문서명_확장자>/facts.json → '<문서명>.<확장자>' 로 되돌린다."""
    slug = os.path.basename(os.path.dirname(os.path.abspath(path)))
    head, _, ext = slug.rpartition("_")
    return f"{head}.{ext}" if head else slug


def _resolve_truth(golden: list[dict], targets: list[dict]) -> dict:
    """골든 레코드의 ``target_text`` 로 '정답 대상 fact' 인덱스를 찾아 붙인다.

    대상 문서의 fact 는 LLM 이 만든 것이라 골든에 id 를 미리 박아둘 수 없다.
    대신 골든이 문서에 실제로 쓴 문구(``target_text``)와 근거 원문의 토큰 겹침이
    가장 큰 fact 를 정답으로 본다(겹침 0.3 미만이면 정답 없음으로 둔다).
    """
    out: dict[str, dict] = {}
    for g in golden:
        best_i, best_s = None, 0.0
        if g["expected"] != "missing":
            for i, t in enumerate(targets):
                s = max(
                    overlap(g["target_text"], t.get("evidence_text", "")),
                    overlap(g["target_text"], fact_text(t)),
                )
                if s > best_s:
                    best_i, best_s = i, s
            if best_s < 0.3:
                best_i = None
        out[norm_name(g["entity_name"])] = {"expected": g["expected"], "index": best_i}
    return out


def _report(path: str, ref_path: str, target_path: str, rows, strategies, exact_n, total) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    lines = [
        "# fact 매칭 spike 결과 (F3.5)",
        "",
        f"- 기준: `{ref_path}`",
        f"- 대상: `{target_path}`",
        f"- entity_name 완전일치: {exact_n}/{total}",
        "",
        "| 기준 entity | 방식 | top 후보 | 점수 | " + " | ".join(strategies) + " |",
        "|---|---|---|---|" + "---|" * len(strategies),
    ]
    for r in rows:
        top = " / ".join(r["top"]) or "—"
        cells = " | ".join(r["verdicts"].get(s, "") for s in strategies)
        lines.append(f"| {r['ref']} | {r['how']} | {top} | {r['score']} | {cells} |")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    sys.exit(main())

"""개념 그래프 좌표 계산 — **순수 함수, 결정적**.

힘기반(force-directed) 레이아웃을 쓰지 않는다. 이유가 둘이다:

1. **재현성.** 디버깅 도구는 같은 입력에 같은 그림을 내야 한다. 무작위 초기값에서
   시작하는 레이아웃은 열 때마다 모양이 달라져 "아까 그 노드"를 다시 찾을 수 없다.
2. **규모.** fact 수백 개면 힘기반은 스파게티가 된다. 실측 기준 문서 하나가 20~40
   fact 이고 대상이 늘면 곱해진다.

대신 **이분 열 배치**(왼쪽=기준, 오른쪽=대상)를 쓴다. 두 문서를 대조하는 화면이라
축이 애초에 둘뿐이고, 행 순서만 정하면 교차가 결정된다.

좌표만 만들고 SVG 문자열은 만들지 않는다 — 브라우저 없이 pytest 로 검증하기 위해서다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

ROW_H = 24
"""행 높이(px). 노드 하나가 차지하는 세로 공간."""

PAD_Y = 18
COL_W = 250
"""열 하나의 가로 폭(px). 라벨이 들어갈 자리."""

GAP = 210
"""두 열 사이 간격(px) — 엣지가 지나는 공간."""

MAX_NODES = 400
"""이보다 많으면 '연결 있는 것만' 자동 필터한다(그리다 만 그림은 쓸모가 없다)."""


@dataclass
class LaidNode:
    key: str
    """``doc#fact_id`` — 화면에서 노드를 지목하는 식별자."""

    label: str
    x: float
    y: float
    side: str
    """``left`` | ``right``."""

    doc: str = ""
    fact_id: str = ""
    linked: bool = False
    """어떤 엣지에도 닿지 않으면 False(회색으로 그린다)."""


@dataclass
class LaidEdge:
    relation: str
    x1: float
    y1: float
    x2: float
    y2: float
    left_key: str = ""
    right_key: str = ""
    tone: str = "gray"
    """``ok`` | ``amber`` | ``gray`` | ``bad`` — 색 규약."""

    label: str = ""
    index: int = -1
    """``concept_graph.json`` 안 위치 — 사람이 찾아갈 좌표."""

    @property
    def path(self) -> str:
        """3차 베지어 — 가로로 부드럽게 건너간다."""
        mid = (self.x1 + self.x2) / 2
        return f"M{self.x1:.1f},{self.y1:.1f} C{mid:.1f},{self.y1:.1f} " \
               f"{mid:.1f},{self.y2:.1f} {self.x2:.1f},{self.y2:.1f}"


@dataclass
class Layout:
    nodes: list[LaidNode] = field(default_factory=list)
    edges: list[LaidEdge] = field(default_factory=list)
    width: int = 0
    height: int = 0
    notes: list[str] = field(default_factory=list)
    """사람에게 알릴 것(필터가 걸렸다 등)."""

    def node(self, key: str) -> Optional[LaidNode]:
        return next((n for n in self.nodes if n.key == key), None)


def edge_tone(relation: str, rejected_by: str = "") -> str:
    """관계 → 색 규약. **거부된 연결은 차단보다 강한 빨강**이다(사람이 볼 것)."""
    if rejected_by:
        return "bad"
    return {"same_as": "ok", "differs_by": "amber"}.get(relation, "gray")


def bipartite_layout(
    left: Sequence[dict],
    right: Sequence[dict],
    edges: Sequence[dict],
    *,
    row_h: int = ROW_H,
    max_nodes: int = MAX_NODES,
) -> Layout:
    """왼쪽/오른쪽 fact 목록과 엣지로 좌표를 만든다.

    항목 규약:

    - ``left``/``right``: ``{"key", "label", "doc", "fact_id"}``
    - ``edges``: ``{"left_key", "right_key", "relation", "rejected_by"?, "label"?, "index"?}``

    오른쪽 순서는 **연결된 왼쪽 행 번호의 중앙값**으로 정한다. 무작위가 아니라
    입력만으로 결정되므로 같은 실행은 언제나 같은 그림이 된다.
    """
    out = Layout()
    valid = [e for e in edges if e.get("left_key") and e.get("right_key")]
    linked_keys = {e["left_key"] for e in valid} | {e["right_key"] for e in valid}

    if len(left) + len(right) > max_nodes:
        before = len(left) + len(right)
        left = [n for n in left if n.get("key") in linked_keys]
        right = [n for n in right if n.get("key") in linked_keys]
        out.notes.append(
            f"노드가 {before}개라 **연결이 있는 {len(left) + len(right)}개만** 그렸습니다. "
            f"연결 없는 fact 는 '이 문서에만 있는 항목'이니 목록 뷰에서 확인하세요.")

    order_of = {n.get("key"): i for i, n in enumerate(left)}
    right_rank = _rank_right(right, valid, order_of)

    left_x = COL_W
    right_x = COL_W + GAP
    for i, node in enumerate(left):
        out.nodes.append(_node(node, left_x, PAD_Y + i * row_h, "left", linked_keys))
    for node in sorted(right, key=lambda n: right_rank.get(n.get("key"), 1e9)):
        i = right_rank.get(node.get("key"), 0)
        out.nodes.append(_node(node, right_x, PAD_Y + i * row_h, "right", linked_keys))

    by_key = {n.key: n for n in out.nodes}
    for e in valid:
        a, b = by_key.get(e["left_key"]), by_key.get(e["right_key"])
        if a is None or b is None:
            continue  # 필터로 빠진 노드를 가리키는 엣지는 그리지 않는다
        out.edges.append(LaidEdge(
            relation=str(e.get("relation") or ""),
            x1=a.x, y1=a.y, x2=b.x, y2=b.y,
            left_key=a.key, right_key=b.key,
            tone=edge_tone(str(e.get("relation") or ""), str(e.get("rejected_by") or "")),
            label=str(e.get("label") or ""),
            index=int(e.get("index", -1)),
        ))

    rows = max(len(left), len(right), 1)
    out.width = COL_W + GAP + COL_W
    out.height = PAD_Y * 2 + rows * row_h
    return out


def _node(raw: dict, x: float, y: float, side: str, linked: set) -> LaidNode:
    key = str(raw.get("key") or "")
    return LaidNode(
        key=key,
        label=str(raw.get("label") or key),
        x=x, y=y, side=side,
        doc=str(raw.get("doc") or ""),
        fact_id=str(raw.get("fact_id") or ""),
        linked=key in linked,
    )


def _rank_right(right: Sequence[dict], edges: Sequence[dict],
                order_of: dict) -> dict[str, int]:
    """오른쪽 노드의 행 번호 — 연결된 왼쪽 행의 중앙값 순.

    교차를 완전히 없애지는 못한다(그건 NP-난해다). 목표는 **결정적이면서
    사람이 따라갈 수 있을 만큼** 정돈되는 것이다.
    """
    partners: dict[str, list[int]] = {}
    for e in edges:
        idx = order_of.get(e.get("left_key"))
        if idx is not None:
            partners.setdefault(e["right_key"], []).append(idx)

    def sort_key(node: dict) -> tuple[float, str]:
        got = sorted(partners.get(node.get("key"), []))
        if not got:
            return (1e9, str(node.get("label") or ""))   # 연결 없는 것은 아래로
        return (got[len(got) // 2], str(node.get("label") or ""))

    return {n.get("key"): i for i, n in enumerate(sorted(right, key=sort_key))}


def focus_layout(
    center: dict,
    partners: Sequence[dict],
    edges: Sequence[dict],
    *,
    row_h: int = ROW_H * 2,
) -> Layout:
    """fact 하나와 그것에 닿는 엣지만 — **디버깅에 실제로 필요한 뷰**.

    이분 뷰는 전체를 보여 주지만, "이 항목이 왜 이렇게 됐나"를 볼 때 필요한 것은
    보통 후보 10개 이하다.
    """
    return bipartite_layout([center], partners, edges, row_h=row_h,
                            max_nodes=MAX_NODES)


def concept_rows(nodes: Iterable, *, min_docs: int = 2) -> list[dict]:
    """개념 노드 목록 — 문서 ``min_docs`` 개 이상에 걸친 것을 위로.

    fact 수만큼 노드가 생기므로(연결 없는 fact 는 혼자 한 노드) 표가 가장 정확한
    전체 뷰다. 여러 문서에 걸친 노드가 **실제로 이어진 개념**이다.
    """
    rows = []
    for node in nodes:
        docs = sorted({m.doc for m in node.members})
        rows.append({
            "concept_id": node.concept_id,
            "label": node.label,
            "docs": docs,
            "doc_count": len(docs),
            "members": [(m.doc, m.fact_id, m.entity_name) for m in node.members],
        })
    rows.sort(key=lambda r: (-r["doc_count"], r["concept_id"]))
    return [r for r in rows if r["doc_count"] >= min_docs] + \
           [r for r in rows if r["doc_count"] < min_docs]

"""개념 그래프 좌표 계산 테스트 — 순수 함수라 브라우저 없이 전부 검증된다.

이 모듈의 계약은 **결정성**이다. 디버깅 도구는 같은 입력에 같은 그림을 내야
"아까 그 노드"를 다시 찾을 수 있다.
"""

from __future__ import annotations

from contentcompare.ui.graph_layout import (
    MAX_NODES,
    bipartite_layout,
    concept_rows,
    edge_tone,
    focus_layout,
)


def _n(key, label=None):
    doc, fid = key.split("#", 1)
    return {"key": key, "label": label or fid, "doc": doc, "fact_id": fid}


def _e(left, right, relation="same_as", **kw):
    out = {"left_key": left, "right_key": right, "relation": relation}
    out.update(kw)
    return out


# --------------------------------------------------------------------------- #
# 결정성
# --------------------------------------------------------------------------- #
def test_layout_is_deterministic():
    left = [_n("A#f1"), _n("A#f2"), _n("A#f3")]
    right = [_n("B#g1"), _n("B#g2"), _n("B#g3")]
    edges = [_e("A#f1", "B#g3"), _e("A#f3", "B#g1")]

    a = bipartite_layout(left, right, edges)
    b = bipartite_layout(left, right, edges)
    assert [(n.key, n.x, n.y) for n in a.nodes] == [(n.key, n.x, n.y) for n in b.nodes]
    assert [e.path for e in a.edges] == [e.path for e in b.edges]


def test_left_column_keeps_input_order():
    left = [_n("A#f1"), _n("A#f2")]
    layout = bipartite_layout(left, [], [])
    ys = [n.y for n in layout.nodes if n.side == "left"]
    assert ys == sorted(ys)
    assert [n.key for n in layout.nodes] == ["A#f1", "A#f2"]


def test_right_order_follows_its_partners():
    """오른쪽은 연결된 왼쪽 행 순으로 — 선이 덜 꼬인다."""
    left = [_n("A#f1"), _n("A#f2"), _n("A#f3")]
    right = [_n("B#g1"), _n("B#g2"), _n("B#g3")]
    # f1→g3, f2→g2, f3→g1 이면 오른쪽은 g3, g2, g1 순이어야 한다.
    edges = [_e("A#f1", "B#g3"), _e("A#f2", "B#g2"), _e("A#f3", "B#g1")]
    layout = bipartite_layout(left, right, edges)
    order = [n.key for n in sorted(
        (n for n in layout.nodes if n.side == "right"), key=lambda n: n.y)]
    assert order == ["B#g3", "B#g2", "B#g1"]


def test_unlinked_right_nodes_go_last():
    left = [_n("A#f1")]
    right = [_n("B#g1"), _n("B#g2")]
    layout = bipartite_layout(left, right, [_e("A#f1", "B#g2")])
    by_key = {n.key: n for n in layout.nodes}
    assert by_key["B#g2"].y < by_key["B#g1"].y
    assert by_key["B#g1"].linked is False
    assert by_key["B#g2"].linked is True


# --------------------------------------------------------------------------- #
# 크기·규모
# --------------------------------------------------------------------------- #
def test_height_grows_with_the_taller_column():
    layout = bipartite_layout([_n("A#f1")], [_n(f"B#g{i}") for i in range(10)], [])
    assert layout.height > 10 * 20
    assert layout.width > 0


def test_large_graph_is_filtered_to_linked_nodes_with_a_note():
    left = [_n(f"A#f{i}") for i in range(MAX_NODES)]
    right = [_n(f"B#g{i}") for i in range(MAX_NODES)]
    layout = bipartite_layout(left, right, [_e("A#f0", "B#g0")])
    assert len(layout.nodes) == 2
    assert layout.notes and "연결이 있는" in layout.notes[0]


def test_edges_to_filtered_nodes_are_dropped_not_broken():
    left = [_n(f"A#f{i}") for i in range(MAX_NODES)]
    right = [_n("B#g0")]
    # f1 은 필터로 빠지는데 엣지가 남아 있으면 좌표가 없는 선이 그려진다.
    layout = bipartite_layout(left, right, [_e("A#f0", "B#g0")])
    assert all(layout.node(e.left_key) and layout.node(e.right_key)
               for e in layout.edges)


# --------------------------------------------------------------------------- #
# 색 규약
# --------------------------------------------------------------------------- #
def test_edge_tone_follows_the_visual_convention():
    assert edge_tone("same_as") == "ok"
    assert edge_tone("differs_by") == "amber"
    assert edge_tone("unknown") == "gray"
    # 거부된 연결은 차단보다 강하게 — 사람이 봐야 하는 것이다.
    assert edge_tone("unknown", "evidence") == "bad"
    assert edge_tone("same_as", "differs_by") == "bad"


def test_edge_path_is_a_horizontal_bezier():
    layout = bipartite_layout([_n("A#f1")], [_n("B#g1")], [_e("A#f1", "B#g1")])
    path = layout.edges[0].path
    assert path.startswith("M") and " C" in path


# --------------------------------------------------------------------------- #
# 경계
# --------------------------------------------------------------------------- #
def test_empty_input_does_not_raise():
    layout = bipartite_layout([], [], [])
    assert layout.nodes == [] and layout.edges == []
    assert layout.height > 0


def test_edge_without_endpoints_is_ignored():
    layout = bipartite_layout([_n("A#f1")], [_n("B#g1")],
                              [{"relation": "same_as"}, _e("A#f1", "B#g1")])
    assert len(layout.edges) == 1


def test_focus_layout_centres_one_fact():
    layout = focus_layout(_n("A#f1"), [_n("B#g1"), _n("B#g2")],
                          [_e("A#f1", "B#g1"), _e("A#f1", "B#g2", "differs_by")])
    assert len([n for n in layout.nodes if n.side == "left"]) == 1
    assert len(layout.edges) == 2


# --------------------------------------------------------------------------- #
# 개념 노드 목록
# --------------------------------------------------------------------------- #
class _M:
    def __init__(self, doc, fact_id, entity_name=""):
        self.doc, self.fact_id, self.entity_name = doc, fact_id, entity_name


class _Node:
    def __init__(self, cid, label, members):
        self.concept_id, self.label, self.members = cid, label, members


def test_concept_rows_put_multi_document_concepts_first():
    nodes = [
        _Node("c-0002", "혼자", [_M("A", "f2")]),
        _Node("c-0001", "이어짐", [_M("A", "f1"), _M("B", "g1")]),
    ]
    rows = concept_rows(nodes)
    assert rows[0]["concept_id"] == "c-0001"
    assert rows[0]["doc_count"] == 2
    assert rows[1]["doc_count"] == 1

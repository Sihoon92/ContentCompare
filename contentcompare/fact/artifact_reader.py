"""실행 산출물 읽기 — ``artifacts/`` 를 한 실행 단위(:class:`RunSnapshot`)로 묶는다.

:class:`~contentcompare.fact.artifacts.ArtifactStore` 는 **한 문서에 쓰는** 쪽이다.
이 모듈은 반대로 **실행 전체를 읽는다** — 기준 문서와 대상 문서들, 개념 그래프,
비교 결과, 진단 계측을 한 객체로 모아 뷰어·CLI·테스트가 같은 것을 보게 한다.

설계 원칙 하나: **어떤 파일이 없어도 예외를 던지지 않는다.** 진단 도구가 진단 대상의
불완전함 때문에 죽으면 쓸모가 없다. 없는 것은 :attr:`RunSnapshot.problems` 에 사람이
읽을 문장으로 쌓고, 무엇을 할 수 있는지는 :attr:`RunSnapshot.capabilities` 로 알린다.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from .artifacts import ArtifactStore
from .concept_models import ConceptEdge, ConceptGraph, ConceptNode

logger = logging.getLogger(__name__)

PAIR_SEP = "↔"
"""``concept_validation`` 이 두 fact 를 이어 붙일 때 쓰는 구분자(``↔``)."""

# 실행 폴더가 아닌 예약 디렉터리(스냅샷 보관소·LLM 추적).
RESERVED_DIRS = ("_runs", "_traces", "_timeline")

# 기준 문서 폴더에만 있는 산출물 — 실행을 찾는 표지로 쓴다.
RUN_MARKER = "comparison_result"


# --------------------------------------------------------------------------- #
# 실행 찾기
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RunRef:
    """실행 하나를 가리키는 참조(아직 읽지 않은 상태)."""

    label: str
    dir: Path
    is_snapshot: bool = False
    """``artifacts/_runs/<라벨>`` 에 손으로 보관해 둔 과거 실행인가."""


def list_runs(root: str | Path) -> list[RunRef]:
    """``root`` 아래의 실행 목록. 최근 수정 순으로 돌려준다.

    실행의 표지는 ``comparison_result.json`` 이다 — 이 파일은 **기준 문서 폴더에만**
    저장되므로, 그것을 가진 폴더를 찾으면 곧 기준 문서를 역추적한 셈이 된다.
    """
    root = Path(root)
    if not root.is_dir():
        return []
    found: list[RunRef] = []
    for d in _subdirs(root):
        if d.name in RESERVED_DIRS:
            continue
        if (d / f"{RUN_MARKER}.json").exists():
            found.append(RunRef(label=d.name, dir=d))
    snapshots = root / "_runs"
    if snapshots.is_dir():
        for d in _subdirs(snapshots):
            if (d / f"{RUN_MARKER}.json").exists():
                found.append(RunRef(label=f"_runs/{d.name}", dir=d, is_snapshot=True))
    found.sort(key=lambda r: _mtime(r.dir / f"{RUN_MARKER}.json"), reverse=True)
    return found


def _subdirs(root: Path) -> list[Path]:
    try:
        return sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return []


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


# --------------------------------------------------------------------------- #
# 문서 단위
# --------------------------------------------------------------------------- #
@dataclass
class DocArtifacts:
    """문서 하나의 산출물 폴더."""

    doc_name: str
    dir: Path
    slug: str = ""
    doc_type: str = ""

    def __post_init__(self) -> None:
        self.slug = self.slug or self.dir.name

    @property
    def available(self) -> set[str]:
        """실제로 존재하는 단계 이름(``.fingerprint`` 사이드카는 세지 않는다)."""
        try:
            return {p.stem for p in self.dir.glob("*.json")}
        except OSError:
            return set()

    def load(self, stage: str) -> Optional[dict]:
        """단계 산출물을 읽는다. 없거나 깨졌으면 ``None``(예외를 올리지 않는다)."""
        path = self.dir / f"{stage}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("[Reader] %s 읽기 실패: %s", path, exc)
            return None
        return data if isinstance(data, dict) else None


# --------------------------------------------------------------------------- #
# 그래프 조회 인덱스
# --------------------------------------------------------------------------- #
class GraphIndex:
    """:class:`ConceptGraph` 조회를 dict 로 바꾼 얇은 래퍼.

    ``ConceptGraph.node_id_of``/``edge_of`` 는 O(N) 선형 탐색이라 fact 수백 개 ×
    조회 수백 번이면 눈에 띄게 느리다. **그래프 모델은 건드리지 않는다** — 파이프라인의
    성능 특성을 진단 도구 사정으로 바꾸지 않기 위해서다.
    """

    def __init__(self, graph: Optional[ConceptGraph]) -> None:
        self.graph = graph
        self._node: dict[tuple[str, str], ConceptNode] = {}
        self._touching: dict[tuple[str, str], list[ConceptEdge]] = {}
        self._by_pair: dict[tuple[str, str], ConceptEdge] = {}
        if graph is None:
            return
        for node in graph.nodes:
            for m in node.members:
                self._node[(m.doc, m.fact_id)] = node
        for edge in graph.edges:
            self._by_pair.setdefault(edge.pair_key, edge)
            for ref in (edge.left, edge.right):
                self._touching.setdefault((ref.doc, ref.fact_id), []).append(edge)

    def node_of(self, doc: str, fact_id: str) -> Optional[ConceptNode]:
        return self._node.get((doc, fact_id))

    def edges_touching(self, doc: str, fact_id: str) -> list[ConceptEdge]:
        return list(self._touching.get((doc, fact_id), ()))

    def edges_between(self, doc: str, fact_id: str, other_doc: str) -> list[ConceptEdge]:
        """이 fact 와 ``other_doc`` 을 잇는(또는 막는) 엣지들."""
        out = []
        for edge in self.edges_touching(doc, fact_id):
            other = edge.right if (edge.left.doc, edge.left.fact_id) == (doc, fact_id) \
                else edge.left
            if other.doc == other_doc:
                out.append(edge)
        return out

    def partners(self, doc: str, fact_id: str, target_doc: str) -> list[str]:
        """같은 개념에 속한 ``target_doc`` 의 fact id 들."""
        node = self.node_of(doc, fact_id)
        if node is None:
            return []
        return [m.fact_id for m in node.members if m.doc == target_doc]

    def edge_index(self, edge: ConceptEdge) -> int:
        """``concept_graph.json`` 안에서의 위치 — 사람이 파일을 열어 찾아가는 좌표."""
        if self.graph is None:
            return -1
        for i, e in enumerate(self.graph.edges):
            if e is edge:
                return i
        return -1


# --------------------------------------------------------------------------- #
# 실행 단위
# --------------------------------------------------------------------------- #
@dataclass
class RunSnapshot:
    """실행 1회가 남긴 것 전부."""

    ref: RunRef
    reference: Optional[DocArtifacts] = None
    docs: dict[str, DocArtifacts] = field(default_factory=dict)
    """문서 이름 → 산출물 폴더(기준 문서 포함)."""

    comparisons: list[dict] = field(default_factory=list)
    compare_stats: dict = field(default_factory=dict)
    graph: Optional[ConceptGraph] = None
    index: GraphIndex = field(default_factory=lambda: GraphIndex(None))
    candidate_pairs: Optional[dict] = None
    problems: list[str] = field(default_factory=list)
    capabilities: set[str] = field(default_factory=set)

    # ------------------------------------------------------------------ #
    @property
    def label(self) -> str:
        return self.ref.label

    @property
    def reference_doc(self) -> str:
        return self.reference.doc_name if self.reference else ""

    @property
    def target_docs(self) -> list[str]:
        """비교에 등장한 대상 문서(등장 순서 유지)."""
        seen: list[str] = []
        for c in self.comparisons:
            doc = str(c.get("target_doc") or "")
            if doc and doc not in seen:
                seen.append(doc)
        return seen

    def doc(self, doc_name: str) -> Optional[DocArtifacts]:
        return self.docs.get(doc_name)

    def comparisons_for(self, target_doc: str = "") -> list[dict]:
        if not target_doc:
            return list(self.comparisons)
        return [c for c in self.comparisons if c.get("target_doc") == target_doc]

    def ranked_for(self, ref_fact_id: str, target_doc: str) -> Optional[dict]:
        """``candidate_pairs`` 에서 (기준 fact × 대상 문서) 칸을 꺼낸다."""
        for entry in (self.candidate_pairs or {}).get("by_ref") or []:
            if entry.get("ref_fact_id") != ref_fact_id:
                continue
            for slot in entry.get("targets") or []:
                if slot.get("doc") == target_doc:
                    return slot
        return None

    def facts_of(self, doc_name: str) -> dict[str, dict]:
        """문서의 ``facts.json`` 을 ``{fact_id: fact}`` 로. 없으면 빈 dict."""
        doc = self.docs.get(doc_name)
        data = doc.load("facts") if doc else None
        return {str(f.get("fact_id")): f for f in (data or {}).get("facts") or []}


def load_snapshot(ref: RunRef) -> RunSnapshot:
    """실행 하나를 읽어 :class:`RunSnapshot` 으로. **예외를 던지지 않는다.**"""
    snap = RunSnapshot(ref=ref)
    holder = DocArtifacts(doc_name="", dir=ref.dir)

    result = holder.load(RUN_MARKER)
    if result is None:
        snap.problems.append(
            f"{RUN_MARKER}.json 을 읽지 못했습니다 — 비교가 끝나지 않은 실행일 수 있습니다.")
        return snap
    snap.comparisons = [c for c in (result.get("comparisons") or []) if isinstance(c, dict)]
    snap.compare_stats = result.get("stats") if isinstance(result.get("stats"), dict) else {}

    ref_name = str(result.get("reference") or "")
    snap.reference = DocArtifacts(doc_name=ref_name, dir=ref.dir)
    snap.docs[ref_name] = snap.reference

    # 대상 문서 폴더는 **기준 폴더 옆**에 있다.
    #
    # 스냅샷(_runs/*)에는 붙이지 않는다. 옆에 있는 것은 *현재* 실행의 산출물이고,
    # fact_id(`fact-word-4` 등)는 실행마다 다시 매겨지는 위치 기반 값이라 서로 다른
    # 실행의 fact 를 같은 id 로 착각하게 만든다 — 진단 도구가 낼 수 있는 최악의 오답이다.
    if ref.is_snapshot:
        snap.problems.append(
            "스냅샷이라 대상 문서의 단계별 산출물은 붙이지 않습니다 "
            "— fact_id 는 실행마다 다시 매겨져 현재 폴더와 섞으면 엉뚱한 fact 를 가리킵니다.")
    else:
        for doc_name in snap.target_docs:
            cand = ref.dir.parent / ArtifactStore.slug(doc_name)
            if cand.is_dir():
                snap.docs[doc_name] = DocArtifacts(doc_name=doc_name, dir=cand)

    _load_graph(snap, holder)
    _load_candidate_pairs(snap, holder)
    _resolve_doc_types(snap)
    _assess(snap)
    return snap


def load_run(root: str | Path, label: str = "") -> Optional[RunSnapshot]:
    """``root`` 에서 ``label`` 실행을 읽는다. 라벨을 비우면 가장 최근 실행."""
    runs = list_runs(root)
    if not runs:
        return None
    chosen = next((r for r in runs if r.label == label), None) if label else runs[0]
    return load_snapshot(chosen) if chosen else None


# --------------------------------------------------------------------------- #
def _load_graph(snap: RunSnapshot, holder: DocArtifacts) -> None:
    data = holder.load("concept_graph")
    if data is None:
        snap.problems.append(
            "concept_graph.json 이 없습니다 — use_concept_graph: false 로 돌린 실행일 수 있습니다.")
        return
    # ConceptGraph.from_dict 는 이미 완전 방어적이다(타입·누락 키 전부 흡수).
    snap.graph = ConceptGraph.from_dict(data)
    snap.index = GraphIndex(snap.graph)


def _load_candidate_pairs(snap: RunSnapshot, holder: DocArtifacts) -> None:
    data = holder.load("candidate_pairs")
    if data is None:
        snap.problems.append(
            "candidate_pairs.json 이 없어 'recall 이 후보를 만들었는가'를 확인할 수 "
            "없습니다 — fact.save_candidate_pairs 를 켜고 다시 실행하세요.")
        return
    snap.candidate_pairs = data


def _resolve_doc_types(snap: RunSnapshot) -> None:
    """``compact_raw`` 에서 doc_type 을 읽어 채운다(없으면 빈 문자열로 둔다)."""
    for doc in snap.docs.values():
        compact = doc.load("compact_raw")
        if compact:
            doc.doc_type = str(compact.get("doc_type") or "")


def _assess(snap: RunSnapshot) -> None:
    """무엇을 할 수 있는 실행인가 — 화면이 어떤 모드를 켤지 판단하는 근거."""
    caps = snap.capabilities
    if snap.comparisons:
        caps.add("debug")
    if snap.reference and "facts" in snap.reference.available:
        caps.add("learn")
    elif snap.ref.is_snapshot:
        snap.problems.append(
            "스냅샷에는 단계별 산출물이 없어(비교 결과 3종만 보관) 학습 모드를 켤 수 없습니다.")
    if snap.graph is not None:
        caps.add("graph")
    if snap.candidate_pairs is not None:
        caps.add("cause_recall")
    if any("facts_by_block" in d.available for d in snap.docs.values()):
        caps.add("cause_extract")
    else:
        snap.problems.append(
            "facts_by_block.json 이 없어 'F3 추출 누락' 여부를 확인할 수 없습니다 "
            "— fact.save_facts_by_block 을 켜고 다시 실행하세요.")


# --------------------------------------------------------------------------- #
# 산출물 해석 헬퍼 — 스키마의 함정을 여기 한 곳에 가둔다
# --------------------------------------------------------------------------- #
def target_of(comparison: dict) -> Optional[dict]:
    """비교 항목의 대상 쪽. ``result == "missing"`` 이면 ``None`` 이다."""
    target = comparison.get("target")
    return target if isinstance(target, dict) else None


def low_confidence_ids(validation_report: Optional[dict]) -> set[str]:
    """저신뢰 fact id 집합.

    ``validation_report.json`` 에는 이 목록이 **없다** — ``ValidationReport`` 의
    파이썬 property 라 ``to_dict()`` 에는 개수(``overall.low_confidence``)만 실린다.
    그래서 ``checks`` 에서 같은 규칙(``severity == "error"``)으로 되살린다.
    """
    checks = (validation_report or {}).get("checks") or []
    return {
        str(c.get("fact_id"))
        for c in checks
        if isinstance(c, dict) and c.get("severity") == "error" and c.get("fact_id")
    }


def split_pair_id(pair_id: str) -> Optional[tuple[tuple[str, str], tuple[str, str]]]:
    """``"docA#fact-x ↔ docB#fact-y"`` → ``(("docA","fact-x"), ("docB","fact-y"))``.

    모양이 다르면 ``None`` — 호출자는 원문을 그대로 보여 주면 된다.
    """
    if PAIR_SEP not in (pair_id or ""):
        return None
    left, _, right = pair_id.partition(PAIR_SEP)
    parts = [side.strip().split("#", 1) for side in (left, right)]
    if any(len(p) != 2 for p in parts):
        return None
    return (parts[0][0], parts[0][1]), (parts[1][0], parts[1][1])


def attributes_text(attributes: Any) -> str:
    """``{자유키: {value, unit}}`` 를 사람이 읽는 한 줄로.

    키는 LLM 이 짓는 자유 문자열이라(한글·영문 혼재) **해석하지 않고 그대로** 나열한다.
    """
    if not isinstance(attributes, dict) or not attributes:
        return "-"
    out = []
    for key, attr in attributes.items():
        attr = attr if isinstance(attr, dict) else {}
        value = attr.get("value")
        unit = str(attr.get("unit") or "")
        out.append(f"{key}={value}{unit}".strip())
    return ", ".join(out)


def iter_edges(graph: Optional[ConceptGraph]) -> Iterable[tuple[int, ConceptEdge]]:
    """``(파일 안 인덱스, 엣지)`` — 사람이 ``edges[42]`` 로 찾아갈 수 있게."""
    for i, edge in enumerate((graph.edges if graph else [])):
        yield i, edge

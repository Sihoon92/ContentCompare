"""F7 개념 그래프 데이터 모델 — 노드(개념) · 엣지(fact 쌍 관계).

설계는 ``docs/FACT_F7_DESIGN.md`` §3 참고. 이 모듈은 **자료구조와 조회만** 담당한다.
근거 검증·병합은 ``concept_assembler``, 관계 산출은 ``concept_builder`` 의 몫이다.

**엣지는 개념이 아니라 fact 쌍을 가리킨다.** ``concept_id`` 는 병합의 결과라 실행마다
바뀌지만 엣지가 담은 근거 인용은 특정 fact 두 개에 대한 주장이기 때문이다. 노드는
엣지에서 파생된다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# 관계 종류 — 코드는 이 값만 보고 집행하고 axis 문자열은 해석하지 않는다.
SAME_AS = "same_as"
DIFFERS_BY = "differs_by"
UNKNOWN = "unknown"
RELATIONS = (SAME_AS, DIFFERS_BY, UNKNOWN)

# 관계를 확정한 주체.
BY_CODE = "code"          # 정규화 이름 완전일치
BY_ONTOLOGY = "ontology"  # 사람이 knowledge/ontology.yaml 에 승격
BY_LLM = "llm"            # 이번 실행에서 LLM 이 판단(아직 아무도 확인하지 않음)


def _as_str(v: Any, default: str = "") -> str:
    return v if isinstance(v, str) else (default if v is None else str(v))


def _as_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class FactRef:
    """문서 안의 fact 하나를 가리키는 참조."""

    doc: str
    fact_id: str

    @property
    def key(self) -> str:
        return f"{self.doc}#{self.fact_id}"

    def to_dict(self) -> dict[str, Any]:
        return {"doc": self.doc, "fact_id": self.fact_id}

    @classmethod
    def from_dict(cls, d: Any) -> "FactRef":
        d = d if isinstance(d, dict) else {}
        return cls(doc=_as_str(d.get("doc")), fact_id=_as_str(d.get("fact_id")))


@dataclass
class ConceptMember:
    """개념 노드에 속한 fact 하나."""

    doc: str
    fact_id: str
    entity_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"doc": self.doc, "fact_id": self.fact_id, "entity_name": self.entity_name}

    @classmethod
    def from_dict(cls, d: Any) -> "ConceptMember":
        d = d if isinstance(d, dict) else {}
        return cls(
            doc=_as_str(d.get("doc")),
            fact_id=_as_str(d.get("fact_id")),
            entity_name=_as_str(d.get("entity_name")),
        )


@dataclass
class ConceptNode:
    """개념 하나. ``members`` 가 여러 문서에 걸쳐 있으면 두 문서가 같은 것을 말한 것이다."""

    concept_id: str = ""
    label: str = ""
    members: list[ConceptMember] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "concept_id": self.concept_id,
            "label": self.label,
            "members": [m.to_dict() for m in self.members],
        }

    @classmethod
    def from_dict(cls, d: Any) -> "ConceptNode":
        d = d if isinstance(d, dict) else {}
        return cls(
            concept_id=_as_str(d.get("concept_id")),
            label=_as_str(d.get("label")),
            members=[ConceptMember.from_dict(m) for m in (d.get("members") or [])],
        )


@dataclass
class ConceptEdge:
    """두 fact 사이의 관계 주장."""

    relation: str
    left: FactRef
    right: FactRef
    axis: str = ""
    """``differs_by`` 일 때 무엇이 다른지. LLM 이 도메인에 맞게 짓는 이름이며 코드는 읽지 않는다."""

    left_text: str = ""
    right_text: str = ""
    """근거 인용(원문 그대로). ``same_as`` 는 이것이 실재해야 성립한다."""

    reason: str = ""
    decided_by: str = BY_LLM
    promoted: bool = False
    recall_score: float = 0.0
    """후보 생성 시 유사도. 판정에 쓰지 않고 진단용으로만 남긴다."""

    rejected_by: str = ""
    """조립 단계에서 강등된 사유(``evidence`` | ``differs_by`` | ``contradiction``)."""

    @property
    def pair_key(self) -> tuple[str, str]:
        """방향 무관 키 — 같은 쌍을 한 번만 다루기 위함."""
        a, b = sorted((self.left.key, self.right.key))
        return (a, b)

    def to_dict(self) -> dict[str, Any]:
        return {
            "relation": self.relation,
            "left": self.left.to_dict(),
            "right": self.right.to_dict(),
            "axis": self.axis,
            "left_text": self.left_text,
            "right_text": self.right_text,
            "reason": self.reason,
            "decided_by": self.decided_by,
            "promoted": self.promoted,
            "recall_score": self.recall_score,
            "rejected_by": self.rejected_by,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "ConceptEdge":
        d = d if isinstance(d, dict) else {}
        return cls(
            relation=_as_str(d.get("relation"), UNKNOWN),
            left=FactRef.from_dict(d.get("left")),
            right=FactRef.from_dict(d.get("right")),
            axis=_as_str(d.get("axis")),
            left_text=_as_str(d.get("left_text")),
            right_text=_as_str(d.get("right_text")),
            reason=_as_str(d.get("reason")),
            decided_by=_as_str(d.get("decided_by"), BY_LLM),
            promoted=bool(d.get("promoted")),
            recall_score=_as_float(d.get("recall_score")),
            rejected_by=_as_str(d.get("rejected_by")),
        )


@dataclass
class ConceptGraph:
    """실행 전체에서 하나. 노드는 개념, 엣지는 fact 쌍 관계."""

    nodes: list[ConceptNode] = field(default_factory=list)
    edges: list[ConceptEdge] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def node_id_of(self, doc: str, fact_id: str) -> Optional[str]:
        for node in self.nodes:
            for m in node.members:
                if m.doc == doc and m.fact_id == fact_id:
                    return node.concept_id
        return None

    def partners(self, doc: str, fact_id: str, target_doc: str) -> list[ConceptMember]:
        """같은 개념에 속한 ``target_doc`` 의 fact 들. 자기 문서는 제외한다."""
        if target_doc == doc:
            return []
        concept_id = self.node_id_of(doc, fact_id)
        if concept_id is None:
            return []
        for node in self.nodes:
            if node.concept_id == concept_id:
                return [m for m in node.members if m.doc == target_doc]
        return []

    def edge_of(self, a: FactRef, b: FactRef) -> Optional[ConceptEdge]:
        key = tuple(sorted((a.key, b.key)))
        for edge in self.edges:
            if edge.pair_key == key:
                return edge
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
            "stats": self.stats,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "ConceptGraph":
        d = d if isinstance(d, dict) else {}
        return cls(
            nodes=[ConceptNode.from_dict(n) for n in (d.get("nodes") or [])],
            edges=[
                ConceptEdge.from_dict(e)
                for e in (d.get("edges") or [])
                if isinstance(e, dict)
            ],
            stats=d.get("stats") if isinstance(d.get("stats"), dict) else {},
        )

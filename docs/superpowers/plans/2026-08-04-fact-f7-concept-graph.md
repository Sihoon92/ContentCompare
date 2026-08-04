# F7 개념 그래프 (Concept Graph) 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 기준 fact 에 대응하는 대상 fact 가 있는지를 임베딩 유사도 임계값이 아니라 **개념 그래프 조회**로 판정해, 비교 대상이 아닌 쌍을 비교하는 오류를 없앤다.

**Architecture:** 문서별 `facts.json` 이 모두 나온 뒤(F3/F4a 완료), 실행 전체에서 **하나의 개념 그래프**를 만든다. 후보 쌍은 임베딩/BM25 로 넉넉히 뽑고(recall 전용), 관계는 `knowledge/ontology.yaml`(사람 확정) → 정규화 이름 완전일치(코드) → LLM 배치 순으로 정한다. 코드는 `same_as`/`differs_by` 라는 엣지 **종류만** 보고 집행하며 `axis` 값("측정조건", "산정방식" 등 LLM 이 도메인에 맞게 짓는 이름)은 해석하지 않는다. F5 Matcher 는 유사도 대신 이 그래프를 조회한다.

**Tech Stack:** Python 3.10+, dataclass, PyYAML(이미 코어 의존성), pytest. 새 서드파티 의존성 없음.

## Global Constraints

- 설계 문서: [`docs/FACT_F7_DESIGN.md`](../../FACT_F7_DESIGN.md). 이 계획은 그 설계를 구현한다.
- 코드 주석·docstring·문서는 **한국어**, 식별자/커맨드는 영어 (`CLAUDE.md` 규약).
- 모든 테스트는 **네트워크·Office(COM)·Ollama 없이** 돌아야 한다. LLM/임베더는 가짜 객체 주입 (`tests/test_fact_matcher.py` 의 `_FakeEmbedder`, `tests/test_fact_comparator.py` 의 `_CountingChat` 패턴).
- **판정 규칙은 하나**: 두 fact 의 개념이 `same_as` 로 이어져 있지 않으면 비교하지 않는다. 새 임계값을 판정 경로에 도입하지 않는다.
- **권한 비대칭**: 연결(`same_as`)은 근거 인용 검증을 통과해야 성립. 차단은 코드 단독으로 가능.
- 계측(`run_stats.json`, `stats`)을 제거하지 않는다 — 오판 추적이 이 파이프라인의 존재 이유다.
- 커밋 메시지는 한국어 본문 + 아래 두 줄로 끝낸다:
  ```
  Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01GJ9o7G2SMZKKQWFi4XZNZC
  ```
- 전체 테스트는 `python -m pytest -q` 로 돌린다(현재 363 passed 기준선).

## 파일 구성

| 파일 | 책임 |
|---|---|
| `contentcompare/fact/concept_models.py` (신규) | `FactRef` / `ConceptMember` / `ConceptNode` / `ConceptEdge` / `ConceptGraph` — 자료구조·직렬화·조회만 |
| `contentcompare/fact/ontology.py` (신규) | `knowledge/ontology.yaml` 로드와 이름 쌍 조회 |
| `contentcompare/fact/concept_assembler.py` (신규) | 근거 검증 → `same_as` 병합(union-find) → `differs_by` 위반 강등 → 노드 생성 |
| `contentcompare/fact/concept_builder.py` (신규) | 후보 쌍 생성 → 온톨로지/완전일치 확정 → LLM 배치 → assembler 호출 |
| `contentcompare/fact/prompts.py` (수정) | `CONCEPT_SYSTEM`, `build_concept_user` 추가 |
| `contentcompare/fact/fact_matcher.py` (수정) | `ConceptMatcher` 추가(기존 `FactMatcher` 는 recall 용도로 그대로 재사용) |
| `contentcompare/fact/validator.py` (수정) | `evidence_coverage` 공개 함수 추출 + `validate_graph` 추가 |
| `contentcompare/fact/pipeline.py` (수정) | F7 단계 삽입, `concept_graph.json` / `concept_validation.json` 저장 |
| `contentcompare/config.py`, `config/config.example.yaml` (수정) | F7 설정 6종 |
| `contentcompare/report/fact_report.py` (수정) | 연결 근거 표시 + "검토 필요" 섹션 |

---

### Task 1: 개념 그래프 데이터 모델

**Files:**
- Create: `contentcompare/fact/concept_models.py`
- Test: `tests/test_concept_models.py`

**Interfaces:**
- Consumes: 없음(신규 모듈)
- Produces:
  - 상수 `SAME_AS="same_as"`, `DIFFERS_BY="differs_by"`, `UNKNOWN="unknown"`, `BY_CODE="code"`, `BY_ONTOLOGY="ontology"`, `BY_LLM="llm"`
  - `FactRef(doc: str, fact_id: str)` — frozen dataclass, `.key -> str`("doc#fact_id"), `.to_dict()`, `.from_dict(d)`
  - `ConceptMember(doc: str, fact_id: str, entity_name: str = "")` — `.to_dict()`, `.from_dict(d)`
  - `ConceptNode(concept_id: str, label: str, members: list[ConceptMember])` — `.to_dict()`, `.from_dict(d)`
  - `ConceptEdge(relation, left: FactRef, right: FactRef, axis="", left_text="", right_text="", reason="", decided_by=BY_LLM, promoted=False, recall_score=0.0, rejected_by="")` — `.pair_key -> tuple[str,str]`(방향 무관), `.to_dict()`, `.from_dict(d)`
  - `ConceptGraph(nodes, edges, stats)` — `.node_id_of(doc, fact_id) -> str|None`, `.partners(doc, fact_id, target_doc) -> list[ConceptMember]`, `.edge_of(a: FactRef, b: FactRef) -> ConceptEdge|None`, `.to_dict()`, `.from_dict(d)`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_concept_models.py`:

```python
"""F7 개념 그래프 데이터 모델 테스트 — 순수 자료구조(LLM/네트워크 불필요)."""

from contentcompare.fact.concept_models import (
    BY_CODE,
    DIFFERS_BY,
    SAME_AS,
    ConceptEdge,
    ConceptGraph,
    ConceptMember,
    ConceptNode,
    FactRef,
)


def _graph() -> ConceptGraph:
    node = ConceptNode(
        concept_id="c-0001",
        label="공칭용량",
        members=[
            ConceptMember(doc="기준.xlsx", fact_id="fact-row-1", entity_name="공칭용량"),
            ConceptMember(doc="규격서.docx", fact_id="fact-word-7", entity_name="공칭용량"),
        ],
    )
    edge = ConceptEdge(
        relation=SAME_AS,
        left=FactRef("기준.xlsx", "fact-row-1"),
        right=FactRef("규격서.docx", "fact-word-7"),
        left_text="1150",
        right_text="공칭용량 1150 mAh",
        reason="같은 항목",
        decided_by=BY_CODE,
    )
    return ConceptGraph(nodes=[node], edges=[edge], stats={"same_as": 1})


def test_fact_ref_key_joins_doc_and_id():
    assert FactRef("a.xlsx", "fact-1").key == "a.xlsx#fact-1"


def test_pair_key_is_direction_independent():
    a, b = FactRef("x.xlsx", "f1"), FactRef("y.docx", "f2")
    assert ConceptEdge(SAME_AS, a, b).pair_key == ConceptEdge(SAME_AS, b, a).pair_key


def test_node_id_of_finds_member():
    g = _graph()
    assert g.node_id_of("규격서.docx", "fact-word-7") == "c-0001"
    assert g.node_id_of("규격서.docx", "없는id") is None


def test_partners_returns_only_requested_document():
    """기준 fact 와 같은 개념에 속한 **그 대상 문서**의 fact 만 후보가 된다."""
    g = _graph()
    partners = g.partners("기준.xlsx", "fact-row-1", "규격서.docx")
    assert [m.fact_id for m in partners] == ["fact-word-7"]
    assert g.partners("기준.xlsx", "fact-row-1", "발표.pptx") == []


def test_partners_excludes_self_document():
    g = _graph()
    assert g.partners("기준.xlsx", "fact-row-1", "기준.xlsx") == []


def test_edge_of_is_direction_independent():
    g = _graph()
    a, b = FactRef("기준.xlsx", "fact-row-1"), FactRef("규격서.docx", "fact-word-7")
    assert g.edge_of(a, b) is g.edge_of(b, a)
    assert g.edge_of(a, FactRef("규격서.docx", "다른id")) is None


def test_round_trip_serialization():
    g = _graph()
    restored = ConceptGraph.from_dict(g.to_dict())
    assert restored.to_dict() == g.to_dict()
    assert restored.node_id_of("기준.xlsx", "fact-row-1") == "c-0001"


def test_from_dict_tolerates_garbage():
    """저장된 산출물이 손상돼도 죽지 않는다(다른 fact 모델과 같은 방어 수준)."""
    g = ConceptGraph.from_dict({"nodes": [{}, None], "edges": ["x", {}]})
    assert g.nodes and g.nodes[0].concept_id == ""
    assert len(g.edges) == 1


def test_differs_by_edge_carries_axis():
    e = ConceptEdge(
        DIFFERS_BY, FactRef("a", "1"), FactRef("b", "2"), axis="측정조건",
    )
    assert e.to_dict()["axis"] == "측정조건"
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `python -m pytest tests/test_concept_models.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'contentcompare.fact.concept_models'`

- [ ] **Step 3: 모델 구현**

`contentcompare/fact/concept_models.py`:

```python
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
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_concept_models.py -q`
Expected: PASS (9 passed)

- [ ] **Step 5: 커밋**

```bash
git add contentcompare/fact/concept_models.py tests/test_concept_models.py
git commit -m "$(cat <<'EOF'
feat(fact): F7 개념 그래프 데이터 모델

엣지는 concept_id 가 아니라 fact 쌍(FactRef)을 가리킨다 — concept_id 는 병합의
결과라 실행마다 바뀌지만 근거 인용은 특정 fact 두 개에 대한 주장이기 때문이다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01GJ9o7G2SMZKKQWFi4XZNZC
EOF
)"
```

---

### Task 2: 온톨로지 파일 로더

**Files:**
- Create: `contentcompare/fact/ontology.py`
- Test: `tests/test_ontology.py`

**Interfaces:**
- Consumes: `concept_models.SAME_AS`/`DIFFERS_BY`, `fact_matcher.norm_name`
- Produces:
  - `Ontology` — `.relation_for(name_a: str, name_b: str) -> tuple[str, str, str] | None` (`(relation, axis, reason)`), `.summary(max_items: int = 20) -> str`(프롬프트 주입용), `.__len__()`
  - `load_ontology(path: str = "knowledge/ontology.yaml") -> Ontology`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_ontology.py`:

```python
"""knowledge/ontology.yaml 로더 테스트 — 파일 IO 만, LLM 불필요."""

from contentcompare.fact.concept_models import DIFFERS_BY, SAME_AS
from contentcompare.fact.ontology import Ontology, load_ontology

YAML = """\
same_as:
  - names: ["고객 표준 버전", "문서 기준 규격"]
    reason: "둘 다 SEC Req. ver.4.7 을 가리킨다"
differs_by:
  - names: ["1개월저장온도", "표준환경온도"]
    axis: "측정조건"
    reason: "저장 조건과 상시 환경 조건은 다르다"
  - names: ["평가환경온도", "평가환경습도"]
    axis: "물리량"
"""


def _write(tmp_path, text: str) -> str:
    p = tmp_path / "ontology.yaml"
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_missing_file_yields_empty_ontology(tmp_path):
    """온톨로지가 없는 상태가 정상 시작 경로다."""
    onto = load_ontology(str(tmp_path / "없음.yaml"))
    assert len(onto) == 0
    assert onto.relation_for("가", "나") is None


def test_same_as_pair_is_found_in_both_directions(tmp_path):
    onto = load_ontology(_write(tmp_path, YAML))
    rel, axis, reason = onto.relation_for("고객 표준 버전", "문서 기준 규격")
    assert (rel, axis) == (SAME_AS, "")
    assert "SEC Req" in reason
    assert onto.relation_for("문서 기준 규격", "고객 표준 버전")[0] == SAME_AS


def test_name_matching_ignores_spacing_and_symbols(tmp_path):
    """문서마다 '고객표준버전'/'고객 표준 버전' 처럼 표기가 흔들린다."""
    onto = load_ontology(_write(tmp_path, YAML))
    assert onto.relation_for("고객표준버전", "문서기준규격")[0] == SAME_AS


def test_differs_by_carries_axis(tmp_path):
    onto = load_ontology(_write(tmp_path, YAML))
    rel, axis, _ = onto.relation_for("1개월저장온도", "표준환경온도")
    assert (rel, axis) == (DIFFERS_BY, "측정조건")


def test_unrelated_pair_returns_none(tmp_path):
    onto = load_ontology(_write(tmp_path, YAML))
    assert onto.relation_for("공칭용량", "정격용량") is None


def test_differs_by_wins_over_same_as(tmp_path):
    """차단이 연결을 이긴다(설계 §2.3 비대칭 권한)."""
    text = (
        'same_as:\n  - names: ["가", "나"]\n'
        'differs_by:\n  - names: ["가", "나"]\n    axis: "기간"\n'
    )
    onto = load_ontology(_write(tmp_path, text))
    assert onto.relation_for("가", "나")[0] == DIFFERS_BY


def test_three_names_expand_to_all_pairs(tmp_path):
    text = 'same_as:\n  - names: ["가", "나", "다"]\n'
    onto = load_ontology(_write(tmp_path, text))
    assert onto.relation_for("나", "다")[0] == SAME_AS


def test_malformed_entries_are_skipped(tmp_path):
    """사람이 손으로 쓰는 파일이므로 깨진 항목이 전체를 죽이면 안 된다."""
    text = 'same_as:\n  - names: ["혼자"]\n  - "문자열"\n  - names: ["가", "나"]\n'
    onto = load_ontology(_write(tmp_path, text))
    assert len(onto) == 1
    assert onto.relation_for("가", "나")[0] == SAME_AS


def test_summary_lists_pairs_for_prompt(tmp_path):
    onto = load_ontology(_write(tmp_path, YAML))
    text = onto.summary()
    assert "고객 표준 버전" in text and "측정조건" in text


def test_empty_ontology_summary_is_empty_string():
    assert Ontology().summary() == ""
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `python -m pytest tests/test_ontology.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'contentcompare.fact.ontology'`

- [ ] **Step 3: 로더 구현**

`contentcompare/fact/ontology.py`:

```python
"""사람이 관리하는 영속 온톨로지(``knowledge/ontology.yaml``) 로더.

``knowledge/*.md``(도메인 지식)와 같은 human-in-the-loop 자리다. 실행이 만든
``concept_graph.json`` 을 사람이 검토해 확정한 관계만 이 파일로 옮기면, 다음 실행부터
그 쌍은 LLM 에 묻지 않는다 — 재현성과 비용을 함께 해결하는 장치다.

키는 **정규화된 항목명**이다. ``fact_id`` 는 실행마다 바뀌므로 쓸 수 없다.
"""

from __future__ import annotations

import itertools
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional

import yaml

from .concept_models import DIFFERS_BY, SAME_AS
from .fact_matcher import norm_name

logger = logging.getLogger(__name__)

DEFAULT_ONTOLOGY_PATH = os.path.join("knowledge", "ontology.yaml")


@dataclass
class Ontology:
    """정규화 이름 쌍 → ``(relation, axis, reason)``."""

    pairs: dict[frozenset, tuple[str, str, str]] = field(default_factory=dict)
    labels: dict[frozenset, tuple[str, str]] = field(default_factory=dict)
    """사람이 쓴 원래 표기(프롬프트 요약에 그대로 보여주기 위함)."""

    def __len__(self) -> int:
        return len(self.pairs)

    def relation_for(self, name_a: str, name_b: str) -> Optional[tuple[str, str, str]]:
        a, b = norm_name(name_a), norm_name(name_b)
        if not a or not b or a == b:
            return None
        return self.pairs.get(frozenset((a, b)))

    def summary(self, max_items: int = 20) -> str:
        """프롬프트에 넣을 요약 — LLM 이 기존 판단과 일관되게 답하도록."""
        lines = []
        for key, (relation, axis, _reason) in list(self.pairs.items())[:max_items]:
            left, right = self.labels.get(key, tuple(sorted(key)))
            tail = f" (축: {axis})" if axis else ""
            lines.append(f"- {left} / {right} → {relation}{tail}")
        return "\n".join(lines)


def load_ontology(path: str = DEFAULT_ONTOLOGY_PATH) -> Ontology:
    """YAML 을 읽어 :class:`Ontology` 로. 파일이 없으면 빈 온톨로지(정상 경로)."""
    onto = Ontology()
    if not os.path.exists(path):
        return onto
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as e:  # 손으로 쓰는 파일이라 깨질 수 있다
        logger.warning("[Ontology] %s 를 읽지 못했습니다: %s", path, e)
        return onto

    # same_as 를 먼저 넣고 differs_by 로 덮는다 — 차단이 연결을 이긴다(설계 §2.3).
    _load_section(onto, data.get("same_as"), SAME_AS)
    _load_section(onto, data.get("differs_by"), DIFFERS_BY)
    logger.info("[Ontology] %s 쌍 로드(%s)", len(onto), path)
    return onto


def _load_section(onto: Ontology, entries: Any, relation: str) -> None:
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        names = [str(n) for n in (entry.get("names") or []) if str(n).strip()]
        if len(names) < 2:
            continue
        axis = str(entry.get("axis") or "")
        reason = str(entry.get("reason") or "")
        for left, right in itertools.combinations(names, 2):
            a, b = norm_name(left), norm_name(right)
            if not a or not b or a == b:
                continue
            key = frozenset((a, b))
            onto.pairs[key] = (relation, axis, reason)
            onto.labels[key] = (left, right)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_ontology.py -q`
Expected: PASS (10 passed)

- [ ] **Step 5: 커밋**

```bash
git add contentcompare/fact/ontology.py tests/test_ontology.py
git commit -m "$(cat <<'EOF'
feat(fact): knowledge/ontology.yaml 로더 — 사람이 확정한 개념 관계

키는 정규화된 항목명이다(fact_id 는 실행마다 바뀐다). 같은 쌍이 same_as 와
differs_by 에 모두 있으면 differs_by 가 이긴다 — 차단이 연결을 이긴다는
비대칭 권한 원칙(설계 §2.3)을 파일 수준에서도 지킨다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01GJ9o7G2SMZKKQWFi4XZNZC
EOF
)"
```

---

### Task 3: 근거 검증 + 그래프 조립

**Files:**
- Modify: `contentcompare/fact/validator.py` (`evidence_coverage` 공개 함수 추출, 185-211행 근처)
- Create: `contentcompare/fact/concept_assembler.py`
- Test: `tests/test_concept_assembler.py`

**Interfaces:**
- Consumes: `concept_models.*`, `validator.evidence_coverage`, `fact_models.Fact`
- Produces:
  - `validator.evidence_coverage(claim: str, source_tokens: set[str]) -> float`
  - `concept_assembler.verify_evidence(edge: ConceptEdge, left: Fact, right: Fact) -> bool`
  - `concept_assembler.assemble(members: list[ConceptMember], edges: list[ConceptEdge], facts: dict[str, Fact]) -> ConceptGraph`
    - `facts` 는 `FactRef.key -> Fact` 매핑
    - 반환 그래프의 `stats` 키: `same_as`, `differs_by`, `unknown`, `rejected_evidence`, `rejected_differs_by`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_concept_assembler.py`:

```python
"""F7 그래프 조립 테스트 — 근거 검증과 병합 제약(전부 순수 코드)."""

from contentcompare.fact.concept_assembler import assemble, verify_evidence
from contentcompare.fact.concept_models import (
    BY_LLM,
    BY_ONTOLOGY,
    DIFFERS_BY,
    SAME_AS,
    UNKNOWN,
    ConceptEdge,
    ConceptMember,
    FactRef,
)
from contentcompare.fact.fact_models import Fact

REF_A = FactRef("기준.xlsx", "fact-row-1")
TGT_A = FactRef("규격서.docx", "fact-word-7")
TGT_B = FactRef("규격서.docx", "fact-word-11")


def _fact(fact_id: str, name: str, evidence: str) -> Fact:
    return Fact(fact_id=fact_id, entity_name=name, evidence_text=evidence,
                search_text=f"{name} {evidence}")


def _facts() -> dict:
    return {
        REF_A.key: _fact("fact-row-1", "공칭용량", "1150"),
        TGT_A.key: _fact("fact-word-7", "공칭용량", "공칭용량, 1150, mAh"),
        TGT_B.key: _fact("fact-word-11", "표준환경온도", "표준환경온도, 21 ~ 29, ℃"),
    }


def _members() -> list[ConceptMember]:
    return [
        ConceptMember("기준.xlsx", "fact-row-1", "공칭용량"),
        ConceptMember("규격서.docx", "fact-word-7", "공칭용량"),
        ConceptMember("규격서.docx", "fact-word-11", "표준환경온도"),
    ]


def _same(left=REF_A, right=TGT_A, **kw) -> ConceptEdge:
    kw.setdefault("left_text", "1150")
    kw.setdefault("right_text", "공칭용량, 1150, mAh")
    return ConceptEdge(SAME_AS, left, right, **kw)


# --------------------------------------------------------------------- #
# 근거 검증
# --------------------------------------------------------------------- #
def test_quoted_evidence_present_in_source_passes():
    facts = _facts()
    assert verify_evidence(_same(), facts[REF_A.key], facts[TGT_A.key]) is True


def test_fabricated_quote_is_rejected():
    """LLM 이 same_as 를 남발해도 근거가 없으면 성립하지 않는다(설계 §2.3)."""
    facts = _facts()
    edge = _same(left_text="존재하지 않는 문구입니다", right_text="이것도 없습니다")
    assert verify_evidence(edge, facts[REF_A.key], facts[TGT_A.key]) is False


def test_empty_quote_is_rejected():
    facts = _facts()
    assert verify_evidence(_same(left_text="", right_text=""), facts[REF_A.key],
                           facts[TGT_A.key]) is False


def test_separator_difference_still_passes():
    """LLM 이 셀 구분자를 바꿔 옮겨도 실재하는 근거는 통과해야 한다."""
    facts = _facts()
    edge = _same(right_text="공칭용량 1150 mAh")
    assert verify_evidence(edge, facts[REF_A.key], facts[TGT_A.key]) is True


# --------------------------------------------------------------------- #
# 병합
# --------------------------------------------------------------------- #
def test_same_as_merges_members_into_one_node():
    g = assemble(_members(), [_same()], _facts())
    assert g.node_id_of("기준.xlsx", "fact-row-1") == g.node_id_of("규격서.docx", "fact-word-7")
    assert g.stats["same_as"] == 1


def test_unmerged_facts_stay_in_their_own_nodes():
    g = assemble(_members(), [], _facts())
    assert len({n.concept_id for n in g.nodes}) == 3
    assert g.partners("기준.xlsx", "fact-row-1", "규격서.docx") == []


def test_rejected_evidence_prevents_merge():
    edge = _same(left_text="지어낸 문구", right_text="지어낸 문구")
    g = assemble(_members(), [edge], _facts())
    assert g.node_id_of("기준.xlsx", "fact-row-1") != g.node_id_of("규격서.docx", "fact-word-7")
    assert g.edges[0].relation == UNKNOWN
    assert g.edges[0].rejected_by == "evidence"
    assert g.stats["rejected_evidence"] == 1


def test_differs_by_blocks_merge_of_same_pair():
    """같은 쌍에 same_as 와 differs_by 가 함께 오면 병합하지 않는다."""
    edges = [
        _same(right=TGT_B, right_text="표준환경온도, 21 ~ 29, ℃"),
        ConceptEdge(DIFFERS_BY, REF_A, TGT_B, axis="측정조건"),
    ]
    g = assemble(_members(), edges, _facts())
    assert g.node_id_of("기준.xlsx", "fact-row-1") != g.node_id_of("규격서.docx", "fact-word-11")
    downgraded = [e for e in g.edges if e.rejected_by == "differs_by"]
    assert len(downgraded) == 1 and downgraded[0].relation == UNKNOWN


def test_differs_by_blocks_transitive_merge():
    """A=B, B=C 로 이어지려는데 A≠C 가 있으면 두 번째 병합을 막는다."""
    members = _members()
    edges = [
        _same(right=TGT_A),
        _same(left=TGT_A, right=TGT_B, left_text="공칭용량, 1150, mAh",
              right_text="표준환경온도, 21 ~ 29, ℃"),
        ConceptEdge(DIFFERS_BY, REF_A, TGT_B, axis="측정조건"),
    ]
    g = assemble(members, edges, _facts())
    assert g.node_id_of("기준.xlsx", "fact-row-1") == g.node_id_of("규격서.docx", "fact-word-7")
    assert g.node_id_of("규격서.docx", "fact-word-11") != g.node_id_of("기준.xlsx", "fact-row-1")


def test_promoted_edge_is_applied_before_llm_edge():
    """사람이 승격한 관계가 LLM 판단보다 먼저 적용된다."""
    llm_edge = _same(right=TGT_B, right_text="표준환경온도, 21 ~ 29, ℃", decided_by=BY_LLM)
    promoted = ConceptEdge(DIFFERS_BY, REF_A, TGT_B, axis="측정조건",
                           decided_by=BY_ONTOLOGY, promoted=True)
    g = assemble(_members(), [llm_edge, promoted], _facts())
    assert g.node_id_of("기준.xlsx", "fact-row-1") != g.node_id_of("규격서.docx", "fact-word-11")


def test_node_ids_are_stable_and_labelled():
    g = assemble(_members(), [_same()], _facts())
    ids = [n.concept_id for n in g.nodes]
    assert ids == sorted(ids) and ids[0] == "c-0001"
    assert g.nodes[0].label == "공칭용량"


def test_unknown_relation_never_merges():
    g = assemble(_members(), [ConceptEdge(UNKNOWN, REF_A, TGT_A)], _facts())
    assert g.node_id_of("기준.xlsx", "fact-row-1") != g.node_id_of("규격서.docx", "fact-word-7")
    assert g.stats["unknown"] == 1


def test_edge_referencing_unknown_fact_is_ignored():
    edge = _same(right=FactRef("규격서.docx", "없는id"))
    g = assemble(_members(), [edge], _facts())
    assert g.stats["rejected_evidence"] == 0
    assert len({n.concept_id for n in g.nodes}) == 3
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `python -m pytest tests/test_concept_assembler.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'contentcompare.fact.concept_assembler'`

- [ ] **Step 3-a: `validator.py` 에서 토큰 포함률 계산을 공개 함수로 추출**

`contentcompare/fact/validator.py` 의 `_check_evidence`(185행 근처) 바로 위에 추가:

```python
def evidence_coverage(claim: str, source_tokens: set[str]) -> float:
    """``claim`` 의 토큰 중 원문에 실재하는 비율(0.0~1.0).

    문자열 부분일치는 쓰지 않는다 — LLM 이 공백·개행·셀 구분자를 바꿔 옮기는 경향이
    있어 실재하는 근거도 실패로 뜬다. F7 개념 그래프의 인용 검증도 이 함수를 쓴다.
    """
    tokens = set(tokenize(claim))
    if not tokens:
        return 0.0
    return len(tokens & source_tokens) / len(tokens)
```

그리고 `_check_evidence` 안의 계산을 이 함수로 바꾼다:

```python
    tokens = set(tokenize(fact.evidence_text))
    if not fact.evidence_text.strip():
        if not fact.attributes:
            return []
        return [CheckResult(
            check="evidence_missing", severity=ERROR, fact_id=fact.fact_id,
            reason="값을 주장하면서 근거 원문(evidence_text)이 비어 있음",
            suggestion="입력에 실제로 있는 문구를 근거로 옮기세요.",
        )]
    if not tokens:
        return []
    coverage = evidence_coverage(fact.evidence_text, doc_tokens)
    if coverage >= EVIDENCE_MIN_COVERAGE:
        return []
```

(이하 `unseen` 계산부터는 그대로 둔다.)

- [ ] **Step 3-b: 회귀 확인 — 기존 validator 테스트가 계속 통과하는지**

Run: `python -m pytest tests/test_fact_validator.py -q`
Expected: PASS (변경 전과 같은 개수)

- [ ] **Step 3-c: 조립기 구현**

`contentcompare/fact/concept_assembler.py`:

```python
"""F7-4 그래프 조립 — 근거 검증 → ``same_as`` 병합 → ``differs_by`` 위반 강등.

**코드가 위상만 보고 집행한다.** ``axis`` 문자열("측정조건"/"산정방식"/…)은 LLM 이 문서
도메인에 맞게 짓는 이름이며 여기서 해석하지 않는다. 그래서 도메인이 바뀌어도 이 모듈은
그대로다(설계 §2.1).

근거 검증은 ``same_as`` 에만 적용한다. 차단(``differs_by``)은 근거가 없어도 손해가 없고,
연결만 검증을 요구하는 것이 비대칭 권한 원칙이다(설계 §2.3).
"""

from __future__ import annotations

import logging

from ..similarity.tokenize import tokenize
from .concept_models import (
    DIFFERS_BY,
    SAME_AS,
    UNKNOWN,
    ConceptEdge,
    ConceptGraph,
    ConceptMember,
    ConceptNode,
)
from .fact_models import Fact
from .validator import evidence_coverage

logger = logging.getLogger(__name__)

EVIDENCE_MIN_COVERAGE = 0.8
"""F4a 와 같은 기준. 인용 토큰의 80% 가 원문에 있어야 실재하는 근거로 본다."""


def verify_evidence(edge: ConceptEdge, left: Fact, right: Fact) -> bool:
    """``same_as`` 의 양쪽 인용이 각 fact 원문에 실재하는가."""
    for claim, fact in ((edge.left_text, left), (edge.right_text, right)):
        if not (claim or "").strip():
            return False
        source = set(tokenize(f"{fact.evidence_text} {fact.search_text} {fact.entity_name}"))
        if evidence_coverage(claim, source) < EVIDENCE_MIN_COVERAGE:
            return False
    return True


def assemble(
    members: list[ConceptMember],
    edges: list[ConceptEdge],
    facts: dict[str, Fact],
) -> ConceptGraph:
    """멤버와 엣지로 개념 그래프를 만든다.

    ``facts`` 는 ``FactRef.key -> Fact``. 엣지가 모르는 fact 를 가리키면 조용히 버린다
    (LLM 이 없는 id 를 지목하는 경우 — 현행 Comparator 의 ``_pick_target`` 과 같은 방어).
    """
    known = {(m.doc, m.fact_id) for m in members}
    valid = [e for e in edges
             if (e.left.doc, e.left.fact_id) in known
             and (e.right.doc, e.right.fact_id) in known]

    stats = {"same_as": 0, "differs_by": 0, "unknown": 0,
             "rejected_evidence": 0, "rejected_differs_by": 0}

    # 1) 근거 검증 — 통과 못 한 same_as 는 unknown 으로 강등한다(버리지 않는다).
    for edge in valid:
        if edge.relation != SAME_AS:
            continue
        if not verify_evidence(edge, facts[edge.left.key], facts[edge.right.key]):
            edge.relation = UNKNOWN
            edge.rejected_by = "evidence"
            stats["rejected_evidence"] += 1
            logger.info("[Concept] 근거 미실재로 연결 거부: %s", edge.pair_key)

    blockers = [e for e in valid if e.relation == DIFFERS_BY]
    stats["differs_by"] = len(blockers)

    # 2) same_as 병합 — 사람이 승격한 것을 먼저 적용한다.
    parent: dict[str, str] = {f"{m.doc}#{m.fact_id}": f"{m.doc}#{m.fact_id}" for m in members}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def blocked(a: str, b: str) -> bool:
        """a·b 를 합치면 differs_by 로 연결된 두 fact 가 한 개념이 되는가."""
        for d in blockers:
            if {find(d.left.key), find(d.right.key)} == {a, b}:
                return True
        return False

    same_edges = [e for e in valid if e.relation == SAME_AS]
    for edge in sorted(same_edges, key=lambda e: not e.promoted):
        a, b = find(edge.left.key), find(edge.right.key)
        if a == b:
            stats["same_as"] += 1
            continue
        if blocked(a, b):
            edge.relation = UNKNOWN
            edge.rejected_by = "differs_by"
            stats["rejected_differs_by"] += 1
            logger.info("[Concept] differs_by 제약으로 병합 거부: %s", edge.pair_key)
            continue
        parent[a] = b
        stats["same_as"] += 1

    stats["unknown"] = sum(1 for e in valid if e.relation == UNKNOWN)

    # 3) 컴포넌트 → 노드. members 순서를 따라 결정적으로 번호를 매긴다.
    groups: dict[str, list[ConceptMember]] = {}
    for m in members:
        groups.setdefault(find(f"{m.doc}#{m.fact_id}"), []).append(m)
    nodes = [
        ConceptNode(concept_id=f"c-{i:04d}", label=group[0].entity_name, members=group)
        for i, group in enumerate(groups.values(), start=1)
    ]
    return ConceptGraph(nodes=nodes, edges=valid, stats=stats)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_concept_assembler.py tests/test_fact_validator.py -q`
Expected: PASS (전부)

- [ ] **Step 5: 커밋**

```bash
git add contentcompare/fact/concept_assembler.py contentcompare/fact/validator.py tests/test_concept_assembler.py
git commit -m "$(cat <<'EOF'
feat(fact): F7 그래프 조립 — 근거 검증과 differs_by 병합 제약

코드는 위상만 본다. axis 문자열은 LLM 이 도메인에 맞게 짓는 이름이라 해석하지
않으므로, 도메인이 바뀌어도 이 모듈은 그대로다.

근거 검증은 same_as 에만 적용한다 — 차단은 근거가 없어도 손해가 없고 연결만
검증을 요구하는 것이 비대칭 권한 원칙이다. 검증에 실패한 연결은 버리지 않고
unknown 으로 강등해 사람이 볼 수 있게 남긴다.

validator 의 토큰 포함률 계산을 evidence_coverage 로 공개 추출해 재사용한다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01GJ9o7G2SMZKKQWFi4XZNZC
EOF
)"
```

---

### Task 4: 후보 쌍 생성 + 코드/온톨로지 확정

**Files:**
- Create: `contentcompare/fact/concept_builder.py`
- Test: `tests/test_concept_builder.py`

**Interfaces:**
- Consumes: `fact_matcher.FactMatcher`/`norm_name`, `fact_store.FactStore`/`DocFacts`, `ontology.Ontology`, `concept_models.*`
- Produces:
  - `CandidatePair` (dataclass): `.left_doc: str`, `.left: Fact`, `.right_doc: str`, `.right: Fact`, `.score: float`, `.exact: bool`
  - `candidate_pairs(store: FactStore, *, embedder=None, top_k=5, min_score=0.3) -> list[CandidatePair]`
  - `resolve_known(pairs: list[CandidatePair], ontology: Ontology) -> tuple[list[ConceptEdge], list[CandidatePair]]` — `(확정된 엣지, LLM 에 넘길 나머지)`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_concept_builder.py`:

```python
"""F7 후보 쌍 생성과 코드/온톨로지 확정 테스트 — 가짜 임베더, LLM 불필요."""

from contentcompare.fact.concept_builder import candidate_pairs, resolve_known
from contentcompare.fact.concept_models import BY_CODE, BY_ONTOLOGY, DIFFERS_BY, SAME_AS
from contentcompare.fact.fact_models import Fact, FactSet
from contentcompare.fact.fact_store import DocFacts, FactStore
from contentcompare.fact.ontology import Ontology, load_ontology


class _FakeEmbedder:
    """텍스트에 '온도'가 들어가면 서로 가깝게, 아니면 멀게 만드는 최소 임베더."""

    def embed(self, texts, kind="passage"):
        return [[1.0, 0.9] if "온도" in t else [1.0, 0.0] for t in texts]


def _fact(fact_id: str, name: str) -> Fact:
    return Fact(fact_id=fact_id, entity_name=name, search_text=name, evidence_text=name)


def _store(ref_names, target_names) -> FactStore:
    store = FactStore()
    store.add(DocFacts(
        doc_name="기준.xlsx",
        facts=FactSet(facts=[_fact(f"fact-row-{i}", n) for i, n in enumerate(ref_names, 1)]),
    ), is_reference=True)
    store.add(DocFacts(
        doc_name="규격서.docx",
        facts=FactSet(facts=[_fact(f"fact-word-{i}", n) for i, n in enumerate(target_names, 1)]),
    ))
    return store


def test_exact_name_pair_is_always_a_candidate():
    store = _store(["공칭용량"], ["공칭용량"])
    pairs = candidate_pairs(store, embedder=_FakeEmbedder())
    assert len(pairs) == 1 and pairs[0].exact is True


def test_spacing_difference_still_counts_as_exact():
    store = _store(["정격충전전압"], ["정격 충전 전압"])
    pairs = candidate_pairs(store, embedder=_FakeEmbedder())
    assert pairs[0].exact is True


def test_similar_names_become_candidates_for_review():
    """무관한 쌍도 후보로는 올라온다 — 판정은 개념 층이 한다."""
    store = _store(["1개월저장온도"], ["표준환경온도"])
    pairs = candidate_pairs(store, embedder=_FakeEmbedder(), min_score=0.3)
    assert len(pairs) == 1 and pairs[0].exact is False


def test_unrelated_pair_below_recall_min_is_dropped():
    store = _store(["1개월저장온도"], ["정격전압"])
    assert candidate_pairs(store, embedder=_FakeEmbedder(), min_score=0.99) == []


def test_pairs_are_generated_per_target_document():
    store = _store(["공칭용량"], ["공칭용량"])
    store.add(DocFacts(doc_name="발표.pptx", facts=FactSet(facts=[_fact("fact-ppt-1", "공칭용량")])))
    pairs = candidate_pairs(store, embedder=_FakeEmbedder())
    assert {p.right_doc for p in pairs} == {"규격서.docx", "발표.pptx"}


def test_works_without_embedder_via_bm25():
    """임베더가 없어도(오프라인) 후보 생성은 동작해야 한다."""
    store = _store(["공칭용량"], ["공칭용량"])
    assert candidate_pairs(store, embedder=None)


# --------------------------------------------------------------------- #
# 코드/온톨로지 확정
# --------------------------------------------------------------------- #
def test_exact_pair_is_confirmed_by_code_without_llm():
    store = _store(["공칭용량"], ["공칭용량"])
    edges, remaining = resolve_known(candidate_pairs(store, embedder=_FakeEmbedder()), Ontology())
    assert remaining == []
    assert edges[0].relation == SAME_AS and edges[0].decided_by == BY_CODE


def test_ontology_pair_skips_llm(tmp_path):
    p = tmp_path / "o.yaml"
    p.write_text('differs_by:\n  - names: ["1개월저장온도", "표준환경온도"]\n    axis: "측정조건"\n',
                 encoding="utf-8")
    store = _store(["1개월저장온도"], ["표준환경온도"])
    edges, remaining = resolve_known(
        candidate_pairs(store, embedder=_FakeEmbedder()), load_ontology(str(p))
    )
    assert remaining == []
    assert edges[0].relation == DIFFERS_BY
    assert edges[0].decided_by == BY_ONTOLOGY and edges[0].promoted is True
    assert edges[0].axis == "측정조건"


def test_same_name_pair_cannot_be_overridden_by_ontology(tmp_path):
    """**알려진 한계**: 온톨로지 키가 정규화 항목명이라, 이름이 같은 두 fact 를
    '사실은 다른 항목'이라고 선언할 방법이 없다. 이름이 같으면 코드가 잇는다.

    실측에서 이름 완전일치는 10/10 정확했으므로 지금은 감수한다. 실제로 문제가
    생기면 그때 항목명 자체를 구분하거나(문서 수정) 별도 예외 목록을 만든다 —
    관찰 전에 만들지 않는다.
    """
    p = tmp_path / "o.yaml"
    p.write_text('differs_by:\n  - names: ["공칭용량", "공칭 용량"]\n    axis: "대상"\n',
                 encoding="utf-8")
    store = _store(["공칭용량"], ["공칭용량"])
    edges, remaining = resolve_known(
        candidate_pairs(store, embedder=_FakeEmbedder()), load_ontology(str(p))
    )
    assert remaining == []
    assert edges[0].decided_by == BY_CODE  # 온톨로지 항목은 정규화하면 자기 자신이라 무시된다


def test_unknown_pair_is_left_for_llm():
    store = _store(["1개월저장온도"], ["표준환경온도"])
    edges, remaining = resolve_known(candidate_pairs(store, embedder=_FakeEmbedder()), Ontology())
    assert edges == [] and len(remaining) == 1
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `python -m pytest tests/test_concept_builder.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'contentcompare.fact.concept_builder'`

- [ ] **Step 3: 구현**

`contentcompare/fact/concept_builder.py`:

```python
"""F7-1~F7-3 — 후보 쌍 생성 → 코드/온톨로지 확정 → LLM 판정.

**유사도는 여기서만 쓰인다.** 판정이 아니라 "LLM 에게 검토시킬 쌍을 좁히는" 용도이므로
임계값이 틀려도 손해가 작다 — 낮게 잡으면 호출이 늘고, 높게 잡으면 후보가 안 만들어져
``missing`` 이 된다(설계 §2.4).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from .concept_models import (
    BY_CODE,
    BY_ONTOLOGY,
    SAME_AS,
    ConceptEdge,
    FactRef,
)
from .fact_matcher import EXACT, FactMatcher, norm_name
from .fact_models import Fact
from .fact_store import FactStore
from .ontology import Ontology

logger = logging.getLogger(__name__)


@dataclass
class CandidatePair:
    """LLM/코드가 관계를 정해야 할 fact 쌍 하나."""

    left_doc: str
    left: Fact
    right_doc: str
    right: Fact
    score: float = 0.0
    exact: bool = False
    """정규화 항목명이 완전히 같은가(코드가 바로 확정할 수 있는 신호)."""

    @property
    def left_ref(self) -> FactRef:
        return FactRef(self.left_doc, self.left.fact_id)

    @property
    def right_ref(self) -> FactRef:
        return FactRef(self.right_doc, self.right.fact_id)


def candidate_pairs(
    store: FactStore,
    *,
    embedder: Any = None,
    top_k: int = 5,
    min_score: float = 0.3,
) -> list[CandidatePair]:
    """기준 fact × 각 대상 문서에서 검토할 후보 쌍을 만든다(recall 전용)."""
    if not store.ready:
        return []
    ref_doc = store.reference
    pairs: list[CandidatePair] = []
    for target in store.targets:
        matcher = FactMatcher(
            target.facts.facts,
            embedder=embedder,
            top_k=top_k,
            min_score=min_score,
            review_score=min_score,  # F7 에서 needs_review 는 연결 주체로 대체된다
        )
        for ref_fact in ref_doc.facts.facts:
            for cand in matcher.search(ref_fact):
                pairs.append(CandidatePair(
                    left_doc=ref_doc.doc_name, left=ref_fact,
                    right_doc=target.doc_name, right=cand.fact,
                    score=cand.score, exact=(cand.method == EXACT),
                ))
    logger.info("[Concept] 후보 쌍 %d 건", len(pairs))
    return pairs


def resolve_known(
    pairs: list[CandidatePair], ontology: Ontology
) -> tuple[list[ConceptEdge], list[CandidatePair]]:
    """LLM 없이 정할 수 있는 것을 먼저 확정한다.

    우선순위는 **온톨로지(사람) > 정규화 이름 완전일치(코드)** 다. 사람이 "이 둘은
    다르다"고 확정했으면 이름이 같아도 잇지 않는다.
    """
    edges: list[ConceptEdge] = []
    remaining: list[CandidatePair] = []
    for pair in pairs:
        known = ontology.relation_for(pair.left.entity_name, pair.right.entity_name)
        if known is not None:
            relation, axis, reason = known
            edges.append(ConceptEdge(
                relation=relation, left=pair.left_ref, right=pair.right_ref,
                axis=axis, reason=reason, decided_by=BY_ONTOLOGY, promoted=True,
                recall_score=pair.score,
            ))
            continue
        if pair.exact:
            edges.append(ConceptEdge(
                relation=SAME_AS, left=pair.left_ref, right=pair.right_ref,
                left_text=pair.left.evidence_text, right_text=pair.right.evidence_text,
                reason=f"정규화 항목명이 동일합니다: {norm_name(pair.left.entity_name)}",
                decided_by=BY_CODE, recall_score=pair.score,
            ))
            continue
        remaining.append(pair)
    logger.info("[Concept] 코드/온톨로지 확정 %d 건, LLM 위임 %d 건", len(edges), len(remaining))
    return edges, remaining
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_concept_builder.py -q`
Expected: PASS (11 passed)

- [ ] **Step 5: 커밋**

```bash
git add contentcompare/fact/concept_builder.py tests/test_concept_builder.py
git commit -m "$(cat <<'EOF'
feat(fact): F7 후보 쌍 생성 + 코드/온톨로지 확정

유사도는 여기서만 쓴다 — 판정이 아니라 LLM 에게 검토시킬 쌍을 좁히는 용도이므로
임계값이 틀려도 손해가 작다.

확정 우선순위는 온톨로지(사람) > 정규화 이름 완전일치(코드)다. 사람이 다르다고
확정했으면 이름이 같아도 잇지 않는다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01GJ9o7G2SMZKKQWFi4XZNZC
EOF
)"
```

---

### Task 5: 개념 판정 프롬프트

**Files:**
- Modify: `contentcompare/fact/prompts.py` (파일 끝에 추가)
- Test: `tests/test_concept_prompts.py`

**Interfaces:**
- Consumes: `concept_builder.CandidatePair`
- Produces:
  - `prompts.CONCEPT_SYSTEM: str`
  - `prompts.build_concept_user(pairs: list[CandidatePair], *, knowledge: str = "", purpose: str = "", ontology_summary: str = "") -> str`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_concept_prompts.py`:

```python
"""F7 개념 판정 프롬프트 테스트 — 문자열 조립만."""

from contentcompare.fact.concept_builder import CandidatePair
from contentcompare.fact.fact_models import Fact
from contentcompare.fact.prompts import CONCEPT_SYSTEM, build_concept_user
from contentcompare.fact.record_models import Attribute


def _pair() -> CandidatePair:
    left = Fact(fact_id="fact-row-20", entity_name="1개월저장온도",
                attributes={"lower_limit": Attribute(value=-10.0, unit="")},
                evidence_text="-10.0, 35.0, 80.0")
    right = Fact(fact_id="fact-word-11", entity_name="표준환경온도",
                 attributes={"lower_limit": Attribute(value=21, unit="℃")},
                 evidence_text="표준환경온도, 21 ~ 29 (중심 25), ℃")
    return CandidatePair("기준.xlsx", left, "규격서.docx", right, score=0.61)


def test_system_prompt_forbids_value_based_reasoning():
    """값이 다른 것은 differs_by 의 근거가 아니다 — 이게 빠지면 개념 층이
    정상적인 불일치를 삼켜버린다(설계 §5)."""
    assert "값" in CONCEPT_SYSTEM
    assert "same_as" in CONCEPT_SYSTEM and "differs_by" in CONCEPT_SYSTEM
    assert "unknown" in CONCEPT_SYSTEM


def test_system_prompt_requires_quotes_for_same_as():
    assert "인용" in CONCEPT_SYSTEM


def test_system_prompt_does_not_fix_axis_vocabulary():
    """축 이름은 도메인마다 다르므로 목록을 고정하지 않는다."""
    assert "정해진 목록은 없" in CONCEPT_SYSTEM


def test_user_prompt_contains_both_facts_and_ids():
    text = build_concept_user([_pair()])
    for token in ("fact-row-20", "fact-word-11", "1개월저장온도", "표준환경온도"):
        assert token in text


def test_user_prompt_includes_units_and_evidence():
    text = build_concept_user([_pair()])
    assert "℃" in text and "표준환경온도, 21 ~ 29" in text


def test_user_prompt_includes_context_when_given():
    text = build_concept_user([_pair()], knowledge="용어: 셀=배터리",
                              purpose="배터리 셀 규격 정의",
                              ontology_summary="- 가 / 나 → same_as")
    assert "셀=배터리" in text and "배터리 셀 규격 정의" in text and "가 / 나" in text


def test_user_prompt_omits_empty_context_sections():
    text = build_concept_user([_pair()])
    assert "참고자료" not in text


def test_multiple_pairs_are_numbered():
    text = build_concept_user([_pair(), _pair()])
    assert "[쌍 1]" in text and "[쌍 2]" in text
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `python -m pytest tests/test_concept_prompts.py -q`
Expected: FAIL — `ImportError: cannot import name 'CONCEPT_SYSTEM'`

- [ ] **Step 3: 프롬프트 구현**

`contentcompare/fact/prompts.py` 끝에 추가 (`_render_fact` 는 파일에 이미 있다):

```python
# --------------------------------------------------------------------------- #
# F7 개념 판정
# --------------------------------------------------------------------------- #
CONCEPT_SYSTEM = """\
당신은 서로 다른 문서의 두 항목이 **같은 것을 가리키는지** 판정하는 검토자입니다.
값이 같은지는 다른 단계가 판단합니다. 당신은 "이 둘을 비교해도 되는가"만 답합니다.

판정 값:
- "same_as": 두 항목이 같은 대상에 대한 같은 주장이다. 표기·언어·단위가 달라도 됩니다.
- "differs_by": 서로 다른 항목이다. **무엇이 달라서 다른지 축(axis) 이름을 직접 지어**
  쓰세요(예: 측정조건, 기간, 물리량, 산정방식, 연결범위). 정해진 목록은 없습니다 —
  이 문서의 분야에 맞는 이름을 지으세요.
- "unknown": 판단이 서지 않는다.

원칙:
1. **값이 다른 것은 differs_by 의 근거가 아닙니다.** "21~29 와 -10~80 은 값이 다르니
   다른 항목"이라고 추론하지 마세요. 값의 차이는 우리가 찾으려는 결과이지 판단 재료가
   아닙니다. 항목이 **무엇에 대한 것인지**만 보세요.
2. "same_as" 를 쓰려면 양쪽에서 **원문을 그대로 인용**해야 합니다(left_text/right_text).
   인용할 원문이 없으면 same_as 를 쓸 수 없습니다.
3. 확신이 없으면 "unknown" 을 쓰세요. 틀린 연결은 사람을 잘못된 수정으로 유도하지만,
   놓친 연결은 검토 목록에 남아 확인됩니다.
4. 주어진 쌍마다 정확히 하나의 판정을 내고, 쌍의 id 를 그대로 옮기세요.

반드시 아래 JSON 만 출력하세요(설명·마크다운 금지):
{
  "pairs": [
    {
      "left_fact_id": "<주어진 id>",
      "right_fact_id": "<주어진 id>",
      "relation": "same_as|differs_by|unknown",
      "axis": "<differs_by 일 때 무엇이 다른지>",
      "left_text": "<same_as 일 때 왼쪽 원문 인용>",
      "right_text": "<same_as 일 때 오른쪽 원문 인용>",
      "reason": "<한국어 한 문장>"
    }
  ]
}"""


def build_concept_user(
    pairs: list,
    *,
    knowledge: str = "",
    purpose: str = "",
    ontology_summary: str = "",
) -> str:
    """개념 판정 프롬프트의 user 파트. ``pairs`` 는 ``CandidatePair`` 리스트."""
    blocks: list[str] = []
    if purpose:
        blocks.append(f"[문서 분야]\n{purpose}")
    if knowledge:
        blocks.append(f"[참고자료 — 도메인 지식]\n{knowledge}")
    if ontology_summary:
        blocks.append(f"[이미 확정된 관계 — 일관되게 판단하세요]\n{ontology_summary}")

    for i, pair in enumerate(pairs, start=1):
        blocks.append(
            f"[쌍 {i}]\n"
            f"left_fact_id: {pair.left.fact_id} (문서: {pair.left_doc})\n"
            f"{_render_fact(pair.left, prefix='  ')}\n"
            f"right_fact_id: {pair.right.fact_id} (문서: {pair.right_doc})\n"
            f"{_render_fact(pair.right, prefix='  ')}"
        )
    return "\n\n".join(blocks)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_concept_prompts.py -q`
Expected: PASS (8 passed)

- [ ] **Step 5: 커밋**

```bash
git add contentcompare/fact/prompts.py tests/test_concept_prompts.py
git commit -m "$(cat <<'EOF'
feat(fact): F7 개념 판정 프롬프트

원칙 1이 핵심이다 — 값이 다른 것은 differs_by 의 근거가 아니다. 이걸 빠뜨리면
"21~29 와 -10~80 은 값이 다르니 다른 항목"이라는 추론이 허용되어, 우리가
찾으려는 정상적인 불일치를 개념 층이 삼켜버린다.

축 이름 목록을 고정하지 않는다. 도메인마다 구분 축이 다르므로 LLM 이 짓고
코드는 해석하지 않는다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01GJ9o7G2SMZKKQWFi4XZNZC
EOF
)"
```

---

### Task 6: LLM 배치 판정 + 그래프 빌드 오케스트레이션

**Files:**
- Modify: `contentcompare/fact/concept_builder.py`
- Test: `tests/test_concept_builder_llm.py`

**Interfaces:**
- Consumes: `llm_stage.LlmRunner`/`LlmBudgetExceeded`, `prompts.CONCEPT_SYSTEM`/`build_concept_user`, `concept_assembler.assemble`
- Produces:
  - `judge_pairs(runner, pairs: list[CandidatePair], *, knowledge="", purpose="", ontology_summary="", batch_size=20) -> list[ConceptEdge]`
  - `build_concept_graph(store, *, embedder=None, runner=None, ontology=None, knowledge="", purpose="", top_k=5, min_score=0.3, batch_size=20) -> ConceptGraph`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_concept_builder_llm.py`:

```python
"""F7 LLM 배치 판정 + 그래프 빌드 테스트 — 가짜 chat/임베더."""

import json

import pytest

from contentcompare.fact.concept_builder import (
    build_concept_graph,
    candidate_pairs,
    judge_pairs,
)
from contentcompare.fact.concept_models import DIFFERS_BY, SAME_AS, UNKNOWN
from contentcompare.fact.fact_models import Fact, FactSet
from contentcompare.fact.fact_store import DocFacts, FactStore
from contentcompare.fact.llm_stage import LlmRunner
from contentcompare.fact.ontology import Ontology


class _ScriptedChat:
    """미리 정한 응답을 순서대로 돌려주는 chat. 호출 수를 센다."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.prompts = []

    def complete(self, system, user, *, temperature=0.0):
        self.calls += 1
        self.prompts.append(user)
        return self.responses.pop(0) if self.responses else "{}"


class _BoomChat:
    def complete(self, system, user, *, temperature=0.0):
        raise RuntimeError("네트워크 끊김")


class _FakeEmbedder:
    def embed(self, texts, kind="passage"):
        return [[1.0, 0.9] if "온도" in t else [1.0, 0.0] for t in texts]


def _fact(fact_id, name, evidence="") -> Fact:
    return Fact(fact_id=fact_id, entity_name=name, search_text=name,
                evidence_text=evidence or name)


def _store() -> FactStore:
    store = FactStore()
    store.add(DocFacts(doc_name="기준.xlsx", facts=FactSet(facts=[
        _fact("fact-row-20", "1개월저장온도", "-10.0, 35.0, 80.0"),
    ])), is_reference=True)
    store.add(DocFacts(doc_name="규격서.docx", facts=FactSet(facts=[
        _fact("fact-word-11", "표준환경온도", "표준환경온도, 21 ~ 29, ℃"),
    ])))
    return store


def _reply(**kw) -> str:
    base = {"left_fact_id": "fact-row-20", "right_fact_id": "fact-word-11",
            "relation": DIFFERS_BY, "axis": "측정조건", "reason": "저장 조건과 환경 조건"}
    base.update(kw)
    return json.dumps({"pairs": [base]}, ensure_ascii=False)


def test_llm_relation_becomes_edge():
    pairs = candidate_pairs(_store(), embedder=_FakeEmbedder())
    runner = LlmRunner(_ScriptedChat([_reply()]), max_calls=5)
    edges = judge_pairs(runner, pairs)
    assert len(edges) == 1
    assert edges[0].relation == DIFFERS_BY and edges[0].axis == "측정조건"


def test_unknown_fact_id_in_reply_is_dropped():
    """LLM 이 주어지지 않은 id 를 지목해도 후보를 벗어나지 않는다."""
    pairs = candidate_pairs(_store(), embedder=_FakeEmbedder())
    runner = LlmRunner(_ScriptedChat([_reply(right_fact_id="없는id")]), max_calls=5)
    edges = judge_pairs(runner, pairs)
    assert [e.relation for e in edges] == [UNKNOWN]


def test_missing_pair_in_reply_becomes_unknown():
    """응답이 일부 쌍을 빠뜨려도 그 쌍을 잃지 않는다."""
    pairs = candidate_pairs(_store(), embedder=_FakeEmbedder())
    runner = LlmRunner(_ScriptedChat(['{"pairs": []}']), max_calls=5)
    edges = judge_pairs(runner, pairs)
    assert len(edges) == 1 and edges[0].relation == UNKNOWN


def test_batching_splits_calls():
    store = _store()
    store.reference.facts.facts.extend([
        _fact("fact-row-21", "3개월저장온도"), _fact("fact-row-22", "1년저장온도")])
    pairs = candidate_pairs(store, embedder=_FakeEmbedder())
    chat = _ScriptedChat(['{"pairs": []}'] * 3)
    judge_pairs(LlmRunner(chat, max_calls=5), pairs, batch_size=1)
    assert chat.calls == 3


def test_budget_exceeded_leaves_rest_unknown():
    store = _store()
    store.reference.facts.facts.append(_fact("fact-row-21", "3개월저장온도"))
    pairs = candidate_pairs(store, embedder=_FakeEmbedder())
    runner = LlmRunner(_ScriptedChat(['{"pairs": []}']), max_calls=1)
    edges = judge_pairs(runner, pairs, batch_size=1)
    assert len(edges) == 2
    assert all(e.relation == UNKNOWN for e in edges)


def test_llm_failure_is_isolated_as_unknown():
    pairs = candidate_pairs(_store(), embedder=_FakeEmbedder())
    edges = judge_pairs(LlmRunner(_BoomChat(), max_calls=5), pairs)
    assert [e.relation for e in edges] == [UNKNOWN]


# --------------------------------------------------------------------- #
# 오케스트레이션
# --------------------------------------------------------------------- #
def test_build_graph_without_llm_still_links_exact_names():
    store = FactStore()
    store.add(DocFacts(doc_name="기준.xlsx",
                       facts=FactSet(facts=[_fact("fact-row-1", "공칭용량", "1150")])),
              is_reference=True)
    store.add(DocFacts(doc_name="규격서.docx",
                       facts=FactSet(facts=[_fact("fact-word-7", "공칭용량", "공칭용량 1150 mAh")])))
    graph = build_concept_graph(store, embedder=_FakeEmbedder(), runner=None)
    assert graph.node_id_of("기준.xlsx", "fact-row-1") == graph.node_id_of("규격서.docx", "fact-word-7")
    assert graph.stats["llm_calls"] == 0


def test_build_graph_does_not_link_different_concepts():
    """이 계획의 존재 이유 — 1개월저장온도와 표준환경온도는 이어지면 안 된다."""
    runner = LlmRunner(_ScriptedChat([_reply()]), max_calls=5)
    graph = build_concept_graph(_store(), embedder=_FakeEmbedder(), runner=runner)
    assert graph.partners("기준.xlsx", "fact-row-20", "규격서.docx") == []
    assert graph.stats["differs_by"] == 1


def test_build_graph_links_when_llm_says_same_with_quotes():
    reply = _reply(relation=SAME_AS, axis="", left_text="-10.0, 35.0, 80.0",
                   right_text="표준환경온도, 21 ~ 29, ℃")
    runner = LlmRunner(_ScriptedChat([reply]), max_calls=5)
    graph = build_concept_graph(_store(), embedder=_FakeEmbedder(), runner=runner)
    assert len(graph.partners("기준.xlsx", "fact-row-20", "규격서.docx")) == 1


def test_build_graph_rejects_same_as_without_real_quotes():
    reply = _reply(relation=SAME_AS, axis="", left_text="지어낸 근거",
                   right_text="이것도 지어냄")
    runner = LlmRunner(_ScriptedChat([reply]), max_calls=5)
    graph = build_concept_graph(_store(), embedder=_FakeEmbedder(), runner=runner)
    assert graph.partners("기준.xlsx", "fact-row-20", "규격서.docx") == []
    assert graph.stats["rejected_evidence"] == 1


def test_build_graph_stats_report_pair_sources():
    runner = LlmRunner(_ScriptedChat([_reply()]), max_calls=5)
    graph = build_concept_graph(_store(), embedder=_FakeEmbedder(), runner=runner)
    for key in ("pairs_considered", "pairs_from_ontology", "pairs_by_code",
                "pairs_by_llm", "llm_calls"):
        assert key in graph.stats


def test_empty_store_yields_empty_graph():
    graph = build_concept_graph(FactStore(), embedder=_FakeEmbedder(), runner=None)
    assert graph.nodes == [] and graph.edges == []
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `python -m pytest tests/test_concept_builder_llm.py -q`
Expected: FAIL — `ImportError: cannot import name 'build_concept_graph'`

- [ ] **Step 3: 구현 — `concept_builder.py` 에 추가**

먼저 import 를 보강한다(파일 상단):

```python
from .concept_assembler import assemble
from .concept_models import (
    BY_CODE,
    BY_LLM,
    BY_ONTOLOGY,
    RELATIONS,
    SAME_AS,
    UNKNOWN,
    ConceptEdge,
    ConceptMember,
    ConceptGraph,
    FactRef,
)
from .llm_stage import LlmBudgetExceeded
from .ontology import Ontology
from .prompts import CONCEPT_SYSTEM, build_concept_user
```

파일 끝에 추가:

```python
def judge_pairs(
    runner: Any,
    pairs: list[CandidatePair],
    *,
    knowledge: str = "",
    purpose: str = "",
    ontology_summary: str = "",
    batch_size: int = 20,
) -> list[ConceptEdge]:
    """남은 후보 쌍을 배치로 LLM 에 넘겨 관계를 받는다.

    실패·예산 초과·응답 누락은 전부 ``unknown`` 엣지로 남긴다. **쌍을 잃지 않는 것**이
    중요하다 — 판단 못 한 쌍은 리포트의 '검토 필요'로 사람에게 간다.
    """
    edges: list[ConceptEdge] = []
    for start in range(0, len(pairs), max(1, batch_size)):
        batch = pairs[start : start + max(1, batch_size)]
        edges.extend(_judge_batch(runner, batch, knowledge, purpose, ontology_summary))
    return edges


def _judge_batch(
    runner: Any,
    batch: list[CandidatePair],
    knowledge: str,
    purpose: str,
    ontology_summary: str,
) -> list[ConceptEdge]:
    by_ids: dict[tuple[str, str], CandidatePair] = {
        (p.left.fact_id, p.right.fact_id): p for p in batch
    }
    try:
        obj = runner.complete_json(
            CONCEPT_SYSTEM,
            build_concept_user(batch, knowledge=knowledge, purpose=purpose,
                               ontology_summary=ontology_summary),
        )
    except Exception as e:  # noqa: BLE001 — 배치 격리(LlmBudgetExceeded·파싱실패·네트워크)
        logger.warning("[Concept] 배치 판정 실패(%s) → 보류: %s", type(e).__name__, e)
        return [_unknown_edge(p, f"LLM 판정 실패({type(e).__name__})") for p in batch]

    decided: dict[tuple[str, str], ConceptEdge] = {}
    for item in (obj.get("pairs") or []):
        if not isinstance(item, dict):
            continue
        key = (str(item.get("left_fact_id") or ""), str(item.get("right_fact_id") or ""))
        pair = by_ids.get(key)
        if pair is None:
            logger.info("[Concept] 후보에 없는 id 지목 → 무시: %s", key)
            continue
        relation = str(item.get("relation") or UNKNOWN)
        if relation not in RELATIONS:
            relation = UNKNOWN
        decided[key] = ConceptEdge(
            relation=relation, left=pair.left_ref, right=pair.right_ref,
            axis=str(item.get("axis") or ""),
            left_text=str(item.get("left_text") or ""),
            right_text=str(item.get("right_text") or ""),
            reason=str(item.get("reason") or ""),
            decided_by=BY_LLM, recall_score=pair.score,
        )
    return [
        decided.get((p.left.fact_id, p.right.fact_id))
        or _unknown_edge(p, "LLM 응답에 이 쌍이 없었습니다")
        for p in batch
    ]


def _unknown_edge(pair: CandidatePair, reason: str) -> ConceptEdge:
    return ConceptEdge(
        relation=UNKNOWN, left=pair.left_ref, right=pair.right_ref,
        reason=reason, decided_by=BY_LLM, recall_score=pair.score,
    )


def build_concept_graph(
    store: FactStore,
    *,
    embedder: Any = None,
    runner: Any = None,
    ontology: Optional[Ontology] = None,
    knowledge: str = "",
    purpose: str = "",
    top_k: int = 5,
    min_score: float = 0.3,
    batch_size: int = 20,
) -> ConceptGraph:
    """F7 전체 — 후보 쌍 → 코드/온톨로지 확정 → LLM → 조립."""
    ontology = ontology or Ontology()
    pairs = candidate_pairs(store, embedder=embedder, top_k=top_k, min_score=min_score)
    known, remaining = resolve_known(pairs, ontology)

    llm_edges: list[ConceptEdge] = []
    if remaining and runner is not None:
        llm_edges = judge_pairs(
            runner, remaining, knowledge=knowledge, purpose=purpose,
            ontology_summary=ontology.summary(), batch_size=batch_size,
        )
    elif remaining:
        llm_edges = [_unknown_edge(p, "LLM 을 쓰지 않아 판정하지 않음") for p in remaining]

    members: list[ConceptMember] = []
    facts: dict[str, Fact] = {}
    for doc in ([store.reference] if store.reference else []) + list(store.targets):
        for fact in doc.facts.facts:
            members.append(ConceptMember(doc.doc_name, fact.fact_id, fact.entity_name))
            facts[FactRef(doc.doc_name, fact.fact_id).key] = fact

    graph = assemble(members, known + llm_edges, facts)
    graph.stats.update({
        "pairs_considered": len(pairs),
        "pairs_from_ontology": sum(1 for e in known if e.decided_by == BY_ONTOLOGY),
        "pairs_by_code": sum(1 for e in known if e.decided_by == BY_CODE),
        "pairs_by_llm": len(llm_edges),
        "llm_calls": getattr(runner, "calls", 0),
    })
    logger.info("[Concept] 그래프 완성 %s", graph.stats)
    return graph
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_concept_builder_llm.py -q`
Expected: PASS (13 passed)

- [ ] **Step 5: 커밋**

```bash
git add contentcompare/fact/concept_builder.py tests/test_concept_builder_llm.py
git commit -m "$(cat <<'EOF'
feat(fact): F7 LLM 배치 판정 + 개념 그래프 빌드

실패·예산 초과·응답 누락은 전부 unknown 엣지로 남긴다. 쌍을 잃지 않는 것이
중요하다 — 판단 못 한 쌍은 리포트의 '검토 필요'로 사람에게 간다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01GJ9o7G2SMZKKQWFi4XZNZC
EOF
)"
```

---

### Task 7: ConceptMatcher — F5 후보를 그래프에서 가져온다

**Files:**
- Modify: `contentcompare/fact/fact_matcher.py` (파일 끝에 추가)
- Test: `tests/test_concept_matcher.py`

**Interfaces:**
- Consumes: `concept_models.ConceptGraph`/`FactRef`/`BY_CODE`/`BY_ONTOLOGY`, 기존 `MatchCandidate`
- Produces:
  - `fact_matcher.CONCEPT = "concept"`
  - `ConceptMatcher(graph, reference_doc: str, target_doc: str, target_facts: list[Fact])` — `.search(ref: Fact) -> list[MatchCandidate]`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_concept_matcher.py`:

```python
"""F5 Matcher 의 개념 그래프 경로 테스트 — 유사도·임계값 없음."""

from contentcompare.fact.concept_models import (
    BY_CODE,
    BY_LLM,
    BY_ONTOLOGY,
    SAME_AS,
    ConceptEdge,
    ConceptGraph,
    ConceptMember,
    ConceptNode,
    FactRef,
)
from contentcompare.fact.fact_matcher import CONCEPT, ConceptMatcher
from contentcompare.fact.fact_models import Fact

REF = Fact(fact_id="fact-row-1", entity_name="공칭용량")
TGT = Fact(fact_id="fact-word-7", entity_name="공칭용량")
OTHER = Fact(fact_id="fact-word-11", entity_name="표준환경온도")


def _graph(decided_by=BY_CODE, promoted=False, linked=True) -> ConceptGraph:
    members = [ConceptMember("기준.xlsx", "fact-row-1", "공칭용량")]
    if linked:
        members.append(ConceptMember("규격서.docx", "fact-word-7", "공칭용량"))
        nodes = [ConceptNode("c-0001", "공칭용량", members)]
    else:
        nodes = [
            ConceptNode("c-0001", "공칭용량", members),
            ConceptNode("c-0002", "표준환경온도",
                        [ConceptMember("규격서.docx", "fact-word-11", "표준환경온도")]),
        ]
    edge = ConceptEdge(
        SAME_AS, FactRef("기준.xlsx", "fact-row-1"), FactRef("규격서.docx", "fact-word-7"),
        decided_by=decided_by, promoted=promoted, recall_score=0.83,
    )
    return ConceptGraph(nodes=nodes, edges=[edge])


def _matcher(graph, facts=None) -> ConceptMatcher:
    return ConceptMatcher(graph, "기준.xlsx", "규격서.docx", facts or [TGT, OTHER])


def test_linked_concept_yields_candidate():
    cands = _matcher(_graph()).search(REF)
    assert [c.fact.fact_id for c in cands] == ["fact-word-7"]
    assert cands[0].method == CONCEPT


def test_unlinked_concept_yields_nothing():
    """연결이 없으면 후보가 없다 → 상위 Comparator 가 missing 으로 판정한다."""
    assert _matcher(_graph(linked=False)).search(REF) == []


def test_code_decided_link_does_not_need_review():
    """정규화 이름 완전일치는 실측 10/10 이라 현행 F5 처럼 신뢰한다."""
    assert _matcher(_graph(decided_by=BY_CODE)).search(REF)[0].needs_review is False


def test_promoted_link_does_not_need_review():
    cands = _matcher(_graph(decided_by=BY_ONTOLOGY, promoted=True)).search(REF)
    assert cands[0].needs_review is False


def test_llm_decided_link_needs_review():
    """아직 아무도 확인하지 않은 연결 위에서 코드가 mismatch 를 단정하지 않게 한다."""
    assert _matcher(_graph(decided_by=BY_LLM)).search(REF)[0].needs_review is True


def test_recall_score_is_carried_for_diagnostics():
    assert _matcher(_graph()).search(REF)[0].score == 0.83


def test_member_missing_from_target_facts_is_skipped():
    """그래프에는 있는데 대상 fact 목록에 없으면(부분 재실행) 조용히 건너뛴다."""
    assert _matcher(_graph(), facts=[OTHER]).search(REF) == []
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `python -m pytest tests/test_concept_matcher.py -q`
Expected: FAIL — `ImportError: cannot import name 'CONCEPT'`

- [ ] **Step 3: 구현 — `fact_matcher.py` 끝에 추가**

상단 상수 근처에 `CONCEPT = "concept"` 를 추가하고(기존 `EXACT`/`EMBED` 옆), 파일 끝에:

```python
class ConceptMatcher:
    """개념 그래프에서 후보를 가져온다 — 유사도도, 임계값도 쓰지 않는다(F7).

    ``FactMatcher`` 는 F7 에서 **후보 쌍 생성(recall)** 전용으로 남고, 비교에 쓰는
    후보는 이 클래스가 만든다. 연결이 없으면 빈 리스트를 돌려주고, 그것이 곧
    ``missing`` 판정의 근거가 된다.
    """

    def __init__(
        self,
        graph: Any,
        reference_doc: str,
        target_doc: str,
        target_facts: list[Fact],
    ) -> None:
        self.graph = graph
        self.reference_doc = reference_doc
        self.target_doc = target_doc
        self._by_id = {f.fact_id: f for f in target_facts}

    def search(self, ref: Fact) -> list[MatchCandidate]:
        from .concept_models import BY_CODE, BY_ONTOLOGY, FactRef

        out: list[MatchCandidate] = []
        for member in self.graph.partners(self.reference_doc, ref.fact_id, self.target_doc):
            fact = self._by_id.get(member.fact_id)
            if fact is None:
                continue
            edge = self.graph.edge_of(
                FactRef(self.reference_doc, ref.fact_id),
                FactRef(self.target_doc, member.fact_id),
            )
            confirmed = bool(edge) and (
                edge.promoted or edge.decided_by in (BY_CODE, BY_ONTOLOGY)
            )
            out.append(MatchCandidate(
                fact=fact,
                score=edge.recall_score if edge else 0.0,
                method=CONCEPT,
                needs_review=not confirmed,
            ))
        return out
```

> 지연 import 를 쓰는 이유: `concept_models` → (없음), `concept_builder` → `fact_matcher` 방향 의존이 이미 있어 모듈 최상단에서 서로를 부르면 순환이 된다. 현행 `pipeline.py` 가 `report.fact_report` 를 지연 import 하는 것과 같은 이유다.

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_concept_matcher.py tests/test_fact_matcher.py -q`
Expected: PASS (기존 matcher 테스트도 그대로 통과)

- [ ] **Step 5: 커밋**

```bash
git add contentcompare/fact/fact_matcher.py tests/test_concept_matcher.py
git commit -m "$(cat <<'EOF'
feat(fact): ConceptMatcher — 비교 후보를 개념 그래프에서 가져온다

유사도도 임계값도 쓰지 않는다. 연결이 없으면 빈 리스트이고 그것이 missing
판정의 근거다. needs_review 는 연결의 확정 주체로 정한다 — 코드(이름 완전일치)와
사람(승격)이 확정한 것은 신뢰하고, LLM 이 이번에 만든 연결은 값 판정도 LLM 이
한 번 더 본다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01GJ9o7G2SMZKKQWFi4XZNZC
EOF
)"
```

---

### Task 8: 그래프 무결성 검증

**Files:**
- Modify: `contentcompare/fact/validator.py` (파일 끝에 추가)
- Test: `tests/test_concept_validator.py`

**Interfaces:**
- Consumes: `concept_models.ConceptGraph`, 기존 `CheckResult`/`ValidationReport`/`ERROR`/`WARN`
- Produces: `validator.validate_graph(graph: ConceptGraph) -> ValidationReport`
  - 검사 이름: `concept_evidence_missing`, `concept_merge_violation`, `concept_contradiction`, `concept_dangling_node`, `concept_unknown_pair`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_concept_validator.py`:

```python
"""F7 그래프 무결성 검증 테스트 — 코드가 위상만 본다."""

from contentcompare.fact.concept_models import (
    DIFFERS_BY,
    SAME_AS,
    UNKNOWN,
    ConceptEdge,
    ConceptGraph,
    ConceptMember,
    ConceptNode,
    FactRef,
)
from contentcompare.fact.validator import ERROR, WARN, validate_graph

A = FactRef("기준.xlsx", "fact-row-1")
B = FactRef("규격서.docx", "fact-word-7")


def _graph(edges, members=None) -> ConceptGraph:
    members = members or [
        ConceptMember("기준.xlsx", "fact-row-1", "공칭용량"),
        ConceptMember("규격서.docx", "fact-word-7", "공칭용량"),
    ]
    return ConceptGraph(nodes=[ConceptNode("c-0001", "공칭용량", members)], edges=edges)


def _checks(report, name):
    return [c for c in report.checks if c.check == name]


def test_clean_graph_has_no_findings():
    report = validate_graph(_graph([ConceptEdge(SAME_AS, A, B, left_text="x", right_text="y")]))
    assert report.checks == []


def test_rejected_evidence_is_error():
    edge = ConceptEdge(UNKNOWN, A, B, rejected_by="evidence")
    report = validate_graph(_graph([edge]))
    found = _checks(report, "concept_evidence_missing")
    assert len(found) == 1 and found[0].severity == ERROR


def test_merge_violation_is_error():
    edge = ConceptEdge(UNKNOWN, A, B, rejected_by="differs_by")
    assert _checks(validate_graph(_graph([edge])), "concept_merge_violation")


def test_contradicting_pair_is_error():
    edges = [ConceptEdge(SAME_AS, A, B, left_text="x", right_text="y"),
             ConceptEdge(DIFFERS_BY, A, B, axis="기간")]
    found = _checks(validate_graph(_graph(edges)), "concept_contradiction")
    assert len(found) == 1 and found[0].severity == ERROR


def test_dangling_node_reference_is_error():
    edge = ConceptEdge(SAME_AS, A, FactRef("규격서.docx", "없는id"),
                       left_text="x", right_text="y")
    assert _checks(validate_graph(_graph([edge])), "concept_dangling_node")


def test_unknown_pair_is_warn_for_human_review():
    edge = ConceptEdge(UNKNOWN, A, B, reason="LLM 이 판단하지 못함")
    found = _checks(validate_graph(_graph([edge])), "concept_unknown_pair")
    assert len(found) == 1 and found[0].severity == WARN


def test_rejected_edge_is_not_double_counted_as_unknown():
    """강등된 엣지는 그 사유로만 보고한다."""
    edge = ConceptEdge(UNKNOWN, A, B, rejected_by="evidence")
    report = validate_graph(_graph([edge]))
    assert _checks(report, "concept_unknown_pair") == []


def test_report_aggregates_by_check():
    edges = [ConceptEdge(UNKNOWN, A, B, rejected_by="evidence"),
             ConceptEdge(UNKNOWN, B, A, reason="보류")]
    data = validate_graph(_graph(edges)).to_dict()
    assert data["by_check"]["concept_evidence_missing"] == 1
    assert data["overall"]["error"] == 1
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `python -m pytest tests/test_concept_validator.py -q`
Expected: FAIL — `ImportError: cannot import name 'validate_graph'`

- [ ] **Step 3: 구현 — `validator.py` 끝에 추가**

```python
# --------------------------------------------------------------------------- #
# F7 개념 그래프 검증
# --------------------------------------------------------------------------- #
_REJECT_CHECKS = {
    "evidence": ("concept_evidence_missing", "인용한 근거가 원문에 없어 연결을 거부했습니다"),
    "differs_by": ("concept_merge_violation", "differs_by 제약을 위반해 병합을 거부했습니다"),
}


def validate_graph(graph: Any) -> ValidationReport:
    """개념 그래프의 무결성을 검사한다(LLM 미사용).

    코드는 **위상만** 본다 — ``axis`` 문자열의 의미는 검사하지 않는다(설계 §2.1).
    """
    from .concept_models import DIFFERS_BY, SAME_AS, UNKNOWN as REL_UNKNOWN

    report = ValidationReport(location="concept_graph", facts=len(graph.nodes))
    known = {(m.doc, m.fact_id) for n in graph.nodes for m in n.members}

    seen: dict[tuple[str, str], set[str]] = {}
    for edge in graph.edges:
        pair = edge.pair_key
        label = f"{pair[0]} ↔ {pair[1]}"

        for ref in (edge.left, edge.right):
            if (ref.doc, ref.fact_id) not in known:
                report.checks.append(CheckResult(
                    check="concept_dangling_node", severity=ERROR, fact_id=label,
                    reason=f"그래프에 없는 fact 를 가리킵니다: {ref.key}",
                    suggestion="엣지를 버리거나 해당 fact 를 멤버에 넣으세요.",
                ))

        if edge.rejected_by in _REJECT_CHECKS:
            check, reason = _REJECT_CHECKS[edge.rejected_by]
            report.checks.append(CheckResult(
                check=check, severity=ERROR, fact_id=label, reason=reason,
                suggestion="사람이 확인해 knowledge/ontology.yaml 로 승격하세요.",
            ))
        elif edge.relation == REL_UNKNOWN:
            report.checks.append(CheckResult(
                check="concept_unknown_pair", severity=WARN, fact_id=label,
                reason=edge.reason or "관계를 판정하지 못했습니다",
                suggestion="리포트의 '검토 필요' 목록에서 확인하세요.",
            ))

        seen.setdefault(pair, set()).add(edge.relation)

    for pair, relations in seen.items():
        if SAME_AS in relations and DIFFERS_BY in relations:
            report.checks.append(CheckResult(
                check="concept_contradiction", severity=ERROR,
                fact_id=f"{pair[0]} ↔ {pair[1]}",
                reason="같은 쌍에 same_as 와 differs_by 가 함께 있습니다",
                suggestion="knowledge/ontology.yaml 로 어느 쪽이 맞는지 확정하세요.",
            ))
    return report
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_concept_validator.py tests/test_fact_validator.py -q`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add contentcompare/fact/validator.py tests/test_concept_validator.py
git commit -m "$(cat <<'EOF'
feat(fact): F7 개념 그래프 무결성 검증

코드는 위상만 본다 — axis 문자열의 의미는 검사하지 않는다. 강등된 엣지는 그
사유(evidence/differs_by)로 보고하고, 판정 못 한 쌍은 warn 으로 남겨 사람의
검토 목록이 되게 한다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01GJ9o7G2SMZKKQWFi4XZNZC
EOF
)"
```

---

### Task 9: 설정 + 파이프라인 배선

**Files:**
- Modify: `contentcompare/config.py` (`FactConfig`, 260행 근처 F5 설정 아래)
- Modify: `config/config.example.yaml` (`fact:` 섹션 끝)
- Modify: `contentcompare/fact/pipeline.py` (`_compare_and_report`)
- Test: `tests/test_fact_pipeline_concept.py`

**Interfaces:**
- Consumes: `concept_builder.build_concept_graph`, `ontology.load_ontology`, `validator.validate_graph`, `fact_matcher.ConceptMatcher`
- Produces:
  - `FactConfig.use_concept_graph: bool = True`, `.concept_recall_top_k: int = 5`, `.concept_recall_min: float = 0.3`, `.concept_batch_pairs: int = 20`, `.max_llm_calls_per_concept: int = 30`, `.ontology_path: str = "knowledge/ontology.yaml"`
  - `FactRunResult.concept_graph: Optional[ConceptGraph] = None`
  - artifacts `concept_graph.json`, `concept_validation.json` (기준 문서 폴더)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_fact_pipeline_concept.py`:

```python
"""F7 파이프라인 배선 테스트 — 가짜 추출기/chat/임베더(COM·네트워크 불필요).

기존 tests/test_fact_pipeline_smoke.py 의 주입 패턴을 따른다.
"""

import json

from contentcompare.config import AppConfig, FactConfig
from contentcompare.fact.concept_models import DIFFERS_BY
from contentcompare.fact.fact_models import Fact, FactSet
from contentcompare.fact.fact_store import DocFacts, FactStore
from contentcompare.fact.pipeline import FactPipeline


class _ScriptedChat:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def complete(self, system, user, *, temperature=0.0):
        self.calls += 1
        return self.responses.pop(0) if self.responses else "{}"


class _FakeEmbedder:
    def embed(self, texts, kind="passage"):
        return [[1.0, 0.9] if "온도" in t else [1.0, 0.0] for t in texts]


def _fact(fact_id, name, evidence) -> Fact:
    return Fact(fact_id=fact_id, entity_name=name, search_text=name, evidence_text=evidence)


def _store() -> FactStore:
    store = FactStore()
    store.add(DocFacts(doc_name="기준.xlsx", facts=FactSet(facts=[
        _fact("fact-row-20", "1개월저장온도", "-10.0, 35.0, 80.0")])), is_reference=True)
    store.add(DocFacts(doc_name="규격서.docx", facts=FactSet(facts=[
        _fact("fact-word-11", "표준환경온도", "표준환경온도, 21 ~ 29, ℃")])))
    return store


def _config(tmp_path, **fact_kw) -> AppConfig:
    cfg = AppConfig()
    cfg.fact = FactConfig(artifacts_dir=str(tmp_path / "artifacts"), **fact_kw)
    return cfg


def _reply() -> str:
    return json.dumps({"pairs": [{
        "left_fact_id": "fact-row-20", "right_fact_id": "fact-word-11",
        "relation": DIFFERS_BY, "axis": "측정조건", "reason": "저장 조건과 환경 조건",
    }]}, ensure_ascii=False)


def test_concept_graph_prevents_comparison_of_different_concepts(tmp_path):
    """F7 의 존재 이유 — 비교 대상이 아닌 쌍은 mismatch 가 아니라 missing 이다."""
    cfg = _config(tmp_path, ontology_path=str(tmp_path / "없음.yaml"))
    pipe = FactPipeline(cfg, chat=_ScriptedChat([_reply()]), embedder=_FakeEmbedder())
    result = pipe._compare_from_store(_store(), "기준.xlsx", ["규격서.docx"])
    assert [c.result for c in result.comparisons] == ["missing"]


def test_concept_graph_artifact_is_saved(tmp_path):
    cfg = _config(tmp_path, ontology_path=str(tmp_path / "없음.yaml"))
    pipe = FactPipeline(cfg, chat=_ScriptedChat([_reply()]), embedder=_FakeEmbedder())
    pipe._compare_from_store(_store(), "기준.xlsx", ["규격서.docx"])
    saved = json.loads((tmp_path / "artifacts" / "기준_xlsx" / "concept_graph.json").read_text(encoding="utf-8"))
    assert saved["edges"][0]["relation"] == DIFFERS_BY
    assert (tmp_path / "artifacts" / "기준_xlsx" / "concept_validation.json").exists()


def test_promoted_ontology_skips_llm(tmp_path):
    onto = tmp_path / "o.yaml"
    onto.write_text('differs_by:\n  - names: ["1개월저장온도", "표준환경온도"]\n    axis: "측정조건"\n',
                    encoding="utf-8")
    cfg = _config(tmp_path, ontology_path=str(onto))
    chat = _ScriptedChat([])
    pipe = FactPipeline(cfg, chat=chat, embedder=_FakeEmbedder())
    result = pipe._compare_from_store(_store(), "기준.xlsx", ["규격서.docx"])
    assert chat.calls == 0
    assert [c.result for c in result.comparisons] == ["missing"]


def test_use_concept_graph_false_falls_back_to_similarity(tmp_path):
    """롤백 스위치 — 기존 유사도 매칭 경로가 그대로 동작한다."""
    cfg = _config(tmp_path, use_concept_graph=False, compare_use_llm=False,
                  match_min_score=0.5)
    pipe = FactPipeline(cfg, chat=_ScriptedChat([]), embedder=_FakeEmbedder())
    result = pipe._compare_from_store(_store(), "기준.xlsx", ["규격서.docx"])
    assert result.comparisons and result.comparisons[0].match_method == "embed"


def test_compare_stats_include_concept_counters(tmp_path):
    cfg = _config(tmp_path, ontology_path=str(tmp_path / "없음.yaml"))
    pipe = FactPipeline(cfg, chat=_ScriptedChat([_reply()]), embedder=_FakeEmbedder())
    result = pipe._compare_from_store(_store(), "기준.xlsx", ["규격서.docx"])
    assert result.compare_stats["concept"]["differs_by"] == 1
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `python -m pytest tests/test_fact_pipeline_concept.py -q`
Expected: FAIL — `TypeError: FactConfig.__init__() got an unexpected keyword argument 'use_concept_graph'`

- [ ] **Step 3-a: 설정 추가**

`contentcompare/config.py` 의 `FactConfig` 에서 `max_llm_calls_per_compare` 아래에 추가:

```python
    # --- F7 개념 그래프 ------------------------------------------------- #
    use_concept_graph: bool = True
    """False 면 F5 가 기존 유사도 매칭으로 동작한다(롤백 스위치)."""

    concept_recall_top_k: int = 5
    """기준 fact 당 개념 판정에 올릴 후보 수."""

    concept_recall_min: float = 0.3
    """후보 생성 최소 유사도. **판정이 아니라 계산량 제한**이라 느슨해도 안전하다.

    개념 그래프가 판정을 맡으므로 이 값이 틀려도 손해가 작다 — 낮으면 LLM 호출이 늘고,
    높으면 후보가 안 만들어져 ``missing`` 이 된다(설계 §2.4).
    """

    concept_batch_pairs: int = 20
    """한 LLM 호출당 판정할 쌍 수."""

    max_llm_calls_per_concept: int = 30
    """개념 단계 LLM 호출 예산(문서 처리·비교 예산과 별도)."""

    ontology_path: str = "knowledge/ontology.yaml"
    """사람이 승격한 개념 관계 파일. 없으면 빈 온톨로지로 시작한다."""
```

`config/config.example.yaml` 의 `fact:` 섹션 끝에 추가:

```yaml
  # --- 개념 그래프(F7) ---
  use_concept_graph: true          # false 면 예전 유사도 매칭으로 롤백
  concept_recall_top_k: 5          # 기준 fact 당 개념 판정에 올릴 후보 수
  concept_recall_min: 0.3          # 후보 생성 최소 유사도(판정 아님 — 계산량 제한)
  concept_batch_pairs: 20          # 한 LLM 호출당 판정할 쌍 수
  max_llm_calls_per_concept: 30    # 개념 단계 LLM 호출 예산
  ontology_path: knowledge/ontology.yaml   # 사람이 승격한 개념 관계
  # ※ use_concept_graph: true 면 match_min_score / match_review_score 는 사용되지 않는다.
```

- [ ] **Step 3-b: 파이프라인 배선**

`contentcompare/fact/pipeline.py` 의 `FactRunResult` 에 필드 추가:

```python
    concept_graph: Any = None
    """F7 개념 그래프(사용 안 하면 None)."""
```

`_compare_and_report` 를 얇은 래퍼로 바꾸고 테스트가 부를 수 있는 `_compare_from_store` 를 만든다. 기존 `_compare_and_report` 본문을 아래로 교체한다:

```python
    def _compare_and_report(
        self,
        store: FactStore,
        reference: str,
        targets: list[str],
        result: FactRunResult,
    ) -> None:
        merged = self._compare_from_store(store, reference, targets)
        result.comparisons = merged.comparisons
        result.compare_stats = merged.compare_stats
        result.markdown = merged.markdown
        result.concept_graph = merged.concept_graph

    def _compare_from_store(
        self, store: FactStore, reference: str, targets: list[str]
    ) -> FactRunResult:
        """fact 가 모인 상태에서 개념 그래프 → 비교 → 리포트까지 한다."""
        result = FactRunResult()
        if not store.ready:
            logger.warning("[Fact] 비교 생략 — 기준/대상 fact 가 부족합니다: %s", store.summary())
            return result

        graph = self._build_graph(store)
        result.concept_graph = graph

        runner = LlmRunner(
            self._chat_client(), max_calls=self.fact.max_llm_calls_per_compare
        ) if self.fact.compare_use_llm else None
        comparator = FactComparator(
            runner=runner,
            knowledge=load_knowledge(),
            use_llm=self.fact.compare_use_llm,
        )
        ref_doc = store.reference
        assert ref_doc is not None  # store.ready 가 보장

        for target in store.targets:
            matcher = self._matcher_for(graph, ref_doc, target)
            for ref_fact in ref_doc.facts.facts:
                result.comparisons.append(comparator.compare(
                    ref_fact,
                    matcher.search(ref_fact),
                    target,
                    ref_low_confidence=ref_doc.is_low_confidence(ref_fact),
                ))

        result.compare_stats = {
            "comparisons": len(result.comparisons),
            "decided_by_llm": sum(1 for c in result.comparisons if c.decided_by == "llm"),
            "llm_calls": comparator.llm_calls,
            "llm_failures": comparator.llm_failures,
            "concept": dict(graph.stats) if graph is not None else {},
        }
        self._save_comparison(ref_doc, result)
        from ..report.fact_report import render_fact_markdown

        result.markdown = render_fact_markdown(
            result.comparisons,
            reference_doc=reference,
            target_docs=targets,
            stats=result.compare_stats,
        )
        logger.info("[Fact] 비교 %s", result.compare_stats)
        return result

    def _build_graph(self, store: FactStore):
        """F7 개념 그래프를 만들고 artifacts 에 남긴다(끄면 None)."""
        if not self.fact.use_concept_graph:
            return None
        from .concept_builder import build_concept_graph
        from .ontology import load_ontology
        from .validator import validate_graph

        runner = LlmRunner(
            self._chat_client(), max_calls=self.fact.max_llm_calls_per_concept
        )
        graph = build_concept_graph(
            store,
            embedder=self._embed_client(),
            runner=runner,
            ontology=load_ontology(self.fact.ontology_path),
            knowledge=load_knowledge(),
            top_k=self.fact.concept_recall_top_k,
            min_score=self.fact.concept_recall_min,
            batch_size=self.fact.concept_batch_pairs,
        )
        ref_doc = store.reference
        if ref_doc is not None:
            artifacts = ArtifactStore(
                self.fact.artifacts_dir, ref_doc.doc_name,
                enabled=self.fact.save_artifacts, cache=False,
            )
            artifacts.save("concept_graph", graph.to_dict())
            artifacts.save("concept_validation", validate_graph(graph).to_dict())
        return graph

    def _matcher_for(self, graph, ref_doc: DocFacts, target: DocFacts):
        """개념 그래프가 있으면 그래프 조회, 없으면 기존 유사도 매칭."""
        if graph is not None:
            from .fact_matcher import ConceptMatcher

            return ConceptMatcher(graph, ref_doc.doc_name, target.doc_name,
                                  target.facts.facts)
        return FactMatcher(
            target.facts.facts,
            embedder=self._embed_client(),
            top_k=self.fact.match_top_k,
            min_score=self.fact.match_min_score,
            review_score=self.fact.match_review_score,
        )
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_fact_pipeline_concept.py tests/test_fact_pipeline_smoke.py -q`
Expected: PASS

- [ ] **Step 5: 전체 회귀 확인**

Run: `python -m pytest -q`
Expected: PASS (기존 363 + 신규 전부)

- [ ] **Step 6: 커밋**

```bash
git add contentcompare/config.py config/config.example.yaml contentcompare/fact/pipeline.py tests/test_fact_pipeline_concept.py
git commit -m "$(cat <<'EOF'
feat(fact): F7 개념 그래프를 비교 경로에 배선

use_concept_graph 로 켜고 끈다(롤백 스위치). 켜면 match_min_score/
match_review_score 는 사용되지 않는다 — 판정에서 유사도가 빠졌다는 뜻이다.

concept_recall_min 은 판정이 아니라 계산량 제한이라 느슨해도 안전하다.
concept_graph.json / concept_validation.json 을 기준 문서 폴더에 남긴다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01GJ9o7G2SMZKKQWFi4XZNZC
EOF
)"
```

---

### Task 10: 리포트 — 연결 근거와 검토 필요 목록

**Files:**
- Modify: `contentcompare/report/fact_report.py`
- Test: `tests/test_fact_report.py` (기존 파일에 추가)

**Interfaces:**
- Consumes: `FactComparison`(기존), `ConceptGraph`
- Produces: `render_fact_markdown(comparisons, *, reference_doc, target_docs, stats=None, graph=None) -> str` — `graph` 인자 추가(기본 `None` 이라 기존 호출부 호환)

- [ ] **Step 1: 실패하는 테스트 작성 — `tests/test_fact_report.py` 끝에 추가**

```python
# --------------------------------------------------------------------- #
# F7 개념 그래프 표시
# --------------------------------------------------------------------- #
from contentcompare.fact.concept_models import (  # noqa: E402
    BY_LLM,
    UNKNOWN as REL_UNKNOWN,
    ConceptEdge,
    ConceptGraph,
    ConceptMember,
    ConceptNode,
    FactRef,
)


def _unknown_graph() -> ConceptGraph:
    members = [ConceptMember("기준.xlsx", "fact-row-20", "1개월저장온도"),
               ConceptMember("규격서.docx", "fact-word-11", "표준환경온도")]
    edge = ConceptEdge(
        REL_UNKNOWN,
        FactRef("기준.xlsx", "fact-row-20"),
        FactRef("규격서.docx", "fact-word-11"),
        reason="LLM 이 판단하지 못했습니다", decided_by=BY_LLM,
    )
    return ConceptGraph(
        nodes=[ConceptNode("c-0001", "1개월저장온도", members[:1]),
               ConceptNode("c-0002", "표준환경온도", members[1:])],
        edges=[edge],
    )


def test_review_section_lists_unresolved_pairs():
    """판정 못 한 쌍은 사람에게 보여야 한다 — 그래야 승격으로 이어진다."""
    md = render_fact_markdown([], reference_doc="기준.xlsx", target_docs=["규격서.docx"],
                              graph=_unknown_graph())
    assert "검토 필요" in md
    assert "1개월저장온도" in md and "표준환경온도" in md
    assert "knowledge/ontology.yaml" in md


def test_no_review_section_when_graph_is_clean():
    graph = ConceptGraph(nodes=[], edges=[])
    md = render_fact_markdown([], reference_doc="기준.xlsx", target_docs=["규격서.docx"],
                              graph=graph)
    assert "검토 필요" not in md


def test_report_renders_without_graph():
    """기존 호출부(그래프 없음)가 그대로 동작해야 한다."""
    md = render_fact_markdown([], reference_doc="기준.xlsx", target_docs=["규격서.docx"])
    assert "기준.xlsx" in md
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `python -m pytest tests/test_fact_report.py -q`
Expected: FAIL — `TypeError: render_fact_markdown() got an unexpected keyword argument 'graph'`

- [ ] **Step 3: 구현**

`contentcompare/report/fact_report.py` 의 `render_fact_markdown`(29-49행)을 아래로 바꾼다 — 시그니처에 `graph` 를 더하고 조립 마지막에 섹션을 붙인다:

```python
def render_fact_markdown(
    comparisons: list[FactComparison],
    *,
    reference_doc: str,
    target_docs: list[str],
    stats: Optional[dict] = None,
    graph: Any = None,
) -> str:
    lines: list[str] = [
        "# 문서 비교 리포트 (fact 엔진)",
        "",
        f"- 생성 시각: {datetime.now():%Y-%m-%d %H:%M:%S}",
        f"- 기준 문서: `{reference_doc}`",
        f"- 대상 문서: {', '.join(f'`{d}`' for d in target_docs)}",
        "",
    ]
    lines += _overview(comparisons)
    lines += _summary_table(comparisons)
    lines += _details(comparisons)
    if stats:
        lines += _run_stats(stats)
    lines += _review_section(graph)
    return "\n".join(lines)
```

`Any` 를 `typing` import 에 추가한다(`from typing import Any, Iterable, Optional`). 그리고 파일 끝에 섹션 렌더러를 넣는다:

```python
def _review_section(graph: Any) -> list[str]:
    """관계를 판정하지 못한 쌍 — 사람이 확인해 온톨로지로 승격하면 영구히 해결된다."""
    from ..fact.concept_models import UNKNOWN as REL_UNKNOWN

    if graph is None:
        return []
    pending = [e for e in graph.edges if e.relation == REL_UNKNOWN]
    if not pending:
        return []

    labels = {(m.doc, m.fact_id): m.entity_name
              for n in graph.nodes for m in n.members}
    lines = [
        "",
        "## ⚠ 검토 필요 — 같은 항목인지 판정하지 못한 쌍",
        "",
        "확인 후 `knowledge/ontology.yaml` 에 `same_as` 또는 `differs_by` 로 적어두면",
        "다음 실행부터 이 쌍은 다시 묻지 않습니다.",
        "",
        "| 기준 항목 | 대상 항목 | 사유 |",
        "|---|---|---|",
    ]
    for edge in pending:
        left = labels.get((edge.left.doc, edge.left.fact_id), edge.left.fact_id)
        right = labels.get((edge.right.doc, edge.right.fact_id), edge.right.fact_id)
        lines.append(f"| {left} | {right} | {edge.reason or '판정 보류'} |")
    return lines
```

`pipeline.py` 의 `render_fact_markdown(...)` 호출에도 `graph=result.concept_graph` 를 넘긴다.

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_fact_report.py tests/test_fact_pipeline_concept.py -q`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add contentcompare/report/fact_report.py contentcompare/fact/pipeline.py tests/test_fact_report.py
git commit -m "$(cat <<'EOF'
feat(report): 판정 못 한 개념 쌍을 '검토 필요'로 리포트에 노출

이 목록이 승격의 입구다 — 사람이 확인해 ontology.yaml 에 적으면 그 쌍은 다음
실행부터 다시 묻지 않는다. 놓친 연결이 조용히 사라지지 않게 하는 장치다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01GJ9o7G2SMZKKQWFi4XZNZC
EOF
)"
```

---

### Task 11: 실측 회귀 고정 + 골든셋 채점

**Files:**
- Create: `tests/test_concept_regression.py`
- Create: `knowledge/ontology.yaml` (실측으로 확인된 관계 초기값)
- Modify: `docs/FACT_F7_DESIGN.md`(상태 갱신), `docs/FACT_PIPELINE_PLAN.md`(로드맵에 F7 추가)

**Interfaces:**
- Consumes: 앞의 모든 것
- Produces: 없음(검증·문서)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_concept_regression.py`:

```python
"""F7 실측 회귀 — 2026-08-03 라이브에서 실제로 잘못 이어졌던 쌍을 고정한다.

근거: docs/FACT_F3_5_LIVE_REPORT.md §9.4, docs/FACT_F7_DESIGN.md §1.
이 세 쌍은 임베딩 점수가 0.6084~0.6944 로 정답(0.7656)과 섞여 있어 임계값으로는
가를 수 없었다. 개념 층이 이것을 막는지 확인한다.
"""

import json

from contentcompare.fact.concept_builder import build_concept_graph
from contentcompare.fact.concept_models import DIFFERS_BY, SAME_AS
from contentcompare.fact.fact_models import Fact, FactSet
from contentcompare.fact.fact_store import DocFacts, FactStore
from contentcompare.fact.llm_stage import LlmRunner
from contentcompare.fact.ontology import load_ontology
from contentcompare.fact.record_models import Attribute

# 실측 오매칭 3쌍 + 정답 1쌍.
BAD_PAIRS = [
    ("1개월저장온도", "표준환경온도", "측정조건"),
    ("평가환경온도", "평가 환경 습도", "물리량"),
    ("충전환경온도", "정격 충전 전압", "물리량"),
]


class _RelationChat:
    """쌍마다 미리 정한 관계를 돌려주는 chat."""

    def __init__(self, relations):
        self.relations = relations
        self.calls = 0

    def complete(self, system, user, *, temperature=0.0):
        self.calls += 1
        pairs = []
        for (left_id, right_id), (relation, axis) in self.relations.items():
            if left_id in user and right_id in user:
                pairs.append({
                    "left_fact_id": left_id, "right_fact_id": right_id,
                    "relation": relation, "axis": axis, "reason": "테스트",
                    "left_text": "", "right_text": "",
                })
        return json.dumps({"pairs": pairs}, ensure_ascii=False)


class _FakeEmbedder:
    """모든 쌍을 후보로 만든다 — recall 이 넉넉해도 개념 층이 막는지 보려는 것."""

    def embed(self, texts, kind="passage"):
        return [[1.0, 0.0] for _ in texts]


class _GroupEmbedder:
    """의도한 3쌍만 후보가 되도록 그룹별 직교 벡터를 준다.

    모든 벡터를 같게 주면 3x3 전부가 후보가 되어, 온톨로지가 덮지 않는 6쌍이
    LLM 으로 가고 chat.calls == 0 이 성립하지 않는다 — 그러면 이 테스트가
    검증하려는 '온톨로지가 LLM 을 건너뛴다'를 증명하지 못한다.
    """

    def embed(self, texts, kind="passage"):
        out = []
        for t in texts:
            if "저장" in t or "표준" in t:
                out.append([1.0, 0.0, 0.0])
            elif "평가" in t or "습도" in t:
                out.append([0.0, 1.0, 0.0])
            else:  # 충전 / 전압
                out.append([0.0, 0.0, 1.0])
        return out


def _fact(fact_id, name, evidence) -> Fact:
    return Fact(fact_id=fact_id, entity_name=name, search_text=f"{name} {evidence}",
                evidence_text=evidence,
                attributes={"target_value": Attribute(value=1, unit="")})


def _store() -> FactStore:
    store = FactStore()
    store.add(DocFacts(doc_name="기준.xlsx", facts=FactSet(facts=[
        _fact("r1", "1개월저장온도", "-10.0, 35.0, 80.0"),
        _fact("r2", "평가환경온도", "21.0, 24.0, 28.0"),
        _fact("r3", "충전환경온도", "0.0, 25.0, 45.0"),
    ])), is_reference=True)
    store.add(DocFacts(doc_name="규격서.docx", facts=FactSet(facts=[
        _fact("w1", "표준환경온도", "표준환경온도, 21 ~ 29, ℃"),
        _fact("w2", "평가 환경 습도", "평가 환경 습도, 33 ~ 53, %RH"),
        _fact("w3", "정격 충전 전압", "정격 충전 전압, 4.55, V"),
    ])))
    return store


def test_measured_false_matches_are_not_linked():
    relations = {
        ("r1", "w1"): (DIFFERS_BY, "측정조건"),
        ("r2", "w2"): (DIFFERS_BY, "물리량"),
        ("r3", "w3"): (DIFFERS_BY, "물리량"),
    }
    runner = LlmRunner(_RelationChat(relations), max_calls=10)
    graph = build_concept_graph(_store(), embedder=_FakeEmbedder(), runner=runner)
    for ref_id in ("r1", "r2", "r3"):
        assert graph.partners("기준.xlsx", ref_id, "규격서.docx") == [], ref_id


def test_promoted_ontology_blocks_them_without_llm(tmp_path):
    """온톨로지에 승격하면 LLM 없이도 막힌다 — 재현성 장치."""
    lines = ["differs_by:"]
    for left, right, axis in BAD_PAIRS:
        lines.append(f'  - names: ["{left}", "{right}"]\n    axis: "{axis}"')
    path = tmp_path / "ontology.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    chat = _RelationChat({})
    graph = build_concept_graph(
        _store(), embedder=_GroupEmbedder(), runner=LlmRunner(chat, max_calls=10),
        ontology=load_ontology(str(path)),
    )
    assert chat.calls == 0
    assert graph.stats["pairs_from_ontology"] == 3
    for ref_id in ("r1", "r2", "r3"):
        assert graph.partners("기준.xlsx", ref_id, "규격서.docx") == []


def test_true_synonym_is_linked_when_promoted(tmp_path):
    """고객 표준 버전 ↔ 문서 기준 규격 — 슬롯으로는 못 잇는 진짜 동의어."""
    path = tmp_path / "ontology.yaml"
    path.write_text('same_as:\n  - names: ["고객 표준 버전", "문서 기준 규격"]\n',
                    encoding="utf-8")
    store = FactStore()
    store.add(DocFacts(doc_name="기준.xlsx", facts=FactSet(facts=[
        _fact("r9", "고객 표준 버전", "배터리승인규격 ver 4.7")])), is_reference=True)
    store.add(DocFacts(doc_name="규격서.docx", facts=FactSet(facts=[
        _fact("w9", "문서 기준 규격", "본 규격은 배터리승인규격 ver 4.7 을 따른다")])))

    graph = build_concept_graph(store, embedder=_FakeEmbedder(), runner=None,
                                ontology=load_ontology(str(path)))
    partners = graph.partners("기준.xlsx", "r9", "규격서.docx")
    assert [m.fact_id for m in partners] == ["w9"]
    assert graph.edges[0].relation == SAME_AS
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `python -m pytest tests/test_concept_regression.py -q`
Expected: 앞 Task 들이 끝났다면 PASS. 실패하면 그 Task 로 돌아간다 — 이 테스트는 새 코드를 요구하지 않고 **앞선 구현이 실제 실패 사례를 막는지** 확인하는 것이다.

- [ ] **Step 3: 실측으로 확인된 관계를 온톨로지 초기값으로 남긴다**

`knowledge/ontology.yaml` 생성:

```yaml
# 사람이 확정한 개념 관계. 실행이 만든 artifacts/<기준문서>/concept_graph.json 을
# 검토해 확실한 것만 여기로 옮긴다. 여기 있는 쌍은 다음 실행부터 LLM 에 묻지 않는다.
#
# 키는 항목명이며 공백·기호 차이는 무시된다("고객 표준 버전" == "고객표준버전").

same_as:
  - names: ["고객 표준 버전", "문서 기준 규격"]
    reason: "둘 다 SEC Req. ver.4.7 을 가리킨다 (2026-08-03 실측 확인)"

differs_by:
  - names: ["1개월저장온도", "표준환경온도"]
    axis: "측정조건"
    reason: "저장 조건과 상시 환경 조건은 다른 규격"
  - names: ["평가환경온도", "평가 환경 습도"]
    axis: "물리량"
    reason: "온도와 습도"
  - names: ["충전환경온도", "정격 충전 전압"]
    axis: "물리량"
    reason: "온도와 전압"
```

- [ ] **Step 4: 라이브 골든셋 채점 (Ollama + Office 필요 — 없으면 이 단계만 건너뛰고 기록)**

```bash
python -m pytest -q
Remove-Item -Recurse -Force artifacts\*
python scripts/compare_engines.py --config config/config.yaml \
  --reference samples/자표준문서.xlsx \
  --targets samples/자표준_규격서.docx samples/자표준_발표.pptx \
  --golden golden/자표준_골든셋.jsonl --engines fact
```

확인할 것(설계 §11 DoD):
1. 정확도가 **17/19 이상**인가.
2. `artifacts/자표준문서_xlsx/comparison_result.json` 에서 **비교 대상이 아닌 쌍이 `match`/`mismatch` 로 보고된 건이 0건**인가.
3. `concept_recall_min` 을 0.3 / 0.45 / 0.6 으로 바꿔 세 번 돌렸을 때 **채점 결과가 같은가**.
4. 두 번째 실행에서 개념 단계 `llm_calls` 가 0~1 인가(온톨로지 승격 효과).

- [ ] **Step 5: 문서 갱신**

- `docs/FACT_F7_DESIGN.md` 머리말 상태를 `설계` → `구현 완료(YYYY-MM-DD)` 로 바꾸고, §11 DoD 옆에 실측값을 적는다.
- `docs/FACT_PIPELINE_PLAN.md` §9 로드맵에 `Phase F7 — 개념 그래프 ✅ 완료` 를 추가하고, 의존 순서 줄을 `F0 → … → F6 → F7 → (F4b 보류)` 로 고친다.
- 라이브 채점을 돌렸다면 `docs/FACT_F3_5_LIVE_REPORT.md` 에 §10 을 추가해 F7 전후 수치를 남긴다. §9.4 에 적힌 위험(경계 점수 오매칭)이 해소됐는지 명시한다.

- [ ] **Step 6: 커밋**

```bash
git add tests/test_concept_regression.py knowledge/ontology.yaml docs/
git commit -m "$(cat <<'EOF'
test(fact): F7 실측 회귀 고정 + 온톨로지 초기값

2026-08-03 라이브에서 실제로 잘못 이어졌던 3쌍(임베딩 0.6084~0.6944 로 정답
0.7656 과 섞여 임계값으로는 가를 수 없었던 것들)을 테스트로 고정한다.

knowledge/ontology.yaml 에 그 3쌍과 진짜 동의어 1쌍을 승격해 둔다 — 슬롯
분해로는 이을 수 없는 '고객 표준 버전 ↔ 문서 기준 규격' 이 사람 승격이
필요한 이유의 실례다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01GJ9o7G2SMZKKQWFi4XZNZC
EOF
)"
```

---

## 실행 순서와 의존성

```
Task 1 (모델)
  ├→ Task 2 (온톨로지)
  ├→ Task 3 (조립) ────┐
  └→ Task 4 (후보) ────┼→ Task 6 (LLM+빌드) → Task 9 (배선) → Task 10 (리포트) → Task 11 (회귀)
       Task 5 (프롬프트) ┘                        ↑
       Task 7 (matcher) ───────────────────────────┤
       Task 8 (검증) ──────────────────────────────┘
```

Task 2·3·4·5 는 서로 독립이라 순서를 바꿔도 된다. Task 6 은 3·4·5 가 끝나야 한다.
Task 9 는 6·7·8 이 모두 끝나야 한다.

## 되돌리기

`use_concept_graph: false` 로 두면 F5 가 예전 유사도 경로로 동작한다. Task 9 의
`_matcher_for` 가 그 분기점이며, Task 9 의 테스트가 이 경로를 지킨다.

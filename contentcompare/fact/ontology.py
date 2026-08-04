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
        # 정상 경로지만 반드시 남긴다 — 경로 오타나 CWD 불일치로 재현성 장치가
        # 소리 없이 사라지면 "왜 승격이 안 먹지"를 추적할 단서가 없다.
        logger.info("[Ontology] %s 가 없어 빈 온톨로지로 시작합니다(cwd: %s)",
                    path, os.getcwd())
        return onto
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as e:  # 손으로 쓰는 파일이라 깨질 수 있다
        logger.warning("[Ontology] %s 를 읽지 못했습니다: %s", path, e)
        return onto

    # YAML 이 파싱은 되지만 상위가 mapping 이 아닌 경우(리스트, scalar) 처리
    if not isinstance(data, dict):
        logger.warning("[Ontology] %s 상위가 mapping 이 아닙니다(type: %s)", path, type(data).__name__)
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

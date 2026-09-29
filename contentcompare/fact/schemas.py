"""단계 이름 → 와이어 JSON Schema. **pydantic 이 없어도 import 된다.**

이 경계층이 따로 있는 이유는 **파이썬 환경이 둘**이기 때문이다. 운영(anaconda)에는
langchain 을 통해 pydantic 이 딸려 오지만 개발용 ``.venv`` 에는 코어 의존성
(``pyyaml``/``requests``)뿐이고, **테스트가 양쪽 모두에서 돈다.** 단계 모듈(``profiler``
등)이 :mod:`.wire_models` 를 직접 import 하면 ``.venv`` 에서 수집조차 안 된다.

그래서 pydantic import 를 **함수 안**으로 밀어 넣고, 없으면 ``None`` 을 돌려준다.
``None`` 은 "구조화 출력 끔"이고 그건 오늘과 **완전히 같은 동작**이라, 폴백 경로가 사고로
썩지 않고 매일 테스트된다 — 폴백을 살려 두는 가장 싼 방법이다.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Optional

from ..llm.structured import strict_schema

logger = logging.getLogger(__name__)

#: 단계 이름. :data:`~contentcompare.fact.wire_models.MODEL_FOR` 의 키와 같아야 하며
#: ``test_fact_wire_models.py`` 가 그 일치를 고정한다.
STAGES = ("profiler", "schema", "record", "fact", "concept", "compare")


@lru_cache(maxsize=None)
def schema_for(stage: str) -> Optional[dict]:
    """단계의 strict JSON Schema. pydantic 이 없거나 단계가 미등록이면 ``None``.

    ⚠️ **돌려주는 dict 를 수정하지 말 것.** ``lru_cache`` 라 모든 호출이 같은 객체를 본다.
    캐시하는 이유는 ``model_json_schema()`` + 전체 순회가 호출당 수백 마이크로초인데 fact
    엔진이 실행 한 번에 수백 회를 부르기 때문이다. 소비자(백엔드의 ``bind``)는 읽기만 한다.

    스키마가 strict 규격을 어기면 :func:`~contentcompare.llm.structured.strict_schema` 가
    ``ValueError`` 를 올린다. **여기서 삼키지 않는다** — 그건 와이어 모델의 결함이고,
    조용히 ``None`` 으로 떨어뜨리면 "구조화 출력을 켰는데 왜 안 걸리지"가 된다. pydantic
    부재(환경 문제)와 스키마 결함(코드 문제)은 조치가 달라서 갈라 두어야 한다.
    """
    try:
        from . import wire_models  # noqa: WPS433 — 지연 import (모듈 독스트링 참고)
    except ImportError as exc:  # pragma: no cover - 환경 의존
        logger.info("pydantic 이 없어 구조화 출력을 끕니다: %s", exc)
        return None
    model = wire_models.MODEL_FOR.get(stage)
    if model is None:
        logger.warning("등록되지 않은 단계라 구조화 출력을 건너뜁니다: %r", stage)
        return None
    return strict_schema(model, name=stage)

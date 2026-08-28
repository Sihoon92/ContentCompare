"""구조화 출력(structured output) — JSON Schema 를 서버에 강제로 넘기는 배선.

프롬프트로 "JSON 만 출력하세요"라고 **부탁**하는 것과, 서버가 문법적으로 그것만 만들 수
있게 **강제**하는 것은 다르다. 후자를 쓰면 파싱 실패로 인한 재시도(호출 예산의 순손실)가
사라진다. 이 모듈이 하는 일은 둘이다:

1. :func:`strict_schema` — pydantic 이 만든 JSON Schema 를 **OpenAI strict 규격**으로
   조인다. ``model_json_schema()`` 는 strict 요구를 자동으로 만족시키지 않는다.
2. :func:`response_format` — 조인 스키마를 백엔드가 보낼 봉투에 담는다.

⚠️ **이 모듈은 pydantic 을 import 하지 않는다.** ``model_json_schema()`` 메서드가 있으면
그냥 부르는 덕 타이핑이다. 이 저장소는 파이썬 환경이 둘이고(운영 anaconda 에는 langchain 을
통해 pydantic 이 딸려 오지만 개발용 ``.venv`` 에는 코어 의존성뿐이다) **테스트가 양쪽에서
돈다** — 여기서 pydantic 을 import 하면 ``.venv`` 에서 수집조차 안 된다. 덤으로 단위
테스트가 생 dict 를 먹여도 되므로 검증이 오히려 쉽다.

**왜 여기서 죽이는가.** ``dict[str, X]`` 같은 자유 키 스키마를 그대로 보내면 게이트웨이가
런타임에 400 을 준다. 그 400 은 파이프라인 한복판에서, 문서 몇 개를 이미 처리한 뒤에,
"Invalid schema" 한 줄로 온다. :func:`strict_schema` 가 ``ValueError`` 를 올리면 같은
결함이 **단위 테스트 실패**로 바뀐다. 이 모듈의 값어치는 대부분 거기에 있다.
"""

from __future__ import annotations

import json
from typing import Any, Optional

#: 설정 ``llm.structured_output`` 이 받는 값.
MODES = ("auto", "json_schema", "json_object", "off")

#: OpenAI strict 가 받지 않는 JSON Schema 키워드. pydantic 이 넣는 것들이라 걷어낸다.
#: **좁게 유지할 것** — 모르는 키워드를 통째로 지우면 ``enum``/``const`` 처럼 *의미가 있는*
#: 제약까지 사라져 스키마가 조용히 헐거워진다.
_UNSUPPORTED_KEYWORDS = frozenset({
    "default", "format", "pattern", "examples", "example",
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
    "minLength", "maxLength", "minItems", "maxItems", "uniqueItems",
    "multipleOf", "patternProperties", "propertyNames", "contains",
})

#: 자식 스키마를 **이름→스키마 map** 으로 담는 키.
_NAMED_CHILDREN = ("properties", "$defs", "definitions")
#: 자식 스키마를 값으로(또는 리스트로) 담는 키.
_ANON_CHILDREN = ("items", "anyOf", "oneOf", "allOf", "prefixItems", "not")

#: "타입이 정해진 노드"로 인정할 키. 하나도 없으면 ``Any`` 라서 서버가 거절한다.
_TYPING_KEYS = ("type", "$ref", "anyOf", "oneOf", "allOf", "enum", "const")

#: OpenAI 가 공표한 strict 한도(보수적으로 낮은 쪽). 넘으면 **빌드 타임에** 알린다 —
#: 스키마가 커지는 것은 모델을 늘리다가 서서히 일어나는 일이라, 넘는 순간을 못 보면
#: 그날의 커밋이 아니라 몇 주 전 커밋을 의심하게 된다.
MAX_DEPTH = 5
MAX_PROPERTIES = 100

_FREE_KEY_MSG = (
    "strict 스키마는 키 이름을 미리 모르는 object 를 표현할 수 없습니다 (경로: {path}). "
    "dict[str, X] 대신 list[{{name, value}}] 로 바꾸고, map 으로 되돌리는 것은 parse_* "
    "함수 한 곳에서 하세요 (예: record_models.parse_attributes)."
)
_UNTYPED_MSG = (
    "strict 스키마는 타입 없는 노드를 받지 않습니다 (경로: {path}). "
    "Any 대신 실제로 올 수 있는 타입의 Union 을 쓰세요."
)


def normalize_mode(value: Any) -> str:
    """설정값을 검증해 소문자로. 모르는 값이면 :class:`ValueError`.

    조용히 기본값으로 떨어뜨리지 않는 이유는 :func:`~contentcompare.llm.factory._make` 가
    알 수 없는 backend 에 ``ValueError`` 를 올리는 것과 같다 — 오타(``json-schema``)를
    조용히 ``auto`` 로 바꾸면 "켰다고 생각했는데 안 켜진" 상태가 되고, 그건 안 켠 것보다
    나쁘다(안 켠 것은 최소한 본인이 안다).
    """
    mode = str(value or "auto").strip().lower()
    if mode not in MODES:
        raise ValueError(
            f"알 수 없는 llm.structured_output: {value!r} ({' | '.join(MODES)})"
        )
    return mode


def strict_schema(source: Any, *, name: str) -> dict:
    """pydantic 모델(또는 생 JSON Schema dict) → OpenAI strict 규격 스키마.

    ``source`` 는 ``model_json_schema()`` 를 가진 것이면 무엇이든 된다(덕 타이핑 — 이
    모듈이 pydantic 을 import 하지 않는 이유).

    조이는 것 셋:

    1. 모든 object 에 ``additionalProperties: false``
    2. **모든 속성을** ``required`` — pydantic 은 기본값이 있으면 빼 버리는데 strict 에는
       "선택 필드" 개념이 없다. 정말 비어도 되는 값은 ``Optional[...]`` 로 **null 을 명시**
       하게 한다("키가 없다"와 "값이 null 이다"를 갈라 두는 것이 이 규격의 요점이다).
    3. 미지원 키워드 제거(:data:`_UNSUPPORTED_KEYWORDS`)

    ``$defs``/``$ref`` 는 **평탄화하지 않는다.** strict 는 같은 문서 안의 ``$ref`` 를 받고,
    평탄화하면 공용 모델이 여러 번 복제되어 매 요청 토큰을 더 쓰며 재귀 스키마는 아예
    표현이 불가능해진다. 대신 ``$defs`` **안까지 걸어 들어가** 같은 규칙을 적용한다 —
    ⚠️ **여기를 빼먹는 것이 가장 흔한 실패다**(루트만 조이면 루트는 통과하고
    ``$defs`` 안의 공용 모델에서 400 이 난다).
    """
    raw = source.model_json_schema() if hasattr(source, "model_json_schema") else source
    # 깊은 복사. pydantic 은 ``model_json_schema()`` 결과를 클래스에 캐시할 수 있어
    # 제자리에서 고치면 **모델 자체를 오염**시킨다. json 왕복은 덤으로 "직렬화 가능한가"를
    # 여기서 확인해 준다(호출 직전 SDK 안에서 터지는 것보다 낫다).
    node: dict = json.loads(json.dumps(raw))
    _tighten(node, path="$")
    _audit(node)
    # 봉투의 ``name`` 과 스키마의 ``title`` 을 같은 값으로 둔다 — 타임라인은 title 로 어느
    # 단계인지 표시하고 서버 오류 메시지에는 name 이 실린다. 둘이 다르면 두 기록을 손으로
    # 이어 붙여야 한다.
    node["title"] = name
    return node


def response_format(schema: Optional[dict], *, mode: str) -> Optional[dict]:
    """스키마 + 모드 → OpenAI ``response_format`` 봉투. 요구하지 않을 때는 ``None``.

    ``None`` 은 "이 키를 아예 보내지 않는다"는 뜻이지 "끔이라고 명시한다"가 아니다 —
    :meth:`~contentcompare.llm.langchain_backend.LangChainBackend._invoke` 가 그 차이를
    지킨다(``response_format=None`` 을 실어 보내는 SDK 버전이 있어서, 그러면 '끔'이
    '끔이라고 명시'로 바뀌어 오늘과 다른 요청이 된다).

    ``json_object`` 는 스키마 없이 "JSON 이기만 하면 된다"를 요구한다. 게이트웨이가 JSON
    모드는 되는데 우리 스키마는 거절할 때의 중간 단계다. ⚠️ OpenAI 규격상 이 모드는
    **프롬프트 어딘가에 'json' 이라는 낱말이 있어야** 한다.
    """
    if mode == "off":
        return None
    if mode == "json_object":
        return {"type": "json_object"}
    if schema is None:
        # auto/json_schema 인데 이 단계에 스키마가 없다(pydantic 미설치 등).
        # ``json_object`` 로 **승격하지 않는다** — 요청한 적 없는 제약을 몰래 거는 셈이고,
        # 그러면 "왜 갑자기 응답이 달라졌나"의 원인이 설정 어디에도 안 보인다.
        return None
    return {
        "type": "json_schema",
        "json_schema": {
            "name": str(schema.get("title") or "response"),
            "strict": True,
            "schema": schema,
        },
    }


def looks_like_schema_rejection(exc: BaseException) -> bool:
    """이 예외가 "스키마 때문에 거절당했다"인가.

    판정을 **좁게** 두는 것이 핵심이다. 넓히면 프롬프트 초과·인증 실패 같은 진짜 오류에도
    "스키마 빼고 다시"를 시도해 **모든 실패의 비용이 두 배**가 된다. 그래서 상태코드
    (400/422)와 본문 마커를 **둘 다** 본다 —
    :func:`~contentcompare.llm.ratelimit.is_rate_limit` 이 상태코드·클래스명·마커 셋 중
    하나로 넓게 잡는 것과 **의도적으로 반대**다. 그쪽은 놓치면 60초를 못 기다려 손해지만,
    이쪽은 잘못 잡으면 실패한 호출을 한 번 더 해서 돈을 두 배 쓴다.
    """
    if _status_of(exc) not in (400, 422):
        return False
    text = str(exc).lower()
    return any(marker in text for marker in (
        "response_format", "json_schema", "schema", "additionalproperties",
        "not supported", "unsupported",
    ))


# --------------------------------------------------------------------------- #
# 내부
# --------------------------------------------------------------------------- #
def _status_of(exc: BaseException) -> Optional[int]:
    """예외에서 HTTP 상태코드. :func:`~contentcompare.llm.ratelimit._status_of` 와 같다.

    두 벌이 되는 것이 마음에 걸리지만, 그쪽을 import 하면 ``ratelimit`` → ``structured``
    → ``langchain_backend`` → ``ratelimit`` 순환이 생긴다. 여덟 줄짜리 순수 함수라
    복제 비용이 순환 비용보다 싸다.
    """
    for holder in (exc, getattr(exc, "response", None)):
        if holder is None:
            continue
        for attr in ("status_code", "status"):
            value = getattr(holder, attr, None)
            if isinstance(value, int):
                return value
    return None


def _tighten(node: Any, *, path: str) -> None:
    """스키마 문서를 **제자리에서** 재귀적으로 조인다.

    ``path`` 는 오로지 오류 메시지용이다. ``$.$defs.WireRecord.properties.attributes``
    처럼 나와야 "어느 모델의 어느 필드가 문제인지"를 바로 고칠 수 있다 — 그게 없으면
    스키마 전체를 눈으로 훑어야 한다.
    """
    if isinstance(node, list):
        for i, item in enumerate(node):
            _tighten(item, path=f"{path}[{i}]")
        return
    if not isinstance(node, dict):
        return

    for key in _UNSUPPORTED_KEYWORDS:
        node.pop(key, None)

    props = node.get("properties")
    if isinstance(props, dict):
        extra = node.get("additionalProperties")
        if extra not in (None, False):
            raise ValueError(_FREE_KEY_MSG.format(path=path))
        node["additionalProperties"] = False
        # **선언 순서 그대로** required 로 만든다. pydantic 이 기본값 있는 필드를 빼 놓은
        # 것을 되돌리는 것이다 — strict 에 "선택 필드"는 없다.
        node["required"] = list(props)
    elif node.get("type") == "object":
        # ``properties`` 없는 object = ``dict[str, X]`` 또는 ``Any``. 자유 키다.
        raise ValueError(_FREE_KEY_MSG.format(path=path))

    # ``list(...)`` 로 스냅샷을 뜬다 — 위에서 ``required``/``additionalProperties`` 를
    # 넣었으므로 그냥 ``node.items()`` 를 돌면 RuntimeError 가 난다.
    for key, value in list(node.items()):
        if key in _NAMED_CHILDREN and isinstance(value, dict):
            for sub_key, sub in value.items():
                _tighten(sub, path=f"{path}.{key}.{sub_key}")
                if key == "properties":
                    _require_type(sub, path=f"{path}.{key}.{sub_key}")
        elif key in _ANON_CHILDREN:
            _tighten(value, path=f"{path}.{key}")


def _require_type(node: Any, *, path: str) -> None:
    """속성 스키마가 **타입을 가졌는지**. ``Any`` 는 ``{}`` 로 나와 서버가 거절한다.

    ``properties`` 아래에서만 검사한다 — ``$defs`` 항목 자체는 위에서 object 로 걸리고,
    ``items`` 아래는 재귀가 결국 여기로 온다.
    """
    if isinstance(node, dict) and any(k in node for k in _TYPING_KEYS):
        return
    raise ValueError(_UNTYPED_MSG.format(path=path))


def _audit(node: dict) -> None:
    """한도(중첩 깊이·속성 총수)를 넘지 않는지. 넘으면 :class:`ValueError`."""
    depth, count = _measure(node, seen=frozenset())
    if depth > MAX_DEPTH:
        raise ValueError(
            f"strict 스키마 중첩이 {depth} 단계입니다(한도 {MAX_DEPTH}). "
            f"중첩 모델을 평평하게 하거나 단계를 쪼개세요."
        )
    if count > MAX_PROPERTIES:
        raise ValueError(
            f"strict 스키마 속성이 {count}개입니다(한도 {MAX_PROPERTIES})."
        )


def _measure(node: Any, *, seen: frozenset) -> tuple[int, int]:
    """(최대 object 중첩 깊이, 속성 총수).

    ``$defs`` 항목도 자식으로 세므로 실제보다 **크게** 나온다 — 한도에 실제보다 일찍
    걸리는 방향이라 안전한 오차다. ``seen`` 은 재귀 스키마에서 무한 루프를 막는다(지금
    모델에는 재귀가 없지만, 이 함수가 ``RecursionError`` 로 죽으면 원인 찾기가 어렵다).
    """
    if id(node) in seen:
        return (0, 0)
    if isinstance(node, list):
        seen = seen | {id(node)}
        got = [_measure(item, seen=seen) for item in node]
        return (max((d for d, _ in got), default=0), sum(c for _, c in got))
    if not isinstance(node, dict):
        return (0, 0)
    seen = seen | {id(node)}
    props = node.get("properties")
    here = 1 if isinstance(props, dict) else 0
    count = len(props) if isinstance(props, dict) else 0
    best = 0
    for key, value in node.items():
        if key in _NAMED_CHILDREN and isinstance(value, dict):
            for sub in value.values():
                depth, sub_count = _measure(sub, seen=seen)
                best, count = max(best, depth), count + sub_count
        elif key in _ANON_CHILDREN:
            depth, sub_count = _measure(value, seen=seen)
            best, count = max(best, depth), count + sub_count
    return (here + best, count)

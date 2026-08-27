"""LLM 응답의 **토큰 사용량**을 한 모양으로 모은다.

세 백엔드 모두 서버가 주는 토큰 수를 **받아놓고 버리고 있었다.** ``complete() -> str``
이라 반환 경로에 실을 자리가 없었기 때문인데, 그 결과 배치 크기를 정할 때 "몇 토큰을
넣어 몇 초가 걸렸나"를 HTTP DEBUG 덤프에서 눈으로 찾아야 했다(실측: ``logs`` 의
``'prompt_eval_count': 776, 'eval_count': 166`` 이 1000자 잘림 안에 파묻혀 있었다).

**같은 숫자를 세 이름으로 부른다** — 그래서 이 모듈이 있다:

===================  ====================================  ==============================
백엔드               입력                                  출력
===================  ====================================  ==============================
``ollama``           ``prompt_eval_count`` (최상위)        ``eval_count``
``internal``         ``usage.prompt_tokens``               ``usage.completion_tokens``
``langchain``        ``usage_metadata.input_tokens``       ``usage_metadata.output_tokens``
===================  ====================================  ==============================

설계 원칙 셋:

1. **읽기만 한다.** 응답에서 숫자를 꺼내는 순수 함수뿐이고 아무것도 저장하지 않는다.
   저장은 백엔드의 ``last_usage``, 표현은 :mod:`contentcompare.timeline` 이 맡는다.
2. **추정하지 않는다.** 서버가 토큰을 안 주면 미상(0)으로 남기고 **키 자체를 빼서**
   타임라인에 나오지 않게 한다. 글자 수에서 토큰을 추정해 같은 칸에 넣으면 실측과
   추정이 섞여 "왜 안 맞나"를 설명할 수 없다 — 글자 수는 이미 ``prompt_chars``/
   ``output_chars`` 로 **따로** 남는다.
3. **어떤 입력에도 죽지 않는다.** 추적이 실행을 막지 않는다는 :mod:`.tracing` 의
   원칙이 여기에도 적용된다(문자열·``None``·깨진 값이 와도 미상으로 돌려준다).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator, Mapping

#: 같은 뜻의 입력 토큰 키들(백엔드별 이름). 먼저 맞는 것이 이긴다.
_INPUT_KEYS = ("input_tokens", "prompt_tokens", "prompt_eval_count")
_OUTPUT_KEYS = ("output_tokens", "completion_tokens", "eval_count")

#: 토큰이 한 겹 안에 들어 있는 경우의 키들(OpenAI 의 ``usage``, langchain 의 두 가지).
_NESTED_KEYS = ("usage", "usage_metadata", "token_usage")

#: langchain 메시지 객체에서 볼 속성. **순서가 우선순위다** — ``usage_metadata`` 가
#: 최신 규격이고, ``response_metadata`` 는 그것이 없는 버전의 폴백이다.
_ATTRS = ("usage_metadata", "response_metadata")


@dataclass(frozen=True)
class Usage:
    """LLM 호출 1건의 토큰 사용량. 0 은 '없음'이 아니라 **'미상'** 이다."""

    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def known(self) -> bool:
        """한쪽이라도 알면 참 — 있는 만큼은 남길 가치가 있다."""
        return bool(self.input_tokens or self.output_tokens)

    def as_detail(self, duration_ms: int = 0) -> dict[str, Any]:
        """타임라인 ``detail`` 에 얹을 모양. **미상이면 빈 dict** 를 돌려준다.

        0 을 남기지 않는 이유: ``output_tokens=0`` 이 줄에 찍히면 '토큰을 0개 썼다'로
        읽히는데, 실제로는 '서버가 안 알려줬다'라서 뜻이 정반대다.
        """
        if not self.known:
            return {}
        detail: dict[str, Any] = {}
        if self.input_tokens:
            detail["input_tokens"] = self.input_tokens
        if self.output_tokens:
            detail["output_tokens"] = self.output_tokens
        rate = self.rate(duration_ms)
        if rate:
            detail["tok_per_sec"] = rate
        return detail

    def rate(self, duration_ms: int) -> float:
        """**출력** 토큰/초. 알 수 없으면 0.

        입력 토큰을 여기 섞지 않는 것은 두 숫자의 성격이 달라서다 — 입력은 한 번에
        읽히고(prefill) 출력은 한 토큰씩 생성된다. 배치를 얼마나 줄여야 하는지는
        생성 쪽 속도가 답한다.
        """
        if duration_ms <= 0 or self.output_tokens <= 0:
            return 0.0
        return round(self.output_tokens / (duration_ms / 1000.0), 1)


#: 미상. 매번 새로 만들지 않도록 하나만 둔다(frozen 이라 공유해도 안전).
UNKNOWN = Usage()


def from_response(response: Any) -> Usage:
    """백엔드 응답(dict 또는 langchain 메시지 객체) → :class:`Usage`.

    **호출부가 쓰는 유일한 함수다.** 어떤 모양이 올지는 이 모듈만 알면 된다.
    """
    if isinstance(response, Mapping):
        return _from_mapping(response)
    for attr in _ATTRS:
        got = getattr(response, attr, None)
        if isinstance(got, Mapping):
            found = _from_mapping(got)
            if found.known:
                return found
    return UNKNOWN


# --------------------------------------------------------------------------- #
def _from_mapping(data: Mapping[str, Any]) -> Usage:
    """dict 안을 (한 겹 중첩까지) 훑어 처음 찾은 사용량을 돌려준다."""
    for scope in _scopes(data):
        found = Usage(_pick(scope, _INPUT_KEYS), _pick(scope, _OUTPUT_KEYS))
        if found.known:
            return found
    return UNKNOWN


def _scopes(data: Mapping[str, Any], depth: int = 0) -> Iterator[Mapping[str, Any]]:
    """자신 → 중첩된 usage 후보 순. 깊이는 2 로 막는다.

    무한정 파고들면 관계없는 dict 의 ``input_tokens`` 를 주워올 수 있다 — 실제
    응답에서 토큰은 최상위이거나 한 겹 안(``response_metadata.token_usage``)이다.
    """
    yield data
    if depth >= 2:
        return
    for key in _NESTED_KEYS:
        inner = data.get(key)
        if isinstance(inner, Mapping):
            yield from _scopes(inner, depth + 1)


def _pick(scope: Mapping[str, Any], keys: tuple[str, ...]) -> int:
    """정수로 읽히는 첫 키의 값. 없거나 숫자가 아니면 0(미상)."""
    for key in keys:
        value = scope.get(key)
        if isinstance(value, bool):  # bool 은 int 의 하위형이라 먼저 걸러낸다
            continue
        if isinstance(value, int) and value >= 0:
            return value
        if isinstance(value, float) and value >= 0:
            return int(value)
    return 0

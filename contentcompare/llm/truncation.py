"""LLM 응답이 **출력 길이 한도에서 잘린 것**을 한 모양으로 모은다.

`usage.py` 와 존재 근거가 글자 그대로 같다 — 그쪽이 *같은 숫자를 세 이름으로 부르는* 문제를
푼다면, 이쪽은 **같은 사건을 네 이름으로 부르는** 문제를 푼다:

=========================================  ==========================================
경로                                       철자
=========================================  ==========================================
``langchain`` + ``response_format``        ``LengthFinishReasonError`` **예외**
``langchain``, ``structured_output: off``  ``AIMessage.response_metadata["finish_reason"]``
``internal``                               ``data["choices"][0]["finish_reason"]``
``ollama``                                 ``data["done_reason"]``
=========================================  ==========================================

**예외로 오는 것은 넷 중 하나뿐이다.** 나머지 셋은 200 으로 잘린 본문을 그대로 돌려주므로
지금까지 "LLM JSON 파싱 실패"로만 보였고, ``temperature=0`` 이라 재시도가 원리적으로 무력했다
(같은 입력 → 같은 지점에서 잘림). 그래서 넷을 한 예외(:class:`LengthLimitError`)로 모은다.

**왜 우리 예외로 번역하는가.** :mod:`contentcompare.fact` 가 "쪼개서 다시 부를지"를 판단하려면
예외를 봐야 하는데, 거기서 ``openai.LengthFinishReasonError`` 를 아는 것은 코어 의존성 최소
정책과 백엔드 교체 가능성(:mod:`.base`)을 동시에 깬다. ``llm/`` 이 자기 예외로 번역하면
``fact/`` 는 백엔드를 모른 채 복구할 수 있다. 원인은 ``raise ... from exc`` 로 보존한다.

**왜 :mod:`.ratelimit` 이 아닌가.** 그 모듈의 계약은 "예외를 판정한다"가 아니라 **"기다릴지
정한다"** 이다(``_RateLimitedBase._call`` 을 구동한다). 길이 한도는 기다려도 안 풀린다 —
거기 두면 다음 사람이 자연스럽게 세 번째 ``if`` 를 달아 5회×60초를 순수하게 버린다.

**왜 :mod:`.structured` 도 아닌가.** 절단은 구조화 출력과 무관하게 일어난다(위 표의 뒤 셋).
구조화 출력은 이 사건을 *예외로 드러나게* 할 뿐 만들지 않는다.

설계 원칙은 :mod:`.usage` 셋을 그대로 물려받는다 — **읽기만 하고**, **추정하지 않고**(못 찾은
것은 빈 문자열·미상으로 둔다), **어떤 입력에도 죽지 않는다**.

⚠️ **예외 메시지에 ``timeout``·``rate limit``·``quota``·``요청 한도`` 를 쓰지 말 것.**
:func:`~contentcompare.llm.ratelimit.is_timeout` 과 :func:`~.ratelimit.is_rate_limit` 이
메시지 마커로도 판정하므로, 조치 안내에 ``llm.timeout`` 같은 낱말을 넣는 순간 이 예외가
"타임아웃"으로 오인되어 **대기 후 재시도**에 들어간다(기다려도 안 풀리는데 60초씩 잔다).
조치 문구는 :data:`contentcompare.timeline._HINTS` 처럼 그 판정을 안 타는 자리에 둔다.
``tests/test_llm_truncation.py`` 가 이 역방향을 고정한다.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from .http import LLMRequestError
from .usage import UNKNOWN, Usage, from_response

#: 잘린 원문을 접을 때 앞/뒤로 남길 글자 수. 꼬리를 남기는 이유는 :func:`fold` 참고.
HEAD_CHARS = 2000
TAIL_CHARS = 2000

#: 종료 사유가 담기는 키. 백엔드마다 이름이 다르다(``ollama`` 는 ``done_reason``).
_REASON_KEYS = ("finish_reason", "done_reason")

#: 길이 한도를 뜻하는 종료 사유 값.
LENGTH = "length"

#: 메시지 마커. **하나뿐이고 넓히지 않는다.**
#:
#: 판정을 좁게 두는 근거는 :func:`~contentcompare.llm.structured.looks_like_schema_rejection`
#: 쪽이지 :func:`~contentcompare.llm.ratelimit.is_rate_limit` 쪽이 아니다. 그쪽은 놓치면
#: 60초를 못 기다려 손해지만, 이쪽은 **잘못 잡으면 배치를 쪼개 호출이 두 배**가 된다.
#: 벌거벗은 ``"length"`` 부분일치는 절대 금지다 — ``content-length``·``context length`` 처럼
#: 무관한 메시지가 걸린다.
_MARKERS = ("length limit was reached",)


class LengthLimitError(LLMRequestError):
    """LLM 출력이 길이 한도에서 잘렸다.

    :class:`~contentcompare.llm.http.LLMRequestError` 를 상속하는 이유는 두 가지다 —
    호출부가 이미 그것을 잡고 있고(``ollama`` 의 빈 응답 설명이 그 타입이었다), 절단은
    실제로 "LLM 요청이 쓸 수 있는 결과를 못 냈다"의 한 종류다.

    :attr:`output` 은 **잘린 응답 원문**(:func:`fold` 로 접힘)이다. 이것이 시나리오를
    가르는 결정적 증거라서 예외에 실어 나른다 — 같은 구조가 반복되면 모델의 퇴행 생성이고,
    서로 다른 정상 항목이 이어지다 끊겼으면 내용이 진짜 긴 것이다. 조치가 정반대다.

    :attr:`output_chars` 는 **접기 전** 길이다. :attr:`output` 의 길이를 쓰면 타임라인에
    4천 자로 찍혀 "출력이 짧았다"로 읽히는데 실제로는 3만 자였다 — ``usage.py`` 가
    ``output_tokens=0`` 을 안 남기는 것과 같은 이유로, 뜻이 뒤집히는 숫자는 안 남긴다.
    """

    def __init__(self, message: str, *, output: str = "", output_chars: int = 0,
                 usage: Usage = UNKNOWN, backend: str = "") -> None:
        super().__init__(message)
        self.output = output
        self.output_chars = output_chars or len(output)
        self.usage = usage
        self.backend = backend


# --------------------------------------------------------------------------- #
# 판정
# --------------------------------------------------------------------------- #
def is_length_limit(exc: BaseException) -> bool:
    """이 예외가 "출력이 길이 한도에서 잘렸다"인가.

    근거 셋을 **강한 것부터** 본다:

    1. 구조 증거 — ``exc.completion.choices[0].finish_reason == "length"``
    2. 클래스명 — 우리 :class:`LengthLimitError` 또는 SDK 의 ``LengthFinishReasonError``
    3. 메시지 마커 — :data:`_MARKERS` (하나뿐이다)

    ``openai`` 를 import 하지 않고 **덕 타이핑**으로 판단하는 것은
    :func:`~contentcompare.llm.ratelimit.is_rate_limit` 과 같은 이유다(코어 의존성 최소).
    """
    if isinstance(exc, LengthLimitError):
        return True
    if _reason_of_completion(_attr(exc, "completion")) == LENGTH:
        return True
    if "lengthfinishreason" in type(exc).__name__.replace("_", "").lower():
        return True
    text = str(exc).lower()
    return any(marker in text for marker in _MARKERS)


def finish_reason_of(response: Any) -> str:
    """응답(dict 또는 langchain 메시지 객체)의 종료 사유. 모르면 빈 문자열.

    **예외가 아니라 성공 응답을 보는 함수다.** 위 표의 뒤 셋(200 으로 잘려 오는 경로)을
    잡는 자리이며, 백엔드가 ``== LENGTH`` 를 확인해 :func:`from_truncated` 로 올린다.
    """
    if isinstance(response, Mapping):
        return _reason_of_mapping(response)
    meta = _attr(response, "response_metadata")
    if isinstance(meta, Mapping):
        return _reason_of_mapping(meta)
    return ""


# --------------------------------------------------------------------------- #
# 번역
# --------------------------------------------------------------------------- #
def from_exception(exc: BaseException, *, backend: str) -> LengthLimitError:
    """SDK 예외 → 우리 예외. 증거(잘린 원문·토큰)를 꺼내 실어 준다.

    호출부는 ``raise from_exception(exc, backend=...) from exc`` 로 원인을 보존한다.
    """
    text, usage = _raw_evidence(exc)
    return LengthLimitError(_message(usage, backend), output=fold(text),
                            output_chars=len(text), usage=usage, backend=backend)


def from_truncated(response: Any, *, backend: str, output: str = "") -> LengthLimitError:
    """예외 없이 잘린 응답(200 + ``finish_reason=length``) → 우리 예외."""
    usage = from_response(response)
    return LengthLimitError(_message(usage, backend), output=fold(output),
                            output_chars=len(output or ""),
                            usage=usage, backend=backend)


def evidence_of(exc: BaseException) -> tuple[str, Usage]:
    """예외에서 **잘린 응답 원문**과 토큰 사용량을 꺼낸다.

    빈 문자열/:data:`~contentcompare.llm.usage.UNKNOWN` 은 "못 찾았다"는 뜻이다 —
    :mod:`.usage` 원칙 2 와 같이 **지어내지 않는다**.

    ``openai`` 의 ``LengthFinishReasonError`` 는 원인이 된 ``ChatCompletion`` 을 통째로
    들고 온다(``_exceptions.py`` 의 ``completion: ChatCompletion``). 지금까지 그 객체가
    ``raise`` 와 함께 버려지고 있었고, 그 안의 잘린 본문이 A/B/C 시나리오를 가르는
    유일한 확정 증거다.
    """
    text, usage = _raw_evidence(exc)
    return fold(text), usage


def _raw_evidence(exc: BaseException) -> tuple[str, Usage]:
    """:func:`evidence_of` 와 같되 **접기 전** 원문을 돌려준다.

    :func:`from_exception` 이 접기 전 길이를 알아야 해서 갈라 뒀다
    (:attr:`LengthLimitError.output_chars` 참고).
    """
    if isinstance(exc, LengthLimitError):
        return exc.output, exc.usage
    completion = _attr(exc, "completion")
    if completion is None:
        return "", UNKNOWN
    message = _attr(_first_choice(completion), "message")
    content = _attr(message, "content")
    # ``from_response`` 에 **completion 통째로** 넘기는 것이 중요하다 — ``.usage`` 라는
    # 이름 있는 속성을 통해서만 들어가야 아무 객체의 ``prompt_tokens`` 를 줍지 않는다.
    return (content if isinstance(content, str) else ""), from_response(completion)


def fold(text: str, *, head: int = HEAD_CHARS, tail: int = TAIL_CHARS) -> str:
    """긴 원문을 **앞 + 꼬리** 로 접는다.

    :class:`~contentcompare.llm.tracing.JsonlTracer` 의 ``_clip`` 은 앞에서만 자르는데,
    절단 진단에서 값진 것은 **꼬리**다 — 어디서 끊겼는지, 무엇이 반복되는지가 거기 있다.
    그래서 추적기를 건드리지 않고 여기서 미리 접는다.
    """
    if not text:
        return ""
    if len(text) <= head + tail:
        return text
    return f"{text[:head]}\n…({len(text) - head - tail}자 생략)…\n{text[-tail:]}"


# --------------------------------------------------------------------------- #
def _message(usage: Usage, backend: str) -> str:
    """사람이 읽을 한 줄.

    ⚠️ **금지어 둘을 지켜야 한다.**

    1. ``timeout``·``rate limit``·``quota``·``요청 한도`` — 이 모듈 독스트링 참고
       (:mod:`.ratelimit` 이 메시지 마커로도 판정해 대기 재시도에 들어간다).
    2. **cp949 에 없는 글자**(``—``·``✓``·``⚠``…). 이 메시지는 결국
       :func:`~contentcompare.logging_setup.log_print` 로 화면에 나가는데 그 함수는
       생 ``print`` 라 :func:`~contentcompare.timeline.console_safe` 를 거치지 않는다.
       Windows PowerShell 기본이 cp949 이므로 그 줄이 ``UnicodeEncodeError`` 로 통째로
       사라진다(CLAUDE.md 의 "종료 줄이 전부 유실됐다"가 같은 사고다).
    """
    got = (f"출력 {usage.output_tokens} 토큰" if usage.output_tokens
           else "출력 토큰 미상")
    where = f"{backend}, " if backend else ""
    return (
        f"LLM 응답이 출력 길이 한도에서 잘렸습니다({where}{got}). "
        "같은 요청을 다시 보내도 같은 지점에서 잘리므로, 배치를 줄이세요"
        "(fact.record_batch_rows / fact.fact_batch_blocks)."
    )


def _reason_of_mapping(data: Mapping[str, Any], depth: int = 0) -> str:
    """dict 에서 종료 사유. ``choices`` 한 겹까지만 들어간다."""
    for key in _REASON_KEYS:
        value = data.get(key)
        if isinstance(value, str) and value:
            return value.strip().lower()
    if depth:
        return ""
    choices = data.get("choices")
    if isinstance(choices, (list, tuple)) and choices:
        first = choices[0]
        if isinstance(first, Mapping):
            return _reason_of_mapping(first, depth + 1)
    return ""


def _reason_of_completion(completion: Any) -> str:
    """``ChatCompletion`` **객체**(dict 아님)에서 종료 사유."""
    value = _attr(_first_choice(completion), "finish_reason")
    return value.strip().lower() if isinstance(value, str) else ""


def _first_choice(completion: Any) -> Any:
    choices = _attr(completion, "choices")
    if isinstance(choices, (list, tuple)) and choices:
        return choices[0]
    return None


def _attr(obj: Any, name: str) -> Optional[Any]:
    """속성 하나를 안전하게 읽는다. 프로퍼티가 던져도 ``None``(:mod:`.usage` 원칙 3)."""
    if obj is None:
        return None
    try:
        return getattr(obj, name, None)
    except Exception:  # noqa: BLE001 — 진단이 실행을 막지 않는다
        return None

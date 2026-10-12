"""``llm.extra_body`` — 백엔드 셋 모두에 임의 요청 필드를 보낸다.

존재 이유는 **추론(사고) 끄기**다. 실측에서 같은 코드가 gemma(-it)로는 통과하고 GLM
계열에서만 출력 절단으로 죽었는데, 사고 토큰이 출력 예산을 먼저 먹기 때문이다. 그런데
사고를 끄는 파라미터 이름은 **배포마다 다르다**(``thinking``/``chat_template_kwargs``/
``reasoning_effort``/…). 그래서 ``think: bool`` 같은 추상을 만들지 않고 날것으로 통과시킨다
— 이름을 하나로 정하는 순간 그 이름을 안 쓰는 게이트웨이에서 **조용히 무시**된다.

``llm.ollama.think`` 와 겹치지 않는다. 그쪽은 Ollama 가 규격으로 정한 이름이 있어 손잡이가
성립하고, 이쪽은 그 이름을 우리가 모르는 경우다.
"""

from __future__ import annotations

import pytest

from contentcompare.config import LLMConfig
from tests.test_llm_truncation import _capture_payload, _langchain_bound

_THINK_OFF = {"thinking": {"type": "disabled"}}


@pytest.mark.parametrize("extra", [{}, _THINK_OFF])
def test_extra_body_reaches_every_backend(extra):
    """세 백엔드가 **같은 설정 한 줄**을 서버에 전달한다.

    ``max_tokens`` 를 셋에 배선할 때와 같은 이유로 한 테스트가 셋을 함께 붙잡는다 — 한
    곳만 하면 ``backend`` 를 바꿨을 때 조용히 무시되는 결함이 **백엔드 축**으로 난다.

    ⚠️ 전달 **방식**은 갈린다. ollama/internal 은 payload 최상위에 병합하고, langchain 은
    openai SDK 의 ``extra_body`` 로 넘긴다 — 그 인자의 정의가 "이 dict 를 요청 본문에
    병합하라"이므로 **서버가 보는 모양은 셋이 같다.** 그래서 앞 둘은 병합 결과를,
    langchain 은 봉투를 확인한다.

    비어 있으면 셋 다 키를 **아예 싣지 않는다** — ``max_tokens: 0`` 과 같은 기본값 계약
    ("오늘과 바이트 단위로 같은 요청")이다.
    """
    from tests.test_llm_http import _NOSLEEP
    from contentcompare.llm.internal import InternalBackend
    from contentcompare.llm.ollama import OllamaBackend

    cfg = LLMConfig(extra_body=dict(extra))
    expected = extra.get("thinking")

    poster, seen = _capture_payload()
    OllamaBackend(cfg, poster=poster, sleep=_NOSLEEP).complete("s", "u")
    assert seen.get("thinking") == expected

    poster, seen = _capture_payload()
    InternalBackend(cfg, poster=poster, sleep=_NOSLEEP).complete("s", "u")
    assert seen.get("thinking") == expected

    assert _langchain_bound(cfg).get("extra_body") == (extra or None)


def test_extra_body_never_overwrites_what_the_code_decided():
    """사람이 넣은 값이 ``messages``/``model`` 같은 코드 소유 필드를 덮으면 안 된다.

    덮게 두면 설정 한 줄로 프롬프트가 통째로 바뀌는데, 그것은 손잡이가 아니라 사고다 —
    ``stream`` 을 켜면 응답 파싱이 조용히 깨지고 원인이 config 에 있다는 것을 아무도 못 본다.
    """
    from tests.test_llm_http import _NOSLEEP
    from contentcompare.llm.internal import InternalBackend

    cfg = LLMConfig(chat_model="real-model",
                    extra_body={"model": "탈취", "messages": [], "thinking": 1})

    poster, seen = _capture_payload()
    InternalBackend(cfg, poster=poster, sleep=_NOSLEEP).complete("s", "u")

    assert seen["model"] == "real-model"
    assert len(seen["messages"]) == 2
    assert seen["thinking"] == 1        # 코드가 안 쓰는 이름은 그대로 통과


def test_the_yaml_key_actually_reaches_the_request():
    """설정 파일 → payload 까지 한 번에 잇는다.

    이 저장소가 두 번 당한 결함이 "설정에는 있는데 호출 경로에는 없다"이므로
    (``_needs_rate_limit_wrapper``), 손잡이를 새로 만들 때는 **YAML 에서 출발하는** 줄이
    하나 필요하다. 위 테스트들은 ``LLMConfig`` 를 직접 만들어 그 구간을 건너뛴다.
    """
    from contentcompare.config import AppConfig
    from contentcompare.llm.internal import InternalBackend
    from tests.test_llm_http import _NOSLEEP

    cfg = AppConfig.from_dict({"llm": {"extra_body": {"thinking": {"type": "disabled"}}}})

    poster, seen = _capture_payload()
    InternalBackend(cfg.llm, poster=poster, sleep=_NOSLEEP).complete("s", "u")

    assert seen["thinking"] == {"type": "disabled"}


def test_the_example_config_still_loads():
    """예시 파일에 새 키를 적었으니 그것이 실제로 읽히는지도 같이 본다."""
    from contentcompare.config import AppConfig

    cfg = AppConfig.load("config/config.example.yaml")
    assert cfg.llm.extra_body == {}

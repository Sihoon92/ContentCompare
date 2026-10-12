"""사내 게이트웨이가 structured output(``response_format``)을 받아주는지 확인한다.

**일회용 프로브다.** 설계 갈래를 정하기 위한 것이고 파이프라인은 이 파일을 쓰지 않는다.

pydantic 모델로 LLM 출력을 정형화하려면 결국 ``response_format`` 을 서버에 보내야
하는데, 사내 게이트웨이가 그것을 어느 수준까지 받아주는지에 따라 **설계가 통째로
갈린다**:

    json_schema 지원  → 스키마까지 서버가 강제. 파싱 실패가 원리적으로 사라진다.
    json_object 만    → 문법만 보장. 스키마는 프롬프트 + pydantic 검증이 맡는다.
    미지원(400)       → 서버 강제 불가. pydantic 은 검증·교정 루프 전담.

그래서 세 발을 쏘고 **상태코드와 에러 본문을 그대로** 보여 준다. 판단은 사람이 한다 —
스크립트가 "지원함/안 함"을 단정하면 게이트웨이가 400 을 주는 이유(스키마 형식 문제인지
기능 미지원인지)를 덮어 버린다.

조건을 실제 운영과 **똑같이** 맞춘다(base_url·api_key·verify_ssl·프록시 정책 모두
``config.yaml`` 에서 읽는다). 그러지 않으면 "프로브는 되는데 본 실행은 안 되는" 차이가
생겨 원인 격리에 쓸 수 없다.

실행:
    python scripts/probe_structured_output.py --config config/config.yaml

    # 대조군만 빠르게 (연결 자체 점검)
    python scripts/probe_structured_output.py --only baseline
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests  # noqa: E402

from contentcompare.config import AppConfig, no_proxy  # noqa: E402
from contentcompare.timeline import console_safe  # noqa: E402


def _say(text: str = "") -> None:
    """화면 출력. **반드시 ``print`` 대신 이것을 쓴다.**

    Windows PowerShell 5.1 기본 인코딩이 cp949 라 ``—``·``✓``·``✗``·``⚠`` 를 그대로
    ``print`` 하면 ``UnicodeEncodeError`` 로 **그 줄이 통째로 사라진다**(실측: 이
    스크립트의 ``--help`` 가 그렇게 죽었다). :func:`console_safe` 가 해당 콘솔이
    실제로 못 쓰는 문자만 바꾼다.
    """
    print(console_safe(text, getattr(sys.stdout, "encoding", None)))


# --------------------------------------------------------------------------- #
# 프로브 입력 — 작게 유지한다(토큰을 거의 안 쓰고, 실패해도 원인이 스키마 하나뿐).
# --------------------------------------------------------------------------- #
SYSTEM = "너는 JSON 만 출력하는 도우미다."
USER = (
    "다음 문장에서 항목 이름과 값을 뽑아 JSON 으로 만들어라.\n"
    "문장: 정격전압은 3.7 V 이다.\n"
    '형식: {"items": [{"name": "...", "value": "...", "unit": "..."}]}'
)

#: ``json_schema`` 프로브용 스키마. OpenAI strict 모드 규칙을 따른다 —
#: 모든 속성이 ``required`` 에 있어야 하고 ``additionalProperties: false`` 여야 한다.
#: (이 규칙을 어기면 기능은 지원되는데도 400 이 나서 오판하게 된다.)
ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "value": {"type": "string"},
        "unit": {"type": "string"},
    },
    "required": ["name", "value", "unit"],
    "additionalProperties": False,
}
RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": ITEM_SCHEMA}},
    "required": ["items"],
    "additionalProperties": False,
}

#: (라벨, response_format) — 위에서부터 순서대로 쏜다. 대조군이 첫 번째인 이유는
#: 여기서 실패하면 나머지 결과가 전부 무의미하기 때문이다(연결·키·모델명 문제).
PROBES: list[tuple[str, Optional[dict]]] = [
    ("baseline", None),
    ("json_object", {"type": "json_object"}),
    (
        "json_schema",
        {
            "type": "json_schema",
            "json_schema": {
                "name": "extraction_result",
                "strict": True,
                "schema": RESULT_SCHEMA,
            },
        },
    ),
]


def _headers(cfg: AppConfig) -> dict[str, str]:
    """``InternalBackend._headers`` 와 같은 규칙 — 직접 지정 키 우선, 없으면 환경변수."""
    headers = {"Content-Type": "application/json"}
    internal = cfg.llm.internal
    api_key = internal.api_key or os.environ.get(internal.api_key_env, "")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _snippet(text: str, limit: int = 600) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + f"… (총 {len(text)}자)"


def _post(cfg: AppConfig, payload: dict) -> tuple[Optional[int], str, Optional[dict]]:
    """(상태코드, 본문 요약, 파싱된 JSON) — 예외도 본문 자리에 담아 돌려준다.

    한 발이 죽어도 나머지를 계속 쏜다. 세 결과를 **나란히** 봐야 "기능 미지원"과
    "환경 문제"가 갈리는데, 첫 예외에서 멈추면 그 비교가 불가능하다.
    """
    url = f"{cfg.llm.internal.base_url.rstrip('/')}/chat/completions"
    ctx = no_proxy() if cfg.llm.internal.unset_proxy else _null_ctx()
    try:
        with ctx:
            resp = requests.post(
                url,
                json=payload,
                headers=_headers(cfg),
                timeout=cfg.llm.timeout,
                verify=cfg.llm.internal.verify_ssl,
            )
    except Exception as exc:  # noqa: BLE001 — 예외 자체가 결과다
        return None, f"{type(exc).__name__}: {exc}", None
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001 — JSON 이 아닌 에러 페이지도 있다
        return resp.status_code, _snippet(resp.text), None
    return resp.status_code, _snippet(json.dumps(data, ensure_ascii=False)), data


class _null_ctx:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: object) -> bool:
        return False


def _content_of(data: Optional[dict]) -> str:
    """OpenAI 호환 응답에서 본문만 꺼낸다. 모양이 다르면 빈 문자열."""
    if not isinstance(data, dict):
        return ""
    try:
        return str(data["choices"][0]["message"]["content"] or "")
    except (KeyError, IndexError, TypeError):
        return ""


def run_probe(cfg: AppConfig, label: str, response_format: Optional[dict]) -> bool:
    """한 발 쏘고 결과를 출력한다. 성공(2xx + 파싱 가능한 JSON 본문)이면 True."""
    payload: dict[str, Any] = {
        "model": cfg.llm.chat_model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": USER},
        ],
        "temperature": 0.0,
    }
    if response_format is not None:
        payload["response_format"] = response_format

    _say(f"\n{'=' * 70}\n[{label}] response_format = "
          f"{json.dumps(response_format, ensure_ascii=False) if response_format else '(없음)'}")
    status, body, data = _post(cfg, payload)
    _say(f"  status : {status if status is not None else '(요청 실패)'}")

    if status is None or status >= 400:
        # 에러 본문이 이 프로브의 **핵심 산출물**이다 — "지원 안 함"과 "스키마 형식이
        # 틀림"과 "모델이 이 기능을 못 씀"이 전부 여기서 갈린다.
        _say(f"  본문   : {body}")
        return False

    content = _content_of(data)
    _say(f"  본문   : {_snippet(content) or body}")
    try:
        parsed = json.loads(content)
    except Exception as exc:  # noqa: BLE001
        _say(f"  파싱   : ✗ 실패 — {type(exc).__name__}: {exc}")
        return False
    _say(f"  파싱   : ✓ 성공 (최상위 {type(parsed).__name__}, "
          f"키={list(parsed)[:5] if isinstance(parsed, dict) else '-'})")
    return True


def main(argv: Optional[list[str]] = None) -> int:
    # ``description=__doc__`` 을 쓰지 않는다 — argparse 는 도움말을 자기가 직접
    # ``file.write`` 로 내보내므로 :func:`_say` 를 우회하고, 모듈 독스트링의 화살표가
    # cp949 콘솔에서 ``--help`` 를 통째로 죽인다(실측). 자세한 설명은 파일 상단 참고.
    p = argparse.ArgumentParser(
        description="사내 게이트웨이가 response_format 을 받아주는지 확인하는 일회용 프로브"
    )
    p.add_argument("--config", default="config/config.yaml", help="설정 파일 경로")
    p.add_argument("--only", default="", help="한 발만 쏜다: baseline|json_object|json_schema")
    args = p.parse_args(argv)

    cfg = AppConfig.load(args.config)
    backend = cfg.llm.backend.lower()
    _say(f"설정   : {args.config}")
    _say(f"backend: {backend}  model: {cfg.llm.chat_model}")
    _say(f"base_url: {cfg.llm.internal.base_url}  verify_ssl={cfg.llm.internal.verify_ssl}"
          f"  unset_proxy={cfg.llm.internal.unset_proxy}")
    if backend not in ("internal", "langchain"):
        # 막지는 않는다 — internal 섹션만 채워져 있으면 프로브는 성립한다.
        _say(f"⚠️ backend 가 '{backend}' 입니다. 이 프로브는 internal 섹션의 "
              f"base_url 로 직접 쏩니다(사내 게이트웨이 확인이 목적).")

    probes = [x for x in PROBES if not args.only or x[0] == args.only]
    if not probes:
        _say(f"✗ --only 값이 잘못됐습니다: {args.only!r}")
        return 2

    results = {label: run_probe(cfg, label, fmt) for label, fmt in probes}

    _say(f"\n{'=' * 70}\n요약")
    for label, ok in results.items():
        _say(f"  {label:<12} {'✓ 통과' if ok else '✗ 실패'}")
    _say(
        "\n판단 기준\n"
        "  baseline 실패          → 연결·키·모델명 문제. 나머지 결과는 무의미하다.\n"
        "  json_schema 통과       → 스키마까지 서버가 강제할 수 있다(최선).\n"
        "  json_object 만 통과    → 문법만 보장. 스키마는 프롬프트+검증이 맡는다.\n"
        "  둘 다 400              → 위 에러 본문을 보고 '기능 미지원'인지\n"
        "                           '스키마 형식 거부'인지 가른다.\n"
    )
    return 0 if results.get("baseline", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())

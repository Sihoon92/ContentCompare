"""사내 LLM 연결 단독 테스트 (langchain 기반).

기존 contentcompare 코드와 **무관**하게, OpenAI 호환 엔드포인트에
base_url + api_key + model 세 값만으로 접속해 응답이 오는지 확인한다.

준비:
    pip install langchain-openai httpx

설정: 아래 세 값만 채우면 된다(또는 환경변수 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL 로 덮어쓰기).

실행:
    python scripts/llm_connect_test.py
"""

from __future__ import annotations

import os
import sys
import traceback

# --------------------------------------------------------------------------- #
# ↓↓↓ 이 세 값만 채우세요 ↓↓↓
BASE_URL = "https://api-gernsi.samsungsdi.net/api/llm/openai/v1"
API_KEY = "여기에_사내_API_KEY"
MODEL = "/models/llm/gemma-4-31B-it"
# ↑↑↑ 여기까지 ↑↑↑
# --------------------------------------------------------------------------- #

# 환경변수가 있으면 그 값을 우선 사용(파일 수정 없이 테스트 가능).
BASE_URL = os.environ.get("LLM_BASE_URL", BASE_URL)
API_KEY = os.environ.get("LLM_API_KEY", API_KEY)
MODEL = os.environ.get("LLM_MODEL", MODEL)


def main() -> int:
    # 사내망 직결: 프록시 환경변수를 비운다(있으면 우회).
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        os.environ[k] = ""

    try:
        import httpx
        from langchain_openai import ChatOpenAI
    except ImportError:
        print("[설치 필요] pip install langchain-openai httpx")
        return 2

    print(f"- base_url: {BASE_URL}")
    print(f"- model   : {MODEL}")
    print(f"- api_key : {'설정됨' if API_KEY and 'API_KEY' not in API_KEY else '미설정(!)'}\n")

    chat = ChatOpenAI(
        model=MODEL,
        base_url=BASE_URL,
        api_key=API_KEY,
        temperature=0,
        timeout=60,
        # 사내 사설 인증서면 검증을 끈다(필요 없으면 verify=True 로).
        http_client=httpx.Client(verify=False),
    )

    try:
        resp = chat.invoke(
            [("system", "연결 테스트입니다."), ("human", "'OK' 라고만 답하세요.")]
        )
    except Exception:  # noqa: BLE001 - 원인 전체 출력
        print("❌ 연결 실패:\n")
        traceback.print_exc()
        return 1

    print("✅ 연결 성공")
    print("응답:", getattr(resp, "content", resp))
    return 0


if __name__ == "__main__":
    sys.exit(main())

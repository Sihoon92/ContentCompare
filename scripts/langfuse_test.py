"""Langfuse 연결 단독 테스트.

기존 contentcompare 코드와 **무관**하게, 다른 프로젝트에서 쓰던 것과 똑같은 방식
(패키지만 설치 + 키 3개)으로 붙여 본다. 목적은 원인을 한 번에 가르는 것이다:

    이 스크립트가 되면  → 인증서·네트워크는 정상. contentcompare 쪽 배선 문제.
    이 스크립트도 안 되면 → 환경(인증서/프록시/키) 문제. 여기서 원인이 찍힌다.

준비:
    pip install langfuse

설정: 아래 세 값을 채우거나 환경변수로 준다
    LANGFUSE_HOST / LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY

실행:
    python scripts/langfuse_test.py

    # 인증서가 원인인지 즉시 확인 (검증 끄고 1단계만 재시도)
    python scripts/langfuse_test.py --insecure
"""

from __future__ import annotations

import os
import sys
import traceback

# --------------------------------------------------------------------------- #
# ↓↓↓ 이 세 값만 채우세요 ↓↓↓
HOST = "https://langfuse.내부주소"      # 브라우저로 접속하는 그 주소 (끝에 / 없이)
PUBLIC_KEY = "pk-lf-..."
SECRET_KEY = "sk-lf-..."
# ↑↑↑ 여기까지 ↑↑↑
# --------------------------------------------------------------------------- #

# 환경변수가 있으면 우선 사용(파일 수정 없이 테스트 가능).
HOST = os.environ.get("LANGFUSE_HOST", HOST).rstrip("/")
PUBLIC_KEY = os.environ.get("LANGFUSE_PUBLIC_KEY", PUBLIC_KEY)
SECRET_KEY = os.environ.get("LANGFUSE_SECRET_KEY", SECRET_KEY)

INSECURE = "--insecure" in sys.argv


def _diagnose(exc: BaseException) -> str:
    """예외를 조치 가능한 한 줄로 번역한다. 스택만 보고 헤매지 않도록."""
    text = f"{type(exc).__name__}: {exc}"
    if "CERTIFICATE_VERIFY_FAILED" in text or "SSLError" in text or "SSL:" in text:
        return (
            "인증서 문제입니다.\n"
            "  → `--insecure` 로 다시 돌려 보세요. 그때 통과하면 원인이 확정됩니다.\n"
            "  → 사내 루트 CA 를 PEM 번들로 만들어 SSL_CERT_FILE 로 지정하거나,\n"
            "     `pip install truststore` 후 이 스크립트를 다시 돌려 보세요\n"
            "     (truststore 가 있으면 아래 1단계가 Windows 인증서 저장소를 씁니다)."
        )
    if "401" in text or "Unauthorized" in text or "auth" in text.lower():
        return "키가 틀렸거나 이 프로젝트의 키가 아닙니다 — Langfuse 웹의 Project Settings 에서 재발급."
    if "ConnectTimeout" in text or "ConnectionError" in text or "Name or service" in text:
        return "주소에 닿지 못했습니다 — HOST 오타 / VPN / 프록시를 확인하세요."
    return "위 스택을 그대로 공유해 주세요."


def step0_env() -> bool:
    """설치·설정이 갖춰졌는지."""
    print("[0] 환경")
    try:
        import langfuse
        ver = getattr(langfuse, "__version__", "?")
    except ImportError:
        print("    ❌ langfuse 미설치 — pip install langfuse")
        return False
    print(f"    langfuse=={ver}")
    print(f"    host={HOST}")
    # 키는 절대 전부 출력하지 않는다. 붙여넣기 사고(공백/줄바꿈)만 잡을 수 있게 앞뒤만.
    for name, key in (("public_key", PUBLIC_KEY), ("secret_key", SECRET_KEY)):
        ok = key and not key.endswith("...")
        mark = "✅" if ok else "❌"
        print(f"    {mark} {name}: {key[:7]}…{key[-3:]} (len={len(key)})")
        if not ok:
            print("       → 값을 채우거나 환경변수로 주세요.")
            return False
    if HOST.startswith("http://"):
        print("    ℹ️  http 라서 인증서 검증 자체가 없습니다 — SSL 오류라면 host 를 의심하세요.")
    return True


def step1_reachable() -> bool:
    """SDK 를 빼고 HTTP 로만 서버에 닿는지. 인증서/네트워크만 분리해서 본다."""
    print("[1] 서버 도달 (SDK 없이)")
    url = f"{HOST}/api/public/health"
    try:
        import httpx
    except ImportError:
        print("    ⏭  httpx 없음 — 건너뜀")
        return True

    verify: object = not INSECURE
    if not INSECURE:
        # truststore 가 있으면 OS(Windows) 인증서 저장소를 그대로 쓴다. 사내 루트가
        # 이미 깔려 있는 PC 에서는 이것만으로 인증서 문제가 사라진다.
        try:
            import ssl

            import truststore
            verify = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            print("    (truststore 사용 — OS 인증서 저장소)")
        except ImportError:
            pass

    try:
        res = httpx.get(url, verify=verify, timeout=10)
    except Exception as exc:  # noqa: BLE001 — 원인을 그대로 보여준다
        print(f"    ❌ {type(exc).__name__}: {exc}")
        print(f"    → {_diagnose(exc)}")
        return False
    print(f"    ✅ {res.status_code} {res.text[:120]}")
    if INSECURE:
        print("    ⚠️  검증을 끄고 통과했습니다 = 인증서가 원인입니다.")
    return True


def step2_sdk() -> bool:
    """다른 프로젝트에서 쓰는 것과 같은 방식으로 클라이언트를 만들고 인증 확인."""
    print("[2] SDK 연결 + 인증")
    # SDK 버전에 따라 host / base_url 을 읽는 이름이 다르다. 둘 다 깔아 준다.
    os.environ["LANGFUSE_HOST"] = HOST
    os.environ["LANGFUSE_BASE_URL"] = HOST
    os.environ["LANGFUSE_PUBLIC_KEY"] = PUBLIC_KEY
    os.environ["LANGFUSE_SECRET_KEY"] = SECRET_KEY

    # 1단계가 통과했는데 여기서 SSL 로 막히는 이유: SDK 가 **내부에서 자기 httpx
    # 클라이언트를 만들고**, httpx 는 SSL_CERT_FILE 을 읽지 않고 certifi 를 쓴다.
    # inject_into_ssl() 은 ssl.SSLContext 자체를 갈아끼워 그 경로까지 덮는다.
    if not INSECURE:
        try:
            import truststore
            truststore.inject_into_ssl()
            print("    (truststore 주입 — SDK 내부 클라이언트까지 OS 저장소 사용)")
        except ImportError:
            print("    ⚠️  truststore 미설치 — SSL 로 막히면 pip install truststore")

    from langfuse import Langfuse

    try:
        client = Langfuse(public_key=PUBLIC_KEY, secret_key=SECRET_KEY, host=HOST)
    except Exception as exc:  # noqa: BLE001
        print(f"    ❌ 생성 실패: {type(exc).__name__}: {exc}")
        print(f"    → {_diagnose(exc)}")
        traceback.print_exc()
        return False

    auth = getattr(client, "auth_check", None)
    if auth is None:
        print("    ℹ️  이 버전엔 auth_check 가 없습니다 — 3단계 전송 결과로 판단하세요.")
        return True
    try:
        ok = auth()
    except Exception as exc:  # noqa: BLE001
        print(f"    ❌ auth_check 실패: {type(exc).__name__}: {exc}")
        print(f"    → {_diagnose(exc)}")
        return False
    print(f"    {'✅ 인증 통과' if ok is not False else '❌ 인증 실패(키 확인)'}")
    return ok is not False


def step3_send() -> bool:
    """trace 1건을 실제로 보내고 웹에서 눈으로 확인한다.

    ``@observe`` 는 v2(``langfuse.decorators``)와 v3(``langfuse``)의 import 위치가
    달라 양쪽을 시도한다 — 사내 설치 버전을 모른 채로도 돌아가게.
    """
    print("[3] trace 전송")
    try:
        from langfuse import observe  # v3
    except ImportError:
        try:
            from langfuse.decorators import observe  # v2
        except ImportError:
            print("    ⏭  observe 를 찾지 못해 건너뜀(2단계까지 통과면 연결은 정상).")
            return True

    @observe()
    def hello(question: str) -> str:
        return f"ContentCompare 연결 테스트 응답 ({question})"

    try:
        out = hello("Langfuse 붙었나요?")
        from langfuse import Langfuse
        Langfuse(public_key=PUBLIC_KEY, secret_key=SECRET_KEY, host=HOST).flush()
    except Exception as exc:  # noqa: BLE001
        print(f"    ❌ {type(exc).__name__}: {exc}")
        print(f"    → {_diagnose(exc)}")
        return False
    print(f"    ✅ 전송 완료: {out}")
    print(f"    → {HOST} 의 Tracing 화면에서 'hello' trace 를 확인하세요.")
    return True


def main() -> int:
    # Windows 콘솔 기본 코드페이지(cp949)는 ✅/❌ 를 못 찍고 죽는다.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print(f"=== Langfuse 연결 테스트 {'(검증 끔)' if INSECURE else ''} ===")
    if not step0_env():
        return 2
    if not step1_reachable():
        return 1
    if not step2_sdk():
        return 1
    if not step3_send():
        return 1
    print("\n🎉 전부 통과 — 순수 SDK 로는 정상입니다.")
    print("   contentcompare 에서만 SSL 오류가 난다면 원인은 인증서가 아니라")
    print("   llm/tracing.py 의 CA 배선입니다. config 의 ssl_cert 를 비우고")
    print("   `contentcompare --check` 를 다시 돌려 비교해 보세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""실행 환경 진단 — 어떤 파이썬/패키지가 실제로 쓰이는지 출력.

같은 파이썬으로 두 번 돌려 비교하세요(중요):
    python scripts/diag_env.py
    python -m streamlit run app/streamlit_app.py   # ← streamlit 도 같은 파이썬으로

'streamlit run' 과 'python' 이 다른 환경이면 embed_backend 에러의 원인입니다.
"""

from __future__ import annotations

import inspect
import sys


def main() -> int:
    print("python 실행 파일:", sys.executable)
    print()

    try:
        import streamlit

        print("streamlit:", streamlit.__version__)
        print("  위치:", streamlit.__file__)
    except Exception as exc:  # noqa: BLE001
        print("streamlit import 실패:", exc)
    print()

    try:
        import contentcompare
        import contentcompare.ui.runner as r

        print("contentcompare 위치:", contentcompare.__file__)
        print("runner 위치       :", r.__file__)
        params = list(inspect.signature(r.build_config).parameters)
        print("build_config 인자 :", params)
        print("embed_backend 존재:", "embed_backend" in params)
        if "embed_backend" not in params:
            print("\n❌ 옛 contentcompare 를 임포트 중입니다.")
            print("   → 이 파이썬 환경에서: pip uninstall -y contentcompare 후 pip install -e .")
            print("   → 그리고 streamlit 은 반드시 'python -m streamlit run ...' 로 실행하세요.")
            return 1
        print("\n✅ 이 파이썬 환경의 contentcompare 는 최신입니다.")
        print("   streamlit 도 같은 파이썬으로: python -m streamlit run app/streamlit_app.py")
    except Exception as exc:  # noqa: BLE001
        print("contentcompare import 실패:", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

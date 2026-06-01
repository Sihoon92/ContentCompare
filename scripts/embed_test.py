"""fastembed 로컬 임베딩 단독 테스트.

chat/LLM 과 무관하게, fastembed 가 설치·동작하고 지정한 모델이 임베딩을 만드는지
확인한다. (사내 chat 엔드포인트가 임베딩을 제공하지 않을 때 이 방식으로 대체)

준비:
    pip install fastembed

실행:
    python scripts/embed_test.py
    python scripts/embed_test.py intfloat/multilingual-e5-small   # 모델 지정
    EMBED_MODEL=BAAI/bge-m3 python scripts/embed_test.py          # 환경변수로 지정
"""

from __future__ import annotations

import os
import sys

# 다국어(한국어 포함) 권장 기본 모델.
DEFAULT_MODEL = "intfloat/multilingual-e5-large"


def main() -> int:
    # 모델명: 인자 > 환경변수 > 기본값.
    model_name = (
        sys.argv[1] if len(sys.argv) > 1 else os.environ.get("EMBED_MODEL", DEFAULT_MODEL)
    )

    try:
        from fastembed import TextEmbedding
    except ImportError:
        print("[설치 필요] pip install fastembed")
        return 2

    # 오프라인(사내망): 미리 받아둔 모델 폴더 지정.
    cache_dir = os.environ.get("EMBED_CACHE_DIR", "")
    kwargs = {"cache_dir": cache_dir} if cache_dir else {}

    print(f"- 모델: {model_name}")
    if cache_dir:
        print(f"- 캐시 폴더: {cache_dir} (오프라인)")
    else:
        print("  (최초 실행 시 모델 다운로드가 일어날 수 있습니다)")
    print()

    try:
        model = TextEmbedding(model_name=model_name, **kwargs)
    except Exception as exc:  # noqa: BLE001 - 미지원 모델 등
        print(f"❌ 모델 로드 실패: {exc}\n")
        names = [
            m.get("model") or m.get("model_name")
            for m in TextEmbedding.list_supported_models()
        ]
        print("지원 모델 목록:")
        for n in names:
            print("  -", n)
        return 1

    texts = ["연결 테스트", "매출액은 1,200억원이다.", "embedding sanity check"]
    vectors = list(model.embed(texts))
    dim = len(vectors[0].tolist())
    print(f"✅ 임베딩 성공 — {len(vectors)}개 문장, 차원 {dim}")
    print(f"   첫 벡터 앞 5개: {vectors[0].tolist()[:5]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

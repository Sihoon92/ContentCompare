"""(인터넷 되는 PC에서 실행) fastembed 임베딩 모델을 폴더로 내려받는다.

사내(오프라인) PC 로 그 폴더를 통째로 복사한 뒤, config 의 embed_cache_dir 에
같은 경로를 지정하면 다운로드 없이 사용된다.

준비:
    pip install fastembed

실행:
    python scripts/download_embed_model.py
    python scripts/download_embed_model.py intfloat/multilingual-e5-large ./fastembed_models
"""

from __future__ import annotations

import sys

DEFAULT_MODEL = "intfloat/multilingual-e5-large"
DEFAULT_DIR = "./fastembed_models"


def main() -> int:
    model_name = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MODEL
    cache_dir = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_DIR

    try:
        from fastembed import TextEmbedding
    except ImportError:
        print("[설치 필요] pip install fastembed")
        return 2

    print(f"모델 다운로드: {model_name}\n저장 폴더: {cache_dir}\n")
    model = TextEmbedding(model_name=model_name, cache_dir=cache_dir)
    # 실제 임베딩을 한 번 돌려 모든 파일이 받아졌는지 확인.
    vec = next(iter(model.embed(["warmup"])))
    print(f"✅ 완료 — 차원 {len(vec.tolist())}")
    print(f"\n이제 '{cache_dir}' 폴더를 사내 PC 로 복사하고, config 에 다음을 설정하세요:")
    print(f"  llm:\n    embed_model: {model_name}\n    embed_cache_dir: {cache_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

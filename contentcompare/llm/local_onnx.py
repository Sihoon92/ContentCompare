"""로컬 ONNX 임베딩 백엔드.

직접 받아둔 ONNX 임베딩 모델 폴더(예: ``multilingual-e5-large-onnx``)를 그대로
읽어 임베딩한다. fastembed 의 다운로드 캐시 구조에 의존하지 않으므로, 폴더를
원하는 위치에 두고 ``config.embed_model_path`` 로 가리키기만 하면 된다.

필요한 파일(폴더 안):
    - model.onnx            (또는 *.onnx 하나)
    - tokenizer.json

런타임 의존성: onnxruntime, tokenizers, numpy (pip install -e .[onnx]).
풀링/정규화는 풀파이썬으로 구현해 테스트 시 세션/토크나이저 주입만으로 검증된다.
"""

from __future__ import annotations

import glob
import math
import os
from typing import Any, Optional

from ..config import LLMConfig


def _find_onnx(folder: str) -> str:
    """폴더에서 사용할 .onnx 파일 경로를 고른다(model.onnx 우선)."""
    preferred = os.path.join(folder, "model.onnx")
    if os.path.isfile(preferred):
        return preferred
    candidates = sorted(glob.glob(os.path.join(folder, "*.onnx")))
    if not candidates:
        raise RuntimeError(f"ONNX 파일(.onnx)을 찾지 못했습니다: {folder}")
    return candidates[0]


class LocalOnnxEmbedding:
    """로컬 폴더의 ONNX 임베딩 모델(EmbeddingClient). chat 은 지원하지 않는다."""

    def __init__(
        self,
        config: LLMConfig,
        *,
        session: Optional[Any] = None,
        tokenizer: Optional[Any] = None,
    ) -> None:
        self.config = config
        self._sess = session
        self._tok = tokenizer

    def _ensure(self):
        path = self.config.embed_model_path
        if not path:
            raise RuntimeError("embed_model_path 에 ONNX 모델 폴더 경로를 지정하세요.")
        if not os.path.isdir(path):
            raise RuntimeError(f"embed_model_path 폴더가 없습니다: {path}")

        if self._sess is None:
            try:
                import onnxruntime as ort  # noqa: WPS433
            except ImportError as exc:  # pragma: no cover - 환경 의존
                raise RuntimeError("onnxruntime 가 필요합니다: pip install -e .[onnx]") from exc
            self._sess = ort.InferenceSession(
                _find_onnx(path), providers=["CPUExecutionProvider"]
            )
        if self._tok is None:
            try:
                from tokenizers import Tokenizer  # noqa: WPS433
            except ImportError as exc:  # pragma: no cover - 환경 의존
                raise RuntimeError("tokenizers 가 필요합니다: pip install -e .[onnx]") from exc
            tok = Tokenizer.from_file(os.path.join(path, "tokenizer.json"))
            tok.enable_truncation(max_length=512)
            tok.enable_padding()
            self._tok = tok
        return self._sess, self._tok

    # --- EmbeddingClient -------------------------------------------------- #
    def embed(self, texts: list[str], *, kind: str = "passage") -> list[list[float]]:
        sess, tok = self._ensure()
        prefix = self.config.embed_prefix_for(kind)
        encs = tok.encode_batch([prefix + t for t in texts])
        input_ids = [list(e.ids) for e in encs]
        attention = [list(e.attention_mask) for e in encs]

        # onnxruntime 입력은 numpy 가 필요(런타임에 항상 동반). 미설치면 리스트로 폴백
        # (테스트의 가짜 세션은 feed 를 무시한다).
        try:
            import numpy as np  # noqa: WPS433

            def arr(x):
                return np.array(x, dtype="int64")
        except ImportError:  # pragma: no cover - 테스트 폴백
            def arr(x):
                return x

        names = {i.name for i in sess.get_inputs()}
        feed: dict[str, Any] = {}
        if "input_ids" in names:
            feed["input_ids"] = arr(input_ids)
        if "attention_mask" in names:
            feed["attention_mask"] = arr(attention)
        if "token_type_ids" in names:
            feed["token_type_ids"] = arr([[0] * len(r) for r in input_ids])

        outputs = sess.run(None, feed)
        last_hidden = outputs[0]
        if hasattr(last_hidden, "tolist"):
            last_hidden = last_hidden.tolist()  # numpy → 풀파이썬

        return [
            _mean_pool_normalize(seq, mask)
            for seq, mask in zip(last_hidden, attention)
        ]


def _mean_pool_normalize(seq: list[list[float]], mask: list[int]) -> list[float]:
    """attention mask 로 평균 풀링 후 L2 정규화(코사인용 단위벡터)."""
    hidden = len(seq[0]) if seq else 0
    summed = [0.0] * hidden
    count = 0
    for tok_vec, m in zip(seq, mask):
        if not m:
            continue
        count += 1
        for j in range(hidden):
            summed[j] += tok_vec[j]
    count = count or 1
    mean = [s / count for s in summed]
    norm = math.sqrt(sum(v * v for v in mean)) or 1.0
    return [v / norm for v in mean]

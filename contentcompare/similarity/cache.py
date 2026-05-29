"""임베딩 디스크 캐시.

같은 텍스트를 재실행마다 다시 임베딩하지 않도록, (모델명+텍스트) 해시를 키로
벡터를 디스크에 보관한다. :class:`EmbeddingClient` 를 감싸 동일 인터페이스를 제공한다.

저장 형식은 모델별 JSON 파일(``{hash: vector}``). 수천 청크 규모를 가정하며,
그 이상은 npy/sqlite 로 교체할 수 있다(인터페이스는 유지).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from typing import Optional

from ..llm.base import EmbeddingClient

logger = logging.getLogger(__name__)


class CachedEmbedder:
    """임베딩 백엔드를 감싸 디스크 캐시를 적용한다.

    cache_dir 가 비어있으면(또는 None) 캐시 없이 그대로 위임한다.
    """

    def __init__(
        self,
        embedder: EmbeddingClient,
        cache_dir: Optional[str],
        model_name: str = "",
    ) -> None:
        self._embedder = embedder
        self._model = model_name
        self._dir = cache_dir or None
        self._cache: dict[str, list[float]] = {}
        self._loaded = False
        self._dirty = False

    # ------------------------------------------------------------------ #
    @property
    def _path(self) -> Optional[str]:
        if not self._dir:
            return None
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", self._model) or "default"
        return os.path.join(self._dir, f"emb_{safe}.json")

    def _key(self, text: str) -> str:
        h = hashlib.sha1()
        h.update(self._model.encode("utf-8"))
        h.update(b"\x00")
        h.update(text.encode("utf-8"))
        return h.hexdigest()

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        path = self._path
        if path and os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self._cache = json.load(f)
                logger.debug("임베딩 캐시 로드: %d개 (%s)", len(self._cache), path)
            except (OSError, ValueError):  # 손상된 캐시는 무시하고 새로 시작
                self._cache = {}

    def _save(self) -> None:
        path = self._path
        if not path or not self._dirty:
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._cache, f)
        os.replace(tmp, path)  # 원자적 교체로 부분쓰기 방지
        self._dirty = False

    # ------------------------------------------------------------------ #
    def embed(self, texts: list[str]) -> list[list[float]]:
        """캐시 히트는 재사용하고, 미스만 백엔드로 임베딩한다(입력 순서 유지)."""
        if self._dir is None:  # 캐시 비활성 → 그대로 위임
            return [list(v) for v in self._embedder.embed(texts)]
        self._load()
        results: list[Optional[list[float]]] = [None] * len(texts)
        misses: list[int] = []
        for i, t in enumerate(texts):
            cached = self._cache.get(self._key(t))
            if cached is not None:
                results[i] = cached
            else:
                misses.append(i)

        if misses:
            fresh = self._embedder.embed([texts[i] for i in misses])
            for i, vec in zip(misses, fresh):
                vec = list(vec)
                results[i] = vec
                self._cache[self._key(texts[i])] = vec
            self._dirty = True
            self._save()

        return [r if r is not None else [] for r in results]

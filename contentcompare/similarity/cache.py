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
import tempfile
import time
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

        # 임시파일은 같은 디렉터리에 '프로세스마다 고유한' 이름으로 만든다.
        # (고정된 path+'.tmp' 를 쓰면 동시 실행 인스턴스끼리 같은 임시파일을 놓고
        #  충돌해 Windows 에서 WinError 32 가 난다.)
        fd, tmp = tempfile.mkstemp(
            prefix=os.path.basename(path) + ".", suffix=".tmp",
            dir=os.path.dirname(path),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._cache, f)
        except OSError as exc:  # 쓰기 실패: 임시파일 정리 후 경고만(캐시는 선택적 최적화)
            _silent_remove(tmp)
            logger.warning("임베딩 캐시 임시파일 쓰기 실패(무시): %s", exc)
            return

        if _replace_with_retry(tmp, path):
            self._dirty = False
        else:
            # 대상 파일이 다른 프로세스(백신/인덱서/동시 실행)에 잠겨 교체 실패.
            # 캐시 저장만 건너뛰고 비교 실행은 계속한다(다음 저장 때 재시도).
            _silent_remove(tmp)
            logger.warning(
                "임베딩 캐시 저장 건너뜀 — 대상 파일이 사용 중입니다(%s). "
                "동시 실행/백신/동기화(OneDrive 등)를 확인하세요.", path
            )

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


# --------------------------------------------------------------------------- #
def _replace_with_retry(
    src: str, dst: str, *, retries: int = 5, base_delay: float = 0.2
) -> bool:
    """``os.replace`` 를 재시도한다. 성공하면 True, 끝내 실패하면 False.

    Windows 에서는 대상 파일이 백신/검색 인덱서/동기화 도구에 잠깐 잠기면
    교체가 ``PermissionError(WinError 32)`` 로 실패할 수 있는데, 보통 짧은 대기
    후 재시도하면 성공한다.
    """
    for attempt in range(1, retries + 1):
        try:
            os.replace(src, dst)
            return True
        except PermissionError as exc:
            if attempt == retries:
                logger.debug("캐시 교체 재시도 소진: %s", exc)
                return False
            time.sleep(base_delay * attempt)  # 0.2s, 0.4s, 0.6s, ...
        except OSError as exc:  # 그 외 OS 오류는 재시도 의미 없음
            logger.debug("캐시 교체 실패(비재시도): %s", exc)
            return False
    return False


def _silent_remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass

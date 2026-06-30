"""중간 산출물 저장소 (ArtifactStore).

fact 파이프라인의 각 단계 산출물(physical_raw, compact_raw, document_profile, ...)을
``<root>/<doc_slug>/<stage>.json`` 에 **깨끗한 JSON**(다운스트림이 그대로 읽는 순수
데이터)으로 남긴다. 추적·오류원인 분석이 이 설계의 핵심 가치다(결정 #5: 항상 저장).

캐시 유효성은 산출물 파일을 더럽히지 않도록 **사이드카** ``<stage>.fingerprint`` 로
분리한다. :meth:`cached_or_compute` 는 같은 입력이면 재계산 없이 디스크 산출물을
재사용해 LLM 호출 비용을 0 으로 만든다(결정 #2). 주 소비처는 F1+ LLM 단계다.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable, Optional

_ILLEGAL = re.compile(r"[^\w\-]", re.UNICODE)


class ArtifactStore:
    """한 문서의 단계별 산출물을 디스크에 저장/로드/캐싱한다."""

    def __init__(
        self, root: str, doc_name: str, *, enabled: bool = True, cache: bool = True
    ) -> None:
        self.root = Path(root)
        self.doc_slug = self.slug(doc_name)
        self.enabled = enabled
        self.cache = cache

    # ------------------------------------------------------------------ #
    # 경로
    # ------------------------------------------------------------------ #
    @staticmethod
    def slug(doc_name: str) -> str:
        """basename 의 경로불가 문자/점을 ``_`` 로 치환(한글 보존).

        예: ``요약.pptx`` → ``요약_pptx``, ``기준 문서.xlsx`` → ``기준_문서_xlsx``.
        """
        name = os.path.basename(doc_name)
        return _ILLEGAL.sub("_", name)

    @property
    def dir(self) -> Path:
        return self.root / self.doc_slug

    def path(self, stage: str) -> Path:
        return self.dir / f"{stage}.json"

    def _fingerprint_path(self, stage: str) -> Path:
        return self.dir / f"{stage}.fingerprint"

    # ------------------------------------------------------------------ #
    # 저장/로드
    # ------------------------------------------------------------------ #
    def exists(self, stage: str) -> bool:
        return self.path(stage).exists()

    def save(self, stage: str, data: Any) -> Optional[Path]:
        """산출물을 저장한다. ``enabled=False`` 면 아무것도 쓰지 않고 ``None`` 반환.

        dict/list 는 들여쓴 JSON(한글 보존)으로, str 은 그대로 기록한다.
        """
        if not self.enabled:
            return None
        self.dir.mkdir(parents=True, exist_ok=True)
        p = self.path(stage)
        if isinstance(data, str):
            p.write_text(data, encoding="utf-8")
        else:
            p.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        return p

    def load(self, stage: str) -> Optional[dict]:
        """저장된 JSON 산출물을 dict 로 로드한다. 없으면 ``None``."""
        p = self.path(stage)
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    # ------------------------------------------------------------------ #
    # 캐싱 (재실행 0비용 — 결정 #2)
    # ------------------------------------------------------------------ #
    def cached_or_compute(
        self,
        stage: str,
        compute: Callable[[], Any],
        *,
        fingerprint: Optional[str] = None,
    ) -> Any:
        """캐시가 유효하면 디스크 산출물을 재사용, 아니면 compute() 후 저장.

        - ``cache`` 가 켜져 있고 산출물이 존재하며, ``fingerprint`` 가 없거나 사이드카
          지문과 일치하면 → 로드해서 반환(재계산/재호출 없음).
        - 그 외에는 ``compute()`` 를 호출해 산출물을 저장(+지문 기록)하고 반환한다.
        """
        if self.cache and self.exists(stage) and self._fingerprint_ok(stage, fingerprint):
            cached = self.load(stage)
            if cached is not None:
                return cached

        data = compute()
        self.save(stage, data)
        if fingerprint is not None:
            self._write_fingerprint(stage, fingerprint)
        return data

    def _fingerprint_ok(self, stage: str, fingerprint: Optional[str]) -> bool:
        if fingerprint is None:
            return True
        fp = self._fingerprint_path(stage)
        if not fp.exists():
            return False
        return fp.read_text(encoding="utf-8").strip() == fingerprint

    def _write_fingerprint(self, stage: str, fingerprint: str) -> None:
        if not self.enabled:
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        self._fingerprint_path(stage).write_text(fingerprint, encoding="utf-8")

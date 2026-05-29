"""Streamlit UI 의 비(非)화면 로직 — streamlit 의존 없이 단위테스트 가능.

설정 구성, 입력 파일 수집/임시저장, 결과 집계/표 변환을 담당한다.
"""

from __future__ import annotations

import os
from typing import Any, Iterable, Optional, Union

from ..config import AppConfig
from ..models import ComparisonResult, RecordResult, Verdict

Result = Union[ComparisonResult, RecordResult]

# get_reader 와 동일한 지원 확장자.
SUPPORTED_EXTS = (".xlsx", ".xls", ".xlsm", ".docx", ".doc", ".pptx", ".ppt")

VERDICT_LABEL = {
    Verdict.SAME: "✅ 같음",
    Verdict.PARTIAL: "🟡 부분일치",
    Verdict.DIFFERENT: "❌ 다름",
    Verdict.NOT_FOUND: "⚪ 미발견",
}


# --------------------------------------------------------------------------- #
# 설정 구성
# --------------------------------------------------------------------------- #
def build_config(
    *,
    base: Optional[str] = None,
    backend: Optional[str] = None,
    chat_model: Optional[str] = None,
    embed_model: Optional[str] = None,
    granularity: Optional[str] = None,
    recall_k: Optional[int] = None,
    top_k: Optional[int] = None,
    fusion: Optional[str] = None,
    rerank: Optional[bool] = None,
) -> AppConfig:
    """기본 설정(파일/기본값)에 UI 입력을 덮어써 :class:`AppConfig` 를 만든다."""
    config = AppConfig.load(base)
    if backend is not None:
        config.llm.backend = backend
    if chat_model:
        config.llm.chat_model = chat_model
    if embed_model:
        config.llm.embed_model = embed_model
    if granularity:
        config.excel.granularity = granularity
    if recall_k is not None:
        config.similarity.recall_k = recall_k
    if top_k is not None:
        config.similarity.top_k = top_k
    if fusion:
        config.similarity.fusion = fusion
    if rerank is not None:
        config.similarity.rerank = rerank
    return config


# --------------------------------------------------------------------------- #
# 입력 파일
# --------------------------------------------------------------------------- #
def is_supported(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in SUPPORTED_EXTS


def gather_target_paths(folder: str) -> list[str]:
    """폴더 안의 지원 문서 경로를 정렬해 반환(하위폴더 포함). 임시(~$) 파일 제외."""
    if not folder or not os.path.isdir(folder):
        return []
    out: list[str] = []
    for root, _dirs, files in os.walk(folder):
        for name in files:
            if name.startswith("~$"):  # Office 잠금 파일 제외
                continue
            if is_supported(name):
                out.append(os.path.join(root, name))
    return sorted(out)


def save_upload(name: str, data: bytes, dest_dir: str) -> str:
    """업로드된 바이트를 dest_dir 에 원래 파일명으로 저장하고 경로를 반환한다.

    COM(xlwings/win32com) 은 실제 파일 경로가 필요하므로 임시 디렉터리에 떨군다.
    """
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, os.path.basename(name))
    with open(path, "wb") as f:
        f.write(data)
    return path


# --------------------------------------------------------------------------- #
# 결과 집계 / 표 변환
# --------------------------------------------------------------------------- #
def verdict_counts(results: Iterable[Result]) -> dict[Verdict, int]:
    counts = {v: 0 for v in Verdict}
    for r in results:
        counts[r.verdict] += 1
    return counts


def summary_rows(results: Iterable[Result]) -> list[dict[str, Any]]:
    """요약 표(화면용) 행 리스트. streamlit/pandas 없이도 만들 수 있다."""
    rows: list[dict[str, Any]] = []
    for i, r in enumerate(results, start=1):
        rows.append(
            {
                "#": i,
                "기준 항목": _truncate(r.reference.text, 50),
                "판정": VERDICT_LABEL[r.verdict],
                "출처": "; ".join(r.sources) if r.sources else "-",
            }
        )
    return rows


def field_rows(result: RecordResult) -> list[dict[str, Any]]:
    """레코드 결과의 필드별 표 행."""
    by_id = {c.item.item_id: c.item.source_label for c in result.candidates}
    rows: list[dict[str, Any]] = []
    for fr in result.fields:
        srcs = [by_id[m] for m in fr.matched_item_ids if m in by_id]
        rows.append(
            {
                "필드": fr.field.header,
                "기준값": fr.field.value_norm,
                "판정": VERDICT_LABEL[fr.verdict],
                "출처": "; ".join(srcs) if srcs else "-",
                "사유": fr.reasoning,
            }
        )
    return rows


def _truncate(text: str, n: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"

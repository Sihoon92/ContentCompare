"""Streamlit UI 의 비(非)화면 로직 — streamlit 의존 없이 단위테스트 가능.

설정 구성, 입력 파일 수집/임시저장, 결과 집계/표 변환을 담당한다.
"""

from __future__ import annotations

import json
import os
from typing import Any, Iterable, Optional, Union

from ..config import AppConfig
from ..models import ComparisonResult, RecordResult, Verdict

Result = Union[ComparisonResult, RecordResult]

# UI 상태(마지막 사용 설정 경로 등) 저장 파일.
_STATE_FILE = os.path.join(os.path.expanduser("~"), ".contentcompare", "ui_state.json")

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
    auto_header: Optional[bool] = None,
    recall_k: Optional[int] = None,
    top_k: Optional[int] = None,
    fusion: Optional[str] = None,
    rerank: Optional[bool] = None,
    embed_backend: Optional[str] = None,
    embed_model_path: Optional[str] = None,
    embed_prefix: Optional[str] = None,
    embed_cache_dir: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> AppConfig:
    """기본 설정(파일/기본값)에 UI 입력을 덮어써 :class:`AppConfig` 를 만든다.

    None 인 인자는 건드리지 않으므로, config.yaml 에서 읽은 값이 그대로 유지된다.
    """
    config = AppConfig.load(base)
    if backend is not None:
        config.llm.backend = backend
    if chat_model:
        config.llm.chat_model = chat_model
    if embed_model:
        config.llm.embed_model = embed_model
    if embed_backend is not None:
        config.llm.embed_backend = embed_backend
    if embed_model_path is not None:
        config.llm.embed_model_path = embed_model_path
    if embed_prefix is not None:
        config.llm.embed_prefix = embed_prefix
    if embed_cache_dir is not None:
        config.llm.embed_cache_dir = embed_cache_dir
    if base_url:
        config.llm.internal.base_url = base_url
    if api_key:
        config.llm.internal.api_key = api_key
    if granularity:
        config.excel.granularity = granularity
    if auto_header is not None:
        config.excel.auto_header = auto_header
    if recall_k is not None:
        config.similarity.recall_k = recall_k
    if top_k is not None:
        config.similarity.top_k = top_k
    if fusion:
        config.similarity.fusion = fusion
    if rerank is not None:
        config.similarity.rerank = rerank
    return config


def config_to_state(config: AppConfig) -> dict[str, Any]:
    """AppConfig → UI 위젯 상태 dict. config.yaml 선택 시 그 값을 화면에 채운다."""
    llm = config.llm
    sim = config.similarity
    return {
        "backend": llm.backend,
        "embed_backend": llm.embed_backend or "(chat와 동일)",
        "chat_model": llm.chat_model,
        "embed_model": llm.embed_model,
        "embed_model_path": llm.embed_model_path,
        "embed_prefix": llm.embed_prefix,
        "embed_cache_dir": llm.embed_cache_dir,
        "base_url": llm.internal.base_url,
        "api_key": llm.internal.api_key,
        "granularity": config.excel.granularity,
        "auto_header": config.excel.auto_header,
        "recall_k": sim.recall_k,
        "top_k": sim.top_k,
        "fusion": sim.fusion,
        "rerank": sim.rerank,
    }


# --------------------------------------------------------------------------- #
# UI 상태 영속화(마지막 사용 설정 경로 기억)
# --------------------------------------------------------------------------- #
def remember_config_path(path: str, *, state_file: str = _STATE_FILE) -> None:
    """다음 실행 때 자동으로 불러오도록 마지막 config 경로를 저장한다."""
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(state_file), exist_ok=True)
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump({"config_path": path}, f)
    except OSError:
        pass  # 저장 실패는 치명적이지 않음


def recall_config_path(*, state_file: str = _STATE_FILE) -> str:
    """지난 실행에서 저장한 config 경로를 돌려준다(없으면 빈 문자열)."""
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            return str(json.load(f).get("config_path") or "")
    except (OSError, ValueError):
        return ""


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
    """레코드 결과의 열(항목)별 확인 내역 표 행(행 종합 판정의 세부 근거)."""
    rows: list[dict[str, Any]] = []
    for fd in result.findings:
        rows.append(
            {
                "항목(열)": fd.field.header,
                "기준값": fd.field.value_norm,
                "확인": "✅ 있음" if fd.found else "⚪ 없음",
                "근거": fd.note,
            }
        )
    return rows


def _truncate(text: str, n: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"

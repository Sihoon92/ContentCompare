"""ContentCompare 로컬 웹 UI (Streamlit).

실행(사용자 PC, Windows + MS Office 환경):

    pip install -e .[ui]
    streamlit run app/streamlit_app.py

COM 자동화(xlwings/win32com)는 데스크톱 세션이 필요하므로, 이 앱은 사용자 PC 의
localhost 에서 구동하는 것을 전제로 한다. 입력은 로컬 경로 직접 지정(권장) 또는
파일 업로드 모두 지원한다.
"""

from __future__ import annotations

import logging
import os

import streamlit as st

from contentcompare.config import AppConfig
from contentcompare.llm.health import all_ok, check_llm
from contentcompare.logging_setup import read_log_text, setup_logging
from contentcompare.pipeline import ComparePipeline
from contentcompare.models import RecordResult, Verdict
from contentcompare.report import render_markdown
from contentcompare.ui import runner

logger = logging.getLogger("contentcompare.ui")

st.set_page_config(page_title="ContentCompare", page_icon="📑", layout="wide")

LLM_BACKENDS = ["ollama", "internal", "langchain"]
EMBED_BACKENDS = ["(chat와 동일)", "fastembed", "onnx", "ollama", "internal", "langchain"]
GRANULARITY = ["hybrid", "field", "row"]
FUSION = ["rrf", "cosine"]

# 위젯 키별 기본값(세션 상태 초기화용).
_DEFAULTS = {
    "backend": "ollama",
    "embed_backend": "(chat와 동일)",
    "chat_model": "qwen2.5:14b",
    "embed_model": "bge-m3",
    "embed_model_path": "",
    "embed_prefix": "",
    "embed_cache_dir": "",
    "base_url": "https://llm.intra.corp/v1",
    "api_key": "",
    "granularity": "hybrid",
    "auto_header": False,
    "recall_k": 30,
    "top_k": 10,
    "fusion": "rrf",
    "rerank": False,
}


def _init_state() -> None:
    for k, v in _DEFAULTS.items():
        st.session_state.setdefault(k, v)


def _load_config_into_state(path: str) -> None:
    """config.yaml 을 읽어 위젯 상태(session_state)에 채운다."""
    config = AppConfig.load(path or None)
    for k, v in runner.config_to_state(config).items():
        st.session_state[k] = v


def _tk_dialog(kind: str, **kwargs):
    """네이티브 선택 창(로컬 데스크톱 전용). kind: open|opens|dir.

    실패(GUI 불가)하면 경고 후 None 반환 → 경로 직접 입력으로 폴백.
    """
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.wm_attributes("-topmost", 1)
        fn = {
            "open": filedialog.askopenfilename,
            "opens": filedialog.askopenfilenames,
            "dir": filedialog.askdirectory,
        }[kind]
        result = fn(**kwargs)
        root.destroy()
        return result
    except Exception as exc:  # noqa: BLE001 - GUI 불가 환경
        st.warning(f"선택 창을 열 수 없습니다({exc}). 경로를 직접 입력하세요.")
        return None


_DOC_TYPES = [
    ("문서", ("*.xlsx", "*.xls", "*.xlsm", "*.docx", "*.doc", "*.pptx", "*.ppt")),
    ("모든 파일", "*.*"),
]
_XLS_TYPES = [("Excel", ("*.xlsx", "*.xls", "*.xlsm")), ("모든 파일", "*.*")]


def _pick_config_file() -> str:
    """config.yaml 경로를 네이티브 창으로 선택."""
    path = _tk_dialog("open", title="config.yaml 선택",
                      filetypes=[("YAML", ("*.yaml", "*.yml")), ("모든 파일", "*.*")])
    return path or ""


# --------------------------------------------------------------------------- #
# 사이드바: 설정
# --------------------------------------------------------------------------- #
def sidebar_config() -> AppConfig:
    _init_state()
    st.sidebar.header("⚙️ 설정")

    # 최초 진입 시: 지난 실행에서 쓰던 config 를 자동으로 불러온다.
    if not st.session_state.get("_autoloaded"):
        st.session_state["_autoloaded"] = True
        last = runner.recall_config_path()
        if last and os.path.exists(last):
            st.session_state["cfg_path"] = last
            try:
                _load_config_into_state(last)
            except Exception:  # noqa: BLE001
                pass

    # 버튼은 text_input 보다 먼저 — 클릭 시 cfg_path 상태를 갱신해야 하므로.
    c1, c2 = st.sidebar.columns(2)
    if c1.button("📁 파일 선택", use_container_width=True):
        picked = _pick_config_file()
        if picked:
            st.session_state["cfg_path"] = picked
            try:
                _load_config_into_state(picked)
                runner.remember_config_path(picked)
            except Exception as exc:  # noqa: BLE001
                st.sidebar.error(f"불러오기 실패: {exc}")
            st.rerun()
    if c2.button("📂 불러오기", use_container_width=True):
        p = st.session_state.get("cfg_path", "")
        try:
            _load_config_into_state(p)
            runner.remember_config_path(p)
            st.sidebar.success("설정을 불러왔습니다.")
        except Exception as exc:  # noqa: BLE001
            st.sidebar.error(f"불러오기 실패: {exc}")
        st.rerun()

    st.sidebar.text_input("config.yaml 경로", key="cfg_path",
                          placeholder=r"C:\path\config.yaml")
    cfg_path = st.session_state.get("cfg_path", "")

    st.sidebar.divider()
    st.sidebar.subheader("LLM (대화)")
    backend = st.sidebar.selectbox("LLM 백엔드", LLM_BACKENDS, key="backend")
    st.sidebar.text_input("chat 모델", key="chat_model")
    if backend in ("internal", "langchain"):
        st.sidebar.text_input("base_url", key="base_url")
        st.sidebar.text_input("api_key", key="api_key", type="password")

    st.sidebar.subheader("임베딩 (유사도)")
    embed_backend = st.sidebar.selectbox("임베딩 백엔드", EMBED_BACKENDS, key="embed_backend")
    st.sidebar.text_input("embed 모델", key="embed_model")
    if embed_backend == "onnx":
        st.sidebar.text_input("ONNX 모델 폴더 경로", key="embed_model_path")
        st.sidebar.text_input("입력 prefix(e5는 'query: ')", key="embed_prefix")
    elif embed_backend == "fastembed":
        st.sidebar.text_input("fastembed 캐시 폴더(오프라인)", key="embed_cache_dir")

    st.sidebar.divider()
    st.sidebar.subheader("검색/분해")
    st.sidebar.selectbox("엑셀 분해", GRANULARITY, key="granularity",
                         help="hybrid=행 검색+셀 판정, field=셀 단위, row=행 단위")
    st.sidebar.checkbox("헤더 자동 추정(LLM)", key="auto_header",
                        help="상위 행을 LLM 이 보고 헤더 시작/행수를 추정(대외비·멀티헤더 대응)")
    st.sidebar.slider("recall_k(1차 후보)", 5, 100, key="recall_k", step=5)
    st.sidebar.slider("top_k(LLM 투입)", 1, 30, key="top_k")
    st.sidebar.selectbox("검색 융합", FUSION, key="fusion")
    st.sidebar.checkbox("재랭킹(rerank)", key="rerank")

    # "(chat와 동일)" → "" 로 변환.
    eb = "" if st.session_state["embed_backend"] == "(chat와 동일)" else st.session_state["embed_backend"]
    config = runner.build_config(
        base=cfg_path or None,
        backend=st.session_state["backend"],
        chat_model=st.session_state["chat_model"],
        embed_model=st.session_state["embed_model"],
        embed_backend=eb,
        embed_model_path=st.session_state["embed_model_path"],
        embed_prefix=st.session_state["embed_prefix"],
        embed_cache_dir=st.session_state["embed_cache_dir"],
        base_url=st.session_state["base_url"],
        api_key=st.session_state["api_key"],
        granularity=st.session_state["granularity"],
        auto_header=st.session_state["auto_header"],
        recall_k=st.session_state["recall_k"],
        top_k=st.session_state["top_k"],
        fusion=st.session_state["fusion"],
        rerank=st.session_state["rerank"],
    )

    st.sidebar.divider()
    if st.sidebar.button("🔌 LLM 연결 테스트", use_container_width=True):
        with st.sidebar.status("점검 중...", expanded=True):
            checks = check_llm(config)
            for r in checks:
                (st.sidebar.success if r.ok else st.sidebar.error)(r.line())
        st.sidebar.caption(
            "모두 ✅ 면 비교 실행 준비 완료" if all_ok(checks)
            else "실패 항목의 메시지를 확인하세요"
        )

    return config


# --------------------------------------------------------------------------- #
# 입력 수집 (로컬 경로 우선 — 사내망 업로더 오류 회피)
# --------------------------------------------------------------------------- #
def collect_inputs():
    """기준 1개 + 대상 N개의 (경로) 를 반환. 네이티브 선택 창으로 경로만 불러온다.

    업로드(파일 복사)가 아니라 **원본 경로**를 그대로 사용 → xlwings/win32com 이
    원본을 직접 연다(사내 보안/DRM 환경 친화적).
    """
    st.session_state.setdefault("ref_path", "")
    st.session_state.setdefault("target_dir", "")
    st.session_state.setdefault("target_list", [])

    st.info("📁 버튼으로 파일/폴더를 고르면 **경로만** 불러옵니다(원본을 직접 엽니다. 업로드/복사 아님).")

    # --- 기준 엑셀 --- (버튼을 text_input 보다 먼저: 클릭 시 상태 갱신) ---
    st.subheader("1) 기준 엑셀")
    if st.button("📁 기준 엑셀 선택", key="pick_ref"):
        p = _tk_dialog("open", title="기준 엑셀 선택", filetypes=_XLS_TYPES)
        if p:
            st.session_state["ref_path"] = p
            st.rerun()
    st.text_input("기준 엑셀 경로", key="ref_path", placeholder=r"C:\data\기준.xlsx")
    ref_path = st.session_state.get("ref_path", "").strip()

    # --- 대상 문서들 ---
    st.subheader("2) 대상 문서들")
    c1, c2, c3 = st.columns(3)
    if c1.button("📁 파일 선택(여러 개)", key="pick_tgts"):
        ps = _tk_dialog("opens", title="대상 문서 선택(여러 개)", filetypes=_DOC_TYPES)
        if ps:
            st.session_state["target_list"] = list(ps)
            st.rerun()
    if c2.button("📂 폴더 선택", key="pick_dir"):
        d = _tk_dialog("dir", title="대상 폴더 선택")
        if d:
            st.session_state["target_dir"] = d
            st.rerun()
    if c3.button("🗑️ 목록 비우기", key="clear_tgts"):
        st.session_state["target_list"] = []
        st.session_state["target_dir"] = ""
        st.rerun()

    st.text_input("대상 폴더 경로(하위 폴더 포함)", key="target_dir",
                  placeholder=r"C:\data\대상문서")

    # 선택 파일 + 폴더 수집을 합치고 중복 제거(순서 보존).
    target_paths: list[str] = list(st.session_state.get("target_list", []))
    folder = st.session_state.get("target_dir", "").strip()
    if folder:
        target_paths.extend(runner.gather_target_paths(folder))
    seen: set[str] = set()
    target_paths = [p for p in target_paths if not (p in seen or seen.add(p))]

    if target_paths:
        st.caption(f"대상 {len(target_paths)}개")
        st.code("\n".join(target_paths), language=None)

    return ref_path, target_paths


# --------------------------------------------------------------------------- #
# 결과 표시
# --------------------------------------------------------------------------- #
def show_results(results, reference_doc, target_docs):
    counts = runner.verdict_counts(results)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("✅ 같음", counts[Verdict.SAME])
    c2.metric("🟡 부분일치", counts[Verdict.PARTIAL])
    c3.metric("❌ 다름", counts[Verdict.DIFFERENT])
    c4.metric("⚪ 미발견", counts[Verdict.NOT_FOUND])

    st.subheader("요약")
    st.dataframe(runner.summary_rows(results), use_container_width=True, hide_index=True)

    st.subheader("상세")
    for i, r in enumerate(results, start=1):
        label = runner.VERDICT_LABEL[r.verdict]
        with st.expander(f"{i}. {r.reference.source_label} — {label}"):
            st.markdown(f"**기준 내용**: {r.reference.text}")
            if isinstance(r, RecordResult):
                st.dataframe(runner.field_rows(r), use_container_width=True, hide_index=True)
            else:
                st.markdown(f"**판단 근거**: {r.reasoning}")
            if r.candidates:
                st.markdown("**검색된 후보**")
                for c in r.candidates:
                    mark = " ⟵ 매칭" if c.item.item_id in r.matched_item_ids else ""
                    st.markdown(f"- ({c.score:.3f}) {c.item.source_label}{mark}")

    md = render_markdown(results, reference_doc=reference_doc, target_docs=target_docs)
    st.download_button(
        "📥 리포트(.md) 다운로드", data=md, file_name="report.md", mime="text/markdown"
    )


# --------------------------------------------------------------------------- #
def _log_panel():
    """사이드바: 로그 파일 경로 + 보기/다운로드."""
    log_path = st.session_state.get("log_path", "")
    st.sidebar.divider()
    st.sidebar.caption(f"🧾 로그: {log_path}")
    with st.sidebar.expander("로그 보기/다운로드"):
        text = read_log_text()
        st.code(text or "(아직 로그 없음)", language=None)
        if text:
            st.download_button("로그 다운로드", data=text,
                               file_name=os.path.basename(log_path), mime="text/plain")


def main():
    st.title("📑 ContentCompare")
    st.caption("엑셀 기준 문서를 여러 대상 문서와 대조 — 항목별 같음/다름·출처·사유")

    # 실행 로그를 파일로 저장(세션 1회).
    if "log_path" not in st.session_state:
        st.session_state["log_path"] = setup_logging()

    config = sidebar_config()
    ref_path, target_paths = collect_inputs()
    _log_panel()

    if st.button("🚀 비교 실행", type="primary"):
        if not ref_path:
            st.error("기준 엑셀 경로를 입력(또는 업로드)하세요.")
            return
        if not target_paths:
            st.error("대상 문서를 하나 이상 지정하세요.")
            return

        progress = st.progress(0.0)
        status = st.empty()

        def on_progress(i, total, result):
            progress.progress(i / total)
            status.text(f"[{i}/{total}] {result.verdict.value} — {result.reference.source_label}")

        logger.info("비교 실행 시작: 기준=%s, 대상=%d개", ref_path, len(target_paths))
        try:
            pipeline = ComparePipeline(config)
            results = pipeline.run(ref_path, target_paths, progress=on_progress)
            logger.info("비교 실행 완료: %d개 항목", len(results))
        except Exception as exc:  # noqa: BLE001 - 사용자에게 오류 노출
            logger.exception("비교 실행 실패")
            st.exception(exc)
            st.error("자세한 원인은 아래 로그를 확인하세요(사이드바 '로그 보기'에서도 가능).")
            st.code(read_log_text(8000), language=None)
            return
        finally:
            progress.empty()

        st.success(f"완료: {len(results)}개 항목 비교")
        show_results(
            results,
            reference_doc=os.path.basename(ref_path),
            target_docs=[os.path.basename(p) for p in target_paths],
        )


if __name__ == "__main__":
    main()

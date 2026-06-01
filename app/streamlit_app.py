"""ContentCompare 로컬 웹 UI (Streamlit).

실행(사용자 PC, Windows + MS Office 환경):

    pip install -e .[ui]
    streamlit run app/streamlit_app.py

COM 자동화(xlwings/win32com)는 데스크톱 세션이 필요하므로, 이 앱은 사용자 PC 의
localhost 에서 구동하는 것을 전제로 한다. 입력은 로컬 경로 직접 지정(권장) 또는
파일 업로드 모두 지원한다.
"""

from __future__ import annotations

import os
import tempfile

import streamlit as st

from contentcompare.config import AppConfig
from contentcompare.llm.health import all_ok, check_llm
from contentcompare.pipeline import ComparePipeline
from contentcompare.models import RecordResult, Verdict
from contentcompare.report import render_markdown
from contentcompare.ui import runner

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


def _pick_config_file() -> str:
    """네이티브 파일 선택 창을 띄워 config.yaml 경로를 받는다(로컬 데스크톱 전용).

    tkinter 가 없거나 실패하면 빈 문자열을 반환하고, 경로 직접 입력으로 폴백한다.
    """
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.wm_attributes("-topmost", 1)
        path = filedialog.askopenfilename(
            title="config.yaml 선택",
            filetypes=[("YAML", ("*.yaml", "*.yml")), ("모든 파일", "*.*")],
        )
        root.destroy()
        return path or ""
    except Exception as exc:  # noqa: BLE001 - GUI 불가 환경
        st.sidebar.warning(f"파일 선택 창을 열 수 없습니다({exc}). 경로를 직접 입력하세요.")
        return ""


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
def collect_inputs(tmp_dir: str):
    """기준 1개 + 대상 N개의 (경로) 를 반환."""
    st.info(
        "💡 사내 보안(nasca)/DRM 문서는 **경로 입력**을 사용하세요. "
        "업로드는 파일을 임시 폴더에 복사하는데, 보안정책이 이를 막거나(Permission denied) "
        "DRM 문서는 원본 위치에서만 열리기 때문입니다. 경로 입력은 원본을 직접 엽니다."
    )
    st.subheader("1) 기준 엑셀")
    ref_path = st.text_input("기준 엑셀 경로", value="", placeholder=r"C:\data\기준.xlsx").strip()

    st.subheader("2) 대상 문서들")
    folder = st.text_input("대상 폴더 경로(하위 폴더 포함)", value="",
                           placeholder=r"C:\data\대상문서").strip()
    target_paths: list[str] = []
    if folder:
        found = runner.gather_target_paths(folder)
        st.caption(f"폴더에서 {len(found)}개 문서 발견")
        target_paths.extend(found)

    # 업로드는 보조 수단(사내 보안/DRM, 업로더 모듈 오류 시 실패할 수 있음).
    with st.expander("📎 또는 파일 업로드 (일반 환경에서만 권장)"):
        st.caption(
            "업로드는 파일을 임시 폴더에 복사합니다. 사내 보안(nasca)/DRM 환경에서는 "
            "'Permission denied' 가 날 수 있으니 위의 경로 입력을 사용하세요."
        )
        ref_up = st.file_uploader("기준 엑셀", type=["xlsx", "xls", "xlsm"], key="ref_up")
        tgt_ups = st.file_uploader(
            "대상 문서(여러 개)",
            type=["xlsx", "xls", "xlsm", "docx", "doc", "pptx", "ppt"],
            accept_multiple_files=True, key="tgt_ups",
        )
        try:
            if ref_up is not None and not ref_path:
                ref_path = runner.save_upload(ref_up.name, ref_up.getbuffer(), tmp_dir)
            for up in tgt_ups or []:
                target_paths.append(runner.save_upload(up.name, up.getbuffer(), tmp_dir))
        except OSError as exc:
            st.error(
                f"업로드 파일 저장 실패: {exc}\n\n"
                "사내 보안정책(nasca)/DRM 으로 임시 저장이 막혔을 수 있습니다. "
                "업로드 대신 **위의 파일 경로 입력**을 사용하세요(원본을 직접 엽니다)."
            )

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
def main():
    st.title("📑 ContentCompare")
    st.caption("엑셀 기준 문서를 여러 대상 문서와 대조 — 항목별 같음/다름·출처·사유")

    config = sidebar_config()

    if "tmp_dir" not in st.session_state:
        st.session_state.tmp_dir = tempfile.mkdtemp(prefix="contentcompare_")
    ref_path, target_paths = collect_inputs(st.session_state.tmp_dir)

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

        try:
            pipeline = ComparePipeline(config)
            results = pipeline.run(ref_path, target_paths, progress=on_progress)
        except Exception as exc:  # noqa: BLE001 - 사용자에게 오류 노출
            st.exception(exc)
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

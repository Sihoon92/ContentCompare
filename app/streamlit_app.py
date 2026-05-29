"""ContentCompare 로컬 웹 UI (Streamlit).

실행(사용자 PC, Windows + MS Office 환경):

    pip install -e .[ui]
    streamlit run app/streamlit_app.py

COM 자동화(xlwings/win32com)는 데스크톱 세션이 필요하므로, 이 앱은 사용자 PC 의
localhost 에서 구동하는 것을 전제로 한다. 입력은 파일 업로드(임시 저장) 또는
로컬 경로 직접 지정 모두 지원한다.
"""

from __future__ import annotations

import os
import tempfile

import streamlit as st

from contentcompare.pipeline import ComparePipeline
from contentcompare.models import RecordResult, Verdict
from contentcompare.report import render_markdown
from contentcompare.ui import runner

st.set_page_config(page_title="ContentCompare", page_icon="📑", layout="wide")


# --------------------------------------------------------------------------- #
# 사이드바: 설정
# --------------------------------------------------------------------------- #
def sidebar_config():
    st.sidebar.header("⚙️ 설정")
    base = st.sidebar.text_input("config.yaml 경로(선택)", value="")
    backend = st.sidebar.selectbox("LLM 백엔드", ["ollama", "internal"], index=0)
    chat_model = st.sidebar.text_input("chat 모델", value="qwen2.5:14b")
    embed_model = st.sidebar.text_input("embed 모델", value="bge-m3")

    st.sidebar.divider()
    granularity = st.sidebar.selectbox(
        "엑셀 분해", ["hybrid", "field", "row"], index=0,
        help="hybrid=행 검색+셀 판정, field=셀 단위, row=행 단위",
    )
    recall_k = st.sidebar.slider("recall_k(1차 후보)", 5, 100, 30, 5)
    top_k = st.sidebar.slider("top_k(LLM 투입)", 1, 30, 10, 1)
    fusion = st.sidebar.selectbox("검색 융합", ["rrf", "cosine"], index=0)
    rerank = st.sidebar.checkbox("재랭킹(rerank)", value=False)

    return runner.build_config(
        base=base or None,
        backend=backend,
        chat_model=chat_model,
        embed_model=embed_model,
        granularity=granularity,
        recall_k=recall_k,
        top_k=top_k,
        fusion=fusion,
        rerank=rerank,
    )


# --------------------------------------------------------------------------- #
# 입력 수집
# --------------------------------------------------------------------------- #
def collect_inputs(tmp_dir: str):
    """기준 1개 + 대상 N개의 (경로) 를 반환. 업로드 또는 로컬 경로 지원."""
    st.subheader("1) 기준 엑셀")
    ref_path = ""
    ref_up = st.file_uploader("기준 엑셀 업로드", type=["xlsx", "xls", "xlsm"], key="ref")
    ref_text = st.text_input("또는 기준 엑셀 경로 직접 입력", value="")
    if ref_up is not None:
        ref_path = runner.save_upload(ref_up.name, ref_up.getbuffer(), tmp_dir)
    elif ref_text.strip():
        ref_path = ref_text.strip()

    st.subheader("2) 대상 문서들")
    target_paths: list[str] = []
    tgt_ups = st.file_uploader(
        "대상 문서 업로드(여러 개)",
        type=["xlsx", "xls", "xlsm", "docx", "doc", "pptx", "ppt"],
        accept_multiple_files=True,
        key="tgts",
    )
    folder = st.text_input("또는 대상 폴더 경로(하위 폴더 포함)", value="")
    for up in tgt_ups or []:
        target_paths.append(runner.save_upload(up.name, up.getbuffer(), tmp_dir))
    if folder.strip():
        found = runner.gather_target_paths(folder.strip())
        st.caption(f"폴더에서 {len(found)}개 문서 발견")
        target_paths.extend(found)

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
            st.error("기준 엑셀을 업로드하거나 경로를 입력하세요.")
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

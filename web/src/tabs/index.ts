import type { ComponentType } from "react";

export interface TabDef {
  key: string;
  title: string;
  Component: ComponentType;
}

// 탭은 Streamlit 과 같은 순서로 채운다: 🚀 비교 실행 · 📄 리포트 · 🔬 현미경 · ⏱ 타임라인 · 📚 도메인 지식.
export const TABS: TabDef[] = [];

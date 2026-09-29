import type { ComponentType } from "react";
import RunTab from "./RunTab";
import ReportTab from "./ReportTab";
import MicroTab from "./MicroTab";
import TimelineTab from "./TimelineTab";
import KnowledgeTab from "./KnowledgeTab";

export interface TabDef {
  key: string;
  title: string;
  // active: 지금 보이는 탭인가. 조회 탭은 활성화될 때마다 목록을 다시 읽는다.
  Component: ComponentType<{ active: boolean }>;
}

// 탭은 Streamlit 과 같은 순서로 채운다: 🚀 비교 실행 · 📄 리포트 · 🔬 현미경 · ⏱ 타임라인 · 📚 도메인 지식.
export const TABS: TabDef[] = [
  { key: "run", title: "🚀 비교 실행", Component: RunTab },
  { key: "report", title: "📄 리포트 보기", Component: ReportTab },
  { key: "micro", title: "🔬 파이프라인 현미경", Component: MicroTab },
  { key: "timeline", title: "⏱ 타임라인", Component: TimelineTab },
  { key: "knowledge", title: "📚 도메인 지식", Component: KnowledgeTab },
];

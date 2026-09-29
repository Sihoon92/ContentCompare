import { useState } from "react";
import LlmPanel from "../components/LlmPanel";
import { TABS } from "../tabs";

export default function MainPage() {
  const [active, setActive] = useState(TABS[0]?.key ?? "");
  return (
    <div className="layout">
      <LlmPanel />
      <main className="main">
        <nav className="tabs">
          {TABS.map((t) => (
            <button key={t.key} className={t.key === active ? "tab active" : "tab"} onClick={() => setActive(t.key)}>
              {t.title}
            </button>
          ))}
        </nav>
        {TABS.length === 0 && <p className="muted">표시할 탭이 없습니다.</p>}
        {/* 탭을 숨기기만 하고 내리지 않는다 — 고른 파일·입력한 내용이 탭을 옮겨도 남게(Streamlit 과 같다). */}
        {TABS.map((t) => (
          <section key={t.key} className="tab-body" hidden={t.key !== active}>
            <t.Component />
          </section>
        ))}
      </main>
    </div>
  );
}

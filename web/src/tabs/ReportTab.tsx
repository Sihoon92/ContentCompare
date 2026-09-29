import { useCallback, useEffect, useState } from "react";
import { errorText } from "../api/client";
import { getReport, listReports } from "../api/endpoints";
import type { ReportItem } from "../api/types";
import Markdown from "../components/Markdown";

export default function ReportTab() {
  const [items, setItems] = useState<ReportItem[]>([]);
  const [picked, setPicked] = useState("");
  const [markdown, setMarkdown] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(() => {
    listReports()
      .then((r) => {
        setItems(r.reports);
        setPicked((prev) => (prev && r.reports.some((i) => i.id === prev) ? prev : r.reports[0]?.id ?? ""));
        setError("");
      })
      .catch((e) => setError(errorText(e)));
  }, []);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (!picked) { setMarkdown(""); return; }
    getReport(picked).then((r) => setMarkdown(r.markdown)).catch((e) => setError(errorText(e)));
  }, [picked]);

  function download() {
    const name = picked.split("/").pop() || "report";
    const url = URL.createObjectURL(new Blob([markdown], { type: "text/markdown;charset=utf-8" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = name.endsWith(".md") ? name : `report_${name}.md`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div>
      <div className="row">
        <select value={picked} onChange={(e) => setPicked(e.target.value)}>
          {items.map((i) => <option key={i.id} value={i.id}>{i.name}</option>)}
        </select>
        <button className="button secondary" onClick={load}>새로고침</button>
        <button className="button secondary" disabled={!markdown} onClick={download}>📥 이 리포트 다운로드</button>
      </div>
      {items.length === 0 && <p className="muted">아직 저장된 리포트가 없습니다. 비교를 실행하면 여기에 나타납니다.</p>}
      {error && <p className="error">{error}</p>}
      <Markdown text={markdown} />
    </div>
  );
}

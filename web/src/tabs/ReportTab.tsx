import { useCallback, useEffect, useRef, useState } from "react";
import { errorText } from "../api/client";
import { getReport, listReports } from "../api/endpoints";
import type { ReportItem } from "../api/types";
import Markdown from "../components/Markdown";

export default function ReportTab({ active }: { active: boolean }) {
  const [items, setItems] = useState<ReportItem[]>([]);
  const [picked, setPicked] = useState("");
  const [markdown, setMarkdown] = useState("");
  const [error, setError] = useState("");
  const pickedByUser = useRef(false); // 이번 활성화 동안 사용자가 직접 고른 적이 있는가

  const load = useCallback(() => {
    let cancelled = false;
    listReports()
      .then((r) => {
        if (!cancelled) {
          setItems(r.reports);
          // 선택 규칙: 이번 활성화 동안 직접 고른 항목이 목록에 있으면 유지, 아니면 최신(첫 항목).
          setPicked((prev) => (pickedByUser.current && prev && r.reports.some((i) => i.id === prev) ? prev : r.reports[0]?.id ?? ""));
          setError("");
        }
      })
      .catch((e) => { if (!cancelled) setError(errorText(e)); });
    return () => { cancelled = true; };
  }, []);

  // 탭이 보이게 될 때마다 목록을 다시 읽는다(비교를 실행한 뒤 새 리포트가 보이도록). 처음 보이기 전에는 요청하지 않는다.
  useEffect(() => {
    if (!active) return;
    pickedByUser.current = false;
    return load();
  }, [active, load]);

  useEffect(() => {
    if (!picked) { setMarkdown(""); return; }
    setMarkdown(""); // 새 리포트를 선택했을 때 즉시 내용을 지워 다운로드 버튼을 비활성화
    let cancelled = false;
    getReport(picked)
      .then((r) => { if (!cancelled) { setMarkdown(r.markdown); setError(""); } })
      .catch((e) => { if (!cancelled) setError(errorText(e)); });
    return () => { cancelled = true; };
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
        <select value={picked} onChange={(e) => { pickedByUser.current = true; setPicked(e.target.value); }}>
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

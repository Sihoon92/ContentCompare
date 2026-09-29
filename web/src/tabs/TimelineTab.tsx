import { useCallback, useEffect, useRef, useState } from "react";
import { errorText } from "../api/client";
import { getTimelineHtml, listTimelines } from "../api/endpoints";
import type { TimelineItem } from "../api/types";
import HtmlFrame from "../components/HtmlFrame";

export default function TimelineTab({ active }: { active: boolean }) {
  const [items, setItems] = useState<TimelineItem[]>([]);
  const [picked, setPicked] = useState("");
  const [errorsOnly, setErrorsOnly] = useState(false);
  const [html, setHtml] = useState("");
  const [error, setError] = useState("");
  const pickedByUser = useRef(false); // 이번 활성화 동안 사용자가 직접 고른 적이 있는가

  const load = useCallback(() => {
    let cancelled = false;
    listTimelines()
      .then((r) => {
        if (!cancelled) {
          setItems(r.timelines);
          // 선택 규칙: 이번 활성화 동안 직접 고른 항목이 목록에 있으면 유지, 아니면 최신(첫 항목).
          setPicked((prev) => (pickedByUser.current && prev && r.timelines.some((i) => i.id === prev) ? prev : r.timelines[0]?.id ?? ""));
          setError("");
        }
      })
      .catch((e) => { if (!cancelled) setError(errorText(e)); });
    return () => { cancelled = true; };
  }, []);

  // 탭이 보이게 될 때마다 목록을 다시 읽는다. 처음 보이기 전에는 목록도 서버가 만든 HTML 도 요청하지 않는다.
  useEffect(() => {
    if (!active) return;
    pickedByUser.current = false;
    return load();
  }, [active, load]);

  useEffect(() => {
    if (!active) return;
    if (!picked) { setHtml(""); return; }
    let cancelled = false;
    getTimelineHtml(picked, errorsOnly)
      .then((r) => { if (!cancelled) { setHtml(r.html); setError(""); } })
      .catch((e) => { if (!cancelled) setError(errorText(e)); });
    return () => { cancelled = true; };
  }, [active, picked, errorsOnly]);

  return (
    <div>
      <p className="muted">
        단계 시작·종료, LLM 호출, 재시도, 한도 대기를 한 시간축에 놓는다. 실패했을 때 '어느 문서 · 어느 단계 · 몇 번째 배치 · 왜'를 여기서 읽는다.
      </p>
      <div className="row">
        <select value={picked} onChange={(e) => { pickedByUser.current = true; setPicked(e.target.value); }}>
          {items.map((i) => <option key={i.id} value={i.id}>{i.label}</option>)}
        </select>
        <label><input type="checkbox" checked={errorsOnly} onChange={(e) => setErrorsOnly(e.target.checked)} /> 실패·재시도만</label>
        <button className="button secondary" onClick={load}>새로고침</button>
      </div>
      {items.length === 0 && <p className="muted">타임라인이 없습니다. 비교를 한 번 실행하면 여기에 나타납니다.</p>}
      {error && <p className="error">{error}</p>}
      {html && <HtmlFrame title="타임라인" html={html} height={720} />}
    </div>
  );
}

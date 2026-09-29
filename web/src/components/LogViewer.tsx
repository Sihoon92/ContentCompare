import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, errorText } from "../api/client";
import { adminLogDownloadUrl, adminLogSources, readAdminLog } from "../api/endpoints";
import type { LogPage, LogSource } from "../api/types";

const LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"];

function isUnauthorized(e: unknown): boolean {
  return e instanceof ApiError && e.status === 401;
}

// 작업 로그(DEBUG)에는 프롬프트·LLM 원문이 있다 — 그래서 이 화면만 비밀번호로 잠근다.
export default function LogViewer({ onUnauthorized }: { onUnauthorized: () => void }) {
  const [sources, setSources] = useState<LogSource[]>([]);
  const [source, setSource] = useState("");
  const [level, setLevel] = useState("INFO");
  const [query, setQuery] = useState("");
  const [tail, setTail] = useState(500);
  const [page, setPage] = useState<LogPage | null>(null);
  const [follow, setFollow] = useState(true);
  const [error, setError] = useState("");
  // 요청 순번 — 응답은 "아직 가장 최근 요청"일 때만 반영한다(늦게 온 옛 응답이 새 상태를 덮지 않게).
  const seq = useRef(0);

  // 관리자 세션이 끝났으면(401) 오류를 보이는 대신 부모에게 알려 로그인 화면으로 돌린다.
  const fail = useCallback((e: unknown) => {
    if (isUnauthorized(e)) onUnauthorized();
    else setError(errorText(e));
  }, [onUnauthorized]);

  const loadSources = useCallback(() => {
    adminLogSources()
      .then((r) => {
        setSources(r.sources);
        setSource((prev) => (prev && r.sources.some((s) => s.id === prev) ? prev : r.sources[0]?.id ?? ""));
      })
      .catch(fail);
  }, [fail]);

  const loadTail = useCallback(() => {
    const mine = ++seq.current; // 조건이 바뀌어 다시 불릴 때 진행 중이던 "더 보기"도 여기서 무효가 된다
    if (!source) return;
    readAdminLog({ source, level, q: query, tail })
      .then((p) => {
        if (mine !== seq.current) return;
        setPage(p);
        setError("");
      })
      .catch((e) => { if (mine === seq.current || isUnauthorized(e)) fail(e); });
  }, [source, level, query, tail, fail]);

  useEffect(() => { loadSources(); }, [loadSources]);
  useEffect(() => { loadTail(); }, [loadTail]);
  useEffect(() => {
    if (!follow) return;
    const timer = window.setInterval(loadTail, 3000);
    return () => window.clearInterval(timer);
  }, [follow, loadTail]);
  // 화면을 떠나면 진행 중인 응답을 모두 무효로 한다.
  useEffect(() => () => { seq.current += 1; }, []);

  function loadEarlier() {
    if (!page || page.start === 0) return;
    setFollow(false); // 앞쪽을 보는 중에는 새 줄로 바꾸지 않는다
    const mine = ++seq.current; // 이미 날아간 새로고침 응답이 늘어난 페이지를 덮지 못하게 한다
    const current = page;
    readAdminLog({ source, level, q: query, tail, before: current.start })
      .then((p) => {
        if (mine !== seq.current) return;
        setPage({ ...p, records: [...p.records, ...current.records], end: current.end });
        setError("");
      })
      .catch((e) => { if (mine === seq.current || isUnauthorized(e)) fail(e); });
  }

  return (
    <section className="admin-section">
      <h3>로그</h3>
      <div className="row">
        <select value={source} onChange={(e) => setSource(e.target.value)}>
          {sources.map((s) => <option key={s.id} value={s.id}>{s.label} ({Math.ceil(s.size / 1024)} KB)</option>)}
        </select>
        <select value={level} onChange={(e) => setLevel(e.target.value)}>
          {LEVELS.map((l) => <option key={l} value={l}>{l} 이상</option>)}
        </select>
        <input type="search" placeholder="검색어" value={query} onChange={(e) => setQuery(e.target.value)} />
        <label>줄 수 <input type="number" min={50} max={5000} step={50} value={tail}
          onChange={(e) => setTail(Number(e.target.value) || 500)} /></label>
        <label><input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> 새 줄 따라가기</label>
        <button className="button secondary" onClick={() => { loadSources(); loadTail(); }}>새로고침</button>
        {source && <a className="button secondary" href={adminLogDownloadUrl(source)}>다운로드</a>}
      </div>
      {error && <p className="error">{error}</p>}
      {page && (
        <>
          <p className="muted">{page.total}줄 중 {page.start + 1}–{page.end}</p>
          {page.start > 0 && <button className="button link" onClick={loadEarlier}>더 보기(앞쪽)</button>}
          <pre className="log-view">
            {page.records.map((r, i) => <div key={i} className={r.level ? `lv-${r.level}` : ""}>{r.text}</div>)}
          </pre>
        </>
      )}
    </section>
  );
}

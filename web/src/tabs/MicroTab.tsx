import { useCallback, useEffect, useState } from "react";
import { errorText } from "../api/client";
import { getMicroHtml, getMicroOptions, listMicroRuns } from "../api/endpoints";
import type { MicroHtml, MicroOptions, MicroRun } from "../api/types";
import HtmlFrame from "../components/HtmlFrame";

export default function MicroTab() {
  const [runs, setRuns] = useState<MicroRun[]>([]);
  const [run, setRun] = useState("");
  const [options, setOptions] = useState<MicroOptions | null>(null);
  const [optionsRun, setOptionsRun] = useState(""); // 로드된 options 가 어느 run 의 것인지 추적
  const [mode, setMode] = useState<"debug" | "learn">("debug");
  const [target, setTarget] = useState("");
  const [results, setResults] = useState<string[]>([]);
  const [doc, setDoc] = useState("");
  const [fact, setFact] = useState("");
  const [view, setView] = useState<MicroHtml | null>(null);
  const [height, setHeight] = useState(900);
  const [error, setError] = useState("");

  const loadRuns = useCallback(() => {
    let cancelled = false;
    listMicroRuns()
      .then((r) => {
        if (!cancelled) {
          setRuns(r.runs);
          setRun((prev) => (prev && r.runs.some((x) => x.id === prev) ? prev : r.runs[0]?.id ?? ""));
          setError("");
        }
      })
      .catch((e) => { if (!cancelled) setError(errorText(e)); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => { loadRuns(); }, [loadRuns]);

  useEffect(() => {
    if (!run) { setOptions(null); setOptionsRun(""); setView(null); return; }
    let cancelled = false;
    getMicroOptions(run)
      .then((o) => {
        if (!cancelled) {
          setOptions(o);
          setOptionsRun(run);
          setTarget("");
          setResults(o.default_results);
          const firstDoc = o.learn_docs[0] ?? o.reference_doc;
          setDoc(firstDoc);
          setFact(o.facts[firstDoc]?.[0]?.id ?? "");
          setError("");
        }
      })
      .catch((e) => { if (!cancelled) setError(errorText(e)); });
    return () => { cancelled = true; };
  }, [run]);

  useEffect(() => {
    if (!run || !options || optionsRun !== run) return;
    let cancelled = false;
    getMicroHtml({ run, mode, target, results: results.join(","), doc, fact, theme: "light" })
      .then((v) => {
        if (!cancelled) {
          setView(v);
          if (v.height) setHeight(Math.min(3000, Math.max(400, v.height)));
          setError("");
        }
      })
      .catch((e) => { if (!cancelled) setError(errorText(e)); });
    return () => { cancelled = true; };
  }, [run, options, optionsRun, mode, target, results, doc, fact]);

  function toggleResult(key: string) {
    setResults((prev) => (prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]));
  }

  function pickDoc(name: string) {
    setDoc(name);
    setFact(options?.facts[name]?.[0]?.id ?? "");
  }

  if (runs.length === 0) {
    return (
      <div>
        <p className="muted">아직 fact 엔진 실행이 없습니다. fact 엔진으로 비교를 한 번 실행하면 이 탭이 채워집니다.</p>
        <button className="button secondary" onClick={loadRuns}>새로고침</button>
        {error && <p className="error">{error}</p>}
      </div>
    );
  }

  return (
    <div>
      <div className="row">
        <select value={run} onChange={(e) => setRun(e.target.value)}>
          {runs.map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}
        </select>
        <button className="button secondary" onClick={loadRuns}>새로고침</button>
        <label><input type="radio" checked={mode === "debug"} onChange={() => setMode("debug")} /> 🔎 디버깅</label>
        <label><input type="radio" checked={mode === "learn"} onChange={() => setMode("learn")} /> 📚 학습</label>
      </div>
      <p className="muted">디버깅=이 판정이 왜 이렇게 나왔나 / 학습=파이프라인이 어떻게 도는가</p>

      {options && mode === "debug" && (
        <div className="row">
          <select value={target} onChange={(e) => setTarget(e.target.value)}>
            <option value="">(전체)</option>
            {options.target_docs.map((d) => <option key={d} value={d}>{d}</option>)}
          </select>
          {options.result_choices.map((c) => (
            <label key={c.key}>
              <input type="checkbox" checked={results.includes(c.key)} onChange={() => toggleResult(c.key)} /> {c.label}
            </label>
          ))}
        </div>
      )}

      {options && mode === "learn" && (
        <div className="row">
          <select value={doc} onChange={(e) => pickDoc(e.target.value)}>
            {(options.learn_docs.length ? options.learn_docs : [options.reference_doc]).map((d) => (
              <option key={d} value={d}>{d}</option>
            ))}
          </select>
          <select value={fact} onChange={(e) => setFact(e.target.value)}>
            {(options.facts[doc] ?? []).map((f) => <option key={f.id} value={f.id}>{f.name} ({f.id})</option>)}
          </select>
        </div>
      )}

      {error && <p className="error">{error}</p>}
      {view?.unavailable ? (
        <div className="warn">
          <p>이 실행에는 단계별 산출물이 없어 학습 모드를 쓸 수 없습니다(스냅샷은 비교 결과 3종만 보관합니다).</p>
          <ul>{view.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
        </div>
      ) : (
        view && (
          <>
            <label className="row">
              화면 높이
              <input type="range" min={400} max={3000} step={100} value={height}
                onChange={(e) => setHeight(Number(e.target.value))} />
              {height}px
            </label>
            <HtmlFrame title="파이프라인 현미경" html={view.html} height={height} />
          </>
        )
      )}
    </div>
  );
}

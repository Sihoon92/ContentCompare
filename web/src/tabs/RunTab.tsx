import { useEffect, useRef, useState } from "react";
import { errorText } from "../api/client";
import { listJobs, submitJob } from "../api/endpoints";
import type { Engine, Job } from "../api/types";
import JobList from "../components/JobList";
import JobWindow from "../components/JobWindow";
import { loadRequester } from "../lib/requester";
import { SUPPORTED_EXTS, addTargets, buildJobForm, isReferenceName, type PickedFile } from "../lib/uploads";

export default function RunTab() {
  const [engine, setEngine] = useState<Engine>("rag");
  const [reference, setReference] = useState<File | null>(null);
  const [targets, setTargets] = useState<PickedFile[]>([]);
  const [skipped, setSkipped] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [jobs, setJobs] = useState<Job[]>([]);
  const [openJob, setOpenJob] = useState<string | null>(null);
  const [openSeq, setOpenSeq] = useState(0);
  const [listError, setListError] = useState("");
  const folderInput = useRef<HTMLInputElement>(null);
  const referenceInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    // 폴더 선택은 표준 속성이 아니라 JSX 타입에 없다 — DOM 에 직접 붙인다.
    folderInput.current?.setAttribute("webkitdirectory", "");
  }, []);

  useEffect(() => {
    let alive = true;
    const load = () => listJobs()
      .then((r) => { if (alive) { setJobs(r.jobs); setListError(""); } })
      .catch((e) => { if (alive) setListError(`작업 목록을 불러오지 못했습니다: ${errorText(e)}`); });
    load();
    const timer = window.setInterval(load, 3000);
    return () => { alive = false; window.clearInterval(timer); };
  }, []);

  // 열기는 언제나 창을 새로 펼친다 — key 가 바뀌면 JobWindow 가 다시 만들어져 최소화 상태가 풀린다.
  function openJobWindow(id: string) {
    setOpenJob(id);
    setOpenSeq((n) => n + 1);
  }

  function pickReference(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0] ?? null;
    if (file && !isReferenceName(file.name)) {
      setError(`기준 문서는 Excel 이어야 합니다: ${file.name}`);
      setReference(null);
      e.target.value = "";
      return;
    }
    setError("");
    setReference(file);
  }

  function pickTargets(files: FileList | null) {
    if (!files) return;
    const r = addTargets(targets, Array.from(files));
    setTargets(r.items);
    setSkipped(r.skipped);
  }

  async function submit() {
    if (!reference) { setError("기준 엑셀을 선택하세요."); return; }
    if (targets.length === 0) { setError("대상 문서를 하나 이상 선택하세요."); return; }
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const r = await submitJob(buildJobForm(engine, loadRequester(), reference, targets));
      openJobWindow(r.job.id);
      if (r.skipped.length > 0) setNotice(`서버가 건너뛴 파일 ${r.skipped.length}개: ${r.skipped.join(", ")}`);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  const mine = jobs.filter((j) => j.mine);
  const active = jobs.filter((j) => j.state === "queued" || j.state === "running");

  return (
    <div className="run-tab">
      <div className="field">
        <span className="label">엔진</span>
        <label><input type="radio" checked={engine === "rag"} onChange={() => setEngine("rag")} /> rag</label>
        <label><input type="radio" checked={engine === "fact"} onChange={() => setEngine("fact")} /> fact</label>
      </div>
      <p className="muted">rag=하이브리드 검색 후 LLM 종합 판정 / fact=문서를 fact 로 정규화한 뒤 개념 그래프로 짝을 찾아 코드가 값 대조</p>

      <h3>1) 기준 엑셀</h3>
      <input ref={referenceInput} type="file" accept=".xlsx,.xls,.xlsm" onChange={pickReference} />
      {reference && <p className="muted">선택됨: {reference.name}</p>}

      <h3>2) 대상 문서들</h3>
      <div className="row">
        <label className="button secondary">
          📁 파일 선택(여러 개)
          <input hidden type="file" multiple accept={SUPPORTED_EXTS.join(",")}
            onChange={(e) => { pickTargets(e.target.files); e.target.value = ""; }} />
        </label>
        <label className="button secondary">
          📂 폴더 선택
          <input hidden ref={folderInput} type="file" multiple
            onChange={(e) => { pickTargets(e.target.files); e.target.value = ""; }} />
        </label>
        <button className="button secondary" onClick={() => { setTargets([]); setSkipped([]); }}>🗑️ 목록 비우기</button>
      </div>
      {targets.length > 0 && (
        <>
          <p className="muted">대상 {targets.length}개</p>
          <pre className="file-list">{targets.map((t) => t.path).join("\n")}</pre>
        </>
      )}
      {skipped.length > 0 && <p className="muted">지원하지 않거나 Office 잠금 파일이라 건너뜀: {skipped.join(", ")}</p>}

      <div className="row">
        <button className="button primary" disabled={busy} onClick={submit}>{busy ? "업로드 중..." : "🚀 비교 실행"}</button>
      </div>
      {error && <p className="error">{error}</p>}
      {notice && <p className="muted">{notice}</p>}

      {listError && <p className="warn">{listError}</p>}
      <JobList title="내 최근 작업" jobs={mine} empty="아직 이 브라우저에서 실행한 작업이 없습니다." onOpen={openJobWindow} />
      <JobList title="대기열" jobs={active} empty="대기 중이거나 실행 중인 작업이 없습니다." onOpen={openJobWindow} />

      {openJob && <JobWindow key={`${openJob}-${openSeq}`} jobId={openJob} onClose={() => setOpenJob(null)} />}
    </div>
  );
}

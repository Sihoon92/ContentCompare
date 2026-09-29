import { useEffect, useState } from "react";
import { errorText } from "../api/client";
import { cancelJob, getJob, getResult } from "../api/endpoints";
import type { JobDetail, JobResult, JobState } from "../api/types";
import { useJobStream } from "../hooks/useJobStream";
import { isFinal, shortId, statusText } from "../lib/format";
import { currentText, formatElapsed, progressSummary, unitChip } from "../lib/progress";
import LogPanel from "./LogPanel";
import ResultView from "./ResultView";

export default function JobView({ jobId }: { jobId: string }) {
  const stream = useJobStream(jobId);
  const [job, setJob] = useState<JobDetail | null>(null);
  const [result, setResult] = useState<{ result: JobResult; report: boolean } | null>(null);
  const [error, setError] = useState("");
  const [now, setNow] = useState(() => Date.now() / 1000);

  const state = (stream.status.state || job?.state || "") as JobState | "";
  const final = isFinal(state);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => window.clearInterval(timer);
  }, []);

  // 요청자·순번·'내 작업' 여부는 SSE 에 없으므로 따로 가져오고, 끝나기 전에는 순번 때문에 몇 초마다 다시 본다.
  useEffect(() => {
    let alive = true;
    const load = () => getJob(jobId)
      .then((j) => { if (alive) { setJob(j); setError(""); } })
      .catch((e) => { if (alive) setError(errorText(e)); });
    load();
    const timer = window.setInterval(() => { if (!final) load(); }, 3000);
    return () => { alive = false; window.clearInterval(timer); };
  }, [jobId, final]);

  useEffect(() => {
    if (!final) return;
    getResult(jobId).then(setResult).catch(() => setResult(null));
  }, [jobId, final]);

  async function cancel() {
    try {
      await cancelJob(jobId);
    } catch (e) {
      setError(errorText(e));
    }
  }

  if (stream.notFound) return <p className="error">작업을 찾을 수 없습니다(보관 기간이 지나 삭제되었을 수 있습니다).</p>;

  const progress = stream.progress ?? job?.progress ?? null;
  const percent = progress?.percent ?? 0;
  const started = stream.status.started_ts || job?.started_ts || 0;
  const finished = stream.status.finished_ts || job?.finished_ts || 0;
  const elapsed = started ? (finished || now) - started : 0;
  const failure = stream.status.error || job?.error || "";

  return (
    <div className="job-view">
      <h2>
        작업 #{shortId(jobId)}{job ? ` · ${job.requester || "이름 없음"} · ${job.engine}` : ""}
      </h2>
      <div className="job-status">
        <span className={`badge state-${state}`}>
          {job && state ? statusText({ state, position: job.position }) : "불러오는 중"}
        </span>
        {stream.stalled && !final && (
          <span className="warn">⚠ {Math.floor(stream.idleS / 60)}분째 진행 기록이 없습니다(LLM 응답을 기다리는 중일 수 있습니다).</span>
        )}
      </div>

      <div className="progress"><div className="progress-bar" style={{ width: `${percent}%` }} /></div>
      <div className="progress-meta">
        <strong>{percent}%</strong>
        <span>{progressSummary(progress)}</span>
        <span>경과 {formatElapsed(elapsed)}</span>
      </div>
      {progress?.current && <p className="current">현재: {currentText(progress.current)}</p>}
      {progress && progress.units.length > 0 && (
        <div className="chips">
          {progress.units.map((u) => {
            const chip = unitChip(u);
            return <span key={u.key} className={`chip chip-${chip.state}`} title={chip.title}>{chip.text}</span>;
          })}
        </div>
      )}

      {final && failure && <p className="error">{failure}</p>}
      {error && <p className="error">{error}</p>}

      <LogPanel lines={stream.lines} />

      <div className="row">
        {job?.mine && !final && <button className="button danger" onClick={cancel}>취소</button>}
      </div>

      {result && <ResultView jobId={jobId} result={result.result} hasReport={result.report} />}
    </div>
  );
}

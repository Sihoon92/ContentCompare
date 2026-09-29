import { useEffect, useState } from "react";
import { ApiError, errorText } from "../api/client";
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
  const [resultError, setResultError] = useState("");
  const [resultTry, setResultTry] = useState(0); // "다시 불러오기" 로 올려 결과 조회 효과를 다시 돌린다
  const [now, setNow] = useState(() => Date.now() / 1000);

  // 스트림이 영영 끊겼는데(연결 거절 등) 폴링한 작업이 이미 끝났다면 폴링 값이 옳다.
  const state = (job && isFinal(job.state) ? job.state : stream.status.state || job?.state || "") as JobState | "";
  const final = isFinal(state);

  // 끝난 뒤에는 경과 시간이 finished 로 고정되므로 매초 다시 그릴 이유가 없다.
  useEffect(() => {
    if (final) return;
    const timer = window.setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => window.clearInterval(timer);
  }, [final]);

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
    let alive = true;
    setResultError("");
    getResult(jobId)
      .then((r) => { if (alive) setResult(r); })
      .catch((e) => {
        if (!alive) return;
        setResult(null);
        // 실패한 작업에는 결과가 없어 404 가 정상이다 — 그것만 조용히 넘기고 나머지는 알린다.
        if (!(e instanceof ApiError && e.status === 404)) setResultError(`결과를 불러오지 못했습니다: ${errorText(e)}`);
      });
    return () => { alive = false; };
  }, [jobId, final, resultTry]);

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
      {resultError && (
        <div className="row">
          <p className="error">{resultError}</p>
          <button className="button secondary" onClick={() => setResultTry((n) => n + 1)}>다시 불러오기</button>
        </div>
      )}

      <LogPanel lines={stream.lines} />

      <div className="row">
        {job?.mine && !final && <button className="button danger" onClick={cancel}>취소</button>}
      </div>

      {result && <ResultView jobId={jobId} result={result.result} hasReport={result.report} />}
    </div>
  );
}

import { useState } from "react";
import { shortId } from "../lib/format";
import JobView from "./JobView";

// 🚀 실행을 누르면 뜨는 창(설계 §8.4). 닫아도 작업은 계속된다.
export default function JobWindow({ jobId, onClose }: { jobId: string; onClose: () => void }) {
  const [minimized, setMinimized] = useState(false);
  if (minimized) {
    return (
      <div className="job-mini">
        <span>작업 #{shortId(jobId)}</span>
        <button className="button link" onClick={() => setMinimized(false)}>열기</button>
        <button className="button link" onClick={onClose}>닫기</button>
      </div>
    );
  }
  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true">
      <div className="modal">
        <div className="modal-toolbar">
          <button className="button secondary" onClick={() => window.open(`/jobs/${jobId}`, "_blank", "noopener")}>새 창으로</button>
          <button className="button secondary" onClick={() => setMinimized(true)}>최소화</button>
          <button className="button secondary" onClick={onClose}>닫기</button>
        </div>
        <p className="muted">창을 닫아도 작업은 계속됩니다. '내 최근 작업'에서 다시 열 수 있습니다.</p>
        <JobView jobId={jobId} />
      </div>
    </div>
  );
}

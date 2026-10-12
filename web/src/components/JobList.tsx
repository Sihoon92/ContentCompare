import type { Job } from "../api/types";
import { formatClock, shortId, statusText } from "../lib/format";

export default function JobList({ title, jobs, empty, onOpen }: {
  title: string;
  jobs: Job[];
  empty: string;
  onOpen: (id: string) => void;
}) {
  return (
    <section className="job-list">
      <h3>{title}</h3>
      {jobs.length === 0 ? (
        <p className="muted">{empty}</p>
      ) : (
        <table className="table">
          <thead>
            <tr><th>작업</th><th>요청자</th><th>엔진</th><th>상태</th><th>접수</th><th></th></tr>
          </thead>
          <tbody>
            {jobs.map((j) => (
              <tr key={j.id}>
                <td>#{shortId(j.id)}</td>
                <td>{j.requester || "이름 없음"}</td>
                <td>{j.engine}</td>
                <td>{statusText(j)}</td>
                <td>{formatClock(j.created_ts)}</td>
                <td><button className="button link" onClick={() => onOpen(j.id)}>열기</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

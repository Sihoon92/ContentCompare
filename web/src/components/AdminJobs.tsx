import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, errorText } from "../api/client";
import { adminCancel, adminDelete, adminJobs } from "../api/endpoints";
import type { AdminJob } from "../api/types";
import { STATE_LABEL, formatDateTime, isFinal } from "../lib/format";

function isUnauthorized(e: unknown): boolean {
  return e instanceof ApiError && e.status === 401;
}

export default function AdminJobs({ onUnauthorized }: { onUnauthorized: () => void }) {
  const [jobs, setJobs] = useState<AdminJob[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<ReadonlySet<string>>(new Set()); // 요청 중인 작업 — 버튼 중복 제출 방지
  const seq = useRef(0); // 늦게 온 옛 목록이 새 목록을 덮지 않게 한다

  // 관리자 세션이 끝났으면(401) 오류를 보이는 대신 부모에게 알려 로그인 화면으로 돌린다.
  const fail = useCallback((e: unknown) => {
    if (isUnauthorized(e)) onUnauthorized();
    else setError(errorText(e));
  }, [onUnauthorized]);

  const load = useCallback(() => {
    const mine = ++seq.current;
    adminJobs()
      .then((r) => {
        if (mine !== seq.current) return;
        setJobs(r.jobs);
        setError("");
      })
      .catch((e) => { if (mine === seq.current || isUnauthorized(e)) fail(e); });
  }, [fail]);

  useEffect(() => {
    load();
    const timer = window.setInterval(load, 5000);
    return () => {
      window.clearInterval(timer);
      seq.current += 1;
    };
  }, [load]);

  function setBusyFor(id: string, on: boolean) {
    setBusy((prev) => {
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  async function act(id: string, fn: () => Promise<unknown>) {
    setBusyFor(id, true);
    try {
      await fn();
      load();
    } catch (e) {
      fail(e);
    } finally {
      setBusyFor(id, false);
    }
  }

  return (
    <section className="admin-section">
      <h3>작업 관리</h3>
      {error && <p className="error">{error}</p>}
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr><th>작업</th><th>요청자</th><th>엔진</th><th>상태</th><th>접수</th><th>종료</th><th>오류</th><th></th></tr>
          </thead>
          <tbody>
            {jobs.map((j) => (
              <tr key={j.id}>
                <td>{j.id}</td>
                <td>{j.requester || "이름 없음"}</td>
                <td>{j.engine}</td>
                <td>{STATE_LABEL[j.state] ?? j.state}</td>
                <td>{formatDateTime(j.created_ts)}</td>
                <td>{formatDateTime(j.finished_ts)}</td>
                <td>{j.error}</td>
                <td>
                  {isFinal(j.state) ? (
                    <button className="button link" disabled={busy.has(j.id)} onClick={() => {
                      if (window.confirm(`작업 ${j.id} 의 폴더(업로드 원본·결과)를 지웁니다.`)) act(j.id, () => adminDelete(j.id));
                    }}>삭제</button>
                  ) : (
                    <button className="button link" disabled={busy.has(j.id)}
                      onClick={() => act(j.id, () => adminCancel(j.id))}>취소</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

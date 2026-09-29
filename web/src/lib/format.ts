import type { JobState } from "../api/types";

export const STATE_LABEL: Record<JobState, string> = {
  queued: "대기 중",
  running: "실행 중",
  succeeded: "완료",
  failed: "실패",
  cancelled: "취소됨",
  interrupted: "중단됨",
};

export const FINAL_STATES: readonly JobState[] = ["succeeded", "failed", "cancelled", "interrupted"];

export function isFinal(state: string): boolean {
  return (FINAL_STATES as readonly string[]).includes(state);
}

export function statusText(job: { state: JobState; position: number | null }): string {
  if (job.state === "queued") {
    const ahead = job.position ?? 0;
    return ahead > 0 ? `대기 중 · 앞에 ${ahead}건` : "대기 중 · 곧 시작";
  }
  return STATE_LABEL[job.state] ?? job.state;
}

export function shortId(id: string): string {
  return id.split("-").pop() ?? id;
}

const pad = (n: number) => String(n).padStart(2, "0");

export function formatClock(ts: number): string {
  if (!ts) return "-";
  const d = new Date(ts * 1000);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function formatDateTime(ts: number): string {
  if (!ts) return "-";
  const d = new Date(ts * 1000);
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

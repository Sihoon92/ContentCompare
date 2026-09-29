import type { JobState, Snapshot } from "../api/types";

// SSE 이벤트를 실행 창 상태로 누적한다. 서버가 보내는 이벤트: log · progress · status · stall · end.
// 모양이 틀린 데이터는 무시한다 — 화면이 한 이벤트 때문에 죽으면 안 된다.

export const MAX_LINES = 2000;

export interface StreamStatus {
  state: JobState | "";
  error: string;
  started_ts: number;
  finished_ts: number;
}

export interface StreamState {
  status: StreamStatus;
  progress: Snapshot | null;
  lines: string[];
  stalled: boolean;
  idleS: number;
  ended: boolean;
  notFound: boolean;
}

export function initialStream(): StreamState {
  return {
    status: { state: "", error: "", started_ts: 0, finished_ts: 0 },
    progress: null,
    lines: [],
    stalled: false,
    idleS: 0,
    ended: false,
    notFound: false,
  };
}

function asRecord(data: unknown): Record<string, unknown> {
  return data && typeof data === "object" && !Array.isArray(data) ? (data as Record<string, unknown>) : {};
}

export function applyEvent(state: StreamState, type: string, data: unknown): StreamState {
  const d = asRecord(data);
  switch (type) {
    case "log": {
      if (!Array.isArray(d.lines) || d.lines.length === 0) return state;
      const lines = state.lines.concat(d.lines.map(String));
      return { ...state, lines: lines.length > MAX_LINES ? lines.slice(lines.length - MAX_LINES) : lines };
    }
    case "progress":
      if (!Array.isArray(d.units)) return state;
      return { ...state, progress: d as unknown as Snapshot };
    case "status":
      return {
        ...state,
        status: {
          state: String(d.state ?? "") as JobState | "",
          error: String(d.error ?? ""),
          started_ts: Number(d.started_ts ?? 0) || 0,
          finished_ts: Number(d.finished_ts ?? 0) || 0,
        },
      };
    case "stall":
      return { ...state, stalled: d.stalled === true, idleS: Number(d.idle_s ?? 0) || 0 };
    case "end":
      return { ...state, ended: true, notFound: d.reason === "not_found" };
    default:
      return state;
  }
}

import { useEffect, useReducer } from "react";
import { jobEventsUrl } from "../api/endpoints";
import { applyEvent, initialStream, type StreamState } from "../lib/jobEvents";

type Action = { type: string; data: unknown };
const EVENT_TYPES = ["log", "progress", "status", "stall", "end"] as const;

function reducer(state: StreamState, action: Action): StreamState {
  return action.type === "reset" ? initialStream() : applyEvent(state, action.type, action.data);
}

/**
 * 작업 이벤트 구독. EventSource 는 끊기면 스스로 다시 붙고 마지막 이벤트 id(Last-Event-ID)를
 * 실어 보내므로 서버가 본 줄을 또 보내지 않는다. 404 처럼 서버가 연결을 거절하면 브라우저가
 * 연결을 닫는다(CLOSED) — 그때는 끝난 것으로 본다.
 */
export function useJobStream(jobId: string): StreamState {
  const [state, dispatch] = useReducer(reducer, undefined, initialStream);
  useEffect(() => {
    dispatch({ type: "reset", data: null });
    if (!jobId) return;
    const source = new EventSource(jobEventsUrl(jobId));
    for (const type of EVENT_TYPES) {
      source.addEventListener(type, (event) => {
        let data: unknown;
        try {
          data = JSON.parse((event as MessageEvent<string>).data);
        } catch {
          return;
        }
        dispatch({ type, data });
        if (type === "end") source.close();
      });
    }
    source.onerror = () => {
      if (source.readyState === EventSource.CLOSED) dispatch({ type: "end", data: {} });
    };
    return () => source.close();
  }, [jobId]);
  return state;
}

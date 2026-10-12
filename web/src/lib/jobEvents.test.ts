import { describe, expect, it } from "vitest";
import { MAX_LINES, applyEvent, initialStream } from "./jobEvents";

describe("applyEvent", () => {
  it("로그 줄을 이어 붙인다", () => {
    let s = applyEvent(initialStream(), "log", { lines: ["첫 줄"] });
    s = applyEvent(s, "log", { lines: ["둘", "셋"] });
    expect(s.lines).toEqual(["첫 줄", "둘", "셋"]);
  });

  it(`최근 ${MAX_LINES}줄만 남긴다`, () => {
    const many = Array.from({ length: MAX_LINES + 500 }, (_, i) => `줄 ${i}`);
    const s = applyEvent(initialStream(), "log", { lines: many });
    expect(s.lines).toHaveLength(MAX_LINES);
    expect(s.lines[0]).toBe("줄 500");
    expect(s.lines[MAX_LINES - 1]).toBe(`줄 ${MAX_LINES + 499}`);
  });

  it("진행·상태·멈춤·끝", () => {
    const progress = { units: [], total: 1, finished: 0, fraction: 0, percent: 0, units_done: 0,
      current: null, started_ts: 0, last_ts: 0, last_seq: 3 };
    let s = applyEvent(initialStream(), "progress", progress);
    expect(s.progress?.last_seq).toBe(3);
    s = applyEvent(s, "status", { state: "failed", error: "ValueError: x", started_ts: 10, finished_ts: 20 });
    expect(s.status).toEqual({ state: "failed", error: "ValueError: x", started_ts: 10, finished_ts: 20 });
    s = applyEvent(s, "stall", { stalled: true, idle_s: 400 });
    expect([s.stalled, s.idleS]).toEqual([true, 400]);
    s = applyEvent(s, "end", { state: "failed" });
    expect([s.ended, s.notFound]).toEqual([true, false]);
    expect(applyEvent(initialStream(), "end", { reason: "not_found" }).notFound).toBe(true);
  });

  it("모르는 이벤트·모양이 틀린 데이터는 무시하거나 안전하게", () => {
    const s0 = initialStream();
    expect(applyEvent(s0, "unknown", { a: 1 })).toBe(s0);
    expect(applyEvent(s0, "log", "문자열")).toBe(s0);
    expect(applyEvent(s0, "log", { lines: "줄 아님" })).toBe(s0);
    expect(applyEvent(s0, "status", null).status.state).toBe("");
    expect(applyEvent(s0, "stall", 42).stalled).toBe(false);
  });
});

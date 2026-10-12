import { describe, expect, it } from "vitest";
import { formatClock, isFinal, shortId, statusText } from "./format";

describe("statusText", () => {
  it("대기 중이면 앞에 몇 건", () => {
    expect(statusText({ state: "queued", position: 2 })).toBe("대기 중 · 앞에 2건");
    expect(statusText({ state: "queued", position: 0 })).toBe("대기 중 · 곧 시작");
    expect(statusText({ state: "queued", position: null })).toBe("대기 중 · 곧 시작");
  });
  it("나머지 상태", () => {
    expect(statusText({ state: "running", position: null })).toBe("실행 중");
    expect(statusText({ state: "interrupted", position: null })).toBe("중단됨");
  });
});

describe("isFinal", () => {
  it("끝난 상태만 참", () => {
    expect(isFinal("succeeded")).toBe(true);
    expect(isFinal("cancelled")).toBe(true);
    expect(isFinal("running")).toBe(false);
    expect(isFinal("")).toBe(false);
  });
});

describe("shortId / formatClock", () => {
  it("작업 ID 의 마지막 조각", () => expect(shortId("20260929-140211-a3f9")).toBe("a3f9"));
  it("시각은 HH:MM, 0 은 -", () => {
    expect(formatClock(0)).toBe("-");
    expect(formatClock(new Date(2026, 8, 29, 9, 5).getTime() / 1000)).toBe("09:05");
  });
});

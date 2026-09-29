import { describe, expect, it } from "vitest";
import type { Snapshot } from "../api/types";
import { currentText, formatElapsed, progressSummary, unitChip } from "./progress";

const snap = (over: Partial<Snapshot> = {}): Snapshot => ({
  units: [], total: 8, finished: 4, fraction: 0.575, percent: 57, units_done: 4.6,
  current: null, started_ts: 0, last_ts: 0, last_seq: 0, ...over,
});

describe("progressSummary", () => {
  it("단계 수를 소수 한 자리로", () => expect(progressSummary(snap())).toBe("4.6 / 8 단계"));
  it("계획 전이면 준비 중", () => {
    expect(progressSummary(null)).toBe("준비 중");
    expect(progressSummary(snap({ total: 0 }))).toBe("준비 중");
  });
});

describe("currentText", () => {
  it("단위·하위 단계·진척", () => {
    expect(currentText({ key: "doc:2", label: "대상B.pptx", part_name: "F3 facts", part_index: 3,
      parts: 4, step_done: 3, step_total: 5 })).toBe("대상B.pptx · F3 facts · 3/5");
  });
  it("하위 단계·진척이 없으면 생략", () => {
    expect(currentText({ key: "concept", label: "F7 개념 판정", part_name: "", part_index: 0,
      parts: 1, step_done: 0, step_total: 0 })).toBe("F7 개념 판정");
  });
  it("없으면 빈 문자열", () => expect(currentText(null)).toBe(""));
});

describe("unitChip", () => {
  it("상태 기호와 실패 사유", () => {
    const chip = unitChip({ key: "doc:1", label: "깨진.xlsx", kind: "doc", state: "failed", error: "OSError" });
    expect(chip).toEqual({ text: "✗ 깨진.xlsx", state: "failed", title: "깨진.xlsx: OSError" });
  });
  it("진행 중·대기", () => {
    expect(unitChip({ key: "a", label: "A", kind: "doc", state: "running", error: "" }).text).toBe("▶ A");
    expect(unitChip({ key: "b", label: "B", kind: "doc", state: "pending", error: "" }).title).toBe("B");
  });
});

describe("formatElapsed", () => {
  it("분:초", () => expect(formatElapsed(750)).toBe("12:30"));
  it("한 시간이 넘으면 시:분:초", () => expect(formatElapsed(3725)).toBe("1:02:05"));
  it("음수·소수는 0 이상 정수", () => {
    expect(formatElapsed(-3)).toBe("0:00");
    expect(formatElapsed(59.9)).toBe("0:59");
  });
});

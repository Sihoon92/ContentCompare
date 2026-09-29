import type { ProgressCurrent, Snapshot, UnitState } from "../api/types";

// 진행률 계산은 서버(progress.summarize)가 한다. 여기서는 보여 줄 문장만 만든다.

export function progressSummary(s: Snapshot | null): string {
  if (!s || s.total === 0) return "준비 중";
  return `${s.units_done.toFixed(1)} / ${s.total} 단계`;
}

export function currentText(c: ProgressCurrent | null): string {
  if (!c) return "";
  const parts = [c.label];
  if (c.part_name) parts.push(c.part_name);
  if (c.step_total > 0) parts.push(`${c.step_done}/${c.step_total}`);
  return parts.join(" · ");
}

const MARK: Record<UnitState["state"], string> = {
  done: "✓", running: "▶", failed: "✗", skipped: "–", pending: "·",
};

export function unitChip(u: UnitState): { text: string; state: UnitState["state"]; title: string } {
  return {
    text: `${MARK[u.state] ?? "·"} ${u.label}`,
    state: u.state,
    title: u.error ? `${u.label}: ${u.error}` : u.label,
  };
}

export function formatElapsed(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

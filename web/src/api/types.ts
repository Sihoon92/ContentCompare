// 백엔드(contentcompare/web/) 응답과 1:1. 필드를 바꾸면 서버 쪽도 함께 본다.

export type JobState = "queued" | "running" | "succeeded" | "failed" | "cancelled" | "interrupted";
export type Engine = "rag" | "fact";

export interface Job {
  id: string;
  engine: Engine;
  requester: string;
  reference: string;
  targets: string[];
  state: JobState;
  created_ts: number;
  started_ts: number;
  finished_ts: number;
  error: string;
  llm: Record<string, string>;
  mine: boolean;
  position: number | null;
}

export interface UnitState {
  key: string;
  label: string;
  kind: string;
  state: "pending" | "running" | "done" | "failed" | "skipped";
  error: string;
}

export interface ProgressCurrent {
  key: string;
  label: string;
  part_name: string;
  part_index: number;
  parts: number;
  step_done: number;
  step_total: number;
}

export interface Snapshot {
  units: UnitState[];
  total: number;
  finished: number;
  fraction: number;
  percent: number;
  units_done: number;
  current: ProgressCurrent | null;
  started_ts: number;
  last_ts: number;
  last_seq: number;
}

export interface JobDetail extends Job {
  progress: Snapshot;
}

export interface JobList {
  jobs: Job[];
  running: string | null;
}

export interface SubmitResult {
  job: Job;
  skipped: string[];
}

export interface CheckLine {
  name: string;
  ok: boolean;
  detail: string;
  line: string;
}

export interface LlmCheck {
  ok: boolean;
  results: CheckLine[];
  cached: boolean;
}

export interface CountItem {
  key: string;
  label: string;
  n: number;
}

export type Row = Record<string, string | number | boolean | null>;

export interface RagDetail {
  index: number;
  label: string;
  verdict: string;
  source_label: string;
  reference_text: string;
  is_record: boolean;
  sources: string[];
  reasoning: string;
  fields: Row[];
  candidates: { score: number; source_label: string; matched: boolean }[];
}

export interface RagResult {
  engine: "rag";
  counts: CountItem[];
  summary: Row[];
  details: RagDetail[];
}

export interface FactResult {
  engine: "fact";
  counts: CountItem[];
  summary: Row[];
  failed_docs: { name: string; error: string }[];
  compare_stats: Record<string, unknown>;
}

export type JobResult = RagResult | FactResult;

export interface ReportItem {
  id: string;
  name: string;
  mtime: number;
}

export interface MicroRun {
  id: string;
  label: string;
  snapshot: boolean;
}

export interface MicroOptions {
  reference_doc: string;
  target_docs: string[];
  learn_docs: string[];
  facts: Record<string, { id: string; name: string }[]>;
  capabilities: string[];
  problems: string[];
  result_choices: { key: string; label: string }[];
  default_results: string[];
}

export interface MicroHtmlParams {
  run: string;
  mode: "debug" | "learn";
  target?: string;
  results?: string;
  doc?: string;
  fact?: string;
  theme?: "light" | "dark";
}

export interface MicroHtml {
  html: string;
  height: number;
  notes: string[];
  unavailable: boolean;
}

export interface TimelineItem {
  id: string;
  label: string;
  mtime: number;
}

export interface KnowledgeFileInfo {
  name: string;
  mtime: number;
  size: number;
}

export interface KnowledgeList {
  files: KnowledgeFileInfo[];
  enabled: boolean;
  template: string;
}

export interface KnowledgeFile {
  name: string;
  content: string;
  mtime: number;
}

export interface KnowledgeConflict {
  message: string;
  current: { content: string; mtime: number };
}

export interface AdminStatus {
  enabled: boolean;
  logged_in: boolean;
}

export interface LogSource {
  id: string;
  label: string;
  size: number;
  mtime: number;
}

export interface LogRecord {
  level: string;
  text: string;
}

export interface LogPage {
  records: LogRecord[];
  start: number;
  end: number;
  total: number;
}

export interface AdminJob extends Omit<Job, "mine"> {
  client_id: string;
}

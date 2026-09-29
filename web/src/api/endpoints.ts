import { fetchText, request } from "./client";
import type {
  AdminJob, AdminStatus, JobDetail, JobList, JobResult, KnowledgeFile, KnowledgeList, LlmCheck,
  LogPage, LogSource, MicroHtml, MicroHtmlParams, MicroOptions, MicroRun, ReportItem, SubmitResult,
  TimelineItem, Job,
} from "./types";

type Query = Record<string, string | number | boolean | null | undefined>;

function qs(params: Query): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null) search.set(key, String(value));
  }
  return search.toString();
}

const job = (id: string) => `/api/jobs/${encodeURIComponent(id)}`;

// --- LLM 점검 ---------------------------------------------------------------
export const checkLlm = () => request<LlmCheck>("POST", "/api/llm/check");

// --- 작업 ---------------------------------------------------------------------
export const listJobs = () => request<JobList>("GET", "/api/jobs");
export const getJob = (id: string) => request<JobDetail>("GET", job(id));
export const submitJob = (form: FormData) => request<SubmitResult>("POST", "/api/jobs", form);
export const cancelJob = (id: string) => request<{ job: Job }>("POST", `${job(id)}/cancel`);
export const getResult = (id: string) =>
  request<{ result: JobResult; report: boolean }>("GET", `${job(id)}/result`);
export const getJobReport = (id: string) => fetchText(`${job(id)}/report`);
export const jobReportUrl = (id: string) => `${job(id)}/report`;
export const jobEventsUrl = (id: string) => `${job(id)}/events`;

// --- 조회 ---------------------------------------------------------------------
export const listReports = () => request<{ reports: ReportItem[] }>("GET", "/api/reports");
export const getReport = (id: string) =>
  request<{ id: string; markdown: string }>("GET", `/api/reports/content?${qs({ id })}`);
export const listMicroRuns = () => request<{ runs: MicroRun[] }>("GET", "/api/micro/runs");
export const getMicroOptions = (run: string) =>
  request<MicroOptions>("GET", `/api/micro/options?${qs({ run })}`);
export const getMicroHtml = (p: MicroHtmlParams) =>
  request<MicroHtml>("GET", `/api/micro/html?${qs({ ...p })}`);
export const listTimelines = () => request<{ timelines: TimelineItem[] }>("GET", "/api/timelines");
export const getTimelineHtml = (run: string, errorsOnly: boolean) =>
  request<{ html: string }>("GET", `/api/timelines/html?${qs({ run, errors_only: errorsOnly })}`);

// --- 도메인 지식 ------------------------------------------------------------------
const knowledge = (name: string) => `/api/knowledge/files/${encodeURIComponent(name)}`;
export const listKnowledge = () => request<KnowledgeList>("GET", "/api/knowledge/files");
export const getKnowledge = (name: string) => request<KnowledgeFile>("GET", knowledge(name));
export const saveKnowledge = (name: string, content: string, baseMtime: number | null) =>
  request<{ name: string; mtime: number }>("PUT", knowledge(name), { content, base_mtime: baseMtime });
export const getMergedKnowledge = () => request<{ text: string }>("GET", "/api/knowledge/merged");

// --- 관리자 -------------------------------------------------------------------
export const adminStatus = () => request<AdminStatus>("GET", "/api/admin/status");
export const adminLogin = (password: string) => request<{ ok: boolean }>("POST", "/api/admin/login", { password });
export const adminLogout = () => request<{ ok: boolean }>("POST", "/api/admin/logout");
export const adminLogSources = () => request<{ sources: LogSource[] }>("GET", "/api/admin/logs");
export const readAdminLog = (p: { source: string; level: string; q: string; tail: number; before?: number }) =>
  request<LogPage>("GET", `/api/admin/logs/read?${qs(p)}`);
export const adminLogDownloadUrl = (source: string) => `/api/admin/logs/download?${qs({ source })}`;
export const adminJobs = () => request<{ jobs: AdminJob[] }>("GET", "/api/admin/jobs");
export const adminCancel = (id: string) => request<{ ok: boolean }>("POST", `/api/admin/jobs/${encodeURIComponent(id)}/cancel`);
export const adminDelete = (id: string) => request<{ ok: boolean }>("DELETE", `/api/admin/jobs/${encodeURIComponent(id)}`);

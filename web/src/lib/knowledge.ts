import type { KnowledgeConflict } from "../api/types";

/** PUT /api/knowledge/files/{name} 의 409 본문 → 충돌 정보. 모양이 다르면 null. */
export function knowledgeConflict(body: unknown): KnowledgeConflict | null {
  if (!body || typeof body !== "object") return null;
  const detail = (body as { detail?: unknown }).detail;
  if (!detail || typeof detail !== "object") return null;
  const { message, current } = detail as { message?: unknown; current?: unknown };
  if (typeof message !== "string" || !current || typeof current !== "object") return null;
  const { content, mtime } = current as { content?: unknown; mtime?: unknown };
  if (typeof content !== "string" || typeof mtime !== "number") return null;
  return { message, current: { content, mtime } };
}

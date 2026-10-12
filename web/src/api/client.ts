// API 호출의 유일한 출입구. 서버 오류는 사람이 읽을 한 줄(detailMessage)로 바꿔 ApiError 로 던진다.

export class ApiError extends Error {
  constructor(public status: number, message: string, public body: unknown) {
    super(message);
    this.name = "ApiError";
  }
}

/** FastAPI 오류 본문 → 한 줄. detail 이 문자열·{message} 객체·검증 오류 목록일 수 있다. */
export function detailMessage(body: unknown, fallback: string): string {
  if (!body || typeof body !== "object" || !("detail" in body)) return fallback;
  const detail = (body as { detail: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => (item && typeof item === "object" && "msg" in item
        ? String((item as { msg: unknown }).msg)
        : String(item)))
      .join("; ");
  }
  if (detail && typeof detail === "object") {
    const message = (detail as { message?: unknown }).message;
    if (typeof message === "string") return message;
  }
  return fallback;
}

export function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

function parse(text: string): unknown {
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

export async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, credentials: "same-origin" };
  if (body instanceof FormData) {
    init.body = body; // 경계(boundary)는 브라우저가 붙인다 — Content-Type 을 직접 넣지 말 것
  } else if (body !== undefined) {
    init.body = JSON.stringify(body);
    init.headers = { "Content-Type": "application/json" };
  }
  const res = await fetch(path, init);
  const data = parse(await res.text());
  if (!res.ok) throw new ApiError(res.status, detailMessage(data, `요청 실패 (${res.status})`), data);
  return data as T;
}

/** 본문을 문자열 그대로 받는다(리포트 markdown). */
export async function fetchText(path: string): Promise<string> {
  const res = await fetch(path, { credentials: "same-origin" });
  const text = await res.text();
  if (!res.ok) {
    const data = parse(text);
    throw new ApiError(res.status, detailMessage(data, `요청 실패 (${res.status})`), data);
  }
  return text;
}

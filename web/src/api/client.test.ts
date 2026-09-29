import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, detailMessage, errorText, request } from "./client";

describe("detailMessage", () => {
  it("문자열 detail", () => {
    expect(detailMessage({ detail: "알 수 없는 엔진입니다" }, "대체")).toBe("알 수 없는 엔진입니다");
  });
  it("객체 detail 의 message(지식 저장 충돌)", () => {
    const body = { detail: { message: "다른 사람이 먼저 저장했습니다.", current: { content: "", mtime: 1 } } };
    expect(detailMessage(body, "대체")).toBe("다른 사람이 먼저 저장했습니다.");
  });
  it("검증 오류 목록", () => {
    expect(detailMessage({ detail: [{ msg: "field required" }, { msg: "bad" }] }, "대체"))
      .toBe("field required; bad");
  });
  it("모양을 모르면 대체 문구", () => {
    expect(detailMessage(null, "대체")).toBe("대체");
    expect(detailMessage("본문", "대체")).toBe("대체");
    expect(detailMessage({ detail: 3 }, "대체")).toBe("대체");
  });
});

describe("request", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("JSON 응답을 돌려준다", async () => {
    vi.stubGlobal("fetch", vi.fn(async (_url: string, _init?: RequestInit) =>
      new Response(JSON.stringify({ ok: true }), { status: 200 })));
    await expect(request("GET", "/api/x")).resolves.toEqual({ ok: true });
  });

  it("오류면 서버 메시지를 담은 ApiError", async () => {
    vi.stubGlobal("fetch", vi.fn(async (_url: string, _init?: RequestInit) =>
      new Response(JSON.stringify({ detail: "작업을 찾을 수 없습니다." }), { status: 404 })));
    const err = (await request("GET", "/api/x").catch((e: unknown) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(404);
    expect(err.message).toBe("작업을 찾을 수 없습니다.");
    expect(errorText(err)).toBe("작업을 찾을 수 없습니다.");
  });

  it("JSON 이 아닌 오류 본문은 상태코드 문구", async () => {
    vi.stubGlobal("fetch", vi.fn(async (_url: string, _init?: RequestInit) =>
      new Response("Internal Server Error", { status: 500 })));
    const err = (await request("GET", "/api/x").catch((e: unknown) => e)) as ApiError;
    expect(err.message).toBe("요청 실패 (500)");
  });

  it("객체 본문은 JSON 으로, FormData 는 그대로 보낸다", async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) =>
      new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await request("PUT", "/api/k", { a: 1 });
    const json = fetchMock.mock.calls[0][1] as RequestInit;
    expect(json.body).toBe('{"a":1}');
    expect((json.headers as Record<string, string>)["Content-Type"]).toBe("application/json");

    const form = new FormData();
    form.append("engine", "fact");
    await request("POST", "/api/jobs", form);
    const multipart = fetchMock.mock.calls[1][1] as RequestInit;
    expect(multipart.body).toBe(form);
    expect(multipart.headers).toBeUndefined();
  });
});

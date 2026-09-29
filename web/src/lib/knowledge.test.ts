import { describe, expect, it } from "vitest";
import { knowledgeConflict } from "./knowledge";

describe("knowledgeConflict", () => {
  it("409 본문에서 서버 내용과 mtime 을 꺼낸다", () => {
    const body = { detail: { message: "다른 사람이 먼저 저장했습니다.", current: { content: "서버 내용", mtime: 12.5 } } };
    expect(knowledgeConflict(body)).toEqual({
      message: "다른 사람이 먼저 저장했습니다.",
      current: { content: "서버 내용", mtime: 12.5 },
    });
  });
  it("충돌 모양이 아니면 null", () => {
    expect(knowledgeConflict({ detail: "파일 이름만 쓸 수 있습니다." })).toBeNull();
    expect(knowledgeConflict({ detail: { message: "x", current: { content: "c" } } })).toBeNull();
    expect(knowledgeConflict(null)).toBeNull();
  });
});

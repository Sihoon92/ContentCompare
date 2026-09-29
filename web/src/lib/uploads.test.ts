import { describe, expect, it } from "vitest";
import { addTargets, buildJobForm, isReferenceName, relativePathOf } from "./uploads";

const file = (name: string, relative = "") => {
  const f = new File(["x"], name);
  if (relative) Object.defineProperty(f, "webkitRelativePath", { value: relative });
  return f;
};

describe("relativePathOf / isReferenceName", () => {
  it("폴더 업로드면 상대 경로, 아니면 이름", () => {
    expect(relativePathOf(file("a.docx", "자료/a.docx"))).toBe("자료/a.docx");
    expect(relativePathOf(file("a.docx"))).toBe("a.docx");
  });
  it("기준은 Excel 만, 잠금 파일 제외", () => {
    expect(isReferenceName("기준.xlsx")).toBe(true);
    expect(isReferenceName("기준.XLSM")).toBe(true);
    expect(isReferenceName("기준.docx")).toBe(false);
    expect(isReferenceName("~$기준.xlsx")).toBe(false);
  });
});

describe("addTargets", () => {
  it("폴더 경로를 보존하고 잠금·미지원 파일은 건너뛴다", () => {
    const r = addTargets([], [
      file("규격서 v2.docx", "자료/하위/규격서 v2.docx"),
      file("~$규격서 v2.docx", "자료/하위/~$규격서 v2.docx"),
      file("메모.txt", "자료/메모.txt"),
      file("발표.PPTX"),
    ]);
    expect(r.items.map((i) => i.path)).toEqual(["자료/하위/규격서 v2.docx", "발표.PPTX"]);
    expect(r.skipped).toEqual(["자료/하위/~$규격서 v2.docx", "자료/메모.txt"]);
  });
  it("같은 경로를 다시 고르면 한 번만", () => {
    const first = addTargets([], [file("a.docx")]);
    const again = addTargets(first.items, [file("a.docx"), file("b.docx")]);
    expect(again.items.map((i) => i.path)).toEqual(["a.docx", "b.docx"]);
  });
});

describe("buildJobForm", () => {
  it("상대 경로는 target_paths 로, 파일 이름은 마지막 조각으로", () => {
    const targets = addTargets([], [file("규격서 v2.docx", "자료/규격서 v2.docx"), file("발표.pptx")]).items;
    const form = buildJobForm("fact", "  홍길동 ", file("기준.xlsx"), targets);
    expect(form.get("engine")).toBe("fact");
    expect(form.get("requester")).toBe("홍길동");
    expect(form.get("reference_path")).toBe("기준.xlsx");
    expect(form.getAll("target_paths")).toEqual(["자료/규격서 v2.docx", "발표.pptx"]);
    expect((form.getAll("targets") as File[]).map((f) => f.name)).toEqual(["규격서 v2.docx", "발표.pptx"]);
  });
});

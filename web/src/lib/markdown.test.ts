import { describe, expect, it } from "vitest";
import remarkGfm from "remark-gfm";
import remarkParse from "remark-parse";
import { unified } from "unified";
import { brToBreak } from "./markdown";

// 마크다운 → mdast → brToBreak 적용 결과. HTML 노드가 어떻게 바뀌는지 트리 수준에서 본다.
function run(md: string) {
  const processor = unified().use(remarkParse).use(remarkGfm);
  const tree = processor.parse(md);
  brToBreak()(tree as never);
  return tree;
}

function collect(node: unknown, type: string, out: { type: string; value?: string }[] = []) {
  const n = node as { type: string; value?: string; children?: unknown[] };
  if (n.type === type) out.push(n);
  n.children?.forEach((c) => collect(c, type, out));
  return out;
}

describe("brToBreak", () => {
  it("표 셀 안의 <br> 변형이 break 로 바뀐다", () => {
    const tree = run("| 출처 |\n| --- |\n| A.docx p3<br>B.pptx s2<br/>C.xlsx r1<BR />D |");
    expect(collect(tree, "break")).toHaveLength(3);
    expect(collect(tree, "html")).toHaveLength(0);
  });

  it("문단 안의 <br> 도 break 로 바뀐다", () => {
    const tree = run("앞<br>뒤");
    expect(collect(tree, "break")).toHaveLength(1);
    expect(collect(tree, "html")).toHaveLength(0);
  });

  it("다른 HTML 은 그대로 둔다(skipHtml 이 버린다)", () => {
    const tree = run("<script>alert(1)</script>\n\n문장 <img src=x onerror=alert(1)> <b>굵게</b>");
    expect(collect(tree, "break")).toHaveLength(0);
    const values = collect(tree, "html").map((n) => n.value);
    expect(values).toContain("<script>alert(1)</script>");
    expect(values).toContain("<img src=x onerror=alert(1)>");
    expect(values).toContain("<b>");
  });

  it("<br> 처럼 보이지만 속성이 붙은 것은 건드리지 않는다", () => {
    const tree = run("가<br onclick=x>나");
    expect(collect(tree, "break")).toHaveLength(0);
    expect(collect(tree, "html")).toHaveLength(1);
  });
});

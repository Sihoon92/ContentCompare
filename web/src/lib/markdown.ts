// 리포트 마크다운(RAG)은 출처를 "<br>" 로 이어 붙인다(markdown_report.py). skipHtml 은 그 노드를
// 통째로 버려 "A.docx p3B.pptx s2" 처럼 붙어 보이므로, 정확히 <br> 인 html 노드만 줄바꿈으로 바꾼다.
// 다른 HTML(<script>·<img onerror>·<b> 등)은 건드리지 않아 skipHtml 이 계속 버린다.

interface MdNode {
  type: string;
  value?: string;
  children?: MdNode[];
}

const BR = /^<br\s*\/?>$/i;

function convert(node: MdNode): void {
  if (!node.children) return;
  node.children = node.children.map((child) => {
    if (child.type === "html" && typeof child.value === "string" && BR.test(child.value)) {
      return { type: "break" };
    }
    convert(child);
    return child;
  });
}

/** remark 플러그인: <br> html 노드 → break 노드(자식까지 재귀). */
export function brToBreak() {
  return (tree: MdNode) => convert(tree);
}

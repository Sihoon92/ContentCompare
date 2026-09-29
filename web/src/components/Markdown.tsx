import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { brToBreak } from "../lib/markdown";

// 리포트 본문에는 문서 원문이 들어가므로 HTML 은 해석하지 않는다(skipHtml). 정확히 <br> 만 줄바꿈으로 살린다(출처 목록).
export default function Markdown({ text }: { text: string }) {
  return (
    <div className="markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm, brToBreak]} skipHtml>{text}</ReactMarkdown>
    </div>
  );
}

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// 리포트 본문에는 문서 원문이 들어가므로 HTML 은 해석하지 않는다(skipHtml).
export default function Markdown({ text }: { text: string }) {
  return (
    <div className="markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml>{text}</ReactMarkdown>
    </div>
  );
}

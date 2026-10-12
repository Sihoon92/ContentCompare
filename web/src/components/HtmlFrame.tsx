// 서버가 만든 자족 HTML(현미경·타임라인)을 격리해서 띄운다. allow-same-origin 을 주지 않으므로
// 그 HTML 의 스크립트는 이 사이트의 쿠키·API 에 접근하지 못한다 — 산출물에는 문서 원문이 들어 있다.
export default function HtmlFrame({ title, html, height }: { title: string; html: string; height: number }) {
  return <iframe className="html-frame" title={title} srcDoc={html} sandbox="allow-scripts" style={{ height }} />;
}

import { useEffect, useState } from "react";
import { getJobReport, jobReportUrl } from "../api/endpoints";
import type { JobResult } from "../api/types";
import DataTable from "./DataTable";
import Markdown from "./Markdown";

// Streamlit 의 show_results / show_fact_results 와 같은 내용. 라벨은 서버가 준 것을 그대로 쓴다.
export default function ResultView({ jobId, result, hasReport }: { jobId: string; result: JobResult; hasReport: boolean }) {
  const [markdown, setMarkdown] = useState("");
  useEffect(() => {
    if (!hasReport) return;
    getJobReport(jobId).then(setMarkdown).catch(() => setMarkdown(""));
  }, [jobId, hasReport]);

  return (
    <section className="result">
      <h3>결과</h3>
      <div className="counts">
        {result.counts.map((c) => (
          <div key={c.key} className="count"><span>{c.label}</span><strong>{c.n}</strong></div>
        ))}
      </div>

      {result.engine === "fact" && result.failed_docs.length > 0 && (
        <div className="error">
          <p>문서 {result.failed_docs.length}건 처리 실패 — 나머지는 계속 처리했습니다.</p>
          <ul>{result.failed_docs.map((d) => <li key={d.name}>{d.name}: {d.error}</li>)}</ul>
        </div>
      )}

      <h4>요약</h4>
      <DataTable rows={result.summary} />

      {result.engine === "rag" && (
        <>
          <h4>상세</h4>
          {result.details.map((d) => (
            <details key={d.index}>
              <summary>{d.index}. {d.source_label} — {d.label}</summary>
              <p><strong>기준 내용</strong>: {d.reference_text}</p>
              {d.is_record ? (
                <>
                  <p><strong>출처(어디에)</strong>: {d.sources.length ? d.sources.join("; ") : "-"}</p>
                  <p><strong>종합 근거(왜)</strong>: {d.reasoning}</p>
                  {d.fields.length > 0 && <DataTable rows={d.fields} />}
                </>
              ) : (
                <p><strong>판단 근거</strong>: {d.reasoning}</p>
              )}
              {d.candidates.length > 0 && (
                <>
                  <p><strong>검색된 후보</strong></p>
                  <ul>
                    {d.candidates.map((c, i) => (
                      <li key={i}>({c.score.toFixed(3)}) {c.source_label}{c.matched ? " ⟵ 매칭" : ""}</li>
                    ))}
                  </ul>
                </>
              )}
            </details>
          ))}
        </>
      )}

      {result.engine === "fact" && <p className="muted">판정이 이상하면 🔬 파이프라인 현미경 탭에서 원인을 추적하세요.</p>}

      {hasReport && (
        <>
          <div className="row">
            <a className="button secondary" href={jobReportUrl(jobId)}>📥 리포트(.md) 다운로드</a>
          </div>
          <details>
            <summary>📄 리포트(Markdown) 미리보기</summary>
            <Markdown text={markdown} />
          </details>
        </>
      )}
    </section>
  );
}

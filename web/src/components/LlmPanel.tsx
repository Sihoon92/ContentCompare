import { useState } from "react";
import { errorText } from "../api/client";
import { checkLlm } from "../api/endpoints";
import type { LlmCheck } from "../api/types";

// 좌측 패널에는 이 버튼만 둔다(설계 §8.1). 접속 정보는 서버의 .env 에서 온다.
export default function LlmPanel() {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<LlmCheck | null>(null);
  const [error, setError] = useState("");

  async function run() {
    setBusy(true);
    setError("");
    try {
      setResult(await checkLlm());
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <aside className="side-panel">
      <button className="button primary wide" disabled={busy} onClick={run}>
        {busy ? "점검 중..." : "🔌 LLM 연결 테스트"}
      </button>
      {error && <p className="error">{error}</p>}
      {result && (
        <div className="check-result">
          <ul>
            {result.results.map((r, i) => (
              <li key={i} className={r.ok ? "ok" : "bad"}>{r.line}</li>
            ))}
          </ul>
          <p className="muted">
            {result.ok ? "모두 ✅ 면 비교 실행 준비 완료" : "실패 항목의 메시지를 확인하세요"}
            {result.cached ? " (30초 안의 점검 결과를 다시 보여 줍니다)" : ""}
          </p>
        </div>
      )}
    </aside>
  );
}

import { memo, useEffect, useRef, useState } from "react";

const WARN = /(ERROR|WARNING|실패|주의|⚠)/;

function LogPanel({ lines }: { lines: string[] }) {
  const [follow, setFollow] = useState(true);
  const box = useRef<HTMLPreElement>(null);
  useEffect(() => {
    if (follow && box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [lines, follow]);
  return (
    <div className="log-panel">
      <div className="log-head">
        <span>로그</span>
        <label>
          <input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> 자동 스크롤
        </label>
      </div>
      <pre ref={box} className="log-body">
        {lines.length === 0 && <span className="muted">아직 로그가 없습니다.</span>}
        {lines.map((line, i) => (
          <div key={i} className={WARN.test(line) ? "log-line warn" : "log-line"}>{line}</div>
        ))}
      </pre>
    </div>
  );
}

// lines 만 바뀔 때 다시 그린다(경과 시간 타이머 등 부모의 다른 갱신에는 반응하지 않는다).
export default memo(LogPanel);

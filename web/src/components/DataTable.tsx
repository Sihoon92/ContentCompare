import type { Row } from "../api/types";

// 서버가 만든 표(ui.runner 의 행)를 그대로 그린다. 열 이름도 서버가 정한다.
export default function DataTable({ rows }: { rows: Row[] }) {
  if (rows.length === 0) return <p className="muted">표시할 행이 없습니다.</p>;
  const columns: string[] = [];
  for (const row of rows) {
    for (const key of Object.keys(row)) if (!columns.includes(key)) columns.push(key);
  }
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>{columns.map((c) => <th key={c}>{c}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              {columns.map((c) => <td key={c}>{row[c] == null ? "" : String(row[c])}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

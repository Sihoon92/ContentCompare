import { Link } from "react-router-dom";
import { useRequester } from "../lib/requester";

export default function Header() {
  const [name, setName] = useRequester();
  return (
    <header className="app-header">
      <Link to="/" className="brand">📑 ContentCompare</Link>
      <label className="requester">
        이름
        <input value={name} maxLength={40} placeholder="요청자 이름" onChange={(e) => setName(e.target.value)} />
      </label>
      <Link to="/admin" className="button secondary">🔐 관리자</Link>
    </header>
  );
}

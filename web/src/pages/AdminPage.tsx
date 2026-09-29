import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { errorText } from "../api/client";
import { adminLogin, adminLogout, adminStatus } from "../api/endpoints";
import type { AdminStatus } from "../api/types";
import AdminJobs from "../components/AdminJobs";
import LogViewer from "../components/LogViewer";

export default function AdminPage() {
  const [status, setStatus] = useState<AdminStatus | null>(null);
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  const refresh = useCallback(() => {
    adminStatus().then(setStatus).catch((e) => setError(errorText(e)));
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  async function login(e: FormEvent) {
    e.preventDefault();
    setError("");
    try {
      await adminLogin(password);
      setPassword("");
      refresh();
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function logout() {
    await adminLogout().catch(() => undefined);
    refresh();
  }

  return (
    <div className="page">
      <div className="row">
        <Link to="/">← 처음으로</Link>
        {status?.logged_in && <button className="button secondary" onClick={logout}>로그아웃</button>}
      </div>
      <h2>관리자</h2>
      {error && <p className="error">{error}</p>}
      {status && !status.enabled && (
        <p className="warn">관리자 기능이 설정되지 않았습니다. 서버의 .env 에 CC_ADMIN_PASSWORD 를 넣고 서버를 다시 시작하세요.</p>
      )}
      {status?.enabled && !status.logged_in && (
        <form className="row" onSubmit={login}>
          <input type="password" placeholder="관리자 비밀번호" value={password} onChange={(e) => setPassword(e.target.value)} />
          <button className="button primary" type="submit">로그인</button>
        </form>
      )}
      {status?.logged_in && (
        <>
          {/* 세션이 끝나면(401) 두 컴포넌트가 refresh 를 불러 상태를 다시 읽는다 — 로그인 폼이 뜨며 폴링도 멈춘다 */}
          <LogViewer onUnauthorized={refresh} />
          <AdminJobs onUnauthorized={refresh} />
        </>
      )}
    </div>
  );
}

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, errorText } from "../api/client";
import { getKnowledge, getMergedKnowledge, listKnowledge, saveKnowledge } from "../api/endpoints";
import type { KnowledgeConflict, KnowledgeList } from "../api/types";
import { knowledgeConflict } from "../lib/knowledge";

const NEW = "__new__";

export default function KnowledgeTab() {
  const [list, setList] = useState<KnowledgeList | null>(null);
  const [pick, setPick] = useState(NEW);
  const [name, setName] = useState("domain.md");
  const [content, setContent] = useState("");
  const [baseMtime, setBaseMtime] = useState<number | null>(null);
  const [conflict, setConflict] = useState<KnowledgeConflict | null>(null);
  const [merged, setMerged] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  // 목록 응답이 늦게 도착했을 때 사용자가 이미 다른 파일을 골랐는지 확인하려고 최신 pick 을 들고 있는다.
  const pickRef = useRef(pick);
  pickRef.current = pick;
  // 저장 직후 pick 이 저장한 파일명으로 바뀌면(새 파일) 그 변경은 사용자가 고른 것이 아니므로 안내를 지우지 않는다.
  const keepNoticeFor = useRef<string | null>(null);

  const loadList = useCallback(async () => {
    const l = await listKnowledge();
    setList(l);
    return l;
  }, []);

  useEffect(() => {
    let cancelled = false;
    listKnowledge()
      .then((l) => {
        if (cancelled) return;
        setList(l);
        setError("");
        // 아직 새 파일 편집 상태일 때만 템플릿을 채운다(그 사이 기존 파일을 골랐다면 덮지 않는다).
        if (pickRef.current === NEW) setContent(l.template);
      })
      .catch((e) => { if (!cancelled) setError(errorText(e)); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    setConflict(null);
    if (keepNoticeFor.current !== pick) setNotice("");
    keepNoticeFor.current = null;
    if (pick === NEW) {
      setName("domain.md");
      setContent(list?.template ?? "");
      setBaseMtime(null);
      return;
    }
    // 파일을 빠르게 바꿔 고르면 앞선 요청의 응답이 나중에 도착할 수 있다 — 오래된 응답은 버린다.
    let cancelled = false;
    getKnowledge(pick)
      .then((f) => {
        if (cancelled) return;
        setName(f.name);
        setContent(f.content);
        setBaseMtime(f.mtime);
        setError("");
      })
      .catch((e) => { if (!cancelled) setError(errorText(e)); });
    return () => { cancelled = true; };
    // list 가 바뀔 때(저장 후 목록 갱신)마다 편집 중인 내용을 덮지 않도록 일부러 pick 에만 반응한다.
  }, [pick]);

  async function save(mtime: number | null) {
    if (saving) return;
    const startPick = pickRef.current;
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const r = await saveKnowledge(name, content, mtime);
      // 응답이 오기 전에 다른 파일로 옮겨 갔다면 그 파일의 상태를 건드리지 않는다.
      if (pickRef.current !== startPick) return;
      setBaseMtime(r.mtime);
      setConflict(null);
      setNotice(`저장됨: ${r.name}`);
      setMerged(null);
      // 저장은 성공했으므로 목록 새로고침 실패는 저장 실패로 보고하지 않는다.
      try {
        await loadList();
      } catch (e) {
        if (pickRef.current === startPick) setError(`저장은 되었지만 파일 목록을 새로고침하지 못했습니다: ${errorText(e)}`);
      }
      if (pickRef.current !== startPick) return;
      if (r.name !== startPick) keepNoticeFor.current = r.name;
      setPick(r.name);
    } catch (e) {
      if (pickRef.current !== startPick) return;
      const c = e instanceof ApiError && e.status === 409 ? knowledgeConflict(e.body) : null;
      if (c) setConflict(c);
      else setError(errorText(e));
    } finally {
      setSaving(false);
    }
  }

  function loadServerCopy() {
    if (!conflict) return;
    setContent(conflict.current.content);
    setBaseMtime(conflict.current.mtime);
    setConflict(null);
  }

  function toggleMerged(open: boolean) {
    if (open && merged === null) getMergedKnowledge().then((r) => setMerged(r.text)).catch((e) => setError(errorText(e)));
  }

  return (
    <div>
      <p className="muted">
        LLM 이 모르는 사내 용어·암묵지를 Markdown 으로 정리하면, 비교 분석 시 참고 자료로 항상 주입됩니다(예: formation = 배터리 화성 공정).
      </p>
      {list && !list.enabled && <p className="warn">현재 설정에서 도메인 지식 주입이 꺼져 있습니다(config: knowledge.enabled).</p>}

      <div className="row">
        <select value={pick} disabled={saving} onChange={(e) => setPick(e.target.value)}>
          <option value={NEW}>+ 새 파일 만들기</option>
          {list?.files.map((f) => <option key={f.name} value={f.name}>{f.name}</option>)}
        </select>
        {pick === NEW && (
          <input value={name} disabled={saving} onChange={(e) => setName(e.target.value)} placeholder="새 파일 이름(.md)" />
        )}
      </div>

      <textarea rows={18} value={content} onChange={(e) => setContent(e.target.value)} />

      <div className="row">
        <button className="button primary" disabled={saving} onClick={() => save(baseMtime)}>💾 저장</button>
        {notice && <span className="muted">{notice}</span>}
      </div>
      {error && <p className="error">{error}</p>}

      {conflict && (
        <div className="conflict">
          <p>{conflict.message}</p>
          <div className="row">
            <button className="button secondary" onClick={loadServerCopy}>서버에 저장된 내용 불러오기</button>
            <button className="button danger" disabled={saving} onClick={() => save(conflict.current.mtime)}>내 내용으로 덮어쓰기</button>
          </div>
          <details>
            <summary>서버에 저장된 내용 보기</summary>
            <pre className="file-list">{conflict.current.content}</pre>
          </details>
        </div>
      )}

      <details onToggle={(e) => toggleMerged((e.target as HTMLDetailsElement).open)}>
        <summary>🔎 비교 시 주입될 전체 지식 미리보기</summary>
        <pre className="file-list">{merged ?? "불러오는 중..."}</pre>
      </details>
    </div>
  );
}

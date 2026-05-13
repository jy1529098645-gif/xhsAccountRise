import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { fmtBytes, fmtRelative, fmtTime } from "../format";
import type { Library } from "../types";

export default function Libraries() {
  const [libs, setLibs] = useState<Library[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [working, setWorking] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [displayName, setDisplayName] = useState("");

  async function load() {
    try { setLibs(await api.libraries()); }
    catch (e: any) { setErr(e.message); }
  }
  useEffect(() => { load(); }, []);

  async function upload() {
    const f = fileRef.current?.files?.[0];
    if (!f) { setErr("请先选择 .db 文件"); return; }
    if (!displayName.trim()) { setErr("请填库名"); return; }
    setUploading(true); setErr(null);
    try {
      await api.uploadLibrary(f, displayName.trim());
      setInfo(`✓ 已上传 ${f.name}`); setDisplayName("");
      if (fileRef.current) fileRef.current.value = "";
      await load();
    } catch (e: any) { setErr(e.message); }
    finally { setUploading(false); }
  }

  async function activate(libId: string) {
    setWorking(libId); setErr(null); setInfo(null);
    try { await api.activateLibrary(libId); setInfo(`✓ 已切换到 ${libId}`); await load(); }
    catch (e: any) { setErr(e.message); }
    finally { setWorking(null); }
  }

  async function analyze(libId: string) {
    setWorking(libId); setErr(null); setInfo("分析中…(可能 10-30s)");
    try {
      const res = await api.analyzeLibrary(libId);
      setInfo(`✓ 分析完成 · DNA v${res.dna_version} · 索引 ${res.fts.notes_indexed} notes`);
      await load();
    } catch (e: any) { setErr(e.message); }
    finally { setWorking(null); }
  }

  async function del(libId: string) {
    if (!confirm(`确认删除 ${libId}?`)) return;
    setWorking(libId); setErr(null);
    try { await api.deleteLibrary(libId); setInfo(`✓ 已删除 ${libId}`); await load(); }
    catch (e: any) { setErr(e.message); }
    finally { setWorking(null); }
  }

  const offline = !api.isConnected();

  return (
    <div>
      <div className="page-header">
        <h1>Libraries · 多语料管理</h1>
        <p>每个 library 是一个独立 SQLite .db。可上传不同赛道的语料 → 切换 → 重跑 DNA → 出不同策略的稿件。</p>
      </div>

      {err && <div className="banner danger" onClick={() => setErr(null)}>{err}</div>}
      {info && <div className="banner info" onClick={() => setInfo(null)}>{info}</div>}
      {offline && <div className="banner warn">上传/激活/删除/重分析都需要本地后端。当前只读。</div>}

      <div className="card">
        <h2>上传新语料</h2>
        <p className="muted">支持任意 xhs.db（schema 跟 crawler 输出一致：notes + comments + discover_queue 等）。</p>
        <div className="row" style={{gap: 10, alignItems: "flex-end"}}>
          <div style={{flex: "0 0 220px"}}>
            <label>库名（显示用）</label>
            <input value={displayName} onChange={e => setDisplayName(e.target.value)}
              placeholder="例如：考研写作-2026" disabled={offline} />
          </div>
          <div style={{flex: 1}}>
            <label>选择 .db 文件</label>
            <input ref={fileRef} type="file" accept=".db,.sqlite,.sqlite3" disabled={offline} />
          </div>
          <button onClick={upload} disabled={uploading || offline}>{uploading ? "上传中…" : "上传"}</button>
        </div>
      </div>

      <div className="card">
        <h2>已有 libraries ({libs.length})</h2>
        {libs.length === 0 ? (
          <p className="muted">还没有库。先上传一个 .db 上来。</p>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>状态</th><th>lib_id</th><th>名称</th>
                <th className="num">notes</th><th className="num">评论</th><th className="num">大小</th>
                <th>来源</th><th>添加时间</th><th></th>
              </tr>
            </thead>
            <tbody>
              {libs.map(l => (
                <tr key={l.lib_id} style={l.active ? {background: "var(--primary-soft)"} : undefined}>
                  <td>{l.active ? "★ active" : <span className="muted">—</span>}</td>
                  <td><code className="kbd">{l.lib_id}</code></td>
                  <td>{l.display_name}</td>
                  <td className="num">{l.notes_count.toLocaleString()}</td>
                  <td className="num">{l.comments_count.toLocaleString()}</td>
                  <td className="num">{fmtBytes(l.size_bytes)}</td>
                  <td className="muted">{l.source}</td>
                  <td className="muted" title={fmtTime(l.uploaded_at)}>{fmtRelative(l.uploaded_at)}</td>
                  <td>
                    <div className="row" style={{gap: 4}}>
                      {!l.active && (
                        <button className="secondary" style={{padding: "4px 8px", fontSize: 12}}
                          disabled={offline || !!working}
                          onClick={() => activate(l.lib_id)}>激活</button>
                      )}
                      <button className="secondary" style={{padding: "4px 8px", fontSize: 12}}
                        disabled={offline || working === l.lib_id}
                        onClick={() => analyze(l.lib_id)}>
                        {working === l.lib_id ? "..." : "重跑分析"}
                      </button>
                      {!l.active && (
                        <button className="ghost" style={{padding: "4px 8px", fontSize: 12, color: "var(--danger)"}}
                          disabled={offline || !!working}
                          onClick={() => del(l.lib_id)}>删除</button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="card">
        <h2>工作流</h2>
        <ol style={{marginLeft: 20, fontSize: 13, lineHeight: 1.9}}>
          <li>上传 .db → 它会被复制到 <code className="kbd">data/libraries/&lt;lib_id&gt;/xhs.db</code></li>
          <li>「重跑分析」会切到该库 → 重建 FTS5 → 跑 DNA → promote hooks → 切回原激活库</li>
          <li>「激活」让该库成为当前 RAG/Composer 的数据源</li>
          <li>跑 Composer → 出稿基于激活库的语料 → 不同库 = 不同策略</li>
        </ol>
      </div>
    </div>
  );
}

import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { fmtBytes, fmtRelative, fmtTime } from "../format";
import PlatformPill from "../components/PlatformPill";
import type { Library, Platform } from "../types";

export default function Libraries() {
  const [libs, setLibs] = useState<Library[]>([]);
  const [platforms, setPlatforms] = useState<Platform[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [working, setWorking] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [displayName, setDisplayName] = useState("");
  const [platform, setPlatform] = useState("xiaohongshu");
  const fileRef = useRef<HTMLInputElement>(null);

  async function load() {
    try { setLibs(await api.libraries()); }
    catch (e: any) { setErr(e.message); }
  }
  useEffect(() => {
    load();
    api.platforms().then(setPlatforms).catch(() => {});
  }, []);

  function pickFile(f: File | null) {
    if (!f) return;
    if (!/\.(db|sqlite|sqlite3)$/i.test(f.name)) {
      setErr(`不像是 SQLite .db 文件：${f.name}`);
      return;
    }
    setErr(null);
    setPendingFile(f);
    // Auto-fill display name from filename.
    if (!displayName) {
      const base = f.name.replace(/\.(db|sqlite|sqlite3)$/i, "");
      setDisplayName(base);
    }
  }

  function onDrop(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragOver(false);
    const f = e.dataTransfer.files?.[0];
    if (f) pickFile(f);
  }

  async function upload() {
    const f = pendingFile ?? fileRef.current?.files?.[0] ?? null;
    if (!f) { setErr("请先选 .db 文件或拖进来"); return; }
    if (!displayName.trim()) { setErr("请填库名"); return; }
    setUploading(true); setErr(null); setInfo(null);
    try {
      const m = await api.uploadLibrary(f, displayName.trim(), platform);
      setInfo(`✓ 已上传 ${f.name} · ${m.notes_count.toLocaleString()} notes · 平台=${platform}`);
      setDisplayName("");
      setPendingFile(null);
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

  async function changePlatform(libId: string, newPlatform: string) {
    setWorking(libId); setErr(null);
    try {
      await api.setLibraryPlatform(libId, newPlatform);
      setInfo(`✓ ${libId} 平台 → ${newPlatform}`);
      await load();
    } catch (e: any) { setErr(e.message); }
    finally { setWorking(null); }
  }

  const offline = !api.isConnected();

  return (
    <div>
      <div className="page-header">
        <h1>Libraries · 多语料管理</h1>
        <p>每个 library 是独立 SQLite。可标记不同平台（小红书 / 抖音 / 快手 / B站 / YouTube / Reddit / X / 其他），切换后会用对应平台的写作风格出稿。</p>
      </div>

      {err && <div className="banner danger" onClick={() => setErr(null)}>{err}</div>}
      {info && <div className="banner info" onClick={() => setInfo(null)}>{info}</div>}
      {offline && <div className="banner warn">上传/激活/删除/重分析都需要本地后端。当前只读。</div>}

      <div className="card">
        <h2>上传新语料</h2>
        <div
          className={`dropzone ${dragOver ? "drag-over" : ""} ${pendingFile ? "has-file" : ""}`}
          onDragOver={e => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={onDrop}
          onClick={() => fileRef.current?.click()}
        >
          <input ref={fileRef} type="file" accept=".db,.sqlite,.sqlite3"
            style={{ display: "none" }}
            onChange={e => pickFile(e.target.files?.[0] ?? null)} />
          {pendingFile ? (
            <>
              <div style={{ fontSize: 28 }}>📦</div>
              <div><b>{pendingFile.name}</b></div>
              <div className="muted">{fmtBytes(pendingFile.size)} · 点击换文件</div>
            </>
          ) : (
            <>
              <div style={{ fontSize: 28 }}>📂</div>
              <div><b>拖拽 .db 文件到此处</b> 或点击选择</div>
              <div className="muted">支持 .db / .sqlite / .sqlite3</div>
            </>
          )}
        </div>

        <div className="row" style={{ gap: 10, marginTop: 12, alignItems: "flex-end" }}>
          <div style={{ flex: 1 }}>
            <label>库名（显示用）</label>
            <input value={displayName} onChange={e => setDisplayName(e.target.value)}
              placeholder="例如：考研写作-2026" disabled={offline} />
          </div>
          <div style={{ flex: "0 0 180px" }}>
            <label>平台</label>
            <select value={platform} onChange={e => setPlatform(e.target.value)} disabled={offline}>
              {platforms.map(p => <option key={p.id} value={p.id}>{p.label}</option>)}
            </select>
          </div>
          <button onClick={upload} disabled={uploading || offline || !pendingFile}>
            {uploading ? "上传中…" : "上传"}
          </button>
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
                <th>状态</th><th>lib_id</th><th>名称</th><th>平台</th>
                <th className="num">notes</th><th className="num">评论</th><th className="num">大小</th>
                <th>添加时间</th><th></th>
              </tr>
            </thead>
            <tbody>
              {libs.map(l => (
                <tr key={l.lib_id} style={l.active ? { background: "var(--primary-soft)" } : undefined}>
                  <td>{l.active ? "★ active" : <span className="muted">—</span>}</td>
                  <td><code className="kbd">{l.lib_id}</code></td>
                  <td>
                    {l.display_name}
                    <div style={{marginTop: 3}}><PlatformPill platform={l.platform} /></div>
                  </td>
                  <td>
                    <select value={l.platform} disabled={offline || working === l.lib_id}
                      onChange={e => changePlatform(l.lib_id, e.target.value)}
                      style={{ fontSize: 12, padding: "2px 6px" }}>
                      {platforms.map(p => <option key={p.id} value={p.id}>{p.label}</option>)}
                    </select>
                  </td>
                  <td className="num">{l.notes_count.toLocaleString()}</td>
                  <td className="num">{l.comments_count.toLocaleString()}</td>
                  <td className="num">{fmtBytes(l.size_bytes)}</td>
                  <td className="muted" title={fmtTime(l.uploaded_at)}>{fmtRelative(l.uploaded_at)}</td>
                  <td>
                    <div className="row" style={{ gap: 4 }}>
                      {!l.active && (
                        <button className="secondary" style={{ padding: "4px 8px", fontSize: 12 }}
                          disabled={offline || !!working}
                          onClick={() => activate(l.lib_id)}>激活</button>
                      )}
                      <button className="secondary" style={{ padding: "4px 8px", fontSize: 12 }}
                        disabled={offline || working === l.lib_id}
                        onClick={() => analyze(l.lib_id)}>
                        {working === l.lib_id ? "..." : "重跑分析"}
                      </button>
                      {!l.active && (
                        <button className="ghost" style={{ padding: "4px 8px", fontSize: 12, color: "var(--danger)" }}
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
        <h2>关于平台与 schema</h2>
        <p className="muted" style={{ fontSize: 13 }}>
          数据库 schema 默认按小红书来（<code className="kbd">notes</code>/<code className="kbd">comments</code>/<code className="kbd">discover_queue</code>...）。
          上传抖音 / B站 / YouTube 等其他平台库时，只要表名和关键字段（title/body/like/collect/comment）对得上，多 Agent 会按你选的平台风格出稿。
        </p>
        <p className="muted" style={{ fontSize: 13 }}>
          不一致的 schema 会让分析失败 —— 这种情况下建议先用一个 ETL 把源平台的字段映射到 xhs schema 再上传。
        </p>
      </div>
    </div>
  );
}

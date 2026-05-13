import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { fmtBytes, fmtRelative, fmtTime, platformLabel } from "../format";
import PlatformPill from "../components/PlatformPill";
import type { Library, Platform } from "../types";

export default function Libraries() {
  const [libs, setLibs] = useState<Library[]>([]);
  const [platforms, setPlatforms] = useState<Platform[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const [importStep, setImportStep] = useState<string>("");
  const [working, setWorking] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [platform, setPlatform] = useState<string>("auto");
  const fileRef = useRef<HTMLInputElement>(null);

  async function load() {
    try { setLibs(await api.libraries()); }
    catch (e: any) { setErr(e.message); }
  }
  useEffect(() => {
    load();
    api.platforms().then(setPlatforms).catch(() => {});
  }, []);

  async function handleFile(f: File | null) {
    if (!f) return;
    if (!/\.(db|sqlite|sqlite3)$/i.test(f.name)) {
      setErr(`只接受 SQLite (.db / .sqlite / .sqlite3)：${f.name}`);
      return;
    }
    setErr(null);
    const displayName = f.name.replace(/\.(db|sqlite|sqlite3)$/i, "");
    await runImport(f, displayName);
  }

  async function runImport(f: File, displayName: string) {
    setImporting(true);
    setImportStep("📤 上传文件…");
    setInfo(null);
    try {
      // The /api/libraries/import endpoint does: upload + detect + activate + analyze in one shot
      setImportStep("📦 解析 + 检测平台 + 建索引 + 跑 DNA…(10-30s)");
      const res = await api.importLibrary(f, displayName, platform);
      const platDetected = res.detected_platform
        ? `（自动识别为 ${platformLabel(res.detected_platform)}）`
        : `（用户指定 ${platformLabel(res.platform)}）`;
      const dnaPart = res.dna_version ? ` · DNA v${res.dna_version}` : "";
      setInfo(`✓ 全部就绪 · ${res.lib_id} · ${res.notes_count.toLocaleString()} notes${dnaPart} ${platDetected}`);
      await load();
    } catch (e: any) { setErr(e.message); }
    finally {
      setImporting(false);
      setImportStep("");
    }
  }

  async function activate(libId: string) {
    setWorking(libId); setErr(null); setInfo(null);
    try { await api.activateLibrary(libId); setInfo(`✓ 已切换到 ${libId}`); await load(); }
    catch (e: any) { setErr(e.message); }
    finally { setWorking(null); }
  }

  async function analyze(libId: string) {
    setWorking(libId); setErr(null); setInfo("🧬 分析中…");
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
      setInfo(`✓ ${libId} 平台 → ${platformLabel(newPlatform)}`);
      await load();
    } catch (e: any) { setErr(e.message); }
    finally { setWorking(null); }
  }

  const offline = !api.isConnected();
  const isEmpty = libs.length === 0;

  return (
    <div>
      <div className="page-header">
        <h1>📥 资源库 · 上传 / 管理</h1>
        <p>
          {isEmpty
            ? "把任意 .db 拖进下方区域，几秒后就能在 Composer 出稿。"
            : `当前 ${libs.length} 个库 · 拖新文件进来直接添加 / 一键切换激活库`}
        </p>
      </div>

      {err && <div className="banner danger" onClick={() => setErr(null)}>{err}</div>}
      {info && <div className="banner info" onClick={() => setInfo(null)}>{info}</div>}

      <div
        className={`hero-drop ${dragOver ? "drag-over" : ""} ${importing ? "has-file" : ""}`}
        onDragOver={e => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={e => { e.preventDefault(); setDragOver(false); handleFile(e.dataTransfer.files?.[0] ?? null); }}
        onClick={() => !importing && fileRef.current?.click()}
        style={{ pointerEvents: importing ? "none" : "auto", opacity: importing ? 0.85 : 1 }}
      >
        <input ref={fileRef} type="file" accept=".db,.sqlite,.sqlite3"
          style={{ display: "none" }}
          onChange={e => handleFile(e.target.files?.[0] ?? null)} />
        {importing ? (
          <>
            <div className="big-icon">⏳</div>
            <h2>{importStep || "处理中…"}</h2>
            <div className="muted">不要关闭页面</div>
          </>
        ) : (
          <>
            <div className="big-icon">📂</div>
            <h2>把数据库拖进来 / 点击选择</h2>
            <div className="muted">
              支持 .db / .sqlite / .sqlite3 · 自动检测平台 · 自动跑 DNA 分析 + 激活 · 完事直接去 Composer 出稿
            </div>
          </>
        )}
      </div>

      <div className="row" style={{ justifyContent: "space-between", alignItems: "center", marginBottom: 20 }}>
        <div>
          <label style={{ marginBottom: 4 }}>平台来源</label>
          <select value={platform} onChange={e => setPlatform(e.target.value)} disabled={offline || importing}
            style={{ minWidth: 200 }}>
            <option value="auto">🪄 自动检测（推荐）</option>
            {platforms.map(p => <option key={p.id} value={p.id}>{p.label}</option>)}
          </select>
        </div>
        <div className="muted" style={{ fontSize: 12, textAlign: "right", maxWidth: 360 }}>
          自动检测看 SQLite schema 里的特征字段（note_id / aweme_id / bvid 等）匹配平台。
          不准就在表格里手动改。
        </div>
      </div>

      {!isEmpty && (
        <div className="card">
          <h2>已有 libraries</h2>
          <table className="table">
            <thead>
              <tr>
                <th>状态</th><th>名称 / lib_id</th><th>平台</th>
                <th className="num">notes</th><th className="num">评论</th><th className="num">大小</th>
                <th>添加</th><th>操作</th>
              </tr>
            </thead>
            <tbody>
              {libs.map(l => (
                <tr key={l.lib_id} style={l.active ? { background: "var(--primary-soft)" } : undefined}>
                  <td>{l.active ? "★ active" : <span className="muted">—</span>}</td>
                  <td>
                    {l.display_name}
                    <div style={{ marginTop: 3 }}>
                      <code className="kbd">{l.lib_id}</code>
                    </div>
                  </td>
                  <td>
                    <PlatformPill platform={l.platform} />
                    <select value={l.platform} disabled={offline || working === l.lib_id}
                      onChange={e => changePlatform(l.lib_id, e.target.value)}
                      style={{ fontSize: 11, padding: "1px 4px", marginTop: 4, display: "block" }}>
                      {platforms.map(p => <option key={p.id} value={p.id}>{p.label}</option>)}
                    </select>
                  </td>
                  <td className="num">{l.notes_count.toLocaleString()}</td>
                  <td className="num">{l.comments_count.toLocaleString()}</td>
                  <td className="num">{fmtBytes(l.size_bytes)}</td>
                  <td className="muted" title={fmtTime(l.uploaded_at)}>{fmtRelative(l.uploaded_at)}</td>
                  <td>
                    <div className="row" style={{ gap: 4, flexWrap: "wrap" }}>
                      {!l.active && (
                        <button className="secondary" style={{ padding: "4px 8px", fontSize: 12 }}
                          disabled={offline || !!working}
                          onClick={() => activate(l.lib_id)}>激活</button>
                      )}
                      <button className="secondary" style={{ padding: "4px 8px", fontSize: 12 }}
                        disabled={offline || working === l.lib_id}
                        onClick={() => analyze(l.lib_id)}>
                        {working === l.lib_id ? "..." : "重跑 DNA"}
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
        </div>
      )}

      <div className="card" style={{ background: "#fafafa" }}>
        <h2>📖 怎么用 / 平台兼容性</h2>
        <ol style={{ marginLeft: 20, lineHeight: 1.8, fontSize: 13 }}>
          <li>把任意 SQLite .db 拖到上面的大框 → 自动检测平台、跑 DNA、激活 → 几十秒后去 Composer 用</li>
          <li><b>平台自动识别</b>看 schema 里的关键字段：<code className="kbd">xsec_token</code>=小红书 / <code className="kbd">aweme_id</code>=抖音 / <code className="kbd">bvid</code>=B站 / 等</li>
          <li><b>schema 要求</b>：核心表 <code className="kbd">notes</code>（含 title/body/liked_count/note_id）+ <code className="kbd">comments</code>。其他平台需要 ETL 映射到这个 schema。</li>
          <li>已有库 → 行内可换平台 / 重跑 DNA / 切换激活 / 删除</li>
          <li>跑稿 → <Link to="/composer">✍️ Composer</Link>；看历史 → <Link to="/drafts">📝 Drafts</Link></li>
        </ol>
      </div>
    </div>
  );
}

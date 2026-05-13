import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { fmtBytes, fmtRelative, fmtTime, platformLabel } from "../format";
import PlatformPill from "../components/PlatformPill";
import { PLATFORM_GUIDES, GITHUB_REPO } from "../catalog";
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
  const [expandedGuide, setExpandedGuide] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

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
    setImportStep("📤 上传 + 自动嗅探平台 + 建索引 + 跑 DNA…");
    setInfo(null);
    try {
      const res = await api.importLibrary(f, displayName, platform);
      const platDetected = res.detected_platform
        ? `（🪄 自动识别为 ${platformLabel(res.detected_platform)}）`
        : `（你选了 ${platformLabel(res.platform)}）`;
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

  async function runInsight(libId: string) {
    setWorking(libId); setErr(null);
    setInfo("📊 双 AI (Claude + OpenAI) 协作分析中…(约 60-180s)");
    try {
      // Activate the library first so the latest DNA artifact is used.
      await api.activateLibrary(libId);
      const r = await api.runInsight(libId);
      setInfo("✓ 分析完成，跳转到报告…");
      setTimeout(() => navigate(`/insight/${r.report_id}`), 600);
    } catch (e: any) { setErr(e.message); }
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
        <h1>📥 资源库 · 拖一个 .db 进来就能用</h1>
        <p>
          {isEmpty
            ? "把任意平台的 SQLite 数据库拖进来，自动检测平台 → 自动建索引 → 自动跑分析 → 直接去 Composer 出稿。"
            : `当前 ${libs.length} 个库 · 拖新文件进来直接添加 / 行内可改平台`}
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
            <div className="muted">不要关闭页面（10-30s）</div>
          </>
        ) : (
          <>
            <div className="big-icon">📂</div>
            <h2>把数据库拖到这里 / 点击选择</h2>
            <div className="muted">
              SQLite (.db / .sqlite / .sqlite3) · 自动检测平台 · 自动跑分析 + 激活 · 完事直接出稿
            </div>
          </>
        )}
      </div>

      <div className="row" style={{ justifyContent: "space-between", alignItems: "center", marginBottom: 20 }}>
        <div>
          <label style={{ marginBottom: 4 }}>来源平台</label>
          <select value={platform} onChange={e => setPlatform(e.target.value)} disabled={offline || importing}
            style={{ minWidth: 200 }}>
            <option value="auto">🪄 自动检测（推荐，看 schema 字段）</option>
            {platforms.map(p => <option key={p.id} value={p.id}>{p.label}</option>)}
          </select>
        </div>
        <div className="muted" style={{ fontSize: 12, textAlign: "right", maxWidth: 360 }}>
          自动检测看 SQLite schema 里的特征字段命中得分（note_id / aweme_id / bvid / ...）。
          检测不准就在表格里手动改。
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
                  <td>{l.active ? "★ 激活中" : <span className="muted">—</span>}</td>
                  <td>{l.display_name}</td>
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
                          onClick={() => activate(l.lib_id)}>切换为当前</button>
                      )}
                      <button className="secondary" style={{ padding: "4px 8px", fontSize: 12 }}
                        disabled={offline || working === l.lib_id}
                        onClick={() => analyze(l.lib_id)}>
                        {working === l.lib_id ? "..." : "重跑分析"}
                      </button>
                      <button style={{ padding: "4px 8px", fontSize: 12 }}
                        disabled={offline || working === l.lib_id}
                        title="Claude × OpenAI 双 AI 协作出一份共识分析报告"
                        onClick={() => runInsight(l.lib_id)}>
                        📊 双 AI 报告
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

      <div className="card">
        <h2>📖 各平台数据库格式说明（点开看建议字段）</h2>
        <p className="muted" style={{fontSize: 13}}>
          本工具按一套统一 schema 工作：核心两张表 <code className="kbd">notes</code> + <code className="kbd">comments</code>。
          不同平台的字段对应见下表。如果你的 .db 字段名不一致，可以写一个 SQL VIEW 别名或先 ETL。
        </p>
        <div className="platform-guides">
          {PLATFORM_GUIDES.map(g => {
            const expanded = expandedGuide === g.id;
            return (
              <div key={g.id} className="platform-guide-card"
                onClick={() => setExpandedGuide(expanded ? null : g.id)}
                style={{cursor: "pointer"}}>
                <h3>{g.emoji} {g.label}</h3>
                <div className="section">
                  <b>必备表：</b><code>{g.expectedTables}</code>
                </div>
                <div className="section">
                  <b>核心字段：</b>
                  <div className="field-list">
                    {g.keyFields.slice(0, expanded ? 99 : 4).map(f => <code key={f}>{f}</code>)}
                    {!expanded && g.keyFields.length > 4 && <span className="muted" style={{fontSize: 11}}>+{g.keyFields.length - 4} 更多</span>}
                  </div>
                </div>
                {expanded && (
                  <>
                    <div className="section"><b>每行内容：</b>{g.contentExamples}</div>
                    <div className="section"><b>推荐数据源：</b>{g.bestSource}</div>
                    {g.notes && <div className="section muted" style={{fontStyle: "italic"}}>💡 {g.notes}</div>}
                  </>
                )}
                <div className="muted" style={{fontSize: 11, textAlign: "right"}}>{expanded ? "▴ 收起" : "▾ 展开详情"}</div>
              </div>
            );
          })}
        </div>
        <p className="muted" style={{fontSize: 12, marginTop: 16}}>
          没有现成 .db？可以参考 <a href={GITHUB_REPO + "#%E5%A4%9A%E5%B9%B3%E5%8F%B0--%E5%A4%9A-library"} target="_blank" rel="noreferrer">仓库 README</a> 里
          的爬虫推荐，或者自己从 CSV / JSON / API 导出转成 SQLite。
        </p>
      </div>
    </div>
  );
}

import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { fmtBytes, fmtRelative, fmtTime, platformLabel } from "../format";
import PlatformPill from "../components/PlatformPill";
import { GITHUB_REPO } from "../catalog";
import type { Library, Platform } from "../types";

interface ReportSummary {
  report_id: string;
  library_id: string;
  created_at: number;
  status: string;
  elapsed_s: number | null;
}

type Stage = "idle" | "uploading" | "analyzing-dna" | "running-insight" | "done";

export default function Reports() {
  const [libs, setLibs] = useState<Library[]>([]);
  const [reports, setReports] = useState<ReportSummary[]>([]);
  const [platforms, setPlatforms] = useState<Platform[]>([]);
  const [stage, setStage] = useState<Stage>("idle");
  const [progress, setProgress] = useState<string>("");
  const [err, setErr] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [selectedLibId, setSelectedLibId] = useState<string>("");
  const [dragOver, setDragOver] = useState(false);
  const [pendingPlatform, setPendingPlatform] = useState<string>("auto");
  const fileRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  async function load() {
    try {
      const [ls, rs, ps] = await Promise.all([
        api.libraries(), api.listInsights(), api.platforms(),
      ]);
      setLibs(ls);
      const active = ls.find(l => l.active);
      if (active) setSelectedLibId(active.lib_id);
      else if (ls[0]) setSelectedLibId(ls[0].lib_id);
      setReports(rs as any);
      setPlatforms(ps);
    } catch (e: any) {
      setErr(e.message);
    }
  }
  useEffect(() => { load(); }, []);

  // ---- Combined upload → DNA → insight flow ------------------------------
  async function handleFile(f: File | null) {
    if (!f) return;
    if (!/\.(db|sqlite|sqlite3)$/i.test(f.name)) {
      setErr(`只接受 SQLite (.db / .sqlite / .sqlite3)：${f.name}`);
      return;
    }
    setErr(null); setInfo(null);
    const displayName = f.name.replace(/\.(db|sqlite|sqlite3)$/i, "");
    try {
      // Step A: upload + detect + activate + DNA analyze
      setStage("uploading");
      setProgress("📤 上传 + 校验 schema + 嗅探平台…");
      setStage("analyzing-dna");
      setProgress("📦 建索引 + 跑爆款 DNA…（10-30s）");
      const imp = await api.importLibrary(f, displayName, pendingPlatform);

      // ---- Validate the analyze step actually succeeded -------------
      if (imp.analyzed === false || imp.analyze_error) {
        setErr(
          `数据库导入成功（${imp.notes_count.toLocaleString()} 条），但爆款 DNA 分析失败：\n` +
          `${imp.analyze_error || "未知"}\n\n` +
          `常见原因：数据库 schema 不完整（缺关键列）。请检查 notes 表 + 必要字段，` +
          `或换一个标准 xhs 爬取的 .db 重试。也可以去「📥 资源库」页面手动重跑分析看更详细的报错。`
        );
        setStage("idle");
        setProgress("");
        await load();  // refresh lib list so user sees the imported (but unanalyzed) one
        return;
      }

      // Surface non-fatal warnings inline so user knows what was skipped
      if (imp.schema_warnings && imp.schema_warnings.length > 0) {
        setInfo(`✓ 已导入，但有些非致命警告（不影响主流程）：${imp.schema_warnings.join(" · ")}`);
      }
      if (imp.section_errors && Object.keys(imp.section_errors).length > 0) {
        const skipped = Object.keys(imp.section_errors).join("、");
        setInfo((prev) =>
          (prev ?? "") +
          `\n⚠️ 以下 DNA 子部分跳过了（其他分析正常）：${skipped}`
        );
      }

      const platDetected = imp.detected_platform
        ? `识别为 ${platformLabel(imp.detected_platform)}`
        : `用户指定 ${platformLabel(imp.platform)}`;
      setProgress(
        `✓ 已导入 · ${imp.notes_count.toLocaleString()} 条 · ${platDetected} · ` +
        `开始让 Claude × OpenAI 出共识报告…`
      );

      // Step B: run insight report
      setStage("running-insight");
      const r = await api.runInsight(imp.lib_id);
      setProgress("✓ 报告完成，跳转中…");
      setStage("done");
      setTimeout(() => navigate(`/reports/${r.report_id}`), 500);
    } catch (e: any) {
      // Surface schema fatal errors cleanly
      let msg: string = e.message || String(e);
      if (msg.includes("422")) {
        msg = "数据库 schema 不兼容：" + msg.replace(/^.+422[^:]*:\s*/, "");
      }
      setErr(msg);
      setStage("idle");
      setProgress("");
    }
  }

  // ---- Re-analyze an existing library -----------------------------------
  async function reAnalyze() {
    if (!selectedLibId) { setErr("先选个数据库"); return; }
    setErr(null);
    try {
      if (!libs.find(l => l.lib_id === selectedLibId)?.active) {
        await api.activateLibrary(selectedLibId);
      }
      setStage("running-insight");
      setProgress("📊 Claude × OpenAI 双 AI 协作分析中…（约 60-180s）");
      const r = await api.runInsight(selectedLibId);
      setProgress("✓ 报告完成，跳转中…");
      setStage("done");
      setTimeout(() => navigate(`/reports/${r.report_id}`), 500);
    } catch (e: any) {
      setErr(e.message);
      setStage("idle");
      setProgress("");
    }
  }

  const offline = !api.isConnected();
  const hasLib = libs.length > 0;
  const selected = libs.find(l => l.lib_id === selectedLibId);
  const reportsForSelected = reports.filter(r => r.library_id === selectedLibId);
  const busy = stage !== "idle" && stage !== "done";

  return (
    <div>
      <div className="page-header">
        <h1>📊 数据库分析报告 · 第 1 步</h1>
        <p>
          {hasLib
            ? "Claude × OpenAI 双 AI 独立分析 → 互相评审查漏补缺 → 主编融合 → 共识报告 + 数据图表"
            : "拖一个 .db 进来 → AI 团队自动分析 → 出一份共识报告。其它步骤都基于这份洞察。"}
        </p>
      </div>

      {err && <div className="banner danger" onClick={() => setErr(null)}>{err}</div>}
      {info && <div className="banner info" onClick={() => setInfo(null)}>{info}</div>}
      {offline && (
        <div className="banner warn">
          本地后端没起来。顶部黄条有启动命令；起来后回这里。
        </div>
      )}

      {/* ====== Combined hero dropzone (always visible, primary entry) ====== */}
      <div className="card" style={{
        background: hasLib ? "var(--bg-card)" : "linear-gradient(180deg, #fff7e6 0%, #fff 100%)",
        borderColor: hasLib ? undefined : "#fde2a3",
      }}>
        <h2 style={{marginTop: 0}}>{hasLib ? "🆕 上传新库 + 分析" : "🚀 第一次使用？拖一个 .db 上来"}</h2>

        <div
          className={`hero-drop ${dragOver ? "drag-over" : ""} ${busy ? "has-file" : ""}`}
          style={{ pointerEvents: busy || offline ? "none" : "auto",
                   opacity: busy ? 0.9 : (offline ? 0.5 : 1),
                   padding: hasLib ? "36px 24px" : "48px 24px" }}
          onDragOver={e => { e.preventDefault(); if (!busy) setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={e => { e.preventDefault(); setDragOver(false); handleFile(e.dataTransfer.files?.[0] ?? null); }}
          onClick={() => !busy && fileRef.current?.click()}
        >
          <input ref={fileRef} type="file" accept=".db,.sqlite,.sqlite3"
            style={{display: "none"}}
            onChange={e => handleFile(e.target.files?.[0] ?? null)} />
          {busy ? (
            <>
              <div className="big-icon">{stage === "running-insight" ? "🤖🤖" : "⏳"}</div>
              <h2 style={{margin: 0}}>{progress || "处理中…"}</h2>
              <div className="muted" style={{marginTop: 6}}>不要关闭页面</div>
            </>
          ) : (
            <>
              <div className="big-icon">📂</div>
              <h2 style={{margin: 0}}>把数据库拖到这里 / 点击选择</h2>
              <div className="muted" style={{marginTop: 6}}>
                SQLite (.db / .sqlite / .sqlite3) · 自动嗅探平台 → 跑爆款 DNA → 直接出 AI 共识报告
              </div>
              <div className="muted" style={{marginTop: 4, fontSize: 11}}>
                整个流程 1-3 分钟
              </div>
            </>
          )}
        </div>

        {!busy && (
          <div className="row" style={{justifyContent: "space-between", alignItems: "center", marginTop: 12}}>
            <div>
              <label style={{marginBottom: 4}}>来源平台</label>
              <select value={pendingPlatform} onChange={e => setPendingPlatform(e.target.value)}
                disabled={offline} style={{minWidth: 220, fontSize: 13}}>
                <option value="auto">🪄 自动嗅探（推荐）</option>
                {platforms.map(p => <option key={p.id} value={p.id}>{p.label}</option>)}
              </select>
            </div>
            <div className="muted" style={{fontSize: 11, textAlign: "right", maxWidth: 360}}>
              没现成 .db？看 <a href={GITHUB_REPO + "#%E5%A4%9A%E5%B9%B3%E5%8F%B0--%E5%A4%9A-library"} target="_blank" rel="noreferrer">README 里的爬虫推荐</a>，
              或者去 <Link to="/libraries">📥 资源库</Link> 看各平台的 schema 要求。
            </div>
          </div>
        )}
      </div>

      {/* ====== Re-analyze existing libraries ====== */}
      {hasLib && !busy && (
        <div className="card">
          <h2>📚 已有库 · 重新生成报告</h2>
          <div className="row" style={{gap: 12, alignItems: "flex-end", marginBottom: 4}}>
            <div style={{flex: 1}}>
              <label>选库</label>
              <select value={selectedLibId} onChange={e => setSelectedLibId(e.target.value)}
                disabled={offline} style={{width: "100%"}}>
                {libs.map(l => (
                  <option key={l.lib_id} value={l.lib_id}>
                    {l.active ? "★ " : ""}
                    {l.display_name} · {platformLabel(l.platform)} · {l.notes_count.toLocaleString()} 条
                  </option>
                ))}
              </select>
              {selected && (
                <div className="muted" style={{fontSize: 11, marginTop: 4}}>
                  <PlatformPill platform={selected.platform} /> ·
                  {selected.notes_count.toLocaleString()} 笔记 · {selected.comments_count.toLocaleString()} 评论 ·
                  {fmtBytes(selected.size_bytes)}
                </div>
              )}
            </div>
            <button onClick={reAnalyze} disabled={offline || !selectedLibId}
              style={{minWidth: 160, fontSize: 14, padding: "10px 18px"}}>
              🚀 重跑报告
            </button>
          </div>
        </div>
      )}

      {/* ====== Pipeline explainer ====== */}
      <details className="card" style={{background: "#fafafa"}}>
        <summary style={{cursor: "pointer", fontWeight: 600, fontSize: 14}}>
          ▾ 这份报告里会有什么 · AI 团队怎么工作的
        </summary>
        <div style={{padding: "10px 4px 4px", fontSize: 13, lineHeight: 1.7, color: "#555"}}>
          <p style={{margin: "6px 0"}}>
            <b>多 agent 协作 + 互相查漏补缺：</b>
          </p>
          <ol style={{marginLeft: 20, marginTop: 4}}>
            <li><b>Phase 1（独立分析）：</b>Claude Opus 和 OpenAI GPT-4o 各自看 DNA 数据，独立写一份报告</li>
            <li><b>Phase 2（互评查漏）：</b>双方读对方的报告 → 写「我赞成的 / 我反对的 / 对方漏了的」</li>
            <li><b>Phase 3（主编融合）：</b>主编（Claude Opus）只把<b>双方都认可</b>的点放进正式报告，分歧/单方观点单独列出</li>
          </ol>
          <p style={{margin: "8px 0 4px"}}><b>报告内容：</b></p>
          <ul style={{marginLeft: 20}}>
            <li>📌 总览 + 关键发现（每条引用 DNA 真实数字）</li>
            <li>🚀 内容机会（蓝海赛道 + 切入方式）</li>
            <li>⚠️ 风险与盲区</li>
            <li>📈 推荐下一步</li>
            <li>🗨️ 双方分歧 / 单方观点（透明保留，避免一家独大）</li>
            <li>📊 数据图表（蓝海排行 / hook 分布 / 时段热力图 / tags / 字数）</li>
          </ul>
        </div>
      </details>

      {/* ====== Historical reports ====== */}
      {hasLib && reportsForSelected.length > 0 && (
        <div className="card">
          <h2>该库的历史报告 ({reportsForSelected.length})</h2>
          <table className="table">
            <thead>
              <tr><th>时间</th><th>状态</th><th>耗时</th><th></th></tr>
            </thead>
            <tbody>
              {reportsForSelected.map(r => (
                <tr key={r.report_id}>
                  <td>{fmtTime(r.created_at)}</td>
                  <td>
                    {r.status === "completed" ? (
                      <span style={{color: "var(--ok)"}}>✓ 完成</span>
                    ) : r.status === "failed" ? (
                      <span style={{color: "var(--danger)"}}>✗ 失败</span>
                    ) : (
                      <span className="muted">{r.status}</span>
                    )}
                  </td>
                  <td className="muted">{r.elapsed_s ? `${r.elapsed_s}s` : "—"}</td>
                  <td>
                    {r.status === "completed" && (
                      <Link to={`/reports/${r.report_id}`}>查看报告 →</Link>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {reports.length > reportsForSelected.length && (
        <div className="card" style={{background: "#fafafa"}}>
          <h3 style={{margin: 0}}>其他库的报告</h3>
          <table className="table" style={{marginTop: 8}}>
            <thead><tr><th>库</th><th>时间</th><th></th></tr></thead>
            <tbody>
              {reports
                .filter(r => r.library_id !== selectedLibId)
                .slice(0, 10)
                .map(r => {
                  const lib = libs.find(l => l.lib_id === r.library_id);
                  return (
                    <tr key={r.report_id}>
                      <td>{lib?.display_name ?? r.library_id}</td>
                      <td className="muted">{fmtRelative(r.created_at)}</td>
                      <td>{r.status === "completed" && <Link to={`/reports/${r.report_id}`}>查看 →</Link>}</td>
                    </tr>
                  );
                })}
            </tbody>
          </table>
        </div>
      )}

      {hasLib && (
        <div className="card" style={{background: "#fff7e6", borderColor: "#fde2a3"}}>
          <h3 style={{margin: "0 0 6px"}}>📖 下一步</h3>
          <p style={{margin: 0, fontSize: 13}}>
            看完报告 → 进 <Link to="/strategy">🚀 起号策略</Link> 让 AI 基于报告 + 你的想法拟一版完整起号方案。
            报告会一直留在这里，随时可以回来翻。
          </p>
        </div>
      )}
    </div>
  );
}

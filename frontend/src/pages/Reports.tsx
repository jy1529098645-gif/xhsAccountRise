import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { fmtRelative, fmtTime, platformLabel } from "../format";
import PlatformPill from "../components/PlatformPill";
import type { Library } from "../types";

interface ReportSummary {
  report_id: string;
  library_id: string;
  created_at: number;
  status: string;
  elapsed_s: number | null;
}

export default function Reports() {
  const [libs, setLibs] = useState<Library[]>([]);
  const [reports, setReports] = useState<ReportSummary[]>([]);
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState<string>("");
  const [err, setErr] = useState<string | null>(null);
  const [selectedLibId, setSelectedLibId] = useState<string>("");
  const navigate = useNavigate();

  async function load() {
    try {
      const ls = await api.libraries();
      setLibs(ls);
      const active = ls.find(l => l.active);
      setSelectedLibId(active?.lib_id ?? ls[0]?.lib_id ?? "");
      const rs = await api.listInsights();
      setReports(rs as any);
    } catch (e: any) {
      setErr(e.message);
    }
  }
  useEffect(() => { load(); }, []);

  async function generate() {
    if (!selectedLibId) {
      setErr("先选个数据库");
      return;
    }
    setRunning(true);
    setErr(null);
    setProgress("📊 Claude × OpenAI 双 AI 协作分析中…（约 60-180s）");
    try {
      // Make sure the chosen library is the active one before generating
      if (!libs.find(l => l.lib_id === selectedLibId)?.active) {
        await api.activateLibrary(selectedLibId);
      }
      const r = await api.runInsight(selectedLibId);
      setProgress("✓ 完成，跳转到报告…");
      setTimeout(() => navigate(`/reports/${r.report_id}`), 500);
    } catch (e: any) {
      setErr(e.message);
      setRunning(false);
      setProgress("");
    }
  }

  const offline = !api.isConnected();
  const hasLib = libs.length > 0;
  const selected = libs.find(l => l.lib_id === selectedLibId);
  const reportsForSelected = reports.filter(r => r.library_id === selectedLibId);

  return (
    <div>
      <div className="page-header">
        <h1>📊 数据库分析报告 · 第 1 步</h1>
        <p>Claude × OpenAI 双 AI 独立分析你的数据库 → 互相评审查漏补缺 → 主编融合 → 出一份共识报告 + 数据图表。看完它，再去做起号策略才有底气。</p>
      </div>

      {err && <div className="banner danger" onClick={() => setErr(null)}>{err}</div>}
      {offline && (
        <div className="banner warn">
          本地后端没起来。顶部黄条有启动命令；起来后回这里。
        </div>
      )}

      {!hasLib && !offline && (
        <div className="banner info">
          <b>还没有数据库</b> · 先去 <Link to="/libraries">📥 资源库</Link> 拖一个 .db 上来再回这里分析。
        </div>
      )}

      {hasLib && (
        <div className="card">
          <h2>🪄 让 AI 双方分析这个库</h2>

          <div className="row" style={{ gap: 12, alignItems: "flex-end", marginBottom: 12 }}>
            <div style={{ flex: 1 }}>
              <label>选数据库</label>
              <select value={selectedLibId} onChange={e => setSelectedLibId(e.target.value)}
                disabled={offline || running} style={{ width: "100%" }}>
                {libs.map(l => (
                  <option key={l.lib_id} value={l.lib_id}>
                    {l.active ? "★ " : ""}
                    {l.display_name} · {platformLabel(l.platform)} · {l.notes_count.toLocaleString()} 条
                  </option>
                ))}
              </select>
              {selected && (
                <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>
                  <PlatformPill platform={selected.platform} /> · {selected.notes_count.toLocaleString()} 笔记 · {selected.comments_count.toLocaleString()} 评论
                </div>
              )}
            </div>
            <button onClick={generate} disabled={offline || running || !selectedLibId}
              style={{ minWidth: 180, fontSize: 14, padding: "10px 18px" }}>
              {running ? "🤖🤖 分析中…" : "🚀 生成新报告"}
            </button>
          </div>

          {progress && (
            <div className="banner info" style={{ margin: "8px 0 0" }}>
              {progress}
            </div>
          )}

          <details style={{ marginTop: 8 }}>
            <summary style={{ cursor: "pointer", fontSize: 12.5, color: "var(--muted)" }}>
              ▾ 这份报告里会有什么 / 怎么工作的
            </summary>
            <div style={{ padding: "8px 12px", fontSize: 12.5, lineHeight: 1.7, color: "#555" }}>
              <p style={{ margin: "6px 0" }}>
                <b>工作流程（多 agent 协作 + 查漏补缺）：</b>
              </p>
              <ol style={{ marginLeft: 18, marginTop: 4 }}>
                <li><b>Phase 1</b>（独立分析）：Claude 和 OpenAI 各自看 DNA 数据，独立写一份报告</li>
                <li><b>Phase 2</b>（互评查漏）：双方读对方的报告 → 写「我赞成的 / 我反对的 / 对方漏了的」</li>
                <li><b>Phase 3</b>（主编融合）：主编（Claude Opus）只把**双方都认可**的点放进正式报告，分歧/单方观点单独列出</li>
              </ol>
              <p style={{ margin: "8px 0 4px" }}><b>报告内容：</b></p>
              <ul style={{ marginLeft: 18 }}>
                <li>📌 总览 + 关键发现（每条引用 DNA 真实数字）</li>
                <li>🚀 内容机会（蓝海赛道 + 切入方式）</li>
                <li>⚠️ 风险与盲区</li>
                <li>📈 推荐下一步</li>
                <li>🗨️ 双方分歧 / 单方观点（透明保留）</li>
                <li>📊 数据图表（蓝海排行 / hook 分布 / 时段热力图 / tags / 字数）</li>
              </ul>
            </div>
          </details>
        </div>
      )}

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
                      <span style={{ color: "var(--ok)" }}>✓ 完成</span>
                    ) : r.status === "failed" ? (
                      <span style={{ color: "var(--danger)" }}>✗ 失败</span>
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

      {hasLib && reports.length > reportsForSelected.length && (
        <div className="card" style={{ background: "#fafafa" }}>
          <h3 style={{ margin: 0 }}>其他库的报告</h3>
          <table className="table" style={{ marginTop: 8 }}>
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

      <div className="card" style={{ background: "#fff7e6", borderColor: "#fde2a3" }}>
        <h3 style={{ margin: "0 0 6px" }}>📖 下一步</h3>
        <p style={{ margin: 0, fontSize: 13 }}>
          看完报告 → 进 <Link to="/strategy">🚀 起号策略</Link> 让 AI 基于报告 + 你的想法拟一版完整起号方案。
        </p>
      </div>
    </div>
  );
}

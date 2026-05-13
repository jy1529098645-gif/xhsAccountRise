import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { fmtLikes, fmtTime } from "../format";
import PlatformPill from "../components/PlatformPill";
import type { DnaArtifact, DraftListItem, Library, Status } from "../types";

export default function Dashboard() {
  const [status, setStatus] = useState<Status | null>(null);
  const [dna, setDna] = useState<DnaArtifact | null>(null);
  const [drafts, setDrafts] = useState<DraftListItem[]>([]);
  const [libs, setLibs] = useState<Library[]>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const [s, d, ds, ls] = await Promise.allSettled([
          api.status(), api.dnaLatest(), api.drafts(), api.libraries(),
        ]);
        if (s.status === "fulfilled") setStatus(s.value);
        if (d.status === "fulfilled") setDna(d.value);
        if (ds.status === "fulfilled") setDrafts(ds.value);
        if (ls.status === "fulfilled") setLibs(ls.value);
      } catch (e: any) { setErr(e.message); }
    })();
  }, []);

  const active = libs.find(l => l.active);

  const noLibs = libs.length === 0;

  return (
    <div>
      <div className="page-header">
        <h1>Dashboard</h1>
        <p>AcademiCats 起号工作台 · 当前库：{active?.display_name ?? "—"} <PlatformPill platform={active?.platform} /></p>
      </div>

      {err && <div className="banner danger">{err}</div>}

      {noLibs && api.isConnected() && (
        <div className="banner info">
          <div>
            <b>👋 还没有数据库</b> · 去 <Link to="/libraries">📥 资源库</Link> 拖一个 .db 进来，全自动跑完检测+分析就能开始用了。
          </div>
        </div>
      )}

      <div className="cards-grid">
        <Stat label="Notes 总数" value={status?.counts.notes ?? active?.notes_count ?? 0} />
        <Stat label="评论总数" value={status?.counts.comments ?? active?.comments_count ?? 0} />
        <Stat label="DNA artifacts" value={status?.counts.dna_artifacts ?? 0} />
        <Stat label="已生成 drafts" value={status?.counts.drafts ?? drafts.length} />
        <Stat label="LLM 候选数" value={status?.counts.candidates ?? 0} />
        <Stat label="Agent traces" value={status?.counts.agent_traces ?? 0} />
      </div>

      <div className="card">
        <h2>LLM Providers</h2>
        <div className="row wrap">
          <Provider name="Anthropic Claude" ok={status?.providers.anthropic ?? false} />
          <Provider name="DeepSeek" ok={status?.providers.deepseek ?? false} />
          <Provider name="OpenAI GPT" ok={status?.providers.openai ?? false} />
        </div>
        <p className="muted" style={{marginTop: 10}}>
          没勾上的家在 <Link to="/settings">Settings</Link> 看说明（本地 .env 配置）。
        </p>
      </div>

      {dna && (
        <div className="card">
          <h2>最新 DNA · v{dna.version}</h2>
          <p className="muted">
            {dna.summary.total_notes_analysed} 条 notes · 生成耗时 {dna.summary.generated_in_seconds}s ·
            主导 hook：{dna.summary.dominant_hooks.map(h => h.category).join(" · ")}
          </p>
          <Link to="/analysis">查看完整 DNA →</Link>
        </div>
      )}

      <div className="card">
        <h2>最近 drafts</h2>
        {drafts.length === 0 ? (
          <p className="muted">还没有 draft。去 <Link to="/composer">Composer</Link> 跑一个？</p>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>时间</th><th>主题</th><th>mode</th><th>final 标题</th><th>候选数</th><th></th>
              </tr>
            </thead>
            <tbody>
              {drafts.slice(0, 8).map(d => (
                <tr key={d.draft_id}>
                  <td className="muted">{fmtTime(d.generated_at)}</td>
                  <td>{d.brief?.topic ?? "—"}</td>
                  <td><span className="tag-pill">{d.mode}</span></td>
                  <td>{d.final_title ?? <em className="muted">—</em>}</td>
                  <td className="num">{d.candidate_count}</td>
                  <td><Link to={`/drafts/${d.draft_id}`}>查看</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="card">
        <h2>当前 Library</h2>
        {!active ? (
          <p className="muted">没有 active library。去 <Link to="/libraries">Libraries</Link> 上传或激活。</p>
        ) : (
          <div className="spread">
            <div>
              <strong>{active.display_name}</strong> <PlatformPill platform={active.platform} />
              <p className="muted">
                {active.notes_count.toLocaleString()} notes · {active.comments_count.toLocaleString()} 评论 ·
                lib_id: <code className="kbd">{active.lib_id}</code>
              </p>
            </div>
            <Link to="/libraries"><button className="secondary">管理库</button></Link>
          </div>
        )}
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="stat-card">
      <div className="label">{label}</div>
      <div className="value">{value.toLocaleString()}</div>
    </div>
  );
}

function Provider({ name, ok }: { name: string; ok: boolean }) {
  return (
    <div style={{
      padding: "6px 12px", borderRadius: 6,
      background: ok ? "var(--ok-soft)" : "#f5f5f5",
      color: ok ? "var(--ok)" : "var(--muted)",
      fontSize: 13,
    }}>
      {ok ? "✓" : "○"} {name}
    </div>
  );
}

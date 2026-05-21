import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { fmtTime } from "../format";
import NextStepCard from "../components/NextStepCard";
import { LaunchModeCard } from "./InsightReport";

interface IntegratedDTO {
  integrated_id: string;
  library_id: string | null;
  created_at: number;
  status: string;
  source_ids: string[];
  elapsed_s: number;
  consensus: any;
  included_single_side_view_indices?: number[];
}

// v0.61.27 ：勾选「采纳的单方观点」本地化 — 不再 PATCH 后端，避免多用户互覆盖。
// 键 ：studio.integrated.included.<pid>.<integrated_id>。每个用户独立。
function includedLocalKey(integratedId: string): string {
  let pid = "default";
  try { pid = localStorage.getItem("studio.activeProjectId") || "default"; } catch { /* ignore */ }
  return `studio.integrated.included.${pid}.${integratedId}`;
}
function readIncludedLocal(integratedId: string): number[] | null {
  try {
    const raw = localStorage.getItem(includedLocalKey(integratedId));
    if (!raw) return null;
    const arr = JSON.parse(raw);
    return Array.isArray(arr) ? arr.filter((n: any) => typeof n === "number") : null;
  } catch { return null; }
}
function writeIncludedLocal(integratedId: string, indices: number[]): void {
  try {
    localStorage.setItem(includedLocalKey(integratedId), JSON.stringify(indices));
  } catch { /* quota */ }
}

export default function IntegratedReport() {
  const { id } = useParams();
  const [data, setData] = useState<IntegratedDTO | null>(null);
  const [sourceNames, setSourceNames] = useState<Record<string, string>>({});
  const [err, setErr] = useState<string | null>(null);
  // v0.61.11 → v0.61.27 ：本地化。优先 localStorage；没有就回退 backend 已存的。
  const [includedIdx, setIncludedIdx] = useState<Set<number>>(new Set());

  useEffect(() => {
    if (!id) return;
    api.getIntegratedReport(id).then(d => {
      setData(d);
      const local = readIncludedLocal(id);
      const initial = local ?? d.included_single_side_view_indices ?? [];
      setIncludedIdx(new Set(initial));
    }).catch(e => setErr(e.message));
    api.listExternalReports().then(rows => {
      const m: Record<string, string> = {};
      for (const r of rows as any[]) m[r.report_id] = r.name;
      setSourceNames(m);
    }).catch(e => console.error("[IntegratedReport] sourceNames", e));
  }, [id]);

  function toggleInclude(idx: number) {
    if (!id) return;
    const next = new Set(includedIdx);
    if (next.has(idx)) next.delete(idx); else next.add(idx);
    setIncludedIdx(next);
    writeIncludedLocal(id, Array.from(next));
    // 不再 PATCH 后端 — 每个用户的「这一条要不要采纳」是私人偏好。
  }
  // 保持 saving 哑变量避免别处 type error（部分 UI 引用 saving）
  const saving = false;

  if (err) return <div className="banner danger">{err}</div>;
  if (!data) return <div className="card muted">加载中…</div>;
  if (data.status !== "completed") {
    return <div className="banner warn">整合稿状态 ：{data.status}（未完成）</div>;
  }

  const c = data.consensus || {};
  return (
    <div>
      <div className="page-header">
        <h1>🪄 {c.title || "整合分析报告"}</h1>
        <p>
          由 GPT-4o 整合你上传的 {data.source_ids.length} 份外部报告
          {c.executive_summary ? " + 本工具的双 AI 共识" : ""} ·
          完成于 {fmtTime(data.created_at)} · 耗时 {data.elapsed_s}s
        </p>
      </div>

      <Link to="/reports">← 回分析报告页</Link>

      <div className="card" style={{background: "#fafafa"}}>
        <b>整合的原始报告 ：</b>
        <ul style={{margin: "6px 0 0 20px", fontSize: 13}}>
          {data.source_ids.map(sid => (
            <li key={sid}>{sourceNames[sid] || sid}</li>
          ))}
        </ul>
      </div>

      {c.executive_summary && (
        <div className="card" style={{borderLeft: "4px solid var(--primary)"}}>
          <h2>💡 整合后的总览</h2>
          <p style={{fontSize: 14, lineHeight: 1.7}}>{c.executive_summary}</p>
        </div>
      )}

      {c.launch_mode?.recommendation && <LaunchModeCard mode={c.launch_mode} />}

      {c.consensus_findings?.length > 0 && (
        <div className="card">
          <h2>🎯 关键发现（整合后）</h2>
          {c.consensus_findings.map((f: any, i: number) => (
            <div key={i} style={{padding: "12px 14px", background: "var(--ok-soft)",
                                  borderRadius: 6, marginBottom: 8}}>
              <div style={{fontWeight: 600, fontSize: 14}}>✓ {f.title}</div>
              {f.evidence && (
                <div style={{fontSize: 12.5, color: "#555", marginTop: 4}}>
                  <b>证据：</b>{f.evidence}
                </div>
              )}
              {f.implication && (
                <div style={{fontSize: 12.5, marginTop: 4}}>
                  <b>意味着：</b>{f.implication}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {c.consensus_opportunities?.length > 0 && (
        <div className="card">
          <h2>🚀 内容机会</h2>
          {c.consensus_opportunities.map((o: any, i: number) => (
            <div key={i} style={{padding: "10px 12px", marginBottom: 8,
                                  background: "var(--primary-soft)", borderRadius: 6}}>
              <div style={{fontWeight: 600}}>{o.opportunity}</div>
              {o.why && (
                <div style={{fontSize: 12, marginTop: 4}}>
                  <span className="muted"><b>因为：</b>{o.why}</span>
                </div>
              )}
              {o.suggested_angle && (
                <div style={{fontSize: 12, marginTop: 4}}>
                  <b style={{color: "var(--primary)"}}>切入方式：</b>{o.suggested_angle}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {c.consensus_risks?.length > 0 && (
        <div className="card">
          <h2>⚠️ 风险与盲区</h2>
          <ul style={{marginLeft: 20, lineHeight: 1.8, fontSize: 13}}>
            {c.consensus_risks.map((r: string, i: number) => <li key={i}>{r}</li>)}
          </ul>
        </div>
      )}

      {c.consensus_next_steps?.length > 0 && (
        <div className="card">
          <h2>📌 推荐下一步</h2>
          <ol style={{marginLeft: 20, lineHeight: 1.9, fontSize: 13.5}}>
            {c.consensus_next_steps.map((n: string, i: number) => <li key={i}>{n}</li>)}
          </ol>
        </div>
      )}

      {c.source_breakdown?.length > 0 && (
        <div className="card">
          <h2>📑 各来源贡献了什么</h2>
          {c.source_breakdown.map((s: any, i: number) => (
            <div key={i} style={{padding: "8px 12px", marginBottom: 6,
                                  background: "#fafafa", borderRadius: 6}}>
              <b>《{s.name}》</b>
              {Array.isArray(s.contributed) && s.contributed.length > 0 && (
                <ul style={{marginLeft: 18, marginTop: 4, fontSize: 12.5}}>
                  {s.contributed.map((p: string, j: number) => <li key={j}>{p}</li>)}
                </ul>
              )}
            </div>
          ))}
        </div>
      )}

      {c.single_side_views?.length > 0 && (
        <details className="card" open>
          <summary style={{cursor: "pointer", fontWeight: 600}}>
            ▾ 没合并 / 单方观点（{c.single_side_views.length}） ·
            <span className="muted" style={{fontWeight: 400, marginLeft: 6}}>
              勾选的会作为「用户已认可的额外观点」注入下游 Strategy / Composer prompt
            </span>
            {saving && <span className="muted" style={{marginLeft: 8, fontSize: 11}}>保存中…</span>}
          </summary>
          <div style={{marginTop: 8}}>
            <div className="muted" style={{fontSize: 11.5, marginBottom: 8}}>
              已勾选 {includedIdx.size} / {c.single_side_views.length}
              {includedIdx.size > 0 && " ✓ 会被下游强参考"}
            </div>
            {c.single_side_views.map((v: any, i: number) => {
              const checked = includedIdx.has(i);
              return (
                <label key={i} style={{
                  display: "flex", gap: 10, alignItems: "flex-start",
                  padding: "10px 12px", marginBottom: 6,
                  background: checked ? "#fdf4ff" : "#fafafa",
                  borderRadius: 6,
                  borderLeft: `3px solid ${checked ? "var(--primary)" : "#a855f7"}`,
                  cursor: "pointer",
                  transition: "background 0.15s",
                }}>
                  <input type="checkbox" checked={checked}
                    onChange={() => toggleInclude(i)}
                    style={{marginTop: 3, flexShrink: 0}} />
                  <div style={{flex: 1, minWidth: 0}}>
                    <div className="muted" style={{fontSize: 11.5, marginBottom: 4}}>
                      来源 ：{v.side}{checked && " · ✓ 已采纳"}
                    </div>
                    <div style={{fontSize: 13}}>{v.point}</div>
                    {v.note && (
                      <div style={{fontSize: 11.5, marginTop: 4, color: "var(--muted)", fontStyle: "italic"}}>
                        {v.note}
                      </div>
                    )}
                  </div>
                </label>
              );
            })}
          </div>
        </details>
      )}

      <NextStepCard
        label="去 🚀 起号策略"
        hint="Strategy / Composer 都会自动引用这份整合稿（取最新一份），用户上传的报告会直接影响 AI 的拟稿。"
        to="/strategy"
      />
    </div>
  );
}

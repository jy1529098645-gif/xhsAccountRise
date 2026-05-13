import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { fmtTime } from "../format";
import type { InsightReportDTO, DnaArtifact } from "../types";

const CHART_LABELS: Record<string, string> = {
  blue_ocean_top15: "🌊 蓝海关键词 Top 15",
  hook_distribution: "🎣 标题 hook 分布",
  timing_heatmap: "📅 发布时段热力图",
  top_tags: "🏷️ 高表现 tags",
  body_length: "📏 字数 vs 互动",
  top_titles: "🏆 Top 标题示例",
  comment_demand: "💬 用户高频询问",
};

export default function InsightReport() {
  const { id } = useParams();
  const [data, setData] = useState<InsightReportDTO | null>(null);
  const [dna, setDna] = useState<DnaArtifact | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    api.getInsight(id).then(setData).catch(e => setErr(e.message));
    api.dnaLatest().then(setDna).catch(() => {});
  }, [id]);

  if (err) return <div className="banner danger">{err}</div>;
  if (!data) return <div className="card muted">加载中…</div>;

  if (data.status === "pending") {
    return <div className="card muted" style={{textAlign: "center", padding: 48}}>
      <div style={{fontSize: 36}}>⏳</div>
      <h2>分析进行中…</h2>
    </div>;
  }
  if (data.status === "failed") {
    return <div className="banner danger">分析失败：{data.error}</div>;
  }

  const c = data.consensus || {};
  const claudeAna = data.claude_analysis || {};
  const openaiAna = data.openai_analysis || {};
  const debate = data.debate || {};

  return (
    <div>
      <div className="page-header">
        <h1>📊 {c.title || "资源库分析报告"}</h1>
        <p>
          双 AI 协作（Claude + OpenAI）独立分析 → 互相评审 → 主编融合共识 ·
          完成于 {fmtTime(data.created_at)} · 耗时 {data.elapsed_s}s
        </p>
      </div>

      <Link to="/libraries">← 回资源库</Link>

      {/* Executive summary */}
      {c.executive_summary && (
        <div className="card" style={{borderLeft: "4px solid var(--primary)"}}>
          <h2>💡 总览</h2>
          <p style={{fontSize: 14, lineHeight: 1.7}}>{c.executive_summary}</p>
        </div>
      )}

      {/* Consensus findings */}
      {c.consensus_findings?.length > 0 && (
        <div className="card">
          <h2>🎯 共识关键发现 (双方都认同)</h2>
          {c.consensus_findings.map((f: any, i: number) => (
            <div key={i} style={{padding: "12px 14px", background: "var(--ok-soft)",
                                  borderRadius: 6, marginBottom: 8}}>
              <div style={{fontWeight: 600, fontSize: 14}}>✓ {f.title}</div>
              <div style={{fontSize: 12.5, color: "#555", marginTop: 4}}>
                <b>证据：</b>{f.evidence}
              </div>
              <div style={{fontSize: 12.5, marginTop: 4}}>
                <b>意味着：</b>{f.implication}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Consensus opportunities */}
      {c.consensus_opportunities?.length > 0 && (
        <div className="card">
          <h2>🚀 内容机会</h2>
          {c.consensus_opportunities.map((o: any, i: number) => (
            <div key={i} style={{padding: "10px 12px", marginBottom: 8,
                                  background: "var(--primary-soft)", borderRadius: 6}}>
              <div style={{fontWeight: 600}}>{o.opportunity}</div>
              <div style={{fontSize: 12, marginTop: 4}}>
                <span className="muted"><b>因为：</b>{o.why}</span>
              </div>
              <div style={{fontSize: 12, marginTop: 4}}>
                <b style={{color: "var(--primary)"}}>切入方式：</b>{o.suggested_angle}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Embedded charts */}
      {c.charts_to_show?.length > 0 && dna && (
        <div className="card">
          <h2>📈 数据图表（点开看）</h2>
          {c.charts_to_show.map((key: string) => (
            <ChartBlock key={key} chartKey={key} dna={dna} />
          ))}
        </div>
      )}

      {/* Risks */}
      {c.consensus_risks?.length > 0 && (
        <div className="card">
          <h2>⚠️ 风险与盲区</h2>
          <ul style={{marginLeft: 20, lineHeight: 1.8, fontSize: 13}}>
            {c.consensus_risks.map((r: string, i: number) => <li key={i}>{r}</li>)}
          </ul>
        </div>
      )}

      {/* Next steps */}
      {c.consensus_next_steps?.length > 0 && (
        <div className="card">
          <h2>📌 推荐下一步</h2>
          <ol style={{marginLeft: 20, lineHeight: 1.9, fontSize: 13.5}}>
            {c.consensus_next_steps.map((n: string, i: number) => <li key={i}>{n}</li>)}
          </ol>
        </div>
      )}

      {/* Single-side views */}
      {c.single_side_views?.length > 0 && (
        <div className="card">
          <h2>🗨️ 分歧 / 单方观点</h2>
          <p className="muted" style={{fontSize: 12}}>下面这些点只有一家 AI 提出，另一家不认同或没提到——供参考。</p>
          {c.single_side_views.map((v: any, i: number) => (
            <div key={i} style={{padding: "8px 12px", marginBottom: 6,
                                  background: "#fafafa", borderRadius: 6,
                                  borderLeft: `3px solid ${v.side === "claude" ? "#a36df0" : "#10a37f"}`}}>
              <div style={{fontSize: 11.5, color: "var(--muted)", marginBottom: 4}}>
                {v.side === "claude" ? "🟣 Claude 独立观点" : "🟢 OpenAI 独立观点"}
              </div>
              <div style={{fontSize: 13}}>{v.point}</div>
              {v.note && <div style={{fontSize: 11.5, marginTop: 4, color: "var(--muted)", fontStyle: "italic"}}>
                {v.note}
              </div>}
            </div>
          ))}
        </div>
      )}

      {/* Raw analyses (collapsed) */}
      <details className="card">
        <summary style={{cursor: "pointer", fontWeight: 600}}>
          ▾ 看原始独立分析 + 双方互评（debug 用）
        </summary>
        <h3 style={{color: "#a36df0"}}>🟣 Claude 独立报告</h3>
        <pre style={{background: "#fafafa", padding: 10, fontSize: 11, overflow: "auto", maxHeight: 300}}>
          {JSON.stringify(claudeAna, null, 2)}
        </pre>
        <h3 style={{color: "#10a37f"}}>🟢 OpenAI 独立报告</h3>
        <pre style={{background: "#fafafa", padding: 10, fontSize: 11, overflow: "auto", maxHeight: 300}}>
          {JSON.stringify(openaiAna, null, 2)}
        </pre>
        <h3>互相评审</h3>
        <pre style={{background: "#fafafa", padding: 10, fontSize: 11, overflow: "auto", maxHeight: 400}}>
          {JSON.stringify(debate, null, 2)}
        </pre>
      </details>
    </div>
  );
}

function ChartBlock({chartKey, dna}: {chartKey: string; dna: DnaArtifact}) {
  const label = CHART_LABELS[chartKey] || chartKey;
  const s = (dna.sections as any) || {};
  return (
    <details style={{marginBottom: 8}}>
      <summary style={{cursor: "pointer", padding: "8px 10px", background: "#fafafa",
                       borderRadius: 6, fontSize: 13.5, fontWeight: 600}}>
        {label}
      </summary>
      <div style={{padding: "10px 4px"}}>
        {chartKey === "blue_ocean_top15" && <BlueOceanChart data={s.keyword_blueocean?.rankings ?? []} />}
        {chartKey === "hook_distribution" && <HookDistChart data={s.titles?.primary_distribution ?? {}} />}
        {chartKey === "timing_heatmap" && <TimingHeatmap data={s.timing?.heatmap ?? []} />}
        {chartKey === "top_tags" && <TagsList data={s.tags?.top_tags ?? []} />}
        {chartKey === "body_length" && <BodyLengthChart data={s.body_and_shape?.by_body_length ?? {}} />}
        {chartKey === "top_titles" && <TopTitlesList data={s.titles?.top_titles ?? []} />}
        {chartKey === "comment_demand" && <CommentDemand data={s.comment_demand?.by_pattern ?? {}} />}
      </div>
    </details>
  );
}

function fmtLikes(n: any) {
  if (!n) return "0"; n = Number(n);
  if (n >= 100000) return `${(n / 10000).toFixed(1)}w`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

function BlueOceanChart({data}: {data: any[]}) {
  const top = data.slice(0, 15);
  const max = top[0]?.blue_ocean_score ?? 1;
  return (
    <table className="table"><thead><tr><th>#</th><th>关键词</th><th className="num">n</th><th className="num">avg</th><th>分布</th></tr></thead>
      <tbody>{top.map((r, i) => (
        <tr key={r.keyword}>
          <td className="num">{i + 1}</td><td>{r.keyword}</td>
          <td className="num">{r.note_count}</td>
          <td className="num">{fmtLikes(Math.round(r.avg_likes))}</td>
          <td><span className="bar" style={{width: 200}}><span className="bar-fill" style={{width: `${Math.min(100, r.blue_ocean_score/max*100)}%`, display: "block"}}/></span></td>
        </tr>
      ))}</tbody></table>
  );
}

function HookDistChart({data}: {data: Record<string, number>}) {
  const arr = Object.entries(data).sort((a, b) => b[1] - a[1]);
  const total = arr.reduce((s, [, v]) => s + v, 0) || 1;
  return (
    <table className="table"><thead><tr><th>hook</th><th className="num">n</th><th className="num">占比</th><th></th></tr></thead>
      <tbody>{arr.map(([k, v]) => (
        <tr key={k}><td>{k}</td><td className="num">{v}</td><td className="num">{(v / total * 100).toFixed(1)}%</td>
          <td><span className="bar" style={{width: 180}}><span className="bar-fill" style={{width: `${v / arr[0][1] * 100}%`, display: "block"}}/></span></td>
        </tr>
      ))}</tbody></table>
  );
}

function TimingHeatmap({data}: {data: any[]}) {
  if (!data.length) return <p className="muted">无</p>;
  const max = Math.max(...data.map(c => c.median_likes ?? 0)) || 1;
  const cell = new Map<string, any>();
  data.forEach(c => cell.set(`${c.dow}_${c.hour}`, c));
  const dow = ["一", "二", "三", "四", "五", "六", "日"];
  return (
    <table className="heatmap">
      <thead><tr><th></th>{Array.from({length: 24}, (_, h) => <th key={h}>{h}</th>)}</tr></thead>
      <tbody>{dow.map((label, d) => (
        <tr key={d}><th>周{label}</th>
          {Array.from({length: 24}, (_, h) => {
            const c = cell.get(`${d}_${h}`) ?? {count: 0, median_likes: 0};
            const r = Math.min(1, c.median_likes / max);
            const rd = Math.round(245 - 145 * r), g = Math.round(245 - 200 * r), b = Math.round(245 - 200 * r);
            return <td key={h} style={{background: `rgb(${rd},${g},${b})`}} title={`周${label} ${h}:00 · n=${c.count} · ${fmtLikes(Math.round(c.median_likes))}`}>{c.count || ""}</td>;
          })}
        </tr>
      ))}</tbody>
    </table>
  );
}

function TagsList({data}: {data: any[]}) {
  return (
    <table className="table"><thead><tr><th>tag</th><th className="num">count</th><th className="num">avg likes</th></tr></thead>
      <tbody>{data.slice(0, 25).map(t => (
        <tr key={t.tag}><td>{t.tag}</td><td className="num">{t.count}</td><td className="num">{fmtLikes(Math.round(t.avg_likes))}</td></tr>
      ))}</tbody></table>
  );
}

function BodyLengthChart({data}: {data: any}) {
  const order = ["<100", "100-300", "300-600", "600-1000", "1000-2000", "2000+"];
  return (
    <table className="table"><thead><tr><th>字数</th><th className="num">n</th><th className="num">median</th><th className="num">p90</th></tr></thead>
      <tbody>{order.map(k => {
        const d = data[k] ?? {};
        return <tr key={k}><td>{k}</td><td className="num">{d.count ?? 0}</td>
          <td className="num">{fmtLikes(Math.round(d.likes?.median ?? 0))}</td>
          <td className="num">{fmtLikes(Math.round(d.likes?.p90 ?? 0))}</td></tr>;
      })}</tbody></table>
  );
}

function TopTitlesList({data}: {data: any[]}) {
  return (
    <table className="table"><thead><tr><th>#</th><th>标题</th><th className="num">likes</th></tr></thead>
      <tbody>{data.slice(0, 15).map((t, i) => (
        <tr key={t.note_id}><td className="num">{i + 1}</td><td>{t.title}</td><td className="num">{fmtLikes(t.liked)}</td></tr>
      ))}</tbody></table>
  );
}

function CommentDemand({data}: {data: Record<string, any[]>}) {
  return <>{Object.entries(data).map(([label, items]) => (
    <div key={label} style={{marginBottom: 10}}>
      <div style={{fontWeight: 600, fontSize: 12.5}}>「{label}」开头</div>
      <div style={{fontSize: 12, color: "#555", marginTop: 2}}>
        {items.slice(0, 5).map(it => `${it.phrase}(${it.count})`).join("、")}
      </div>
    </div>
  ))}</>;
}

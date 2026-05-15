import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { fmtTime } from "../format";
import NextStepCard from "../components/NextStepCard";
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

  const claudeFindings = (claudeAna.key_findings || []).length;
  const openaiFindings = (openaiAna.key_findings || []).length;
  const consensusFindings = (c.consensus_findings || []).length;

  return (
    <div>
      <div className="page-header">
        <h1>📊 {c.title || "资源库分析报告"}</h1>
        <p>
          双 AI 协作（Claude + OpenAI）独立分析 → 互相评审 → 主编融合共识 ·
          完成于 {fmtTime(data.created_at)} · 耗时 {data.elapsed_s}s
        </p>
      </div>

      <Link to="/reports">← 回分析报告页</Link>

      {/* AI collaboration timeline — make the 2-AI process visually obvious */}
      <div className="card" style={{background: "linear-gradient(90deg, #f5f0ff 0%, #fff 50%, #ecfdf5 100%)"}}>
        <h2>🤝 AI 协作过程</h2>
        <div className="cards-grid" style={{gridTemplateColumns: "1fr 1fr 1fr", gap: 12}}>
          <div className="stat-card" style={{background: "#f5f0ff", borderColor: "#d9c5f5"}}>
            <div className="label" style={{color: "#7c3aed"}}>🟣 Claude 独立分析</div>
            <div className="value" style={{color: "#7c3aed"}}>{claudeFindings}</div>
            <div className="sub">条关键发现</div>
          </div>
          <div className="stat-card" style={{background: "#ecfdf5", borderColor: "#a7f3d0"}}>
            <div className="label" style={{color: "#10a37f"}}>🟢 OpenAI 独立分析</div>
            <div className="value" style={{color: "#10a37f"}}>{openaiFindings}</div>
            <div className="sub">条关键发现</div>
          </div>
          <div className="stat-card" style={{background: "var(--primary-soft)", borderColor: "#fab8c4"}}>
            <div className="label" style={{color: "var(--primary)"}}>⭐ 双方共识（已融合）</div>
            <div className="value" style={{color: "var(--primary)"}}>{consensusFindings}</div>
            <div className="sub">条双方都认可</div>
          </div>
        </div>
        <p className="muted" style={{fontSize: 11.5, marginTop: 8, marginBottom: 0}}>
          流程：双方各写一份 → 互相评审「赞成 / 反对 / 漏了的」 → 主编只把双方都认可的进正文，分歧单列。
          这样保证你看到的不是单一 AI 的偏见。
        </p>
      </div>

      {/* Executive summary */}
      {c.executive_summary && (
        <div className="card" style={{borderLeft: "4px solid var(--primary)"}}>
          <h2>💡 总览</h2>
          <p style={{fontSize: 14, lineHeight: 1.7}}>{c.executive_summary}</p>
        </div>
      )}

      {/* Launch-mode verdict — the #1 thing the user needs to know */}
      {c.launch_mode && c.launch_mode.recommendation && (
        <LaunchModeCard mode={c.launch_mode} />
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

      {/* Consensus opportunities — clickable to pre-fill Strategy brief */}
      {c.consensus_opportunities?.length > 0 && (
        <div className="card">
          <h2>🚀 内容机会 · 点击直接出策略</h2>
          <p className="muted" style={{fontSize: 12, marginTop: 0, marginBottom: 10}}>
            点任一条 → 跳到「起号策略」并把这条作为初步定位预填，AI 会基于此再推 8-12 个具体方向。
          </p>
          {c.consensus_opportunities.map((o: any, i: number) => (
            <OpportunityCard key={i} opp={o} />
          ))}
        </div>
      )}

      {/* Embedded charts — open by default for the first 2 */}
      {c.charts_to_show?.length > 0 && dna && (
        <div className="card">
          <h2>📈 数据图表（{c.charts_to_show.length} 个）</h2>
          {c.charts_to_show.map((key: string, i: number) => (
            <ChartBlock key={key} chartKey={key} dna={dna} defaultOpen={i < 2} />
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

      {/* Each AI's full independent report — first-class section, not debug */}
      <div className="card">
        <h2>🟣 Claude 独立报告</h2>
        <p className="muted" style={{fontSize: 12, marginBottom: 12}}>
          没看过对方的版本，独立分析的结果。下面的「共识」就是从这版 + OpenAI 版交叉评审而来。
        </p>
        <AIReportBlock report={claudeAna} accentColor="#a36df0" />
      </div>

      <div className="card">
        <h2>🟢 OpenAI 独立报告</h2>
        <p className="muted" style={{fontSize: 12, marginBottom: 12}}>
          GPT-5 / GPT-4o 独立看到同一份数据，给出自己的判断。
        </p>
        <AIReportBlock report={openaiAna} accentColor="#10a37f" />
      </div>

      {/* Mutual critique — collapsed by default but accessible */}
      <details className="card">
        <summary style={{cursor: "pointer", fontWeight: 600}}>
          ▾ 看两家 AI 互相评审的细节
        </summary>
        <p className="muted" style={{fontSize: 12, marginTop: 6}}>
          双方读了对方的报告之后写的「我赞成 / 我反对 / 对方漏了什么」。最终主编只把双方都认可的进了共识。
        </p>
        <pre style={{background: "#fafafa", padding: 10, fontSize: 11, overflow: "auto", maxHeight: 500}}>
          {JSON.stringify(debate, null, 2)}
        </pre>
      </details>

      <NextStepCard
        label="去 🚀 起号策略"
        hint="AI 会基于这份共识报告自动拟方向 + 周历 + 材料清单。报告会一直保留，随时可回来翻。"
        to="/strategy"
      />
    </div>
  );
}

function AIReportBlock({report, accentColor}: {report: any; accentColor: string}) {
  if (!report || typeof report !== "object") {
    return <p className="muted">（未返回内容）</p>;
  }
  if (report._error) {
    return (
      <div className="banner danger" style={{margin: 0}}>
        <b>这家 AI 这一轮没出报告。</b>
        <div style={{fontSize: 12, marginTop: 6, fontFamily: "monospace", whiteSpace: "pre-wrap"}}>
          {String(report._error).slice(0, 600)}
        </div>
        <div style={{fontSize: 12, marginTop: 8}}>
          → 回 <Link to="/reports">分析报告页</Link> 重新跑一次试试；如果还是失败，看顶部黄条确认本地后端 / API key 是否正常。
        </div>
      </div>
    );
  }
  return (
    <div>
      {report.launch_mode?.recommendation && (
        <MiniLaunchModeBadge mode={report.launch_mode} accentColor={accentColor} />
      )}
      {report.executive_summary && (
        <div style={{borderLeft: `3px solid ${accentColor}`, padding: "8px 12px",
                     background: "#fafafa", borderRadius: 4, marginBottom: 12}}>
          <div className="muted" style={{fontSize: 11, fontWeight: 600, marginBottom: 4}}>总览</div>
          <div style={{fontSize: 13.5, lineHeight: 1.7}}>{report.executive_summary}</div>
        </div>
      )}

      {(report.key_findings ?? []).length > 0 && (
        <div style={{marginBottom: 12}}>
          <h3 style={{margin: "8px 0 6px"}}>关键发现</h3>
          {report.key_findings.map((f: any, i: number) => (
            <div key={i} style={{padding: "8px 12px", marginBottom: 6,
                                 background: "#fafafa", borderRadius: 4}}>
              <div style={{fontWeight: 600, fontSize: 13.5}}>· {f.title}</div>
              {f.evidence && (
                <div style={{fontSize: 12, marginTop: 3, color: "#555"}}>
                  <b>证据：</b>{f.evidence}
                </div>
              )}
              {f.implication && (
                <div style={{fontSize: 12, marginTop: 3}}>
                  <b>意义：</b>{f.implication}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {(report.content_opportunities ?? []).length > 0 && (
        <div style={{marginBottom: 12}}>
          <h3 style={{margin: "8px 0 6px"}}>内容机会</h3>
          {report.content_opportunities.map((o: any, i: number) => (
            <div key={i} style={{padding: "8px 12px", marginBottom: 6,
                                 background: "#fafafa", borderRadius: 4}}>
              <div style={{fontWeight: 600, fontSize: 13.5}}>· {o.opportunity}</div>
              {o.why && <div style={{fontSize: 12, color: "var(--muted)", marginTop: 3}}>{o.why}</div>}
              {o.suggested_angle && (
                <div style={{fontSize: 12, marginTop: 3, color: accentColor}}>
                  切入：{o.suggested_angle}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {report.audience_insight && (
        <div style={{marginBottom: 12}}>
          <h3 style={{margin: "8px 0 6px"}}>受众洞察</h3>
          <p style={{fontSize: 13, lineHeight: 1.7}}>{report.audience_insight}</p>
        </div>
      )}

      {(report.risks_and_blind_spots ?? []).length > 0 && (
        <div style={{marginBottom: 12}}>
          <h3 style={{margin: "8px 0 6px"}}>风险 / 盲区</h3>
          <ul style={{marginLeft: 18, fontSize: 13, lineHeight: 1.7}}>
            {report.risks_and_blind_spots.map((r: any, i: number) => <li key={i}>{r}</li>)}
          </ul>
        </div>
      )}

      {(report.recommended_next_steps ?? []).length > 0 && (
        <div>
          <h3 style={{margin: "8px 0 6px"}}>推荐下一步</h3>
          <ol style={{marginLeft: 18, fontSize: 13, lineHeight: 1.7}}>
            {report.recommended_next_steps.map((s: any, i: number) => <li key={i}>{s}</li>)}
          </ol>
        </div>
      )}
    </div>
  );
}

function ChartBlock({chartKey, dna, defaultOpen}: {chartKey: string; dna: DnaArtifact; defaultOpen?: boolean}) {
  const label = CHART_LABELS[chartKey] || chartKey;
  const s = (dna.sections as any) || {};
  return (
    <details open={defaultOpen} style={{marginBottom: 8}}>
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
  const order: { key: string; label: string }[] = [
    { key: "<100",      label: "100 字以内（一句话型）" },
    { key: "100-300",   label: "100–300 字（口语短文）" },
    { key: "300-600",   label: "300–600 字（标准短文）" },
    { key: "600-1000",  label: "600–1000 字（中长文）" },
    { key: "1000-2000", label: "1000–2000 字（干货长文）" },
    { key: "2000+",     label: "2000 字以上（深度长文）" },
  ];
  // Sum totals so we can render an "占比" column the user actually grasps.
  const totalN = order.reduce((s, o) => s + (data[o.key]?.count ?? 0), 0) || 1;
  return (
    <>
      <p className="muted" style={{fontSize: 12, marginTop: 0, marginBottom: 8}}>
        把库里所有笔记按正文字数分桶，看哪种长度的笔记更吃量。「中位互动」= 这一档里位于中间的那篇拿到的点赞数；「头部 10% 互动」= 这一档前 10% 爆款的点赞水位。
      </p>
      <table className="table">
        <thead>
          <tr>
            <th>正文字数段</th>
            <th className="num">这档有几篇</th>
            <th className="num">占总量</th>
            <th className="num" title="该字数段里所有笔记点赞数的中位值 — 代表这种长度的「日常表现」">中位互动</th>
            <th className="num" title="该字数段里前 10% 爆款的点赞水位 — 代表这种长度的「天花板」">头部 10% 互动</th>
          </tr>
        </thead>
        <tbody>
          {order.map(({key, label}) => {
            const d = data[key] ?? {};
            const n = d.count ?? 0;
            const pct = totalN ? (n / totalN * 100) : 0;
            return (
              <tr key={key}>
                <td>{label}</td>
                <td className="num">{n}</td>
                <td className="num">{n ? `${pct.toFixed(1)}%` : "—"}</td>
                <td className="num">{n ? fmtLikes(Math.round(d.likes?.median ?? 0)) : "—"}</td>
                <td className="num">{n ? fmtLikes(Math.round(d.likes?.p90 ?? 0)) : "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </>
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

const LAUNCH_MODES: Record<string, { label: string; emoji: string; color: string; soft: string; tagline: string }> = {
  cold_start: {
    label: "建议冷启动",
    emoji: "🧊",
    color: "#0284c7",
    soft: "#e0f2fe",
    tagline: "先用 3-7 篇低门槛、纯垂直、不强转化的内容养号子，让平台先打上正确标签再发主线内容。",
  },
  hot_start: {
    label: "建议硬启动（热启动）",
    emoji: "🔥",
    color: "#dc2626",
    soft: "#fee2e2",
    tagline: "第一篇就直接发最有把握的爆款角度，不养号，靠内容力直接撕开流量。",
  },
  hybrid: {
    label: "建议混合启动",
    emoji: "🌗",
    color: "#a855f7",
    soft: "#f3e8ff",
    tagline: "前 2 篇冷启动建立标签，第 3 篇起直接打爆款角度。",
  },
};

export function LaunchModeCard({mode}: {mode: any}) {
  const key = String(mode.recommendation || "").toLowerCase();
  const m = LAUNCH_MODES[key] || {
    label: `建议方式：${mode.recommendation || "—"}`,
    emoji: "🚀", color: "#444", soft: "#f3f4f6",
    tagline: "",
  };
  const lvl = mode.agreement_level;
  const lvlBadge = lvl === "both_agree" ? { txt: "双 AI 一致", color: "#10a37f" }
                : lvl === "leaned"      ? { txt: "倾向判断（一边偏向）", color: "#a855f7" }
                : lvl === "split"       ? { txt: "双方有分歧 · 折中建议", color: "#d97706" }
                : null;
  return (
    <div className="card" style={{borderLeft: `4px solid ${m.color}`, background: m.soft}}>
      <div className="row" style={{justifyContent: "space-between", alignItems: "flex-start", gap: 12}}>
        <div style={{flex: 1}}>
          <h2 style={{margin: "0 0 4px", color: m.color}}>
            {m.emoji} {m.label}
          </h2>
          {m.tagline && (
            <p className="muted" style={{margin: "0 0 8px", fontSize: 12.5}}>{m.tagline}</p>
          )}
        </div>
        {lvlBadge && (
          <span style={{
            background: "#fff", color: lvlBadge.color, fontSize: 11.5, fontWeight: 600,
            padding: "3px 9px", borderRadius: 10, border: `1px solid ${lvlBadge.color}30`,
            whiteSpace: "nowrap",
          }}>{lvlBadge.txt}</span>
        )}
      </div>
      {mode.rationale && (
        <div style={{padding: "8px 12px", background: "#fff", borderRadius: 6,
                     fontSize: 13, lineHeight: 1.7, marginTop: 4}}>
          <b>为什么这么选 ：</b>{mode.rationale}
        </div>
      )}
      {mode.first_week_plan && (
        <div style={{padding: "8px 12px", background: "#fff", borderRadius: 6,
                     fontSize: 13, lineHeight: 1.7, marginTop: 8}}>
          <b>第一周怎么执行 ：</b>{mode.first_week_plan}
        </div>
      )}
    </div>
  );
}

function MiniLaunchModeBadge({mode, accentColor}: {mode: any; accentColor: string}) {
  const key = String(mode.recommendation || "").toLowerCase();
  const m = LAUNCH_MODES[key];
  if (!m) return null;
  return (
    <div style={{
      display: "inline-flex", alignItems: "center", gap: 6,
      background: m.soft, color: m.color, padding: "4px 10px",
      borderRadius: 12, fontSize: 12, fontWeight: 600, marginBottom: 10,
      border: `1px solid ${accentColor}30`,
    }}>
      <span>{m.emoji}</span>
      <span>{m.label.replace("建议", "")}</span>
      {mode.rationale && (
        <span className="muted" style={{fontWeight: 400, marginLeft: 4, color: "#555"}}>
          · {String(mode.rationale).slice(0, 56)}{String(mode.rationale).length > 56 ? "…" : ""}
        </span>
      )}
    </div>
  );
}

function OpportunityCard({opp}: {opp: any}) {
  const navigate = useNavigate();
  function pickIt() {
    // Stash a strategy brief prefill in sessionStorage and jump to /strategy.
    // Strategy.tsx reads this on mount.
    try {
      sessionStorage.setItem("strategy.briefPrefill", JSON.stringify({
        positioning: String(opp.opportunity || "").slice(0, 80),
        target_audience: "",
        personal_strengths: "",
        constraints: String(opp.suggested_angle || ""),
        note: `从分析报告带入 ：${opp.opportunity} (${opp.why || ""})`,
      }));
    } catch { /* fall through */ }
    // Bug D 修复 ：直接进 wizard（/strategy 默认会显示 PackView 把 stash 漏掉）
    navigate("/strategy/new");
  }
  return (
    <div onClick={pickIt} style={{padding: "10px 12px", marginBottom: 8,
                          background: "var(--primary-soft)", borderRadius: 6,
                          cursor: "pointer", border: "1px solid transparent",
                          transition: "border-color 0.15s"}}
         onMouseEnter={e => (e.currentTarget.style.borderColor = "var(--primary)")}
         onMouseLeave={e => (e.currentTarget.style.borderColor = "transparent")}>
      <div className="row" style={{justifyContent: "space-between", alignItems: "flex-start"}}>
        <div style={{flex: 1}}>
          <div style={{fontWeight: 600}}>{opp.opportunity}</div>
          {opp.why && (
            <div style={{fontSize: 12, marginTop: 4}}>
              <span className="muted"><b>因为：</b>{opp.why}</span>
            </div>
          )}
          {opp.suggested_angle && (
            <div style={{fontSize: 12, marginTop: 4}}>
              <b style={{color: "var(--primary)"}}>切入方式：</b>{opp.suggested_angle}
            </div>
          )}
        </div>
        <span style={{fontSize: 12, color: "var(--primary)", whiteSpace: "nowrap",
                      paddingTop: 2}}>用这个 →</span>
      </div>
    </div>
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

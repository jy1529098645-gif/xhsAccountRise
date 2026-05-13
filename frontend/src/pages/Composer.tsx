import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { fmtLikes } from "../format";
import AgentConfigPanel, {
  AgentSelection, defaultSelection, selectionToSpecs,
} from "../components/AgentConfigPanel";
import ProgressTimeline, { Stage as TimelineStage } from "../components/ProgressTimeline";
import NextStepCard from "../components/NextStepCard";
import { humaniseError, humaniseErrorAsync } from "../errors";
import type { ComposeBundle, DraftCandidate, Library, Platform } from "../types";

const COMPOSE_STAGES: TimelineStage[] = [
  { label: "🤖 策略师定方向", durationSec: 25, sub: "选 hook 类型 / 开头钩子 / 结构 / 避坑" },
  { label: "🔍 调研员检索参考爆款 (无 LLM)", durationSec: 3 },
  { label: "🤖🤖🤖 起草团并发起草 N 份候选", durationSec: 60 },
  { label: "🤖🤖 审稿团跨家评分", durationSec: 35 },
  { label: "🤖 改稿师按评审改稿", durationSec: 25 },
  { label: "🤖 融合师综合所有候选 → 最终稿", durationSec: 25 },
  { label: "🤖 计划师产发布计划", durationSec: 20 },
];

const ANGLES = ["教程", "痛点", "故事", "工具评测", "对比", "感悟", "数字", "种草", "建议"];

export default function Composer() {
  const [topic, setTopic] = useState("降AI率技巧");
  const [angle, setAngle] = useState("教程");
  const [length, setLength] = useState(600);
  const [cta, setCta] = useState<"none" | "soft" | "strong">("soft");
  const [niche, setNiche] = useState("");
  const [extra, setExtra] = useState("");
  const [platform, setPlatform] = useState<string>("");
  const [platforms, setPlatforms] = useState<Platform[]>([]);
  const [activeLib, setActiveLib] = useState<Library | null>(null);
  const [agentConfig, setAgentConfig] = useState<AgentSelection>(defaultSelection());
  const [showAgentConfig, setShowAgentConfig] = useState(false);

  const [running, setRunning] = useState(false);
  const [bundle, setBundle] = useState<ComposeBundle | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.platforms().then(setPlatforms).catch(() => {});
    api.libraries().then(ls => setActiveLib(ls.find(l => l.active) ?? null)).catch(() => {});
  }, []);

  // ---- Pre-fill from Strategy "出这一篇 →" navigation ---------------------
  // Reads sessionStorage instead of location.state. The old location.state
  // approach + navigate(replace) inside useEffect was somehow blanking the
  // whole page tree for the user (and Libraries / Dashboard right after).
  // sessionStorage is dead simple, survives one cross-page hop, and we
  // delete it immediately on read so refreshes don't re-prefill.
  const [prefillNote, setPrefillNote] = useState<string | null>(null);
  const prefilled = useRef(false);

  useEffect(() => {
    if (prefilled.current) return;
    let bf: any = null;
    try {
      const raw = sessionStorage.getItem("composer.briefPrefill");
      if (raw) {
        bf = JSON.parse(raw);
        sessionStorage.removeItem("composer.briefPrefill");
      }
    } catch { /* malformed storage — ignore */ }
    if (!bf) return;
    prefilled.current = true;
    try {
      if (bf.topic) setTopic(String(bf.topic));
      if (bf.angle && ANGLES.includes(String(bf.angle))) setAngle(String(bf.angle));
      if (typeof bf.target_length === "number") setLength(bf.target_length);
      if (bf.cta_strength === "none" || bf.cta_strength === "soft" || bf.cta_strength === "strong") {
        setCta(bf.cta_strength);
      }
      if (bf.niche) setNiche(String(bf.niche));
      if (bf.extra_constraints) setExtra(String(bf.extra_constraints));
      if (bf.platform) setPlatform(String(bf.platform));
      setPrefillNote(`已从「起号策略」一键带入：「${String(bf.topic || "").slice(0, 40) || "无标题"}」`);
    } catch (e) {
      console.error("prefill failed", e);
    }
  }, []);

  async function run() {
    setRunning(true); setErr(null); setBundle(null);
    try {
      const res = await api.compose({
        topic, angle, target_length: length, cta_strength: cta,
        niche, extra_constraints: extra,
        platform: platform || undefined,
        ...selectionToSpecs(agentConfig),
      });
      setBundle(res);
    } catch (e: any) {
      setErr(await humaniseErrorAsync(e));
    } finally { setRunning(false); }
  }

  const noBackend = !api.isConnected();

  return (
    <div>
      <div className="page-header">
        <h1>✍️ Composer · AI 起号助手</h1>
        <p>填主题 → 点开始 → 多个 AI 协作出最佳稿件 + 发布计划</p>
      </div>

      {noBackend && (
        <div className="banner warn">
          ⚠️ 本地后端没起来。看顶部黄色 banner 复制命令启动。
        </div>
      )}
      {!noBackend && !activeLib && (
        <div className="banner info">
          <b>还没有数据库。</b> 去 <Link to="/libraries">📥 资源库</Link> 拖一个 .db 进来（10 秒就好），再回这里出稿。
        </div>
      )}
      {prefillNote && (
        <div className="banner info" style={{background: "var(--primary-soft)", borderColor: "var(--primary)"}}>
          ✨ {prefillNote} · 你可以再微调一下下面字段，或者直接 ▶️ 开始
        </div>
      )}

      <div className="compose-grid">
        <div className="compose-form card">
          <h2>1. 主题</h2>

          <div style={{marginBottom: 8}}>
            <label>这篇要写什么？</label>
            <input value={topic} onChange={e => setTopic(e.target.value)}
              placeholder="比如：降AI率技巧 / 论文怎么写引言 / 留学党赶ddl" />
          </div>
          <div className="row">
            <div style={{flex: 1}}>
              <label>角度</label>
              <select value={angle} onChange={e => setAngle(e.target.value)}>
                {ANGLES.map(a => <option key={a}>{a}</option>)}
              </select>
            </div>
            <div style={{flex: 1}}>
              <label>正文字数</label>
              <input type="number" min={120} max={3000} step={50}
                value={length} onChange={e => setLength(Number(e.target.value))} />
            </div>
          </div>
          <div className="row">
            <div style={{flex: 1}}>
              <label>结尾引导强度</label>
              <select value={cta} onChange={e => setCta(e.target.value as any)}>
                <option value="none">无（不刻意引流）</option>
                <option value="soft">轻引导（评论/收藏）</option>
                <option value="strong">强转化（求私信/求资源）</option>
              </select>
            </div>
            <div style={{flex: 1}}>
              <label>赛道（可选）</label>
              <input value={niche} onChange={e => setNiche(e.target.value)}
                placeholder="比如：考研 / 留子 / 文献综述" />
            </div>
          </div>
          <div style={{marginBottom: 10}}>
            <label>平台风格 {activeLib?.platform && <span className="muted">· 默认随激活库 ({activeLib.platform})</span>}</label>
            <select value={platform} onChange={e => setPlatform(e.target.value)}>
              <option value="">▾ 跟随激活库</option>
              {platforms.map(p => <option key={p.id} value={p.id}>{p.label}</option>)}
            </select>
          </div>
          <div style={{marginBottom: 10}}>
            <label>额外要求（可选）</label>
            <textarea value={extra} onChange={e => setExtra(e.target.value)}
              placeholder='比如："不要露出 ChatGPT 字样" / "本帖要带降重案例数字"' />
          </div>

          <h2 style={{marginTop: 18}}>
            <span style={{display: "inline-flex", justifyContent: "space-between", width: "100%"}}>
              <span>2. AI 配置</span>
              <button className="ghost" type="button" style={{fontSize: 12, padding: "2px 8px"}}
                onClick={() => setShowAgentConfig(!showAgentConfig)}>
                {showAgentConfig ? "▴ 收起" : "▾ 自定义"}
              </button>
            </span>
          </h2>
          {!showAgentConfig ? (
            <div className="muted" style={{fontSize: 12, padding: 8, background: "#fafafa", borderRadius: 6}}>
              当前用默认配置：6 个 AI 角色（策略师 / 起草团 ×3 / 审稿团 ×2 / 改稿师 / 融合师 / 计划师）。
              想换便宜/最强阵容点上面「▾ 自定义」。
            </div>
          ) : (
            <AgentConfigPanel selection={agentConfig} onChange={setAgentConfig} />
          )}

          <button onClick={run} disabled={running || !topic.trim() || noBackend} style={{marginTop: 16, width: "100%", fontSize: 15, padding: "10px 0"}}>
            {running ? "🤖 AI 们正在协作出稿中…(60-180s)" : "🚀 启动 AI 团队"}
          </button>
          {err && (
            <div className="banner danger" style={{marginTop: 10, display: "flex",
                                                   justifyContent: "space-between",
                                                   alignItems: "flex-start", gap: 12}}>
              <div style={{whiteSpace: "pre-wrap", flex: 1}}>{err}</div>
              <div className="row" style={{gap: 6, flexShrink: 0}}>
                <button className="secondary" style={{padding: "4px 10px", fontSize: 12}}
                  onClick={() => { setErr(null); run(); }}>↻ 重试</button>
                <button className="ghost" style={{padding: "4px 8px", fontSize: 12}}
                  onClick={() => setErr(null)}>关闭</button>
              </div>
            </div>
          )}
        </div>

        <div>
          {running && (
            <div className="card">
              <h2 style={{margin: "0 0 4px"}}>🤖🤖🤖 AI 团队工作中</h2>
              <p className="muted" style={{margin: 0}}>7 个角色协作出稿 + 发布计划</p>
              <ProgressTimeline stages={COMPOSE_STAGES} currentIndex={-1} auto error={err} />
            </div>
          )}
          {!bundle && !running && (
            <div className="card muted" style={{textAlign: "center", padding: 40}}>
              <div style={{fontSize: 36, marginBottom: 10}}>👈</div>
              填好左边的主题，点「🚀 启动 AI 团队」。
              <br />
              <span style={{fontSize: 12}}>结果会在这里展示：6 步 agent 时间线 + N 份候选 + 评审分数 + 改稿 + 融合最终稿 + 发布执行计划</span>
            </div>
          )}
          {bundle && <ComposeResult bundle={bundle} />}
        </div>
      </div>
    </div>
  );
}

function ComposeResult({bundle}: {bundle: ComposeBundle}) {
  return (
    <>
      <div className="card">
        <div className="spread">
          <div>
            <strong>本次出稿 #{bundle.draft_id.slice(0, 8)}</strong>
            <p className="muted">
              耗时 {bundle.totals.elapsed_s}s · 成本 ≈ ${bundle.totals.cost_usd.toFixed(4)} · {bundle.drafts.length} 份候选
            </p>
          </div>
          <Link to={`/drafts/${bundle.draft_id}`}><button className="secondary">详情</button></Link>
        </div>
      </div>

      {bundle.strategy && Object.keys(bundle.strategy).length > 0 && (
        <div className="card">
          <h2>📋 策略 (Strategist)</h2>
          <div className="cards-grid">
            <SBox label="hook 类型" value={bundle.strategy.recommended_hook} />
            <SBox label="开头钩子" value={bundle.strategy.opening_hook} />
            <SBox label="结尾 CTA" value={bundle.strategy.cta_phrase} />
            <SBox label="语气方向" value={bundle.strategy.tone} />
          </div>
          {(bundle.strategy.structure ?? []).length > 0 && (
            <>
              <h3>结构</h3>
              <ol>{bundle.strategy.structure!.map((s, i) => <li key={i}>{s}</li>)}</ol>
            </>
          )}
          {(bundle.strategy.avoid ?? []).length > 0 && (
            <>
              <h3>避坑</h3>
              <ul>{bundle.strategy.avoid!.map((s, i) => <li key={i}>{s}</li>)}</ul>
            </>
          )}
        </div>
      )}

      <div className="card">
        <h2>⏱ AI 时间线</h2>
        <div className="trace-list">
          {bundle.trace.map((s, i) => (
            <div key={i} className={`step ${s.error ? "err" : ""}`}>
              <span>#{s.step_index}</span>
              <span className="agent">{roleName(s.agent_name)}</span>
              <span>{s.error || s.output_summary}</span>
              <span style={{textAlign: "right"}}>{s.latency_ms}ms</span>
            </div>
          ))}
        </div>
      </div>

      <div className="card">
        <h2>📚 参考爆款 ({bundle.rag.refs.length})</h2>
        <ol>
          {bundle.rag.refs.slice(0, 5).map(r => (
            <li key={r.note_id}>[{fmtLikes(r.likes)} likes] {r.title}</li>
          ))}
        </ol>
        <p className="muted" style={{fontSize: 12}}>+ {bundle.rag.comments_count} 条用户原话评论 + {bundle.rag.hooks.length} 个 hook 模板</p>
      </div>

      <div className="card">
        <h2>📝 N 份候选 (起草团 + 审稿团)</h2>
        <div className="candidate-grid">
          {bundle.drafts.map(c => <Candidate key={c.candidate_id} c={c} />)}
        </div>
      </div>

      {bundle.refined && (
        <div className="card">
          <h2>✏️ 改稿 (Refiner)</h2>
          <div className="candidate-grid">
            <Candidate c={bundle.refined} />
          </div>
        </div>
      )}

      {bundle.final && (
        <div className="card">
          <h2>★ 最终稿 (Synthesizer 融合)</h2>
          <div className="candidate-grid">
            <Candidate c={bundle.final} highlighted />
          </div>
        </div>
      )}

      {bundle.plan && Object.keys(bundle.plan).length > 0 && <PlanCard plan={bundle.plan} />}

      <NextStepCard
        label="去 📝 历史出稿 评分 / 标记 final"
        hint="所有出过的稿件都保留着，可以回头打分、对照新的"
        to="/drafts"
        emoji="📝"
      />
    </>
  );
}

const ROLE_LABELS: Record<string, string> = {
  strategist: "策略师",
  researcher: "调研员",
  drafter: "起草",
  critic: "审稿",
  refiner: "改稿师",
  synthesizer: "融合师",
  planner: "计划师",
};
function roleName(agentName: string): string {
  const [base, llm] = agentName.split(":");
  const label = ROLE_LABELS[base] ?? base;
  return llm ? `${label}·${llm}` : label;
}

function SBox({label, value}: {label: string; value?: string}) {
  return (
    <div className="stat-card">
      <div className="label">{label}</div>
      <div style={{fontSize: 13, marginTop: 4, lineHeight: 1.5}}>{value || <em className="muted">—</em>}</div>
    </div>
  );
}

function PlanCard({plan}: {plan: any}) {
  return (
    <div className="card">
      <h2>📋 执行计划 (Planner)</h2>
      {plan.series_thesis && (
        <p style={{fontStyle: "italic", color: "var(--muted)", marginBottom: 14}}>主线：{plan.series_thesis}</p>
      )}
      {plan.publish_schedule?.length > 0 && (
        <>
          <h3>📅 推荐发布时段</h3>
          <table className="table">
            <thead><tr><th>时段</th><th className="num">median likes</th><th>为什么</th></tr></thead>
            <tbody>
              {plan.publish_schedule.map((s: any, i: number) => (
                <tr key={i}>
                  <td><b>{s.slot}</b></td>
                  <td className="num">{s.median_likes?.toLocaleString() ?? "—"}</td>
                  <td className="muted">{s.why}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      {plan.follow_up_angles?.length > 0 && (
        <>
          <h3 style={{marginTop: 14}}>🔁 后续选题 ({plan.follow_up_angles.length})</h3>
          {plan.follow_up_angles.map((a: any, i: number) => (
            <div key={i} style={{padding: "10px 12px", background: "#fafafa", borderRadius: 6, marginBottom: 8}}>
              <div style={{fontWeight: 600}}>{a.title}</div>
              <div style={{fontSize: 12, marginTop: 4}}>
                <span className="tag-pill">{a.angle}</span>
                <span className="tag-pill">{a.hook_type}</span>
              </div>
              <div className="muted" style={{fontSize: 12, marginTop: 6}}>{a.why}</div>
            </div>
          ))}
        </>
      )}
      {plan.engagement_tactics?.length > 0 && (
        <>
          <h3 style={{marginTop: 14}}>💬 互动运营建议</h3>
          <ol style={{marginLeft: 20, lineHeight: 1.7}}>
            {plan.engagement_tactics.map((t: any, i: number) =>
              <li key={i}>{typeof t === "string" ? t : (t?.tactic ?? JSON.stringify(t))}</li>
            )}
          </ol>
        </>
      )}
    </div>
  );
}

function Candidate({c, highlighted}: {c: DraftCandidate; highlighted?: boolean}) {
  if (c.error) {
    return (
      <div className="cand failed">
        <div className="llm">{c.llm}</div>
        <div style={{color: "var(--danger)", marginTop: 6}}>FAILED</div>
        <pre style={{whiteSpace: "pre-wrap", fontSize: 11, color: "var(--danger)"}}>{c.error}</pre>
      </div>
    );
  }
  const p = c.payload;
  const tok = c.token_usage ?? {};
  const scores = c.critiques?.[0]?.scores ?? {};
  return (
    <div className={`cand ${highlighted ? "final" : ""}`}>
      <div className="llm">{c.llm}</div>
      <div className="muted" style={{fontSize: 11}}>
        {c.latency_ms}ms · {tok.input ?? 0}↑/{tok.output ?? 0}↓ · ${c.cost_estimate_usd?.toFixed(4) ?? "0"} ·
        自评 {p.self_score?.toFixed(1)} {c.critique_avg != null && <>· 审稿 <b>{c.critique_avg.toFixed(1)}</b></>}
      </div>
      <div className="title">{p.title}</div>
      <div className="body">{p.body}</div>
      <div style={{marginTop: 8}}>
        {p.tags?.map(t => <span key={t} className="tag-pill">#{t}</span>)}
      </div>
      {p.cover_prompt && <div className="cover"><b>封面图描述：</b>{p.cover_prompt}</div>}
      {Object.keys(scores).length > 0 && (
        <div className="scores">
          {(["hook","language_fit","shareability","brand_safety","structural_clarity"] as const).map(k => (
            <div key={k} className="s">
              <div className="lbl">{({
                hook: "钩子", language_fit: "口语", shareability: "转发",
                brand_safety: "安全", structural_clarity: "结构",
              } as any)[k]}</div>
              <div className="val">{(scores as any)[k]?.toFixed(1) ?? "—"}</div>
            </div>
          ))}
        </div>
      )}
      {c.critiques?.length > 0 && (
        <div style={{marginTop: 8, fontSize: 11.5, color: "#555"}}>
          {c.critiques.map((cr, i) => (
            <div key={i} style={{padding: "4px 6px", borderTop: "1px solid #f0f0f0"}}>
              <b style={{color: "var(--primary)"}}>{cr.critic_llm}</b> 综合 {cr.overall.toFixed(1)} ·
              {cr.risk_flags?.length > 0 && cr.risk_flags.map((f, j) =>
                <span key={j} style={{display: "inline-block", padding: "1px 6px", background: "var(--warn-soft)", color: "var(--warn)", borderRadius: 8, fontSize: 10.5, margin: "0 4px"}}>{f}</span>
              )}
              <div className="muted">{cr.suggestion}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

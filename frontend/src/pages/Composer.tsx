import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { fmtLikes } from "../format";
import type { ComposeBundle, DraftCandidate, Library, Platform } from "../types";

const ANGLES = ["教程", "痛点", "故事", "工具评测", "对比", "感悟", "数字", "种草", "建议"];

export default function Composer() {
  const [topic, setTopic] = useState("降AI率技巧");
  const [angle, setAngle] = useState("教程");
  const [length, setLength] = useState(600);
  const [cta, setCta] = useState<"none" | "soft" | "strong">("soft");
  const [niche, setNiche] = useState("");
  const [extra, setExtra] = useState("");
  const [platform, setPlatform] = useState<string>("");  // "" = inherit from active library
  const [platforms, setPlatforms] = useState<Platform[]>([]);
  const [activeLib, setActiveLib] = useState<Library | null>(null);

  const [strategist, setStrategist] = useState("claude:opus");
  const [drafters, setDrafters] = useState("claude:opus,deepseek,openai");
  const [critics, setCritics] = useState("claude:sonnet,deepseek");
  const [refiner, setRefiner] = useState("claude:opus");
  const [synthesizer, setSynthesizer] = useState("claude:opus");
  const [planner, setPlanner] = useState("claude:opus");
  const [skipStrategist, setSkipStrategist] = useState(false);
  const [skipCritics, setSkipCritics] = useState(false);
  const [skipRefiner, setSkipRefiner] = useState(false);
  const [skipSynthesizer, setSkipSynthesizer] = useState(false);
  const [skipPlanner, setSkipPlanner] = useState(false);

  const [running, setRunning] = useState(false);
  const [bundle, setBundle] = useState<ComposeBundle | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.platforms().then(setPlatforms).catch(() => {});
    api.libraries().then(ls => setActiveLib(ls.find(l => l.active) ?? null)).catch(() => {});
  }, []);

  async function run() {
    setRunning(true); setErr(null); setBundle(null);
    try {
      const res = await api.compose({
        topic, angle, target_length: length, cta_strength: cta,
        niche, extra_constraints: extra,
        platform: platform || undefined,
        strategist_spec: strategist,
        drafter_spec: drafters,
        critic_spec: critics,
        refiner_spec: refiner,
        synthesizer_spec: synthesizer,
        planner_spec: planner,
        skip_strategist: skipStrategist,
        skip_critics: skipCritics,
        skip_refiner: skipRefiner,
        skip_synthesizer: skipSynthesizer,
        skip_planner: skipPlanner,
      });
      setBundle(res);
    } catch (e: any) {
      setErr(e.message);
    } finally { setRunning(false); }
  }

  return (
    <div>
      <div className="page-header">
        <h1>Composer · 多 Agent 内容生成</h1>
        <p>Strategist → Researcher → Drafter pool → Critic pool → Refiner → Synthesizer</p>
      </div>

      {!api.isConnected() && (
        <div className="banner warn">
          后端没连上。顶部已经显示了启动命令；先把后端起起来再回来。
        </div>
      )}
      {api.isConnected() && !activeLib && (
        <div className="banner info">
          <b>还没有激活的库。</b> 去 <Link to="/libraries">📥 资源库</Link> 拖一个 .db 进来再回这里。
        </div>
      )}

      <div className="compose-grid">
        <div className="compose-form card">
          <h2>Brief</h2>

          <div style={{marginBottom: 8}}>
            <label>主题（必填）</label>
            <input value={topic} onChange={e => setTopic(e.target.value)} />
          </div>
          <div className="row">
            <div style={{flex: 1}}>
              <label>角度</label>
              <select value={angle} onChange={e => setAngle(e.target.value)}>
                {ANGLES.map(a => <option key={a}>{a}</option>)}
              </select>
            </div>
            <div style={{flex: 1}}>
              <label>正文目标字数</label>
              <input type="number" min={120} max={3000} step={50}
                value={length} onChange={e => setLength(Number(e.target.value))} />
            </div>
          </div>
          <div className="row">
            <div style={{flex: 1}}>
              <label>CTA 强度</label>
              <select value={cta} onChange={e => setCta(e.target.value as any)}>
                <option value="none">none</option>
                <option value="soft">soft</option>
                <option value="strong">strong</option>
              </select>
            </div>
            <div style={{flex: 1}}>
              <label>赛道（可选）</label>
              <input value={niche} onChange={e => setNiche(e.target.value)} />
            </div>
          </div>
          <div style={{marginBottom: 10}}>
            <label>平台风格 {activeLib && <span className="muted">· 默认随激活库 ({activeLib.platform})</span>}</label>
            <select value={platform} onChange={e => setPlatform(e.target.value)}>
              <option value="">▾ 跟随激活库</option>
              {platforms.map(p => <option key={p.id} value={p.id}>{p.label}</option>)}
            </select>
          </div>
          <div style={{marginBottom: 10}}>
            <label>附加要求（可选）</label>
            <textarea value={extra} onChange={e => setExtra(e.target.value)}
              placeholder='例如："不要露出 ChatGPT 字样"' />
          </div>

          <h2 style={{marginTop: 20}}>Agent 配置</h2>
          <p className="muted">spec 格式：<code className="kbd">claude:opus,deepseek,openai</code>。每家可选 <code className="kbd">opus/sonnet/haiku/chat/...</code></p>

          <Field label="Strategist" value={strategist} onChange={setStrategist} skip={skipStrategist} setSkip={setSkipStrategist} />
          <Field label="Drafter 池" value={drafters} onChange={setDrafters} />
          <Field label="Critic 池" value={critics} onChange={setCritics} skip={skipCritics} setSkip={setSkipCritics} />
          <Field label="Refiner" value={refiner} onChange={setRefiner} skip={skipRefiner} setSkip={setSkipRefiner} />
          <Field label="Synthesizer (融合各家)" value={synthesizer} onChange={setSynthesizer} skip={skipSynthesizer} setSkip={setSkipSynthesizer} />
          <Field label="Planner (出执行计划)" value={planner} onChange={setPlanner} skip={skipPlanner} setSkip={setSkipPlanner} />

          <button onClick={run} disabled={running || !topic.trim()} style={{marginTop: 14, width: "100%"}}>
            {running ? "Agent 运转中…(可能 30s-2min)" : "🚀 启动多 Agent 流水线"}
          </button>
          {err && <div className="banner danger" style={{marginTop: 10}}>{err}</div>}
        </div>

        <div>
          {!bundle && !running && (
            <div className="card muted" style={{textAlign: "center", padding: 40}}>
              填好 brief 点上面那个按钮。生成后这里会显示完整的 agent 时间线 + 所有 LLM 候选 + critic 评分 + refiner 改写 + 最终选择。
            </div>
          )}
          {bundle && <ComposeResult bundle={bundle} />}
        </div>
      </div>
    </div>
  );
}

function Field({label, value, onChange, skip, setSkip}: {
  label: string; value: string; onChange: (v: string) => void;
  skip?: boolean; setSkip?: (v: boolean) => void;
}) {
  return (
    <div style={{marginBottom: 8}}>
      <div className="spread">
        <label style={{marginBottom: 0}}>{label}</label>
        {setSkip && (
          <label style={{fontSize: 11, color: "var(--muted)"}}>
            <input type="checkbox" checked={!!skip} onChange={e => setSkip(e.target.checked)} /> 跳过
          </label>
        )}
      </div>
      <input value={value} onChange={e => onChange(e.target.value)} disabled={skip} />
    </div>
  );
}

function ComposeResult({bundle}: {bundle: ComposeBundle}) {
  return (
    <>
      <div className="card">
        <div className="spread">
          <div>
            <strong>Draft {bundle.draft_id}</strong>
            <p className="muted">
              elapsed {bundle.totals.elapsed_s}s · cost est ${bundle.totals.cost_usd.toFixed(4)} · {bundle.drafts.length} 候选
            </p>
          </div>
          <Link to={`/drafts/${bundle.draft_id}`}><button className="secondary">查看持久化详情</button></Link>
        </div>
      </div>

      {bundle.strategy && Object.keys(bundle.strategy).length > 0 && (
        <div className="card">
          <h2>Strategist 策略</h2>
          <div className="cards-grid">
            <SBox label="推荐 hook" value={bundle.strategy.recommended_hook} />
            <SBox label="开头钩子" value={bundle.strategy.opening_hook} />
            <SBox label="结尾 CTA" value={bundle.strategy.cta_phrase} />
            <SBox label="语气" value={bundle.strategy.tone} />
          </div>
          <h3>结构</h3>
          <ol>{(bundle.strategy.structure ?? []).map((s, i) => <li key={i}>{s}</li>)}</ol>
          <h3>避坑</h3>
          <ul>{(bundle.strategy.avoid ?? []).map((s, i) => <li key={i}>{s}</li>)}</ul>
        </div>
      )}

      <div className="card">
        <h2>Agent 时间线</h2>
        <div className="trace-list">
          {bundle.trace.map((s, i) => (
            <div key={i} className={`step ${s.error ? "err" : ""}`}>
              <span>#{s.step_index}</span>
              <span className="agent">{s.agent_name}</span>
              <span>{s.error || s.output_summary}</span>
              <span style={{textAlign: "right"}}>{s.latency_ms}ms</span>
            </div>
          ))}
        </div>
      </div>

      <div className="card">
        <h2>RAG 参考</h2>
        <ol>
          {bundle.rag.refs.map(r => (
            <li key={r.note_id}>[{fmtLikes(r.likes)} likes] {r.title}</li>
          ))}
        </ol>
        <p className="muted">+ {bundle.rag.comments_count} 条评论 + hooks: {bundle.rag.hooks.join(", ")}</p>
      </div>

      <div className="card">
        <h2>Drafter 候选 ({bundle.drafts.length})</h2>
        <div className="candidate-grid">
          {bundle.drafts.map(c => <Candidate key={c.candidate_id} c={c} />)}
        </div>
      </div>

      {bundle.refined && (
        <div className="card">
          <h2>Refiner 改写</h2>
          <div className="candidate-grid">
            <Candidate c={bundle.refined} />
          </div>
        </div>
      )}

      {bundle.final && (
        <div className="card">
          <h2>★ Final</h2>
          <div className="candidate-grid">
            <Candidate c={bundle.final} highlighted />
          </div>
        </div>
      )}

      {bundle.plan && Object.keys(bundle.plan).length > 0 && <PlanCard plan={bundle.plan} />}
    </>
  );
}

function PlanCard({plan}: {plan: any}) {
  return (
    <div className="card">
      <h2>📋 执行计划 (Planner)</h2>
      {plan.series_thesis && (
        <p style={{fontStyle: "italic", color: "var(--muted)", marginBottom: 14}}>
          主线：{plan.series_thesis}
        </p>
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
          <h3 style={{marginTop: 16}}>🔁 后续选题 ({plan.follow_up_angles.length})</h3>
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
          <h3 style={{marginTop: 16}}>💬 互动运营建议</h3>
          <ol style={{marginLeft: 20, lineHeight: 1.7}}>
            {plan.engagement_tactics.map((t: string, i: number) => <li key={i}>{t}</li>)}
          </ol>
        </>
      )}
    </div>
  );
}

function SBox({label, value}: {label: string; value?: string}) {
  return (
    <div className="stat-card">
      <div className="label">{label}</div>
      <div style={{fontSize: 13, marginTop: 4, lineHeight: 1.5}}>{value || <em className="muted">—</em>}</div>
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
        {c.latency_ms}ms · tok {tok.input ?? 0}/{tok.output ?? 0} · ${c.cost_estimate_usd?.toFixed(4) ?? "0"} ·
        self {p.self_score?.toFixed(1)} {c.critique_avg != null && <>· avg <b>{c.critique_avg.toFixed(1)}</b></>}
      </div>
      <div className="title">{p.title}</div>
      <div className="body">{p.body}</div>
      <div style={{marginTop: 8}}>
        {p.tags?.map(t => <span key={t} className="tag-pill">#{t}</span>)}
      </div>
      {p.cover_prompt && <div className="cover"><b>cover：</b>{p.cover_prompt}</div>}
      {Object.keys(scores).length > 0 && (
        <div className="scores">
          {(["hook","language_fit","shareability","brand_safety","structural_clarity"] as const).map(k => (
            <div key={k} className="s">
              <div className="lbl">{k.split("_")[0]}</div>
              <div className="val">{(scores as any)[k]?.toFixed(1) ?? "—"}</div>
            </div>
          ))}
        </div>
      )}
      {c.critiques?.length > 0 && (
        <div style={{marginTop: 8, fontSize: 11.5, color: "#555"}}>
          {c.critiques.map((cr, i) => (
            <div key={i} style={{padding: "4px 6px", borderTop: "1px solid #f0f0f0"}}>
              <b style={{color: "var(--primary)"}}>{cr.critic_llm}</b> overall {cr.overall.toFixed(1)} ·
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

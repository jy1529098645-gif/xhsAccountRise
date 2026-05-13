import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { platformLabel } from "../format";
import PlatformPill from "../components/PlatformPill";
import { LLM_CATALOG } from "../catalog";
import type {
  AccountInputDTO, Library, Platform, StrategicDirectionDTO, StrategyPackDTO,
} from "../types";

type Phase = "input" | "loading-propose" | "directions" | "loading-expand" | "pack";

const DOW_LABELS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];
const INTENT_COLORS: Record<string, string> = {
  "拉新": "#fff5f5", "互动": "#fff8e6", "转化": "#fdecea", "沉淀": "#f0fafe",
};

export default function Strategy() {
  const [phase, setPhase] = useState<Phase>("input");
  const [input, setInput] = useState<AccountInputDTO>({
    positioning: "",
    target_audience: "",
    cycle_weeks: 4,
    posts_per_week: 3,
    personal_strengths: "",
    constraints: "",
    platform: "",
  });
  const [activeLib, setActiveLib] = useState<Library | null>(null);
  const [platforms, setPlatforms] = useState<Platform[]>([]);
  const [packId, setPackId] = useState<string | null>(null);
  const [directions, setDirections] = useState<StrategicDirectionDTO[]>([]);
  const [chosenIdx, setChosenIdx] = useState<number | null>(null);
  const [pack, setPack] = useState<StrategyPackDTO | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [positionerSpec, setPositionerSpec] = useState("claude:opus");
  const [topicgenSpec, setTopicgenSpec] = useState("claude:opus,deepseek,openai");
  const [schedulerSpec, setSchedulerSpec] = useState("claude:opus");
  const [resourcerSpec, setResourcerSpec] = useState("claude:opus");

  useEffect(() => {
    api.libraries().then(ls => setActiveLib(ls.find(l => l.active) ?? null)).catch(() => {});
    api.platforms().then(setPlatforms).catch(() => {});
  }, []);

  const platform = input.platform || activeLib?.platform || "xiaohongshu";

  async function submitInput() {
    if (!input.positioning.trim() || !input.target_audience.trim()) {
      setErr("「账号定位」和「目标受众」都得填");
      return;
    }
    setErr(null); setInfo(null); setPhase("loading-propose");
    try {
      const res = await api.proposeStrategy({
        ...input,
        platform: platform,
        positioner_spec: positionerSpec,
      });
      setPackId(res.pack_id);
      setDirections(res.directions);
      setPhase("directions");
    } catch (e: any) {
      setErr(e.message); setPhase("input");
    }
  }

  async function pickDirection(idx: number) {
    if (!packId) return;
    setChosenIdx(idx); setErr(null);
    setInfo("AI 正在生成 N 周完整排期 + 材料清单（约 60-120s）…");
    setPhase("loading-expand");
    try {
      const res = await api.expandStrategy(packId, idx, {
        topicgen_spec: topicgenSpec,
        scheduler_spec: schedulerSpec,
        resourcer_spec: resourcerSpec,
      });
      setPack(res.pack);
      setInfo(null);
      setPhase("pack");
    } catch (e: any) {
      setErr(e.message); setPhase("directions");
    }
  }

  function reset() {
    setPhase("input"); setPackId(null); setDirections([]);
    setChosenIdx(null); setPack(null); setErr(null); setInfo(null);
  }

  return (
    <div>
      <div className="page-header">
        <h1>🚀 起号策略 · 第一步先做这个</h1>
        <p>多 AI 团队帮你定方向 + 排周期 + 写每篇标题大纲 + 列要准备的材料</p>
      </div>

      {!api.isConnected() && (
        <div className="banner warn">本地后端没起来 — 看顶部黄条复制命令启动。</div>
      )}
      {!activeLib && api.isConnected() && (
        <div className="banner info">
          <b>建议先有数据库再做策略</b>，AI 才能基于真实爆款数据给方向。
          没库的话也能做（用平台默认风格），但效果会差一截。
          <Link to="/libraries" style={{marginLeft: 8}}>去上传 →</Link>
        </div>
      )}
      {err && <div className="banner danger" onClick={() => setErr(null)}>{err}</div>}
      {info && <div className="banner info">{info}</div>}

      {phase === "input" && (
        <InputForm
          input={input} setInput={setInput}
          platforms={platforms} platformHint={activeLib?.platform}
          showAdvanced={showAdvanced} setShowAdvanced={setShowAdvanced}
          positionerSpec={positionerSpec} setPositionerSpec={setPositionerSpec}
          topicgenSpec={topicgenSpec} setTopicgenSpec={setTopicgenSpec}
          schedulerSpec={schedulerSpec} setSchedulerSpec={setSchedulerSpec}
          resourcerSpec={resourcerSpec} setResourcerSpec={setResourcerSpec}
          onSubmit={submitInput}
        />
      )}

      {phase === "loading-propose" && (
        <LoadingCard
          title="AI 团队正在分析数据 + 拟方向…"
          subtitle="读 brief → 解析爆款 DNA → 输出 3-5 个差异化定位方向（约 20-40s）"
        />
      )}

      {phase === "directions" && (
        <DirectionsList
          directions={directions} chosenIdx={chosenIdx}
          onPick={pickDirection} onReset={reset}
        />
      )}

      {phase === "loading-expand" && (
        <LoadingCard
          title="AI 团队正在排期 + 列材料…"
          subtitle="3 家 LLM 并发起草选题 → 排期师融合排成周历 → 资源师整理材料/风险/指标"
        />
      )}

      {phase === "pack" && pack && (
        <PackView pack={pack} onReset={reset} />
      )}
    </div>
  );
}

function LoadingCard({title, subtitle}: {title: string; subtitle: string}) {
  return (
    <div className="card" style={{textAlign: "center", padding: 48}}>
      <div style={{fontSize: 48, marginBottom: 12}}>🤖🤖🤖</div>
      <h2 style={{margin: 0}}>{title}</h2>
      <p className="muted">{subtitle}</p>
    </div>
  );
}

function InputForm(props: {
  input: AccountInputDTO;
  setInput: (i: AccountInputDTO) => void;
  platforms: Platform[];
  platformHint?: string;
  showAdvanced: boolean;
  setShowAdvanced: (b: boolean) => void;
  positionerSpec: string;
  setPositionerSpec: (s: string) => void;
  topicgenSpec: string;
  setTopicgenSpec: (s: string) => void;
  schedulerSpec: string;
  setSchedulerSpec: (s: string) => void;
  resourcerSpec: string;
  setResourcerSpec: (s: string) => void;
  onSubmit: () => void;
}) {
  const i = props.input;
  function set<K extends keyof AccountInputDTO>(k: K, v: AccountInputDTO[K]) {
    props.setInput({ ...i, [k]: v });
  }
  return (
    <div className="card">
      <h2>1. 你的账号想法</h2>
      <div style={{marginBottom: 10}}>
        <label>账号定位（一句话说清楚做什么）<span style={{color: "var(--danger)"}}>*</span></label>
        <input value={i.positioning} onChange={e => set("positioning", e.target.value)}
          placeholder="比如：留学生写论文工具种草 / 考研一战经验分享 / AI 学术副业" />
      </div>
      <div style={{marginBottom: 10}}>
        <label>目标受众<span style={{color: "var(--danger)"}}>*</span></label>
        <input value={i.target_audience} onChange={e => set("target_audience", e.target.value)}
          placeholder="比如：赶 ddl 的留学生 / 文科类毕业班学生 / 想做 AI 副业的应届生" />
      </div>

      <div className="row">
        <div style={{flex: 1}}>
          <label>运营周期</label>
          <select value={i.cycle_weeks} onChange={e => set("cycle_weeks", Number(e.target.value))}>
            <option value={2}>2 周（冲短期）</option>
            <option value={4}>4 周（推荐起步）</option>
            <option value={8}>8 周（中长期）</option>
            <option value={12}>12 周（深耕）</option>
          </select>
        </div>
        <div style={{flex: 1}}>
          <label>每周更新</label>
          <select value={i.posts_per_week} onChange={e => set("posts_per_week", Number(e.target.value))}>
            <option value={2}>2 篇 / 周（轻量）</option>
            <option value={3}>3 篇 / 周（推荐）</option>
            <option value={5}>5 篇 / 周（高产）</option>
            <option value={7}>每天一篇</option>
          </select>
        </div>
      </div>

      <div style={{marginBottom: 10}}>
        <label>平台 {props.platformHint && <span className="muted">· 默认随激活的库 ({platformLabel(props.platformHint)})</span>}</label>
        <select value={i.platform} onChange={e => set("platform", e.target.value)}>
          <option value="">▾ 跟随激活库</option>
          {props.platforms.map(p => <option key={p.id} value={p.id}>{p.label}</option>)}
        </select>
      </div>

      <div style={{marginBottom: 10}}>
        <label>你的个人优势（可选）</label>
        <textarea value={i.personal_strengths}
          onChange={e => set("personal_strengths", e.target.value)}
          placeholder="比如：985 在读 / 已经用 ChatGPT 写了 5 篇论文 / 有真实降重案例可分享"
          style={{minHeight: 60}} />
      </div>

      <div style={{marginBottom: 14}}>
        <label>附加要求（可选）</label>
        <textarea value={i.constraints} onChange={e => set("constraints", e.target.value)}
          placeholder='比如："不能露出真名" / "前 2 周不能带商品" / "想偏 KOL 路线"'
          style={{minHeight: 50}} />
      </div>

      <div style={{marginBottom: 12}}>
        <button className="ghost" onClick={() => props.setShowAdvanced(!props.showAdvanced)}
          style={{fontSize: 12, padding: "2px 8px"}}>
          {props.showAdvanced ? "▴ 收起 AI 配置" : "▾ AI 配置 (高级)"}
        </button>
      </div>

      {props.showAdvanced && (
        <div className="agent-config" style={{marginBottom: 14}}>
          <SpecField label="🎯 定位师" hint="提案 3-5 个差异化方向（单选）" value={props.positionerSpec} onChange={props.setPositionerSpec} options={LLM_CATALOG.map(l => l.id)} />
          <SpecField label="📝 选题官（并行池）" hint="多家 LLM 并发出选题候选（逗号分隔）" value={props.topicgenSpec} onChange={props.setTopicgenSpec} />
          <SpecField label="📅 排期师" hint="融合候选 + 排成周历（单选）" value={props.schedulerSpec} onChange={props.setSchedulerSpec} options={LLM_CATALOG.map(l => l.id)} />
          <SpecField label="🎒 资源/风险师" hint="整理材料清单 + 风险 + 指标（单选）" value={props.resourcerSpec} onChange={props.setResourcerSpec} options={LLM_CATALOG.map(l => l.id)} />
        </div>
      )}

      <button onClick={props.onSubmit} disabled={!i.positioning.trim() || !i.target_audience.trim()}
        style={{width: "100%", fontSize: 15, padding: "10px 0"}}>
        🚀 启动 AI 团队 → 出 3-5 个候选方向
      </button>
    </div>
  );
}

function SpecField({label, hint, value, onChange, options}: {
  label: string; hint: string; value: string; onChange: (s: string) => void;
  options?: string[];
}) {
  return (
    <div style={{marginBottom: 8}}>
      <label>{label} <span className="muted" style={{fontWeight: 400}}>· {hint}</span></label>
      {options ? (
        <select value={value} onChange={e => onChange(e.target.value)}>
          {options.map(o => <option key={o} value={o}>{LLM_CATALOG.find(l => l.id === o)?.label ?? o}</option>)}
        </select>
      ) : (
        <input value={value} onChange={e => onChange(e.target.value)} />
      )}
    </div>
  );
}

function DirectionsList({directions, chosenIdx, onPick, onReset}: {
  directions: StrategicDirectionDTO[];
  chosenIdx: number | null;
  onPick: (i: number) => void;
  onReset: () => void;
}) {
  return (
    <div>
      <div className="spread" style={{marginBottom: 12}}>
        <h2 style={{margin: 0}}>2. 选一个方向继续</h2>
        <button className="ghost" onClick={onReset}>↺ 重新填表</button>
      </div>
      <p className="muted" style={{fontSize: 13, marginBottom: 16}}>
        AI 团队基于你的初步定位 + 该平台爆款数据，提了 {directions.length} 个差异化方向。
        每个方向都锚定 DNA 里的真实信号（蓝海词 / 用户原话 / 高表现 hook）。挑一个最来电的，下一步出完整周历 + 材料。
      </p>
      <div className="cards-grid" style={{gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))"}}>
        {directions.map((d, i) => (
          <div key={i} className="card" style={{
            border: chosenIdx === i ? "2px solid var(--primary)" : undefined,
            cursor: "pointer",
            padding: "16px 18px",
          }} onClick={() => onPick(i)}>
            <div className="spread" style={{alignItems: "flex-start"}}>
              <div style={{flex: 1}}>
                <div style={{fontSize: 16, fontWeight: 600}}>{d.name}</div>
                <div className="muted" style={{fontSize: 12, marginTop: 2}}>{d.positioning_statement}</div>
              </div>
              <div style={{
                background: "var(--primary-soft)", color: "var(--primary)",
                fontSize: 11, padding: "2px 8px", borderRadius: 10, fontWeight: 600,
                whiteSpace: "nowrap",
              }}>潜力 {d.score?.toFixed(1) ?? "—"}/10</div>
            </div>

            <div style={{fontSize: 12.5, marginTop: 10}}>
              <b>受众：</b>{d.target_audience}
            </div>

            {d.hook_angles?.length > 0 && (
              <div style={{fontSize: 12, marginTop: 8}}>
                <b style={{color: "#555"}}>hook 角度：</b>
                <div style={{marginTop: 4}}>
                  {d.hook_angles.map((h, j) => <span key={j} className="tag-pill" style={{marginBottom: 2}}>{h}</span>)}
                </div>
              </div>
            )}

            {d.differentiator && (
              <div style={{fontSize: 12, marginTop: 10}}>
                <b style={{color: "#555"}}>差异化：</b>
                <span className="muted">{d.differentiator}</span>
              </div>
            )}
            {d.risk && (
              <div style={{fontSize: 12, marginTop: 6}}>
                <b style={{color: "var(--warn)"}}>风险：</b>
                <span className="muted">{d.risk}</span>
              </div>
            )}
            {d.why_works && (
              <div style={{fontSize: 11.5, marginTop: 10, padding: 8, background: "#fafafa", borderRadius: 6, fontStyle: "italic"}}>
                💡 {d.why_works}
              </div>
            )}

            <button style={{width: "100%", marginTop: 12}}>选这个方向 →</button>
          </div>
        ))}
      </div>
    </div>
  );
}

function PackView({pack, onReset}: {pack: StrategyPackDTO; onReset: () => void}) {
  const totalSlots = pack.schedule.length;
  return (
    <div>
      <div className="spread" style={{marginBottom: 12}}>
        <div>
          <h2 style={{margin: 0}}>3. 完整起号策略包</h2>
          <div className="muted" style={{fontSize: 12, marginTop: 2}}>
            <PlatformPill platform={pack.platform} /> · {pack.input.cycle_weeks} 周 · {totalSlots} 篇排期
          </div>
        </div>
        <div className="row" style={{gap: 6}}>
          <Link to="/composer"><button>去 Composer 出第一篇 →</button></Link>
          <button className="secondary" onClick={onReset}>新建策略</button>
        </div>
      </div>

      <div className="card">
        <h2>方向 · {pack.chosen_direction.name}</h2>
        <p style={{margin: "4px 0", fontSize: 14}}>{pack.chosen_direction.positioning_statement}</p>
        <p className="muted" style={{fontSize: 12}}>受众：{pack.chosen_direction.target_audience}</p>
        {pack.series_thesis && (
          <p style={{fontStyle: "italic", color: "var(--muted)", fontSize: 13, marginTop: 8}}>
            主线：{pack.series_thesis}
          </p>
        )}
      </div>

      {pack.weekly_themes.length > 0 && (
        <div className="card">
          <h2>📅 周主题</h2>
          <div className="cards-grid">
            {pack.weekly_themes.map((w, i) => (
              <div key={i} className="stat-card" style={{
                background: INTENT_COLORS[w.intent] ?? undefined,
              }}>
                <div className="label">第 {w.week} 周 · {w.intent}</div>
                <div style={{fontSize: 14, fontWeight: 600, marginTop: 4}}>{w.theme}</div>
                {w.notes && <div className="muted" style={{fontSize: 11, marginTop: 4}}>{w.notes}</div>}
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="card">
        <h2>📝 全部 {totalSlots} 篇排期</h2>
        <table className="table">
          <thead>
            <tr>
              <th>周</th><th>时段</th><th>标题</th><th>角度</th><th>意图</th><th>材料</th>
            </tr>
          </thead>
          <tbody>
            {pack.schedule.map((s, i) => (
              <tr key={i}>
                <td className="num">W{s.week}<br/><span className="muted" style={{fontSize: 11}}>{DOW_LABELS[s.day_of_week] ?? `D${s.day_of_week}`}</span></td>
                <td><b>{s.publish_slot || "—"}</b></td>
                <td>
                  <div style={{fontWeight: 600}}>{s.title}</div>
                  {s.title_variants?.length > 0 && (
                    <div className="muted" style={{fontSize: 11}}>变体：{s.title_variants.slice(0, 2).join(" / ")}</div>
                  )}
                  {s.outline?.length > 0 && (
                    <details style={{marginTop: 4}}>
                      <summary style={{cursor: "pointer", fontSize: 11.5, color: "var(--muted)"}}>▾ 内容大纲</summary>
                      <ul style={{margin: "4px 0 0 18px", fontSize: 12, lineHeight: 1.6}}>
                        {s.outline.map((o, j) => <li key={j}>{o}</li>)}
                      </ul>
                    </details>
                  )}
                </td>
                <td><span className="tag-pill">{s.angle}</span><br/><span className="tag-pill">{s.hook_type}</span></td>
                <td><span className="tag-pill" style={{background: INTENT_COLORS[s.intent] ?? "#f4f4f4"}}>{s.intent}</span></td>
                <td className="muted" style={{fontSize: 11.5}}>
                  {s.materials_needed?.map((m, j) => <div key={j}>· {m}</div>)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {pack.materials_checklist.length > 0 && (
        <div className="card">
          <h2>🎒 启动前要准备的材料</h2>
          <ul style={{marginLeft: 20, lineHeight: 1.9}}>
            {pack.materials_checklist.map((m, i) => <li key={i}>{m}</li>)}
          </ul>
        </div>
      )}

      {pack.risks_and_mitigations.length > 0 && (
        <div className="card">
          <h2>⚠️ 风险 + 应对</h2>
          <ol style={{marginLeft: 20, lineHeight: 1.9}}>
            {pack.risks_and_mitigations.map((r, i) => <li key={i}>{r}</li>)}
          </ol>
        </div>
      )}

      {pack.success_metrics.length > 0 && (
        <div className="card">
          <h2>📈 成功指标</h2>
          <ul style={{marginLeft: 20, lineHeight: 1.9}}>
            {pack.success_metrics.map((m, i) => <li key={i}>{m}</li>)}
          </ul>
        </div>
      )}
    </div>
  );
}

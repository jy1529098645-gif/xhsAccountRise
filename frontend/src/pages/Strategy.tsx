import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { fmtRelative, platformLabel } from "../format";
import PlatformPill from "../components/PlatformPill";
import ProgressTimeline, { Stage } from "../components/ProgressTimeline";
import { humaniseError } from "../errors";
import { LLM_CATALOG } from "../catalog";
import type {
  AccountInputDTO, Library, Platform, StrategicDirectionDTO, StrategyPackDTO,
  StrategyListItem,
} from "../types";

const AUTOFILL_STAGES: Stage[] = [
  { label: "🤖 Claude 看你的库出一版初稿", durationSec: 20,
    sub: "拟方向 / 受众 / 周期 / 频率 等字段" },
  { label: "🤖 OpenAI 独立出另一版", durationSec: 20,
    sub: "并行进行" },
  { label: "🤖 主编融合共识 → 给你一份合并稿", durationSec: 15 },
];

const PROPOSE_STAGES: Stage[] = [
  { label: "🤖 读 DNA + 你的 brief", durationSec: 5 },
  { label: "🤖 Claude 产 3-5 个差异化方向", durationSec: 25,
    sub: "每个方向带 hook / 受众 / 风险 / 备选" },
];

const EXPAND_STAGES: Stage[] = [
  { label: "🤖 3 家 LLM 并发起草选题候选（30+ 条）", durationSec: 50 },
  { label: "🤖 排期师融合 + 排进周历", durationSec: 35 },
  { label: "🤖 资源/风险师整理材料清单 + 指标", durationSec: 20 },
];

type Phase = "autofilling" | "input" | "loading-propose" | "directions" | "loading-expand" | "pack";

interface FieldRationale {
  source: string;          // 'merged' | 'consensus' | 'claude' | 'openai'
  rationale: string;
  alternatives?: any[];
}
interface AutofillResult {
  input: AccountInputDTO;
  field_rationale: Record<string, FieldRationale>;
  consensus_notes: string[];
  single_side_views: { side: string; field: string; point: string; note?: string }[];
  elapsed_s: number;
}

const DOW_LABELS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];
const INTENT_COLORS: Record<string, string> = {
  "拉新": "#fff5f5", "互动": "#fff8e6", "转化": "#fdecea", "沉淀": "#f0fafe",
};

// localStorage key for in-progress brief draft (per project)
const DRAFT_KEY = "studio.strategy.draftInput";
function emptyInput(): AccountInputDTO {
  return {
    positioning: "", target_audience: "",
    cycle_weeks: 4, posts_per_week: 3,
    personal_strengths: "", constraints: "", platform: "",
  };
}

export default function Strategy() {
  const { packId: urlPackId } = useParams<{ packId?: string }>();
  const navigate = useNavigate();

  const [phase, setPhase] = useState<Phase>("input");
  const [input, setInput] = useState<AccountInputDTO>(() => {
    // Resume in-progress draft from localStorage if present.
    try {
      const cached = localStorage.getItem(DRAFT_KEY);
      if (cached) return { ...emptyInput(), ...JSON.parse(cached) };
    } catch { /* ignore */ }
    return emptyInput();
  });
  const [activeLib, setActiveLib] = useState<Library | null>(null);
  const [platforms, setPlatforms] = useState<Platform[]>([]);
  const [packId, setPackId] = useState<string | null>(null);
  const [directions, setDirections] = useState<StrategicDirectionDTO[]>([]);
  const [chosenIdx, setChosenIdx] = useState<number | null>(null);
  const [pack, setPack] = useState<StrategyPackDTO | null>(null);
  const [history, setHistory] = useState<StrategyListItem[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [positionerSpec, setPositionerSpec] = useState("claude:opus");
  const [topicgenSpec, setTopicgenSpec] = useState("claude:opus,deepseek,openai");
  const [schedulerSpec, setSchedulerSpec] = useState("claude:opus");
  const [resourcerSpec, setResourcerSpec] = useState("claude:opus");
  const [autofill, setAutofill] = useState<AutofillResult | null>(null);
  const [autofillErr, setAutofillErr] = useState<string | null>(null);

  // Save in-progress input to localStorage as user types.
  useEffect(() => {
    if (phase !== "input") return;
    try {
      const trimmed = { ...input };
      // Don't persist trivial empty state.
      if (trimmed.positioning || trimmed.target_audience || trimmed.personal_strengths) {
        localStorage.setItem(DRAFT_KEY, JSON.stringify(trimmed));
      }
    } catch { /* ignore quota etc. */ }
  }, [input, phase]);

  // Load library list + history on mount; trigger autofill only on first-time
  // use (no existing history, no in-progress draft, no specific pack in URL).
  useEffect(() => {
    api.libraries().then(ls => setActiveLib(ls.find(l => l.active) ?? null)).catch(() => {});
    api.platforms().then(setPlatforms).catch(() => {});
    api.listStrategies().then(hs => {
      setHistory(hs);
      if (urlPackId) return;  // The other effect will load it
      let hasDraft = false;
      try {
        const cached = localStorage.getItem(DRAFT_KEY);
        if (cached) {
          const d = JSON.parse(cached);
          hasDraft = !!(d.positioning?.trim() || d.target_audience?.trim());
        }
      } catch { /* ignore */ }
      if (api.isConnected() && !hasDraft && hs.length === 0) {
        runAutofill();
      }
    }).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // If URL contains a packId, load that saved pack.
  useEffect(() => {
    if (!urlPackId) return;
    (async () => {
      try {
        setPhase("loading-expand");
        const d = await api.getStrategy(urlPackId);
        if (d.pack) {
          setPack(d.pack);
          setPackId(urlPackId);
          setPhase("pack");
        } else if (d.directions?.length) {
          // Pack expansion never finished — let user pick again.
          setPackId(urlPackId);
          setDirections(d.directions);
          setPhase("directions");
        } else {
          setPhase("input");
        }
      } catch (e: any) {
        setErr(`无法加载该策略 ${urlPackId}: ${e.message}`);
        setPhase("input");
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [urlPackId]);

  async function runAutofill(extraHints?: { personal?: string; constraints?: string }) {
    setPhase("autofilling"); setAutofillErr(null);
    try {
      const r = await api.autofillStrategy({
        personal_hint: extraHints?.personal ?? input.personal_strengths ?? "",
        constraints_hint: extraHints?.constraints ?? input.constraints ?? "",
        claude_spec: "claude:opus",
        openai_spec: "openai",
        moderator_spec: "claude:opus",
      });
      setAutofill(r);
      setInput({
        positioning: r.input.positioning || "",
        target_audience: r.input.target_audience || "",
        cycle_weeks: r.input.cycle_weeks || 4,
        posts_per_week: r.input.posts_per_week || 3,
        personal_strengths: r.input.personal_strengths || "",
        constraints: r.input.constraints || "",
        platform: r.input.platform || "",
      });
      setPhase("input");
    } catch (e: any) {
      setAutofillErr(humaniseError(e));
      setPhase("input");
    }
  }

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
      // Persist URL so reload/bookmark works
      navigate(`/strategy/${res.pack_id}`, { replace: true });
      api.listStrategies().then(setHistory).catch(() => {});
    } catch (e: any) {
      setErr(humaniseError(e)); setPhase("input");
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
      // Update URL so refresh / bookmark works
      navigate(`/strategy/${packId}`, { replace: true });
      // Once expanded successfully, clear in-progress input draft
      try { localStorage.removeItem(DRAFT_KEY); } catch { /* ignore */ }
      // Refresh history list
      api.listStrategies().then(setHistory).catch(() => {});
    } catch (e: any) {
      setErr(humaniseError(e)); setPhase("directions");
    }
  }

  function reset() {
    setPhase("input"); setPackId(null); setDirections([]);
    setChosenIdx(null); setPack(null); setErr(null); setInfo(null);
    setAutofill(null);
    try { localStorage.removeItem(DRAFT_KEY); } catch { /* ignore */ }
    setInput(emptyInput());
    if (urlPackId) navigate("/strategy");
  }

  function startNew(useAi = true) {
    setPhase("input"); setPackId(null); setDirections([]);
    setChosenIdx(null); setPack(null); setErr(null); setInfo(null);
    if (urlPackId) navigate("/strategy");
    if (useAi && api.isConnected()) {
      runAutofill();
    }
  }

  return (
    <div>
      <div className="page-header">
        <h1>🚀 起号策略 · 第 2 步</h1>
        <p>多 AI 团队帮你定方向 + 排周期 + 写每篇标题大纲 + 列要准备的材料</p>
        <p className="muted" style={{fontSize: 12, marginTop: 4}}>
          💡 建议先去 <Link to="/reports">📊 分析报告</Link> 看一眼 Claude × OpenAI 对你库的共识洞察，再回这里拟策略，效果更准。
        </p>
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

      {/* Strategy history — visible across all phases when not viewing a specific pack */}
      {history.length > 0 && phase === "input" && !urlPackId && (
        <div className="card" style={{background: "#fafafa"}}>
          <div className="spread">
            <h3 style={{margin: 0}}>📜 历史策略（{history.length}）· 之前的方案仍然保留</h3>
            <button className="ghost" onClick={() => startNew(true)} style={{fontSize: 12}}>+ 新建（让 AI 重新拟）</button>
          </div>
          <table className="table" style={{marginTop: 8}}>
            <thead>
              <tr><th>定位</th><th>周期</th><th>状态</th><th>时间</th><th></th></tr>
            </thead>
            <tbody>
              {history.slice(0, 10).map(h => (
                <tr key={h.pack_id}>
                  <td>{h.input?.positioning?.slice(0, 36) || <em className="muted">—</em>}</td>
                  <td className="muted">{h.input?.cycle_weeks ?? "?"} 周 · {h.input?.posts_per_week ?? "?"} 篇/周</td>
                  <td>
                    {h.status === "expanded" ? (
                      <span style={{color: "var(--ok)"}}>✓ 完整方案</span>
                    ) : (
                      <span className="muted">仅候选方向</span>
                    )}
                  </td>
                  <td className="muted">{fmtRelative(h.created_at)}</td>
                  <td><Link to={`/strategy/${h.pack_id}`}>打开 →</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {phase === "autofilling" && (
        <div className="card">
          <h2 style={{margin: "0 0 4px"}}>🤖🤖 AI 双方正在为你拟起号初稿</h2>
          <p className="muted" style={{margin: 0}}>Claude + OpenAI 独立分析 → 互评 → 主编融合共识</p>
          <ProgressTimeline stages={AUTOFILL_STAGES} currentIndex={-1} auto />
        </div>
      )}

      {phase === "input" && (
        <>
          {autofill && (
            <div className="banner info" style={{display: "block"}}>
              <div className="row" style={{justifyContent: "space-between", alignItems: "flex-start"}}>
                <div>
                  <b>✨ AI 已为你拟好初稿</b> · {autofill.elapsed_s}s 完成
                  <div className="muted" style={{fontSize: 12, marginTop: 2}}>
                    下面每个字段都可以编辑。带 💡 的字段点开看 AI 是怎么推的（含备选）。
                  </div>
                </div>
                <button className="ghost" onClick={() => runAutofill()}
                  style={{fontSize: 12, padding: "4px 10px"}}>🪄 重新让 AI 拟</button>
              </div>
            </div>
          )}
          {autofillErr && (
            <div className="banner warn">
              ⚠️ AI 自动拟稿失败：{autofillErr} · 你可以自己填，或者
              <button className="ghost" onClick={() => runAutofill()}
                style={{fontSize: 12, padding: "2px 8px", marginLeft: 6}}>🪄 再试一次</button>
            </div>
          )}
          <InputForm
            input={input} setInput={setInput}
            platforms={platforms} platformHint={activeLib?.platform}
            showAdvanced={showAdvanced} setShowAdvanced={setShowAdvanced}
            positionerSpec={positionerSpec} setPositionerSpec={setPositionerSpec}
            topicgenSpec={topicgenSpec} setTopicgenSpec={setTopicgenSpec}
            schedulerSpec={schedulerSpec} setSchedulerSpec={setSchedulerSpec}
            resourcerSpec={resourcerSpec} setResourcerSpec={setResourcerSpec}
            onSubmit={submitInput}
            fieldRationale={autofill?.field_rationale ?? {}}
            consensusNotes={autofill?.consensus_notes ?? []}
            singleSideViews={autofill?.single_side_views ?? []}
          />
        </>
      )}

      {phase === "loading-propose" && (
        <div className="card">
          <h2 style={{margin: "0 0 4px"}}>🤖 AI 在为你拟候选方向</h2>
          <p className="muted" style={{margin: 0}}>读 brief → 解析爆款 DNA → 输出 3-5 个差异化定位</p>
          <ProgressTimeline stages={PROPOSE_STAGES} currentIndex={-1} auto />
        </div>
      )}

      {phase === "directions" && (
        <DirectionsList
          directions={directions} chosenIdx={chosenIdx}
          onPick={pickDirection} onReset={reset}
        />
      )}

      {phase === "loading-expand" && (
        <div className="card">
          <h2 style={{margin: "0 0 4px"}}>🤖🤖🤖 AI 团队正在排期 + 列材料</h2>
          <p className="muted" style={{margin: 0}}>多家 LLM 协作出完整周历 + 材料清单 + 风险评估</p>
          <ProgressTimeline stages={EXPAND_STAGES} currentIndex={-1} auto />
        </div>
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
  fieldRationale: Record<string, FieldRationale>;
  consensusNotes: string[];
  singleSideViews: { side: string; field: string; point: string; note?: string }[];
}) {
  const i = props.input;
  function set<K extends keyof AccountInputDTO>(k: K, v: AccountInputDTO[K]) {
    props.setInput({ ...i, [k]: v });
  }
  const hasAutofill = Object.keys(props.fieldRationale).length > 0;
  return (
    <div className="card">
      <h2>{hasAutofill ? "1. 检查 / 编辑 AI 拟好的初稿" : "1. 你的账号想法"}</h2>

      <FieldWithRationale label="账号定位（一句话说清楚做什么）" required
        rationale={props.fieldRationale.positioning}
        onAlt={(v) => set("positioning", v)}>
        <input value={i.positioning} onChange={e => set("positioning", e.target.value)}
          placeholder="比如：留学生写论文工具种草 / 考研一战经验分享 / AI 学术副业" />
      </FieldWithRationale>

      <FieldWithRationale label="目标受众" required
        rationale={props.fieldRationale.target_audience}
        onAlt={(v) => set("target_audience", v)}>
        <input value={i.target_audience} onChange={e => set("target_audience", e.target.value)}
          placeholder="比如：赶 ddl 的留学生 / 文科类毕业班学生 / 想做 AI 副业的应届生" />
      </FieldWithRationale>

      <div className="row" style={{gap: 12, marginBottom: 4}}>
        <FieldWithRationale label="运营周期"
          rationale={props.fieldRationale.cycle_weeks}
          onAlt={(v) => set("cycle_weeks", Number(v))}
          style={{flex: 1}}>
          <select value={i.cycle_weeks} onChange={e => set("cycle_weeks", Number(e.target.value))}>
            <option value={2}>2 周（冲短期）</option>
            <option value={4}>4 周（推荐起步）</option>
            <option value={8}>8 周（中长期）</option>
            <option value={12}>12 周（深耕）</option>
          </select>
        </FieldWithRationale>
        <FieldWithRationale label="每周更新"
          rationale={props.fieldRationale.posts_per_week}
          onAlt={(v) => set("posts_per_week", Number(v))}
          style={{flex: 1}}>
          <select value={i.posts_per_week} onChange={e => set("posts_per_week", Number(e.target.value))}>
            <option value={2}>2 篇 / 周（轻量）</option>
            <option value={3}>3 篇 / 周（推荐）</option>
            <option value={5}>5 篇 / 周（高产）</option>
            <option value={7}>每天一篇</option>
          </select>
        </FieldWithRationale>
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

      {(props.consensusNotes.length > 0 || props.singleSideViews.length > 0) && (
        <details style={{marginTop: 14}}>
          <summary style={{cursor: "pointer", fontSize: 12.5, color: "var(--muted)"}}>
            ▾ 看 AI 双方分析的共识 + 分歧（共 {props.consensusNotes.length} 条共识 / {props.singleSideViews.length} 条分歧）
          </summary>
          {props.consensusNotes.length > 0 && (
            <div style={{marginTop: 8}}>
              <div style={{fontSize: 12, fontWeight: 600, color: "#555", marginBottom: 4}}>双方共识</div>
              <ul style={{margin: 0, marginLeft: 18, fontSize: 12, lineHeight: 1.7}}>
                {props.consensusNotes.map((n, j) => <li key={j}>{n}</li>)}
              </ul>
            </div>
          )}
          {props.singleSideViews.length > 0 && (
            <div style={{marginTop: 10}}>
              <div style={{fontSize: 12, fontWeight: 600, color: "#555", marginBottom: 4}}>分歧 / 单方观点</div>
              {props.singleSideViews.map((v, j) => (
                <div key={j} style={{padding: "4px 8px", marginBottom: 4, fontSize: 11.5,
                  borderLeft: `3px solid ${v.side === "claude" ? "#a36df0" : "#10a37f"}`,
                  background: "#fafafa"}}>
                  <b>{v.side === "claude" ? "🟣 Claude" : "🟢 OpenAI"}</b> · {v.field}：{v.point}
                </div>
              ))}
            </div>
          )}
        </details>
      )}
    </div>
  );
}

function FieldWithRationale({label, required, rationale, onAlt, children, style}: {
  label: string;
  required?: boolean;
  rationale?: FieldRationale;
  onAlt?: (value: string) => void;
  children: React.ReactNode;
  style?: React.CSSProperties;
}) {
  const [showRat, setShowRat] = useState(false);
  const hasRat = rationale?.rationale;
  const hasAlts = rationale?.alternatives && rationale.alternatives.length > 0;
  return (
    <div style={{marginBottom: 10, ...style}}>
      <div className="row" style={{justifyContent: "space-between", alignItems: "baseline", gap: 6}}>
        <label>
          {label}{required && <span style={{color: "var(--danger)"}}> *</span>}
        </label>
        {hasRat && (
          <button className="ghost" onClick={() => setShowRat(!showRat)} type="button"
            style={{fontSize: 11, padding: "1px 8px"}}>
            💡 AI 怎么推的{rationale!.source ? ` [${rationale!.source}]` : ""}
          </button>
        )}
      </div>
      {children}
      {showRat && rationale && (
        <div style={{
          marginTop: 6, padding: "8px 10px",
          background: "#fff7e6", border: "1px solid #fde2a3",
          borderRadius: 6, fontSize: 12, lineHeight: 1.6,
        }}>
          <div style={{color: "#92400e"}}>💡 {rationale.rationale}</div>
          {hasAlts && (
            <div style={{marginTop: 6}}>
              <div className="muted" style={{fontSize: 11, marginBottom: 3}}>备选（点击采用）：</div>
              {rationale.alternatives!.map((alt, i) => (
                <button key={i} type="button"
                  onClick={() => { onAlt?.(String(alt)); setShowRat(false); }}
                  className="ghost"
                  style={{
                    display: "block", width: "100%", textAlign: "left",
                    fontSize: 11.5, padding: "4px 8px", marginBottom: 2,
                    background: "#fff", border: "1px solid var(--border)",
                    borderRadius: 4,
                  }}>
                  {String(alt)}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
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

import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { fmtLikes, roleName, slotDate, defaultCycleStartDate, topPublishingSlots } from "../format";
import AgentConfigPanel, {
  AgentSelection, defaultSelection, selectionToSpecs,
} from "../components/AgentConfigPanel";
import ProgressTimeline, { Stage as TimelineStage } from "../components/ProgressTimeline";
import NextStepCard from "../components/NextStepCard";
import PlatformPill from "../components/PlatformPill";
import { humaniseError, humaniseErrorAsync } from "../errors";
import { isAborted } from "../api";
import { startJob, getJob, cancelJob, useJob } from "../lib/jobs";
import { LLM_CATALOG } from "../catalog";
import type {
  ComposeBundle, DraftCandidate, Library, Platform,
  StrategyPackDTO, TopicSlotDTO,
} from "../types";

// v0.62.4 ：StrategyOverview / IterateCard / TopPublishingSlotsCard 从
// Strategy.tsx 整体搬过来。Strategy 现在不再渲染 PackView，expand 完成
// 后直接跳 /composer?pack=... — 所有 pack 概览 + 排期 + 迭代 UI 集中
// 在 Composer 这一个板块里，跟「写正文」环节无缝衔接。
const DIRECTION_COLORS = ["#2E5C8A", "#a36df0", "#10a37f", "#e0a800", "#c4429a", "#5BC0EB", "#FCB97D", "#7a6fc8"];
const INTENT_COLORS: Record<string, string> = {
  "拉新": "#fff5f5", "互动": "#fff8e6", "转化": "#fdecea", "沉淀": "#f0fafe",
};

const COMPOSE_STAGES: TimelineStage[] = [
  { label: "🤖 策略师定方向", durationSec: 25, sub: "选 hook 类型 / 开头钩子 / 结构 / 避坑" },
  { label: "🔍 调研员检索参考爆款 (无 LLM)", durationSec: 3 },
  { label: "🤖🤖🤖 起草团并发起草 N 份候选", durationSec: 60 },
  { label: "🤖🤖 审稿团跨家评分", durationSec: 35 },
  { label: "🤖 改稿师按评审改稿", durationSec: 25 },
  { label: "🤖 融合师综合所有候选 → 最终稿", durationSec: 25 },
  { label: "🤖 计划师产发布计划", durationSec: 20 },
];

const ANGLES = [
  "教程", "痛点", "故事", "工具评测", "对比", "感悟", "数字", "种草", "建议",
  "段子", "科普", "避雷", "测评",
  // v0.61.22 新增 5 个 ：盘点 / 复盘 / 问答 / 打卡 / 教训
  "盘点", "复盘", "问答", "打卡", "教训",
];

// v0.51 → v0.52: persist the user's form state across navigation. Now also
// stores `angles` (multi-select). `angle` is kept as the primary fallback so
// existing callers / older saved state still works.
// v0.61.15 ：按项目作用域。每个项目独立保存 Composer 表单。老 key 一次性
// 迁移到默认项目。
const COMPOSER_FORM_KEY_BASE = "studio.composer.form.v1";
const COMPOSER_FORM_LEGACY_KEY = "studio.composer.form.v1";
function composerFormKey(): string {
  let pid = "default";
  try { pid = localStorage.getItem("studio.activeProjectId") || "default"; } catch { /* ignore */ }
  return `${COMPOSER_FORM_KEY_BASE}.${pid}`;
}
interface ComposerFormState {
  topic: string; angle: string; angles: string[]; length: number;
  cta: "none" | "soft" | "strong";
  niche: string; extra: string; platform: string;
  /** v0.61.22 ：每角度专属 model spec。空 string / 缺失 = "auto"（用 round-robin）。 */
  angleModels?: Record<string, string>;
}
const COMPOSER_FORM_DEFAULT: ComposerFormState = {
  topic: "降AI率技巧", angle: "教程", angles: ["教程"], length: 600, cta: "soft",
  niche: "", extra: "", platform: "", angleModels: {},
};
function loadComposerForm(): ComposerFormState {
  try {
    const k = composerFormKey();
    let raw = localStorage.getItem(k);
    if (!raw) {
      // 一次性 ：default 项目从老 legacy key 继承
      let pid = "default";
      try { pid = localStorage.getItem("studio.activeProjectId") || "default"; } catch { /* ignore */ }
      if (pid === "default") {
        const legacy = localStorage.getItem(COMPOSER_FORM_LEGACY_KEY);
        if (legacy && legacy !== "{}") {
          try {
            localStorage.setItem(k, legacy);
            localStorage.removeItem(COMPOSER_FORM_LEGACY_KEY);
            raw = legacy;
          } catch { /* quota */ }
        }
      }
    }
    if (!raw) return COMPOSER_FORM_DEFAULT;
    const parsed = JSON.parse(raw) as Partial<ComposerFormState>;
    const merged = { ...COMPOSER_FORM_DEFAULT, ...parsed };
    // Migrate v0.51 saves (no `angles` field) → seed from singular `angle`.
    if (!Array.isArray(merged.angles) || merged.angles.length === 0) {
      merged.angles = [merged.angle || "教程"];
    }
    return merged;
  } catch { return COMPOSER_FORM_DEFAULT; }
}

export default function Composer() {
  const initialForm = useRef(loadComposerForm()).current;
  const [topic, setTopic] = useState(initialForm.topic);
  const [angles, setAngles] = useState<string[]>(initialForm.angles);
  const [length, setLength] = useState(initialForm.length);
  const [cta, setCta] = useState<"none" | "soft" | "strong">(initialForm.cta);
  const [niche, setNiche] = useState(initialForm.niche);
  const [extra, setExtra] = useState(initialForm.extra);
  const [platform, setPlatform] = useState<string>(initialForm.platform);
  // v0.61.22 ：每角度专属 model spec。""/缺失 = auto = round-robin。
  const [angleModels, setAngleModels] = useState<Record<string, string>>(
    initialForm.angleModels ?? {}
  );

  // v0.62 ：从 URL ?pack=PACK_ID 加载 Strategy 输出的 schedule，在顶部显示
  // SchedulePanel 让用户在 30 个 slot 之间挑选 + 在每个 slot 的推荐/次选间挑。
  // 这是「Strategy 内容合并到 Composer」的核心入口。
  const [searchParams] = useSearchParams();
  const packIdFromUrl = searchParams.get("pack");
  const [strategyPack, setStrategyPack] = useState<StrategyPackDTO | null>(null);
  useEffect(() => {
    if (!packIdFromUrl) return;
    api.getStrategy(packIdFromUrl).then((d: any) => {
      if (d.pack) setStrategyPack(d.pack);
    }).catch(() => { /* pack not found / no backend — silent fallback */ });
  }, [packIdFromUrl]);
  function setAngleModel(angle: string, spec: string) {
    setAngleModels(prev => {
      const next = { ...prev };
      if (!spec || spec === "auto") delete next[angle];
      else next[angle] = spec;
      return next;
    });
  }

  // v0.62 ：SchedulePanel 里选了哪个 slot/alt 就把那条的 metadata 直接灌进
  // 当前 Composer 表单。等于「Strategy 的内容」+「Composer 的 form」无缝衔接。
  function handleSlotChosen(slot: TopicSlotDTO, altIdx: number = -1) {
    const alts = Array.isArray((slot as any).alternative_versions) ? (slot as any).alternative_versions : [];
    const alt = (altIdx >= 0 && altIdx < alts.length) ? alts[altIdx] : null;
    const eff = alt ? {
      title: alt.title || slot.title,
      angle: alt.angle || slot.angle,
      hook_type: alt.hook_type || slot.hook_type,
      content_format: alt.content_format || slot.content_format,
      outline: Array.isArray(alt.mini_outline) ? alt.mini_outline : slot.outline,
      publish_slot: alt.publish_slot || slot.publish_slot,
    } : {
      title: slot.title,
      angle: slot.angle,
      hook_type: slot.hook_type,
      content_format: slot.content_format,
      outline: slot.outline,
      publish_slot: slot.publish_slot,
    };
    setTopic(eff.title || "");
    // angle must be in ANGLES enum to render properly
    if (eff.angle && ANGLES.includes(eff.angle)) {
      setAngles([eff.angle]);
    }
    if (strategyPack?.platform) setPlatform(strategyPack.platform);
    const niche = strategyPack?.chosen_direction?.positioning_statement || "";
    setNiche(niche);
    setExtra([
      alt ? `🎯 从 Strategy 「${alt.label || "次选"}」 进入 ：${alt.why_alt || ""}` : "🎯 从 Strategy 推荐方案进入",
      eff.content_format ? `内容形式 ：${eff.content_format}（必须按此格式写）` : "",
      eff.hook_type ? `hook_type: ${eff.hook_type}` : "",
      eff.publish_slot ? `发布时段 ：${eff.publish_slot}` : "",
      slot.intent ? `意图 ：${slot.intent}` : "",
      eff.outline?.length ? "大纲：" + (eff.outline as string[]).join(" / ") : "",
      slot.materials_needed?.length ? "需要材料：" + (slot.materials_needed as string[]).join("、") : "",
      strategyPack?.chosen_direction?.target_audience ? `目标受众：${strategyPack.chosen_direction.target_audience}` : "",
    ].filter(Boolean).join("\n\n"));
    setPrefillNote(`从 Strategy ${alt ? `「${alt.label || "次选"}」` : "推荐方案"} 一键带入 ：${eff.title?.slice(0, 30) || ""}`);
    // 滚到表单顶部
    setTimeout(() => {
      const el = document.getElementById("composer-step-1");
      if (el) el.scrollIntoView({behavior: "smooth", block: "start"});
    }, 50);
  }

  function toggleAngle(a: string) {
    setAngles(prev => {
      if (prev.includes(a)) {
        // Don't allow zero — at least one angle must be picked.
        return prev.length > 1 ? prev.filter(x => x !== a) : prev;
      }
      return [...prev, a];
    });
  }
  const [platforms, setPlatforms] = useState<Platform[]>([]);
  const [activeLib, setActiveLib] = useState<Library | null>(null);
  const [hasExternalReports, setHasExternalReports] = useState<boolean>(false);
  const [agentConfig, setAgentConfig] = useState<AgentSelection>(defaultSelection());
  const [showAgentConfig, setShowAgentConfig] = useState(false);

  const [bundle, setBundle] = useState<ComposeBundle | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [paused, setPaused] = useState(false);

  // Job tracker survives page navigation — switching to Reports/Strategy
  // mid-compose no longer drops the result.
  const COMPOSE_JOB_ID = "compose:current";
  const composeJob = useJob<ComposeBundle>(COMPOSE_JOB_ID);
  const running = composeJob?.status === "running";

  // Re-hydrate from the job store on mount (e.g. user navigated away then
  // back). If the job finished while we were elsewhere, surface its result.
  useEffect(() => {
    const j = getJob<ComposeBundle>(COMPOSE_JOB_ID);
    if (!j) return;
    if (j.status === "done" && j.result) setBundle(j.result);
    if (j.status === "failed" && j.error) setErr(j.error);
    if (j.status === "aborted") setPaused(true);
  }, []);

  useEffect(() => {
    api.platforms().then(setPlatforms).catch(() => {});
    api.libraries().then(ls => setActiveLib(ls.find(l => l.active) ?? null)).catch(() => {});
    Promise.all([api.listExternalReports(), api.listIntegratedReports()])
      .then(([ext, integ]) => setHasExternalReports(ext.length > 0 || integ.length > 0))
      .catch(() => {});
  }, []);

  // Persist form state on every change so navigation doesn't blow it away.
  useEffect(() => {
    try {
      localStorage.setItem(composerFormKey(), JSON.stringify({
        topic, angle: angles[0] || "教程", angles, length, cta, niche, extra, platform,
        angleModels,
      }));
    } catch { /* quota — ignore */ }
  }, [topic, angles, length, cta, niche, extra, platform, angleModels]);

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
      // prefill: support both `angle` (legacy single) and `angles` (multi).
      if (Array.isArray(bf.angles) && bf.angles.length > 0) {
        const cleaned = bf.angles.filter((a: any) => ANGLES.includes(String(a))).map(String);
        if (cleaned.length > 0) setAngles(cleaned);
      } else if (bf.angle && ANGLES.includes(String(bf.angle))) {
        setAngles([String(bf.angle)]);
      }
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
    setErr(null); setBundle(null); setPaused(false);
    const job = startJob<ComposeBundle>(
      COMPOSE_JOB_ID, "compose",
      (signal) => api.compose({
        topic,
        angle: angles[0] || "教程",   // back-compat singular fallback
        angles,                        // v0.52: drafter fans 1 per angle
        target_length: length, cta_strength: cta,
        niche, extra_constraints: extra,
        platform: platform || undefined,
        // v0.61.22 ：只发已选角度的非 auto 映射，省 payload。
        angle_models: Object.fromEntries(
          Object.entries(angleModels).filter(([a, s]) => angles.includes(a) && s && s !== "auto")
        ),
        ...selectionToSpecs(agentConfig),
      }, signal),
      { topic },
    );
    try {
      const res = await job.promise;
      setBundle(res);
    } catch (e: any) {
      if (isAborted(e)) { setPaused(true); }
      else { setErr(await humaniseErrorAsync(e)); }
    }
  }
  function pause() {
    cancelJob(COMPOSE_JOB_ID);
  }

  const noBackend = !api.isConnected();

  // v0.61.18 ：4 步 stepper（仿 Strategy）。Composer 跟 Strategy 不一样的是 ：
  // 跑完后所有结果都在同一页 — 所以这里的「跳步」实际是滚动到对应锚点 section。
  // 每步是否可点 = 该 section 是否已有数据。
  const hasStrategy = !!(bundle?.strategy && Object.keys(bundle.strategy).length > 0);
  const hasDrafts = Array.isArray(bundle?.drafts) && (bundle?.drafts?.length ?? 0) > 0;
  const hasFinal = !!(bundle?.final || bundle?.refined);
  // 当前活跃步骤 ：未跑 → 1（表单）；running → 2（起草中）；done → 4（最终稿）/3（候选）
  const composerStep = !bundle ? (running ? 2 : 1) : (hasFinal ? 4 : hasDrafts ? 3 : 2);
  function scrollTo(id: string) {
    const el = document.getElementById(id);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  function ComposerStepBtn({n, label, canGo, anchorId}: {
    n: number; label: string; canGo: boolean; anchorId: string;
  }) {
    const isCurrent = composerStep === n;
    const isDone = composerStep > n;
    const clickable = canGo || isDone || n === 1;
    return (
      <button type="button" onClick={() => { if (clickable) scrollTo(anchorId); }}
        disabled={!clickable}
        title={clickable ? `跳到第 ${n} 步` : `先完成前面 / 等当前跑完`}
        style={{
          flex: 1, padding: "8px 12px", fontSize: 13, fontWeight: 600,
          border: "1px solid " + (isCurrent ? "var(--primary)" : "var(--border)"),
          background: isCurrent ? "var(--primary)" : (isDone ? "var(--ok-soft)" : "#fff"),
          color: isCurrent ? "#fff" : (isDone ? "var(--ok)" : (clickable ? "var(--fg)" : "var(--muted)")),
          borderRadius: 6, cursor: clickable ? "pointer" : "not-allowed",
          opacity: clickable ? 1 : 0.55,
          transition: "background 0.15s, color 0.15s",
        }}>
        <span style={{
          display: "inline-flex", alignItems: "center", justifyContent: "center",
          width: 20, height: 20, borderRadius: "50%", marginRight: 6,
          background: isCurrent ? "rgba(255,255,255,0.25)" : (isDone ? "var(--ok)" : "#eee"),
          color: isCurrent ? "#fff" : (isDone ? "#fff" : "var(--muted)"),
          fontSize: 11,
        }}>{isDone ? "✓" : n}</span>
        {label}
      </button>
    );
  }

  return (
    <div>
      <div className="page-header">
        <h1>✍️ Composer · AI 起号助手</h1>
        <p>填主题 → 点开始 → 多个 AI 协作出最佳稿件 + 发布计划</p>
      </div>

      {/* v0.62.4 ：Strategy 板块整体迁移到 Composer。pack 存在时顶部依次显示 ：
          1) StrategyOverview — 方向/元信息/周主题/最佳时段/材料/风险/指标
          2) SchedulePanel — 30 篇排期 + 主推荐 + 2 备选 picker
          下面才是 4 步表单（主题→策略→候选→最终稿）+ IterateCard。 */}
      {strategyPack && (
        <>
          <StrategyOverview pack={strategyPack} />
          <SchedulePanel pack={strategyPack} onChoose={handleSlotChosen} />
        </>
      )}

      {/* v0.61.18 ：4 步跳转条 — 点击滚动到对应 section */}
      <div className="card" style={{padding: "10px 12px"}}>
        <div className="row" style={{gap: 6, alignItems: "stretch"}}>
          <ComposerStepBtn n={1} label="📝 主题" canGo={true} anchorId="composer-step-1" />
          <ComposerStepBtn n={2} label="📋 策略" canGo={hasStrategy} anchorId="composer-step-2" />
          <ComposerStepBtn n={3} label="📑 候选" canGo={hasDrafts} anchorId="composer-step-3" />
          <ComposerStepBtn n={4} label="★ 最终稿" canGo={hasFinal} anchorId="composer-step-4" />
        </div>
        <div className="muted" style={{fontSize: 11, marginTop: 6, textAlign: "center"}}>
          点上面任一步滚动到对应区域 · 跑完一次每个区域都会有内容
        </div>
      </div>

      {noBackend && (
        <div className="banner warn">
          ⚠️ 本地后端没起来。看顶部黄色 banner 复制命令启动。
        </div>
      )}
      {!noBackend && !activeLib && !hasExternalReports && (
        <div className="banner info">
          <b>还没有参考材料</b>。可以 ：(a) 去 <Link to="/reports">📊 分析报告</Link>
          上传 .db 让 AI 自动出共识，或 (b) 在该页底部上传你已有的外部分析报告（PDF/TXT/MD）。
          完全不传也能跑，但效果会差。
        </div>
      )}
      {prefillNote && (
        <div className="banner info" style={{background: "var(--primary-soft)", borderColor: "var(--primary)"}}>
          ✨ {prefillNote} · 你可以再微调一下下面字段，或者直接 ▶️ 开始
        </div>
      )}

      <div className="compose-grid">
        <div className="compose-form card" id="composer-step-1">
          <h2>1. 主题</h2>

          <div style={{marginBottom: 8}}>
            <label>这篇要写什么？</label>
            <input value={topic} onChange={e => setTopic(e.target.value)}
              placeholder="比如：降AI率技巧 / 论文怎么写引言 / 留学党赶ddl" />
          </div>
          <div style={{marginBottom: 10}}>
            <label>角度（多选 · 起草团会按你选的角度数量出对应数量的稿件）</label>
            <div style={{display: "flex", flexWrap: "wrap", gap: 6, marginTop: 4}}>
              {ANGLES.map(a => {
                const on = angles.includes(a);
                return (
                  <button
                    key={a}
                    type="button"
                    onClick={() => toggleAngle(a)}
                    style={{
                      padding: "4px 12px", borderRadius: 16, fontSize: 13,
                      border: on ? "1.5px solid var(--primary)" : "1px solid var(--border)",
                      background: on ? "var(--primary-soft)" : "#fff",
                      color: on ? "var(--primary)" : "#333",
                      cursor: "pointer", fontWeight: on ? 600 : 400,
                    }}
                  >
                    {on ? "✓ " : ""}{a}
                  </button>
                );
              })}
            </div>
            <div className="muted" style={{fontSize: 11, marginTop: 4}}>
              已选 {angles.length} 个角度 → 起草团会出 {angles.length} 份候选（每份一个角度）
            </div>
            {/* v0.61.22 ：每角度专属 model（可选 ：默认 auto = 3 家轮转） */}
            {angles.length > 0 && (
              <details style={{marginTop: 8}}>
                <summary style={{cursor: "pointer", fontSize: 11.5, color: "var(--muted)"}}>
                  ▾ 高级 ：钉死某个角度用哪家 LLM（默认 auto = 自动轮转 3 家）
                </summary>
                <div style={{
                  marginTop: 8, padding: "8px 10px",
                  background: "#fafafa", borderRadius: 6, display: "grid",
                  gridTemplateColumns: "1fr 1fr", gap: 6,
                }}>
                  {angles.map(a => (
                    <div key={a} className="row" style={{gap: 6, alignItems: "center"}}>
                      <span style={{
                        flex: "0 0 60px", fontSize: 12, fontWeight: 600,
                        color: "var(--primary)",
                      }}>{a}</span>
                      <select value={angleModels[a] || "auto"}
                        onChange={e => setAngleModel(a, e.target.value)}
                        style={{flex: 1, fontSize: 12, padding: "3px 4px"}}>
                        <option value="auto">🤖 auto · 自动轮转</option>
                        {LLM_CATALOG.map(o => (
                          <option key={o.id} value={o.id}>
                            {o.label} {o.cost === "high" ? "💸" : o.cost === "mid" ? "·" : "🪙"}
                          </option>
                        ))}
                      </select>
                    </div>
                  ))}
                </div>
                <div className="muted" style={{fontSize: 10.5, marginTop: 6}}>
                  钉死的角度直接用对应 LLM 写；其它角度仍按 drafter_spec 默认轮转。
                </div>
              </details>
            )}
          </div>
          <div style={{marginBottom: 8}}>
            <label>正文字数</label>
            <input type="number" min={120} max={3000} step={50}
              value={length} onChange={e => setLength(Number(e.target.value))} />
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

          <div className="row" style={{gap: 8, marginTop: 16}}>
            <button onClick={run} disabled={running || !topic.trim() || noBackend}
              style={{flex: 1, fontSize: 15, padding: "10px 0"}}>
              {running ? "🤖 AI 们正在协作出稿中…(60-180s)" : (paused ? "🚀 重新启动" : "🚀 启动 AI 团队")}
            </button>
            {running && (
              <button className="ghost" onClick={pause}
                style={{padding: "10px 16px", fontSize: 14}}>⏸ 暂停</button>
            )}
          </div>
          {paused && !running && (
            <div className="banner info" style={{marginTop: 8}}>
              ⏸ 已暂停。后端可能还在跑（无害），点上面「🚀 重新启动」会从头开始。
            </div>
          )}
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
              <span style={{fontSize: 12}}>结果会在这里展示 ：agent 时间线 + N 份候选 + 评审分数 + 自动选最高分作 final（你可以一键改选）+ 发布执行计划</span>
            </div>
          )}
          {bundle && <ComposeResult bundle={bundle} />}
        </div>
      </div>

      {/* v0.62.4 ：IterateCard 从 Strategy 搬来 — 用户写完整轮 30 篇后可以
          在这里填表现数据 → AI 出下一轮 pack（也直接进 Composer）。 */}
      {strategyPack && <IterateCard pack={strategyPack} />}
    </div>
  );
}

// v0.61.27 ：chosen 候选选择本地化（不再 PATCH 后端，避免多用户互覆盖）。
// 键 ：studio.composer.chosen.<pid>.<draft_id>。每个用户 / 每个浏览器独立。
function chosenLocalKey(draftId: string): string {
  let pid = "default";
  try { pid = localStorage.getItem("studio.activeProjectId") || "default"; } catch { /* ignore */ }
  return `studio.composer.chosen.${pid}.${draftId}`;
}
function readChosenLocal(draftId: string): string | null {
  try { return localStorage.getItem(chosenLocalKey(draftId)); } catch { return null; }
}
function writeChosenLocal(draftId: string, cid: string | null): void {
  try {
    if (cid) localStorage.setItem(chosenLocalKey(draftId), cid);
    else localStorage.removeItem(chosenLocalKey(draftId));
  } catch { /* quota */ }
}

function ComposeResult({bundle}: {bundle: ComposeBundle}) {
  // v0.61.19 → v0.61.27 ：chosen 现在**纯本地** ：
  // 1) initial 优先读 localStorage（本浏览器之前选过的）
  // 2) 否则 fallback bundle.final / refined（_pick_best 自动挑的 top critic）
  // 3) 切换时只写 localStorage，不再 PATCH 后端 → 多人协作不互覆盖。
  const [chosenId, setChosenId] = useState<string | null>(() => {
    const local = readChosenLocal(bundle.draft_id);
    return local
      ?? (bundle.final as any)?.candidate_id
      ?? (bundle.refined as any)?.candidate_id
      ?? null;
  });
  function chooseDraft(cid: string) {
    if (cid === chosenId) return;
    setChosenId(cid);
    writeChosenLocal(bundle.draft_id, cid);
  }
  // 当前 final 候选对象 ：找 chosenId 对应的那条 draft
  const chosenDraft = bundle.drafts.find((d: any) => d.candidate_id === chosenId)
    ?? bundle.final ?? bundle.refined ?? null;
  return (
    <>
      <div className="card">
        <div className="spread">
          <div>
            <strong>本次出稿 #{bundle.draft_id.slice(0, 8)}</strong>
            <p className="muted">
              耗时 {bundle.totals.elapsed_s}s · 成本 ≈ ${bundle.totals.cost_usd.toFixed(4)} · {bundle.drafts.length} 份候选 · 你的 final 选择只存本地浏览器（队友看到的可能是 critic 自动挑的）
            </p>
          </div>
          <Link to={`/drafts/${bundle.draft_id}`}><button className="secondary">详情</button></Link>
        </div>
      </div>

      {bundle.strategy && Object.keys(bundle.strategy).length > 0 && (
        <div className="card" id="composer-step-2">
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

      {Array.isArray(bundle.trace) && bundle.trace.length > 0 && (
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
      )}

      {/* v0.61.25 ：refs 概览卡保留（这里只列标题）— 详细内容在下面 final 稿
          section 里折叠展开。 */}
      {bundle.rag && Array.isArray(bundle.rag.refs) && bundle.rag.refs.length > 0 && (
        <div className="card">
          <h2>📚 参考爆款 ({bundle.rag.refs.length})</h2>
          <ol>
            {bundle.rag.refs.slice(0, 5).map((r: any) => (
              <li key={r.note_id}>
                [{fmtLikes(r.liked_count ?? r.likes)} likes] {r.title}
              </li>
            ))}
          </ol>
          <p className="muted" style={{fontSize: 12}}>
            + {(bundle.rag as any).comments_count ?? 0} 条用户原话评论 + {(bundle.rag.hooks?.length ?? 0)} 个 hook 模板
          </p>
        </div>
      )}

      {Array.isArray(bundle.drafts) && bundle.drafts.length > 0 && (
        <div className="card" id="composer-step-3">
          <h2>📝 {bundle.drafts.length} 份候选 (起草团 + 审稿团)</h2>
          <p className="muted" style={{fontSize: 12, marginTop: -4, marginBottom: 8}}>
            默认 AI 自动选 critic 分最高的那条作为 final（★）— 不满意**点别的卡片就能换**（整张卡可点）。
          </p>
          <div className="candidate-grid">
            {bundle.drafts.map(c => (
              <Candidate key={c.candidate_id} c={c}
                chosen={c.candidate_id === chosenId}
                onChoose={() => chooseDraft(c.candidate_id)} />
            ))}
          </div>
        </div>
      )}

      {bundle.refined && (
        <div className="card">
          <h2>✏️ Refiner 润色版（基于候选的进一步打磨）</h2>
          <div className="candidate-grid">
            <Candidate c={bundle.refined}
              chosen={(bundle.refined as any).candidate_id === chosenId}
              onChoose={() => chooseDraft((bundle.refined as any).candidate_id)} />
          </div>
        </div>
      )}

      {/* v0.61.19 ：「★ 最终稿」section 现在显示用户选的那一条（默认 = critic
          最高分）。点上面任一候选的 「★ 选这条」可换。
          v0.61.21 ：加复制按钮（标题 / 正文 / 全文 三种）— 直接拿去发不用手抄。 */}
      {chosenDraft && (
        <div className="card" id="composer-step-4" style={{borderTop: "3px solid var(--primary)"}}>
          <div className="row" style={{justifyContent: "space-between", alignItems: "flex-start", gap: 12, marginBottom: 6}}>
            <div style={{flex: 1, minWidth: 0}}>
              <h2 style={{margin: 0}}>★ 当前 final 稿 · 这条会进 Drafts 历史</h2>
              <p className="muted" style={{fontSize: 12, margin: "4px 0 0"}}>
                想换 final ？回到 「📑 候选」section 点别条的卡片。
              </p>
            </div>
            <CopyButtons cand={chosenDraft as any} />
          </div>
          <div className="candidate-grid">
            <Candidate c={chosenDraft as any} highlighted />
          </div>
          {/* v0.61.25 ：AI 写这条稿时具体参考了哪些素材 — 让用户能验证 AI 没瞎编 */}
          {bundle.rag && (
            <ReferenceSourcesPanel rag={bundle.rag as any} />
          )}
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

// v0.62 ：SchedulePanel — Strategy 的 schedule 合并到 Composer 里渲染。
// 用户从 Strategy 「→ 去出稿」 进来时，URL 带 ?pack=ID，Composer 加载 pack，
// 顶部用这个面板显示 30 个 slot ：每条紧凑一行（日期 + 标题 + 角度 + 格式），
// 展开后看 outline + materials + 2 个 alternative_versions（每个独立 「✍️ 写这个」）。
// 点哪个 → handleSlotChosen 把 metadata 灌进下面的 Composer 表单。
function SchedulePanel({pack, onChoose}: {
  pack: StrategyPackDTO;
  onChoose: (slot: TopicSlotDTO, altIdx: number) => void;
}) {
  const [expanded, setExpanded] = useState<number | null>(null);
  const [collapsed, setCollapsed] = useState(false);
  const schedule = Array.isArray(pack.schedule) ? pack.schedule : [];
  const dirName = (pack.chosen_direction && pack.chosen_direction.name) || "";
  const cycleStart = pack.input.cycle_start_date || "";
  return (
    <div className="card" style={{borderLeft: "4px solid #a855f7", padding: "12px 14px"}}>
      <div className="spread" style={{alignItems: "flex-start"}}>
        <div style={{flex: 1, minWidth: 0}}>
          <h2 style={{margin: 0, fontSize: 15}}>
            📅 当前起号策略 schedule · {schedule.length} 篇
          </h2>
          <p className="muted" style={{fontSize: 12, margin: "4px 0 0"}}>
            来自 Strategy 包 #{pack.pack_id.slice(0, 8)} · 方向「{dirName}」
            · 点任一条选 「主推荐」 或 「次选」 → 自动填进下面的表单 → ▶️ 开始 → 多 agent 出稿
          </p>
        </div>
        <button className="ghost" onClick={() => setCollapsed(v => !v)}
          style={{fontSize: 12}}>
          {collapsed ? "展开 schedule →" : "收起 ▴"}
        </button>
      </div>
      {!collapsed && (
        <div style={{marginTop: 10, display: "grid", gap: 4, maxHeight: "60vh", overflow: "auto"}}>
          {schedule.length === 0 && (
            <div className="muted" style={{fontSize: 13, padding: 8}}>
              这份 pack 的 schedule 为空，可能上一次 expand 出错。回 Strategy 重跑。
            </div>
          )}
          {schedule.map((s: any, i: number) => {
            const isExp = expanded === i;
            const dt = slotDate(cycleStart, s.week, s.day_of_week);
            const dateLabel = dt ? dt.display : `W${s.week}·D${s.day_of_week}`;
            const alts = Array.isArray(s.alternative_versions) ? s.alternative_versions : [];
            return (
              <div key={i} style={{
                border: isExp ? "1px solid var(--primary)" : "1px solid #eee",
                borderRadius: 6,
                background: isExp ? "var(--primary-soft)" : "#fff",
              }}>
                <div className="row" style={{
                  padding: "6px 10px", gap: 8, alignItems: "center", cursor: "pointer",
                }} onClick={() => setExpanded(isExp ? null : i)}>
                  <span style={{
                    fontSize: 11, padding: "1px 6px", background: "var(--primary)",
                    color: "#fff", borderRadius: 4, fontWeight: 600, flexShrink: 0,
                  }}>#{i + 1}</span>
                  <span className="muted" style={{fontSize: 11, flexShrink: 0, minWidth: 80}}>
                    📅 {dateLabel}
                  </span>
                  {s.publish_slot && (
                    <span className="muted" style={{fontSize: 11, flexShrink: 0}}>⏰ {s.publish_slot}</span>
                  )}
                  <span style={{
                    flex: 1, minWidth: 0, fontSize: 12.5, fontWeight: 600,
                    overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                  }}>{s.title || "(无标题)"}</span>
                  {s.angle && <span className="tag-pill" style={{fontSize: 10.5, flexShrink: 0}}>{s.angle}</span>}
                  {s.content_format && (
                    <span className="tag-pill" style={{fontSize: 10.5, flexShrink: 0}}>
                      {s.content_format}
                    </span>
                  )}
                  {alts.length > 0 && (
                    <span className="muted" style={{fontSize: 10.5, flexShrink: 0}}>
                      + {alts.length} 备选
                    </span>
                  )}
                  <span style={{fontSize: 11, color: "var(--muted)", flexShrink: 0}}>{isExp ? "▴" : "▾"}</span>
                </div>
                {isExp && (
                  <div style={{padding: "0 10px 10px"}}>
                    {/* Main option */}
                    <div style={{padding: 8, background: "#fff", borderRadius: 4, marginTop: 4,
                                  border: "1px solid #ffd0d8"}}>
                      <div className="row" style={{justifyContent: "space-between", alignItems: "flex-start", gap: 8}}>
                        <div style={{flex: 1, minWidth: 0}}>
                          <span style={{
                            fontSize: 10.5, padding: "1px 6px", background: "var(--primary)",
                            color: "#fff", borderRadius: 4, fontWeight: 600,
                          }}>★ 主推荐</span>
                          {s.outline?.length > 0 && (
                            <ul style={{margin: "6px 0 0 18px", fontSize: 11.5, lineHeight: 1.55, color: "#555"}}>
                              {s.outline.slice(0, 5).map((o: string, j: number) => <li key={j}>{o}</li>)}
                            </ul>
                          )}
                          {s.materials_needed?.length > 0 && (
                            <div className="muted" style={{fontSize: 11, marginTop: 4}}>
                              📦 材料 ：{s.materials_needed.join("、")}
                            </div>
                          )}
                          {s.decision_rationale && (
                            <div className="muted" style={{fontSize: 11, marginTop: 4, fontStyle: "italic"}}>
                              🧠 {s.decision_rationale}
                            </div>
                          )}
                          {s.publish_rationale && (
                            <div className="muted" style={{fontSize: 11, marginTop: 2, fontStyle: "italic"}}>
                              ⏰ {s.publish_rationale}
                            </div>
                          )}
                          {s.flexible_window && (
                            <div className="muted" style={{fontSize: 11, marginTop: 2, fontStyle: "italic"}}>
                              🗓️ 推荐窗口 ：{s.flexible_window}
                            </div>
                          )}
                        </div>
                        <button onClick={(e) => { e.stopPropagation(); onChoose(s, -1); }}
                          style={{whiteSpace: "nowrap", fontSize: 12, padding: "4px 10px"}}>
                          ✍️ 写这个 →
                        </button>
                      </div>
                    </div>
                    {/* Alternative options */}
                    {alts.map((alt: any, ai: number) => (
                      <div key={ai} style={{
                        padding: 8, background: "#fff", borderRadius: 4, marginTop: 6,
                        borderLeft: "3px solid #a855f7", border: "1px solid #eadcff",
                      }}>
                        <div className="row" style={{justifyContent: "space-between", alignItems: "flex-start", gap: 8}}>
                          <div style={{flex: 1, minWidth: 0}}>
                            <div className="row" style={{gap: 6, flexWrap: "wrap"}}>
                              <span style={{
                                fontSize: 10.5, padding: "1px 6px", background: "#a855f7",
                                color: "#fff", borderRadius: 4, fontWeight: 600,
                              }}>{alt.label || `次选 ${ai === 0 ? "A" : "B"}`}</span>
                              {alt.publish_slot && <span className="tag-pill" style={{fontSize: 10.5}}>⏰ {alt.publish_slot}</span>}
                              {alt.angle && <span className="tag-pill" style={{fontSize: 10.5}}>{alt.angle}</span>}
                              {alt.content_format && <span className="tag-pill" style={{fontSize: 10.5}}>{alt.content_format}</span>}
                            </div>
                            {alt.title && (
                              <div style={{fontSize: 12.5, fontWeight: 600, marginTop: 4}}>{alt.title}</div>
                            )}
                            {Array.isArray(alt.mini_outline) && alt.mini_outline.length > 0 && (
                              <ul style={{margin: "4px 0 0 18px", fontSize: 11.5, lineHeight: 1.55, color: "#555"}}>
                                {alt.mini_outline.map((o: string, j: number) => <li key={j}>{o}</li>)}
                              </ul>
                            )}
                            {alt.why_alt && (
                              <div className="muted" style={{fontSize: 11, marginTop: 4, fontStyle: "italic"}}>
                                💡 {alt.why_alt}
                              </div>
                            )}
                          </div>
                          <button onClick={(e) => { e.stopPropagation(); onChoose(s, ai); }}
                            className="ghost"
                            style={{
                              whiteSpace: "nowrap", fontSize: 12, padding: "4px 10px",
                              borderColor: "#a855f7", color: "#a855f7",
                            }}>
                            ✍️ 写这个 →
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}


// v0.62.4 ：StrategyOverview — 从 Strategy.tsx PackView 整体搬过来。
// 在 Composer 顶部（SchedulePanel 上面）渲染 ：方向卡 + 元信息行（冷热
// 启动 / 内容形式偏好 / 周期 / 频率 / 平台）+ 周主题 + 本号最佳时段 +
// 材料 / 风险 / 指标。Strategy 不再渲染这些 — 全部集中到这里。
function StrategyOverview({pack}: {pack: StrategyPackDTO}) {
  function toArr(x: any): string[] {
    if (Array.isArray(x)) return x.map(String);
    if (typeof x === "string") {
      const s = x.trim();
      if (s.startsWith("[") && s.endsWith("]")) {
        try { const j = JSON.parse(s); if (Array.isArray(j)) return j.map(String); } catch { /* fall through */ }
      }
      return s.split("\n").map(l => l.trim()).filter(Boolean);
    }
    return [];
  }
  const materials = toArr(pack.materials_checklist);
  const risks = toArr(pack.risks_and_mitigations);
  const metrics = toArr(pack.success_metrics);
  const themes = Array.isArray(pack.weekly_themes) ? pack.weekly_themes : [];
  return (
    <>
      <div className="card">
        {(pack.chosen_directions && pack.chosen_directions.length > 1) ? (
          <>
            <h2 style={{marginTop: 0}}>方向 · {pack.chosen_directions.length} 个主题混排</h2>
            <p className="muted" style={{fontSize: 12, margin: "4px 0 10px"}}>
              多方向起号 — {pack.schedule.length} 篇 slot 跨这 {pack.chosen_directions.length} 个方向混排，
              每周保留拉新/专业感/沉淀/转化 4 阶段意图。
            </p>
            <div style={{display: "grid", gap: 8}}>
              {pack.chosen_directions.map((d, i) => (
                <div key={i} style={{
                  padding: "8px 12px", background: "#fafafa", borderRadius: 6,
                  borderLeft: `3px solid ${DIRECTION_COLORS[i % DIRECTION_COLORS.length]}`,
                  fontSize: 13,
                }}>
                  <span style={{
                    display: "inline-block", marginRight: 8, fontSize: 11,
                    padding: "1px 6px", borderRadius: 3,
                    background: DIRECTION_COLORS[i % DIRECTION_COLORS.length], color: "#fff",
                    fontWeight: 600,
                  }}>方向 #{i + 1}</span>
                  <b>{d.name}</b>
                  <div className="muted" style={{fontSize: 12, marginTop: 2}}>
                    {d.positioning_statement} · 受众：{d.target_audience}
                  </div>
                </div>
              ))}
            </div>
          </>
        ) : (
          <>
            <h2 style={{marginTop: 0}}>方向 · {pack.chosen_direction.name}</h2>
            <p style={{margin: "4px 0", fontSize: 14}}>{pack.chosen_direction.positioning_statement}</p>
            <p className="muted" style={{fontSize: 12}}>受众：{pack.chosen_direction.target_audience}</p>
          </>
        )}
        {pack.series_thesis && (
          <p style={{fontStyle: "italic", color: "var(--muted)", fontSize: 13, marginTop: 8}}>
            主线：{pack.series_thesis}
          </p>
        )}
        {/* 策略元信息行 — 冷热启动 / 内容形式偏好 / 周期 / 频率 / 平台 */}
        <div className="row" style={{
          gap: 6, flexWrap: "wrap", marginTop: 10,
          paddingTop: 8, borderTop: "1px dashed #eee",
        }}>
          {(() => {
            const sp = pack.input.startup_phase || "";
            const phaseMap: Record<string, {label: string; hint: string}> = {
              "":       { label: "🤖 AI 自决节奏",       hint: "AI 据 DNA / 报告自己挑节奏" },
              "cold":   { label: "🆕 冷启动",            hint: "0 粉 · 主营造人设痛点 · 后期才转化" },
              "warm":   { label: "🔥 热启动",            hint: "已有粉丝/资源 · 早期就可强转化" },
              "hybrid": { label: "🌗 混合启动",          hint: "前期人设 + 后期转化的渐进节奏" },
            };
            const fp = pack.input.content_format_preference || "";
            const formatMap: Record<string, string> = {
              "":            "🤖 内容形式 AI 自决",
              "tuwen_only":  "📝 纯图文",
              "video_only":  "🎬 纯短视频",
              "mixed":       "🔀 图文+视频混合",
            };
            const ph = phaseMap[sp] || phaseMap[""];
            return (
              <>
                <span className="tag-pill" title={ph.hint}
                  style={{background: "#fff3e6", color: "#b34d00", fontWeight: 600}}>
                  {ph.label}
                </span>
                <span className="tag-pill" style={{background: "#eef6ff", color: "#1e40af"}}>
                  {formatMap[fp] || formatMap[""]}
                </span>
                <span className="tag-pill" style={{background: "#f4f4f4"}}>
                  📅 {pack.input.cycle_weeks} 周
                </span>
                <span className="tag-pill" style={{background: "#f4f4f4"}}>
                  📊 每周 {pack.input.posts_per_week} 篇
                </span>
                {pack.input.cycle_start_date && (
                  <span className="tag-pill" style={{background: "#f4f4f4"}}>
                    🗓️ 起 {pack.input.cycle_start_date}
                  </span>
                )}
                <PlatformPill platform={pack.platform} />
              </>
            );
          })()}
        </div>
      </div>

      {themes.length > 0 && (
        <div className="card">
          <h2 style={{marginTop: 0}}>📅 周主题</h2>
          <div className="cards-grid">
            {themes.map((w, i) => (
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

      <TopPublishingSlotsCard />

      {materials.length > 0 && (
        <div className="card">
          <h2 style={{marginTop: 0}}>🎒 启动前要准备的材料</h2>
          <ul style={{marginLeft: 20, lineHeight: 1.9}}>
            {materials.map((m, i) => <li key={i}>{m}</li>)}
          </ul>
        </div>
      )}

      {risks.length > 0 && (
        <div className="card">
          <h2 style={{marginTop: 0}}>⚠️ 风险 + 应对</h2>
          <ol style={{marginLeft: 20, lineHeight: 1.9}}>
            {risks.map((r, i) => <li key={i}>{r}</li>)}
          </ol>
        </div>
      )}

      {metrics.length > 0 && (
        <div className="card">
          <h2 style={{marginTop: 0}}>📈 成功指标</h2>
          <ul style={{marginLeft: 20, lineHeight: 1.9}}>
            {metrics.map((m, i) => <li key={i}>{m}</li>)}
          </ul>
        </div>
      )}
    </>
  );
}

// 「本账号最佳发布时段 Top 5」总览卡 — 从 Strategy.tsx 整体搬过来。
function TopPublishingSlotsCard() {
  const [top, setTop] = useState<Array<{label: string; median_likes: number; count: number}>>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancel = false;
    (async () => {
      try {
        const dna: any = await api.dnaLatest();
        if (cancel) return;
        const heatmap = (dna?.sections?.timing?.heatmap || []) as any[];
        setTop(topPublishingSlots(heatmap, 5, 5));
      } catch (e: any) {
        if (!cancel) setErr(e.message || String(e));
      }
    })();
    return () => { cancel = true; };
  }, []);

  if (err || top.length === 0) return null;  // 静默失败 — 没 DNA 就不显示
  return (
    <div className="card" style={{
      background: "linear-gradient(180deg, #fff8e6 0%, #fff 100%)",
      borderColor: "#fde2a3",
    }}>
      <h2 style={{marginTop: 0}}>📊 本账号最佳发布时段 Top 5</h2>
      <p className="muted" style={{fontSize: 12, marginTop: 2, marginBottom: 12}}>
        从你激活的语料库的 DNA 热力图里挑出来 — 这 5 个 (周几, 小时) 格子的中位点赞最高。
        AI 排期会优先把内容塞进这些时段，但也会按「内容类型 vs 时段」做差异化。
      </p>
      <div style={{display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))", gap: 8}}>
        {top.map((cell, i) => (
          <div key={i} style={{
            padding: 10, background: "#fff", borderRadius: 8,
            border: "1px solid #f0d8a0", textAlign: "center",
          }}>
            <div style={{fontSize: 11, color: "#a67700", fontWeight: 600}}>
              #{i + 1}
            </div>
            <div style={{fontSize: 14, fontWeight: 700, marginTop: 4}}>
              {cell.label}
            </div>
            <div className="muted" style={{fontSize: 11, marginTop: 4}}>
              中位赞 <b style={{color: "#333"}}>{Math.round(cell.median_likes)}</b>
            </div>
            <div className="muted" style={{fontSize: 10}}>
              （n={cell.count}）
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// IterateCard — 从 Strategy.tsx 搬过来。让用户在 Composer 写完这一轮
// 30 篇后填表现数据 → AI 出下一轮 pack（也直接进 Composer）。
function IterateCard({pack}: {pack: StrategyPackDTO}) {
  const navigate = useNavigate();
  const [showForm, setShowForm] = useState(false);
  const [rawNotes, setRawNotes] = useState("");
  const [perSlot, setPerSlot] = useState<{[idx: number]: {likes?: string; comments?: string; saves?: string}}>({});
  const [busy, setBusy] = useState(false);
  const [iterating, setIterating] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [history, setHistory] = useState<any[]>([]);

  useEffect(() => {
    if (!pack?.pack_id) return;
    api.listStrategyPerformance(pack.pack_id).then(setHistory).catch(() => {});
  }, [pack?.pack_id]);

  async function submit() {
    setBusy(true); setErr(null); setInfo(null);
    try {
      const per_slot = Object.entries(perSlot)
        .filter(([, v]) => v && (v.likes || v.comments || v.saves))
        .map(([idx, v]) => ({
          slot_idx: Number(idx),
          likes: v.likes ? Number(v.likes) : undefined,
          comments: v.comments ? Number(v.comments) : undefined,
          saves: v.saves ? Number(v.saves) : undefined,
        }));
      const r = await api.saveStrategyPerformance(pack.pack_id, {
        raw_notes: rawNotes, per_slot, overall: {},
      });
      setInfo(`✓ 数据已保存（${per_slot.length} 篇有数 / ${rawNotes ? "含" : "无"}文字复盘）`);
      setHistory(prev => [r, ...prev]);
      setIterating(true);
      const out = await api.iterateStrategy(pack.pack_id, {
        feedback_id: r.feedback_id, iterator_spec: "openai",
      });
      setInfo(`✓ 下一轮策略已生成（迭代 #${out.iteration_n}）。即将跳转…`);
      setTimeout(() => navigate(`/composer?pack=${out.pack_id}`), 800);
    } catch (e: any) {
      setErr(humaniseError(e));
      setIterating(false);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card" style={{borderTop: "3px solid var(--primary)"}}>
      <div className="spread" style={{alignItems: "flex-start"}}>
        <div>
          <h2 style={{margin: 0}}>🔄 跑完这一轮？让 AI 看效果 + 出下一轮</h2>
          <p className="muted" style={{fontSize: 12, margin: "4px 0 0"}}>
            发完这 {pack.schedule.length} 篇后回来填真实表现 → AI 会分析哪些 hook / 角度真的爆了，下一轮加大投入、砍掉翻车点。
          </p>
        </div>
        {!showForm && (
          <button onClick={() => setShowForm(true)}>📊 我跑完了 / 上传表现</button>
        )}
      </div>

      {history.length > 0 && (
        <div className="muted" style={{fontSize: 12, marginTop: 6}}>
          上次反馈 ：{new Date(history[0].created_at * 1000).toLocaleString()} ·
          逐篇 {history[0].per_slot?.length ?? 0} 篇有数
        </div>
      )}

      {showForm && (
        <div style={{marginTop: 14, padding: 12, background: "#fafafa", borderRadius: 8}}>
          <label style={{marginBottom: 4}}>📝 文字复盘（什么爆了 / 什么翻了 / 评论里看到什么 — 越具体越好）</label>
          <textarea value={rawNotes} onChange={e => setRawNotes(e.target.value)}
            placeholder="比如：第 2 篇 hook '4小时跑通'爆了, 2800 赞；第 5 篇标题太长没人点；评论里反复问'文科版的prompt模板'，下一轮要专门做。"
            style={{minHeight: 100, width: "100%", fontFamily: "inherit", fontSize: 13, lineHeight: 1.7,
                    marginBottom: 12}} />

          <label style={{marginBottom: 4}}>📊 逐篇数据（可只填几篇代表性的，不需要全填）</label>
          <div style={{display: "grid", gap: 6, fontSize: 12}}>
            <div style={{display: "grid", gridTemplateColumns: "1fr 90px 90px 90px",
                         gap: 6, fontWeight: 600, color: "#555", padding: "2px 4px"}}>
              <div>标题</div>
              <div className="num">👍 点赞</div>
              <div className="num">💬 评论</div>
              <div className="num">⭐ 收藏</div>
            </div>
            {pack.schedule.slice(0, 30).map((s, i) => {
              const v = perSlot[i] || {};
              const set = (k: "likes"|"comments"|"saves", val: string) =>
                setPerSlot(prev => ({...prev, [i]: {...prev[i], [k]: val}}));
              return (
                <div key={i} style={{display: "grid", gridTemplateColumns: "1fr 90px 90px 90px",
                                      gap: 6, alignItems: "center", padding: "2px 4px"}}>
                  <div className="muted" style={{fontSize: 11.5, whiteSpace: "nowrap",
                                                  overflow: "hidden", textOverflow: "ellipsis"}}>
                    W{s.week}·#{i+1} {s.title}
                  </div>
                  <input type="number" min="0" value={v.likes ?? ""}
                    onChange={e => set("likes", e.target.value)}
                    style={{padding: "3px 6px", fontSize: 12}} />
                  <input type="number" min="0" value={v.comments ?? ""}
                    onChange={e => set("comments", e.target.value)}
                    style={{padding: "3px 6px", fontSize: 12}} />
                  <input type="number" min="0" value={v.saves ?? ""}
                    onChange={e => set("saves", e.target.value)}
                    style={{padding: "3px 6px", fontSize: 12}} />
                </div>
              );
            })}
          </div>

          {err && <div className="banner danger" style={{marginTop: 10}}>{err}</div>}
          {info && <div className="banner info" style={{marginTop: 10}}>{info}</div>}

          <div className="row" style={{gap: 8, marginTop: 12}}>
            <button onClick={submit} disabled={busy || (!rawNotes.trim() && Object.keys(perSlot).length === 0)}>
              {iterating ? "🤖 Claude 在分析上轮 + 出下轮策略（60-90s）…"
              : busy ? "上传中…"
              : "🚀 保存表现 + 一键出下一轮策略"}
            </button>
            <button className="ghost" onClick={() => setShowForm(false)} disabled={busy}>关闭</button>
          </div>
        </div>
      )}
    </div>
  );
}


// v0.61.25 ：参考素材面板 — 让用户看清 AI 写这条稿时具体参考了哪些 ：
// 1) 同赛道高赞标题 + 正文片段（refs）— 验证 AI 没编内容
// 2) 高赞评论原话（comments）— 验证情绪 / 语气来源
// 3) hook 模板（hooks）— 看 AI 学了哪些标题套路
// 默认收起避免视觉拥挤。
function ReferenceSourcesPanel({rag}: {rag: any}) {
  const refs = Array.isArray(rag?.refs) ? rag.refs : [];
  const comments = Array.isArray(rag?.comments) ? rag.comments : [];
  const hooks = Array.isArray(rag?.hooks) ? rag.hooks : [];
  if (refs.length === 0 && comments.length === 0 && hooks.length === 0) return null;
  return (
    <details style={{marginTop: 14, padding: "10px 12px", background: "#fafafa", borderRadius: 8}}>
      <summary style={{cursor: "pointer", fontWeight: 600, fontSize: 13}}>
        📚 看 AI 写这条稿时参考了什么 ：{refs.length} 篇爆款 + {comments.length} 条评论原话 + {hooks.length} 个 hook 模板
      </summary>
      {refs.length > 0 && (
        <div style={{marginTop: 10}}>
          <div style={{fontWeight: 600, fontSize: 12.5, color: "var(--primary)", marginBottom: 6}}>
            ▸ 同赛道高赞参考稿（refs）
          </div>
          <div style={{display: "grid", gap: 8}}>
            {refs.slice(0, 8).map((r: any, i: number) => (
              <div key={r.note_id || i} style={{
                padding: 10, background: "#fff", borderRadius: 6,
                border: "1px solid #eee",
              }}>
                <div className="row" style={{justifyContent: "space-between", alignItems: "baseline", gap: 8}}>
                  <div style={{fontWeight: 600, fontSize: 13, flex: 1, minWidth: 0}}>
                    {r.title || "(无标题)"}
                  </div>
                  <div className="muted" style={{fontSize: 11, whiteSpace: "nowrap"}}>
                    {fmtLikes(r.liked_count ?? r.likes)} likes · {fmtLikes(r.collected_count)} 收藏 · {fmtLikes(r.comment_count)} 评论
                  </div>
                </div>
                {r.body_excerpt && (
                  <div className="muted" style={{
                    fontSize: 12, lineHeight: 1.6, marginTop: 6,
                    background: "#fafafa", padding: "6px 8px", borderRadius: 4,
                    whiteSpace: "pre-wrap",
                  }}>
                    {r.body_excerpt}
                  </div>
                )}
                {r.url && (
                  <a href={r.url} target="_blank" rel="noreferrer"
                    style={{fontSize: 11, color: "var(--primary)", marginTop: 4, display: "inline-block"}}>
                    🔗 原文
                  </a>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
      {comments.length > 0 && (
        <div style={{marginTop: 14}}>
          <div style={{fontWeight: 600, fontSize: 12.5, color: "var(--primary)", marginBottom: 6}}>
            ▸ 目标用户原话（高赞评论） · 用于 voice / 情绪学习
          </div>
          <ul style={{margin: 0, paddingLeft: 18, fontSize: 12, lineHeight: 1.7, color: "#555"}}>
            {comments.slice(0, 12).map((c: any, i: number) => (
              <li key={c.comment_id || i}>
                <span className="muted" style={{fontSize: 11}}>({c.like_count ?? 0}👍)</span>{" "}
                {String(c.content ?? "").slice(0, 200)}
              </li>
            ))}
          </ul>
        </div>
      )}
      {hooks.length > 0 && (
        <div style={{marginTop: 14}}>
          <div style={{fontWeight: 600, fontSize: 12.5, color: "var(--primary)", marginBottom: 6}}>
            ▸ hook 模板 · AI 学了哪些标题套路
          </div>
          <div style={{display: "grid", gap: 4, fontSize: 12, color: "#555"}}>
            {hooks.slice(0, 8).map((h: any, i: number) => (
              <div key={i}>
                <b>{h.category}</b>
                <span className="muted" style={{marginLeft: 6, fontSize: 11}}>
                  n={h.count} · 中位 {fmtLikes(h.median_likes)} likes
                </span>
                {Array.isArray(h.examples) && h.examples.length > 0 && (
                  <div className="muted" style={{paddingLeft: 14, fontSize: 11.5, marginTop: 2}}>
                    例 ：{h.examples.slice(0, 3).map((e: any) => e.title).join(" | ")}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </details>
  );
}

// v0.61.21 ：复制按钮组 — 标题 / 正文 / 全文 三个。最常用的是「全文」=
// 标题 + 正文 + tags 拼成一份可直接粘到小红书的格式。
function CopyButtons({cand}: {cand: any}) {
  const [done, setDone] = useState<string | null>(null);  // 哪个按钮刚被点
  function copy(kind: "title" | "body" | "all") {
    const p = cand?.payload ?? {};
    const title = String(p.title ?? "");
    const body = String(p.body ?? "");
    const tags = Array.isArray(p.tags) ? p.tags.map((t: string) => `#${t}`).join(" ") : "";
    let text = "";
    if (kind === "title") text = title;
    else if (kind === "body") text = body;
    else text = [title, "", body, tags && "", tags].filter(Boolean).join("\n");
    if (!text) return;
    try {
      navigator.clipboard?.writeText(text);
      setDone(kind);
      setTimeout(() => setDone(d => d === kind ? null : d), 1500);
    } catch { /* clipboard unavailable */ }
  }
  function label(kind: "title" | "body" | "all", base: string) {
    return done === kind ? "✓ 已复制" : base;
  }
  const baseBtn = {
    padding: "5px 10px", fontSize: 11.5, fontWeight: 600,
    background: "#fff", color: "var(--primary)",
    border: "1px solid var(--primary)", borderRadius: 6, cursor: "pointer",
    whiteSpace: "nowrap" as const,
  };
  const doneBtn = {
    ...baseBtn, background: "var(--primary)", color: "#fff", borderColor: "var(--primary)",
  };
  return (
    <div className="row" style={{gap: 6, flexShrink: 0, flexWrap: "wrap"}}>
      <button onClick={() => copy("title")}
        style={done === "title" ? doneBtn : baseBtn}>{label("title", "📋 标题")}</button>
      <button onClick={() => copy("body")}
        style={done === "body" ? doneBtn : baseBtn}>{label("body", "📋 正文")}</button>
      <button onClick={() => copy("all")}
        style={done === "all" ? {...doneBtn, fontWeight: 700} : {...baseBtn, fontWeight: 700}}
        title="标题 + 正文 + tags 一起，拷贝去小红书粘贴框直接发">
        {label("all", "📋 全文（标题+正文+tags）")}
      </button>
    </div>
  );
}

function Candidate({c, highlighted, chosen, onChoose}: {
  c: DraftCandidate;
  highlighted?: boolean;
  /** v0.61.19 ：是否当前 final（替代之前的「Synthesizer 融合」）。chosen=true
   *  的卡用 primary 边框 + 「★ 已选为 final」徽章。
   *  v0.61.20 ：onChoose 时整张卡可点 — 不再需要单独点小按钮。 */
  chosen?: boolean;
  onChoose?: () => void;
}) {
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
  const cAngle = (p as any).angle as string | undefined;
  const clickable = !!onChoose && !chosen;
  // 整张卡可点 ：onClick + cursor + hover。chosen 的卡不响应点击（避免重复
  // 触发 API），但点别的卡仍能切换 chosen。
  return (
    <div
      className={`cand ${(chosen || highlighted) ? "final" : ""} ${clickable ? "cand-clickable" : ""}`}
      onClick={clickable ? () => onChoose!() : undefined}
      role={clickable ? "button" : undefined}
      tabIndex={clickable ? 0 : undefined}
      onKeyDown={clickable ? (e => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onChoose!(); }
      }) : undefined}
      title={clickable ? "点击这条卡 = 选为 final（覆盖当前选择）" : undefined}
      style={clickable ? { cursor: "pointer" } : undefined}
    >
      <div className="row" style={{justifyContent: "space-between", alignItems: "flex-start", gap: 8}}>
        <div className="llm" style={{flex: 1, minWidth: 0}}>
          {c.llm}
          {cAngle && (
            <span style={{
              marginLeft: 8, padding: "1px 8px", fontSize: 11, fontWeight: 600,
              background: "var(--primary-soft)", color: "var(--primary)",
              borderRadius: 8,
            }}>{cAngle}</span>
          )}
          {chosen && (
            <span style={{
              marginLeft: 8, padding: "1px 8px", fontSize: 11, fontWeight: 700,
              background: "var(--primary)", color: "#fff", borderRadius: 8,
            }}>★ 已选为 final</span>
          )}
        </div>
        {clickable && (
          <span style={{
            padding: "3px 10px", fontSize: 11.5, fontWeight: 600,
            background: "var(--primary-soft)", color: "var(--primary)",
            border: "1px dashed var(--primary)", borderRadius: 6,
            flexShrink: 0, whiteSpace: "nowrap",
          }}>★ 点这卡选这条</span>
        )}
      </div>
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

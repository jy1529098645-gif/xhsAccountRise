import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { fmtRelative, platformLabel, defaultCycleStartDate, slotDate, topPublishingSlots } from "../format";
import PlatformPill from "../components/PlatformPill";
import ProgressTimeline, { Stage as TimelineStage } from "../components/ProgressTimeline";
import NextStepCard from "../components/NextStepCard";
import { humaniseError, humaniseErrorAsync } from "../errors";
import { isAborted, cancelBackendJob } from "../api";
import { startJob, getJob, cancelJob as cancelLocalJob, clearJob as clearLocalJob, useJob } from "../lib/jobs";
import { LLM_CATALOG, CONTENT_ANGLES as STRATEGY_ANGLES } from "../catalog";
import type {
  AccountInputDTO, Library, Platform, StrategicDirectionDTO, StrategyPackDTO,
  StrategyListItem, ProductContextDTO, GoalTypeDTO,
} from "../types";

const AUTOFILL_STAGES: TimelineStage[] = [
  { label: "🤖 Claude 看你的库出一版初稿", durationSec: 20,
    sub: "拟方向 / 受众 / 周期 / 频率 等字段" },
  { label: "🤖 OpenAI 独立出另一版", durationSec: 20,
    sub: "并行进行" },
  { label: "🤖 主编融合共识 → 给你一份合并稿", durationSec: 15 },
];

const PROPOSE_STAGES: TimelineStage[] = [
  { label: "🤖 读 DNA + 你的 brief", durationSec: 5 },
  { label: "🤖 Claude 产 3-5 个差异化方向", durationSec: 25,
    sub: "每个方向带 hook / 受众 / 风险 / 备选" },
];

// v0.62 ：strategy 不再批量生成 body draft（搬到 Composer 单篇多 agent 流程）。
// expand 阶段 = 排期 (含每条 2 个 alt) + 资源清单，~50s 总耗时。
const EXPAND_STAGES: TimelineStage[] = [
  { label: "🤖 排期师 + 替代方案生成（30 条 × 主+2 备选）", durationSec: 40,
    sub: "时段 / 角度 / 格式 / 大纲，全部数据驱动" },
  { label: "🤖 资源/风险师整理材料清单 + 指标", durationSec: 15 },
];

// v0.59: 「goal」 phase 加在最前面 — 选起号目标后再决定后续表单字段。
type Phase = "goal" | "autofilling" | "input" | "loading-propose" | "directions" | "loading-expand" | "pack";

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

// v0.59: 多方向 slot 的颜色编码（每个 direction_idx 对应一种颜色）
const DIRECTION_COLORS = ["#2E5C8A", "#a36df0", "#10a37f", "#e0a800", "#c4429a", "#5BC0EB", "#FCB97D", "#7a6fc8"];
const INTENT_COLORS: Record<string, string> = {
  "拉新": "#fff5f5", "互动": "#fff8e6", "转化": "#fdecea", "沉淀": "#f0fafe",
};

// v0.61.15 ：从 localStorage 读 ProjectPicker 写入的 active project id。
// 用作所有「项目作用域 localStorage 键」的后缀，避免跨项目串数据。
function currentProjectId(): string {
  try { return localStorage.getItem("studio.activeProjectId") || "default"; }
  catch { return "default"; }
}

// In-progress brief draft — 每个项目独立一份（避免「项目 A 的 goal_type
// 在项目 B 上还残留」）。老的全局 key 兜底迁移到 default 项目。
const DRAFT_KEY_BASE = "studio.strategy.draftInput";
function draftKey(): string {
  return `${DRAFT_KEY_BASE}.${currentProjectId()}`;
}
// 旧版（v0.61.14 及之前）单一 key — 仅用于一次性迁移到 default。
const LEGACY_DRAFT_KEY = "studio.strategy.draftInput";

// localStorage key for the user's chosen direction per strategy pack.
// v0.51: persists across navigation so users don't lose their pick when they
// switch modules then come back. Keyed by pack_id so each strategy has its
// own pick.
const CHOSEN_IDX_KEY = "studio.strategy.chosenIdxByPack.v1";

function loadChosenIdxFor(packId: string): number | null {
  try {
    const raw = localStorage.getItem(CHOSEN_IDX_KEY);
    if (!raw) return null;
    const map = JSON.parse(raw) as Record<string, number>;
    const v = map[packId];
    return typeof v === "number" ? v : null;
  } catch { return null; }
}

function saveChosenIdxFor(packId: string, idx: number): void {
  try {
    const raw = localStorage.getItem(CHOSEN_IDX_KEY);
    const map = raw ? JSON.parse(raw) as Record<string, number> : {};
    map[packId] = idx;
    // Keep map size bounded (most recent 50 packs).
    const entries = Object.entries(map);
    if (entries.length > 50) {
      const trimmed = Object.fromEntries(entries.slice(-50));
      localStorage.setItem(CHOSEN_IDX_KEY, JSON.stringify(trimmed));
    } else {
      localStorage.setItem(CHOSEN_IDX_KEY, JSON.stringify(map));
    }
  } catch { /* ignore quota */ }
}

/** Poll a pack until status leaves 'expanding'. Returns the StrategyDetail
 * on success ('expanded'), null if it stayed 'expanding' past the timeout
 * or transitioned to 'expand_failed'.
 *
 * Critical: deduplicates on packId via a module-level map. If a poll is
 * already running for the same pack, new callers piggy-back on it instead
 * of spawning their own — without this, repeated retry clicks pile up
 * N parallel poll loops that hammer the backend at ~N req/sec, starving
 * the actual expand POST and wedging uvicorn. */
const _activePolls = new Map<string, Promise<any>>();

async function pollPackUntilDone(packId: string, timeoutMs: number): Promise<any> {
  const existing = _activePolls.get(packId);
  if (existing) return existing;
  const p = (async () => {
    const start = Date.now();
    let consecutiveFails = 0;
    while (Date.now() - start < timeoutMs) {
      await new Promise(r => setTimeout(r, 6000));
      try {
        const d = await api.getStrategy(packId);
        consecutiveFails = 0;
        if (d.status === "expanded" && d.pack) return d;
        if (d.status === "expand_failed") return null;
      } catch {
        consecutiveFails++;
        if (consecutiveFails >= 20) return null;
      }
    }
    return null;
  })().finally(() => { _activePolls.delete(packId); });
  _activePolls.set(packId, p);
  return p;
}
function emptyInput(): AccountInputDTO {
  return {
    positioning: "", target_audience: "",
    cycle_weeks: 4, posts_per_week: 3,
    personal_strengths: "", constraints: "", platform: "",
    expected_angles: [],
    cycle_start_date: defaultCycleStartDate(),
    goal_type: "",
  };
}

export default function Strategy() {
  const { packId: urlPackId } = useParams<{ packId?: string }>();
  const navigate = useNavigate();

  // v0.59: 默认 phase = "goal" (新流程要先选目标)。如果旧 draft 已经有 goal_type
  // 或者从 packId URL 进来，会被 useEffect 自动调整。
  const [phase, setPhase] = useState<Phase>("goal");
  const [input, setInput] = useState<AccountInputDTO>(() => {
    // v0.61.15 ：每个项目独立 draft。先尝试当前项目 key；如果空 + legacy
    // 全局 key 还存在，迁移那份到默认项目（只有 default 项目能继承旧草稿）。
    try {
      const k = draftKey();
      const cached = localStorage.getItem(k);
      if (cached) return { ...emptyInput(), ...JSON.parse(cached) };
      const pid = currentProjectId();
      if (pid === "default") {
        const legacy = localStorage.getItem(LEGACY_DRAFT_KEY);
        if (legacy) {
          // Migrate ：写到新 key，删 legacy
          try {
            localStorage.setItem(k, legacy);
            localStorage.removeItem(LEGACY_DRAFT_KEY);
          } catch { /* ignore */ }
          return { ...emptyInput(), ...JSON.parse(legacy) };
        }
      }
    } catch { /* ignore */ }
    return emptyInput();
  });
  // v0.59: 多选 directions（替代之前的单选 chosenIdx）
  const [chosenIdxs, setChosenIdxs] = useState<number[]>([]);
  // v0.59: 起号目标分类列表（从 API 拿）
  const [goals, setGoals] = useState<GoalTypeDTO[]>([]);
  const [activeLib, setActiveLib] = useState<Library | null>(null);
  const [platforms, setPlatforms] = useState<Platform[]>([]);
  const [hasExternalReports, setHasExternalReports] = useState<boolean>(false);
  // v0.58: project-level product contexts (the brand bible the LLM reads)
  const [productContexts, setProductContexts] = useState<ProductContextDTO[]>([]);
  function reloadProductContexts() {
    api.listProductContexts().then(setProductContexts).catch(() => {});
  }
  const [packId, setPackId] = useState<string | null>(null);
  const [directions, setDirections] = useState<StrategicDirectionDTO[]>([]);
  const [chosenIdx, setChosenIdx] = useState<number | null>(null);
  const [pack, setPack] = useState<StrategyPackDTO | null>(null);
  const [history, setHistory] = useState<StrategyListItem[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [positionerSpec, setPositionerSpec] = useState("openai");
  const [topicgenSpec, setTopicgenSpec] = useState("openai,deepseek");
  const [schedulerSpec, setSchedulerSpec] = useState("openai");
  const [resourcerSpec, setResourcerSpec] = useState("deepseek");
  const [autofill, setAutofill] = useState<AutofillResult | null>(null);
  const [autofillErr, setAutofillErr] = useState<string | null>(null);
  const [proposeSeen, setProposeSeen] = useState<number>(0);  // SSE progress: how many directions visible so far
  const [lastFailedAction, setLastFailedAction] = useState<
    { kind: "autofill" } | { kind: "propose" } | { kind: "expand"; idx: number } | null
  >(null);
  const activeJobIdRef = useRef<string | null>(null);
  function pauseCurrent() {
    const jid = activeJobIdRef.current;
    if (!jid) return;
    // For expand: real backend cancel (drops in-flight LLM calls, saves
    // checkpoint, so resume picks up). For other jobs: frontend abort
    // only — backend keeps running but result is ignored.
    if (jid.startsWith("expand:")) cancelBackendJob(jid);
    cancelLocalJob(jid);
  }

  // Save in-progress input to localStorage as user types.
  // v0.58: trigger expanded — also save when user only changes angles /
  // cycle_start_date / cycle_weeks / posts_per_week etc. without filling
  // the text fields. Previously the save was gated on positioning OR
  // target_audience OR personal_strengths being non-empty, so picking a
  // date or angles would silently get reset on refresh.
  useEffect(() => {
    if (phase !== "input") return;
    try {
      const trimmed = { ...input };
      const def = emptyInput();
      const hasMeaningfulEdit =
        !!trimmed.positioning ||
        !!trimmed.target_audience ||
        !!trimmed.personal_strengths ||
        !!trimmed.constraints ||
        (trimmed.expected_angles?.length ?? 0) > 0 ||
        trimmed.cycle_weeks !== def.cycle_weeks ||
        trimmed.posts_per_week !== def.posts_per_week ||
        // cycle_start_date check is loose — defaultCycleStartDate() depends
        // on today, so just save if a value is set.
        !!trimmed.cycle_start_date ||
        !!trimmed.platform ||
        // v0.59.1: include goal_type so user who only picked a goal and
        // didn't fill text fields still gets their selection persisted.
        !!trimmed.goal_type;
      if (hasMeaningfulEdit) {
        localStorage.setItem(draftKey(), JSON.stringify(trimmed));
      }
    } catch { /* ignore quota etc. */ }
  }, [input, phase]);

  // Load library list + history on mount. No more auto-firing autofill —
  // it was blocking users 60-100s on first entry without consent. Now there's
  // an explicit "✨ AI 帮拟初稿" button on the form for users who want it.
  useEffect(() => {
    api.libraries().then(ls => setActiveLib(ls.find(l => l.active) ?? null)).catch(() => {});
    api.platforms().then(setPlatforms).catch(() => {});
    api.listStrategies().then(setHistory).catch(() => {});
    // Check if user has external reports OR integrated reports — those count
    // as "reference material" too, so we shouldn't nag about missing DB.
    Promise.all([api.listExternalReports(), api.listIntegratedReports()])
      .then(([ext, integ]) => setHasExternalReports(ext.length > 0 || integ.length > 0))
      .catch(() => {});
    reloadProductContexts();
    api.listStrategyGoals().then(gs => {
      setGoals(gs);
      if (gs.length === 0) {
        // 后端没返回目标列表 — 前端体验非常糟糕（卡在「加载中…」），
        // 退化到 input 阶段让用户能继续。控制台告诉开发者发生了啥。
        // eslint-disable-next-line no-console
        console.warn("[Strategy] /api/strategy/goals returned empty — falling back to input phase.");
        setPhase("input");
      }
    }).catch(err => {
      // eslint-disable-next-line no-console
      console.error("[Strategy] failed to load goals:", err);
      setPhase("input"); // 同上，避免卡在加载中
    });
    // v0.61.2: 不再据 localStorage cache 自动跳过 GoalPicker。
    // 旧逻辑 ：if (cached.goal_type) phase = "input" → 用户每次进 /strategy
    // 都被踢到 input，看不到 8 大目标卡片，反复抱怨「没有起号类型供选择」。
    // 新逻辑 ：永远从 goal phase 开始；缓存的 goal_type 会让对应卡片高亮
    // = 「已选 · 进入下一步 →」，用户再点一下就跳到 input，想换也直接换。

    // Read prefill from sessionStorage if user clicked a 'use this →' opp
    // on the Reports page. Wipe immediately so refresh doesn't re-prefill.
    try {
      const raw = sessionStorage.getItem("strategy.briefPrefill");
      if (raw) {
        const pf = JSON.parse(raw);
        sessionStorage.removeItem("strategy.briefPrefill");
        setInput(prev => ({
          ...prev,
          positioning: pf.positioning ?? prev.positioning,
          target_audience: pf.target_audience ?? prev.target_audience,
          personal_strengths: pf.personal_strengths ?? prev.personal_strengths,
          constraints: pf.constraints ?? prev.constraints,
        }));
        if (pf.note) setInfo(`✨ ${pf.note}`);
      }
    } catch { /* ignore malformed storage */ }

    // Restore previously-completed autofill / propose results from the
    // jobs store. If user navigated away and back, the AI rationale chips
    // and direction cards reappear without re-running.
    const af = getJob<any>("autofill:current");
    if (af?.status === "done" && af.result) {
      setAutofill(af.result);
      // Don't auto-write into input fields here — localStorage DRAFT_KEY
      // is the user's source of truth for what they were editing.
    }
    const pr = getJob<any>("propose:current");
    if (pr?.status === "done" && pr.result && !urlPackId) {
      // Only restore propose if URL doesn't already point at a specific
      // pack (in which case the [urlPackId] effect handles loading).
      setPackId(pr.result.pack_id);
      setDirections(pr.result.directions || []);
      if (pr.result.directions?.length) setPhase("directions");
      // Also restore the user's previously-chosen direction if any.
      const cachedIdx = loadChosenIdxFor(pr.result.pack_id);
      if (cachedIdx !== null) setChosenIdx(cachedIdx);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // If URL contains a packId, load that saved pack. Two important guards
  // against the old race-condition that left users staring at a "🤖🤖🤖 排期
  // 中…" screen forever after propose succeeded:
  //
  //   1. If we already have this pack's state in memory (because submitInput
  //      / pickDirection just populated it before calling navigate), skip
  //      the re-fetch entirely.
  //   2. Don't blindly flip phase to "loading-expand" before we know whether
  //      we're loading a directions-only pack or a fully-expanded one —
  //      that's what made the post-propose flow look hung.
  useEffect(() => {
    if (!urlPackId) return;
    // Already loaded this exact pack? Done — this is the post-propose case
    // where submitInput populated state and then navigated; we must NOT
    // overwrite phase or refetch, otherwise users get stuck on a
    // "🤖🤖🤖 排期 + 列材料" timeline that points nowhere.
    if (packId === urlPackId && (pack || directions.length > 0)) return;

    (async () => {
      // Fresh load from a bookmark / history click — phase=input is the
      // safest neutral state while we figure out what's stored.
      setPhase("input");
      try {
        const d = await api.getStrategy(urlPackId);
        // Server-side chosen_direction_idx wins over the local cache when
        // present (it reflects what was actually expanded). Falls back to
        // the local cache for direction-only packs the user hadn't
        // yet expanded before navigating away.
        const serverIdx = (d as any).chosen_direction_idx;
        const cachedIdx = loadChosenIdxFor(urlPackId);
        const restoredIdx = (typeof serverIdx === "number" && serverIdx >= 0)
          ? serverIdx
          : cachedIdx;
        if (restoredIdx !== null && restoredIdx !== undefined) {
          setChosenIdx(restoredIdx);
        }
        // v0.59.1: restore multi-direction selection from server. Backend
        // returns chosen_direction_idxs as a parsed list. Empty / missing
        // = legacy single-direction pack (fall back to single chosenIdx).
        const serverIdxs = (d as any).chosen_direction_idxs;
        if (Array.isArray(serverIdxs) && serverIdxs.length > 0) {
          setChosenIdxs(serverIdxs);
        }
        if (d.pack) {
          setPack(d.pack);
          setPackId(urlPackId);
          setDirections(d.directions || []);
          setPhase("pack");
        } else if (d.directions?.length) {
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

  async function runAutofill(extraHints?: { personal?: string; constraints?: string; deep?: boolean }) {
    setPhase("autofilling"); setAutofillErr(null); setInfo(null);
    const job = startJob<any>(
      "autofill:current", "autofill",
      (signal) => api.autofillStrategy({
        personal_hint: extraHints?.personal ?? input.personal_strengths ?? "",
        constraints_hint: extraHints?.constraints ?? input.constraints ?? "",
        deep: !!extraHints?.deep,
      }, signal),
    );
    activeJobIdRef.current = "autofill:current";
    try {
      const r = await job.promise;
      setAutofill(r);
      // v0.59.1: 必须用「函数式 setInput」保留用户已设的字段（特别是
      // goal_type — autofill 后端目前还不知道 goal 分类，response.input.goal_type
      // 永远是空字符串，整体替换会把用户的选择 nuke 掉）。
      setInput(prev => ({
        ...prev,
        positioning: r.input.positioning || prev.positioning,
        target_audience: r.input.target_audience || prev.target_audience,
        cycle_weeks: r.input.cycle_weeks || prev.cycle_weeks || 4,
        posts_per_week: r.input.posts_per_week || prev.posts_per_week || 3,
        personal_strengths: r.input.personal_strengths || prev.personal_strengths,
        constraints: r.input.constraints || prev.constraints,
        platform: r.input.platform || prev.platform,
        expected_angles: r.input.expected_angles?.length ? r.input.expected_angles : prev.expected_angles,
        cycle_start_date: r.input.cycle_start_date || prev.cycle_start_date || defaultCycleStartDate(),
        // goal_type 永远从 prev 保留（autofill 不动它）
        goal_type: prev.goal_type,
      }));
      setLastFailedAction(null);
      setPhase("input");
    } catch (e: any) {
      if (isAborted(e)) {
        setInfo("⏸ 已暂停。后端可能还在跑（无害），需要时点上面🪄重新拟。");
        setPhase("input");
      } else {
        setAutofillErr(await humaniseErrorAsync(e));
        setLastFailedAction({ kind: "autofill" });
        setPhase("input");
      }
    } finally {
      activeJobIdRef.current = null;
    }
  }

  const platform = input.platform || activeLib?.platform || "xiaohongshu";

  async function submitInput() {
    // No required-fields gate. positioning + target_audience are now optional
    // hints; if empty, propose runs on DNA + reports alone. This is the
    // user-flow fix: forcing users to write positioning before AI suggests
    // any was illogical.
    setErr(null); setInfo(null); setPhase("loading-propose");
    setProposeSeen(0);
    // v0.59.1: clear multi-direction selection — new propose produces fresh
    // directions, old indices would point at the wrong directions.
    setChosenIdxs([]);
    const proposeJob = startJob<any>(
      "propose:current", "propose",
      (signal) => api.proposeStrategyStream(
        { ...input, platform: platform, positioner_spec: positionerSpec },
        (kind, data) => {
          if (kind === "progress" && typeof data?.n_seen === "number") {
            setProposeSeen(data.n_seen);
          }
        },
        signal,
      ),
    );
    activeJobIdRef.current = "propose:current";
    try {
      const res = await proposeJob.promise;
      setPackId(res.pack_id);
      setDirections(res.directions);
      setLastFailedAction(null);
      setPhase("directions");
      setProposeSeen(0);
      // Persist URL so reload/bookmark works
      navigate(`/strategy/${res.pack_id}`, { replace: true });
      api.listStrategies().then(setHistory).catch(() => {});
    } catch (e: any) {
      if (isAborted(e)) {
        setInfo("⏸ 已暂停。点上面🚀重新启动会从头开始。");
        setPhase("input");
      } else {
        setErr(await humaniseErrorAsync(e)); setInfo(null);
        setLastFailedAction({ kind: "propose" });
        setPhase("input");
      }
    } finally {
      activeJobIdRef.current = null;
    }
  }

  // v0.59: 改为多选 confirm。idx 留作向后兼容：直接传 idx 时也加进 chosenIdxs 列表。
  async function pickDirection(idx: number, restart: boolean = false) {
    if (!packId) return;
    // Use current chosenIdxs if non-empty (multi-select flow), otherwise
    // treat the explicit idx as single-direction.
    const idxs = chosenIdxs.length > 0 ? chosenIdxs : [idx];
    setChosenIdx(idxs[0] ?? idx); setErr(null);
    saveChosenIdxFor(packId, idxs[0] ?? idx);
    // If a job for this pack is already running locally, cancel it before
    // starting a new one (the backend restart=true also handles this on
    // its side; we do both for snappy UX).
    const existingId = `expand:${packId}`;
    const existing = getJob(existingId);
    if (existing && existing.status === "running") {
      cancelLocalJob(existingId);
      clearLocalJob(existingId, true);  // force-clear so startJob doesn't dedupe
      restart = true;
    }
    setInfo(restart
      ? "🔄 取消之前的，重新跑（约 60-90s）…"
      : "AI 正在生成 N 周完整排期 + 材料清单（约 60-90s）…");
    setPhase("loading-expand");
    activeJobIdRef.current = `expand:${packId}`;
    const expandJob = startJob<any>(
      `expand:${packId}`, "expand",
      (signal) => api.expandStrategy(packId, idxs[0] ?? idx, {
        topicgen_spec: topicgenSpec,
        scheduler_spec: schedulerSpec,
        resourcer_spec: resourcerSpec,
        restart,
        chosenIdxs: idxs.length > 1 ? idxs : undefined,
      }, signal),
      { pack_id: packId, idx: idxs[0] ?? idx, restart, idxs },
    );
    try {
      const res: any = await expandJob.promise;
      if (res && res.status === "paused") {
        setInfo("⏸ 已暂停。点这个方向「继续等 / 重新点击」会从断点接上 — 已完成的阶段不会重跑。");
        setPhase("directions");
        return;
      }
      setPack(res.pack);
      setInfo(null);
      setLastFailedAction(null);
      setPhase("pack");
      navigate(`/strategy/${packId}`, { replace: true });
      try { localStorage.removeItem(draftKey()); } catch { /* ignore */ }
      api.listStrategies().then(setHistory).catch(() => {});
    } catch (e: any) {
      // User pressed pause: just stop, don't surface as error.
      if (isAborted(e)) {
        setInfo("⏸ 已暂停。后端可能还在跑 — 之后点这个方向「继续等」会自动接回结果。");
        setPhase("directions");
        return;
      }
      // Two recovery paths:
      //   (a) Network drop mid-call: backend is probably still running →
      //       poll for completion.
      //   (b) 409 'already expanding': another tab/click started one and
      //       it's still in flight → also just poll.
      const msg = e instanceof Error ? e.message : String(e);
      const isNetwork = /Failed to fetch|NetworkError|TypeError.*fetch|net::ERR/.test(msg);
      const isAlreadyRunning = /409|expand 已经在跑/.test(msg);
      if (isNetwork || isAlreadyRunning) {
        setInfo(isAlreadyRunning
          ? "⏳ 这个 pack 已经有 expand 在跑了，正在等结果…（最多 8 分钟）"
          : "⚡ 网络断了一下，但后端可能还在跑。正在尝试自动恢复…（最多 8 分钟）");
        const recovered = await pollPackUntilDone(packId, 8 * 60_000);
        if (recovered?.pack) {
          setPack(recovered.pack);
          setInfo(null);
          setLastFailedAction(null);
          setPhase("pack");
          navigate(`/strategy/${packId}`, { replace: true });
          try { localStorage.removeItem(draftKey()); } catch { /* ignore */ }
          api.listStrategies().then(setHistory).catch(() => {});
          return;
        }
      }
      setErr(await humaniseErrorAsync(e)); setInfo(null);
      setLastFailedAction({ kind: "expand", idx });
      setPhase("directions");
    } finally {
      activeJobIdRef.current = null;
      activeJobIdRef.current = null;
    }
  }

  function retryLastAction() {
    if (!lastFailedAction) return;
    setErr(null);
    if (lastFailedAction.kind === "autofill") runAutofill();
    else if (lastFailedAction.kind === "propose") submitInput();
    else if (lastFailedAction.kind === "expand") pickDirection(lastFailedAction.idx);
  }

  async function deleteHistory(packIdToDelete: string) {
    try {
      await api.deleteStrategy(packIdToDelete);
      setHistory(prev => prev.filter(h => h.pack_id !== packIdToDelete));
    } catch (e: any) {
      setErr(humaniseError(e));
    }
  }

  function reset() {
    // v0.59: 重置回 goal 第 0 步，让用户重新选目标。包括清空 chosenIdxs。
    setPhase("goal"); setPackId(null); setDirections([]);
    setChosenIdx(null); setChosenIdxs([]); setPack(null);
    setErr(null); setInfo(null);
    setAutofill(null);
    try { localStorage.removeItem(draftKey()); } catch { /* ignore */ }
    setInput(emptyInput());
    if (urlPackId) navigate("/strategy");
  }

  // v0.57: 返回上一步但保留所有状态。reset() 是 nuke 全部重来，这两个
  // 只切 phase，不动 input/directions/pack — 用户可以来回切。
  // 如果切回 input 后改了字段重新「出方向」，directions 自然被新结果覆盖。
  function backToInput() {
    setPhase("input"); setErr(null); setInfo(null);
  }
  function backToDirections() {
    if (directions.length === 0) {
      // 没有 directions 在内存里（比如直接访问 /strategy/{packId}）→ 回到 input
      backToInput();
      return;
    }
    setPhase("directions"); setErr(null); setInfo(null);
  }

  function startNew(useAi = true) {
    setPhase("input"); setPackId(null); setDirections([]);
    setChosenIdx(null); setPack(null); setErr(null); setInfo(null);
    if (urlPackId) navigate("/strategy");
    if (useAi && api.isConnected()) {
      runAutofill();
    }
  }

  // v0.61.14 ：4 步 stepper — 让用户随时跳回任意步骤回看 / 修改。每步
  // 是否可点 ：
  //   1 目标 → 永远可点（回去重选 goal_type，不丢失下游已有的 input / pack）
  //   2 输入 → 永远可点（回去改字段；如果已有 pack 也保留在内存里）
  //   3 方向 → directions 有内容时可点（要么有 packId + directions，要么
  //            propose 已完成在 jobs store 里）
  //   4 排期 → pack 有内容时可点（expand 已完成）
  const stepperStep =
    phase === "goal" ? 1
    : phase === "autofilling" ? 2
    : phase === "input" ? 2
    : phase === "loading-propose" ? 3
    : phase === "directions" ? 3
    : phase === "loading-expand" ? 4
    : phase === "pack" ? 4
    : 1;
  const canStep3 = directions.length > 0;
  const canStep4 = !!pack;
  function StepBtn({n, label, canGo, onGo}: {n: number; label: string; canGo: boolean; onGo: () => void}) {
    const isCurrent = stepperStep === n;
    const isDone = stepperStep > n;
    const clickable = canGo || isDone;
    return (
      <button type="button" onClick={() => { if (clickable) onGo(); }} disabled={!clickable}
        title={clickable ? `跳到第 ${n} 步` : `先完成前面的步骤`}
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
        <h1>🚀 起号策略 · 第 2 步</h1>
        <p>定方向 + 排周期 + 出初稿正文</p>
      </div>

      {/* v0.61.14 ：步骤跳转条 ─ 每步状态/可点击都自动算出来 */}
      <div className="card" style={{padding: "10px 12px"}}>
        <div className="row" style={{gap: 6, alignItems: "stretch"}}>
          <StepBtn n={1} label="🎯 目标" canGo={true}
            onGo={() => setPhase("goal")} />
          <StepBtn n={2} label="📝 输入" canGo={true}
            onGo={() => setPhase("input")} />
          <StepBtn n={3} label="🚀 方向" canGo={canStep3}
            onGo={() => setPhase("directions")} />
          <StepBtn n={4} label="📅 排期" canGo={canStep4}
            onGo={() => setPhase("pack")} />
        </div>
        <div className="muted" style={{fontSize: 11, marginTop: 6, textAlign: "center"}}>
          每步都自动保存 · 随时点上面任一步回看或修改
        </div>
      </div>

      {!api.isConnected() && (
        <div className="banner warn">本地后端没起来 — 看顶部黄条复制命令启动。</div>
      )}
      {!activeLib && !hasExternalReports && api.isConnected() && (
        <div className="banner info">
          <b>建议先给 AI 一些参考材料</b>。
          你可以 ：(a) 上传一个数据库（小红书爬取的 .db） → AI 自动出 DNA + 共识报告，或者
          (b) <Link to="/reports">📊 分析报告</Link> 页底部直接**上传你已有的外部分析报告**
          （PDF / TXT / MD / 任何格式），AI 会直接当强参考使用。
          完全不传也能跑，但效果会差。
        </div>
      )}
      {!activeLib && hasExternalReports && api.isConnected() && (
        <div className="banner info" style={{background: "var(--ok-soft)"}}>
          ✓ 检测到你已上传外部分析报告，AI 会基于这些报告出策略（无需数据库）。
        </div>
      )}
      {err && (
        <div className="banner danger" style={{display: "flex",
                                                 justifyContent: "space-between",
                                                 alignItems: "flex-start", gap: 12}}>
          <div style={{whiteSpace: "pre-wrap", flex: 1}}>{err}</div>
          <div className="row" style={{gap: 6, flexShrink: 0}}>
            {lastFailedAction && (
              <button className="secondary" style={{padding: "4px 10px", fontSize: 12}}
                onClick={retryLastAction}>↻ 重试</button>
            )}
            <button className="ghost" style={{padding: "4px 8px", fontSize: 12}}
              onClick={() => setErr(null)}>关闭</button>
          </div>
        </div>
      )}
      {info && !err && <div className="banner info">{info}</div>}

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
                  <td>
                    <div className="row" style={{gap: 8, justifyContent: "flex-end"}}>
                      <Link to={`/strategy/${h.pack_id}`}>打开 →</Link>
                      <button className="ghost" style={{padding: "2px 8px", fontSize: 11, color: "var(--danger)"}}
                        onClick={() => deleteHistory(h.pack_id)}>删除</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {phase === "goal" && (
        <GoalPicker goals={goals} selected={input.goal_type || ""}
          onPick={(key) => {
            setInput(prev => ({ ...prev, goal_type: key }));
            setPhase("input");
          }} />
      )}

      {phase === "autofilling" && (
        <div className="card">
          <div className="spread" style={{alignItems: "flex-start"}}>
            <div>
              <h2 style={{margin: "0 0 4px"}}>🤖🤖 AI 双方正在为你拟起号初稿</h2>
              <p className="muted" style={{margin: 0}}>Claude + OpenAI 独立分析 → 互评 → 主编融合共识</p>
            </div>
            <button className="ghost" onClick={pauseCurrent}
              style={{padding: "6px 12px", fontSize: 13}}>⏸ 暂停</button>
          </div>
          <ProgressTimeline stages={AUTOFILL_STAGES} currentIndex={-1} auto />
        </div>
      )}

      {phase === "input" && (
        <>
          <MaterialsBar
            activeLib={activeLib}
            hasExternalReports={hasExternalReports}
            productContexts={productContexts}
            currentGoal={goals.find(g => g.key === input.goal_type) ?? null}
            onChangeGoal={() => setPhase("goal")}
          />
          <ProductContextCard
            contexts={productContexts}
            onChanged={reloadProductContexts}
          />
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
            onBackToGoal={() => setPhase("goal")}
            selectedGoal={goals.find(g => g.key === input.goal_type) ?? null}
            onRequestAutofill={() => runAutofill()}
            hasAutofillResult={!!autofill}
            fieldRationale={autofill?.field_rationale ?? {}}
            consensusNotes={autofill?.consensus_notes ?? []}
            singleSideViews={autofill?.single_side_views ?? []}
          />
        </>
      )}

      {phase === "loading-propose" && (
        <div className="card">
          <div className="spread" style={{alignItems: "flex-start"}}>
            <div>
              <h2 style={{margin: "0 0 4px"}}>🤖 AI 在为你拟候选方向</h2>
              <p className="muted" style={{margin: 0}}>流式输出 · 读 brief → 解析 DNA → 8-12 个方向边写边出</p>
            </div>
            <button className="ghost" onClick={pauseCurrent}
              style={{padding: "6px 12px", fontSize: 13}}>⏸ 暂停</button>
          </div>
          {proposeSeen > 0 && (
            <div className="banner info" style={{marginTop: 10,
                                                  background: "var(--primary-soft)",
                                                  borderColor: "var(--primary)"}}>
              ✨ 已生成 <b>{proposeSeen}</b> 个方向中…（目标 8-12 个）
            </div>
          )}
          <ProgressTimeline stages={PROPOSE_STAGES} currentIndex={-1} auto />
        </div>
      )}

      {phase === "directions" && (
        <DirectionsList
          directions={directions} chosenIdx={chosenIdx}
          chosenIdxs={chosenIdxs} setChosenIdxs={setChosenIdxs}
          onPick={pickDirection} onReset={reset} onBack={backToInput}
          slotCount={input.cycle_weeks * input.posts_per_week}
        />
      )}

      {phase === "loading-expand" && (
        <div className="card">
          <div className="spread" style={{alignItems: "flex-start"}}>
            <div>
              <h2 style={{margin: "0 0 4px"}}>🤖🤖🤖 AI 团队正在排期 + 列材料</h2>
              <p className="muted" style={{margin: 0}}>多家 LLM 协作出完整周历 + 材料清单 + 风险评估</p>
            </div>
            <button className="ghost" onClick={pauseCurrent}
              style={{padding: "6px 12px", fontSize: 13}}>⏸ 暂停</button>
          </div>
          <ProgressTimeline stages={EXPAND_STAGES} currentIndex={-1} auto />
        </div>
      )}

      {phase === "pack" && pack && (
        <PackView pack={pack} onReset={reset} onBack={backToDirections}
          hasDirections={directions.length > 0} />
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
  onBackToGoal?: () => void;
  selectedGoal?: GoalTypeDTO | null;
  onRequestAutofill?: () => void;
  hasAutofillResult?: boolean;
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
      <div className="spread" style={{alignItems: "flex-start"}}>
        <h2 style={{margin: 0}}>{hasAutofill ? "2. 检查 / 编辑 AI 拟好的初稿" : "2. 你的账号想法"}</h2>
        <div className="row" style={{gap: 6}}>
          {props.onBackToGoal && (
            <button className="ghost" onClick={props.onBackToGoal}
              style={{fontSize: 12, padding: "6px 12px"}}
              title="返回上一步重新选起号目标">
              ← 返回选目标
            </button>
          )}
          {!hasAutofill && !props.hasAutofillResult && props.onRequestAutofill && (
            <button className="ghost" onClick={props.onRequestAutofill}
              style={{fontSize: 12, padding: "6px 12px"}}>
              ✨ 不知道填啥？AI 帮拟一版（~15s）
            </button>
          )}
        </div>
      </div>
      {props.selectedGoal && (
        <div style={{
          marginTop: 8, padding: 8, background: "var(--primary-soft)",
          borderRadius: 6, fontSize: 12.5,
        }}>
          <b>{props.selectedGoal.emoji} {props.selectedGoal.name}</b>
          <span className="muted" style={{marginLeft: 8}}>{props.selectedGoal.description}</span>
        </div>
      )}

      {/* v0.61.5 启动阶段 — AI 据 DNA/报告推荐，用户可手动覆盖 */}
      <div style={{marginTop: 10, marginBottom: 12}}>
        <label style={{fontSize: 12.5, marginBottom: 4}}>启动阶段</label>
        <div className="row" style={{gap: 6, flexWrap: "wrap"}}>
          {[
            {k: "", label: "🤖 AI 自决", hint: "据 DNA/报告自己挑节奏（默认）"},
            {k: "cold", label: "🆕 冷启动", hint: "0 粉 · 主营造人设/痛点共鸣 · 后期才转化"},
            {k: "warm", label: "🔥 热启动", hint: "已有粉丝/资源 · 早期就可强转化"},
            {k: "hybrid", label: "🌗 混合", hint: "前期人设 + 后期转化的渐进节奏"},
          ].map(opt => {
            const sel = (i.startup_phase || "") === opt.k;
            return (
              <button key={opt.k} type="button"
                onClick={() => set("startup_phase", opt.k)}
                title={opt.hint}
                style={{
                  flex: 1, minWidth: 130,
                  padding: "6px 10px", fontSize: 12.5,
                  background: sel ? "var(--primary)" : "#fff",
                  color: sel ? "#fff" : "var(--fg)",
                  border: sel ? "1px solid var(--primary)" : "1px solid var(--border)",
                  borderRadius: 6, cursor: "pointer", fontWeight: sel ? 600 : 400,
                }}>
                {opt.label}
              </button>
            );
          })}
        </div>
      </div>

      {/* v0.61.13 内容形式偏好 — 图文 / 短视频 / 混合 / AI 自决。硬约束。 */}
      <div style={{marginTop: 6, marginBottom: 12}}>
        <label style={{fontSize: 12.5, marginBottom: 4}}>内容形式</label>
        <div className="row" style={{gap: 6, flexWrap: "wrap"}}>
          {[
            {k: "", label: "🤖 AI 自决", hint: "按 DNA 真实分布决定每篇格式（默认）"},
            {k: "tuwen_only", label: "📝 纯图文", hint: "所有 slot 都是图文（不出视频脚本）"},
            {k: "video_only", label: "🎬 纯短视频", hint: "所有 slot 都是短视频分镜脚本"},
            {k: "mixed", label: "🔀 混合", hint: "图文 + 视频都要，比例 AI 自定（至少 30% / 30%）"},
          ].map(opt => {
            const sel = (i.content_format_preference || "") === opt.k;
            return (
              <button key={opt.k} type="button"
                onClick={() => set("content_format_preference", opt.k)}
                title={opt.hint}
                style={{
                  flex: 1, minWidth: 130,
                  padding: "6px 10px", fontSize: 12.5,
                  background: sel ? "var(--primary)" : "#fff",
                  color: sel ? "#fff" : "var(--fg)",
                  border: sel ? "1px solid var(--primary)" : "1px solid var(--border)",
                  borderRadius: 6, cursor: "pointer", fontWeight: sel ? 600 : 400,
                }}>
                {opt.label}
              </button>
            );
          })}
        </div>
      </div>

      <p className="muted" style={{fontSize: 12, margin: "4px 0 12px"}}>
        字段都可直接填 · AI 帮拟只是给你起点。
      </p>

      <FieldWithRationale label="账号定位（可选 · 留空让 AI 自由推荐）"
        rationale={props.fieldRationale.positioning}
        onAlt={(v) => set("positioning", v)}>
        <input value={i.positioning} onChange={e => set("positioning", e.target.value)}
          placeholder="比如：留学生写论文工具种草 / 考研一战经验分享 / AI 学术副业" />
      </FieldWithRationale>

      <FieldWithRationale label="目标受众（可选）"
        rationale={props.fieldRationale.target_audience}
        onAlt={(v) => set("target_audience", v)}>
        <input value={i.target_audience} onChange={e => set("target_audience", e.target.value)}
          placeholder="比如：赶 ddl 的留学生 / 文科类毕业班学生 / 想做 AI 副业的应届生" />
      </FieldWithRationale>

      <div style={{padding: 12, background: "var(--primary-soft)", borderRadius: 8,
                   marginBottom: 12, border: "1px solid var(--primary)"}}>
        <div className="muted" style={{fontSize: 12, marginBottom: 8}}>
          ⭐ 周期 × 每周篇数 = 总稿数
        </div>
        <div className="row" style={{gap: 12}}>
          <FieldWithRationale label="运营周期"
            rationale={props.fieldRationale.cycle_weeks}
            onAlt={(v) => set("cycle_weeks", Number(v))}
            style={{flex: 1}}>
            <select value={i.cycle_weeks} onChange={e => set("cycle_weeks", Number(e.target.value))}>
              <option value={1}>1 周（试水）</option>
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
              <option value={1}>1 篇 / 周（试水）</option>
              <option value={2}>2 篇 / 周（轻量）</option>
              <option value={3}>3 篇 / 周（推荐）</option>
              <option value={5}>5 篇 / 周（高产）</option>
              <option value={7}>每天一篇</option>
            </select>
          </FieldWithRationale>
        </div>
        <div style={{marginTop: 10, fontSize: 14, fontWeight: 600, color: "var(--primary)",
                      textAlign: "center", padding: 6, background: "#fff", borderRadius: 6}}>
          ⇒ 最终会出 <span style={{fontSize: 18}}>{i.cycle_weeks * i.posts_per_week}</span> 篇带初稿正文的内容
        </div>
        <div style={{marginTop: 10}}>
          <label>📅 起点日期（Day 1 = 哪天发第一篇）</label>
          <div className="row" style={{gap: 8, alignItems: "center"}}>
            <input
              type="date"
              value={i.cycle_start_date || defaultCycleStartDate()}
              onChange={e => set("cycle_start_date", e.target.value)}
              style={{flex: "0 0 auto"}}
            />
            <button type="button" className="ghost" style={{fontSize: 12, padding: "4px 10px"}}
              onClick={() => set("cycle_start_date", defaultCycleStartDate())}>
              下个周一
            </button>
            <span className="muted" style={{fontSize: 12}}>
              排期表会显示每篇的真实日期（5/21 周三 等）
            </span>
          </div>
        </div>
      </div>

      <div style={{marginBottom: 10}}>
        <label>希望覆盖的内容角度（多选 · 留空让 AI 自由分配）</label>
        <div style={{display: "flex", flexWrap: "wrap", gap: 6, marginTop: 4}}>
          {STRATEGY_ANGLES.map(a => {
            const sel = (i.expected_angles || []).includes(a);
            return (
              <button key={a} type="button"
                onClick={() => {
                  const cur = i.expected_angles || [];
                  set("expected_angles", sel ? cur.filter(x => x !== a) : [...cur, a]);
                }}
                style={{
                  padding: "4px 12px", borderRadius: 16, fontSize: 13,
                  border: sel ? "1.5px solid var(--primary)" : "1px solid var(--border)",
                  background: sel ? "var(--primary-soft)" : "#fff",
                  color: sel ? "var(--primary)" : "#333",
                  cursor: "pointer", fontWeight: sel ? 600 : 400,
                }}>
                {sel ? "✓ " : ""}{a}
              </button>
            );
          })}
        </div>
        <div className="muted" style={{fontSize: 11, marginTop: 4}}>
          {(i.expected_angles || []).length === 0
            ? "未选 → AI 自己决定每篇用什么角度"
            : `已选 ${(i.expected_angles || []).length} 个 → 排期会均匀分布在这些角度上`}
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

      <button onClick={props.onSubmit}
        style={{width: "100%", fontSize: 15, padding: "12px 0", fontWeight: 600}}>
        🚀 让 AI 推荐 8-12 个方向 → 选一个后会出 <b style={{fontSize: 17}}>{i.cycle_weeks * i.posts_per_week}</b> 篇带初稿正文
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

function DirectionsList({directions, chosenIdx, chosenIdxs, setChosenIdxs, onPick, onReset, onBack, slotCount}: {
  directions: StrategicDirectionDTO[];
  chosenIdx: number | null;
  chosenIdxs: number[];
  setChosenIdxs: (idxs: number[]) => void;
  onPick: (i: number) => void;
  onReset: () => void;
  onBack: () => void;
  slotCount: number;
}) {
  // v0.59: 多选 — 用户可勾 2-5 个方向，slot 跨方向混排
  function toggle(i: number) {
    if (chosenIdxs.includes(i)) {
      setChosenIdxs(chosenIdxs.filter(x => x !== i));
    } else {
      if (chosenIdxs.length >= 8) {
        alert("最多 8 个方向 — 太分散就失去聚焦了");
        return;
      }
      setChosenIdxs([...chosenIdxs, i]);
    }
  }

  const canSubmit = chosenIdxs.length > 0;
  const slotsPerDir = canSubmit ? Math.max(1, Math.floor(slotCount / chosenIdxs.length)) : 0;

  return (
    <div>
      <div className="spread" style={{marginBottom: 12}}>
        <h2 style={{margin: 0}}>2. 多选 2-5 个方向 · 跨主题混排</h2>
        <div className="row" style={{gap: 6}}>
          <button className="ghost" onClick={onBack}
            title="返回填表页面，保留方向 — 想重新跑 propose 可改 brief 后再点出方向">
            ← 返回填表（保留方向）
          </button>
          <button className="ghost" onClick={onReset}
            style={{color: "var(--danger)"}}
            title="清空所有数据从头开始">
            🗑️ 清空重来
          </button>
        </div>
      </div>
      <p className="muted" style={{fontSize: 13, marginBottom: 16}}>
        AI 给了 {directions.length} 个差异化方向。<b>v0.59 新逻辑 ：你可以多选 2-5 个相关方向</b>，
        然后排期会把 {slotCount} 篇 slot 跨这几个方向混排（每周保留拉新/专业感/沉淀/转化 4 阶段意图，但每篇主题可不同 — 这样发散度更高）。
      </p>

      {/* 选择状态条 */}
      <div style={{
        position: "sticky", top: 8, zIndex: 5,
        padding: 12, background: "var(--primary-soft)",
        border: "1px solid var(--primary)", borderRadius: 8, marginBottom: 14,
      }}>
        <div className="spread" style={{alignItems: "center", gap: 12}}>
          <div style={{fontSize: 13.5}}>
            {canSubmit ? (
              <>
                ✓ 已选 <b style={{fontSize: 16, color: "var(--primary)"}}>{chosenIdxs.length}</b> 个方向
                <span className="muted" style={{marginLeft: 8, fontSize: 12}}>
                  · 大致每个方向 ≈ {slotsPerDir} 篇 · 共 {slotCount} 篇
                </span>
              </>
            ) : (
              <span className="muted">未选任何方向 · 至少选 1 个（推荐 2-5 个相关方向）</span>
            )}
          </div>
          <button
            disabled={!canSubmit}
            onClick={() => onPick(chosenIdxs[0] ?? 0)}
            style={{fontSize: 14, padding: "10px 24px", fontWeight: 600}}>
            🚀 用这 {chosenIdxs.length || "?"} 个方向出排期 →
          </button>
        </div>
      </div>

      <div className="cards-grid" style={{gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))"}}>
        {directions.map((d, i) => {
          const isSel = chosenIdxs.includes(i);
          const isLegacyChosen = chosenIdx === i && !isSel;
          return (
            <div key={i} className="card" style={{
              border: isSel ? "2px solid var(--primary)"
                : isLegacyChosen ? "2px dashed var(--primary)"
                : "1px solid var(--border)",
              background: isSel ? "var(--primary-soft)" : undefined,
              cursor: "pointer",
              padding: "16px 18px",
              position: "relative",
            }} onClick={() => toggle(i)}>
              {/* 多选 checkbox */}
              <div style={{
                position: "absolute", top: 12, right: 12,
                width: 24, height: 24, borderRadius: 4,
                border: `2px solid ${isSel ? "var(--primary)" : "#ccc"}`,
                background: isSel ? "var(--primary)" : "#fff",
                display: "flex", alignItems: "center", justifyContent: "center",
                color: "#fff", fontSize: 14, fontWeight: 700,
              }}>{isSel ? "✓" : ""}</div>

              <div style={{paddingRight: 36}}>
                <div style={{fontSize: 16, fontWeight: 600}}>{d.name}</div>
                <div className="muted" style={{fontSize: 12, marginTop: 2}}>{d.positioning_statement}</div>
              </div>

              <div style={{
                display: "inline-block", marginTop: 8,
                background: "#fff8e6", color: "#a67700",
                fontSize: 11, padding: "2px 8px", borderRadius: 10, fontWeight: 600,
              }}>潜力 {d.score?.toFixed(1) ?? "—"}/10</div>

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
            </div>
          );
        })}
      </div>
    </div>
  );
}

function PackView({pack, onReset, onBack, hasDirections}: {
  pack: StrategyPackDTO; onReset: () => void;
  onBack: () => void; hasDirections: boolean;
}) {
  // Defensive: a legacy pack from before the resourcer-output coercion fix
  // can have risks_and_mitigations or success_metrics stored as a single
  // JSON-encoded string. .map on a string throws and would blank the
  // page (or hit ErrorBoundary). Coerce here too.
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
  const schedule = Array.isArray(pack.schedule) ? pack.schedule : [];
  const materials = toArr(pack.materials_checklist);
  const risks = toArr(pack.risks_and_mitigations);
  const metrics = toArr(pack.success_metrics);
  const themes = Array.isArray(pack.weekly_themes) ? pack.weekly_themes : [];
  const totalSlots = schedule.length;

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
          {hasDirections && (
            <button className="ghost" onClick={onBack}
              title="回到方向列表，可换一个方向重新出排期。当前排期保留在内存里。">
              ← 重新选方向（保留排期）
            </button>
          )}
          <button className="secondary" onClick={onReset}
            title="清空所有数据从头开始">
            🆕 新建策略
          </button>
        </div>
      </div>

      <div className="card">
        {(pack.chosen_directions && pack.chosen_directions.length > 1) ? (
          <>
            <h2>方向 · {pack.chosen_directions.length} 个主题混排</h2>
            <p className="muted" style={{fontSize: 12, margin: "4px 0 10px"}}>
              v0.59 多方向起号 — 30 篇 slot 跨这 {pack.chosen_directions.length} 个方向混排，
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
            <h2>方向 · {pack.chosen_direction.name}</h2>
            <p style={{margin: "4px 0", fontSize: 14}}>{pack.chosen_direction.positioning_statement}</p>
            <p className="muted" style={{fontSize: 12}}>受众：{pack.chosen_direction.target_audience}</p>
          </>
        )}
        {pack.series_thesis && (
          <p style={{fontStyle: "italic", color: "var(--muted)", fontSize: 13, marginTop: 8}}>
            主线：{pack.series_thesis}
          </p>
        )}
      </div>

      {themes.length > 0 && (
        <div className="card">
          <h2>📅 周主题</h2>
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

      {/* v0.62 重构 ：Strategy 不再渲染每篇 slot 详细卡（含 body draft）。
          那些详细 UI + alternatives picker 已经搬到 Composer 的 SchedulePanel。
          这里只给紧凑概览 + 大「→ 去出稿写每篇」按钮。 */}
      <div className="card" style={{borderTop: "3px solid var(--primary)"}}>
        <div className="spread" style={{alignItems: "flex-start", marginBottom: 8}}>
          <div>
            <h2 style={{margin: 0}}>📋 排期概览 · {totalSlots} 篇</h2>
            <p className="muted" style={{fontSize: 12, margin: "4px 0 0"}}>
              每篇都有 AI 推荐 + 2 个次选方案 · 点下方「→ 去出稿写每篇」进 Composer，
              那里能看完整 schedule + 备选 + 多 agent 写每一篇。
            </p>
            {pack.input.cycle_start_date && (
              <p className="muted" style={{fontSize: 12, marginTop: 4}}>
                📅 起点日期 ：<b>{pack.input.cycle_start_date}</b>
              </p>
            )}
          </div>
          <Link to={`/composer?pack=${encodeURIComponent(pack.pack_id)}`}>
            <button style={{whiteSpace: "nowrap", fontSize: 14, padding: "10px 18px", fontWeight: 600}}>
              ✍️ 去出稿写每篇 →
            </button>
          </Link>
        </div>
        {schedule.length === 0 ? (
          <div className="muted" style={{padding: 16, background: "#fafafa",
                                          borderRadius: 8, fontSize: 13}}>
            ⚠️ 这次 AI 没有排出任何 slot（可能是模型一次性输出超长被截）。
            点上面「新建策略」重新跑一次，或者去 设置 切到 claude:opus 重试。
          </div>
        ) : (
          // 紧凑表格 ：日期 / 标题 / 角度 / 格式 / 备选数 — 详情都在 Composer 看
          <table className="table" style={{marginTop: 8, fontSize: 12.5}}>
            <thead>
              <tr>
                <th style={{width: 36}}>#</th>
                <th style={{width: 90}}>📅 日期</th>
                <th style={{width: 90}}>⏰ 时段</th>
                <th>标题</th>
                <th style={{width: 70}}>角度</th>
                <th style={{width: 70}}>格式</th>
                <th style={{width: 60}}>备选</th>
              </tr>
            </thead>
            <tbody>
              {schedule.map((s: any, i: number) => {
                const dt = slotDate(pack.input.cycle_start_date, s.week, s.day_of_week);
                const dateLabel = dt ? dt.display : `W${s.week}·D${s.day_of_week}`;
                const altCount = Array.isArray(s.alternative_versions) ? s.alternative_versions.length : 0;
                return (
                  <tr key={i}>
                    <td className="muted">{i + 1}</td>
                    <td className="muted" style={{fontSize: 11}}>{dateLabel}</td>
                    <td className="muted" style={{fontSize: 11}}>{s.publish_slot || "—"}</td>
                    <td style={{maxWidth: 360, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap"}}>
                      <b>{s.title || "(无标题)"}</b>
                    </td>
                    <td><span className="tag-pill" style={{fontSize: 10.5}}>{s.angle || "—"}</span></td>
                    <td><span className="tag-pill" style={{fontSize: 10.5}}>{s.content_format || "—"}</span></td>
                    <td className="num muted" style={{fontSize: 11}}>
                      {altCount > 0 ? `+ ${altCount}` : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {materials.length > 0 && (
        <div className="card">
          <h2>🎒 启动前要准备的材料</h2>
          <ul style={{marginLeft: 20, lineHeight: 1.9}}>
            {materials.map((m, i) => <li key={i}>{m}</li>)}
          </ul>
        </div>
      )}

      {risks.length > 0 && (
        <div className="card">
          <h2>⚠️ 风险 + 应对</h2>
          <ol style={{marginLeft: 20, lineHeight: 1.9}}>
            {risks.map((r, i) => <li key={i}>{r}</li>)}
          </ol>
        </div>
      )}

      {metrics.length > 0 && (
        <div className="card">
          <h2>📈 成功指标</h2>
          <ul style={{marginLeft: 20, lineHeight: 1.9}}>
            {metrics.map((m, i) => <li key={i}>{m}</li>)}
          </ul>
        </div>
      )}

      <NextStepCard
        label="✍️ 去出稿 · 写每篇（带这份 schedule）"
        hint="Composer 顶部会显示完整 schedule + 每条的备选方案。点哪条就写哪条，多 agent 协作出最终稿。"
        to={`/composer?pack=${encodeURIComponent(pack.pack_id)}`}
      />

      <IterateCard pack={pack} />
    </div>
  );
}

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
      setTimeout(() => navigate(`/strategy/${out.pack_id}`), 800);
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

// v0.62.2: SlotCard removed — all per-slot detail (alternatives, body draft,
// outline, materials, rationale) now lives in Composer's SchedulePanel.
// Strategy only renders the compact 7-column schedule overview.

// v0.55: 「本账号最佳发布时段 Top 5」总览卡 — 从激活库的 DNA 热力图里
// 取 median_likes 最高的 5 个 (周几, 小时) 格子。让运营在看具体排期前
// 先建立总体认知，知道「为什么 AI 把这条排在周三 21:00」。
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

// v0.58 · 「已接入资料」状态条 — 在 Strategy 第一页顶部明确显示 AI 现在
// 能读到哪些材料：激活库 + 外部分析报告 + 产品上下文 + 当前起号目标。
// v0.59.6: 加显眼的「当前起号目标」+ 换号入口（之前 GoalPicker 被
// localStorage cache 后用户找不到入口换）。
function MaterialsBar({activeLib, hasExternalReports, productContexts, currentGoal, onChangeGoal}: {
  activeLib: Library | null;
  hasExternalReports: boolean;
  productContexts: ProductContextDTO[];
  currentGoal: GoalTypeDTO | null;
  onChangeGoal: () => void;
}) {
  const activeContexts = productContexts.filter(c => c.active === 1);
  const items = [
    {
      label: "📚 数据库（爆款 DNA）",
      ok: !!activeLib,
      detail: activeLib ? `${activeLib.display_name} · ${activeLib.notes_count.toLocaleString()} 条笔记` : "未激活",
      cta: activeLib ? null : { text: "去上传 →", to: "/settings#libraries" },
    },
    {
      label: "📊 外部分析报告",
      ok: hasExternalReports,
      detail: hasExternalReports ? "已上传" : "未上传（可选）",
      cta: hasExternalReports ? null : { text: "去上传 →", to: "/reports" },
    },
    {
      label: "📦 产品上下文",
      ok: activeContexts.length > 0,
      detail: activeContexts.length > 0
        ? `${activeContexts.length} 份激活 · 共 ${activeContexts.reduce((a, c) => a + c.chars, 0).toLocaleString()} 字`
        : "未上传（产品/SaaS 必填，其它可选）",
      cta: null, // 表单下面就有上传区，不用跳转
    },
  ];
  return (
    <div className="card" style={{background: "#fafafa", padding: 12}}>
      {/* 当前起号目标 + 换号入口 — 最显眼 */}
      <div style={{
        padding: "8px 12px", marginBottom: 12,
        background: "var(--primary-soft)", borderRadius: 6,
        display: "flex", alignItems: "center", justifyContent: "space-between",
        flexWrap: "wrap", gap: 8,
      }}>
        <div style={{fontSize: 13}}>
          🎯 <b>当前起号目标 ：</b>
          {currentGoal ? (
            <>
              <span style={{fontSize: 16, marginLeft: 4}}>{currentGoal.emoji}</span>
              <b style={{marginLeft: 4}}>{currentGoal.name}</b>
              <span className="muted" style={{fontSize: 11.5, marginLeft: 8}}>
                · {currentGoal.phase_emphasis}
              </span>
            </>
          ) : (
            <span className="muted">未选 — 用通用 voice（建议明确选一个）</span>
          )}
        </div>
        <button className="ghost" onClick={onChangeGoal}
          style={{fontSize: 12, padding: "4px 12px"}}
          title="换号类型 — 个人分享/情感/学术/产品/教学 等">
          {currentGoal ? "🔄 换目标" : "→ 去选目标"}
        </button>
      </div>

      <div className="muted" style={{fontSize: 12, marginBottom: 8}}>
        📊 <b>AI 现在能读到这些材料</b>（材料越完整，策略越贴合你的产品）
      </div>
      <div style={{display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 8}}>
        {items.map((it, i) => (
          <div key={i} style={{
            padding: 10, borderRadius: 6,
            background: "#fff",
            borderLeft: `3px solid ${it.ok ? "var(--ok)" : "#fde2a3"}`,
            fontSize: 12.5,
          }}>
            <div className="row" style={{justifyContent: "space-between", alignItems: "baseline"}}>
              <b style={{fontSize: 13}}>{it.label}</b>
              <span style={{fontSize: 11, color: it.ok ? "var(--ok)" : "#a67700"}}>
                {it.ok ? "✓" : "○"}
              </span>
            </div>
            <div className="muted" style={{marginTop: 2, fontSize: 11.5}}>{it.detail}</div>
            {it.cta && (
              <Link to={it.cta.to} style={{fontSize: 11, marginTop: 4, display: "inline-block"}}>
                {it.cta.text}
              </Link>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

// v0.58 · 产品上下文卡（A）— 让用户在 Strategy 第一页直接上传/管理产品资料。
// 项目级永久：保存后所有后续 Strategy / Compose / Insight 都会自动注入到 prompt。
function ProductContextCard({contexts, onChanged}: {
  contexts: ProductContextDTO[];
  onChanged: () => void;
}) {
  const [expanded, setExpanded] = useState(contexts.length === 0);
  const [name, setName] = useState("");
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  async function handleSave() {
    if (!body.trim()) { setErr("正文不能为空"); return; }
    setBusy(true); setErr(null);
    try {
      await api.createProductContext(name.trim() || "产品介绍", body.trim());
      setInfo("✓ 已保存。下次 Strategy / Compose 都会自动读这个上下文。");
      setName(""); setBody("");
      onChanged();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleFile(f: File | null) {
    if (!f) return;
    setBusy(true); setErr(null);
    try {
      const r = await api.uploadProductContextFile(f);
      setInfo(`✓ 上传成功 · ${r.chars.toLocaleString()} 字 · 自动激活`
        + (r.extract_warning ? `（注意 ：${r.extract_warning}）` : ""));
      onChanged();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function toggleActive(c: ProductContextDTO) {
    try { await api.setProductContextActive(c.context_id, c.active !== 1); onChanged(); }
    catch (e: any) { alert("失败: " + e.message); }
  }

  async function del(c: ProductContextDTO) {
    if (!confirm(`删除「${c.name}」？删了之后 AI 就不再读这条上下文了。`)) return;
    try { await api.deleteProductContext(c.context_id); onChanged(); }
    catch (e: any) { alert("失败: " + e.message); }
  }

  return (
    <div className="card" style={{borderLeft: "4px solid var(--primary)"}}>
      <div className="spread" style={{alignItems: "flex-start"}}>
        <div style={{flex: 1}}>
          <h2 style={{margin: 0}}>📦 产品上下文</h2>
          <p className="muted" style={{fontSize: 12, margin: "4px 0 0"}}>
            告诉 AI 你的产品 — 项目级永久，Strategy/Compose/Insight 自动读。
          </p>
        </div>
        <button className="ghost" onClick={() => setExpanded(!expanded)}>
          {expanded ? "收起" : `${contexts.length > 0 ? `${contexts.length} 个 · ` : ""}展开管理 →`}
        </button>
      </div>

      {/* Existing contexts list — always show even when collapsed if any */}
      {contexts.length > 0 && (
        <div style={{marginTop: 10, display: "grid", gap: 6}}>
          {contexts.map(c => (
            <div key={c.context_id} style={{
              padding: 10, background: c.active === 1 ? "#f6fff0" : "#fafafa",
              borderRadius: 6, borderLeft: `3px solid ${c.active === 1 ? "var(--ok)" : "#ddd"}`,
            }}>
              <div className="row" style={{justifyContent: "space-between"}}>
                <div style={{flex: 1, minWidth: 0}}>
                  <div style={{fontWeight: 600, fontSize: 13}}>
                    {c.active === 1 ? "✓ " : "○ "}{c.name}
                    <span className="muted" style={{fontWeight: 400, fontSize: 11, marginLeft: 8}}>
                      {c.source_format}{c.source_filename ? ` · ${c.source_filename}` : ""} · {c.chars.toLocaleString()} 字
                    </span>
                  </div>
                </div>
                <div className="row" style={{gap: 4}}>
                  <button className="ghost" style={{fontSize: 11, padding: "2px 8px"}}
                    onClick={() => toggleActive(c)}>
                    {c.active === 1 ? "停用" : "启用"}
                  </button>
                  <button className="ghost" style={{fontSize: 11, padding: "2px 8px", color: "var(--danger)"}}
                    onClick={() => del(c)}>删除</button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {expanded && (
        <div style={{marginTop: 12, padding: 12, background: "#f7faff", borderRadius: 8}}>
          {err && <div className="banner danger" onClick={() => setErr(null)}>{err}</div>}
          {info && !err && <div className="banner info" onClick={() => setInfo(null)}>{info}</div>}

          <div className="cards-grid" style={{gridTemplateColumns: "1fr 1fr", gap: 12}}>
            <div style={{padding: 14, background: "#fff", borderRadius: 8,
                         border: "2px dashed var(--primary)", textAlign: "center",
                         cursor: busy ? "wait" : "pointer", opacity: busy ? 0.6 : 1}}
                 onClick={() => !busy && fileRef.current?.click()}>
              <div style={{fontSize: 28}}>📎</div>
              <div style={{fontWeight: 600, marginTop: 4, fontSize: 13}}>
                {busy ? "处理中…" : "上传产品文档"}
              </div>
              <div className="muted" style={{fontSize: 11, marginTop: 4}}>
                PDF / DOCX / MD / TXT · 自动提取文字
              </div>
              <input ref={fileRef} type="file" style={{display: "none"}}
                accept=".pdf,.docx,.md,.txt"
                onChange={e => handleFile(e.target.files?.[0] ?? null)} />
            </div>

            <div style={{padding: 14, background: "#fff", borderRadius: 8, border: "1px solid #eee"}}>
              <div style={{fontWeight: 600, marginBottom: 6, fontSize: 13}}>✍️ 或直接粘贴文本</div>
              <input value={name} onChange={e => setName(e.target.value)}
                placeholder="名称（如：AcademiCats 产品说明）"
                style={{width: "100%", marginBottom: 6, fontSize: 12.5}} />
              <textarea value={body} onChange={e => setBody(e.target.value)}
                placeholder="粘贴产品介绍 / 经典叙事三句话 / 核心功能 / 目标受众 / 雷区... 越详细 AI 越准"
                style={{width: "100%", minHeight: 100, fontSize: 12.5, fontFamily: "inherit"}} />
              <button onClick={handleSave} disabled={busy || !body.trim()}
                style={{marginTop: 6, fontSize: 12, padding: "6px 12px"}}>
                {busy ? "保存中…" : "💾 保存为产品上下文"}
              </button>
            </div>
          </div>

          <details style={{marginTop: 10}}>
            <summary style={{cursor: "pointer", fontSize: 11, color: "var(--muted)"}}>
              💡 应该粘什么？看这里
            </summary>
            <ul style={{marginLeft: 18, fontSize: 12, lineHeight: 1.7, marginTop: 6}}>
              <li><b>核心叙事三句话</b>（"姐妹们我以前要开 8 个网页 / 现在只开 1 个 / AcademiCats 把搜→引→写→降→审 全干了"）</li>
              <li><b>真实功能清单</b>（"14 学术源并发检索 / Synthesis Lab 综述实验室 / Citation list 自动引用 / 多格式导出"）— 避免 AI 编出 "Citation Wizard" 这种不存在的工具名</li>
              <li><b>目标受众分群</b>（"国内毕业论文党 / 留学 essay 党 / 硕士开题党 — 每群语言不一样"）</li>
              <li><b>账号人设</b>（"AC 学姐 · 留英 5 年 · 帮 3000+ 同学搞定论文"）</li>
              <li><b>禁忌词 / 雷区</b>（"代写 / 包过 / 降 0 一律不许说"）</li>
              <li><b>场景金句</b>（"姐妹们 / 家人们 / 宝子" — 用学生圈黑话）</li>
            </ul>
          </details>
        </div>
      )}
    </div>
  );
}

// v0.59: 起号目标分类选择 — 第 0 步，决定后续表单字段 + voice + 是否
// 强制要求产品上下文。8 大目标的细分见 studio/strategy/goals.py。
function GoalPicker({goals, selected, onPick}: {
  goals: GoalTypeDTO[];
  selected: string;
  onPick: (key: string) => void;
}) {
  return (
    <div className="card">
      <h2 style={{margin: "0 0 6px"}}>1. 起号目标</h2>
      <p className="muted" style={{fontSize: 12.5, margin: "0 0 16px"}}>
        AI 据此调整 voice / 阶段 / required 字段。
      </p>

      {goals.length === 0 && <div className="muted">加载中…</div>}

      <div className="cards-grid" style={{gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 12}}>
        {goals.map(g => {
          const isSel = selected === g.key;
          return (
            <div key={g.key} onClick={() => onPick(g.key)}
              className="card"
              style={{
                cursor: "pointer", margin: 0, padding: "14px 16px",
                border: isSel ? "2px solid var(--primary)" : "1px solid var(--border)",
                background: isSel ? "var(--primary-soft)" : "#fff",
                transition: "border-color 0.15s, background 0.15s",
              }}>
              <div style={{fontSize: 28, marginBottom: 4}}>{g.emoji}</div>
              <div style={{fontWeight: 700, fontSize: 15}}>{g.name}</div>
              <div className="muted" style={{fontSize: 12, marginTop: 4, lineHeight: 1.6}}>
                {g.description}
              </div>

              <div style={{marginTop: 10, fontSize: 11, lineHeight: 1.6}}>
                <div className="muted">📅 阶段权重 ：{g.phase_emphasis}</div>
                {g.requires_product_context && (
                  <div style={{color: "#c44", marginTop: 3, fontWeight: 600}}>
                    ⚠️ 产品上下文必填
                  </div>
                )}
                {!g.requires_product_context && g.recommended_product_context && (
                  <div style={{color: "#a67700", marginTop: 3}}>
                    💡 推荐填产品上下文
                  </div>
                )}
              </div>

              {g.example_directions.length > 0 && (
                <div style={{marginTop: 10}}>
                  <div className="muted" style={{fontSize: 10.5, marginBottom: 3}}>
                    示例方向 ：
                  </div>
                  <div style={{display: "flex", flexWrap: "wrap", gap: 3}}>
                    {g.example_directions.slice(0, 4).map(ex => (
                      <span key={ex} className="tag-pill" style={{fontSize: 10.5, padding: "1px 6px"}}>
                        {ex}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              <div style={{
                marginTop: 12, textAlign: "right", fontSize: 12,
                color: isSel ? "var(--primary)" : "var(--muted)",
                fontWeight: isSel ? 700 : 400,
              }}>
                {isSel ? "✓ 已选 · 进入下一步 →" : "选这个 →"}
              </div>
            </div>
          );
        })}
      </div>

      <div className="muted" style={{fontSize: 11.5, marginTop: 14, textAlign: "center"}}>
        下一步可改 / 可重选。
      </div>
    </div>
  );
}

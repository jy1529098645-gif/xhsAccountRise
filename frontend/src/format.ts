export function fmtLikes(n: number | null | undefined): string {
  if (!n) return "0";
  if (n >= 100_000) return `${(n / 10000).toFixed(1)}w`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

export function fmtBytes(b: number): string {
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / 1024 / 1024).toFixed(1)} MB`;
}

export function fmtTime(ts: number): string {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  const pad = (n: number) => n.toString().padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function fmtRelative(ts: number): string {
  if (!ts) return "—";
  const diff = Math.floor(Date.now() / 1000 - ts);
  if (diff < 60) return `${diff}s 前`;
  if (diff < 3600) return `${Math.floor(diff / 60)}分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}小时前`;
  return `${Math.floor(diff / 86400)}天前`;
}

const PLATFORM_LABELS: Record<string, string> = {
  xiaohongshu: "小红书", douyin: "抖音", kuaishou: "快手",
  bilibili: "B站", youtube: "YouTube", reddit: "Reddit",
  x: "X / Twitter", other: "其他",
};

export function platformLabel(id: string | undefined): string {
  if (!id) return "—";
  return PLATFORM_LABELS[id] ?? id;
}

/**
 * Coerce any LLM-returned "list of plain items" into string[].
 *
 * Different models (Claude with tool-schema vs. DeepSeek/GPT with bare
 * JSON-mode) don't honor the schema equally. `engagement_tactics` was typed
 * as `string[]` but DeepSeek returns things like `[{tactic: "..."}]`,
 * `{tactics: [...]}`, or even a single string. This normalises any of those
 * into a clean `string[]` so `.map()` is always safe.
 *
 * - null/undefined           → []
 * - string                   → [string] (or [] if blank)
 * - array                    → flatMap each item through this same coercer
 * - object with a known key  → [value-of-that-key]
 * - object with array prop   → recurse into the first array property
 * - anything else            → [JSON.stringify(value)]
 */
export function coerceStringList(
  value: any,
  keys: string[] = ["tactic", "text", "content", "item", "value"],
): string[] {
  if (value == null) return [];
  if (typeof value === "string") return value.trim() ? [value] : [];
  if (Array.isArray(value)) return value.flatMap(v => coerceStringList(v, keys));
  if (typeof value === "object") {
    for (const k of keys) {
      if (typeof value[k] === "string" && value[k].trim()) return [value[k]];
    }
    for (const v of Object.values(value)) {
      if (Array.isArray(v)) return coerceStringList(v, keys);
    }
    try { return [JSON.stringify(value)]; } catch { return []; }
  }
  return [String(value)];
}

// Compose pipeline 里 7 个 Agent 的英文名 → 用户友好的中文名。
// `agent_name` 在 trace 里通常是 "researcher" / "drafter:gpt-4o[教程]" 等。
const AGENT_ROLE_LABELS: Record<string, string> = {
  strategist: "策略师",
  researcher: "调研员",
  drafter: "起草",
  critic: "审稿",
  refiner: "改稿师",
  synthesizer: "融合师",
  planner: "计划师",
};

export function roleName(agentName: string | undefined): string {
  if (!agentName) return "—";
  // Format: "drafter:gpt-4o[教程]"  → base="drafter", rest="gpt-4o[教程]"
  const [base, rest] = agentName.split(":");
  const label = AGENT_ROLE_LABELS[base] ?? base;
  return rest ? `${label}·${rest}` : label;
}

// ============================================================
// v0.55: calendar date math for Strategy schedule.
// Each TopicSlot has (week, day_of_week) — relative coords against the
// user-chosen cycle_start_date. We compute the actual calendar date for
// display ("5/21 周三").
// ============================================================
const DOW_LABELS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];

/** Snap a Date to the Monday of its ISO week (Mon=0 ... Sun=6 relative). */
function snapToMonday(d: Date): Date {
  const day = d.getDay();           // 0=Sun, 1=Mon, ..., 6=Sat
  const offsetToMonday = day === 0 ? -6 : 1 - day;
  const out = new Date(d);
  out.setDate(out.getDate() + offsetToMonday);
  out.setHours(0, 0, 0, 0);
  return out;
}

/** Return "YYYY-MM-DD" of the next Monday on or after today. */
export function defaultCycleStartDate(): string {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  // If today IS Monday, treat as the start; otherwise next Monday.
  const day = today.getDay();
  const offset = day === 1 ? 0 : (day === 0 ? 1 : (8 - day));
  const monday = new Date(today);
  monday.setDate(today.getDate() + offset);
  return formatDateISO(monday);
}

export function formatDateISO(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

/**
 * Compute the real calendar date for a slot.
 *   cycleStartISO: "YYYY-MM-DD" — anchor day (Monday recommended)
 *   week: 1-based
 *   dayOfWeek: 0=Mon ... 6=Sun
 * Returns "M/D (周X)" + actual Date object (for further formatting).
 */
export function slotDate(
  cycleStartISO: string | undefined,
  week: number,
  dayOfWeek: number,
): { date: Date; display: string } | null {
  if (!cycleStartISO) return null;
  const start = new Date(cycleStartISO);
  if (isNaN(start.getTime())) return null;
  const snapped = snapToMonday(start);
  const offsetDays = (Math.max(1, week) - 1) * 7 + Math.max(0, Math.min(6, dayOfWeek));
  const d = new Date(snapped);
  d.setDate(snapped.getDate() + offsetDays);
  const m = d.getMonth() + 1;
  const day = d.getDate();
  const dow = DOW_LABELS[Math.max(0, Math.min(6, dayOfWeek))];
  return { date: d, display: `${m}/${day} (${dow})` };
}

/**
 * From a DNA heatmap (list of {dow, hour, count, median_likes}), return
 * the top N cells by median_likes (skipping noise: cells with count < 5).
 * Used by the global "本账号最佳发布时段 Top N" summary card.
 */
export interface HeatmapCell {
  dow: number;
  hour: number;
  count: number;
  median_likes: number;
}
export function topPublishingSlots(
  heatmap: HeatmapCell[] | undefined,
  n: number = 5,
  minCount: number = 5,
): Array<HeatmapCell & { label: string }> {
  if (!heatmap || !Array.isArray(heatmap)) return [];
  const sorted = heatmap
    .filter(c => (c.count ?? 0) >= minCount && (c.median_likes ?? 0) > 0)
    .sort((a, b) => (b.median_likes ?? 0) - (a.median_likes ?? 0))
    .slice(0, n);
  return sorted.map(c => ({
    ...c,
    label: `${DOW_LABELS[c.dow] ?? `周?`} ${String(c.hour).padStart(2, "0")}:00`,
  }));
}

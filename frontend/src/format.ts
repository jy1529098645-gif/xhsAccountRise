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
 * `{tactics: [...]}`, or even a single string. This normalizes any of those
 * into a clean `string[]` so `.map()` is always safe.
 *
 * - null/undefined           → []
 * - string                   → [string] (or [] if blank)
 * - array                    → flatMap each item through this same coercer
 * - object with a known key  → [value-of-that-key]
 * - object with array prop   → recurse into the first array property
 * - anything else            → [JSON.stringify(value)]
 */
export function coerceStringList(value: any, keys: string[] = ["tactic", "text", "content", "item", "value"]): string[] {
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

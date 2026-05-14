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

/**
 * v0.65 (P1) — Anchor chips for slot decision / publish rationale.
 *
 * scheduler 现在除了自由文本 decision_rationale / publish_rationale ，还得输出
 * decision_anchors[] / publish_anchors[] ：明确列出引用的 DNA 数据点（蓝海词 /
 * heatmap cell / hook 类别 / tag / 评论 ...），每个 anchor 带 n / median。
 * 这里把每个 anchor 渲染成 chip ，hover 显示完整 label + 来源元数据 ─ 让用户
 * 看见 「AI 不是凭感觉决定的，是因为 X」。
 */

export interface Anchor {
  type?: string;       // blue_ocean | heatmap | hook | tag | comment | keyword | timing_rule
  label?: string;
  value?: string;
  n?: number | null;
  median?: number | null;
  p90?: number | null;
  dow?: number | null;
  hour?: number | null;
}

interface Props {
  anchors?: Anchor[];
  prefix?: string;     // e.g. "📍 来自" or "📅 时段依据"
}

const TYPE_ICON: Record<string, string> = {
  blue_ocean: "🌊",
  hook: "🎣",
  tag: "🏷️",
  comment: "💬",
  keyword: "🔑",
  heatmap: "📅",
  timing_rule: "⏰",
};

const TYPE_LABEL: Record<string, string> = {
  blue_ocean: "蓝海词",
  hook: "hook 类别",
  tag: "tag",
  comment: "评论",
  keyword: "关键词",
  heatmap: "热力图",
  timing_rule: "时段规律",
};

const DOW = ["一", "二", "三", "四", "五", "六", "日"];

export default function AnchorChips({ anchors, prefix }: Props) {
  if (!anchors || anchors.length === 0) return null;
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 4, alignItems: "center", marginTop: 4 }}>
      {prefix && (
        <span style={{ fontSize: 10.5, color: "var(--muted)", marginRight: 2 }}>{prefix}</span>
      )}
      {anchors.map((a, i) => {
        const t = a.type || "keyword";
        const icon = TYPE_ICON[t] || "📌";
        const typeLabel = TYPE_LABEL[t] || t;
        const main = a.label || a.value || typeLabel;
        const tooltipLines: string[] = [`${typeLabel}`];
        if (a.label) tooltipLines.push(a.label);
        if (a.value && a.value !== a.label) tooltipLines.push(`值 ：${a.value}`);
        if (a.dow != null && a.hour != null)
          tooltipLines.push(`时段 ：周${DOW[a.dow] || a.dow} ${String(a.hour).padStart(2, "0")}:00`);
        if (a.n != null) tooltipLines.push(`样本 n=${a.n}`);
        if (a.median != null) tooltipLines.push(`中位互动 ${a.median.toLocaleString()}`);
        if (a.p90 != null) tooltipLines.push(`P90 ${a.p90.toLocaleString()}`);
        return (
          <span key={i} title={tooltipLines.join("\n")} style={{
            display: "inline-flex", alignItems: "center", gap: 3,
            fontSize: 10.5, padding: "1px 6px", borderRadius: 4,
            background: "var(--primary-soft)", color: "var(--primary)",
            fontWeight: 500, maxWidth: 280, cursor: "help",
          }}>
            <span>{icon}</span>
            <span style={{
              overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
            }}>{main}</span>
            {a.n != null && (
              <span style={{ opacity: 0.7, fontSize: 10 }}>· n={a.n}</span>
            )}
          </span>
        );
      })}
    </div>
  );
}

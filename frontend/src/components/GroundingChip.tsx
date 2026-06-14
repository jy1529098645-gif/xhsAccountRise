/**
 * v0.65 (P4) — Grounding-score chip.
 *
 * `grounding_score` = ([ref:xxx] markers + 蓝海词命中) / 段落数 ，由后端在
 * 出稿/重写/快速生成时算好存进 candidate.meta。
 *
 * 色块约定：
 *   - >= 1.0    🟢 强锚定（每段至少 1 个数据点引用）
 *   - 0.3 - 1.0 🟡 部分锚定
 *   - < 0.3     🔴 黑盒嫌疑（基本是 AI 自由发挥）
 *
 * 鼠标 hover 显示 breakdown ：ref markers / 命中关键词列表 / 段落数。
 */
import type { GroundingBreakdown } from "../types";

interface Props {
  score?: number | null;
  breakdown?: GroundingBreakdown | null;
  /** When false, render an inline minimal pill instead of the boxed chip. */
  compact?: boolean;
}

function band(score: number) {
  if (score >= 1.0) return { bg: "#dcfce7", fg: "#15803d", icon: "🟢", label: "强锚定" };
  if (score >= 0.3) return { bg: "#fef3c7", fg: "#92400e", icon: "🟡", label: "部分锚定" };
  return { bg: "#fee2e2", fg: "#991b1b", icon: "🔴", label: "黑盒嫌疑" };
}

export default function GroundingChip({ score, breakdown, compact = false }: Props) {
  if (score == null) return null;
  const b = band(score);
  const kw = breakdown?.keywords_matched ?? [];
  const tooltip =
    `锚定度 ${score.toFixed(2)} · ${b.label}\n` +
    `  · [ref:xxx] 数据点引用 ：${breakdown?.ref_markers ?? 0}\n` +
    `  · 蓝海词命中 ：${breakdown?.keyword_hits ?? 0}` +
    (kw.length ? `（${kw.slice(0, 5).join(" / ")}${kw.length > 5 ? "…" : ""}）` : "") + "\n" +
    `  · 段落数 ：${breakdown?.segments ?? 1}\n` +
    `算法 ：(引用数 + 关键词命中) / 段落数`;
  if (compact) {
    return (
      <span title={tooltip} style={{
        fontSize: 10.5, color: b.fg, fontWeight: 600,
        padding: "0 4px", borderRadius: 3, background: b.bg,
        cursor: "help", whiteSpace: "nowrap",
      }}>
        {b.icon} 锚 {score.toFixed(1)}
      </span>
    );
  }
  return (
    <div title={tooltip} style={{
      display: "inline-flex", alignItems: "center", gap: 6,
      padding: "2px 8px", borderRadius: 6,
      background: b.bg, color: b.fg, fontSize: 12, fontWeight: 600,
      cursor: "help", whiteSpace: "nowrap",
    }}>
      <span>{b.icon}</span>
      <span>锚定度 {score.toFixed(2)}</span>
      <span style={{ fontSize: 10.5, opacity: 0.8 }}>· {b.label}</span>
    </div>
  );
}

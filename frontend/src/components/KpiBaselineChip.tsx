/**
 * v0.65 (P3) — KPI 基线色块 ：把 predicted_likes 跟同 hook_type 在本库的
 * 真实分布（median / p75 / p90）对照，渲染弱/良/强三色块。
 *
 * 跟抖音 DouyinProvenancePanel 的色块体系一致：
 *   weak   = < median        灰
 *   good   = median..p90     主题色
 *   strong = >= p90          金色
 */
import type { KpiBaseline } from "../types";

interface Props {
  predicted: number;
  baseline?: KpiBaseline | null;
}

function band(predicted: number, b: KpiBaseline) {
  if (predicted >= (b.p90 || Infinity)) {
    return { bg: "#fff4dc", fg: "#b06200", label: "≥ P90 强表现", hint: `${(b.p90 || 0).toLocaleString()}+` };
  }
  if (predicted >= (b.median || 0)) {
    return { bg: "var(--primary-soft)", fg: "var(--primary)", label: "≥ 中位 良", hint: `中位 ${(b.median || 0).toLocaleString()}` };
  }
  return { bg: "#f4f4f4", fg: "#666", label: "< 中位 弱", hint: `中位 ${(b.median || 0).toLocaleString()}` };
}

export default function KpiBaselineChip({ predicted, baseline }: Props) {
  if (!baseline || !baseline.median) return null;
  const b = band(predicted, baseline);
  const tooltip =
    `${baseline.source || "本库同类 hook"}\n` +
    `  · 中位 ：${(baseline.median || 0).toLocaleString()}\n` +
    `  · P75  ：${(baseline.p75 || 0).toLocaleString()}\n` +
    `  · P90  ：${(baseline.p90 || 0).toLocaleString()}\n` +
    `  · 样本 ：${baseline.n} 篇\n` +
    `AI 预估的 ${predicted.toLocaleString()} 落在「${b.label}」区。`;
  return (
    <span title={tooltip} style={{
      display: "inline-flex", alignItems: "center", gap: 4,
      fontSize: 10.5, padding: "1px 6px", borderRadius: 4,
      background: b.bg, color: b.fg, fontWeight: 600,
      cursor: "help",
    }}>
      vs {b.hint} → {b.label}
    </span>
  );
}

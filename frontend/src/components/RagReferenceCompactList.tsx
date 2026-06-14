/**
 * v0.65.3 — 出稿页专用 ：用户反馈起号策略已经显示了 「8 篇真实爆款 + 图卡片」，
 * 出稿这里只需要显示 「参考的原话 + 原贴链接 + 原贴数据」。所以这个组件只渲染
 * comments（每条带 source_note 子对象 ：原贴 title / url / 互动数）。
 *
 * 不再显示 ：refs grid（策略页 StrategyPackView 已经有）、hook 模板表（信息冗余）。
 */
import type { RagRef, RagComment, RagHook } from "../types";

const fmtNum = (n: number | undefined) =>
  !n ? "0"
    : n >= 10000 ? (n / 10000).toFixed(1) + "w"
    : n >= 1000 ? (n / 1000).toFixed(1) + "k"
    : String(n);

export interface RagReferenceCompactListProps {
  /** Kept for back-compat — not rendered. */
  refs?: RagRef[];
  comments?: RagComment[];
  /** Kept for back-compat — not rendered. */
  hooks?: RagHook[];
  title?: string;
  subtitle?: string;
  defaultOpen?: boolean;
  className?: string;
  rightSlot?: React.ReactNode;
}

export default function RagReferenceCompactList({
  comments = [],
  title = "📚 AI 参考的真实用户原话",
  subtitle = "下面是 AI 起草时实际读到的高赞评论。每条标了来源原贴 + 原贴的真实互动数据，点链接验证。",
  defaultOpen = true,
  className = "card",
  rightSlot,
}: RagReferenceCompactListProps) {
  if (comments.length === 0) {
    return (
      <div className={className} style={{
        marginTop: 12, borderLeft: "4px solid #f6c265",
        background: "#fff8e6", padding: "10px 14px",
      }}>
        <b style={{color: "#8a5a00"}}>{title} ─ 暂无可用评论数据</b>
        <div style={{fontSize: 12, marginTop: 4, color: "#8a5a00"}}>
          可能原因 ：(a) 本库没存评论（xlsx 导入 / 旧爬虫格式） ；
          (b) 这个 brief 主题没在评论里命中关键词。
          {/* 真实「8 篇爆款」 + 图卡片 已经在 「起号策略」页显示了 ，
              出稿这里只补充评论原话维度。 */}
        </div>
      </div>
    );
  }
  return (
    <details className={className} open={defaultOpen}
      style={{marginTop: 12, borderLeft: "4px solid var(--primary)"}}>
      <summary style={{cursor: "pointer", fontSize: 14.5, fontWeight: 700, color: "var(--primary)"}}>
        {title}
        <span style={{marginLeft: 8, fontSize: 12, color: "var(--muted)", fontWeight: 400}}>
          {comments.length} 条用户原话（含原贴链接 + 互动数据）
        </span>
        {rightSlot && <span style={{marginLeft: 12}}>{rightSlot}</span>}
      </summary>
      <p className="muted" style={{fontSize: 12, margin: "8px 0 12px"}}>{subtitle}</p>

      <ul style={{margin: 0, paddingLeft: 0, listStyle: "none"}}>
        {comments.map((c, idx) => {
          const src = c.source_note;
          return (
            <li key={c.comment_id || idx} style={{
              padding: "8px 10px", marginBottom: 6,
              background: "#fafafa", borderRadius: 6,
              borderLeft: "3px solid var(--primary)",
            }}>
              <div style={{fontSize: 13, lineHeight: 1.65}}>
                <span style={{
                  fontSize: 10.5, padding: "0 5px", borderRadius: 3,
                  background: "var(--primary-soft)", color: "var(--primary)",
                  fontWeight: 600, marginRight: 6,
                }}>{c.like_count}👍</span>
                {c.content}
              </div>
              {/* 来源原贴 ：标题 + 链接 + 互动数据 */}
              {src && (
                <div style={{
                  marginTop: 6, paddingTop: 6,
                  borderTop: "1px dashed #ddd",
                  fontSize: 11.5, color: "#555",
                  display: "flex", flexWrap: "wrap", gap: 8, alignItems: "baseline",
                }}>
                  <span className="muted" style={{fontSize: 10.5, flexShrink: 0}}>📎 来源原贴：</span>
                  {src.url ? (
                    <a href={src.url} target="_blank" rel="noreferrer"
                      style={{flex: 1, minWidth: 0, fontWeight: 600, color: "var(--primary)",
                              overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap"}}>
                      {src.title || "（无标题）"}
                    </a>
                  ) : (
                    <span style={{flex: 1, minWidth: 0, fontWeight: 600,
                                  overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap"}}>
                      {src.title || "（无标题）"}
                    </span>
                  )}
                  <span className="muted" style={{fontSize: 10.5, whiteSpace: "nowrap"}}>
                    👍 {fmtNum(src.liked_count)}
                    {(src.collected_count ?? 0) > 0 && <> · ⭐ {fmtNum(src.collected_count)}</>}
                    {(src.comment_count ?? 0) > 0 && <> · 💬 {fmtNum(src.comment_count)}</>}
                    {(src.share_count ?? 0) > 0 && <> · 🔁 {fmtNum(src.share_count)}</>}
                    {(src.duration_sec ?? 0) > 0 && <> · ▶︎ {src.duration_sec}s</>}
                    {src.author_nickname && <> · @{src.author_nickname}</>}
                  </span>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </details>
  );
}

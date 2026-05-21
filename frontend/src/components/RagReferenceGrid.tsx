/**
 * v0.63 — Shared image-rich reference grid card.
 *
 * Used by 3 places:
 *   1. DraftDetail (历史出稿) — show what AI referenced for that draft
 *   2. Composer  (出稿当下)  — show what AI is referencing while the user
 *                              picks their final
 *   3. Strategy PackView (起号策略最后一步) — show what AI looked at when
 *                              choosing this direction
 *
 * Renders a default-open card with a CSS Grid of reference cards. Each card:
 *   - cover image (4:5, lazy load, clickable to original post)
 *   - up to 3 small image thumbnails (carousel preview)
 *   - title (linked)
 *   - body excerpt (3-line clamp)
 *   - tags chips
 *   - engagement stats + 看原帖 link
 *
 * If a ref has no image (e.g. Douyin video without thumbnail extraction),
 * shows a colored placeholder with ▶︎ or 📝 icon based on duration_sec.
 *
 * Falls back gracefully when image CDN blocks the hotlink (xhs has loose
 * anti-hotlinking; sometimes works, sometimes doesn't — we render a colored
 * gradient if onError fires).
 */
import type { RagRef, RagComment, RagHook } from "../types";

const fmtNum = (n: number | undefined) =>
  !n ? "0"
    : n >= 10000 ? (n / 10000).toFixed(1) + "w"
    : n >= 1000 ? (n / 1000).toFixed(1) + "k"
    : String(n);

export interface RagReferenceGridProps {
  refs: RagRef[];
  comments?: RagComment[];
  hooks?: RagHook[];
  title?: string;        // 默认 "📚 AI 参考的真实素材"
  subtitle?: string;     // 默认 "点封面图或标题跳原帖看完整图文"
  defaultOpen?: boolean; // 默认 true — 用户专门要看
  className?: string;
  rightSlot?: React.ReactNode; // 右上角按钮槽 (e.g. 🔄 刷新)
}

export default function RagReferenceGrid({
  refs, comments = [], hooks = [],
  title = "📚 AI 参考的真实素材",
  subtitle = "这一稿不是凭空写的 ——下面是 AI 起草时实际读到的真实贴文。点封面图或标题跳原帖看完整图文。",
  defaultOpen = true,
  className = "card",
  rightSlot,
}: RagReferenceGridProps) {
  if (refs.length === 0 && comments.length === 0 && hooks.length === 0) {
    return null;
  }
  return (
    <details className={className} open={defaultOpen}
      style={{marginTop: 12, borderLeft: "4px solid var(--primary)"}}>
      <summary style={{cursor: "pointer", fontSize: 14.5, fontWeight: 700, color: "var(--primary)"}}>
        {title}
        <span style={{marginLeft: 8, fontSize: 12, color: "var(--muted)", fontWeight: 400}}>
          {refs.length} 篇真实爆款 · {comments.length} 条用户原话 · {hooks.length} 个 hook 模板
        </span>
        {rightSlot && <span style={{marginLeft: 12}}>{rightSlot}</span>}
      </summary>
      <p className="muted" style={{fontSize: 12, margin: "8px 0 12px"}}>{subtitle}</p>

      {refs.length > 0 && (
        <div>
          <h3 style={{margin: "0 0 8px", fontSize: 13}}>🔥 参考爆款（按相关度 × 互动量混合排序）</h3>
          <div style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))",
            gap: 12,
          }}>
            {refs.map((r, idx) => {
              const imgs = (r.image_urls && r.image_urls.length > 0)
                ? r.image_urls : (r.cover_image ? [r.cover_image] : []);
              const cover = imgs[0];
              return (
                <div key={r.note_id || idx} style={{
                  padding: 0, borderRadius: 8, overflow: "hidden",
                  background: "#fff", border: "1px solid #ececec",
                  display: "flex", flexDirection: "column",
                }}>
                  {cover ? (
                    <a href={r.url} target="_blank" rel="noreferrer"
                      style={{display: "block", background: "#f0f0f0", aspectRatio: "4 / 5", overflow: "hidden"}}>
                      <img src={cover} alt={r.title}
                        loading="lazy" referrerPolicy="no-referrer"
                        style={{width: "100%", height: "100%", objectFit: "cover", display: "block"}}
                        onError={(e) => {
                          const el = e.currentTarget as HTMLImageElement;
                          el.style.display = "none";
                          (el.parentNode as HTMLElement).style.background =
                            "linear-gradient(135deg, var(--primary-soft), #f8f8f8)";
                        }}
                      />
                    </a>
                  ) : (
                    <div style={{
                      aspectRatio: "4 / 5",
                      background: "linear-gradient(135deg, var(--primary-soft) 0%, #fafafa 100%)",
                      display: "flex", alignItems: "center", justifyContent: "center",
                      fontSize: 32, color: "var(--primary)", opacity: 0.4,
                    }}>
                      {(r.duration_sec ?? 0) > 0 ? "▶︎" : "📝"}
                    </div>
                  )}
                  {imgs.length > 1 && (
                    <div style={{display: "grid", gridTemplateColumns: `repeat(${Math.min(imgs.length - 1, 3)}, 1fr)`, gap: 1, background: "#eee"}}>
                      {imgs.slice(1, 4).map((u, i) => (
                        <a key={i} href={r.url} target="_blank" rel="noreferrer"
                          style={{display: "block", aspectRatio: "1 / 1", overflow: "hidden", background: "#f5f5f5"}}>
                          <img src={u} loading="lazy" referrerPolicy="no-referrer"
                            style={{width: "100%", height: "100%", objectFit: "cover", display: "block"}}
                            onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = "none"; }}
                          />
                        </a>
                      ))}
                    </div>
                  )}
                  <div style={{padding: "10px 12px", display: "flex", flexDirection: "column", gap: 6}}>
                    <div className="row" style={{gap: 6, flexWrap: "wrap"}}>
                      <span className="tag-pill" style={{background: "var(--primary-soft)", color: "var(--primary)", fontSize: 10.5}}>
                        #{idx + 1}
                      </span>
                      {(r.duration_sec ?? 0) > 0 ? (
                        <span className="tag-pill" style={{fontSize: 10.5}}>▶︎ {r.duration_sec}s</span>
                      ) : (r.image_count ?? 0) > 0 ? (
                        <span className="tag-pill" style={{fontSize: 10.5}}>🖼️ {r.image_count} 图</span>
                      ) : null}
                      {r.author_nickname && (
                        <span className="tag-pill" style={{fontSize: 10.5}}>@{r.author_nickname}</span>
                      )}
                    </div>
                    <div style={{fontSize: 13, fontWeight: 600, lineHeight: 1.4}}>
                      {r.url ? (
                        <a href={r.url} target="_blank" rel="noreferrer" style={{color: "inherit"}}>
                          {r.title || "（无标题）"}
                        </a>
                      ) : r.title || "（无标题）"}
                    </div>
                    {r.body_excerpt && (
                      <div className="muted" style={{
                        fontSize: 11.5, lineHeight: 1.55,
                        display: "-webkit-box", WebkitLineClamp: 3,
                        WebkitBoxOrient: "vertical", overflow: "hidden",
                      }}>
                        {r.body_excerpt}
                      </div>
                    )}
                    {(r.tags?.length ?? 0) > 0 && (
                      <div style={{fontSize: 10}}>
                        {r.tags!.slice(0, 5).map((t, i) => (
                          <span key={i} className="tag-pill" style={{fontSize: 10}}>#{t}</span>
                        ))}
                      </div>
                    )}
                    <div className="muted" style={{fontSize: 11}}>
                      👍 {fmtNum(r.liked_count)}
                      {(r.collected_count ?? 0) > 0 && <> · ⭐ {fmtNum(r.collected_count)}</>}
                      {(r.comment_count ?? 0) > 0 && <> · 💬 {fmtNum(r.comment_count)}</>}
                      {(r.share_count ?? 0) > 0 && <> · 🔁 {fmtNum(r.share_count)}</>}
                      {r.url && <>　·　<a href={r.url} target="_blank" rel="noreferrer">看原帖 →</a></>}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {comments.length > 0 && (
        <div style={{marginTop: 14}}>
          <h3 style={{margin: "0 0 6px", fontSize: 13}}>💬 用户原话（高赞评论） · AI 用这些话校准真实语气</h3>
          <ul style={{margin: 0, paddingLeft: 18, fontSize: 12.5, lineHeight: 1.75, color: "#555"}}>
            {comments.slice(0, 12).map((c, i) => (
              <li key={c.comment_id || i}>
                <span className="muted" style={{fontSize: 11, marginRight: 6}}>({c.like_count ?? 0}👍)</span>
                {String(c.content ?? "").slice(0, 200)}
              </li>
            ))}
          </ul>
        </div>
      )}

      {hooks.length > 0 && (
        <div style={{marginTop: 14}}>
          <h3 style={{margin: "0 0 6px", fontSize: 13}}>🎣 Hook 模板 · 资源库里最稳的标题套路</h3>
          <div style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))",
            gap: 8,
          }}>
            {hooks.slice(0, 12).map((h, i) => (
              <div key={i} style={{padding: "8px 10px", borderRadius: 6, background: "#fafafa", border: "1px solid #eee"}}>
                <div style={{fontWeight: 600, fontSize: 12.5}}>{h.category}</div>
                <div className="muted" style={{fontSize: 11, marginTop: 3}}>
                  n={h.count} · median 👍 {fmtNum(h.median_likes)}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </details>
  );
}

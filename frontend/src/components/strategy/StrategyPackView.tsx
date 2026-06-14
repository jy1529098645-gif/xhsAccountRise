// Pure display of one strategy pack — direction card, schedule timeline,
// per-slot RAG refs (on expand), and the iterate-to-next-cycle hook.
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../../api";
import { slotDate, topPublishingSlots } from "../../format";
import PlatformPill from "../PlatformPill";
import { humaniseError } from "../../errors";
import { useRagFetch } from "../../hooks/useRagFetch";
import RagReferenceGrid from "../RagReferenceGrid";
import AnchorChips from "../AnchorChips";
import GroundingChip from "../GroundingChip";
import GroundedBody from "../GroundedBody";
import type { StrategyPackDTO } from "../../types";

const DIRECTION_COLORS = ["#2E5C8A", "#a36df0", "#10a37f", "#e0a800", "#c4429a", "#5BC0EB", "#FCB97D", "#7a6fc8"];
const INTENT_COLORS: Record<string, string> = {
  "拉新": "#fff5f5", "互动": "#fff8e6", "转化": "#fdecea", "沉淀": "#f0fafe",
};

// Backend FTS5 uses a trigram tokenizer — usable=false means no token has
// the 3-char minimum, so the request would return 0 hits anyway. Cap at
// 200 chars to keep long positioning statements from blowing up into 100+
// trigram OR clauses.
export function sanitizeRagQuery(raw: string): { query: string; usable: boolean } {
  const cleaned = (raw || "")
    .replace(/[()[\]{}"'`~!@#$%^&*+=\-./\\:;<>?|]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 200);
  if (!cleaned) return { query: "", usable: false };
  const usable = cleaned.split(/\s+/).some(t => t.length >= 3);
  return { query: cleaned, usable };
}

// onWriteClick: omit on the Strategy page (default navigates to
// /composer?slot=...); pass on the Composer page to fill the inline brief
// form without changing routes.
export default function StrategyPackView({pack, onWriteClick, compact = false, onPackReload}: {
  pack: StrategyPackDTO;
  onWriteClick?: (slotIdx: number, altIdx: number) => void;
  /** compact=true (Composer embed) only renders pack ID summary +
   *  SchedulePanel，**省略** Overview / 周主题 / 最佳时段 / 材料 / 风险 /
   *  指标 / IterateCard — 那些都是「起号策略板块」的内容。 */
  compact?: boolean;
  /** Refresh the whole pack after a slot regenerate — keeps the canonical
   *  source of truth in sync with SchedulePanel's local optimistic update. */
  onPackReload?: () => void;
}) {
  const navigate = useNavigate();
  function goWrite(slotIdx: number, altIdx: number) {
    if (onWriteClick) {
      onWriteClick(slotIdx, altIdx);
      return;
    }
    // autostart=1 ：用户从起号策略板块点 ✍️ 写这个 → 跳到出稿 → AI brief
    // 预填完成后自动启动 compose 流水线，不需要用户再点「🚀 启动 AI 团队」。
    // Composer 消费完会从 URL 删掉 autostart 参数，避免刷新时重复 trigger。
    const params: string[] = [
      `slot=${encodeURIComponent(pack.pack_id)}:${slotIdx}`,
      "autostart=1",
    ];
    if (altIdx >= 0) params.push(`alt=${altIdx}`);
    navigate(`/composer?${params.join("&")}`);
  }
  if (compact) {
    // Composer 嵌入模式 ：1 条 pack 摘要 + SchedulePanel + 「看完整大纲」链接
    const dirName = pack.chosen_direction?.name || "";
    return (
      <div>
        <div className="card" style={{padding: "10px 14px", background: "#fafafa"}}>
          <div className="row" style={{justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap"}}>
            <div style={{fontSize: 12.5, flex: 1, minWidth: 200}}>
              📦 <b>当前 pack</b> ：{dirName || pack.pack_id.slice(0, 8)}
              {pack.platform && <span className="muted" style={{marginLeft: 8}}>· {pack.platform}</span>}
              <span className="muted" style={{marginLeft: 8}}>· {pack.schedule.length} 篇 schedule</span>
            </div>
            <button className="ghost" onClick={() => navigate(`/strategy/${pack.pack_id}`)}
              style={{fontSize: 11.5, padding: "3px 10px"}}
              title="跳起号策略板块看完整大纲（方向 / 主题 / 材料 / 风险 / 指标 / 迭代）">
              📋 看完整大纲 →
            </button>
          </div>
        </div>
        <SchedulePanel pack={pack} onWrite={goWrite} />
      </div>
    );
  }
  return (
    <div>
      <StrategyOverview pack={pack} />
      <FavoritesPanel pack={pack} navigate={navigate} />
      <StrategyRefsPanel pack={pack} />
      <SchedulePanel pack={pack} onWrite={goWrite} onPackReload={onPackReload} />
      <IterateCard pack={pack} />
    </div>
  );
}

// v0.66 (item4) ：星标收藏库面板。列出当前 project 收藏的方向 / slot，可删除、
// 可「用这个写」（带结构种子跳出稿）。解决「方向是一次性的，返回上层调整就
// 拿不到相同结果」—— 现在满意的就收藏，随时复用。
function FavoritesPanel({pack, navigate}: {pack: StrategyPackDTO; navigate: (to: string) => void}) {
  const [favs, setFavs] = useState<any[]>([]);
  const [open, setOpen] = useState(false);

  async function reload() {
    try { setFavs(await api.listFavorites()); } catch { setFavs([]); }
  }
  useEffect(() => {
    reload();
    const h = () => reload();
    window.addEventListener("favorites:changed", h);
    return () => window.removeEventListener("favorites:changed", h);
  }, []);

  async function remove(favId: string) {
    try {
      await api.deleteFavorite(favId);
      setFavs(prev => prev.filter(f => f.fav_id !== favId));
    } catch { /* ignore */ }
  }

  // 用收藏的 slot 写一篇 ：把它的结构组成 strategy_seed，经 sessionStorage 带到
  // 出稿页（Composer 读 composer.briefPrefill）。换 pack / 换 cycle 也能复用。
  function writeFromFav(fav: any) {
    const p = fav.payload || {};
    const outline = Array.isArray(p.outline) ? p.outline.map(String) : [];
    const seed = {
      recommended_hook: String(p.hook_type || ""),
      opening_hook: String(p.title || ""),
      structure: outline,
      content_format: String(p.content_format || ""),
      source: "favorite",
    };
    const prefill = {
      topic: String(p.title || fav.label || ""),
      angle: String(p.angle || ""),
      angles: p.angle ? [String(p.angle)] : [],
      extra_constraints: [
        "⭐ 从收藏库带入",
        p.content_format ? `内容形式 ：${p.content_format}` : "",
        outline.length ? `大纲 ：${outline.join(" / ")}` : "",
      ].filter(Boolean).join("\n"),
      strategy_seed: seed,
    };
    try { sessionStorage.setItem("composer.briefPrefill", JSON.stringify(prefill)); } catch { /* quota */ }
    navigate("/composer");
  }

  if (favs.length === 0) return null;
  return (
    <div className="card">
      <div className="row" style={{justifyContent: "space-between", alignItems: "center", cursor: "pointer"}}
        onClick={() => setOpen(o => !o)}>
        <h2 style={{margin: 0}}>⭐ 我的收藏库 <span className="muted" style={{fontSize: 12}}>({favs.length})</span></h2>
        <span style={{fontSize: 12, color: "var(--muted)"}}>{open ? "▴ 收起" : "▾ 展开"}</span>
      </div>
      {open && (
        <div style={{display: "grid", gap: 8, marginTop: 10}}>
          {favs.map(f => (
            <div key={f.fav_id} className="row" style={{
              justifyContent: "space-between", alignItems: "center", gap: 8,
              padding: "8px 10px", background: "#fffdf5", borderRadius: 6,
              border: "1px solid #f0e0a8",
            }}>
              <div style={{flex: 1, minWidth: 0}}>
                <span style={{
                  fontSize: 10.5, padding: "1px 6px", borderRadius: 3, marginRight: 6,
                  background: f.kind === "direction" ? "#e8eefc" : "#fdf0e0",
                  color: f.kind === "direction" ? "#1e40af" : "#b06200", fontWeight: 600,
                }}>{f.kind === "direction" ? "方向" : "选题"}</span>
                <b style={{fontSize: 13}}>{f.label || "(未命名)"}</b>
              </div>
              <div className="row" style={{gap: 6, flexShrink: 0}}>
                {f.kind === "slot" && (
                  <button onClick={() => writeFromFav(f)}
                    style={{fontSize: 11.5, padding: "3px 10px", background: "#fff",
                            border: "1px solid var(--primary)", color: "var(--primary)", fontWeight: 600}}>
                    ✍️ 用这个写
                  </button>
                )}
                <button className="ghost" onClick={() => remove(f.fav_id)}
                  style={{fontSize: 11.5, padding: "3px 8px", color: "var(--muted)"}}>✕</button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// "Why this direction" — RAG refs against the chosen direction's
// name + positioning. Separate from per-slot panels (which justify each
// individual post).
function StrategyRefsPanel({pack}: {pack: StrategyPackDTO}) {
  const chosen = pack.chosen_direction
    || (Array.isArray(pack.chosen_directions) && pack.chosen_directions[0])
    || null;
  const { query, usable: queryUsable } = sanitizeRagQuery(
    chosen ? `${chosen.name || ""} ${chosen.positioning_statement || ""}` : "",
  );
  const { data, loading, err, retry } = useRagFetch(
    query, queryUsable, 8, 12, [pack.pack_id], "StrategyRefsPanel",
  );

  if (loading) {
    return (
      <div className="card" style={{marginTop: 12, borderLeft: "4px solid var(--primary)",
                                     background: "var(--primary-soft)"}}>
        <h2 style={{margin: 0, fontSize: 14, color: "var(--primary)"}}>
          📚 AI 决策这个方向时看的真实素材 · 加载中…
        </h2>
        <p className="muted" style={{fontSize: 12, margin: "4px 0 0"}}>
          按「{chosen?.name || "方向名"}」去资源库 FTS 检索真实爆款 + 抽取封面图…
        </p>
      </div>
    );
  }
  if (err) {
    return (
      <div className="card" style={{marginTop: 12, borderLeft: "4px solid var(--warn, #f6c265)"}}>
        <div className="row" style={{justifyContent: "space-between", alignItems: "flex-start", gap: 8}}>
          <div style={{flex: 1, minWidth: 0}}>
            <h2 style={{margin: 0, fontSize: 14}}>📚 加载参考素材失败</h2>
            <p className="muted" style={{fontSize: 12, margin: "4px 0 0", whiteSpace: "pre-wrap"}}>{err}</p>
            <p className="muted" style={{fontSize: 11, margin: "6px 0 0"}}>
              query ：<code style={{background: "#f6f6f6", padding: "1px 4px"}}>{query || "(空)"}</code>
              {!queryUsable && query && " — 太短/无 ≥3 字 token，FTS 检索不到"}
            </p>
          </div>
          <button className="ghost" onClick={retry}
            style={{fontSize: 11.5, padding: "3px 10px", whiteSpace: "nowrap"}}>
            🔄 重试
          </button>
        </div>
      </div>
    );
  }
  if (!queryUsable) return null;
  if (!data || ((data.refs?.length ?? 0) === 0 && (data.comments?.length ?? 0) === 0 && (data.hooks?.length ?? 0) === 0)) {
    return (
      <div className="card" style={{marginTop: 12, borderLeft: "4px solid #ddd"}}>
        <div className="row" style={{justifyContent: "space-between", alignItems: "flex-start", gap: 8}}>
          <div style={{flex: 1}}>
            <h2 style={{margin: 0, fontSize: 14}}>📚 资源库里这个方向没匹配到爆款</h2>
            <p className="muted" style={{fontSize: 12, margin: "4px 0 0"}}>
              query 「{query.slice(0, 60)}{query.length > 60 ? "…" : ""}」在当前资源库的 FTS 索引里 0 命中。
              先去「资源库」里导入更多和这个方向相关的真实帖，AI 才能拿到参考。
            </p>
          </div>
          <button className="ghost" onClick={retry}
            style={{fontSize: 11.5, padding: "3px 10px", whiteSpace: "nowrap"}}>
            🔄 重试
          </button>
        </div>
      </div>
    );
  }
  return (
    <RagReferenceGrid
      refs={data.refs || []}
      comments={data.comments || []}
      hooks={data.hooks || []}
      title="📚 AI 决策这个方向时参考的真实素材"
      subtitle={`按「${chosen?.name || "方向名"}」从你资源库里筛出的真实爆款帖。点封面 / 标题跳原帖 — 这些就是 AI 决策这个方向时实际读到的内容。`}
      defaultOpen={true}
      rightSlot={
        <button className="ghost" onClick={(e) => { e.preventDefault(); retry(); }}
          style={{fontSize: 11.5, padding: "2px 8px"}}
          title="重新去 RAG 拉一遍参考素材">🔄 刷新</button>
      }
    />
  );
}

// "Why this post" — RAG refs against one schedule slot's title + angle +
// outline. Lazy: only mounts when the slot is expanded; remounts (and
// refetches) on re-expand.
function SlotRagPanel({slot, slotIdx, packId, altIdx = -1}: {
  slot: any;
  slotIdx: number;
  packId: string;
  // -1 = main recommendation; >=0 = alt index (disambiguates cache key
  // when alts are expanded under the same slot).
  altIdx?: number;
}) {
  // v0.65 (P0) ：优先用 slot 持久化的 rag_refs / rag_comments / rag_hooks ─
  // 这些是 body drafter 实际看的内容。altIdx 走 alt 时 fall back 到 live 查询。
  const persistedRefs = Array.isArray(slot?.rag_refs) ? slot.rag_refs : [];
  const persistedComments = Array.isArray(slot?.rag_comments) ? slot.rag_comments : [];
  const persistedHooks = Array.isArray(slot?.rag_hooks) ? slot.rag_hooks : [];
  const hasPersisted = altIdx === -1 && (
    persistedRefs.length > 0 || persistedComments.length > 0 || persistedHooks.length > 0
  );

  const rawParts: string[] = [];
  if (slot?.title) rawParts.push(String(slot.title));
  if (slot?.angle) rawParts.push(String(slot.angle));
  const outline = Array.isArray(slot?.outline) ? slot.outline
    : Array.isArray(slot?.mini_outline) ? slot.mini_outline : [];
  for (const o of outline.slice(0, 2)) if (o) rawParts.push(String(o));
  const { query, usable: queryUsable } = sanitizeRagQuery(rawParts.join(" "));
  const { data, loading, err, retry } = useRagFetch(
    query, hasPersisted ? false : queryUsable, 6, 8, [packId, slotIdx, altIdx], "SlotRagPanel",
  );

  if (hasPersisted) {
    return (
      <div style={{marginTop: 8}}>
        <RagReferenceGrid
          refs={persistedRefs}
          comments={persistedComments}
          hooks={persistedHooks}
          title="📚 AI 写这条时实际看的真实参考（持久化）"
          subtitle="这些就是 body drafter 当时喂进 prompt 的真实贴文 + 评论 + hook 模板。点封面 / 标题跳原帖验证。"
          defaultOpen={true}
          className="card"
        />
      </div>
    );
  }

  if (!queryUsable) return null;

  if (loading) {
    return (
      <div className="muted" style={{
        marginTop: 8, padding: "6px 10px", background: "#f8f8f8",
        borderRadius: 4, fontSize: 11.5,
      }}>
        📚 给这一篇查参考素材中 …（按「{slot?.title ? String(slot.title).slice(0, 30) : "标题"}」检索资源库）
      </div>
    );
  }
  if (err) {
    return (
      <div style={{
        marginTop: 8, padding: "6px 10px", background: "#fff8e6",
        borderRadius: 4, fontSize: 11.5, color: "#8a5a00",
        borderLeft: "3px solid #f6c265",
      }}>
        <div className="row" style={{justifyContent: "space-between", alignItems: "flex-start", gap: 8}}>
          <div style={{flex: 1, minWidth: 0, whiteSpace: "pre-wrap"}}>
            ⚠️ 这一篇没拉到参考素材：{err}
          </div>
          <button className="ghost" onClick={retry}
            style={{fontSize: 11, padding: "2px 8px", whiteSpace: "nowrap"}}>
            🔄 重试
          </button>
        </div>
      </div>
    );
  }
  const refsN = data?.refs?.length ?? 0;
  const commentsN = data?.comments?.length ?? 0;
  const hooksN = data?.hooks?.length ?? 0;
  if (refsN === 0 && commentsN === 0 && hooksN === 0) {
    return (
      <div className="muted" style={{
        marginTop: 8, padding: "6px 10px", background: "#fafafa",
        borderRadius: 4, fontSize: 11.5, borderLeft: "3px solid #ddd",
      }}>
        📚 这一篇在你的资源库里没匹配到爆款 — 先去「资源库」补一些和「{slot?.title ? String(slot.title).slice(0, 24) : "标题"}」
        相关的真实帖，再回来看。
      </div>
    );
  }
  return (
    <div style={{marginTop: 8}}>
      <RagReferenceGrid
        refs={data?.refs || []}
        comments={data?.comments || []}
        hooks={data?.hooks || []}
        title="📚 这一篇 AI 看的真实参考"
        subtitle="按这一篇 slot 的标题 + 角度从资源库里筛出的真实爆款。点封面 / 标题跳原帖 — 验证 AI 没瞎编。"
        defaultOpen={true}
        className="card"
        rightSlot={
          <button className="ghost" onClick={(e) => { e.preventDefault(); retry(); }}
            style={{fontSize: 11, padding: "2px 8px"}}
            title="重新去 RAG 拉一遍">🔄 刷新</button>
        }
      />
    </div>
  );
}

function StrategyOverview({pack}: {pack: StrategyPackDTO}) {
  // v0.66 (item4) ：收藏方向到「我的收藏库」。
  const [dirFaved, setDirFaved] = useState(false);
  async function favoriteDirection(dir: any) {
    try {
      await api.addFavorite("direction", dir, String(dir?.name || ""));
      setDirFaved(true);
      setTimeout(() => setDirFaved(false), 1800);
      window.dispatchEvent(new CustomEvent("favorites:changed"));
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error("addFavorite(direction) failed", e);
    }
  }
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
            <div className="row" style={{justifyContent: "space-between", alignItems: "flex-start", gap: 8}}>
              <h2 style={{marginTop: 0}}>方向 · {pack.chosen_direction.name}</h2>
              <button onClick={() => favoriteDirection(pack.chosen_direction)}
                title="收藏这个方向到「我的收藏库」，返回上层调整后也能拿回来"
                style={{
                  fontSize: 11.5, padding: "3px 10px", whiteSpace: "nowrap", flexShrink: 0,
                  background: dirFaved ? "var(--primary-soft)" : "#fff",
                  border: "1px solid #e0a800", color: "#b06200", fontWeight: 600,
                }}>
                {dirFaved ? "✓ 已收藏" : "⭐ 收藏方向"}
              </button>
            </div>
            <p style={{margin: "4px 0", fontSize: 14}}>{pack.chosen_direction.positioning_statement}</p>
            <p className="muted" style={{fontSize: 12}}>受众：{pack.chosen_direction.target_audience}</p>
          </>
        )}
        {pack.series_thesis && (
          <p style={{fontStyle: "italic", color: "var(--muted)", fontSize: 13, marginTop: 8}}>
            主线：{pack.series_thesis}
          </p>
        )}
        {/* 策略元信息行 ：冷热启动 / 内容形式偏好 / 周期 / 频率 / 平台 */}
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

      {(materials.length > 0 || (pack.benchmark_examples?.length ?? 0) > 0) && (
        <div className="card">
          <h2 style={{marginTop: 0}}>🎒 启动前要准备的材料</h2>
          {materials.length > 0 && (
            <ul style={{marginLeft: 20, lineHeight: 1.9}}>
              {materials.map((m, i) => <li key={i}>{m}</li>)}
            </ul>
          )}
          {/* v0.66 (item1) ：材料清单旁挂 1-5 篇真实图文对标帖 ─ 让用户照着图文
              效果准备素材，比纯文字「需要 X 图」直观得多。复用 RagReferenceGrid。 */}
          {(pack.benchmark_examples?.length ?? 0) > 0 && (
            <div style={{marginTop: 12}}>
              <RagReferenceGrid
                refs={pack.benchmark_examples || []}
                title="🎯 对标这几篇的图文效果来准备素材"
                subtitle="按这轮方向从你资源库里筛出的高赞真实帖（封面 / 排版 / 配图都可参照）。点封面或标题跳原帖看完整图文。"
                defaultOpen={true}
              />
            </div>
          )}
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

      {/* v0.66 (item5) ：成功指标 — 有两套方案时并排对比让用户选，
          否则回退旧的单列渲染（向后兼容旧 pack）。 */}
      {Array.isArray(pack.metrics_plans) && pack.metrics_plans.length > 0 ? (
        <MetricsPlansCard plans={pack.metrics_plans} />
      ) : metrics.length > 0 ? (
        <div className="card">
          <h2 style={{marginTop: 0}}>📈 成功指标</h2>
          <ul style={{marginLeft: 20, lineHeight: 1.9}}>
            {metrics.map((m, i) => <li key={i}>{m}</li>)}
          </ul>
        </div>
      ) : null}
    </>
  );
}

// v0.66 (item5) ：两套成功指标方案并排对比 + 选择。解决「每次 expand 出的指标
// 都不一样、没法对比」的痛点 —— 现在一次给两套（稳健/进取），用户点选一套高亮。
function MetricsPlansCard({plans}: {plans: { label: string; metrics: string[]; rationale?: string }[]}) {
  const [picked, setPicked] = useState(0);
  return (
    <div className="card">
      <h2 style={{marginTop: 0}}>📈 成功指标 · {plans.length} 套方案对比</h2>
      <p className="muted" style={{fontSize: 12, marginTop: -4, marginBottom: 10}}>
        按你的风险偏好选一套作为这轮起号的衡量标准 — 点卡片切换。
      </p>
      <div style={{display: "grid", gridTemplateColumns: `repeat(${Math.min(plans.length, 2)}, 1fr)`, gap: 12}}>
        {plans.map((p, i) => {
          const on = i === picked;
          return (
            <div key={i} onClick={() => setPicked(i)} role="button" tabIndex={0}
              onKeyDown={e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setPicked(i); } }}
              style={{
                cursor: "pointer", padding: "12px 14px", borderRadius: 8,
                border: on ? "2px solid var(--primary)" : "1px solid var(--border)",
                background: on ? "var(--primary-soft)" : "#fff",
              }}>
              <div className="row" style={{justifyContent: "space-between", alignItems: "center", marginBottom: 6}}>
                <span style={{fontWeight: 700, fontSize: 14, color: on ? "var(--primary)" : "var(--text)"}}>
                  {i === 0 ? "🛡️ " : i === 1 ? "🚀 " : "📌 "}{p.label}
                </span>
                {on && <span style={{fontSize: 11, fontWeight: 700, color: "var(--primary)"}}>✓ 已选</span>}
              </div>
              {p.rationale && (
                <div className="muted" style={{fontSize: 11.5, marginBottom: 8, fontStyle: "italic"}}>{p.rationale}</div>
              )}
              <ul style={{marginLeft: 18, lineHeight: 1.8, fontSize: 13}}>
                {p.metrics.map((m, j) => <li key={j}>{m}</li>)}
              </ul>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function SchedulePanel({pack, onWrite, onPackReload}: {
  pack: StrategyPackDTO;
  onWrite: (slotIdx: number, altIdx: number) => void;
  onPackReload?: () => void;
}) {
  const [expanded, setExpanded] = useState<number | null>(null);
  const [collapsed, setCollapsed] = useState(false);
  // Local optimistic copy so a single-slot regenerate updates inline
  // without re-fetching the whole pack.
  const initialSchedule = Array.isArray(pack.schedule) ? pack.schedule : [];
  const [schedule, setSchedule] = useState<any[]>(initialSchedule);
  useEffect(() => { setSchedule(Array.isArray(pack.schedule) ? pack.schedule : []); }, [pack]);
  const [regenIdx, setRegenIdx] = useState<number | null>(null);
  const [regenErr, setRegenErr] = useState<string | null>(null);
  // v0.66 (item3) ：每条 slot 的「按指令重出」输入框内容（按 slotIdx 存）。
  const [regenInstr, setRegenInstr] = useState<Record<number, string>>({});
  // v0.66 (item4) ：刚收藏的 slot idx（短暂显示「✓ 已收藏」反馈）。
  const [favedIdx, setFavedIdx] = useState<number | null>(null);

  async function favoriteSlot(i: number, slot: any) {
    try {
      await api.addFavorite("slot", slot, String(slot?.title || ""));
      setFavedIdx(i);
      setTimeout(() => setFavedIdx(p => (p === i ? null : p)), 1800);
      window.dispatchEvent(new CustomEvent("favorites:changed"));
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error("addFavorite failed", e);
    }
  }

  function isPlaceholder(s: any): boolean {
    const t = String(s?.title || "");
    return t.includes("AI 漏排") || t.startsWith("待补 #") || t.includes("请用 ✍️");
  }

  async function regen(i: number, instruction?: string) {
    setRegenIdx(i); setRegenErr(null);
    try {
      const r = await api.regenerateSlot(pack.pack_id, i,
        instruction ? { instruction } : undefined);
      setSchedule(prev => {
        const next = [...prev];
        next[i] = r.slot;
        return next;
      });
      // Refetch the canonical pack so a later parent re-render doesn't
      // clobber the optimistic update with the stale schedule array.
      onPackReload?.();
    } catch (e: any) {
      const msg = humaniseError(e);
      setRegenErr(msg || (e?.message ?? String(e)) || "重生成失败（未知原因）");
      // eslint-disable-next-line no-console
      console.error("regenerate_slot failed", e);
    } finally {
      setRegenIdx(null);
    }
  }
  const dirName = (pack.chosen_direction && pack.chosen_direction.name) || "";
  const cycleStart = pack.input.cycle_start_date || "";
  return (
    <div className="card" style={{borderLeft: "4px solid #a855f7", padding: "12px 14px"}}>
      <div className="spread" style={{alignItems: "flex-start"}}>
        <div style={{flex: 1, minWidth: 0}}>
          <h2 style={{margin: 0, fontSize: 15}}>
            📅 时间线 · {schedule.length} 篇 schedule
          </h2>
          <p className="muted" style={{fontSize: 12, margin: "4px 0 0"}}>
            策略包 #{pack.pack_id.slice(0, 8)} · 方向「{dirName}」
            · 点任一条选 「主推荐」 或 「次选」 → 跳到出稿板块多 agent 写
          </p>
          {/* v0.65 ：反黑盒图例 ─ 让用户看见 chip 含义 */}
          <div style={{
            fontSize: 11, color: "var(--muted)", marginTop: 6,
            padding: "4px 8px", background: "var(--primary-soft)", borderRadius: 4,
            display: "inline-block",
          }}>
            💡 每条 slot 旁边的
            <span style={{
              fontSize: 10.5, padding: "0 4px", margin: "0 3px",
              background: "var(--primary-soft)", color: "var(--primary)",
              fontWeight: 600, border: "1px solid var(--primary)", borderRadius: 3,
            }}>📚 N 篇参考</span>
            =「AI 写这条时实际看了 N 篇真实贴文」 ；
            <span style={{
              fontSize: 10.5, padding: "0 4px", margin: "0 3px",
              background: "#ecfdf5", color: "#15803d", fontWeight: 600, borderRadius: 3,
            }}>📍 N 锚点</span>
            =「AI 引用了 N 个 DNA 数据点（蓝海词 / hook / 时段 ...）」 ─ 全部可点验证。
          </div>
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
              这份 pack 的 schedule 为空，可能上一次 expand 出错。回出稿板块重跑。
            </div>
          )}
          {regenErr && (
            <div className="banner danger" onClick={() => setRegenErr(null)}
              style={{fontSize: 12, marginBottom: 4, cursor: "pointer"}}>
              重生成失败：{regenErr}（点关闭）
            </div>
          )}
          {schedule.map((s: any, i: number) => {
            const isExp = expanded === i;
            const dt = slotDate(cycleStart, s.week, s.day_of_week);
            const dateLabel = dt ? dt.display : `W${s.week}·D${s.day_of_week}`;
            const alts = Array.isArray(s.alternative_versions) ? s.alternative_versions : [];
            const placeholder = isPlaceholder(s);
            const isRegenerating = regenIdx === i;
            return (
              <div key={i} style={{
                border: placeholder
                  ? "1px solid #f6c265"
                  : (isExp ? "1px solid var(--primary)" : "1px solid #eee"),
                borderRadius: 6,
                background: placeholder
                  ? "#fffbf2"
                  : (isExp ? "var(--primary-soft)" : "#fff"),
              }}>
                <div className="row" style={{
                  padding: "6px 10px", gap: 8, alignItems: "center", cursor: "pointer",
                }} onClick={() => setExpanded(isExp ? null : i)}>
                  <span style={{
                    fontSize: 11, padding: "1px 6px",
                    background: placeholder ? "#b06200" : "var(--primary)",
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
                    color: placeholder ? "#b06200" : undefined,
                  }}>
                    {placeholder ? "⚠️ AI 漏排 · 点 🔁 让 AI 重新出这一篇" : (s.title || "(无标题)")}
                  </span>
                  {!placeholder && s.angle && (
                    <span className="tag-pill" style={{fontSize: 10.5, flexShrink: 0}}>{s.angle}</span>
                  )}
                  {!placeholder && s.content_format && (
                    <span className="tag-pill" style={{fontSize: 10.5, flexShrink: 0}}>
                      {s.content_format}
                    </span>
                  )}
                  {!placeholder && alts.length > 0 && (
                    <span className="muted" style={{fontSize: 10.5, flexShrink: 0}}>
                      + {alts.length} 备选
                    </span>
                  )}
                  {/* v0.65 ：collapsed 状态也能看到「AI 看了 N 篇」+「引用 N 处」chip，
                      让用户在 schedule 顶层就感知到哪些 slot 有数据锚定。 */}
                  {!placeholder && Array.isArray(s.rag_refs) && s.rag_refs.length > 0 && (
                    <span title={`AI 写这条时实际看了 ${s.rag_refs.length} 篇真实贴文`}
                      style={{
                        fontSize: 10.5, padding: "1px 6px", borderRadius: 3,
                        background: "var(--primary-soft)", color: "var(--primary)",
                        fontWeight: 600, flexShrink: 0, cursor: "help",
                      }}>
                      📚 {s.rag_refs.length} 篇参考
                    </span>
                  )}
                  {!placeholder && Array.isArray(s.decision_anchors) && s.decision_anchors.length > 0 && (
                    <span title="决策锚点 ：AI 引用的 DNA 数据点（蓝海词/hook/tag/评论）"
                      style={{
                        fontSize: 10.5, padding: "1px 6px", borderRadius: 3,
                        background: "#ecfdf5", color: "#15803d", fontWeight: 600,
                        flexShrink: 0, cursor: "help",
                      }}>
                      📍 {s.decision_anchors.length} 锚点
                    </span>
                  )}
                  {placeholder && (
                    <button onClick={(e) => { e.stopPropagation(); regen(i); }}
                      disabled={isRegenerating}
                      style={{
                        fontSize: 11.5, padding: "3px 10px", flexShrink: 0,
                        background: "#fff", border: "1px solid #b06200",
                        color: "#b06200", fontWeight: 600,
                      }}>
                      {isRegenerating ? "🤖 重生成中…" : "🔁 让 AI 重新出"}
                    </button>
                  )}
                  <span style={{fontSize: 11, color: "var(--muted)", flexShrink: 0}}>{isExp ? "▴" : "▾"}</span>
                </div>
                {isExp && (
                  <div style={{padding: "0 10px 10px"}}>
                    {/* v0.66 (item3) ：按指令单独调这一条 — 解决「这条太重叠/太拖沓，
                        我想只改它」。留空 = 换一条；填指令 = 按我的话改。 */}
                    <div onClick={(e) => e.stopPropagation()}
                      className="row" style={{gap: 6, alignItems: "center", margin: "6px 0 4px"}}>
                      <input
                        value={regenInstr[i] || ""}
                        onChange={(e) => setRegenInstr(prev => ({...prev, [i]: e.target.value}))}
                        placeholder="想怎么改这一条？例：太拖沓压缩成3段 / 换更冲突的hook / 改成测评角度（留空=换一条）"
                        style={{flex: 1, fontSize: 12, padding: "5px 8px"}}
                      />
                      <button onClick={() => regen(i, (regenInstr[i] || "").trim() || undefined)}
                        disabled={isRegenerating}
                        style={{
                          fontSize: 11.5, padding: "5px 10px", whiteSpace: "nowrap",
                          background: "#fff", border: "1px solid var(--primary)",
                          color: "var(--primary)", fontWeight: 600, flexShrink: 0,
                        }}>
                        {isRegenerating ? "🤖 重出中…" : "🔁 按指令重出"}
                      </button>
                      {/* v0.66 (item4) ：收藏这条 slot 到「我的收藏库」供之后复用。 */}
                      <button onClick={() => favoriteSlot(i, s)}
                        title="收藏这条选题到「我的收藏库」，之后可复用"
                        style={{
                          fontSize: 11.5, padding: "5px 10px", whiteSpace: "nowrap",
                          background: favedIdx === i ? "var(--primary-soft)" : "#fff",
                          border: "1px solid #e0a800", color: "#b06200",
                          fontWeight: 600, flexShrink: 0,
                        }}>
                        {favedIdx === i ? "✓ 已收藏" : "⭐ 收藏"}
                      </button>
                    </div>
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
                          {Array.isArray(s.decision_anchors) && s.decision_anchors.length > 0 && (
                            <AnchorChips anchors={s.decision_anchors} prefix="📍 引用" />
                          )}
                          {/* v0.65 ：删除 per-slot publish_rationale + publish_anchors，
                              用户嫌每条都写一行时段太冗余 ─ 这些只放 ⬆ 顶部 TopPublishingSlotsCard
                              （全库 Top 5 时段表）一处。per-slot 不再显示。 */}
                          {/* v0.65 (P4) ：slot 锚定度 + body_draft 数据锚定视图 */}
                          {(s.grounding_score != null || s.body_draft) && (
                            <div style={{marginTop: 6, display: "flex", gap: 6, alignItems: "center"}}>
                              {s.grounding_score != null && (
                                <GroundingChip score={s.grounding_score} breakdown={s.grounding_breakdown} compact />
                              )}
                              {s.kpi_baseline?.median > 0 && (
                                <span title={`同 hook_type 在本库 ：中位 ${s.kpi_baseline.median} · P90 ${s.kpi_baseline.p90 || "—"} · n=${s.kpi_baseline.n}`}
                                  style={{fontSize: 10.5, color: "var(--muted)", cursor: "help"}}>
                                  基线 ：中位 {s.kpi_baseline.median} · n={s.kpi_baseline.n}
                                </span>
                              )}
                            </div>
                          )}
                          {s.body_draft && Array.isArray(s.rag_refs) && s.rag_refs.length > 0 && /\[ref:/.test(s.body_draft) && (
                            <details style={{marginTop: 6}} open>
                              <summary style={{cursor: "pointer", fontSize: 11.5, color: "var(--primary)", fontWeight: 600}}>
                                🔗 正文数据锚定视图（{(s.body_draft.match(/\[ref:[A-Za-z0-9_\-]+\]/g) || []).length} 处引用 · 点 chip 跳来源）
                              </summary>
                              <GroundedBody text={s.body_draft} refs={s.rag_refs}
                                style={{fontSize: 12.5, marginTop: 6, padding: "8px 10px",
                                        background: "#fafafa", borderRadius: 4,
                                        border: "1px dashed var(--primary)"}} />
                            </details>
                          )}
                          {/* v0.65 ：flexible_window 也删 ─ 见上方 TopPublishingSlotsCard 表。 */}
                        </div>
                        <button onClick={(e) => { e.stopPropagation(); onWrite(i, -1); }}
                          style={{whiteSpace: "nowrap", fontSize: 12, padding: "4px 10px"}}>
                          ✍️ 写这个 →
                        </button>
                      </div>
                    </div>
                    {/* Skip placeholder slots — no usable title query. */}
                    {!placeholder && (
                      <SlotRagPanel
                        slot={s}
                        slotIdx={i}
                        packId={pack.pack_id}
                        altIdx={-1}
                      />
                    )}
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
                          <button onClick={(e) => { e.stopPropagation(); onWrite(i, ai); }}
                            className="ghost"
                            style={{
                              whiteSpace: "nowrap", fontSize: 12, padding: "4px 10px",
                              borderColor: "#a855f7", color: "#a855f7",
                            }}>
                            ✍️ 写这个 →
                          </button>
                        </div>
                        {(alt.title || alt.angle) && (
                          <SlotRagPanel
                            slot={alt}
                            slotIdx={i}
                            packId={pack.pack_id}
                            altIdx={ai}
                          />
                        )}
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
        setTop(topPublishingSlots(heatmap, 8, 5));   // 拉 Top 8 让用户看到层次
      } catch (e: any) {
        if (!cancel) setErr(e.message || String(e));
      }
    })();
    return () => { cancel = true; };
  }, []);

  if (err || top.length === 0) return null;
  // v0.65 ：把 Top 8 时段按中位赞高低分成 ⭐⭐⭐ 强推荐 (前 3) / ⭐⭐ 次选 (4-6) /
  // ⭐ 备选 (7-8)，让用户一眼看到「强推荐」和「次选」 ，而不是 5 个并列的卡片。
  // 文字说明 ：「优先发 X / Y / Z 这 3 个时段；如果错过 ，A / B / C 也可以」。
  const strong = top.slice(0, 3);
  const fair = top.slice(3, 6);
  const weak = top.slice(6, 8);
  const renderTier = (
    cells: typeof top, label: string, accent: string, icon: string,
  ) => cells.length > 0 && (
    <div style={{marginBottom: 10}}>
      <div style={{fontSize: 12, fontWeight: 700, color: accent, marginBottom: 4}}>
        {icon} {label}
      </div>
      <div style={{display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))", gap: 6}}>
        {cells.map((cell, i) => (
          <div key={i} style={{
            padding: 8, background: "#fff", borderRadius: 6,
            border: `1px solid ${accent}33`, textAlign: "center",
          }}>
            <div style={{fontSize: 13, fontWeight: 600}}>{cell.label}</div>
            <div className="muted" style={{fontSize: 11, marginTop: 3}}>
              中位赞 <b style={{color: "#333"}}>{Math.round(cell.median_likes)}</b>
              <span style={{opacity: 0.6}}> · n={cell.count}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
  // 一句话推荐 ：把 Top 3 拼成文字
  const recommendStr = strong.map(c => c.label).join(" / ");
  const fallbackStr = fair.map(c => c.label).join(" / ");
  return (
    <div className="card" style={{
      background: "linear-gradient(180deg, #fff8e6 0%, #fff 100%)",
      borderColor: "#fde2a3",
    }}>
      <h2 style={{marginTop: 0}}>📊 本账号最佳发布时段（来自本库 DNA 热力图）</h2>
      <p style={{fontSize: 12.5, marginTop: 2, marginBottom: 10, lineHeight: 1.7}}>
        <b>💡 推荐节奏</b> ：优先发 <b style={{color: "#a67700"}}>{recommendStr || "（暂无数据）"}</b>
        {fallbackStr && <>，错过就发 <b style={{color: "#7a6a40"}}>{fallbackStr}</b></>}。
        <br />
        <span className="muted" style={{fontSize: 11.5}}>
          按本库高赞笔记的 (周几, 小时) 中位互动量排序。具体每条 slot 不再单独标时段 —
          按下方推荐时段表自己排即可。
        </span>
      </p>
      {renderTier(strong, "⭐⭐⭐ 强推荐 Top 3（首选）", "#a67700", "🟢")}
      {renderTier(fair,   "⭐⭐ 次选 (4-6)", "#7a6a40", "🟡")}
      {renderTier(weak,   "⭐ 备选 (7-8)", "#888", "⚪")}
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
    api.listStrategyPerformance(pack.pack_id).then(setHistory)
      .catch(e => console.error("[IterateCard] listStrategyPerformance", e));
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

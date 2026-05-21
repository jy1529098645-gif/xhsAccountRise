// v0.62.5 ：StrategyPackView — 一个 pack 的「时间线大纲」纯展示页。
//
// 板块拆分后，「起号策略」（/strategy/:pack_id）渲染这个组件，
// 用户在这里浏览：
//   - 方向卡 + 文字策略指导（主线 / 元信息 / 主题 / 材料 / 风险 / 指标）
//   - 时间线 schedule（每篇主推荐 + 2 个备选）
//   - 下一轮迭代入口
// 点 「✍️ 写这个 →」 直接跳 /composer?slot=PACK_ID:IDX&alt=N 进出稿。
//
// 之前曾搬到 Composer.tsx，这次抽成独立组件供 Strategy 板块使用。

import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../../api";
import { slotDate, topPublishingSlots } from "../../format";
import PlatformPill from "../PlatformPill";
import { humaniseError } from "../../errors";
import type { StrategyPackDTO } from "../../types";

const DIRECTION_COLORS = ["#2E5C8A", "#a36df0", "#10a37f", "#e0a800", "#c4429a", "#5BC0EB", "#FCB97D", "#7a6fc8"];
const INTENT_COLORS: Record<string, string> = {
  "拉新": "#fff5f5", "互动": "#fff8e6", "转化": "#fdecea", "沉淀": "#f0fafe",
};

/** Top-level composite: a full pack viewer.
 *
 *  v0.62.5 ：onWriteClick 可选。Strategy 板块（/strategy/:pack_id）不传 ：
 *  默认行为是 navigate('/composer?slot=PACK:IDX&alt=N')。Composer 板块
 *  传 ：用 callback 在当前页填表单（不跳路由）。
 */
export default function StrategyPackView({pack, onWriteClick, compact = false, onPackReload}: {
  pack: StrategyPackDTO;
  onWriteClick?: (slotIdx: number, altIdx: number) => void;
  /** Bug B fix ：compact=true (Composer 板块嵌入) 只渲染 pack ID 摘要 +
   *  SchedulePanel，**省略** Overview / 周主题 / 最佳时段 / 材料 / 风险 /
   *  指标 / IterateCard — 那些都是「起号策略板块」的内容。 */
  compact?: boolean;
  /** v0.63: 占位 slot 被 regenerate_slot 改完后通知父刷新整个 pack，
   *  避免 SchedulePanel 的 local schedule state 被父 re-render 冲掉。 */
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
      <SchedulePanel pack={pack} onWrite={goWrite} onPackReload={onPackReload} />
      <IterateCard pack={pack} />
    </div>
  );
}

function StrategyOverview({pack}: {pack: StrategyPackDTO}) {
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
            <h2 style={{marginTop: 0}}>方向 · {pack.chosen_direction.name}</h2>
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

      {materials.length > 0 && (
        <div className="card">
          <h2 style={{marginTop: 0}}>🎒 启动前要准备的材料</h2>
          <ul style={{marginLeft: 20, lineHeight: 1.9}}>
            {materials.map((m, i) => <li key={i}>{m}</li>)}
          </ul>
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

      {metrics.length > 0 && (
        <div className="card">
          <h2 style={{marginTop: 0}}>📈 成功指标</h2>
          <ul style={{marginLeft: 20, lineHeight: 1.9}}>
            {metrics.map((m, i) => <li key={i}>{m}</li>)}
          </ul>
        </div>
      )}
    </>
  );
}

function SchedulePanel({pack, onWrite, onPackReload}: {
  pack: StrategyPackDTO;
  onWrite: (slotIdx: number, altIdx: number) => void;
  onPackReload?: () => void;
}) {
  const [expanded, setExpanded] = useState<number | null>(null);
  const [collapsed, setCollapsed] = useState(false);
  // v0.63: 本地 schedule 副本支持单 slot 重生成后 inline 替换。每次 pack
  // 变化时同步；regenerate_slot 成功后只更新这里，无需重新拉整个 pack。
  const initialSchedule = Array.isArray(pack.schedule) ? pack.schedule : [];
  const [schedule, setSchedule] = useState<any[]>(initialSchedule);
  useEffect(() => { setSchedule(Array.isArray(pack.schedule) ? pack.schedule : []); }, [pack]);
  const [regenIdx, setRegenIdx] = useState<number | null>(null);
  const [regenErr, setRegenErr] = useState<string | null>(null);

  // v0.63: 判断一个 slot 是不是 gap-fill 占位（AI 实际没排出来的占位行）
  function isPlaceholder(s: any): boolean {
    const t = String(s?.title || "");
    return t.includes("AI 漏排") || t.startsWith("待补 #") || t.includes("请用 ✍️");
  }

  async function regen(i: number) {
    setRegenIdx(i); setRegenErr(null);
    try {
      const r = await api.regenerateSlot(pack.pack_id, i);
      // v0.63: update local state for immediate visual feedback...
      setSchedule(prev => {
        const next = [...prev];
        next[i] = r.slot;
        return next;
      });
      // ...AND ask parent to refetch the full pack so the canonical source
      // of truth syncs (otherwise a later parent re-render could clobber
      // our local update with the stale `pack.schedule` array).
      onPackReload?.();
    } catch (e: any) {
      // v0.63: surface the real error so user doesn't think the click did
      // nothing. Common causes: LLM API down/no key, rate limit, all
      // 2 LLMs returning empty schedule (very rare with our cross-family
      // fallback but possible if both providers are down).
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
            // v0.63: placeholder slot = AI scheduler 没排出来时填的占位行
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
                          {s.publish_rationale && (
                            <div className="muted" style={{fontSize: 11, marginTop: 2, fontStyle: "italic"}}>
                              ⏰ {s.publish_rationale}
                            </div>
                          )}
                          {s.flexible_window && (
                            <div className="muted" style={{fontSize: 11, marginTop: 2, fontStyle: "italic"}}>
                              🗓️ 推荐窗口 ：{s.flexible_window}
                            </div>
                          )}
                        </div>
                        <button onClick={(e) => { e.stopPropagation(); onWrite(i, -1); }}
                          style={{whiteSpace: "nowrap", fontSize: 12, padding: "4px 10px"}}>
                          ✍️ 写这个 →
                        </button>
                      </div>
                    </div>
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
        setTop(topPublishingSlots(heatmap, 5, 5));
      } catch (e: any) {
        if (!cancel) setErr(e.message || String(e));
      }
    })();
    return () => { cancel = true; };
  }, []);

  if (err || top.length === 0) return null;
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
            <div style={{fontSize: 11, color: "#a67700", fontWeight: 600}}>#{i + 1}</div>
            <div style={{fontSize: 14, fontWeight: 700, marginTop: 4}}>{cell.label}</div>
            <div className="muted" style={{fontSize: 11, marginTop: 4}}>
              中位赞 <b style={{color: "#333"}}>{Math.round(cell.median_likes)}</b>
            </div>
            <div className="muted" style={{fontSize: 10}}>（n={cell.count}）</div>
          </div>
        ))}
      </div>
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

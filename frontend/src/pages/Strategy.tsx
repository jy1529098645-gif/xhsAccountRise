// v0.62.6 ：起号策略板块（第 2 步，分析报告 → 起号策略 → 出稿）。
//
// 这个板块**只做一件事** ：给用户看时间线大纲。
//   • 文字策略指导 ：方向 / 主线 / 周主题 / 材料 / 风险 / 指标
//   • 每日所需信息 ：日期 / 冷热启动 / 风格 / 主题 / 图文视频 / 时段
//   • 每日多方案 ：主推荐 + 2 个备选（不同时段/角度/格式）
//   • 「✍️ 写这个 →」 跳出稿板块 (/composer?slot=PACK:IDX&alt=N)
//
// 路由 ：
//   /strategy            默认显示最新 pack 的大纲 + 顶部 pack 切换器
//   /strategy/{pack_id}  显示指定 pack 的大纲
//
// **没有任何创建动作** — 创建 pack 在出稿板块用 wizard 完成。
// **没有任何写正文动作** — 写正文在出稿板块多 agent 完成。

import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { fmtRelative } from "../format";
import PlatformPill from "../components/PlatformPill";
import { humaniseError } from "../errors";
import StrategyPackView from "../components/strategy/StrategyPackView";
import type { StrategyPackDTO, StrategyListItem } from "../types";

export default function Strategy() {
  const { packId: urlPackId } = useParams<{ packId?: string }>();
  return <StrategyPage explicitPackId={urlPackId} />;
}

/** 板块主页 ：默认拿最新 pack 来显示 ；URL 有 pack_id 用那个。
 *  顶部带一个 pack 切换器，让用户在多个 pack 之间切。 */
function StrategyPage({ explicitPackId }: { explicitPackId?: string }) {
  const navigate = useNavigate();
  const [history, setHistory] = useState<StrategyListItem[]>([]);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [pack, setPack] = useState<StrategyPackDTO | null>(null);
  const [packLoading, setPackLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // 1) 先拉 history（用来决定哪个 pack 默认显示 + 渲染切换器）
  useEffect(() => {
    let cancel = false;
    (async () => {
      try {
        const list = await api.listStrategies();
        if (!cancel) setHistory(list);
      } catch (e: any) {
        if (!cancel) setErr(humaniseError(e));
      } finally {
        if (!cancel) setHistoryLoading(false);
      }
    })();
    return () => { cancel = true; };
  }, []);

  // 2) 决定显示哪个 pack ：URL > 最新一个 expanded 的 pack > 第一个 > 啥也没
  const expandedPacks = history.filter(p => p.status === "expanded");
  const targetPackId = explicitPackId
    || (expandedPacks[0]?.pack_id)
    || (history[0]?.pack_id)
    || null;

  // 3) 拉指定 pack 的详情
  useEffect(() => {
    if (!targetPackId) { setPack(null); return; }
    let cancel = false;
    setPackLoading(true);
    (async () => {
      try {
        const d = await api.getStrategy(targetPackId);
        if (cancel) return;
        if (d.pack) {
          setPack(d.pack);
        } else {
          setPack(null);
          setErr("这个 pack 还没生成完成 — 去出稿板块继续 4 步流程");
        }
      } catch (e: any) {
        if (!cancel) setErr(humaniseError(e));
      } finally {
        if (!cancel) setPackLoading(false);
      }
    })();
    return () => { cancel = true; };
  }, [targetPackId]);

  async function handleDelete(id: string, name: string) {
    if (!confirm(`确定删除 pack 「${name}」？此操作无法撤销。`)) return;
    try {
      await api.deleteStrategy(id);
      setHistory(h => h.filter(p => p.pack_id !== id));
      // 如果删的是当前显示的，跳回 /strategy 让 effect 重选默认 pack
      if (targetPackId === id) navigate("/strategy", { replace: true });
    } catch (e: any) {
      alert(humaniseError(e));
    }
  }

  // No pack at all → onboarding
  if (!historyLoading && history.length === 0) {
    return (
      <div>
        <div className="page-header">
          <h1>🚀 起号策略</h1>
          <p>时间线大纲 + 每日多方案备选 + 文字策略指导 — 进出稿前先看清整盘节奏</p>
        </div>
        <div className="card" style={{textAlign: "center", padding: 48}}>
          <div style={{fontSize: 48, marginBottom: 12}}>📭</div>
          <h2 style={{margin: 0}}>还没有任何起号策略 pack</h2>
          <p className="muted" style={{fontSize: 13, marginTop: 6}}>
            策略 pack 在出稿板块创建 — 选目标 → 填输入 → 选方向 → 自动排期，一气呵成。
            <br />回到这里就能看完整时间线大纲 + 每日多方案。
          </p>
          <button onClick={() => navigate("/composer")}
            style={{marginTop: 18, fontSize: 14, padding: "10px 20px", fontWeight: 600}}>
            ✍️ 去出稿创建第一份 pack →
          </button>
        </div>
      </div>
    );
  }

  return (
    <div>
      <div className="page-header">
        <h1>🚀 起号策略 · 时间线大纲</h1>
        <p>看大局 · 每天 AI 给主推荐 + 2 个备选 · 点哪条进出稿板块写哪条</p>
      </div>

      {/* pack 切换器 — 当前显示哪个 pack，下拉切其它 */}
      {history.length > 0 && (
        <div className="card" style={{padding: "10px 14px", marginBottom: 12}}>
          <div className="row" style={{gap: 10, alignItems: "center", flexWrap: "wrap"}}>
            <span style={{fontSize: 13, fontWeight: 600}}>📦 当前 pack ：</span>
            <select value={targetPackId || ""}
              onChange={(e) => {
                const v = e.target.value;
                if (v) navigate(`/strategy/${v}`);
              }}
              style={{padding: "4px 10px", fontSize: 12.5, flex: 1, minWidth: 200, maxWidth: 500}}>
              {history.map(p => {
                const dt = p.created_at ? fmtRelative(p.created_at) : "";
                const label = (p.input?.positioning || `(未填定位)`).slice(0, 40);
                const statusBadge = p.status === "expanded" ? "✓ 已排期"
                  : p.status === "expanding" ? "⏳ 排期中"
                  : p.status === "directions" ? "✏️ 方向就绪" : p.status;
                return (
                  <option key={p.pack_id} value={p.pack_id}>
                    [{statusBadge}] {label} · {dt}
                  </option>
                );
              })}
            </select>
            {targetPackId && (
              <button className="ghost" style={{fontSize: 11.5, padding: "3px 8px", color: "#c53030"}}
                onClick={() => {
                  const p = history.find(x => x.pack_id === targetPackId);
                  handleDelete(targetPackId, p?.input?.positioning?.slice(0, 30) || targetPackId.slice(0, 8));
                }}>
                ✕ 删除此 pack
              </button>
            )}
            <Link to="/composer">
              <button style={{fontSize: 12, padding: "4px 12px", whiteSpace: "nowrap"}}>
                + 出稿建新 pack
              </button>
            </Link>
          </div>
          {targetPackId && (() => {
            const p = history.find(x => x.pack_id === targetPackId);
            if (!p) return null;
            return (
              <div className="muted" style={{fontSize: 11, marginTop: 6, display: "flex", gap: 12, flexWrap: "wrap"}}>
                {p.platform && <><PlatformPill platform={p.platform} /> ·</>}
                {p.input?.cycle_weeks ? `${p.input.cycle_weeks} 周` : ""}
                {p.input?.posts_per_week ? ` · 每周 ${p.input.posts_per_week} 篇` : ""}
                {p.created_at ? ` · 创建于 ${fmtRelative(p.created_at)}` : ""}
              </div>
            );
          })()}
        </div>
      )}

      {(packLoading || historyLoading) && (
        <div className="card" style={{textAlign: "center", padding: 32}}>
          <div className="muted">读取中…</div>
        </div>
      )}
      {err && !packLoading && (
        <div className="card">
          <div className="banner danger">{err}</div>
          <button onClick={() => navigate("/composer")} style={{marginTop: 12}}>
            ✍️ 去出稿板块继续 / 新建
          </button>
        </div>
      )}
      {pack && !err && <StrategyPackView pack={pack} />}
    </div>
  );
}

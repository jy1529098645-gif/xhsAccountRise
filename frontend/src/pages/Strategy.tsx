// v0.62.8 ：起号策略板块（第 2 步）— 创建 pack + 看大纲，全流程闭环。
//
// 板块依赖关系（终于对了）：
//   分析报告 (1) → 起号策略 (2) → 出稿 (3) → 复盘 (4)
//
// 起号策略板块 = **前置阶段**，输出一份带时间线 + 多方案备选的 pack。
// 出稿板块 = **后置阶段**，消费 pack 去多 agent 写每篇正文。
//
// 路由 ：
//   /strategy             默认 ：有 pack → 看最新 ；无 pack → 进 wizard
//   /strategy/new         强制进 wizard（即使有 pack 也允许新建）
//   /strategy/{pack_id}   看指定 pack
//
// wizard（4 步 ：目标 → 输入 → 方向 → 排期）内容描述账号的 ：
//   平台 / 目的 / 详细描述 / 受众 / 强项 / 约束 / 周期 / 频率 / 启动方式 / 内容形式
// 全部依赖分析报告 + DNA 数据驱动，AI 据此生成 timeline + 文字策略指导。
//
// pack 视图（StrategyPackView）显示 ：方向卡 + 文字策略大纲 + 30 条时间线
// + 每条主推荐 + 2 个备选 + 「✍️ 写这个 →」跳 /composer 写正文。

import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { fmtRelative } from "../format";
import PlatformPill from "../components/PlatformPill";
import { humaniseError } from "../errors";
import StrategyWizard from "../components/strategy/StrategyWizard";
import StrategyPackView from "../components/strategy/StrategyPackView";
import type { StrategyPackDTO, StrategyListItem } from "../types";

export default function Strategy() {
  const { packId: urlPackId } = useParams<{ packId?: string }>();
  return <StrategyPage explicitPackId={urlPackId} />;
}

function StrategyPage({ explicitPackId }: { explicitPackId?: string }) {
  const navigate = useNavigate();
  const [history, setHistory] = useState<StrategyListItem[]>([]);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [pack, setPack] = useState<StrategyPackDTO | null>(null);
  const [packLoading, setPackLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // 是否处于「新建模式」：覆盖 PackView 渲染 wizard。
  // 触发条件 ：URL = /strategy/new ；或用户点「+ 新建另一份 pack」按钮 ；
  // 或完全没有 pack 历史（首次用户）。
  const [createMode, setCreateMode] = useState(explicitPackId === "new");

  // 1) 拉 history（决定默认 pack + 切换器选项 + 是否首次用户）
  useEffect(() => {
    let cancel = false;
    (async () => {
      try {
        const list = await api.listStrategies();
        if (!cancel) {
          setHistory(list);
          // 没有任何 pack → 直接进 wizard（首次用户体验）
          if (list.length === 0 && explicitPackId !== "new") setCreateMode(true);
        }
      } catch (e: any) {
        if (!cancel) setErr(humaniseError(e));
      } finally {
        if (!cancel) setHistoryLoading(false);
      }
    })();
    return () => { cancel = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // URL ↔ createMode 双向同步 ：
  //   /strategy/new           → createMode = true（强制 wizard）
  //   /strategy/{real_id}     → createMode = false（看 pack，Bug A 修复）
  //   /strategy（无 packId）   → 保持 createMode（既不强进 wizard 也不退出）
  // 另外 ：sessionStorage 有 strategy.briefPrefill（从 InsightReport / Retrospective
  // 跳过来的）→ 强制 wizard（用户意图就是新建）。
  useEffect(() => {
    if (explicitPackId === "new") {
      setCreateMode(true);
    } else if (explicitPackId && explicitPackId !== "new") {
      setCreateMode(false);
    }
  }, [explicitPackId]);

  // Bug E ：从分析报告 / 复盘跳进来时带的 briefPrefill stash — 强制进 wizard。
  // 旧默认（v0.62.8 之前）会显示 PackView 把 stash 漏掉。
  useEffect(() => {
    try {
      const raw = sessionStorage.getItem("strategy.briefPrefill");
      if (raw && !createMode) setCreateMode(true);
    } catch { /* sessionStorage may be disabled */ }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 2) 决定默认显示哪个 pack
  const expandedPacks = history.filter(p => p.status === "expanded");
  const targetPackId = (!createMode && explicitPackId !== "new")
    ? (explicitPackId || expandedPacks[0]?.pack_id || history[0]?.pack_id || null)
    : null;

  // 3) 拉指定 pack 详情
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
          setErr("这个 pack 还没生成完成 — 重跑或换一个");
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
      if (targetPackId === id) navigate("/strategy", { replace: true });
    } catch (e: any) {
      alert(humaniseError(e));
    }
  }

  // v0.62.8 ：wizard 完成 = pack 已 expanded。导航到该 pack，离开新建模式。
  function handleWizardPackReady(pid: string) {
    setCreateMode(false);
    // 重新拉一次 history 让切换器显示新 pack
    api.listStrategies().then(setHistory).catch(() => {});
    navigate(`/strategy/${pid}`, { replace: true });
  }

  // ─── 新建模式 ：渲染 wizard ──────────────────────────────────
  if (createMode) {
    return (
      <div>
        <div className="page-header" style={{display: "flex", justifyContent: "space-between", alignItems: "flex-start"}}>
          <div>
            <h1>🚀 起号策略 · 创建新 pack</h1>
            <p>详细描述账号情况 → AI 据分析报告 + DNA 生成时间线大纲 + 多方案备选</p>
          </div>
          {history.length > 0 && (
            <button className="ghost" onClick={() => {
              setCreateMode(false);
              navigate("/strategy", { replace: true });
            }} style={{fontSize: 12.5, padding: "6px 12px"}}>
              ← 取消，看现有 pack
            </button>
          )}
        </div>
        <StrategyWizard onPackReady={handleWizardPackReady} />
      </div>
    );
  }

  // ─── 浏览模式 ：渲染 PackView ──────────────────────────────────
  return (
    <div>
      <div className="page-header">
        <h1>🚀 起号策略 · 时间线大纲</h1>
        <p>详细策略 + 每日多方案 · 选哪条进出稿板块写哪条</p>
      </div>

      {/* pack 切换器 + 新建按钮 */}
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
            <button onClick={() => {
              setCreateMode(true);
              navigate("/strategy/new", { replace: true });
            }} style={{fontSize: 12, padding: "4px 12px", whiteSpace: "nowrap"}}>
              + 新建另一份 pack
            </button>
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
          <button onClick={() => {
            setCreateMode(true);
            navigate("/strategy/new", { replace: true });
          }} style={{marginTop: 12}}>
            🚀 新建一份 pack
          </button>
        </div>
      )}
      {pack && !err && (
        <>
          <StrategyPackView pack={pack} />
          {/* 底部 ：明确「下一步去出稿」CTA — 让用户清楚两板块的依赖关系 */}
          <div className="card" style={{borderTop: "3px solid var(--ok)", marginTop: 14}}>
            <div className="row" style={{justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap"}}>
              <div>
                <h2 style={{margin: 0}}>📦 这份 pack 准备好了 → 去出稿写每篇</h2>
                <p className="muted" style={{fontSize: 12, margin: "4px 0 0"}}>
                  上面 schedule 里每条都有「✍️ 写这个 →」按钮可单独跳。也可以直接进出稿板块挑。
                </p>
              </div>
              <Link to="/composer">
                <button style={{fontSize: 14, padding: "10px 18px", whiteSpace: "nowrap", fontWeight: 600}}>
                  ✍️ 去出稿板块 →
                </button>
              </Link>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

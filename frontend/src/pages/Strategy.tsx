// v0.62.5 ：起号策略板块 — 只剩「pack 浏览」+「pack 历史列表」。
//
// 板块大重构后 ：
//   • 创建一个新 pack 的「目标 / 输入 / 方向 / 排期」4 步 wizard 搬去
//     出稿板块 (/composer) 默认展开 — 用户在那里一站式生成 + 写正文。
//   • 起号策略板块只保留「新增加的功能」：
//       - /strategy           显示所有 pack 历史列表
//       - /strategy/{pack_id} 显示该 pack 的时间线大纲（StrategyPackView）：
//         方向卡 + 文字策略指导 + 时间线 schedule + 主推荐 + 2 备选 picker
//         + 「✍️ 写这个 →」 跳 /composer?slot=...
//
// 旧实现的整 ~1700 行 wizard 代码已搬到 components/strategy/StrategyWizard.tsx。

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
  if (urlPackId) return <StrategyPackPage packId={urlPackId} />;
  return <StrategyHistoryPage />;
}

/** /strategy/:pack_id — 单 pack 大纲浏览 */
function StrategyPackPage({ packId }: { packId: string }) {
  const navigate = useNavigate();
  const [pack, setPack] = useState<StrategyPackDTO | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancel = false;
    (async () => {
      setLoading(true); setErr(null);
      try {
        const d = await api.getStrategy(packId);
        if (cancel) return;
        if (d.pack) {
          setPack(d.pack);
        } else {
          setErr("这个 pack 还没生成完成 — 回出稿板块继续 4 步流程");
        }
      } catch (e: any) {
        if (!cancel) setErr(humaniseError(e));
      } finally {
        if (!cancel) setLoading(false);
      }
    })();
    return () => { cancel = true; };
  }, [packId]);

  return (
    <div>
      <div className="page-header" style={{display: "flex", justifyContent: "space-between", alignItems: "flex-start"}}>
        <div>
          <h1>📋 起号策略 · 时间线大纲</h1>
          <p>这是 pack 的纯展示页 — 看大局、对比方案、选哪条进出稿。</p>
        </div>
        <Link to="/strategy" className="ghost"
          style={{padding: "6px 12px", border: "1px solid var(--border)", borderRadius: 6, fontSize: 13, whiteSpace: "nowrap"}}>
          ← 所有 pack
        </Link>
      </div>

      {loading && (
        <div className="card" style={{textAlign: "center", padding: 32}}>
          <div className="muted">读取中…</div>
        </div>
      )}
      {err && !loading && (
        <div className="card">
          <div className="banner danger">{err}</div>
          <button onClick={() => navigate("/composer")}
            style={{marginTop: 12}}>
            ✍️ 去出稿板块继续 / 新建
          </button>
        </div>
      )}
      {pack && !err && <StrategyPackView pack={pack} />}
    </div>
  );
}

/** /strategy — pack 历史列表 */
function StrategyHistoryPage() {
  const navigate = useNavigate();
  const [history, setHistory] = useState<StrategyListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancel = false;
    (async () => {
      try {
        const list = await api.listStrategies();
        if (!cancel) setHistory(list);
      } catch (e: any) {
        if (!cancel) setErr(humaniseError(e));
      } finally {
        if (!cancel) setLoading(false);
      }
    })();
    return () => { cancel = true; };
  }, []);

  async function handleDelete(id: string, name: string) {
    if (!confirm(`确定删除 pack 「${name}」？此操作无法撤销。`)) return;
    try {
      await api.deleteStrategy(id);
      setHistory(h => h.filter(p => p.pack_id !== id));
    } catch (e: any) {
      alert(humaniseError(e));
    }
  }

  return (
    <div>
      <div className="page-header" style={{display: "flex", justifyContent: "space-between", alignItems: "flex-start"}}>
        <div>
          <h1>📋 起号策略</h1>
          <p>所有 pack 的时间线大纲在这查 · 创建新 pack 去出稿板块</p>
        </div>
        <button onClick={() => navigate("/composer")}
          style={{fontSize: 14, padding: "8px 16px", whiteSpace: "nowrap"}}>
          ✍️ 去出稿 · 新建 pack
        </button>
      </div>

      {loading && (
        <div className="card" style={{textAlign: "center", padding: 32}}>
          <div className="muted">读取中…</div>
        </div>
      )}
      {err && (
        <div className="card">
          <div className="banner danger">{err}</div>
        </div>
      )}
      {!loading && history.length === 0 && !err && (
        <div className="card" style={{textAlign: "center", padding: 40}}>
          <div style={{fontSize: 40, marginBottom: 10}}>📭</div>
          <h2 style={{margin: 0}}>还没有任何 pack</h2>
          <p className="muted" style={{fontSize: 13}}>
            起号策略 pack 在出稿板块创建 — 选目标 → 填输入 → 选方向 → 排期 一气呵成。
          </p>
          <button onClick={() => navigate("/composer")}
            style={{marginTop: 14, fontSize: 14, padding: "10px 20px", fontWeight: 600}}>
            ✍️ 开始创建第一份 pack →
          </button>
        </div>
      )}

      {history.length > 0 && (
        <div className="card">
          <h2 style={{marginTop: 0}}>📜 pack 历史 · {history.length} 份</h2>
          <table className="table" style={{fontSize: 13}}>
            <thead>
              <tr>
                <th style={{width: 80}}>状态</th>
                <th>方向 / 主题</th>
                <th style={{width: 100}}>平台</th>
                <th style={{width: 110}}>创建时间</th>
                <th style={{width: 60}}></th>
              </tr>
            </thead>
            <tbody>
              {history.map((p) => {
                const isExpanded = p.status === "expanded";
                return (
                  <tr key={p.pack_id}
                      onClick={() => navigate(`/strategy/${p.pack_id}`)}
                      style={{cursor: "pointer"}}>
                    <td>
                      <span className="tag-pill" style={{
                        fontSize: 10.5, fontWeight: 600,
                        background: isExpanded ? "var(--ok-soft)" : "#f4f4f4",
                        color: isExpanded ? "var(--ok)" : "var(--muted)",
                      }}>
                        {isExpanded ? "✓ 已排期" : p.status}
                      </span>
                    </td>
                    <td>
                      <div style={{fontWeight: 600}}>{p.input?.positioning?.slice(0, 60) || "(未填定位)"}</div>
                      <div className="muted" style={{fontSize: 11.5, marginTop: 2}}>
                        {p.input?.target_audience?.slice(0, 80) || "(未填受众)"}
                      </div>
                    </td>
                    <td>
                      {p.platform ? <PlatformPill platform={p.platform} /> : (
                        <span className="muted" style={{fontSize: 11}}>—</span>
                      )}
                    </td>
                    <td className="muted" style={{fontSize: 11}}>
                      {p.created_at ? fmtRelative(p.created_at) : "—"}
                      <div style={{fontSize: 10, marginTop: 1}}>
                        {p.input?.cycle_weeks ? `${p.input.cycle_weeks} 周` : ""}
                        {p.input?.posts_per_week ? ` · ${p.input.posts_per_week}/周` : ""}
                      </div>
                    </td>
                    <td>
                      <button className="ghost"
                        onClick={(e) => { e.stopPropagation(); handleDelete(p.pack_id, p.input?.positioning?.slice(0, 30) || p.pack_id.slice(0, 8)); }}
                        style={{fontSize: 11, padding: "2px 8px", color: "#c53030"}}>
                        ✕
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="muted" style={{fontSize: 11, marginTop: 10}}>
            点任意行进入 pack 大纲 · 写每篇 / 迭代下一轮都在出稿板块
          </p>
        </div>
      )}
    </div>
  );
}

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { fmtRelative } from "../format";
import GroundingChip from "../components/GroundingChip";
import type { DraftListItem } from "../types";

export default function Drafts() {
  const [drafts, setDrafts] = useState<DraftListItem[]>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    reload();
  }, []);

  function reload() {
    api.drafts()
      .then(d => setDrafts(Array.isArray(d) ? d : []))
      .catch(e => {
        // eslint-disable-next-line no-console
        console.error("[Drafts] load failed:", e);
        setErr(e?.message || String(e));
      });
  }

  async function deleteDraft(id: string, topic: string) {
    // v0.61.27 ：参考 ProjectPicker 一点就删的体验，不弹 confirm — 误删
    // 自负，反正用户随时能再 compose 一份。
    try {
      await api.deleteDraft(id);
      setDrafts(prev => prev.filter(d => d.draft_id !== id));
    } catch (e: any) {
      alert("删除失败 ：" + (e?.message || String(e)));
    }
    // 抑制未使用警告 — topic 可以用于将来加 toast
    void topic;
  }

  return (
    <div>
      <div className="page-header">
        <h1>📝 历史出稿</h1>
        <p>所有生成过的稿件 · 「锚定度」一列让黑盒稿件一眼可识</p>
      </div>
      {err && <div className="banner danger">{err}</div>}

      <div className="card">
        {/* v0.65 ：反黑盒图例 — 列表层就解释清楚 chip 含义 */}
        <div style={{
          fontSize: 11.5, color: "var(--muted)", marginBottom: 10,
          padding: "6px 10px", background: "var(--primary-soft)", borderRadius: 6,
          borderLeft: "3px solid var(--primary)",
        }}>
          💡 <b>锚定度 = (正文里 [ref:xxx] 数据引用 + 蓝海词命中) / 段落数</b>。
          <span style={{color: "#15803d", fontWeight: 600, marginLeft: 8}}>🟢 ≥1.0 强锚定</span>
          <span style={{color: "#92400e", fontWeight: 600, marginLeft: 8}}>🟡 0.3-1.0</span>
          <span style={{color: "#991b1b", fontWeight: 600, marginLeft: 8}}>🔴 &lt;0.3 黑盒嫌疑</span>
        </div>
        {drafts.length === 0 ? (
          <p className="muted">还没有出过稿。<Link to="/composer">去 ✍️ 出稿 写第一篇 →</Link></p>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>主题</th><th>最终标题</th><th>方式</th>
                <th className="num">候选数</th>
                <th title="锚定度 = ([ref:xxx] 数据引用数 + 蓝海词命中) / 段落数；越高 = 越按真实数据写、越不黑盒">
                  锚定度 ℹ︎
                </th>
                <th>时间</th><th></th><th></th>
              </tr>
            </thead>
            <tbody>
              {drafts.map(d => (
                <tr key={d.draft_id}>
                  <td><b>{d.brief?.topic ?? "—"}</b></td>
                  <td>{d.final_title ?? <em className="muted">—</em>}</td>
                  <td><span className="tag-pill">{d.mode === "multi-agent" ? "多 AI" : "单 AI"}</span></td>
                  <td className="num">{d.candidate_count}</td>
                  <td>
                    {d.grounding_score != null
                      ? <GroundingChip score={d.grounding_score} breakdown={d.grounding_breakdown ?? undefined} compact />
                      : <span className="muted" style={{fontSize: 11}}>—</span>}
                  </td>
                  <td className="muted">{fmtRelative(d.generated_at)}</td>
                  <td><Link to={`/drafts/${d.draft_id}`}>详情</Link></td>
                  <td>
                    <button className="ghost"
                      style={{padding: "2px 8px", fontSize: 11, color: "var(--danger)"}}
                      title="永久删除这条出稿 + 所有候选 / 审稿 / 时间线"
                      onClick={() => deleteDraft(d.draft_id, d.brief?.topic || "")}>
                      ✕ 删
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

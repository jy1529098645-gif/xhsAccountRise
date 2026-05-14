import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { fmtRelative } from "../format";
import type { DraftListItem } from "../types";

export default function Drafts() {
  const [drafts, setDrafts] = useState<DraftListItem[]>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.drafts()
      .then(d => setDrafts(Array.isArray(d) ? d : []))
      .catch(e => {
        // eslint-disable-next-line no-console
        console.error("[Drafts] load failed:", e);
        setErr(e?.message || String(e));
      });
  }, []);

  return (
    <div>
      <div className="page-header">
        <h1>📝 历史出稿</h1>
        <p>所有生成过的稿件</p>
      </div>
      {err && <div className="banner danger">{err}</div>}

      <div className="card">
        {drafts.length === 0 ? (
          <p className="muted">还没有出过稿。<Link to="/composer">去 ✍️ 出稿 写第一篇 →</Link></p>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>主题</th><th>最终标题</th><th>方式</th>
                <th className="num">候选数</th><th>时间</th><th></th>
              </tr>
            </thead>
            <tbody>
              {drafts.map(d => (
                <tr key={d.draft_id}>
                  <td><b>{d.brief?.topic ?? "—"}</b></td>
                  <td>{d.final_title ?? <em className="muted">—</em>}</td>
                  <td><span className="tag-pill">{d.mode === "multi-agent" ? "多 AI" : "单 AI"}</span></td>
                  <td className="num">{d.candidate_count}</td>
                  <td className="muted">{fmtRelative(d.generated_at)}</td>
                  <td><Link to={`/drafts/${d.draft_id}`}>详情</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

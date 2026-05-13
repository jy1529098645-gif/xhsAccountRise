import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { fmtTime } from "../format";
import type { DraftListItem } from "../types";

export default function Drafts() {
  const [drafts, setDrafts] = useState<DraftListItem[]>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.drafts().then(setDrafts).catch(e => setErr(e.message));
  }, []);

  return (
    <div>
      <div className="page-header">
        <h1>Drafts</h1>
        <p>所有生成过的草稿，含单 LLM 和 多 Agent。</p>
      </div>
      {err && <div className="banner danger">{err}</div>}

      <div className="card">
        {drafts.length === 0 ? (
          <p className="muted">还没有 draft。<Link to="/composer">去生成第一篇 →</Link></p>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>时间</th><th>主题</th><th>mode</th><th>final 标题</th>
                <th className="num">候选数</th><th>lib</th><th></th>
              </tr>
            </thead>
            <tbody>
              {drafts.map(d => (
                <tr key={d.draft_id}>
                  <td className="muted">{fmtTime(d.generated_at)}</td>
                  <td>{d.brief?.topic ?? "—"}</td>
                  <td><span className="tag-pill">{d.mode}</span></td>
                  <td>{d.final_title ?? <em className="muted">—</em>}</td>
                  <td className="num">{d.candidate_count}</td>
                  <td className="muted">{d.library_id ?? "—"}</td>
                  <td><Link to={`/drafts/${d.draft_id}`}>查看</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

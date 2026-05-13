import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { fmtTime } from "../format";

export default function DraftDetail() {
  const { id } = useParams();
  const [data, setData] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    api.draftDetail(id).then(setData).catch(e => setErr(e.message));
  }, [id]);

  if (err) return <div className="banner danger">{err}</div>;
  if (!data) return <div className="card muted">加载中…</div>;

  const d = data.draft;
  const cands = data.candidates ?? [];
  const trace = data.trace ?? [];
  const brief = d.brief ?? {};

  async function score(cid: string, s: number) {
    await api.scoreCandidate(d.draft_id, cid, s);
    api.draftDetail(d.draft_id).then(setData);
  }
  async function choose(cid: string) {
    await api.chooseCandidate(d.draft_id, cid);
    api.draftDetail(d.draft_id).then(setData);
  }

  return (
    <div>
      <div className="page-header">
        <h1>Draft {d.draft_id}</h1>
        <p>{fmtTime(d.generated_at)} · mode={d.mode} · library={d.library_id ?? "—"}</p>
      </div>
      <Link to="/drafts">← 全部 drafts</Link>

      <div className="card" style={{marginTop: 12}}>
        <h2>Brief</h2>
        <table className="table"><tbody>
          {Object.entries(brief).map(([k, v]) => (
            <tr key={k}><td style={{width: 120}}>{k}</td><td>{String(v) || <em className="muted">—</em>}</td></tr>
          ))}
        </tbody></table>
      </div>

      {trace.length > 0 && (
        <div className="card">
          <h2>Agent 时间线</h2>
          <div className="trace-list">
            {trace.map((s: any) => (
              <div key={s.trace_id} className={`step ${s.error ? "err" : ""}`}>
                <span>#{s.step_index}</span>
                <span className="agent">{s.agent_name}</span>
                <span>{s.error || s.output_summary}</span>
                <span style={{textAlign: "right"}}>{s.latency_ms}ms</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="card">
        <h2>候选 ({cands.length})</h2>
        <div className="candidate-grid">
          {cands.map((c: any) => (
            <div key={c.candidate_id} className={`cand ${c.chosen ? "final" : ""} ${c.meta?.error ? "failed" : ""}`}>
              <div className="llm">{c.llm}{c.chosen ? " ★" : ""}</div>
              <div className="muted" style={{fontSize: 11}}>
                self {c.self_score?.toFixed?.(1) ?? "—"} ·
                ${c.meta?.cost_estimate_usd?.toFixed?.(4) ?? "0"} · {c.meta?.latency_ms ?? 0}ms
              </div>
              <div className="title">{c.title}</div>
              <div className="body">{c.body}</div>
              <div style={{marginTop: 8}}>
                {(c.tags ?? []).map((t: string) => <span key={t} className="tag-pill">#{t}</span>)}
              </div>
              {c.cover_prompt && <div className="cover"><b>cover：</b>{c.cover_prompt}</div>}

              {(c.critiques ?? []).length > 0 && (
                <div style={{marginTop: 8, fontSize: 11.5}}>
                  {c.critiques.map((cr: any) => (
                    <div key={cr.critique_id} style={{padding: "4px 6px", borderTop: "1px solid #f0f0f0"}}>
                      <b style={{color: "var(--primary)"}}>{cr.critic_llm}</b> overall {cr.overall?.toFixed?.(1) ?? "—"}
                      <div className="muted">{cr.suggestion}</div>
                    </div>
                  ))}
                </div>
              )}

              <div className="row" style={{marginTop: 10, justifyContent: "space-between"}}>
                <div style={{fontSize: 11, color: "var(--muted)"}}>
                  人工评分：
                  {[1, 2, 3, 4, 5].map(n => (
                    <button key={n} className="ghost"
                      style={{padding: "2px 6px", fontSize: 12, color: c.human_score === n ? "var(--primary)" : undefined}}
                      onClick={() => score(c.candidate_id, n)}>
                      {c.human_score === n ? "★" : "☆"}{n}
                    </button>
                  ))}
                </div>
                {!c.chosen && (
                  <button className="secondary" style={{padding: "4px 8px", fontSize: 12}}
                    onClick={() => choose(c.candidate_id)}>选为 final</button>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

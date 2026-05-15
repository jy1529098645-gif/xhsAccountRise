import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { fmtTime, coerceStringList } from "../format";
import PlatformPill from "../components/PlatformPill";

export default function DraftDetail() {
  const { id } = useParams();
  const [data, setData] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    api.draftDetail(id).then(setData).catch(e => setErr(e.message));
  }, [id]);

  async function reload() {
    if (!id) return;
    try { setData(await api.draftDetail(id)); }
    catch (e: any) { setErr(e.message); }
  }

  if (err) return <div className="banner danger">{err}</div>;
  if (!data) return <div className="card muted">加载中…</div>;

  const d = data.draft;
  const cands = data.candidates ?? [];
  const trace = data.trace ?? [];
  const brief = d.brief ?? {};
  const finalCand = cands.find((c: any) => c.chosen);

  async function score(cid: string, s: number) {
    await api.scoreCandidate(d.draft_id, cid, s);
    api.draftDetail(d.draft_id).then(setData);
  }
  async function choose(cid: string) {
    await api.chooseCandidate(d.draft_id, cid);
    api.draftDetail(d.draft_id).then(setData);
  }

  const plan = data.plan;

  return (
    <div>
      <div className="page-header">
        <h1>{brief?.topic ?? "出稿详情"}</h1>
        <p>
          {fmtTime(d.generated_at)} · {d.mode === "multi-agent" ? "多 AI 协作" : "单 AI"}
          {brief?.platform && <> · <PlatformPill platform={brief.platform} /></>}
        </p>
      </div>
      <Link to="/drafts">← 全部历史出稿</Link>

      <PublishWidget draft={d} finalCand={finalCand} onChanged={reload} />

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

      {plan && Object.keys(plan).length > 0 && (
        <div className="card">
          <h2>📋 执行计划</h2>
          {plan.series_thesis && (
            <p style={{fontStyle: "italic", color: "var(--muted)"}}>主线：{plan.series_thesis}</p>
          )}
          {plan.publish_schedule?.length > 0 && (
            <>
              <h3>📅 推荐发布时段</h3>
              <table className="table"><thead><tr><th>时段</th><th className="num">median likes</th><th>为什么</th></tr></thead><tbody>
                {plan.publish_schedule.map((s: any, i: number) => (
                  <tr key={i}><td><b>{s.slot}</b></td><td className="num">{s.median_likes?.toLocaleString?.() ?? "—"}</td><td className="muted">{s.why}</td></tr>
                ))}
              </tbody></table>
            </>
          )}
          {plan.follow_up_angles?.length > 0 && (
            <>
              <h3 style={{marginTop: 14}}>🔁 后续选题</h3>
              {plan.follow_up_angles.map((a: any, i: number) => (
                <div key={i} style={{padding: "10px 12px", background: "#fafafa", borderRadius: 6, marginBottom: 8}}>
                  <div style={{fontWeight: 600}}>{a.title}</div>
                  <div style={{fontSize: 12, marginTop: 4}}>
                    <span className="tag-pill">{a.angle}</span>
                    <span className="tag-pill">{a.hook_type}</span>
                  </div>
                  <div className="muted" style={{fontSize: 12, marginTop: 6}}>{a.why}</div>
                </div>
              ))}
            </>
          )}
          {(() => {
            // See Composer.tsx — non-Claude models can return tactics in
            // shapes other than string[]. Coerce or .map() crashes.
            const tactics = coerceStringList(plan.engagement_tactics);
            if (tactics.length === 0) return null;
            return (
              <>
                <h3 style={{marginTop: 14}}>💬 互动运营建议</h3>
                <ol style={{marginLeft: 20, lineHeight: 1.7}}>
                  {tactics.map((t, i) => <li key={i}>{t}</li>)}
                </ol>
              </>
            );
          })()}
        </div>
      )}

      {finalCand && d.published && (
        <PerformanceWidget draft={d} onChanged={reload} />
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

// ---------- Publish + performance widgets (drives the 复盘 page) -----------

function PublishWidget({draft, finalCand, onChanged}: {
  draft: any; finalCand: any; onChanged: () => void;
}) {
  const isPublished = !!draft.published;
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [pubTitle, setPubTitle] = useState(draft.published_title ?? finalCand?.title ?? "");
  const [pubBody, setPubBody] = useState(draft.published_body ?? finalCand?.body ?? "");
  const [pubUrl, setPubUrl] = useState(draft.published_url ?? "");
  const [pubNotes, setPubNotes] = useState(draft.published_notes ?? "");

  if (!finalCand && !isPublished) {
    return (
      <div className="banner info" style={{marginTop: 10}}>
        先在下面候选里 ★ 选一份为 final，然后再标记为已发布。
      </div>
    );
  }

  async function save() {
    setBusy(true);
    try {
      await api.markPublished(draft.draft_id, {
        published_title: pubTitle || null,
        published_body: pubBody || null,
        published_url: pubUrl || null,
        published_notes: pubNotes || null,
      });
      setEditing(false);
      onChanged();
    } catch (e: any) {
      alert("保存失败 ：" + e.message);
    } finally { setBusy(false); }
  }

  async function unpublish() {
    setBusy(true);
    try {
      await api.unmarkPublished(draft.draft_id);
      onChanged();
    } catch (e: any) {
      alert("取消失败 ：" + e.message);
    } finally { setBusy(false); }
  }

  return (
    <div className="card" style={{
      marginTop: 12,
      borderLeft: `4px solid ${isPublished ? "var(--ok)" : "var(--primary)"}`,
      background: isPublished ? "var(--ok-soft)" : undefined,
    }}>
      <div className="spread" style={{alignItems: "flex-start"}}>
        <div>
          <h2 style={{margin: 0}}>
            {isPublished ? "✅ 已标记为已发布" : "📌 标记这一篇为「已发布」"}
          </h2>
          <p className="muted" style={{fontSize: 12, margin: "4px 0 0"}}>
            {isPublished
              ? "之后到 📊 复盘 页面录入真实数据 → AI 帮你拆解涨粉规律。"
              : "如果你已经把这篇真的发到平台了，标记一下，附上发布版本的文本（如果改过的话），复盘的时候能对比 AI 原稿 vs 实际发的版本。"}
          </p>
        </div>
        <div className="row" style={{gap: 6}}>
          {!isPublished && !editing && (
            <button onClick={() => setEditing(true)}>✏️ 标记为已发布</button>
          )}
          {isPublished && !editing && (
            <>
              <button className="ghost" style={{fontSize: 12}} onClick={() => setEditing(true)}>✏️ 编辑发布信息</button>
              <button className="ghost" style={{fontSize: 12, color: "var(--danger)"}} disabled={busy}
                onClick={unpublish}>取消标记</button>
            </>
          )}
        </div>
      </div>

      {editing && (
        <div style={{marginTop: 12, padding: 12, background: "#fff", borderRadius: 8}}>
          <label>实际发布的标题（如果改过 AI 的话）</label>
          <input value={pubTitle} onChange={e => setPubTitle(e.target.value)}
            placeholder="留空 = 沿用 AI 原标题" />

          <label style={{marginTop: 8}}>实际发布的正文（粘贴你真正发出去的版本）</label>
          <textarea value={pubBody} onChange={e => setPubBody(e.target.value)}
            placeholder="留空 = 沿用 AI 原稿；如果改过，这里贴改后的版本"
            style={{minHeight: 160, fontFamily: "inherit", fontSize: 13, lineHeight: 1.65}} />

          <div className="row" style={{gap: 8, marginTop: 8}}>
            <div style={{flex: 1}}>
              <label>笔记 URL（可选）</label>
              <input value={pubUrl} onChange={e => setPubUrl(e.target.value)}
                placeholder="https://www.xiaohongshu.com/explore/..." />
            </div>
          </div>

          <label style={{marginTop: 8}}>你的发布备注（可选）</label>
          <textarea value={pubNotes} onChange={e => setPubNotes(e.target.value)}
            placeholder="比如 ：把开头改短了 / 加了 3 个 emoji / 删掉了第二段案例"
            style={{minHeight: 60}} />

          <div className="row" style={{gap: 8, marginTop: 10}}>
            <button onClick={save} disabled={busy}>{busy ? "保存中…" : "💾 保存"}</button>
            <button className="ghost" onClick={() => setEditing(false)} disabled={busy}>关闭</button>
          </div>
        </div>
      )}

      {isPublished && !editing && (
        <div style={{marginTop: 8, fontSize: 12}}>
          <span className="muted">发布于 ：</span>{new Date((draft.published_at || 0) * 1000).toLocaleString()}
          {draft.published_url && (
            <>　<a href={draft.published_url} target="_blank" rel="noreferrer">🔗 笔记链接</a></>
          )}
        </div>
      )}
    </div>
  );
}

function PerformanceWidget({draft, onChanged}: {draft: any; onChanged: () => void}) {
  const [busy, setBusy] = useState(false);
  const [likes, setLikes] = useState("");
  const [comments, setComments] = useState("");
  const [saves, setSaves] = useState("");
  const [views, setViews] = useState("");
  const [followerDelta, setFollowerDelta] = useState("");
  const [notes, setNotes] = useState("");
  const [recent, setRecent] = useState<any[]>([]);

  useEffect(() => {
    // Reload latest perf from the published-drafts endpoint (since draftDetail
    // doesn't include perf rows).
    let cancel = false;
    api.listPublishedDrafts().then((rows: any[]) => {
      if (cancel) return;
      const me = rows.find(r => r.draft_id === draft.draft_id);
      setRecent(me?.performance ?? []);
    }).catch(() => {});
    return () => { cancel = true; };
  }, [draft.draft_id, draft.published]);

  async function save() {
    setBusy(true);
    try {
      await api.recordDraftPerformance(draft.draft_id, {
        likes: likes ? Number(likes) : null,
        comments: comments ? Number(comments) : null,
        saves: saves ? Number(saves) : null,
        views: views ? Number(views) : null,
        follower_delta: followerDelta ? Number(followerDelta) : null,
        notes,
      });
      setLikes(""); setComments(""); setSaves(""); setViews(""); setFollowerDelta(""); setNotes("");
      onChanged();
      // Reload local recent list
      const rows = await api.listPublishedDrafts();
      const me = rows.find((r: any) => r.draft_id === draft.draft_id);
      setRecent(me?.performance ?? []);
    } catch (e: any) {
      alert("保存失败 ：" + e.message);
    } finally { setBusy(false); }
  }

  return (
    <div className="card" style={{marginTop: 10}}>
      <h2 style={{margin: "0 0 4px"}}>📈 录入平台数据（可分多次 ：发布后 +1d / +1w / +1m）</h2>
      <p className="muted" style={{fontSize: 12, margin: "0 0 12px"}}>
        填几条都行，复盘的时候 AI 会自动用最新一条。
      </p>
      <div style={{display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 8}}>
        <NumIn label="👍 点赞" v={likes} setV={setLikes} />
        <NumIn label="💬 评论" v={comments} setV={setComments} />
        <NumIn label="⭐ 收藏" v={saves} setV={setSaves} />
        <NumIn label="👁️ 阅读" v={views} setV={setViews} />
        <NumIn label="+粉丝" v={followerDelta} setV={setFollowerDelta} />
      </div>
      <label style={{marginTop: 10}}>备注（可选）</label>
      <textarea value={notes} onChange={e => setNotes(e.target.value)}
        placeholder="评论里的高频问 / 涨粉来源 / 私信咨询数 / 任何观察"
        style={{minHeight: 50}} />
      <button onClick={save} disabled={busy} style={{marginTop: 8, fontSize: 13}}>
        {busy ? "保存中…" : "💾 保存这条数据"}
      </button>

      {recent.length > 0 && (
        <div style={{marginTop: 12}}>
          <div className="muted" style={{fontSize: 12, marginBottom: 4}}>历史录入 ：</div>
          <table className="table">
            <thead><tr>
              <th>时间</th><th className="num">点赞</th><th className="num">评论</th>
              <th className="num">收藏</th><th className="num">阅读</th><th className="num">+粉</th><th>备注</th>
            </tr></thead>
            <tbody>
              {recent.map((p: any) => (
                <tr key={p.perf_id}>
                  <td className="muted" style={{fontSize: 12}}>{new Date(p.recorded_at * 1000).toLocaleString()}</td>
                  <td className="num">{p.likes ?? "—"}</td>
                  <td className="num">{p.comments ?? "—"}</td>
                  <td className="num">{p.saves ?? "—"}</td>
                  <td className="num">{p.views ?? "—"}</td>
                  <td className="num">{p.follower_delta ?? "—"}</td>
                  <td className="muted" style={{fontSize: 12}}>{p.notes || ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function NumIn({label, v, setV}: {label: string; v: string; setV: (s: string) => void}) {
  return (
    <div>
      <label style={{fontSize: 11, color: "#666", display: "block"}}>{label}</label>
      <input type="number" min="0" value={v} onChange={e => setV(e.target.value)}
        style={{padding: "4px 6px", fontSize: 13, width: "100%"}} />
    </div>
  );
}

import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { fmtTime } from "../format";
import PlatformPill from "../components/PlatformPill";
import type { ComplianceHit, RagRef, RagComment, RagHook, VariantChild,
              TrackingFetchResult } from "../types";

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

      <ComplianceBanner finalCand={finalCand} />

      {d.parent_draft_id && (
        <div className="banner info" style={{marginTop: 10}}>
          🔁 这是<b>{d.variant_label || "变体"}</b>，源自{" "}
          <Link to={`/drafts/${d.parent_draft_id}`}>父稿件 →</Link>
        </div>
      )}

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
          {plan.engagement_tactics?.length > 0 && (
            <>
              <h3 style={{marginTop: 14}}>💬 互动运营建议</h3>
              <ol style={{marginLeft: 20, lineHeight: 1.7}}>
                {plan.engagement_tactics.map((t: string, i: number) => <li key={i}>{t}</li>)}
              </ol>
            </>
          )}
        </div>
      )}

      {finalCand && d.published && (
        <PerformanceWidget draft={d} onChanged={reload} />
      )}

      <VariantFanOutCard
        draftId={d.draft_id}
        existing={data.variants ?? []}
        published={!!d.published}
        onSpawned={reload}
      />

      <ProvenancePanel rag={data.rag} />

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
              <div className="body">{renderWithHits(c.body, (c.compliance?.hits ?? []).filter((h: ComplianceHit) => h.where === "body"))}</div>
              <CandidateComplianceLine compliance={c.compliance} />
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

      <RefreshFromUrl draft={draft} onRefreshed={() => {
        onChanged();
        api.listPublishedDrafts().then((rows: any[]) => {
          const me = rows.find(r => r.draft_id === draft.draft_id);
          setRecent(me?.performance ?? []);
        });
      }} />


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

// =============================================================
// v0.53 additions
// =============================================================

// ---------- Compliance ----------------------------------------
function ComplianceBanner({finalCand}: {finalCand: any}) {
  if (!finalCand) return null;
  const comp = finalCand.compliance;
  if (!comp || comp.severity === "pass") return null;
  const isBlock = comp.severity === "block";
  return (
    <div className="card" style={{
      marginTop: 10,
      borderLeft: `4px solid ${isBlock ? "#d44" : "#e0a800"}`,
      background: isBlock ? "#fff4f4" : "#fffaf0",
    }}>
      <h2 style={{margin: 0, color: isBlock ? "#a33" : "#a67700"}}>
        {isBlock ? "⛔ 合规闸门：发布前必须改" : "⚠️ 合规闸门：建议修改"}
      </h2>
      <p className="muted" style={{fontSize: 12, margin: "6px 0 12px"}}>
        命中策略报告 6.4 红线词 {comp.hit_count} 处。点开看具体位置 + 一键替换。
      </p>
      <ComplianceHitTable hits={comp.hits ?? []} candidateId={finalCand.candidate_id} />
    </div>
  );
}

function ComplianceHitTable({hits, candidateId}: {hits: ComplianceHit[]; candidateId: string}) {
  const [rewriteState, setRewriteState] = useState<Record<string, string>>({});

  if (hits.length === 0) return null;

  async function rewriteAll(where: "title" | "body", text: string) {
    try {
      const r = await api.complianceRewrite(text, where);
      setRewriteState(prev => ({...prev, [`${candidateId}:${where}`]: r.rewritten}));
    } catch (e: any) {
      alert("一键改写失败: " + e.message);
    }
  }

  // Group hits by `where`
  const byWhere: Record<string, ComplianceHit[]> = {};
  hits.forEach(h => {
    byWhere[h.where] = byWhere[h.where] ?? [];
    byWhere[h.where].push(h);
  });

  return (
    <div style={{display: "grid", gap: 10}}>
      {Object.entries(byWhere).map(([where, ws]) => (
        <div key={where} style={{background: "#fff", padding: 10, borderRadius: 6}}>
          <div style={{fontSize: 12, fontWeight: 600, marginBottom: 6}}>
            {where} · {ws.length} 命中
            {(where === "title" || where === "body") && (
              <button className="ghost" style={{marginLeft: 8, fontSize: 11, padding: "2px 8px"}}
                onClick={() => {
                  const text = window.prompt(`粘贴当前的${where}文本：`);
                  if (text) rewriteAll(where as any, text);
                }}>
                一键改写
              </button>
            )}
          </div>
          {ws.map((h, i) => (
            <div key={i} style={{fontSize: 12, padding: "4px 0", borderTop: i > 0 ? "1px dashed #eee" : undefined}}>
              <span style={{
                background: h.severity === "block" ? "#fadcdc" : "#fff0c0",
                color: h.severity === "block" ? "#a33" : "#a67700",
                padding: "1px 6px", borderRadius: 3, fontWeight: 600,
              }}>{h.term}</span>
              <span className="muted" style={{marginLeft: 8}}>{h.rule_id} · {h.category}</span>
              <div style={{marginTop: 2, color: "#666"}}>
                → 推荐 <code>{h.safe_alternative}</code>
              </div>
              <div className="muted" style={{fontSize: 11, marginTop: 2}}>{h.rationale}</div>
            </div>
          ))}
          {rewriteState[`${candidateId}:${where}`] && (
            <div style={{marginTop: 8, padding: 8, background: "#f6fff0", borderRadius: 4, fontSize: 12}}>
              <div className="muted" style={{marginBottom: 4}}>改写后：</div>
              <code style={{whiteSpace: "pre-wrap"}}>{rewriteState[`${candidateId}:${where}`]}</code>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function CandidateComplianceLine({compliance}: {compliance: any}) {
  if (!compliance || compliance.severity === "pass") return null;
  const sev = compliance.severity;
  return (
    <div style={{
      marginTop: 6, fontSize: 11,
      color: sev === "block" ? "#a33" : "#a67700",
      fontWeight: 600,
    }}>
      {sev === "block" ? "⛔" : "⚠️"} 合规 {compliance.hit_count} 处 ({compliance.hits.map((h: ComplianceHit) => h.term).slice(0, 3).join(" / ")})
    </div>
  );
}

// Render body with red highlights on hit spans. Used inside candidate cards.
function renderWithHits(text: string, hits: ComplianceHit[]) {
  if (!text || !hits || hits.length === 0) return text;
  // Sort + merge overlapping spans.
  const sorted = [...hits].filter(h => h.span_start >= 0 && h.span_end > h.span_start)
    .sort((a, b) => a.span_start - b.span_start);
  const parts: any[] = [];
  let cursor = 0;
  for (const h of sorted) {
    if (h.span_start < cursor) continue;  // skip overlap
    if (h.span_start > cursor) parts.push(text.slice(cursor, h.span_start));
    parts.push(
      <mark key={`${h.span_start}-${h.span_end}`}
        title={`${h.rule_id} → ${h.safe_alternative}`}
        style={{
          background: h.severity === "block" ? "#fadcdc" : "#fff0c0",
          color: h.severity === "block" ? "#a33" : "#a67700",
          padding: "0 2px", borderRadius: 2,
        }}>
        {text.slice(h.span_start, h.span_end)}
      </mark>
    );
    cursor = h.span_end;
  }
  if (cursor < text.length) parts.push(text.slice(cursor));
  return parts;
}

// ---------- Variant fan-out -----------------------------------
const ALL_ANGLES = ["教程","痛点","故事","工具评测","对比","感悟","数字","种草","建议"];

function VariantFanOutCard({draftId, existing, published, onSpawned}: {
  draftId: string; existing: VariantChild[]; published: boolean; onSpawned: () => void;
}) {
  const [opened, setOpened] = useState(false);
  const [picked, setPicked] = useState<string[]>(["痛点","故事","数字"]);
  const [busy, setBusy] = useState(false);
  const [lastResult, setLastResult] = useState<any>(null);

  function toggle(a: string) {
    setPicked(prev => prev.includes(a) ? prev.filter(x => x !== a) : [...prev, a]);
  }
  async function spawn() {
    if (picked.length === 0) { alert("至少选一个 angle"); return; }
    setBusy(true);
    try {
      const r = await api.spawnVariants(draftId, picked);
      setLastResult(r);
      onSpawned();
    } catch (e: any) {
      alert("生成变体失败: " + e.message);
    } finally { setBusy(false); }
  }

  const callToAction = published
    ? "🔁 这篇发了，生成 3 个同主题变体（适合 48h+500 赞快速跟进）"
    : "🔁 生成同主题变体（不同角度并发出稿）";

  return (
    <div className="card" style={{marginTop: 10}}>
      <div className="spread">
        <h2 style={{margin: 0}}>{callToAction}</h2>
        <button className="ghost" onClick={() => setOpened(v => !v)}>
          {opened ? "收起" : "展开 →"}
        </button>
      </div>

      {existing.length > 0 && (
        <div style={{marginTop: 8, fontSize: 12}}>
          <div className="muted" style={{marginBottom: 4}}>已有 {existing.length} 个变体：</div>
          <div style={{display: "flex", flexWrap: "wrap", gap: 6}}>
            {existing.map(v => (
              <Link key={v.draft_id} to={`/drafts/${v.draft_id}`}
                className="tag-pill" style={{textDecoration: "none"}}>
                {v.variant_label || v.angle} · {(v.final_title || "").slice(0, 20)}…
              </Link>
            ))}
          </div>
        </div>
      )}

      {opened && (
        <div style={{marginTop: 12}}>
          <div className="muted" style={{fontSize: 12, marginBottom: 6}}>
            勾选要覆盖的 angle（每个 angle 跑一份 fast_mode Compose，约 30-50s × 并发 3）：
          </div>
          <div style={{display: "flex", flexWrap: "wrap", gap: 6}}>
            {ALL_ANGLES.map(a => (
              <button key={a}
                className={picked.includes(a) ? "" : "ghost"}
                style={{fontSize: 12, padding: "4px 10px"}}
                onClick={() => toggle(a)}>
                {picked.includes(a) ? "✓ " : ""}{a}
              </button>
            ))}
          </div>
          <div style={{marginTop: 12}}>
            <button onClick={spawn} disabled={busy || picked.length === 0}>
              {busy ? `生成 ${picked.length} 个变体中…` : `🚀 生成 ${picked.length} 个变体`}
            </button>
          </div>

          {lastResult && (
            <div style={{marginTop: 12, fontSize: 12, background: "#f8f8f8", padding: 10, borderRadius: 6}}>
              成功 <b>{lastResult.succeeded}</b> 失败 <b>{lastResult.failed}</b>
              <ul style={{marginTop: 6, marginBottom: 0}}>
                {lastResult.variants.map((v: any, i: number) => (
                  <li key={i}>
                    {v.angle}：
                    {v.draft_id ? <Link to={`/drafts/${v.draft_id}`}>{v.draft_id.slice(0, 8)}… →</Link>
                      : <span style={{color: "#a33"}}>{v.error}</span>}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---------- Provenance ----------------------------------------
function ProvenancePanel({rag}: {rag?: {refs: RagRef[]; comments: RagComment[]; hooks: RagHook[]}}) {
  if (!rag) return null;
  const hasAny = (rag.refs?.length ?? 0) > 0 || (rag.comments?.length ?? 0) > 0 || (rag.hooks?.length ?? 0) > 0;
  if (!hasAny) return null;
  return (
    <details className="card" style={{marginTop: 10}}>
      <summary style={{cursor: "pointer", fontSize: 14, fontWeight: 600}}>
        📚 这篇稿子参考了什么（{rag.refs.length} 篇爆款 · {rag.comments.length} 条评论 · {rag.hooks.length} 个 hook 模板）
      </summary>
      <div style={{marginTop: 10}}>
        {rag.refs.length > 0 && (
          <>
            <h3 style={{margin: "8px 0 4px", fontSize: 13}}>🔥 参考爆款（Researcher 抓的 top-K）</h3>
            <table className="table">
              <thead><tr>
                <th>标题</th><th className="num">点赞</th><th className="num">收藏</th><th className="num">评论</th>
              </tr></thead>
              <tbody>
                {rag.refs.map(r => (
                  <tr key={r.note_id}>
                    <td>
                      {r.url
                        ? <a href={r.url} target="_blank" rel="noreferrer">{r.title}</a>
                        : r.title}
                    </td>
                    <td className="num">{r.liked_count?.toLocaleString?.() ?? "—"}</td>
                    <td className="num">{r.collected_count?.toLocaleString?.() ?? "—"}</td>
                    <td className="num">{r.comment_count?.toLocaleString?.() ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
        {rag.comments.length > 0 && (
          <>
            <h3 style={{margin: "12px 0 4px", fontSize: 13}}>💬 用户原话（高赞评论）</h3>
            <ul style={{marginLeft: 18, fontSize: 12, lineHeight: 1.7}}>
              {rag.comments.slice(0, 12).map(c => (
                <li key={c.comment_id}>
                  <span className="muted" style={{marginRight: 6}}>({c.like_count}👍)</span>
                  {c.content}
                </li>
              ))}
            </ul>
          </>
        )}
        {rag.hooks.length > 0 && (
          <>
            <h3 style={{margin: "12px 0 4px", fontSize: 13}}>🎣 Hook 模板</h3>
            <table className="table">
              <thead><tr>
                <th>类型</th><th className="num">样本数</th><th className="num">中位赞</th><th>示例</th>
              </tr></thead>
              <tbody>
                {rag.hooks.map(h => (
                  <tr key={h.category}>
                    <td><b>{h.category}</b></td>
                    <td className="num">{h.count}</td>
                    <td className="num">{Math.round(h.median_likes ?? 0)}</td>
                    <td className="muted" style={{fontSize: 12}}>
                      {(h.examples ?? []).map(e => e.title).slice(0, 3).join(" / ")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </div>
    </details>
  );
}

// ---------- Tracking refresh button ---------------------------
function RefreshFromUrl({draft, onRefreshed}: {draft: any; onRefreshed: () => void}) {
  const [available, setAvailable] = useState<boolean | null>(null);
  const [hint, setHint] = useState("");
  const [busy, setBusy] = useState(false);
  const [lastFetch, setLastFetch] = useState<TrackingFetchResult | null>(null);

  useEffect(() => {
    api.trackingStatus().then(s => { setAvailable(s.crawler_available); setHint(s.hint); });
  }, []);

  async function refresh() {
    if (!draft.published_url) {
      alert("先在上方填写 published_url");
      return;
    }
    setBusy(true);
    try {
      const r = await api.trackingRefresh(draft.draft_id);
      setLastFetch(r);
      if (r.status === "ok") onRefreshed();
    } catch (e: any) {
      alert("刷新失败: " + e.message);
    } finally { setBusy(false); }
  }

  if (!draft.published || !draft.published_url) return null;

  return (
    <div style={{marginBottom: 10, padding: 10, background: "#f0f7fc", borderRadius: 6}}>
      <div className="spread">
        <div style={{fontSize: 13}}>
          <b>🔄 自动从 URL 刷新</b>{" "}
          <span className="muted" style={{fontSize: 11}}>{hint}</span>
        </div>
        <button onClick={refresh} disabled={busy || available === false}
          className={available === false ? "ghost" : ""}
          style={{fontSize: 12, padding: "4px 10px"}}>
          {busy ? "刷新中…" : "刷新"}
        </button>
      </div>
      {lastFetch && (
        <div style={{marginTop: 6, fontSize: 12}}>
          状态: <b style={{color: lastFetch.status === "ok" ? "#2a8" : "#a33"}}>
            {lastFetch.status}
          </b> · {lastFetch.raw_summary}
        </div>
      )}
    </div>
  );
}

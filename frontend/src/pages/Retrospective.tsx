import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { fmtTime, fmtRelative } from "../format";
import NextStepCard from "../components/NextStepCard";
import ErrorBoundary from "../components/ErrorBoundary";
import { humaniseError } from "../errors";
import { isAborted } from "../api";
import { startJob, getJob, cancelJob as cancelLocalJob, clearJob } from "../lib/jobs";

interface PublishedDraft {
  draft_id: string;
  library_id: string;
  published_at: number;
  published_title: string | null;
  published_body: string | null;
  published_url: string | null;
  published_notes: string | null;
  brief: any;
  final_title: string | null;
  final_body: string | null;
  performance: Array<{
    perf_id: string;
    recorded_at: number;
    likes: number | null;
    comments: number | null;
    saves: number | null;
    shares: number | null;
    views: number | null;
    follower_delta: number | null;
    notes: string | null;
  }>;
}

interface ReviewRow {
  review_id: string;
  library_id: string | null;
  created_at: number;
  status: string;
  draft_ids: string[];
  elapsed_s: number | null;
  error: string | null;
}

// v0.51: persist the user's draft selections so navigating away + back
// keeps the checkbox state.
const SELECTED_IDS_KEY = "studio.retro.selectedIds.v1";
function loadSelectedIds(): Set<string> {
  try {
    const raw = localStorage.getItem(SELECTED_IDS_KEY);
    if (!raw) return new Set();
    const arr = JSON.parse(raw) as string[];
    return new Set(Array.isArray(arr) ? arr : []);
  } catch { return new Set(); }
}
function saveSelectedIds(ids: Set<string>): void {
  try {
    localStorage.setItem(SELECTED_IDS_KEY, JSON.stringify(Array.from(ids)));
  } catch { /* quota — ignore */ }
}

export default function Retrospective() {
  const [drafts, setDrafts] = useState<PublishedDraft[]>([]);
  const [reviews, setReviews] = useState<ReviewRow[]>([]);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => loadSelectedIds());
  const [analyzing, setAnalyzing] = useState(false);
  const [analysis, setAnalysis] = useState<any | null>(null);
  const [latestReviewId, setLatestReviewId] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const RETRO_JOB_ID = "retro:current";
  function pauseAnalyze() { cancelLocalJob(RETRO_JOB_ID); }

  async function load() {
    try {
      const [d, r] = await Promise.all([
        api.listPublishedDrafts(), api.listRetrospectives(),
      ]);
      setDrafts(d as any);
      setReviews(r as any);
    } catch (e: any) {
      setErr(humaniseError(e));
    }
  }
  useEffect(() => {
    load();
    // Restore the last analysis result from the jobs store (survives
    // navigation away/back and even page reload via localStorage).
    const j = getJob<any>(RETRO_JOB_ID);
    if (j?.status === "done" && j.result?.analysis) {
      setAnalysis(j.result.analysis);
      setLatestReviewId(j.result.review_id);
    }
  }, []);

  function toggleSel(id: string) {
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      saveSelectedIds(next);
      return next;
    });
  }
  function selectAll() {
    const next = new Set(drafts.map(d => d.draft_id));
    saveSelectedIds(next);
    setSelectedIds(next);
  }
  function clearSel() {
    saveSelectedIds(new Set());
    setSelectedIds(new Set());
  }

  async function runAnalyze() {
    if (selectedIds.size === 0) {
      setErr("先勾选要复盘的稿子（建议 ≥ 3 篇，越多分析越准）");
      return;
    }
    setErr(null); setInfo(null); setAnalyzing(true);
    const job = startJob<any>(
      RETRO_JOB_ID, "retrospective",
      (signal) => api.runRetrospective({
        draft_ids: Array.from(selectedIds),
        model_spec: "openai:gpt-4o",
      }, signal),
    );
    try {
      const r = await job.promise;
      setAnalysis(r.analysis);
      setLatestReviewId(r.review_id);
      setInfo(`✓ 复盘报告生成完成（${r.elapsed_s}s · 覆盖 ${r.draft_ids.length} 篇）`);
      load();
    } catch (e: any) {
      if (isAborted(e)) {
        setInfo("⏸ 已暂停。点🚀生成复盘报告可重新开始。");
      } else {
        setErr(humaniseError(e));
      }
    } finally {
      setAnalyzing(false);
    }
  }

  async function openReview(id: string) {
    try {
      const r = await api.getRetrospective(id);
      setAnalysis(r.analysis);
      setLatestReviewId(id);
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (e: any) {
      setErr(humaniseError(e));
    }
  }

  const hasPublished = drafts.length > 0;

  return (
    <div>
      <div className="page-header">
        <h1>📊 复盘 · 第 4 步</h1>
        <p>
          把已经发出去的内容 + 真实平台数据交给 AI ：
          它会拆出哪条 hook 真涨粉、哪类标题没人点、用户改稿改对了哪几处，
          然后告诉你下一轮该加大什么 / 砍掉什么。
        </p>
        <p className="muted" style={{fontSize: 12, marginTop: 6}}>
          💡 前置 ：先在 <Link to="/drafts">📝 历史出稿</Link> 把发了的稿子标记为
          「已发布」+ 录入数据，再回这里跑复盘。
        </p>
      </div>

      {err && (
        <div className="banner danger" style={{display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12}}>
          <div style={{whiteSpace: "pre-wrap", flex: 1}}>{err}</div>
          <button className="ghost" onClick={() => setErr(null)} style={{padding: "4px 8px", fontSize: 12, flexShrink: 0}}>关闭</button>
        </div>
      )}
      {info && !err && <div className="banner info">{info}</div>}

      {!hasPublished && (
        <div className="card" style={{textAlign: "center", padding: 32}}>
          <div style={{fontSize: 32}}>📭</div>
          <h2 style={{margin: "8px 0"}}>还没有已发布的稿件</h2>
          <p className="muted" style={{margin: "4px 0 14px"}}>
            到 <Link to="/composer">✍️ 出稿</Link> 出几篇 → 真的发出去后回
            <Link to="/drafts"> 📝 历史出稿</Link> 点「标记为已发布」+ 录入数据。
            积累 3 篇以上再回这里跑复盘。
          </p>
        </div>
      )}

      {analysis && (
        <ErrorBoundary>
          <AnalysisView analysis={analysis} reviewId={latestReviewId} drafts={drafts}
            onClose={() => {
              setAnalysis(null); setLatestReviewId(null);
              clearJob(RETRO_JOB_ID);
            }} />
        </ErrorBoundary>
      )}

      {hasPublished && (
        <div className="card">
          <div className="spread" style={{alignItems: "flex-start"}}>
            <div>
              <h2 style={{margin: 0}}>📝 已发布的稿件 ({drafts.length})</h2>
              <p className="muted" style={{fontSize: 12, margin: "4px 0 0"}}>
                勾选要复盘的稿子，下面点「生成复盘报告」。
                未录入数据的稿子也能勾，但 AI 会缺乏判据。
              </p>
            </div>
            <div className="row" style={{gap: 6}}>
              <button className="ghost" onClick={selectAll} style={{fontSize: 12}}>全选</button>
              <button className="ghost" onClick={clearSel} style={{fontSize: 12}}>清空</button>
            </div>
          </div>

          <div style={{marginTop: 12, display: "grid", gap: 10}}>
            {drafts.map(d => (
              <DraftReviewCard key={d.draft_id} draft={d}
                selected={selectedIds.has(d.draft_id)}
                onToggle={() => toggleSel(d.draft_id)}
                onChanged={load} />
            ))}
          </div>

          <div style={{marginTop: 14, padding: 12, background: "var(--primary-soft)",
                       borderRadius: 8, border: "1px solid var(--primary)"}}>
            <div className="spread" style={{alignItems: "center"}}>
              <div>
                <b>🚀 让 Claude Opus 跑复盘分析（已选 {selectedIds.size} 篇）</b>
                <div className="muted" style={{fontSize: 12, marginTop: 4}}>
                  Wins / Losses / 模式拆解 / 下一轮该做什么 — 约 30-60s
                </div>
              </div>
              <div className="row" style={{gap: 6}}>
                <button onClick={runAnalyze} disabled={analyzing || selectedIds.size === 0}>
                  {analyzing ? "🤖 分析中（30-60s）…" : "🚀 生成复盘报告"}
                </button>
                {analyzing && (
                  <button className="ghost" onClick={pauseAnalyze}
                    style={{padding: "8px 14px", fontSize: 13}}>⏸ 暂停</button>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {reviews.length > 0 && (
        <div className="card" style={{background: "#fafafa"}}>
          <h3 style={{margin: "0 0 6px"}}>📚 历史复盘报告</h3>
          <table className="table">
            <thead>
              <tr><th>时间</th><th className="num">覆盖几篇</th><th>状态</th><th className="num">耗时</th><th></th></tr>
            </thead>
            <tbody>
              {reviews.slice(0, 10).map(r => (
                <tr key={r.review_id}>
                  <td>{fmtTime(r.created_at)}</td>
                  <td className="num">{r.draft_ids?.length ?? 0}</td>
                  <td>
                    {r.status === "completed" ? <span style={{color: "var(--ok)"}}>✓</span>
                    : r.status === "failed" ? <span style={{color: "var(--danger)"}}>✗ {r.error?.slice(0, 50)}</span>
                    : <span className="muted">{r.status}</span>}
                  </td>
                  <td className="num muted">{r.elapsed_s ? `${r.elapsed_s}s` : "—"}</td>
                  <td>
                    {r.status === "completed" && (
                      <button className="ghost" style={{padding: "2px 8px", fontSize: 12}}
                        onClick={() => openReview(r.review_id)}>查看 →</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {analysis && (
        <NextStepCard
          label="去 🚀 起号策略 出下一轮"
          hint="复盘报告里的 wins / losses / 推荐都会被下一轮策略自动引用。"
          to="/strategy"
        />
      )}
    </div>
  );
}

// ---------- per-draft card with metric editor ------------------------------

function DraftReviewCard({draft, selected, onToggle, onChanged}: {
  draft: PublishedDraft;
  selected: boolean;
  onToggle: () => void;
  onChanged: () => void;
}) {
  const latestPerf = draft.performance?.[0];
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [likes, setLikes] = useState(latestPerf?.likes?.toString() ?? "");
  const [comments, setComments] = useState(latestPerf?.comments?.toString() ?? "");
  const [saves, setSaves] = useState(latestPerf?.saves?.toString() ?? "");
  const [views, setViews] = useState(latestPerf?.views?.toString() ?? "");
  const [followerDelta, setFollowerDelta] = useState(latestPerf?.follower_delta?.toString() ?? "");
  const [notes, setNotes] = useState(latestPerf?.notes ?? "");

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
      setEditing(false);
      onChanged();
    } catch (e: any) {
      alert("保存失败 ：" + humaniseError(e));
    } finally {
      setBusy(false);
    }
  }

  const title = draft.published_title || draft.final_title || draft.brief?.topic || "(无标题)";
  const body = draft.published_body || draft.final_body || "";
  const edited = (draft.published_body && draft.final_body && draft.published_body !== draft.final_body)
              || (draft.published_title && draft.final_title && draft.published_title !== draft.final_title);
  const m = latestPerf || {} as any;
  const hasMetrics = m.likes != null || m.comments != null || m.saves != null
                  || m.views != null || m.follower_delta != null;

  return (
    <div style={{padding: 12, border: selected ? "2px solid var(--primary)" : "1px solid var(--border)",
                 borderRadius: 8, background: "#fff"}}>
      <div className="row" style={{alignItems: "flex-start", gap: 10}}>
        <input type="checkbox" checked={selected} onChange={onToggle}
          style={{marginTop: 4, transform: "scale(1.2)"}} />
        <div style={{flex: 1, minWidth: 0}}>
          <div className="spread" style={{alignItems: "baseline"}}>
            <div style={{fontWeight: 600, fontSize: 14}}>{title}</div>
            <div className="muted" style={{fontSize: 11, whiteSpace: "nowrap"}}>
              发布于 {fmtRelative(draft.published_at)}
            </div>
          </div>
          {edited && (
            <div style={{fontSize: 11, color: "var(--primary)", marginTop: 2}}>
              ✏️ 用户编辑过 AI 原稿
            </div>
          )}
          <details style={{marginTop: 6}}>
            <summary style={{cursor: "pointer", fontSize: 12, color: "var(--muted)"}}>
              ▾ 看正文 ({body.length} 字)
            </summary>
            <pre style={{whiteSpace: "pre-wrap", fontFamily: "inherit", fontSize: 12.5,
                          marginTop: 6, padding: 8, background: "#fafafa", borderRadius: 4,
                          maxHeight: 240, overflow: "auto"}}>{body}</pre>
            {draft.published_url && (
              <div style={{fontSize: 11.5, marginTop: 4}}>
                🔗 <a href={draft.published_url} target="_blank" rel="noreferrer">{draft.published_url}</a>
              </div>
            )}
          </details>

          {!editing && (
            <div className="row" style={{gap: 10, alignItems: "center", marginTop: 8, fontSize: 12}}>
              {hasMetrics ? (
                <>
                  {m.likes != null && <span>👍 {m.likes}</span>}
                  {m.comments != null && <span>💬 {m.comments}</span>}
                  {m.saves != null && <span>⭐ {m.saves}</span>}
                  {m.views != null && <span>👁️ {m.views}</span>}
                  {m.follower_delta != null && <span>+{m.follower_delta} 粉</span>}
                </>
              ) : (
                <span className="muted">还没录入数据</span>
              )}
              <button className="ghost" style={{marginLeft: "auto", padding: "2px 8px", fontSize: 11}}
                onClick={() => setEditing(true)}>{hasMetrics ? "✏️ 编辑" : "+ 录数据"}</button>
            </div>
          )}

          {editing && (
            <div style={{marginTop: 10, padding: 10, background: "#fafafa", borderRadius: 6}}>
              <div style={{display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 6, fontSize: 12}}>
                <NumField label="👍 点赞" v={likes} onChange={setLikes} />
                <NumField label="💬 评论" v={comments} onChange={setComments} />
                <NumField label="⭐ 收藏" v={saves} onChange={setSaves} />
                <NumField label="👁️ 阅读" v={views} onChange={setViews} />
                <NumField label="+粉" v={followerDelta} onChange={setFollowerDelta} />
              </div>
              <label style={{display: "block", marginTop: 8, fontSize: 12, color: "#555"}}>备注（评论亮点 / 转化 / 你的复盘想法）</label>
              <textarea value={notes} onChange={e => setNotes(e.target.value)}
                placeholder="比如 ：评论区不停问 prompt 模板 / 第 3 天涨粉 50 / 私信咨询 8 条"
                style={{minHeight: 50, fontSize: 12.5, lineHeight: 1.5, marginTop: 2}} />
              <div className="row" style={{gap: 6, marginTop: 8}}>
                <button onClick={save} disabled={busy} style={{fontSize: 12, padding: "4px 10px"}}>
                  {busy ? "保存中…" : "💾 保存"}
                </button>
                <button className="ghost" onClick={() => setEditing(false)} disabled={busy}
                  style={{fontSize: 12, padding: "4px 10px"}}>关闭</button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function NumField({label, v, onChange}: {label: string; v: string; onChange: (s: string) => void}) {
  return (
    <div>
      <label style={{fontSize: 11, color: "#666", display: "block"}}>{label}</label>
      <input type="number" min="0" value={v} onChange={e => onChange(e.target.value)}
        style={{padding: "3px 6px", fontSize: 12, width: "100%"}} />
    </div>
  );
}

// ---------- analysis viewer -------------------------------------------------

function AnalysisView({analysis, reviewId, drafts, onClose}: {
  analysis: any; reviewId: string | null;
  drafts: PublishedDraft[]; onClose: () => void;
}) {
  function draftTitle(id: string) {
    return drafts.find(d => d.draft_id === id)?.published_title
        || drafts.find(d => d.draft_id === id)?.final_title
        || id.slice(0, 8);
  }
  return (
    <div className="card" style={{borderLeft: "4px solid var(--primary)"}}>
      <div className="spread">
        <h2 style={{margin: 0}}>🪄 复盘分析报告 {reviewId && <span className="muted" style={{fontSize: 12}}>#{reviewId.slice(0, 12)}</span>}</h2>
        <button className="ghost" onClick={onClose} style={{fontSize: 12}}>✕ 关闭</button>
      </div>

      {analysis.executive_summary && (
        <p style={{fontSize: 14, lineHeight: 1.7, marginTop: 10}}>{analysis.executive_summary}</p>
      )}

      {analysis.wins?.length > 0 && (
        <>
          <h3>✅ Wins ({analysis.wins.length})</h3>
          {analysis.wins.map((w: any, i: number) => (
            <div key={i} style={{padding: 10, marginBottom: 6, background: "var(--ok-soft)", borderRadius: 6}}>
              <b>{w.title || draftTitle(w.draft_id)}</b>
              {w.metrics && <span className="muted" style={{fontSize: 12, marginLeft: 8}}>· {w.metrics}</span>}
              <div style={{fontSize: 13, marginTop: 4}}>{w.why_won}</div>
            </div>
          ))}
        </>
      )}

      {analysis.losses?.length > 0 && (
        <>
          <h3>❌ Losses ({analysis.losses.length})</h3>
          {analysis.losses.map((l: any, i: number) => (
            <div key={i} style={{padding: 10, marginBottom: 6, background: "#fef2f2", borderRadius: 6}}>
              <b>{l.title || draftTitle(l.draft_id)}</b>
              {l.metrics && <span className="muted" style={{fontSize: 12, marginLeft: 8}}>· {l.metrics}</span>}
              <div style={{fontSize: 13, marginTop: 4}}>{l.why_lost}</div>
            </div>
          ))}
        </>
      )}

      {analysis.ai_vs_human_edits?.length > 0 && (
        <>
          <h3>✏️ AI 原稿 vs 用户实际发的版本</h3>
          <ul style={{marginLeft: 20, lineHeight: 1.7, fontSize: 13}}>
            {analysis.ai_vs_human_edits.map((s: string, i: number) => <li key={i}>{s}</li>)}
          </ul>
        </>
      )}

      {analysis.patterns?.length > 0 && (
        <>
          <h3>🔁 跨篇规律</h3>
          <ul style={{marginLeft: 20, lineHeight: 1.7, fontSize: 13}}>
            {analysis.patterns.map((s: string, i: number) => <li key={i}>{s}</li>)}
          </ul>
        </>
      )}

      {analysis.next_cycle_recommendations && (
        <>
          <h3>🚀 下一轮怎么打</h3>
          <div className="cards-grid" style={{gridTemplateColumns: "1fr 1fr 1fr"}}>
            <div style={{padding: 10, background: "var(--primary-soft)", borderRadius: 6}}>
              <b>加大投入</b>
              <ul style={{marginLeft: 18, fontSize: 12.5, marginTop: 4}}>
                {(analysis.next_cycle_recommendations.double_down_on || []).map((s: string, i: number) => <li key={i}>{s}</li>)}
              </ul>
            </div>
            <div style={{padding: 10, background: "#fef2f2", borderRadius: 6}}>
              <b>砍掉</b>
              <ul style={{marginLeft: 18, fontSize: 12.5, marginTop: 4}}>
                {(analysis.next_cycle_recommendations.drop || []).map((s: string, i: number) => <li key={i}>{s}</li>)}
              </ul>
            </div>
            <div style={{padding: 10, background: "#fff7e6", borderRadius: 6}}>
              <b>新机会</b>
              <ul style={{marginLeft: 18, fontSize: 12.5, marginTop: 4}}>
                {(analysis.next_cycle_recommendations.new_to_try || []).map((s: string, i: number) => <li key={i}>{s}</li>)}
              </ul>
            </div>
          </div>
        </>
      )}

      {analysis.risk_flags?.length > 0 && (
        <>
          <h3>⚠️ 风险提示</h3>
          <ul style={{marginLeft: 20, lineHeight: 1.7, fontSize: 13, color: "#b45309"}}>
            {analysis.risk_flags.map((s: string, i: number) => <li key={i}>{s}</li>)}
          </ul>
        </>
      )}

      <PromptProposalPanel reviewId={reviewId} />

      <NextCyclePicker analysis={analysis} drafts={drafts} />
    </div>
  );
}

// Pick a parent pack from the published drafts + jump to Strategy with
// the retrospective findings threaded as the brief prefill. Skips having
// to manually re-fill the form for the next cycle.
function NextCyclePicker({analysis, drafts}: {analysis: any; drafts: PublishedDraft[]}) {
  const navigate = useNavigate();
  const rec = analysis.next_cycle_recommendations || {};
  function goNext() {
    try {
      // Build a free-form constraints string out of the recommendations so
      // the next cycle's positioner sees the wins/losses up front.
      const ddl: string[] = rec.double_down_on || [];
      const drop: string[] = rec.drop || [];
      const newTry: string[] = rec.new_to_try || [];
      const lines: string[] = [];
      if (ddl.length) lines.push("【加大投入】" + ddl.join("； "));
      if (drop.length) lines.push("【砍掉】" + drop.join("； "));
      if (newTry.length) lines.push("【新机会】" + newTry.join("； "));
      const constraints = lines.join("\n");
      sessionStorage.setItem("strategy.briefPrefill", JSON.stringify({
        positioning: "",
        target_audience: "",
        personal_strengths: "",
        constraints,
        note: `从复盘报告带入：上一轮 ${drafts.length} 篇的 wins/losses 已塞进「附加约束」，AI 会据此调整方向。`,
      }));
    } catch { /* ignore */ }
    // Bug D 修复 ：直接进 wizard（/strategy 默认会显示 PackView 把 stash 漏掉）
    navigate("/strategy/new");
  }
  return (
    <div style={{marginTop: 16, padding: 14, background: "var(--primary-soft)",
                 borderRadius: 8, border: "1px solid var(--primary)"}}>
      <div className="spread" style={{alignItems: "center"}}>
        <div>
          <b>🚀 据此出下一轮策略</b>
          <div className="muted" style={{fontSize: 12, marginTop: 4}}>
            把 wins / losses / 新机会 自动写进下一轮的「附加约束」，AI 会按这些调整方向。
          </div>
        </div>
        <button onClick={goNext}>→ 直接出下一轮</button>
      </div>
    </div>
  );
}

// ============================================================
// v0.53 — Prompt proposal panel (item 8)
//
// After a retrospective completes, the LLM can derive a prompt-diff suggestion
// based on what worked / what didn't, and queue it for human approval. Once
// approved, the next Compose run picks up the new active prompt automatically.
// ============================================================
function PromptProposalPanel({reviewId}: {reviewId: string | null}) {
  const [busy, setBusy] = useState(false);
  const [latest, setLatest] = useState<any>(null);   // latest proposal row for this review
  const [allPending, setAllPending] = useState<any[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [previewing, setPreviewing] = useState<any>(null);

  useEffect(() => {
    if (!reviewId) return;
    let cancelled = false;
    (async () => {
      try {
        const proposals = await api.listProposals();
        if (cancelled) return;
        const forReview = proposals.find((p: any) => p.review_id === reviewId);
        setLatest(forReview ?? null);
        setAllPending(proposals.filter((p: any) => p.status === "pending"));
      } catch {/* ignore */}
    })();
    return () => { cancelled = true; };
  }, [reviewId]);

  async function propose() {
    if (!reviewId) return;
    setBusy(true); setErr(null);
    try {
      const r = await api.feedbackProposeFromReview(reviewId);
      if (r.skipped) {
        setErr(`LLM 认为暂不需要改 prompt: ${r.reason}`);
      } else {
        setLatest(r as any);
      }
      const proposals = await api.listProposals();
      setAllPending(proposals.filter((p: any) => p.status === "pending"));
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function decide(proposalId: string, action: "approve" | "reject") {
    setBusy(true);
    try {
      if (action === "approve") {
        const r = await api.approveProposal(proposalId);
        alert(`✅ 已升级到 ${r.new_active_version} — 下次 Compose 自动用新 prompt。`);
      } else {
        await api.rejectProposal(proposalId);
      }
      // Reload.
      const proposals = await api.listProposals();
      const forReview = proposals.find((p: any) => p.review_id === reviewId);
      setLatest(forReview ?? null);
      setAllPending(proposals.filter((p: any) => p.status === "pending"));
      setPreviewing(null);
    } catch (e: any) {
      alert("失败: " + e.message);
    } finally {
      setBusy(false);
    }
  }

  async function loadPreview(proposalId: string) {
    try {
      const p = await api.getProposal(proposalId);
      setPreviewing(p);
    } catch (e: any) {
      alert("加载 prompt 详情失败: " + e.message);
    }
  }

  return (
    <div style={{marginTop: 16, padding: 14, background: "#f7f9fc",
                 borderRadius: 8, border: "1px solid #c8d6e5"}}>
      <div className="spread" style={{alignItems: "flex-start"}}>
        <div>
          <h3 style={{margin: 0}}>🔧 据此改 Prompt（自学习闭环）</h3>
          <div className="muted" style={{fontSize: 12, marginTop: 4}}>
            让 LLM 看完这轮复盘，提一份对当前出稿 prompt 的最小可行修改建议。
            <b>人工 approve 才会生效</b>，下次 Compose 自动用新版本。
          </div>
        </div>
        {!latest && (
          <button onClick={propose} disabled={busy || !reviewId}
            style={{whiteSpace: "nowrap"}}>
            {busy ? "🤖 提议中…" : "🪄 生成 Prompt 改进建议"}
          </button>
        )}
      </div>

      {err && (
        <div className="banner info" style={{marginTop: 10, fontSize: 12}}>{err}</div>
      )}

      {latest && (
        <div style={{marginTop: 12, padding: 12, background: "#fff", borderRadius: 6}}>
          <div className="spread" style={{alignItems: "baseline"}}>
            <div>
              <b style={{fontSize: 14}}>
                {latest.parent_version} → {latest.proposed_version}
              </b>
              <span className="muted" style={{fontSize: 11, marginLeft: 8}}>
                状态 ：{latest.status}
              </span>
            </div>
            {latest.status === "pending" && (
              <div className="row" style={{gap: 6}}>
                <button className="ghost" onClick={() => loadPreview(latest.proposal_id)}
                  style={{fontSize: 12, padding: "4px 10px"}}>查看完整 prompt</button>
                <button onClick={() => decide(latest.proposal_id, "approve")} disabled={busy}
                  style={{fontSize: 12, padding: "4px 10px"}}>✓ Approve</button>
                <button className="ghost" onClick={() => decide(latest.proposal_id, "reject")} disabled={busy}
                  style={{fontSize: 12, padding: "4px 10px", color: "var(--danger)"}}>✗ Reject</button>
              </div>
            )}
          </div>
          <div style={{marginTop: 8, fontSize: 13}}>{latest.diff_summary}</div>
          {latest.expected_gain && (
            <div style={{marginTop: 6, fontSize: 12, color: "#0a7"}}>
              📈 <b>预期 ：</b>{latest.expected_gain}
            </div>
          )}
          {latest.evidence?.length > 0 && (
            <details style={{marginTop: 8, fontSize: 12}}>
              <summary style={{cursor: "pointer"}}>📚 LLM 引用的复盘证据 ({latest.evidence.length})</summary>
              <ul style={{marginLeft: 18, marginTop: 4}}>
                {latest.evidence.map((e: any, i: number) => (
                  <li key={i} style={{marginBottom: 4}}>
                    <b>{e.signal}</b> → {e.why_changes_prompt}
                  </li>
                ))}
              </ul>
            </details>
          )}
          {previewing && previewing.proposal_id === latest.proposal_id && (
            <div style={{marginTop: 10, padding: 10, background: "#f0f4f8", borderRadius: 4}}>
              <div className="muted" style={{fontSize: 11, marginBottom: 4}}>完整新 prompt：</div>
              <pre style={{whiteSpace: "pre-wrap", fontSize: 11, lineHeight: 1.5, margin: 0}}>
                {previewing.proposed_prompt}
              </pre>
            </div>
          )}
        </div>
      )}

      {allPending.length > (latest ? 1 : 0) && (
        <details style={{marginTop: 10, fontSize: 12}}>
          <summary style={{cursor: "pointer"}}>
            还有 {allPending.length - (latest ? 1 : 0)} 个未决建议来自其它复盘
          </summary>
          <ul style={{marginLeft: 18, marginTop: 4}}>
            {allPending.filter(p => p.proposal_id !== latest?.proposal_id).map(p => (
              <li key={p.proposal_id} style={{marginBottom: 4}}>
                <code>{p.parent_version} → {p.proposed_version}</code> · {p.diff_summary}
                <button className="ghost" onClick={() => decide(p.proposal_id, "approve")}
                  disabled={busy} style={{fontSize: 11, marginLeft: 8, padding: "1px 6px"}}>approve</button>
                <button className="ghost" onClick={() => decide(p.proposal_id, "reject")}
                  disabled={busy} style={{fontSize: 11, marginLeft: 4, padding: "1px 6px", color: "var(--danger)"}}>reject</button>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

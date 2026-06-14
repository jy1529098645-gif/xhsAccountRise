import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { fmtTime, roleName, coerceStringList } from "../format";
import PlatformPill from "../components/PlatformPill";
import DouyinProvenancePanel from "../components/DouyinProvenancePanel";
import GroundedBody from "../components/GroundedBody";
import GroundingChip from "../components/GroundingChip";
import KpiBaselineChip from "../components/KpiBaselineChip";
import type { ComplianceHit, RagRef, RagComment, RagHook, VariantChild } from "../types";

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

  // v0.61.17 ：API 偶尔返回不完整 payload（比如 backend 报错但仍返回 200）。
  // 没有 draft 字段 → 整页崩成白屏。这里 fail-soft：给用户「数据有问题」
  // 卡片 + 重试按钮，而不是静默白屏。
  if (!data.draft) {
    return (
      <div className="card" style={{borderLeft: "4px solid var(--warn)"}}>
        <h2 style={{margin: 0}}>⚠️ 这份出稿数据看着不完整</h2>
        <p className="muted" style={{fontSize: 12.5, marginTop: 6}}>
          后端没返回 draft 字段。可能是 ID 失效 / DB 切换 / 后端版本不匹配。
        </p>
        <Link to="/drafts">← 回到全部历史出稿</Link>
        <details style={{marginTop: 8}}>
          <summary style={{fontSize: 11.5, color: "var(--muted)", cursor: "pointer"}}>
            ▾ 原始返回
          </summary>
          <pre style={{fontSize: 10.5, background: "#fafafa", padding: 8, maxHeight: 200, overflow: "auto"}}>
            {JSON.stringify(data, null, 2).slice(0, 1500)}
          </pre>
        </details>
      </div>
    );
  }

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

      <ComplianceBanner finalCand={finalCand} draftId={d.draft_id} onApplied={reload} />

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

      {/* v0.63: AI 参考的真实素材 — 用户专门提的需求，原本埋在第 8 位
          (Brief / Trace / Plan / Performance / Variant / Repurpose /
          Monetization / Douyin Provenance 之后) 用户根本看不到。
          上移到 Brief 之后做页面第 2 块卡片。Douyin 专属面板紧跟在它后面。 */}
      {finalCand?.douyin && (
        <DouyinProvenancePanel candidate={{...finalCand, douyin_struct: finalCand?.meta?.douyin_meta}} />
      )}

      <ProvenancePanel rag={data.rag} draftId={d.draft_id} onRefreshed={reload} />

      {trace.length > 0 && (
        <div className="card">
          <h2>Agent 时间线</h2>
          <div className="trace-list">
            {trace.map((s: any) => (
              <div key={s.trace_id} className={`step ${s.error ? "err" : ""}`}>
                <span>#{s.step_index}</span>
                <span className="agent">{roleName(s.agent_name)}</span>
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
          {/* Non-Claude models don't honor the planner JSON schema as
              strictly as Claude's tool_use does — these fields can come
              back as objects/strings instead of arrays. See Composer.tsx
              for the same pattern. */}
          {Array.isArray(plan.publish_schedule) && plan.publish_schedule.length > 0 && (
            <>
              <h3>📅 推荐发布时段</h3>
              <table className="table"><thead><tr><th>时段</th><th className="num">median likes</th><th>为什么</th></tr></thead><tbody>
                {plan.publish_schedule.map((s: any, i: number) => (
                  <tr key={i}>
                    <td><b>{typeof s === "string" ? s : s?.slot}</b></td>
                    <td className="num">{typeof s?.median_likes === "number" ? s.median_likes.toLocaleString() : "—"}</td>
                    <td className="muted">{typeof s === "string" ? "" : s?.why}</td>
                  </tr>
                ))}
              </tbody></table>
            </>
          )}
          {Array.isArray(plan.follow_up_angles) && plan.follow_up_angles.length > 0 && (
            <>
              <h3 style={{marginTop: 14}}>🔁 后续选题</h3>
              {plan.follow_up_angles.map((a: any, i: number) => (
                <div key={i} style={{padding: "10px 12px", background: "#fafafa", borderRadius: 6, marginBottom: 8}}>
                  <div style={{fontWeight: 600}}>{typeof a === "string" ? a : a?.title}</div>
                  {typeof a === "object" && a && (a.angle || a.hook_type) && (
                    <div style={{fontSize: 12, marginTop: 4}}>
                      {a.angle && <span className="tag-pill">{a.angle}</span>}
                      {a.hook_type && <span className="tag-pill">{a.hook_type}</span>}
                    </div>
                  )}
                  {typeof a === "object" && a?.why && (
                    <div className="muted" style={{fontSize: 12, marginTop: 6}}>{a.why}</div>
                  )}
                </div>
              ))}
            </>
          )}
          {(() => {
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

      <VariantFanOutCard
        draftId={d.draft_id}
        existing={data.variants ?? []}
        published={!!d.published}
        onSpawned={reload}
      />

      {/* v0.61.26 ：跨平台改稿。把这条稿改成 抖音 / B 站 / Reddit / X 版本。 */}
      <RepurposeCard draftId={d.draft_id} sourcePlatform={brief?.platform || "xiaohongshu"}
        onDone={reload} />

      {/* v0.61.27 ：变现套装 — 商单评估 + 引流话术 */}
      <MonetizationCard draftId={d.draft_id} />

      <div className="card">
        <h2>候选 ({cands.length})</h2>
        {/* v0.65 ：反黑盒图例 ─ 让用户第一眼就知道这些 chip 是什么 */}
        <div style={{
          fontSize: 11.5, color: "var(--muted)", marginTop: -4, marginBottom: 10,
          padding: "6px 10px", background: "var(--primary-soft)", borderRadius: 6,
          borderLeft: "3px solid var(--primary)",
        }}>
          💡 每条候选下都标了 <b>🟢/🟡/🔴 锚定度</b>（这条正文里有几处真实数据引用） +
          <b> vs 中位 ... 弱/良/强</b>（AI 预估互动 vs 本库同 hook 真实分布）。
          正文里的 <span style={{
            display: "inline-block", padding: "0 4px", borderRadius: 3,
            background: "var(--primary-soft)", color: "var(--primary)", fontWeight: 600, fontSize: 10.5,
          }}>#N</span> 小角标点开就跳来源贴 ─ AI 没瞎编。
        </div>
        <div className="candidate-grid">
          {cands.map((c: any) => {
            // Pre-compute how many [ref:xxx] markers we found in body.
            const refMarkerCount = (c.body?.match(/\[ref:[A-Za-z0-9_\-]+\]/g) || []).length;
            const hasRefs = (data.rag?.refs?.length ?? 0) > 0 && refMarkerCount > 0;
            return (
            <div key={c.candidate_id} className={`cand ${c.chosen ? "final" : ""} ${c.meta?.error ? "failed" : ""}`}>
              <div className="llm">{c.llm}{c.chosen ? " ★" : ""}</div>
              <div className="muted" style={{fontSize: 11}}>
                self {c.self_score?.toFixed?.(1) ?? "—"} ·
                ${c.meta?.cost_estimate_usd?.toFixed?.(4) ?? "0"} · {c.meta?.latency_ms ?? 0}ms
              </div>
              {/* v0.65 ：锚定度 + KPI 基线 chips ─ 反黑盒最直接的体感入口 */}
              <div style={{display: "flex", flexWrap: "wrap", gap: 6, marginTop: 6, alignItems: "center"}}>
                <GroundingChip score={c.meta?.grounding_score} breakdown={c.meta?.grounding_breakdown} compact />
                {c.meta?.kpi_baseline && (c.predicted_likes ?? 0) > 0 && (
                  <KpiBaselineChip predicted={c.predicted_likes ?? 0} baseline={c.meta.kpi_baseline} />
                )}
                {refMarkerCount > 0 && (
                  <span title="正文里 [ref:xxx] inline marker 出现次数 ─ AI 声明引用了这么多次真实数据"
                    style={{
                      fontSize: 10.5, color: "#15803d", fontWeight: 600,
                      padding: "0 6px", borderRadius: 3, background: "#dcfce7",
                      cursor: "help",
                    }}>
                    🔗 {refMarkerCount} 处数据引用
                  </span>
                )}
              </div>
              <div className="title">{c.title}</div>
              <div className="body">{renderWithHits(c.body, (c.compliance?.hits ?? []).filter((h: ComplianceHit) => h.where === "body"))}</div>
              {/* v0.65 (P1) ：「数据锚定视图」 ─ 同一段正文，把 [ref:xxx] inline marker
                  渲染成 chip 让用户点开看来源帖。compliance 高亮版照样保留在上面。
                  默认展开 ─ 有引用就直接展示，不再藏在 ▸ 后面。 */}
              {hasRefs && (
                <details style={{marginTop: 6}} open>
                  <summary style={{cursor: "pointer", fontSize: 11.5, color: "var(--primary)", fontWeight: 600}}>
                    🔗 数据锚定视图（{refMarkerCount} 处引用 · 点 chip 跳来源帖）
                  </summary>
                  <GroundedBody text={c.body || ""} refs={data.rag.refs}
                    style={{fontSize: 13, marginTop: 6, padding: "8px 10px",
                            background: "#fafafa", borderRadius: 4,
                            border: "1px dashed var(--primary)"}} />
                </details>
              )}
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
            );
          })}
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
    }).catch(e => console.error("[DraftDetail] performance", e));
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

      {/* v0.61.27 ：自动 fetch 已禁用避风控。手动填即可。 */}

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
function ComplianceBanner({finalCand, draftId, onApplied}: {
  finalCand: any; draftId: string; onApplied?: () => void;
}) {
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
        命中策略报告 6.4 红线词 {comp.hit_count} 处。下面是具体位置 + 一键安全改写。
      </p>
      <ComplianceHitTable hits={comp.hits ?? []}
        finalCand={finalCand} draftId={draftId} onApplied={onApplied} />
    </div>
  );
}

function ComplianceHitTable({hits, finalCand, draftId, onApplied}: {
  hits: ComplianceHit[];
  finalCand: any;
  draftId: string;
  onApplied?: () => void;
}) {
  // Per-where rewrite preview state. Key is just the where; values are the
  // resulting text after server-side rewrite_safe.
  const [rewritten, setRewritten] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);

  if (hits.length === 0) return null;

  // Read live text from the candidate so we don't need user paste — this was
  // a UX bug in v0.53.0 where we asked the user to manually paste their own
  // body back into a prompt() dialog.
  function liveTextFor(where: string): string {
    if (where === "title") return finalCand?.title ?? "";
    if (where === "body") return finalCand?.body ?? "";
    if (where === "cover_prompt") return finalCand?.cover_prompt ?? "";
    return "";
  }

  async function rewriteWhere(where: "title" | "body" | "cover_prompt") {
    const text = liveTextFor(where);
    if (!text) {
      alert(`${where} 是空的，没法改写`);
      return;
    }
    setBusy(where);
    try {
      // The /rewrite endpoint only knows 'title' | 'body'; cover_prompt
      // shares body rules.
      const apiWhere = (where === "title") ? "title" : "body";
      const r = await api.complianceRewrite(text, apiWhere);
      setRewritten(prev => ({...prev, [where]: r.rewritten}));
      if (!r.changed) {
        alert("没有命中红线词需要替换。");
      }
    } catch (e: any) {
      alert("改写失败: " + e.message);
    } finally {
      setBusy(null);
    }
  }

  async function copyToClipboard(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      alert("✓ 已复制到剪贴板");
    } catch {
      alert("复制失败 — 请手动选中并 Ctrl+C");
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
          <div style={{display: "flex", alignItems: "center", flexWrap: "wrap",
                       gap: 8, marginBottom: 6}}>
            <span style={{fontSize: 12, fontWeight: 600}}>
              {where} · {ws.length} 命中
            </span>
            {(where === "title" || where === "body" || where === "cover_prompt") && (
              <button className="ghost"
                style={{fontSize: 11, padding: "2px 10px"}}
                disabled={busy === where}
                onClick={() => rewriteWhere(where as any)}>
                {busy === where ? "改写中…" : "🔧 一键改写"}
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
          {rewritten[where] && (
            <div style={{marginTop: 8, padding: 8, background: "#f6fff0", borderRadius: 4, fontSize: 12}}>
              <div className="muted" style={{marginBottom: 4}}>改写后：</div>
              <div style={{whiteSpace: "pre-wrap", fontFamily: "monospace"}}>{rewritten[where]}</div>
              <button className="ghost" style={{marginTop: 6, fontSize: 11, padding: "2px 10px"}}
                onClick={() => copyToClipboard(rewritten[where])}>
                📋 复制改写后的文本
              </button>
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
const ALL_ANGLES = [
  "教程","痛点","故事","工具评测","对比","感悟","数字","种草","建议",
  "段子","科普","避雷","测评",
  "盘点","复盘","问答","打卡","教训",
];

// v0.61.27 ：变现套装卡 — 给当前 draft 评 「适合恰饭吗」 + 生成私域引流话术
function MonetizationCard({draftId}: {draftId: string}) {
  const [opened, setOpened] = useState(false);
  const [intent, setIntent] = useState<"none"|"soft_lead"|"hard_sell"|"brand_collab">("soft_lead");
  const [evalBusy, setEvalBusy] = useState(false);
  const [evalResult, setEvalResult] = useState<any>(null);
  const [scriptsBusy, setScriptsBusy] = useState(false);
  const [scriptsResult, setScriptsResult] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);

  async function runEval() {
    setEvalBusy(true); setErr(null);
    try {
      const r = await api.evaluateMonetization(draftId, {monetization_intent: intent});
      setEvalResult(r);
    } catch (e: any) {
      setErr("评估失败 ：" + (e?.message || String(e)));
    } finally {
      setEvalBusy(false);
    }
  }
  async function runScripts() {
    setScriptsBusy(true); setErr(null);
    try {
      const r = await api.generateLeadScripts(draftId);
      setScriptsResult(r);
    } catch (e: any) {
      setErr("生成话术失败 ：" + (e?.message || String(e)));
    } finally {
      setScriptsBusy(false);
    }
  }

  function copyText(s: string) {
    try { navigator.clipboard?.writeText(s); } catch { /* ignore */ }
  }

  return (
    <div className="card" style={{marginTop: 10}}>
      <div className="spread">
        <h2 style={{margin: 0}}>💰 变现 · 商单 + 私域引流</h2>
        <button className="ghost" onClick={() => setOpened(v => !v)}>
          {opened ? "收起" : "展开 →"}
        </button>
      </div>
      <p className="muted" style={{fontSize: 12, margin: "4px 0 0"}}>
        评这条稿适合接广告 / 恰饭吗 · 生成不踩平台违规线的私域引流话术
      </p>
      {opened && (
        <div style={{marginTop: 12}}>
          {err && <div className="banner danger" onClick={() => setErr(null)}>{err}</div>}

          <div className="row" style={{gap: 12, alignItems: "flex-end", marginBottom: 14}}>
            <div style={{flex: 1}}>
              <label>变现意图</label>
              <select value={intent} onChange={e => setIntent(e.target.value as any)}
                style={{width: "100%"}}>
                <option value="none">💚 none · 纯涨粉，不恰饭</option>
                <option value="soft_lead">🟡 soft_lead · 软引流到私域</option>
                <option value="hard_sell">🟠 hard_sell · 挂商单卖货</option>
                <option value="brand_collab">🔴 brand_collab · 品牌植入</option>
              </select>
            </div>
            <button onClick={runEval} disabled={evalBusy}
              style={{minWidth: 140, padding: "8px 16px"}}>
              {evalBusy ? "🤖 评估中…" : "💸 评商单适合度"}
            </button>
            <button onClick={runScripts} disabled={scriptsBusy}
              className="secondary"
              style={{minWidth: 140, padding: "8px 16px"}}>
              {scriptsBusy ? "🤖 生成中…" : "💬 生成引流话术"}
            </button>
          </div>

          {evalResult && (
            <div style={{marginBottom: 16, padding: 12,
                         background: evalResult.commercial_score >= 7 ? "#f0fff4"
                                   : evalResult.commercial_score >= 4 ? "#fff8e0"
                                   : "#ffe9e9",
                         borderRadius: 8, border: "1px solid #e0e0e0"}}>
              <div className="row" style={{justifyContent: "space-between", alignItems: "baseline", marginBottom: 8}}>
                <div>
                  <b style={{fontSize: 18}}>💸 商单适合度 ：{evalResult.commercial_score?.toFixed(1)} / 10</b>
                  <span className="muted" style={{marginLeft: 12, fontSize: 12}}>
                    价位 ：{evalResult.estimated_price_band}
                  </span>
                </div>
              </div>
              <div style={{fontSize: 13, marginBottom: 10}}>
                <b>判断 ：</b>{evalResult.verdict}
              </div>
              <div style={{display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 6, marginBottom: 10}}>
                {Object.entries(evalResult.factors || {}).map(([k, v]: any) => (
                  <div key={k} style={{padding: 6, background: "#fff", borderRadius: 4, textAlign: "center"}}>
                    <div className="muted" style={{fontSize: 10.5}}>{({
                      authenticity_preservation: "真实感", audience_friction: "用户反感",
                      conversion_path: "转化路径", compliance_risk: "合规", pricing_leverage: "议价",
                    } as any)[k] || k}</div>
                    <div style={{fontWeight: 600, fontSize: 14}}>{Number(v).toFixed(1)}</div>
                  </div>
                ))}
              </div>
              {evalResult.risks?.length > 0 && (
                <div style={{fontSize: 12, marginBottom: 6}}>
                  <b>⚠️ 风险 ：</b>
                  <ul style={{margin: "4px 0 0 18px"}}>
                    {evalResult.risks.map((r: string, i: number) => <li key={i}>{r}</li>)}
                  </ul>
                </div>
              )}
              {evalResult.suggestions?.length > 0 && (
                <div style={{fontSize: 12}}>
                  <b>💡 改进建议 ：</b>
                  <ul style={{margin: "4px 0 0 18px"}}>
                    {evalResult.suggestions.map((s: string, i: number) => <li key={i}>{s}</li>)}
                  </ul>
                </div>
              )}
            </div>
          )}

          {scriptsResult && (
            <div style={{padding: 12, background: "var(--primary-soft)", borderRadius: 8}}>
              <b style={{fontSize: 14}}>💬 私域引流话术（点 📋 复制）</b>
              {scriptsResult.comment_prompts?.length > 0 && (
                <div style={{marginTop: 10}}>
                  <div style={{fontSize: 12.5, fontWeight: 600, color: "var(--primary)"}}>
                    ▸ 评论区引导（自己评论自己稿件）
                  </div>
                  <div style={{display: "grid", gap: 4, marginTop: 4}}>
                    {scriptsResult.comment_prompts.map((p: string, i: number) => (
                      <div key={i} style={{padding: "6px 10px", background: "#fff", borderRadius: 4,
                                            display: "flex", justifyContent: "space-between", alignItems: "center", gap: 6}}>
                        <span style={{fontSize: 12.5, flex: 1}}>{p}</span>
                        <button className="ghost" onClick={() => copyText(p)}
                          style={{fontSize: 10.5, padding: "2px 8px", flexShrink: 0}}>📋</button>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {scriptsResult.dm_opener?.length > 0 && (
                <div style={{marginTop: 10}}>
                  <div style={{fontSize: 12.5, fontWeight: 600, color: "var(--primary)"}}>
                    ▸ 私信开场白
                  </div>
                  <div style={{display: "grid", gap: 4, marginTop: 4}}>
                    {scriptsResult.dm_opener.map((p: string, i: number) => (
                      <div key={i} style={{padding: "6px 10px", background: "#fff", borderRadius: 4,
                                            display: "flex", justifyContent: "space-between", alignItems: "center", gap: 6}}>
                        <span style={{fontSize: 12.5, flex: 1}}>{p}</span>
                        <button className="ghost" onClick={() => copyText(p)}
                          style={{fontSize: 10.5, padding: "2px 8px", flexShrink: 0}}>📋</button>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {scriptsResult.bio_oneliner && (
                <div style={{marginTop: 10}}>
                  <div style={{fontSize: 12.5, fontWeight: 600, color: "var(--primary)"}}>
                    ▸ 主页 bio 一句话
                  </div>
                  <div style={{padding: "6px 10px", background: "#fff", borderRadius: 4, marginTop: 4,
                                display: "flex", justifyContent: "space-between", alignItems: "center", gap: 6}}>
                    <span style={{fontSize: 12.5, flex: 1}}>{scriptsResult.bio_oneliner}</span>
                    <button className="ghost" onClick={() => copyText(scriptsResult.bio_oneliner)}
                      style={{fontSize: 10.5, padding: "2px 8px", flexShrink: 0}}>📋</button>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// v0.61.26 ：跨平台改稿卡。把当前 draft 的 final candidate 改写成另一个
// 平台的版本。target_lib_id 可选 ：用户有那个平台的 lib 就传，AI 会拉真实
// 爆款做 voice 锚；不传就靠 AI 通用平台 prompt 生成（质量略次但能用）。
function RepurposeCard({draftId, sourcePlatform, onDone}: {
  draftId: string;
  sourcePlatform: string;
  onDone: () => void;
}) {
  const [opened, setOpened] = useState(false);
  const [target, setTarget] = useState<string>("");
  const [targetLibId, setTargetLibId] = useState<string>("");
  const [libs, setLibs] = useState<Array<{lib_id: string; display_name: string; platform: string}>>([]);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<any>(null);

  useEffect(() => {
    if (opened && libs.length === 0) {
      api.libraries().then(setLibs as any).catch(e => console.error("[DraftDetail] libraries", e));
    }
  }, [opened, libs.length]);

  const PLATFORMS = [
    {id: "xiaohongshu", label: "📕 小红书"},
    {id: "douyin", label: "🎵 抖音"},
    {id: "kuaishou", label: "📹 快手"},
    {id: "bilibili", label: "📺 B站"},
    {id: "youtube", label: "🎬 YouTube"},
    {id: "reddit", label: "🤖 Reddit"},
    {id: "x", label: "𝕏 Twitter"},
  ].filter(p => p.id !== sourcePlatform);

  // 默认 target — 第一个不是 source 的
  useEffect(() => {
    if (opened && !target && PLATFORMS.length > 0) {
      setTarget(PLATFORMS[0].id);
    }
  }, [opened, target, PLATFORMS]);

  // 当 target 变化时自动找该平台的 lib（如果有）
  useEffect(() => {
    if (!target) return;
    const matching = libs.find(l => l.platform === target);
    setTargetLibId(matching?.lib_id ?? "");
  }, [target, libs]);

  async function run() {
    if (!target) { alert("先选目标平台"); return; }
    setBusy(true); setResult(null);
    try {
      const r = await api.repurposeDraft(draftId, {
        target_platform: target,
        target_lib_id: targetLibId || null,
      });
      setResult(r);
      onDone();
    } catch (e: any) {
      alert("改稿失败 ：" + e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card" style={{marginTop: 10}}>
      <div className="spread">
        <h2 style={{margin: 0}}>🔄 跨平台改稿 · 一稿多发</h2>
        <button className="ghost" onClick={() => setOpened(v => !v)}>
          {opened ? "收起" : "展开 →"}
        </button>
      </div>
      <p className="muted" style={{fontSize: 12, margin: "4px 0 0"}}>
        把这条稿改成另一个平台版本（voice + 长度 + 格式 自动迁移）。
        有目标平台的 library 时会拉真实爆款做 voice 锚，没有就靠 AI 通用平台风格生成。
      </p>
      {opened && (
        <div style={{marginTop: 12}}>
          <div className="row" style={{gap: 8, marginBottom: 10, alignItems: "flex-end"}}>
            <div style={{flex: 1}}>
              <label>目标平台</label>
              <select value={target} onChange={e => setTarget(e.target.value)}
                style={{width: "100%"}}>
                {PLATFORMS.map(p => (
                  <option key={p.id} value={p.id}>{p.label}</option>
                ))}
              </select>
              <div className="muted" style={{fontSize: 11, marginTop: 2}}>
                源 ：{sourcePlatform} → 目标 ：{target || "选一个"}
              </div>
            </div>
            <div style={{flex: 1}}>
              <label>目标平台的 library（可选）</label>
              <select value={targetLibId} onChange={e => setTargetLibId(e.target.value)}
                style={{width: "100%"}}>
                <option value="">无 · AI 通用平台风格生成</option>
                {libs.filter(l => l.platform === target).map(l => (
                  <option key={l.lib_id} value={l.lib_id}>
                    {l.display_name}
                  </option>
                ))}
              </select>
              <div className="muted" style={{fontSize: 11, marginTop: 2}}>
                {libs.filter(l => l.platform === target).length === 0
                  ? "你没上传过这个平台的 lib · AI 据通用模板生成"
                  : "有 lib → AI 会按真实爆款 voice 改写（更准）"}
              </div>
            </div>
            <button onClick={run} disabled={busy || !target}
              style={{minWidth: 140, padding: "8px 16px"}}>
              {busy ? "🤖 改稿中…(约 20-40s)" : "🚀 开始改稿"}
            </button>
          </div>
          {result && (
            <div style={{marginTop: 12, padding: 12, background: "#f0fff4",
                         border: "1px solid var(--ok)", borderRadius: 8}}>
              <div className="spread">
                <b>✓ 改稿成功（{result.elapsed_s}s）</b>
                <Link to={`/drafts/${result.child_draft_id}`}>
                  <button className="secondary">查看新稿 →</button>
                </Link>
              </div>
              {result.rationale && (
                <div className="muted" style={{fontSize: 12, marginTop: 6}}>
                  💡 改稿说明 ：{result.rationale}
                </div>
              )}
              <details style={{marginTop: 8, fontSize: 13}}>
                <summary style={{cursor: "pointer", fontWeight: 600}}>
                  ▾ 预览新稿
                </summary>
                <div style={{marginTop: 6, fontSize: 13.5, fontWeight: 600}}>{result.payload?.title}</div>
                <div style={{marginTop: 4, fontSize: 12.5, lineHeight: 1.7, whiteSpace: "pre-wrap",
                              padding: 8, background: "#fff", borderRadius: 4}}>
                  {result.payload?.body}
                </div>
              </details>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

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
function ProvenancePanel({rag, draftId, onRefreshed}: {
  rag?: {refs: RagRef[]; comments: RagComment[]; hooks: RagHook[]};
  draftId?: string;
  onRefreshed?: () => void;
}) {
  const [refreshing, setRefreshing] = useState(false);
  const [refreshNote, setRefreshNote] = useState<string | null>(null);
  const [autoTried, setAutoTried] = useState(false);
  const refs = rag?.refs ?? [];
  const comments = rag?.comments ?? [];
  const hooks = rag?.hooks ?? [];
  const hasAny = refs.length > 0 || comments.length > 0 || hooks.length > 0;
  const fmt = (n: number | undefined) =>
    !n ? "0" : n >= 10000 ? (n / 10000).toFixed(1) + "w" : n >= 1000 ? (n / 1000).toFixed(1) + "k" : String(n);

  async function refresh() {
    if (!draftId) return;
    setRefreshing(true); setRefreshNote(null);
    try {
      const r = await api.backfillDraftRag(draftId);
      setRefreshNote(
        `✓ 已加载 ${r.refs} 篇参考帖、${r.comments} 条评论、${r.hooks} 个 hook` +
        (r.with_images > 0 ? ` ·${r.with_images} 篇有图文` : "")
      );
      onRefreshed?.();
    } catch (e: any) {
      setRefreshNote("✗ 加载失败 ：" + (e?.message || e));
    } finally {
      setRefreshing(false);
    }
  }

  // v0.63: 老 draft 没持久化 rag 时，自动 backfill 一次（用户专门提的
  // 功能不应该让他先看到「空」再去点按钮）。autoTried 防止 reload 后又
  // 触发死循环。
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (draftId && !hasAny && !autoTried && !refreshing) {
      setAutoTried(true);
      refresh();
    }
  }, [draftId, hasAny, autoTried]);

  // v0.63: 老 draft (pre-v0.55) 没持久化 rag → 显示一个邀请用户点 "刷新参考数据"
  // 的占位卡片，而不是悄悄隐藏整个面板。
  if (!hasAny) {
    if (!draftId) return null;
    return (
      <div className="card" style={{marginTop: 12, borderLeft: "4px solid var(--primary)",
                                     background: "var(--primary-soft)"}}>
        <div className="spread" style={{alignItems: "center"}}>
          <div>
            <h2 style={{margin: 0, fontSize: 14, color: "var(--primary)"}}>
              📚 AI 参考的真实素材
              {refreshing && <span style={{marginLeft: 10, fontSize: 12, fontWeight: 400}}>· 加载中…</span>}
            </h2>
            <p className="muted" style={{fontSize: 12, margin: "4px 0 0"}}>
              {refreshing
                ? "正在按 brief 主题去资源库 FTS 检索真实爆款帖 + 抽取封面图…（5-10 秒）"
                : (refreshNote ||
                   "这条稿子是 pre-v0.55 老稿，还没持久化参考数据 — 加载中会显示真实爆款帖的封面图 + 标题 + 互动数据 + tags。")}
            </p>
          </div>
          <button onClick={refresh} disabled={refreshing} style={{fontSize: 12, whiteSpace: "nowrap"}}>
            {refreshing ? "🤖 检索中…" : "🔄 立即加载"}
          </button>
        </div>
      </div>
    );
  }
  return (
    // v0.65.3: 跟 Composer.tsx 对齐 ─ 起号策略页已显示「真实爆款 + 图卡片」，
    // 这里只保留 「评论原话 + 来源原贴链接 + 原贴互动数据」，refs grid 改成折叠区供需要时翻看。
    <details className="card" open style={{marginTop: 12, borderLeft: "4px solid var(--primary)"}}>
      <summary style={{cursor: "pointer", fontSize: 14.5, fontWeight: 700, color: "var(--primary)"}}>
        📚 AI 这篇稿子听到的真实用户原话
        <span style={{marginLeft: 8, fontSize: 12, color: "var(--muted)", fontWeight: 400}}>
          {comments.length} 条评论（含原贴链接 + 数据） · {refs.length} 篇爆款参考折叠 · {hooks.length} 个 hook 模板
        </span>
        {draftId && (
          <button onClick={(e) => { e.preventDefault(); e.stopPropagation(); refresh(); }}
            disabled={refreshing}
            className="ghost"
            title="按当前 brief 主题重新检索 RAG，刷新数据"
            style={{marginLeft: 12, fontSize: 11, padding: "2px 8px"}}>
            {refreshing ? "刷新中…" : "🔄 刷新"}
          </button>
        )}
      </summary>
      {refreshNote && (
        <div className="muted" style={{fontSize: 11.5, marginTop: 6}}>{refreshNote}</div>
      )}
      <p className="muted" style={{fontSize: 12, margin: "8px 0 12px"}}>
        这一稿不是凭空写的 ──下面是 AI 起草时实际读到的高赞评论 + 每条来源原贴的真实数据。
      </p>
      {comments.length > 0 ? (
        <ul style={{margin: 0, paddingLeft: 0, listStyle: "none"}}>
          {comments.slice(0, 15).map((c, idx) => {
            const src = c.source_note;
            return (
              <li key={c.comment_id || idx} style={{
                padding: "8px 10px", marginBottom: 6,
                background: "#fafafa", borderRadius: 6,
                borderLeft: "3px solid var(--primary)",
              }}>
                <div style={{fontSize: 13, lineHeight: 1.65}}>
                  <span style={{
                    fontSize: 10.5, padding: "0 5px", borderRadius: 3,
                    background: "var(--primary-soft)", color: "var(--primary)",
                    fontWeight: 600, marginRight: 6,
                  }}>{c.like_count}👍</span>
                  {c.content}
                </div>
                {src && (
                  <div style={{
                    marginTop: 6, paddingTop: 6, borderTop: "1px dashed #ddd",
                    fontSize: 11.5, color: "#555",
                    display: "flex", flexWrap: "wrap", gap: 8, alignItems: "baseline",
                  }}>
                    <span className="muted" style={{fontSize: 10.5, flexShrink: 0}}>📎 来源原贴：</span>
                    {src.url ? (
                      <a href={src.url} target="_blank" rel="noreferrer"
                        style={{flex: 1, minWidth: 0, fontWeight: 600, color: "var(--primary)",
                                overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap"}}>
                        {src.title || "（无标题）"}
                      </a>
                    ) : (
                      <span style={{flex: 1, minWidth: 0, fontWeight: 600,
                                    overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap"}}>
                        {src.title || "（无标题）"}
                      </span>
                    )}
                    <span className="muted" style={{fontSize: 10.5, whiteSpace: "nowrap"}}>
                      👍 {fmt(src.liked_count)}
                      {(src.collected_count ?? 0) > 0 && <> · ⭐ {fmt(src.collected_count)}</>}
                      {(src.comment_count ?? 0) > 0 && <> · 💬 {fmt(src.comment_count)}</>}
                      {(src.share_count ?? 0) > 0 && <> · 🔁 {fmt(src.share_count)}</>}
                      {(src.duration_sec ?? 0) > 0 && <> · ▶︎ {src.duration_sec}s</>}
                      {src.author_nickname && <> · @{src.author_nickname}</>}
                    </span>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      ) : (
        <div className="muted" style={{
          fontSize: 12, padding: "6px 10px", background: "#fff8e6", borderRadius: 4,
          borderLeft: "3px solid #f6c265",
        }}>
          这次 brief 主题在本库评论里没匹配到 ─ 可能 ：(a) xlsx 导入库没评论 ，
          (b) 主题词太冷门。可点上方 「🔄 刷新」 重试。
        </div>
      )}
      {/* refs 仍然保留 ，但折叠 ─ 用户主要看评论 ，需要看爆款时展开。 */}
      {refs.length > 0 && (
        <details style={{marginTop: 12}}>
          <summary style={{cursor: "pointer", fontSize: 12.5, color: "var(--muted)"}}>
            ▸ 看 {refs.length} 篇爆款参考（起号策略页已有详细图卡 ，这里只列表）
          </summary>
          <ul style={{margin: "6px 0 0", paddingLeft: 0, listStyle: "none", fontSize: 12, lineHeight: 1.65}}>
            {refs.map((r, idx) => (
              <li key={r.note_id} style={{
                padding: "4px 8px", borderBottom: "1px solid #f3f3f3",
                background: r.is_benchmark ? "#fff8e1" : undefined,
              }}>
                <div style={{display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap"}}>
                  <span style={{fontSize: 10.5, color: "var(--muted)", flexShrink: 0}}>#{idx + 1}</span>
                  {r.is_benchmark && <span style={{fontSize: 10.5, flexShrink: 0}} title="对标账号">🎯</span>}
                  <span style={{flex: 1, minWidth: 0, fontWeight: 600,
                                overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap"}}>
                    {r.url ? <a href={r.url} target="_blank" rel="noreferrer" style={{color: "inherit"}}>{r.title || "（无标题）"}</a>
                          : (r.title || "（无标题）")}
                  </span>
                  <span className="muted" style={{fontSize: 10.5, flexShrink: 0, whiteSpace: "nowrap"}}>
                    👍 {fmt(r.liked_count)}
                    {(r.collected_count ?? 0) > 0 && <> · ⭐ {fmt(r.collected_count)}</>}
                    {(r.comment_count ?? 0) > 0 && <> · 💬 {fmt(r.comment_count)}</>}
                    {(r.share_count ?? 0) > 0 && <> · 🔁 {fmt(r.share_count)}</>}
                  </span>
                </div>
              </li>
            ))}
          </ul>
        </details>
      )}
        {hooks.length > 0 && (
          <>
            <h3 style={{margin: "12px 0 4px", fontSize: 13}}>🎣 Hook 模板</h3>
            <table className="table">
              <thead><tr>
                <th>类型</th><th className="num">样本数</th><th className="num">中位赞</th><th>示例</th>
              </tr></thead>
              <tbody>
                {hooks.map(h => (
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
    </details>
  );
}

// v0.61.27 ：原 RefreshFromUrl 自动 fetch 组件已删除。原因 ：
// 抓取小红书后台数据需要 curl_cffi + chrome131 impersonation + 登录 cookie，
// 风控风险（限流 / ban 账号）大于收益。数据回流改为纯手动填。

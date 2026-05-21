/**
 * v0.57 — Douyin draft provenance.
 *
 * Sits at the top of DraftDetail when the candidate was generated through
 * the Douyin pipeline. Surfaces three layers of "where this draft came
 * from":
 *
 *   1. 🎯 Content bucket — which of the 6 baseline buckets the draft is
 *      targeting (AI工具教程 / 情绪段子 / etc.), with that bucket's real-world
 *      中位 / P90 / 爆款率 / 收藏赞比 / 分享赞比 baseline numbers.
 *
 *   2. 📈 Predicted KPI — what the AI estimated for THIS draft on
 *      赞粉比 / 收藏赞比 / 分享赞比 / 评论赞比, rendered as colored chips
 *      against the playbook thresholds (weak / good / strong).
 *
 *   3. 📝 Library titles inspired — which entries from the 1380-hook
 *      hand-curated library the AI cited as inspiration, with category
 *      chips so the user can see "AI 真的用了标题库" not just claims it.
 *
 *   4. 🎬 Shot list / hook_3s / cta_voice — the structured fields the LLM
 *      returned (vs the xhs body blob). Already lives in candidate.body
 *      as pretty text, but the panel formats it as a timeline.
 *
 * Renders nothing when candidate.douyin is absent (xhs path).
 */
import { useState } from "react";

interface DouyinShot {
  t: string;
  voice: string;
  visual: string;
}

interface DouyinLibraryTitle {
  title_id: number;
  category: string;
  title: string;
  hashtags?: string[];
  char_len?: number;
}

interface DouyinMeta {
  candidate_id: string;
  content_bucket_id: string;
  content_bucket_label: string;
  predicted_metrics: {
    "赞粉比"?: number;
    "收藏赞比"?: number;
    "分享赞比"?: number;
    "评论赞比"?: number;
  };
  duration_sec?: number;
  hashtags: string[];
  library_titles: DouyinLibraryTitle[];
}

// Thresholds mirror studio/douyin/playbook.py KPI_THRESHOLDS. Kept in
// sync manually — if you re-tune the backend numbers, update these too.
const KPI_THRESHOLDS: Record<string, { good: number; strong: number; weak_hint: string; good_hint: string; strong_hint: string }> = {
  "赞粉比":   { good: 0.20, strong: 1.00, weak_hint: "<20% 基础盘内部",   good_hint: "20-100% 选题有效",   strong_hint: ">100% 强破圈" },
  "收藏赞比": { good: 0.15, strong: 0.30, weak_hint: "<15% 沉淀价值低",   good_hint: "15-30% 有工具价值", strong_hint: ">30% 强教程价值" },
  "分享赞比": { good: 0.10, strong: 0.20, weak_hint: "<10% 社交价值弱",   good_hint: "10-20% 有共鸣",     strong_hint: ">20% 强社交货币" },
  "评论赞比": { good: 0.05, strong: 0.15, weak_hint: "<5% 讨论冷",        good_hint: "5-15% 有共鸣/求助", strong_hint: ">15% 强讨论" },
};

function bandFor(metric: string, value: number): { band: "weak" | "good" | "strong"; hint: string } {
  const t = KPI_THRESHOLDS[metric];
  if (!t) return { band: "weak", hint: "" };
  if (value >= t.strong) return { band: "strong", hint: t.strong_hint };
  if (value >= t.good)   return { band: "good",   hint: t.good_hint };
  return { band: "weak", hint: t.weak_hint };
}

function bandColor(band: "weak" | "good" | "strong"): { bg: string; fg: string } {
  return band === "strong" ? { bg: "#fff4dc", fg: "#b06200" }
       : band === "good"   ? { bg: "var(--primary-soft)", fg: "var(--primary)" }
                            : { bg: "#f4f4f4", fg: "#888" };
}

const PCT = (v: number) => `${(v * 100).toFixed(1)}%`;
const FMT = (n: number) => n >= 10000 ? `${(n / 10000).toFixed(1)}w` : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);

export default function DouyinProvenancePanel({
  candidate,
}: {
  candidate: any;
}) {
  const dy: DouyinMeta | undefined = candidate?.douyin;
  const meta = candidate?.meta?.error ? null : candidate;
  if (!dy || !meta) return null;

  // The structured meta is also embedded in candidate.body as readable text;
  // but we ALSO want to parse the original payload's douyin_meta for shots /
  // hook_3s / cta_voice. Those come from CandidatePayload.douyin_meta on the
  // backend — exposed through candidate.meta_extras when the LLM returned
  // them. Fall back to body-string parsing when not present.
  const struct = candidate?.meta?.douyin_meta ?? candidate?.douyin_struct;
  const shots: DouyinShot[] = struct?.shots ?? [];
  const hook3s: string = struct?.hook_3s ?? "";
  const cta: string = struct?.cta_voice ?? "";
  const caption: string = struct?.caption ?? candidate?.title ?? "";
  const coverText: string = struct?.cover_text ?? "";

  return (
    <div className="card" style={{borderLeft: "4px solid var(--primary)", marginTop: 12}}>
      <div className="spread" style={{alignItems: "baseline"}}>
        <h2 style={{margin: 0}}>🎵 抖音视频脚本 · Provenance</h2>
        <span className="muted" style={{fontSize: 11.5}}>
          基于 10091 视频 playbook + 1380 条标题库
        </span>
      </div>

      <BucketCard dy={dy} />
      <PredictedKpiRow dy={dy} />

      {(hook3s || shots.length > 0 || cta) && (
        <ShotsTimeline caption={caption} hook3s={hook3s} shots={shots}
          cta={cta} coverText={coverText} duration={dy.duration_sec} />
      )}

      {dy.hashtags && dy.hashtags.length > 0 && (
        <div style={{marginTop: 12}}>
          <div className="muted" style={{fontSize: 12, marginBottom: 4}}>📍 推荐 hashtags（按分布规则挑选）</div>
          <div className="row" style={{gap: 6, flexWrap: "wrap"}}>
            {dy.hashtags.map((t, i) => (
              <span key={i} className="tag-pill" style={{background: "var(--primary-soft)", color: "var(--primary)"}}>
                #{t}
              </span>
            ))}
          </div>
        </div>
      )}

      {dy.library_titles && dy.library_titles.length > 0 && (
        <LibraryTitlesPanel titles={dy.library_titles} />
      )}
    </div>
  );
}

function BucketCard({dy}: {dy: DouyinMeta}) {
  // We don't have the full bucket baselines on the frontend without an
  // extra fetch; the backend persists `content_bucket_label` and `_id` only.
  // Hard-code the 6 known baselines here (lifted verbatim from playbook.py).
  const BUCKETS: Record<string, {median: number; p90: number; viral: number; save: number; share: number; tip: string}> = {
    emotion_drama:      {median: 2104, p90: 64000, viral: 0.400, save: 0.049, share: 0.093, tip: "强情绪驱动，破圈测试用，不作主线"},
    service_conversion: {median: 254,  p90: 12000, viral: 0.087, save: 0.202, share: 0.096, tip: "信任铺垫够再上，做转化"},
    ai_tutorial:        {median: 155,  p90: 28000, viral: 0.139, save: 0.485, share: 0.118, tip: "★ 主桶 — 收藏赞比全样本最高 48.5%"},
    academic_writing:   {median: 103,  p90: 50000, viral: 0.258, save: 0.175, share: 0.080, tip: "细分技能型，rubric/reference/methodology"},
    ddl_panic:          {median: 53,   p90: 17000, viral: 0.110, save: 0.062, share: 0.055, tip: "情绪开头 + 中段引出工具 + 结尾共鸣"},
    lifestyle_identity: {median: 23,   p90: 1529,  viral: 0.038, save: 0.133, share: 0.063, tip: "贴标签用，不作主线"},
  };
  const b = BUCKETS[dy.content_bucket_id];
  return (
    <div style={{
      marginTop: 12, padding: "12px 14px",
      background: "linear-gradient(135deg, var(--primary-soft) 0%, #fafafa 100%)",
      borderRadius: 8, border: "1px solid var(--primary)",
    }}>
      <div className="spread" style={{alignItems: "baseline"}}>
        <div>
          <div style={{fontSize: 11, color: "var(--muted)"}}>🎯 内容桶 · content_bucket</div>
          <div style={{fontWeight: 700, fontSize: 16, color: "var(--primary)"}}>
            {dy.content_bucket_label} <span className="muted" style={{fontSize: 11, fontWeight: 400}}>({dy.content_bucket_id})</span>
          </div>
        </div>
        {dy.duration_sec && (
          <span className="tag-pill" style={{fontSize: 11}}>目标 {dy.duration_sec}s</span>
        )}
      </div>
      {b && (
        <>
          <div style={{display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 8, marginTop: 10, fontSize: 12}}>
            <BaselineBox label="中位总互动"  value={FMT(b.median)} />
            <BaselineBox label="P90 总互动"  value={FMT(b.p90)} />
            <BaselineBox label="爆款率"      value={`${(b.viral * 100).toFixed(1)}%`} />
            <BaselineBox label="中位 收藏赞比" value={PCT(b.save)} />
            <BaselineBox label="中位 分享赞比" value={PCT(b.share)} />
          </div>
          <div className="muted" style={{fontSize: 11.5, marginTop: 8, lineHeight: 1.55}}>
            💡 {b.tip}
          </div>
        </>
      )}
    </div>
  );
}

function BaselineBox({label, value}: {label: string; value: string}) {
  return (
    <div style={{background: "#fff", borderRadius: 6, padding: "6px 8px"}}>
      <div className="muted" style={{fontSize: 10}}>{label}</div>
      <div style={{fontSize: 14, fontWeight: 600, marginTop: 2}}>{value}</div>
    </div>
  );
}

function PredictedKpiRow({dy}: {dy: DouyinMeta}) {
  const pm = dy.predicted_metrics || {};
  const entries = (["赞粉比", "收藏赞比", "分享赞比", "评论赞比"] as const)
    .map((k) => ({key: k, value: pm[k] ?? 0}));
  return (
    <div style={{marginTop: 12}}>
      <div className="muted" style={{fontSize: 12, marginBottom: 4}}>
        📈 AI 预估 KPI · 跟桶基线对比
      </div>
      <div style={{display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 8}}>
        {entries.map(({key, value}) => {
          const b = bandFor(key, value);
          const c = bandColor(b.band);
          return (
            <div key={key} style={{
              padding: "8px 10px", borderRadius: 6,
              background: c.bg, color: c.fg,
              border: `1px solid ${b.band === "weak" ? "#e0e0e0" : "transparent"}`,
            }}>
              <div style={{fontSize: 11, opacity: 0.85}}>{key}</div>
              <div style={{fontSize: 18, fontWeight: 700, marginTop: 2}}>{PCT(value)}</div>
              <div style={{fontSize: 10.5, marginTop: 2, opacity: 0.85}}>{b.hint}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ShotsTimeline({
  caption, hook3s, shots, cta, coverText, duration,
}: {
  caption: string; hook3s: string; shots: DouyinShot[];
  cta: string; coverText: string; duration?: number;
}) {
  const [open, setOpen] = useState(true);
  return (
    <details open={open} onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)} style={{marginTop: 12}}>
      <summary style={{cursor: "pointer", fontSize: 13, fontWeight: 600, color: "var(--primary)"}}>
        🎬 分镜脚本 · {shots.length} 镜 · 目标 {duration ?? "?"}s
      </summary>
      <div style={{marginTop: 8, padding: 12, background: "#fafafa", borderRadius: 6}}>
        {coverText && (
          <div style={{padding: "8px 10px", background: "#fff4dc",
                       border: "1px solid #f6c265", borderRadius: 6, marginBottom: 10, fontSize: 13}}>
            🖼️ <b>封面贴片</b>：{coverText}
          </div>
        )}
        {caption && (
          <div style={{padding: "8px 10px", background: "#fff",
                       border: "1px solid var(--border)", borderRadius: 6, marginBottom: 10, fontSize: 13}}>
            📝 <b>caption（视频下方文案）</b>：{caption}
          </div>
        )}
        {hook3s && (
          <div style={{padding: "8px 10px", background: "var(--primary-soft)",
                       borderRadius: 6, marginBottom: 10, fontSize: 13.5, fontWeight: 600, color: "var(--primary)"}}>
            ⚡ 前 3 秒钩子：{hook3s}
          </div>
        )}
        {shots.length > 0 && (
          <ol style={{listStyle: "none", margin: 0, padding: 0}}>
            {shots.map((s, i) => (
              <li key={i} style={{
                padding: "10px 0", borderBottom: i < shots.length - 1 ? "1px dashed #ddd" : "none",
                display: "grid", gridTemplateColumns: "60px 1fr", gap: 10,
              }}>
                <div style={{fontSize: 12, fontWeight: 700, color: "var(--primary)", whiteSpace: "nowrap"}}>
                  {s.t || `#${i + 1}`}
                </div>
                <div style={{fontSize: 13, lineHeight: 1.5}}>
                  <div><b style={{color: "#555"}}>口播：</b>{s.voice}</div>
                  <div style={{marginTop: 3}}><b style={{color: "#555"}}>画面：</b>{s.visual}</div>
                </div>
              </li>
            ))}
          </ol>
        )}
        {cta && (
          <div style={{padding: "8px 10px", background: "#fff",
                       border: "1px dashed var(--primary)", borderRadius: 6, marginTop: 10, fontSize: 13}}>
            🎯 <b>结尾口播</b>：{cta}
          </div>
        )}
      </div>
    </details>
  );
}

function LibraryTitlesPanel({titles}: {titles: DouyinLibraryTitle[]}) {
  return (
    <details style={{marginTop: 12}}>
      <summary style={{cursor: "pointer", fontWeight: 600, fontSize: 13}}>
        📝 AI 借鉴的标题库条目（{titles.length}） · 来自 1380 条 hand-curated 抖音 hook 池
      </summary>
      <div style={{marginTop: 8, display: "grid",
                   gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 8}}>
        {titles.map((t) => (
          <div key={t.title_id} style={{
            padding: "8px 10px", borderRadius: 6,
            background: "#fafafa", border: "1px solid #eee",
          }}>
            <div style={{fontSize: 10, color: "var(--muted)", marginBottom: 4}}>
              #{t.title_id} · {t.category}
            </div>
            <div style={{fontSize: 13, lineHeight: 1.45}}>{t.title}</div>
            {t.hashtags && t.hashtags.length > 0 && (
              <div style={{marginTop: 4, fontSize: 10}}>
                {t.hashtags.map((h, i) => (
                  <span key={i} className="tag-pill" style={{fontSize: 10}}>#{h}</span>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
      <div className="muted" style={{fontSize: 11, marginTop: 6}}>
        💡 AI 学习了这些 hook 的句式与节奏，写出新版 caption / hook_3s — 你可以对照看出它的灵感来源。
      </div>
    </details>
  );
}

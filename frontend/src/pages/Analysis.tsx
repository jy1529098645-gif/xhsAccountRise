import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { fmtLikes } from "../format";
import type { DnaArtifact } from "../types";

export default function Analysis() {
  const [dna, setDna] = useState<DnaArtifact | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.dnaLatest().then(setDna).catch(e => setErr(e.message));
  }, []);

  if (err) return <div className="banner danger">{err}</div>;
  if (!dna) return <div className="card muted">加载中…</div>;

  const sections = dna.sections ?? {} as any;
  const summary = dna.summary ?? {} as any;
  const titles = sections.titles ?? {};
  const blueocean = sections.keyword_blueocean?.rankings ?? [];
  const tags = sections.tags?.top_tags ?? [];
  const tagPairs = sections.tags?.top_pairs ?? [];
  const topPerf = sections.top_performers ?? {};
  const timing = sections.timing ?? {};
  const shape = sections.body_and_shape ?? {};
  const comments = sections.comment_demand ?? {};

  return (
    <div>
      <div className="page-header">
        <h1>爆款 DNA · v{dna.version}</h1>
        <p>{(summary.total_notes_analysed ?? 0).toLocaleString()} 条 notes 全量分析 · 耗时 {summary.generated_in_seconds ?? "?"}s</p>
      </div>

      <Section title="1 · 标题 hook 分布">
        <HookDist dist={titles.primary_distribution ?? {}} byCat={titles.by_category ?? {}} />
        <h3>Top 30 标题（按 likes）</h3>
        <table className="table">
          <thead>
            <tr><th>#</th><th>标题</th><th className="num">likes</th><th className="num">cmt</th><th>hook</th></tr>
          </thead>
          <tbody>
            {(titles.top_titles ?? []).slice(0, 30).map((t: any, i: number) => (
              <tr key={t.note_id}>
                <td className="num">{i + 1}</td>
                <td>{t.title}</td>
                <td className="num">{fmtLikes(t.liked)}</td>
                <td className="num">{fmtLikes(t.commented)}</td>
                <td>{(t.hook_matched ?? []).map((h: string) => <span key={h} className="tag-pill">{h}</span>)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>

      <Section title="2 · 关键词蓝海">
        <p className="muted">分数 = avg_likes / log2(n + 2)。分数高 = 该词供给少 + 单帖回报高，最适合切入。</p>
        <table className="table">
          <thead>
            <tr><th>#</th><th>关键词</th><th className="num">n</th><th className="num">avg</th><th className="num">median</th><th className="num">p90</th><th className="num">score</th><th></th></tr>
          </thead>
          <tbody>
            {blueocean.slice(0, 30).map((r: any, i: number) => (
              <tr key={r.keyword}>
                <td className="num">{i + 1}</td>
                <td>{r.keyword}</td>
                <td className="num">{r.note_count}</td>
                <td className="num">{fmtLikes(Math.round(r.avg_likes))}</td>
                <td className="num">{fmtLikes(Math.round(r.median_likes))}</td>
                <td className="num">{fmtLikes(Math.round(r.p90_likes))}</td>
                <td className="num">{Math.round(r.blue_ocean_score)}</td>
                <td><MiniBar value={r.blue_ocean_score} max={blueocean[0]?.blue_ocean_score ?? 1} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>

      <Section title="3 · 发布时机 · 周 × 小时 (median likes)">
        <Heatmap data={timing.heatmap ?? []} />
      </Section>

      <Section title="4 · 内容形态">
        <div className="row" style={{gap: 24, flexWrap: "wrap"}}>
          <ShapeTable title="正文字数" data={shape.by_body_length ?? {}} order={["<100", "100-300", "300-600", "600-1000", "1000-2000", "2000+"]} />
          <ShapeTable title="图片数量" data={shape.by_image_count ?? {}} />
        </div>
      </Section>

      <Section title="5 · 标签生态 · Top 30 + 共现">
        <div className="row" style={{gap: 24, alignItems: "flex-start", flexWrap: "wrap"}}>
          <div style={{flex: 1, minWidth: 320}}>
            <table className="table">
              <thead><tr><th>tag</th><th className="num">count</th><th className="num">avg likes</th></tr></thead>
              <tbody>
                {tags.slice(0, 30).map((t: any) => (
                  <tr key={t.tag}>
                    <td>{t.tag}</td>
                    <td className="num">{t.count}</td>
                    <td className="num">{fmtLikes(Math.round(t.avg_likes))}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div style={{flex: 1, minWidth: 320}}>
            <h3>Top 共现对</h3>
            <table className="table">
              <thead><tr><th>a</th><th>b</th><th className="num">共现</th></tr></thead>
              <tbody>
                {tagPairs.slice(0, 25).map((p: any, i: number) => (
                  <tr key={i}><td>{p.a}</td><td>{p.b}</td><td className="num">{p.count}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </Section>

      <Section title="6 · 评论需求挖掘">
        <p className="muted">用户原话里高频询问的模式 — 直接对应到 AcademiCats 的功能/营销 backlog。</p>
        {Object.entries(comments.by_pattern ?? {}).map(([label, items]: [string, any]) => (
          <div key={label} style={{marginBottom: 16}}>
            <h3>「{label}」开头</h3>
            <table className="table">
              <thead><tr><th>短语</th><th className="num">出现</th></tr></thead>
              <tbody>{items.slice(0, 8).map((p: any) => (
                <tr key={p.phrase}><td>{p.phrase}</td><td className="num">{p.count}</td></tr>
              ))}</tbody>
            </table>
          </div>
        ))}
      </Section>

      <Section title="7 · Top performers">
        <h3>by likes</h3>
        <PerfTable rows={topPerf.top_likes ?? []} />
      </Section>
    </div>
  );
}

function Section({title, children}: any) {
  return (
    <div className="card">
      <h2>{title}</h2>
      {children}
    </div>
  );
}

function HookDist({dist, byCat}: any) {
  const entries = Object.entries(dist).sort((a, b) => (b[1] as number) - (a[1] as number));
  const total = entries.reduce((a, [, v]) => a + (v as number), 0) || 1;
  const max = (entries[0]?.[1] as number) ?? 1;
  return (
    <table className="table">
      <thead><tr><th>hook</th><th className="num">n</th><th className="num">占比</th><th>分布</th><th className="num">median likes</th><th className="num">p90</th></tr></thead>
      <tbody>
        {entries.map(([cat, n]) => {
          const c = byCat[cat] ?? {};
          return (
            <tr key={cat}>
              <td>{cat}</td>
              <td className="num">{n as number}</td>
              <td className="num">{(((n as number) / total) * 100).toFixed(1)}%</td>
              <td><MiniBar value={n as number} max={max} /></td>
              <td className="num">{fmtLikes(Math.round(c.likes?.median ?? 0))}</td>
              <td className="num">{fmtLikes(Math.round(c.likes?.p90 ?? 0))}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function MiniBar({value, max}: {value: number; max: number}) {
  const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0;
  return <span className="bar" style={{width: 180}}><span className="bar-fill" style={{width: `${pct}%`, display: "block"}} /></span>;
}

function Heatmap({data}: {data: any[]}) {
  if (!data.length) return <p className="muted">无数据</p>;
  const max = Math.max(...data.map(c => c.median_likes ?? 0)) || 1;
  const cell = new Map<string, any>();
  data.forEach(c => cell.set(`${c.dow}_${c.hour}`, c));
  const dowLabel = ["一", "二", "三", "四", "五", "六", "日"];
  return (
    <table className="heatmap">
      <thead>
        <tr><th></th>{Array.from({length: 24}, (_, h) => <th key={h}>{h}</th>)}</tr>
      </thead>
      <tbody>
        {dowLabel.map((label, d) => (
          <tr key={d}>
            <th>周{label}</th>
            {Array.from({length: 24}, (_, h) => {
              const c = cell.get(`${d}_${h}`) ?? {count: 0, median_likes: 0};
              const ratio = Math.min(1, c.median_likes / max);
              const r = Math.round(245 - 145 * ratio);
              const g = Math.round(245 - 200 * ratio);
              const b = Math.round(245 - 200 * ratio);
              const tip = `周${label} ${h.toString().padStart(2, "0")}:00 · n=${c.count} · median ${fmtLikes(Math.round(c.median_likes))}`;
              return <td key={h} title={tip} style={{background: `rgb(${r},${g},${b})`}}>{c.count || ""}</td>;
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ShapeTable({title, data, order}: {title: string; data: any; order?: string[]}) {
  const keys = order ?? Object.keys(data).sort();
  return (
    <div style={{flex: 1, minWidth: 240}}>
      <h3>{title}</h3>
      <table className="table">
        <thead><tr><th>bucket</th><th className="num">n</th><th className="num">median</th><th className="num">p90</th></tr></thead>
        <tbody>
          {keys.map(k => {
            const d = data[k] ?? {};
            return (
              <tr key={k}>
                <td>{k}</td>
                <td className="num">{d.count ?? 0}</td>
                <td className="num">{fmtLikes(Math.round(d.likes?.median ?? 0))}</td>
                <td className="num">{fmtLikes(Math.round(d.likes?.p90 ?? 0))}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function PerfTable({rows}: {rows: any[]}) {
  return (
    <table className="table">
      <thead><tr><th>#</th><th>标题</th><th>作者</th><th className="num">likes</th><th className="num">收藏</th><th className="num">评论</th></tr></thead>
      <tbody>
        {rows.slice(0, 20).map((r, i) => (
          <tr key={r.note_id}>
            <td className="num">{i + 1}</td>
            <td>{r.url ? <a href={r.url} target="_blank" rel="noreferrer">{r.title}</a> : r.title}</td>
            <td className="muted">{r.author_nickname}</td>
            <td className="num">{fmtLikes(r.liked_count)}</td>
            <td className="num">{fmtLikes(r.collected_count)}</td>
            <td className="num">{fmtLikes(r.comment_count)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

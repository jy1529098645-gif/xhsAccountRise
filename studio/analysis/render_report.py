"""Render a DNA artifact JSON into a single static HTML report.

Self-contained: inline CSS, no external assets, safe to push to GitHub Pages.
Goal is "you can read the whole 爆款 DNA in 5 minutes" — every section folds
into an at-a-glance bar or table.
"""
from __future__ import annotations

import html
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from .. import config

_TZ = timezone(timedelta(hours=8))


def _fmt_num(n: float | int | None) -> str:
    if n is None:
        return "—"
    if isinstance(n, float) and not n.is_integer():
        if n >= 1000:
            return f"{n:,.0f}"
        return f"{n:.1f}"
    n = int(n)
    if n >= 100_000:
        return f"{n / 10000:.1f}w"
    if n >= 1_000:
        return f"{n / 1000:.1f}k"
    return str(n)


def _bar(value: float, max_value: float, width: int = 200) -> str:
    if max_value <= 0:
        pct = 0
    else:
        pct = max(0, min(100, value / max_value * 100))
    return (
        f'<div class="bar" style="width:{width}px">'
        f'<div class="bar-fill" style="width:{pct:.1f}%"></div></div>'
    )


def _heatmap_color(value: float, max_value: float) -> str:
    if max_value <= 0:
        ratio = 0
    else:
        ratio = min(1.0, value / max_value)
    # interpolate light grey → dark red
    r = int(245 - 145 * ratio)
    g = int(245 - 200 * ratio)
    b = int(245 - 200 * ratio)
    return f"rgb({r},{g},{b})"


_CSS = """
* { box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
               "Microsoft YaHei", sans-serif;
  margin: 0; background: #fafafa; color: #1a1a1a;
  font-size: 14px; line-height: 1.55;
}
.wrap { max-width: 1280px; margin: 0 auto; padding: 32px 24px 80px; }
h1 { font-size: 26px; margin: 0 0 4px; }
h2 { font-size: 19px; margin: 32px 0 12px; border-left: 4px solid #ff2442;
     padding-left: 10px; }
h3 { font-size: 15px; margin: 18px 0 6px; color: #444; }
.muted { color: #888; font-size: 12px; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
         gap: 12px; margin: 12px 0 4px; }
.card { background: #fff; border: 1px solid #ececec; border-radius: 8px;
        padding: 14px 16px; }
.card .label { font-size: 11px; color: #888; text-transform: uppercase;
               letter-spacing: 0.04em; }
.card .value { font-size: 22px; font-weight: 600; margin-top: 2px; }
.card .sub { font-size: 12px; color: #666; }
table { width: 100%; border-collapse: collapse; background: #fff;
        border: 1px solid #ececec; border-radius: 8px; overflow: hidden;
        font-size: 13px; }
th, td { padding: 8px 10px; text-align: left; border-bottom: 1px solid #f0f0f0; }
th { background: #f7f7f7; font-weight: 600; font-size: 12px; color: #555; }
tr:last-child td { border-bottom: 0; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
.bar { display: inline-block; height: 8px; background: #f0f0f0; border-radius: 4px;
       overflow: hidden; vertical-align: middle; }
.bar-fill { height: 100%; background: #ff2442; }
.heatmap { border-collapse: collapse; font-size: 11px; }
.heatmap td { width: 28px; height: 22px; text-align: center; padding: 0;
              border: 1px solid #fff; }
.heatmap th { background: transparent; padding: 4px 6px; font-weight: 500;
              color: #888; }
.section { background: #fff; border: 1px solid #ececec; border-radius: 8px;
           padding: 16px 18px; margin-bottom: 14px; }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.pill { display: inline-block; padding: 1px 8px; background: #f4f4f4;
        border-radius: 10px; font-size: 11px; color: #555; margin-right: 4px; }
.title-text { max-width: 540px; display: inline-block; vertical-align: middle;
              white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
a { color: #ff2442; text-decoration: none; }
a:hover { text-decoration: underline; }
details { margin-top: 6px; }
summary { cursor: pointer; color: #555; font-size: 12px; }
"""


def _section_summary(s: dict[str, Any]) -> str:
    summary = s.get("summary", {})
    cards = [
        ("notes analysed", _fmt_num(summary.get("total_notes_analysed"))),
        ("dominant hook", (summary.get("dominant_hooks") or [{}])[0].get("category", "—")),
        ("generated in", f"{summary.get('generated_in_seconds', 0)}s"),
        (
            "generated at",
            datetime.fromtimestamp(s.get("generated_at", 0), tz=_TZ).strftime("%Y-%m-%d %H:%M"),
        ),
    ]
    inner = "".join(
        f'<div class="card"><div class="label">{html.escape(l)}</div>'
        f'<div class="value">{html.escape(str(v))}</div></div>'
        for l, v in cards
    )
    return f'<div class="cards">{inner}</div>'


def _section_titles(titles: dict[str, Any]) -> str:
    if not titles or titles.get("note_count", 0) == 0:
        return "<p class='muted'>No titled notes.</p>"
    dist = titles["primary_distribution"]
    total = sum(dist.values()) or 1
    rows = sorted(dist.items(), key=lambda kv: kv[1], reverse=True)
    max_n = rows[0][1] if rows else 1

    bars = "".join(
        f"<tr><td>{html.escape(cat)}</td>"
        f"<td class='num'>{n}</td>"
        f"<td class='num'>{n / total * 100:.1f}%</td>"
        f"<td>{_bar(n, max_n)}</td>"
        f"<td class='num'>{_fmt_num(titles['by_category'][cat]['likes'].get('median', 0))}</td>"
        f"<td class='num'>{_fmt_num(titles['by_category'][cat]['likes'].get('p90', 0))}</td>"
        f"</tr>"
        for cat, n in rows
    )
    table = (
        "<table><thead><tr>"
        "<th>hook 类型</th><th>数量</th><th>占比</th><th>分布</th>"
        "<th>median likes</th><th>p90 likes</th>"
        "</tr></thead><tbody>"
        f"{bars}</tbody></table>"
    )

    top = titles.get("top_titles", [])[:30]
    top_rows = "".join(
        f"<tr><td class='num'>{i + 1}</td>"
        f"<td><span class='title-text' title='{html.escape(t['title'])}'>"
        f"{html.escape(t['title'])}</span><br>"
        f"{''.join(f'<span class=\"pill\">{html.escape(m)}</span>' for m in t['hook_matched'])}"
        f"</td>"
        f"<td class='num'>{_fmt_num(t['liked'])}</td>"
        f"<td class='num'>{_fmt_num(t['commented'])}</td>"
        f"<td class='num'>{t['char_count']}</td></tr>"
        for i, t in enumerate(top)
    )
    top_table = (
        "<table><thead><tr><th>#</th><th>title</th><th>likes</th>"
        "<th>cmt</th><th>字数</th></tr></thead>"
        f"<tbody>{top_rows}</tbody></table>"
    )

    # Title length distribution
    by_len = titles.get("by_title_length", {})
    order = ["<10", "10-14", "15-19", "20-24", "25-29", "30+"]
    len_rows = "".join(
        f"<tr><td>{b}</td>"
        f"<td class='num'>{by_len.get(b, {}).get('count', 0)}</td>"
        f"<td class='num'>{_fmt_num(by_len.get(b, {}).get('likes', {}).get('median', 0))}</td>"
        f"<td class='num'>{_fmt_num(by_len.get(b, {}).get('likes', {}).get('p90', 0))}</td>"
        f"</tr>"
        for b in order
    )
    len_table = (
        "<table><thead><tr><th>title 字数</th><th>n</th>"
        "<th>median likes</th><th>p90 likes</th></tr></thead>"
        f"<tbody>{len_rows}</tbody></table>"
    )

    return (
        f"{table}"
        "<h3>Top 30 标题（按 likes）</h3>"
        f"{top_table}"
        "<h3>标题字数 × 互动</h3>"
        f"{len_table}"
    )


def _section_shape(shape: dict[str, Any]) -> str:
    body = shape.get("by_body_length", {})
    body_order = ["<100", "100-300", "300-600", "600-1000", "1000-2000", "2000+"]
    body_rows = "".join(
        f"<tr><td>{b}</td>"
        f"<td class='num'>{body.get(b, {}).get('count', 0)}</td>"
        f"<td class='num'>{_fmt_num(body.get(b, {}).get('likes', {}).get('median', 0))}</td>"
        f"<td class='num'>{_fmt_num(body.get(b, {}).get('likes', {}).get('p90', 0))}</td></tr>"
        for b in body_order
    )
    body_tbl = (
        "<h3>正文字数 × 互动</h3>"
        "<table><thead><tr><th>body 字数</th><th>n</th><th>median</th><th>p90</th>"
        f"</tr></thead><tbody>{body_rows}</tbody></table>"
    )

    imgs = shape.get("by_image_count", {})
    img_rows = "".join(
        f"<tr><td>{k} 张</td>"
        f"<td class='num'>{v['count']}</td>"
        f"<td class='num'>{_fmt_num(v['likes'].get('median', 0))}</td>"
        f"<td class='num'>{_fmt_num(v['likes'].get('p90', 0))}</td></tr>"
        for k, v in sorted(imgs.items(), key=lambda kv: int(kv[0]))
    )
    img_tbl = (
        "<h3>图片数 × 互动</h3>"
        "<table><thead><tr><th>image_count</th><th>n</th><th>median</th><th>p90</th>"
        f"</tr></thead><tbody>{img_rows}</tbody></table>"
    )

    vv = shape.get("video_vs_image", {})
    vv_rows = "".join(
        f"<tr><td>{html.escape(k)}</td>"
        f"<td class='num'>{vv[k].get('n', 0)}</td>"
        f"<td class='num'>{_fmt_num(vv[k].get('median', 0))}</td>"
        f"<td class='num'>{_fmt_num(vv[k].get('p90', 0))}</td></tr>"
        for k in vv
    )
    vv_tbl = (
        "<h3>视频 vs 图文</h3>"
        "<table><thead><tr><th>类型</th><th>n</th><th>median</th><th>p90</th>"
        f"</tr></thead><tbody>{vv_rows}</tbody></table>"
    )

    return f"<div class='grid2'><div>{body_tbl}{vv_tbl}</div><div>{img_tbl}</div></div>"


def _section_timing(timing: dict[str, Any]) -> str:
    heat = timing.get("heatmap", [])
    if not heat:
        return "<p class='muted'>No timing data.</p>"
    max_likes = max((c["median_likes"] for c in heat), default=0)
    cell = {(c["dow"], c["hour"]): c for c in heat}
    dow_label = ["一", "二", "三", "四", "五", "六", "日"]
    head = "".join(f"<th>{h}</th>" for h in range(24))
    rows = ""
    for d in range(7):
        cells = ""
        for h in range(24):
            c = cell.get((d, h), {"count": 0, "median_likes": 0})
            color = _heatmap_color(c["median_likes"], max_likes)
            tip = (
                f"周{dow_label[d]} {h:02d}:00 · n={c['count']} · "
                f"median {_fmt_num(c['median_likes'])}"
            )
            cells += f"<td style='background:{color}' title='{tip}'>{c['count']}</td>"
        rows += f"<tr><th>周{dow_label[d]}</th>{cells}</tr>"
    table = (
        "<table class='heatmap'>"
        f"<thead><tr><th></th>{head}</tr></thead>"
        f"<tbody>{rows}</tbody></table>"
        "<p class='muted'>cell = 该时段发布的 note 数；底色 = median likes</p>"
    )

    monthly = timing.get("monthly", {})
    mrows = "".join(
        f"<tr><td>{m}</td>"
        f"<td class='num'>{v['count']}</td>"
        f"<td class='num'>{_fmt_num(v['likes'].get('median', 0))}</td>"
        f"<td class='num'>{_fmt_num(v['likes'].get('p90', 0))}</td></tr>"
        for m, v in monthly.items()
    )
    monthly_tbl = (
        "<h3>按月份</h3>"
        "<table><thead><tr><th>月</th><th>n</th><th>median</th><th>p90</th></tr>"
        f"</thead><tbody>{mrows}</tbody></table>"
    )

    return f"<h3>hour × weekday heatmap</h3>{table}{monthly_tbl}"


def _section_tags(tags: dict[str, Any]) -> str:
    top = tags.get("top_tags", [])[:60]
    max_c = top[0]["count"] if top else 1
    rows = "".join(
        f"<tr><td>{html.escape(t['tag'])}</td>"
        f"<td class='num'>{t['count']}</td>"
        f"<td>{_bar(t['count'], max_c, 120)}</td>"
        f"<td class='num'>{_fmt_num(t['avg_likes'])}</td>"
        f"<td class='num'>{_fmt_num(t['median_likes'])}</td></tr>"
        for t in top
    )
    tag_tbl = (
        "<h3>Top 60 tags</h3>"
        "<table><thead><tr><th>tag</th><th>count</th><th></th>"
        "<th>avg likes</th><th>median</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )

    pairs = tags.get("top_pairs", [])[:40]
    prows = "".join(
        f"<tr><td>{html.escape(p['a'])}</td><td>{html.escape(p['b'])}</td>"
        f"<td class='num'>{p['count']}</td></tr>"
        for p in pairs
    )
    pair_tbl = (
        "<h3>Top 40 tag pairs</h3>"
        "<table><thead><tr><th>a</th><th>b</th><th>共现次数</th></tr></thead>"
        f"<tbody>{prows}</tbody></table>"
    )

    return f"<div class='grid2'><div>{tag_tbl}</div><div>{pair_tbl}</div></div>"


def _section_blueocean(bo: dict[str, Any]) -> str:
    rows_data = bo.get("rankings", [])
    if not rows_data:
        return "<p class='muted'>No keyword-mapped notes.</p>"
    max_score = rows_data[0]["blue_ocean_score"] if rows_data else 1
    rows = "".join(
        f"<tr><td class='num'>{i + 1}</td>"
        f"<td>{html.escape(r['keyword'])}</td>"
        f"<td class='num'>{r['note_count']}</td>"
        f"<td class='num'>{_fmt_num(r['avg_likes'])}</td>"
        f"<td class='num'>{_fmt_num(r['median_likes'])}</td>"
        f"<td class='num'>{_fmt_num(r['p90_likes'])}</td>"
        f"<td class='num'>{r['blue_ocean_score']:.0f}</td>"
        f"<td>{_bar(r['blue_ocean_score'], max_score)}</td></tr>"
        for i, r in enumerate(rows_data[:50])
    )
    return (
        "<p class='muted'>蓝海得分 = avg_likes / log2(note_count + 2)。"
        "得分高 = 该关键词供给少且单帖回报高，更值得切入。</p>"
        "<table><thead><tr>"
        "<th>#</th><th>关键词</th><th>n</th><th>avg</th><th>median</th>"
        "<th>p90</th><th>score</th><th></th>"
        f"</tr></thead><tbody>{rows}</tbody></table>"
    )


def _section_comments(cd: dict[str, Any]) -> str:
    total = cd.get("total_comments", 0)
    out = [f"<p class='muted'>分析了 {total} 条评论。</p>"]
    for label, items in cd.get("by_pattern", {}).items():
        if not items:
            continue
        out.append(f"<h3>「{html.escape(label)}」开头 Top {min(20, len(items))}</h3>")
        rows = "".join(
            f"<tr><td>{html.escape(p['phrase'])}</td>"
            f"<td class='num'>{p['count']}</td></tr>"
            for p in items[:20]
        )
        out.append(
            "<table><thead><tr><th>短语</th><th>出现次数</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>"
        )
    return "".join(out)


def _section_top(top: dict[str, Any]) -> str:
    def _tbl(rows: list[dict[str, Any]], key: str) -> str:
        body = "".join(
            f"<tr><td class='num'>{i + 1}</td>"
            f"<td><a href='{html.escape(r.get('url', '') or '#')}' target='_blank' rel='noreferrer'>"
            f"<span class='title-text' title='{html.escape(r.get('title', '') or '')}'>"
            f"{html.escape(r.get('title', '') or '')}</span></a><br>"
            f"<span class='muted'>{html.escape(r.get('author_nickname', '') or '')}</span></td>"
            f"<td class='num'>{_fmt_num(r.get('liked_count'))}</td>"
            f"<td class='num'>{_fmt_num(r.get('collected_count'))}</td>"
            f"<td class='num'>{_fmt_num(r.get('comment_count'))}</td>"
            f"<td class='num'>{r.get('image_count') or 0}</td></tr>"
            for i, r in enumerate(rows[:30])
        )
        return (
            "<table><thead><tr><th>#</th><th>title / 作者</th>"
            "<th>likes</th><th>collects</th><th>cmts</th><th>imgs</th>"
            f"</tr></thead><tbody>{body}</tbody></table>"
        )

    return (
        "<details open><summary><b>Top 30 by likes</b></summary>"
        f"{_tbl(top.get('top_likes', []), 'liked_count')}</details>"
        "<details><summary><b>Top 30 by collects</b></summary>"
        f"{_tbl(top.get('top_collects', []), 'collected_count')}</details>"
        "<details><summary><b>Top 30 by comments</b></summary>"
        f"{_tbl(top.get('top_comments', []), 'comment_count')}</details>"
    )


def render(artifact: dict[str, Any], out_path: Path | None = None) -> Path:
    sections = artifact.get("sections", {})
    title = f"xhs DNA · v{artifact['version']}"
    body = (
        f"<h1>{html.escape(title)}</h1>"
        f"<p class='muted'>AcademiCats × 小红书爆款 DNA — 全自动统计报告</p>"
        f"{_section_summary(artifact)}"
        "<h2>1 · 标题 hook 分布</h2>"
        f"<div class='section'>{_section_titles(sections.get('titles', {}))}</div>"
        "<h2>2 · 内容形态（字数 / 图片 / 视频）</h2>"
        f"<div class='section'>{_section_shape(sections.get('body_and_shape', {}))}</div>"
        "<h2>3 · 发布时机</h2>"
        f"<div class='section'>{_section_timing(sections.get('timing', {}))}</div>"
        "<h2>4 · 标签</h2>"
        f"<div class='section'>{_section_tags(sections.get('tags', {}))}</div>"
        "<h2>5 · 关键词蓝海排行</h2>"
        f"<div class='section'>{_section_blueocean(sections.get('keyword_blueocean', {}))}</div>"
        "<h2>6 · 评论需求挖掘</h2>"
        f"<div class='section'>{_section_comments(sections.get('comment_demand', {}))}</div>"
        "<h2>7 · Top performers</h2>"
        f"<div class='section'>{_section_top(sections.get('top_performers', {}))}</div>"
    )
    html_doc = (
        "<!DOCTYPE html><html lang='zh-CN'><head>"
        f"<meta charset='utf-8'><title>{html.escape(title)}</title>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<meta name='referrer' content='no-referrer'>"
        f"<style>{_CSS}</style></head><body>"
        f"<div class='wrap'>{body}</div></body></html>"
    )

    if out_path is None:
        out_path = config.ANALYSIS_DIR / f"v{artifact['version']}.html"
    out_path.write_text(html_doc, encoding="utf-8")
    return out_path


def render_from_file(json_path: Path, out_path: Path | None = None) -> Path:
    artifact = json.loads(json_path.read_text(encoding="utf-8"))
    return render(artifact, out_path)

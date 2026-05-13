"""Render a multi-agent compose bundle into a single static HTML report.

Sections (top → bottom):
    0. Header w/ brief summary
    1. Agent timeline (per-step latency, LLM, errors)
    2. Strategy card (from Strategist)
    3. References used (RAG)
    4. Candidate grid (drafter outputs side by side with critic scores)
    5. Refined version (if any)
    6. Final pick (highlighted)
"""
from __future__ import annotations

import html
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from .. import config

_TZ = timezone(timedelta(hours=8))


_CSS = """
* { box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
               "Microsoft YaHei", sans-serif;
  margin: 0; background: #fafafa; color: #1a1a1a;
  font-size: 14px; line-height: 1.6;
}
.wrap { max-width: 1480px; margin: 0 auto; padding: 24px 24px 80px; }
h1 { font-size: 22px; margin: 0 0 4px; }
h2 { font-size: 17px; margin: 22px 0 10px; border-left: 4px solid #ff2442;
     padding-left: 10px; }
h3 { font-size: 14px; margin: 14px 0 6px; color: #444; }
.muted { color: #888; font-size: 12px; }
.section { background: #fff; border: 1px solid #ececec; border-radius: 8px;
           padding: 14px 16px; margin-bottom: 12px; }
.brief { font-size: 13px; }
.brief b { color: #555; }
.timeline { font-family: ui-monospace, monospace; font-size: 12px; }
.timeline table { width: 100%; border-collapse: collapse; }
.timeline td { padding: 3px 6px; border-bottom: 1px solid #f3f3f3; }
.timeline td.idx { color: #999; width: 30px; }
.timeline td.agent { color: #ff2442; font-weight: 600; }
.timeline td.llm { color: #555; }
.timeline td.latency { text-align: right; color: #888; }
.timeline td.error { color: #c0392b; }
.strategy-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px;
                 font-size: 13px; }
.strategy-grid .row { padding: 6px 0; border-bottom: 1px solid #f4f4f4; }
.strategy-grid b { color: #555; font-size: 12px; display: block; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr));
         gap: 14px; }
.card { background: #fff; border: 1px solid #ececec; border-radius: 10px;
        padding: 16px; display: flex; flex-direction: column; }
.card.failed { background: #fff7f7; border-color: #f4cccc; }
.card.final { border: 2px solid #ff2442; box-shadow: 0 2px 12px rgba(255,36,66,0.15); }
.card .llm { font-size: 12px; font-weight: 600; color: #ff2442;
             text-transform: uppercase; letter-spacing: 0.04em; }
.card .meta { font-size: 11px; color: #888; margin-top: 2px; }
.card .title { font-size: 16px; font-weight: 600; margin: 10px 0 4px; }
.card .body { white-space: pre-wrap; font-size: 12.5px; color: #333;
              max-height: 280px; overflow: auto;
              background: #fafafa; padding: 8px; border-radius: 6px;
              border: 1px solid #f0f0f0; }
.tags { margin-top: 8px; }
.tag { display: inline-block; padding: 1px 7px; background: #fff0f2;
       border-radius: 10px; font-size: 11px; color: #ff2442; margin: 0 4px 4px 0; }
.scores { display: grid; grid-template-columns: repeat(5, 1fr); gap: 6px;
          margin-top: 10px; }
.score { background: #fafafa; padding: 6px; border-radius: 4px; text-align: center;
         font-size: 11px; }
.score .label { color: #888; }
.score .val { font-weight: 600; font-size: 14px; }
.critic-row { font-size: 11px; padding: 4px 6px; border-top: 1px solid #f0f0f0;
              color: #555; }
.critic-row b { color: #ff2442; }
.tag-flag { display: inline-block; padding: 1px 7px; background: #fff5e6;
            color: #d97706; border-radius: 10px; font-size: 10.5px; margin: 0 4px 4px 0; }
.cover { font-size: 11px; color: #777; background: #fafafa; padding: 8px;
         border-radius: 4px; margin-top: 8px; word-break: break-word; }
ol.refs { margin: 6px 0 0 18px; padding: 0; font-size: 13px; }
"""


def _fmt_likes(n: Any) -> str:
    if not n:
        return "0"
    n = int(n)
    if n >= 100_000:
        return f"{n / 10000:.1f}w"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def _section_brief(brief: dict[str, Any]) -> str:
    parts = [
        ("主题", brief.get("topic")),
        ("角度", brief.get("angle")),
        ("目标字数", brief.get("target_length")),
        ("CTA", brief.get("cta_strength")),
        ("赛道", brief.get("niche")),
    ]
    return " · ".join(
        f"<b>{html.escape(k)}：</b>{html.escape(str(v))}"
        for k, v in parts if v not in (None, "", 0)
    )


def _section_trace(trace: list[dict[str, Any]]) -> str:
    rows = "".join(
        f"<tr><td class='idx'>{s['step_index']}</td>"
        f"<td class='agent'>{html.escape(s['agent_name'])}</td>"
        f"<td class='llm'>{html.escape(s.get('llm') or '—')}</td>"
        f"<td>{html.escape(s.get('output_summary') or '')[:120]}</td>"
        f"<td class='latency'>{s.get('latency_ms', 0)}ms</td>"
        f"<td class='error'>{html.escape(s.get('error') or '')}</td></tr>"
        for s in trace
    )
    return f"<div class='timeline'><table>{rows}</table></div>"


def _section_strategy(strategy: dict[str, Any]) -> str:
    if not strategy:
        return "<p class='muted'>（未运行 Strategist）</p>"
    return (
        "<div class='strategy-grid'>"
        f"<div class='row'><b>推荐 hook</b>{html.escape(strategy.get('recommended_hook', ''))}</div>"
        f"<div class='row'><b>开头钩子</b>{html.escape(strategy.get('opening_hook', ''))}</div>"
        f"<div class='row'><b>结尾 CTA</b>{html.escape(strategy.get('cta_phrase', ''))}</div>"
        f"<div class='row'><b>语气</b>{html.escape(strategy.get('tone', ''))}</div>"
        f"<div class='row' style='grid-column:1/3'><b>结构</b>"
        + " → ".join(html.escape(s) for s in strategy.get("structure", []))
        + "</div>"
        f"<div class='row' style='grid-column:1/3'><b>避坑</b>"
        + "；".join(html.escape(s) for s in strategy.get("avoid", []))
        + "</div>"
        "</div>"
    )


def _section_rag(rag: dict[str, Any]) -> str:
    refs = rag.get("refs", [])
    if not refs:
        return "<p class='muted'>无参考</p>"
    items = "".join(
        f"<li>[{_fmt_likes(r.get('likes'))} likes] {html.escape(r.get('title', ''))}</li>"
        for r in refs
    )
    hooks = ", ".join(html.escape(h) for h in rag.get("hooks", []))
    return (
        f"<ol class='refs'>{items}</ol>"
        f"<p class='muted'>另用 {rag.get('comments_count', 0)} 条评论 + hook 模板：{hooks}</p>"
    )


def _scores_html(scores: dict[str, float]) -> str:
    if not scores:
        return ""
    keys = ["hook", "language_fit", "shareability", "brand_safety", "structural_clarity"]
    cells = "".join(
        f"<div class='score'><div class='label'>{html.escape(k)}</div>"
        f"<div class='val'>{scores.get(k, 0):.1f}</div></div>"
        for k in keys
    )
    return f"<div class='scores'>{cells}</div>"


def _card(c: dict[str, Any], is_final: bool = False, label: str | None = None) -> str:
    classes = ["card"]
    if c.get("error"):
        classes.append("failed")
    if is_final:
        classes.append("final")
    cls = " ".join(classes)

    if c.get("error"):
        return (
            f"<div class='{cls}'>"
            f"<div class='llm'>{html.escape(c['llm'])}</div>"
            f"<div class='title' style='color:#c0392b'>FAILED</div>"
            f"<pre style='white-space:pre-wrap;color:#c0392b;font-size:12px'>"
            f"{html.escape(c['error'])}</pre>"
            "</div>"
        )

    p = c["payload"]
    tags = "".join(
        f"<span class='tag'>#{html.escape(t)}</span>" for t in p.get("tags", [])
    )
    cost = c.get("cost_estimate_usd", 0)
    tok = c.get("token_usage", {}) or {}
    critique_lines = ""
    for cr in c.get("critiques", []):
        flags = "".join(
            f"<span class='tag-flag'>{html.escape(f)}</span>" for f in cr.get("risk_flags", [])
        )
        critique_lines += (
            f"<div class='critic-row'><b>{html.escape(cr['critic_llm'])}</b> "
            f"overall {cr['overall']:.1f} · {flags} <br>"
            f"<span class='muted'>{html.escape(cr.get('suggestion', ''))}</span></div>"
        )
    label_html = f"<div class='muted'>{html.escape(label)}</div>" if label else ""
    avg = c.get("critique_avg")
    avg_html = f" · avg <b>{avg:.1f}</b>" if avg is not None else ""

    return (
        f"<div class='{cls}'>"
        f"{label_html}"
        f"<div class='llm'>{html.escape(c['llm'])}</div>"
        f"<div class='meta'>latency {c.get('latency_ms', 0)}ms · "
        f"tokens {tok.get('input', 0)}/{tok.get('output', 0)} · ${cost:.4f}{avg_html}</div>"
        f"<div class='title'>{html.escape(p.get('title') or '')}</div>"
        f"<div class='body'>{html.escape(p.get('body') or '')}</div>"
        f"<div class='tags'>{tags}</div>"
        f"<div class='cover'><b>cover prompt：</b>{html.escape(p.get('cover_prompt') or '')}</div>"
        f"{_scores_html(c.get('critiques', [{}])[0].get('scores', {}) if c.get('critiques') else {})}"
        f"{critique_lines}"
        "</div>"
    )


def render(bundle: dict[str, Any], out_path: Path | None = None) -> Path:
    when = datetime.fromtimestamp(bundle.get("generated_at", 0), tz=_TZ).strftime("%Y-%m-%d %H:%M")
    brief = bundle.get("brief", {})
    title = f"Compose {bundle['draft_id']} · {brief.get('topic', '')}"

    drafts = bundle.get("drafts", [])
    refined = bundle.get("refined")
    final = bundle.get("final")
    totals = bundle.get("totals", {})

    draft_cards = "".join(_card(c) for c in drafts)
    refined_card = _card(refined, label="Refiner 改写后") if refined else ""
    final_card = _card(final, is_final=True, label="★ Synthesizer 最终选择") if final else ""

    body = (
        f"<h1>{html.escape(title)}</h1>"
        f"<p class='muted'>{html.escape(when)} · "
        f"elapsed {totals.get('elapsed_s', 0)}s · cost est ${totals.get('cost_usd', 0):.4f}</p>"
        f"<div class='section brief'>{_section_brief(brief)}</div>"
        "<h2>Agent 时间线</h2>"
        f"<div class='section'>{_section_trace(bundle.get('trace', []))}</div>"
        "<h2>Strategist 策略</h2>"
        f"<div class='section'>{_section_strategy(bundle.get('strategy', {}))}</div>"
        "<h2>RAG 参考</h2>"
        f"<div class='section'>{_section_rag(bundle.get('rag', {}))}</div>"
        "<h2>Drafter 候选</h2>"
        f"<div class='cards'>{draft_cards}</div>"
    )
    if refined_card:
        body += f"<h2>Refiner 改写</h2><div class='cards'>{refined_card}</div>"
    if final_card:
        body += f"<h2>Final</h2><div class='cards'>{final_card}</div>"

    html_doc = (
        "<!DOCTYPE html><html lang='zh-CN'><head>"
        f"<meta charset='utf-8'><title>{html.escape(title)}</title>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<style>{_CSS}</style></head><body>"
        f"<div class='wrap'>{body}</div></body></html>"
    )

    if out_path is None:
        out_path = config.DRAFTS_DIR / f"compose_{bundle['draft_id']}.html"
    out_path.write_text(html_doc, encoding="utf-8")
    return out_path

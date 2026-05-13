"""Render a multi-candidate draft into a single comparison HTML.

Side-by-side cards per LLM with title, body, tags, cover prompt, self_score,
critique, cost. Failed candidates render with their error.
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
.wrap { max-width: 1400px; margin: 0 auto; padding: 28px 24px 80px; }
h1 { font-size: 23px; margin: 0 0 4px; }
h3 { font-size: 16px; margin: 16px 0 8px; }
.muted { color: #888; font-size: 12px; }
.brief { background: #fff; border: 1px solid #ececec; border-radius: 8px;
         padding: 16px; margin: 12px 0 20px; }
.brief b { color: #555; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(440px, 1fr));
         gap: 16px; }
.card { background: #fff; border: 1px solid #ececec; border-radius: 10px;
        padding: 18px; display: flex; flex-direction: column; }
.card.error { background: #fff7f7; border-color: #f4cccc; }
.card .llm { font-size: 12px; font-weight: 600; color: #ff2442;
             text-transform: uppercase; letter-spacing: 0.04em; }
.card .meta { font-size: 11px; color: #888; margin-top: 2px; }
.card .title { font-size: 17px; font-weight: 600; margin: 12px 0 6px;
               color: #1a1a1a; }
.card .body { white-space: pre-wrap; font-size: 13px; color: #333;
              max-height: 360px; overflow: auto;
              background: #fafafa; padding: 10px; border-radius: 6px;
              border: 1px solid #f0f0f0; }
.tags { margin-top: 10px; }
.tag { display: inline-block; padding: 1px 8px; background: #fff0f2;
       border-radius: 10px; font-size: 11px; color: #ff2442; margin: 0 4px 4px 0; }
.score-row { display: flex; gap: 14px; font-size: 12px; color: #555;
             margin-top: 10px; padding-top: 10px; border-top: 1px solid #f0f0f0; }
.score-row b { color: #1a1a1a; font-weight: 600; }
.critique { font-size: 12px; color: #888; margin-top: 8px; font-style: italic; }
.cover { font-size: 11px; color: #777; background: #fafafa; padding: 8px;
         border-radius: 4px; margin-top: 10px; word-break: break-word; }
.error-text { color: #c0392b; font-family: ui-monospace, monospace; font-size: 12px;
              white-space: pre-wrap; }
.refs { background: #fff; border: 1px solid #ececec; border-radius: 8px;
        padding: 14px; margin-bottom: 20px; }
.refs ol { margin: 6px 0 0 18px; padding: 0; }
.refs li { margin-bottom: 4px; }
"""


def _fmt_likes(n: int | None) -> str:
    if not n:
        return "0"
    if n >= 100_000:
        return f"{n / 10000:.1f}w"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def _fmt_brief(brief: dict[str, Any]) -> str:
    parts = [
        ("主题", brief.get("topic")),
        ("角度", brief.get("angle")),
        ("目标字数", brief.get("target_length")),
        ("CTA", brief.get("cta_strength")),
        ("赛道", brief.get("niche")),
        ("附加要求", brief.get("extra_constraints")),
    ]
    bits = []
    for k, v in parts:
        if v in (None, "", 0):
            continue
        bits.append(f"<b>{html.escape(k)}：</b>{html.escape(str(v))}")
    return " · ".join(bits)


def _fmt_refs(refs: list[dict[str, Any]]) -> str:
    if not refs:
        return "<p class='muted'>无参考</p>"
    items = "".join(
        f"<li>[{_fmt_likes(r.get('likes'))} likes] "
        f"{html.escape(r.get('title') or '')}</li>"
        for r in refs[:8]
    )
    return f"<ol>{items}</ol>"


def _fmt_candidate(c: dict[str, Any]) -> str:
    if c.get("error"):
        return (
            f"<div class='card error'>"
            f"<div class='llm'>{html.escape(c['llm'])}</div>"
            f"<div class='meta'>latency {c.get('latency_ms', 0)}ms</div>"
            f"<div class='title' style='color:#c0392b'>FAILED</div>"
            f"<pre class='error-text'>{html.escape(c['error'])}</pre>"
            "</div>"
        )
    p = c["payload"]
    tags_html = "".join(
        f"<span class='tag'>#{html.escape(t)}</span>" for t in p.get("tags", [])
    )
    cost = c.get("cost_estimate_usd", 0)
    tok = c.get("token_usage", {}) or {}
    return (
        "<div class='card'>"
        f"<div class='llm'>{html.escape(c['llm'])}</div>"
        f"<div class='meta'>latency {c.get('latency_ms', 0)}ms · "
        f"tokens {tok.get('input', 0)}/{tok.get('output', 0)} · "
        f"${cost:.4f}</div>"
        f"<div class='title'>{html.escape(p.get('title') or '')}</div>"
        f"<div class='body'>{html.escape(p.get('body') or '')}</div>"
        f"<div class='tags'>{tags_html}</div>"
        f"<div class='cover'><b>cover prompt：</b>{html.escape(p.get('cover_prompt') or '')}</div>"
        "<div class='score-row'>"
        f"<span>hook: <b>{html.escape(p.get('hook_type') or '—')}</b></span>"
        f"<span>self_score: <b>{p.get('self_score', 0):.1f}</b></span>"
        f"<span>预测 likes: <b>{_fmt_likes(p.get('predicted_likes'))}</b></span>"
        "</div>"
        f"<div class='critique'>{html.escape(p.get('self_critique') or '')}</div>"
        "</div>"
    )


def render(bundle: dict[str, Any], out_path: Path | None = None) -> Path:
    brief = bundle.get("brief", {})
    rag = bundle.get("rag", {})
    cands = bundle.get("candidates", [])
    when = datetime.fromtimestamp(bundle.get("generated_at", 0), tz=_TZ).strftime("%Y-%m-%d %H:%M")
    title = f"Draft {bundle['draft_id']} · {brief.get('topic') or ''}"

    body = (
        f"<h1>{html.escape(title)}</h1>"
        f"<p class='muted'>{html.escape(when)} · {len(cands)} candidates</p>"
        f"<div class='brief'>{_fmt_brief(brief)}</div>"
        "<h3>检索到的参考爆款</h3>"
        f"<div class='refs'>{_fmt_refs(rag.get('refs', []))}"
        f"<p class='muted'>另用 {rag.get('comments_count', 0)} 条评论 + hook 模板 "
        f"{', '.join(html.escape(h) for h in rag.get('hooks', []))}</p>"
        "</div>"
        "<h3>候选稿件</h3>"
        f"<div class='cards'>{''.join(_fmt_candidate(c) for c in cands)}</div>"
    )

    html_doc = (
        "<!DOCTYPE html><html lang='zh-CN'><head>"
        f"<meta charset='utf-8'><title>{html.escape(title)}</title>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<style>{_CSS}</style></head><body>"
        f"<div class='wrap'>{body}</div></body></html>"
    )

    if out_path is None:
        out_path = config.DRAFTS_DIR / f"{bundle['draft_id']}.html"
    out_path.write_text(html_doc, encoding="utf-8")
    return out_path

"""`python -m studio` entrypoint."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Load .env early so config.* sees keys. Always anchor to REPO_ROOT and
# override so a stale empty var in the parent shell can't silently win.
try:
    from dotenv import load_dotenv
    from . import config as _config
    load_dotenv(dotenv_path=_config.REPO_ROOT / ".env", override=True)
except ImportError:
    pass

from . import db, library
from .agents import pipeline as agent_pipeline
from .analysis import extract_dna, promote_hooks, render_report
from .brief import Brief
from .generators import orchestrator, registry, render_drafts
from .rag import build_index


def _cmd_migrate(args: argparse.Namespace) -> int:
    applied = db.apply_migrations()
    if not applied:
        print("[migrate] up to date")
    return 0


def _cmd_analyze(args: argparse.Namespace) -> int:
    db.apply_migrations(verbose=False)
    print(f"[analyze] building DNA artifact (version={args.version or 'today'})...")
    artifact = extract_dna.build_dna(version=args.version)
    fp = extract_dna.persist(artifact)
    print(f"[analyze] json:  {fp}")
    if not args.no_html:
        html_fp = render_report.render(artifact)
        print(f"[analyze] html:  {html_fp}")
    s = artifact["summary"]
    print(
        f"[analyze] done in {s['generated_in_seconds']}s · "
        f"{s['total_notes_analysed']} notes · "
        f"dominant: {', '.join(c['category'] for c in s['dominant_hooks'])}"
    )
    return 0


def _cmd_render(args: argparse.Namespace) -> int:
    src = Path(args.input)
    fp = render_report.render_from_file(src)
    print(f"[render] {fp}")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    with db.connect(read_only=True) as con:
        c = con.cursor()
        def _safe(t: str) -> int:
            try:
                return c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except Exception:
                return 0
        out = {
            "notes": _safe("notes"),
            "comments": _safe("comments"),
            "fts_notes": _safe("studio_fts_notes"),
            "fts_comments": _safe("studio_fts_comments"),
            "studio_drafts": _safe("studio_drafts"),
            "studio_candidates": _safe("studio_draft_candidates"),
            "studio_my_posts": _safe("studio_my_posts"),
            "studio_dna_artifacts": _safe("studio_dna_artifacts"),
        }
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


def _cmd_rag_build(args: argparse.Namespace) -> int:
    db.apply_migrations(verbose=False)
    print("[rag] rebuilding FTS5 indices...")
    stats = build_index.rebuild_all()
    print(json.dumps(stats, indent=2))
    return 0


def _cmd_promote_hooks(args: argparse.Namespace) -> int:
    res = promote_hooks.promote(min_sample_size=args.min_sample)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


def _cmd_rag_search(args: argparse.Namespace) -> int:
    from .rag import retrieve
    res = retrieve.retrieve_for_brief(args.topic, k_notes=args.k, n_comments=args.n)
    print(f"refs ({len(res['refs'])}):")
    for r in res["refs"]:
        print(
            f"  [{r['liked_count']:>6} likes · score {r['hybrid_score']:.2f}]"
            f" {r['title']}"
        )
    print(f"\ncomments ({len(res['comments'])}):")
    for c in res["comments"][:8]:
        text = (c["content"] or "").replace("\n", " ")[:140]
        print(f"  ({c['like_count']:>3}👍) {text}")
    print(f"\nhooks ({len(res['hooks'])}):")
    for h in res["hooks"]:
        print(f"  - {h['category']} (n={h['count']}, median {int(h.get('median_likes', 0))})")
    return 0


def _cmd_generate(args: argparse.Namespace) -> int:
    db.apply_migrations(verbose=False)
    brief = Brief(
        topic=args.topic,
        angle=args.angle,
        target_length=args.length,
        cta_strength=args.cta,
        niche=args.niche or "",
        extra_constraints=args.extra or "",
    )
    try:
        generators = registry.build(args.llms)
    except ValueError as e:
        print(f"[generate] {e}", file=sys.stderr)
        return 2
    if not generators:
        print("[generate] no generators selected", file=sys.stderr)
        return 2

    print(f"[generate] brief: {brief.to_json()}")
    print(f"[generate] LLMs: {', '.join(g.model for g in generators)}")
    bundle = asyncio.run(
        orchestrator.generate(brief, generators, k_refs=args.k_refs)
    )

    print(f"[generate] draft_id: {bundle['draft_id']}")
    for c in bundle["candidates"]:
        if c["error"]:
            print(f"  ✗ {c['llm']}: {c['error']}")
        else:
            p = c["payload"]
            print(
                f"  ✓ {c['llm']}: {p['title'][:36]!r:<40} "
                f"score={p['self_score']:.1f} "
                f"latency={c['latency_ms']}ms "
                f"${c['cost_estimate_usd']:.4f}"
            )

    if not args.no_html:
        fp = render_drafts.render(bundle)
        print(f"[generate] html: {fp}")
    if args.json_out:
        out = Path(args.json_out)
        out.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[generate] json: {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="studio", description="xhs account-rise studio CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("migrate", help="apply pending migrations")
    sp.set_defaults(func=_cmd_migrate)

    sp = sub.add_parser("analyze", help="extract DNA artifact + render HTML")
    sp.add_argument("--version", default=None)
    sp.add_argument("--no-html", action="store_true")
    sp.set_defaults(func=_cmd_analyze)

    sp = sub.add_parser("render", help="re-render an existing DNA JSON into HTML")
    sp.add_argument("input")
    sp.set_defaults(func=_cmd_render)

    sp = sub.add_parser("status", help="dump DB row counts")
    sp.set_defaults(func=_cmd_status)

    rag = sub.add_parser("rag", help="RAG index commands")
    rag_sub = rag.add_subparsers(dest="rag_cmd", required=True)

    sp = rag_sub.add_parser("build", help="rebuild FTS5 indices")
    sp.set_defaults(func=_cmd_rag_build)

    sp = rag_sub.add_parser("search", help="test retrieval for a topic")
    sp.add_argument("topic")
    sp.add_argument("--k", type=int, default=8)
    sp.add_argument("--n", type=int, default=15)
    sp.set_defaults(func=_cmd_rag_search)

    sp = sub.add_parser("promote-hooks",
                        help="copy hooks from latest DNA artifact into studio_hook_templates")
    sp.add_argument("--min-sample", type=int, default=30,
                    help="skip categories with fewer than N samples")
    sp.set_defaults(func=_cmd_promote_hooks)

    sp = sub.add_parser("generate", help="multi-LLM generation for a brief")
    sp.add_argument("--topic", required=True)
    sp.add_argument("--angle", default="教程")
    sp.add_argument("--length", type=int, default=600)
    sp.add_argument("--cta", default="soft", choices=["none", "soft", "strong"])
    sp.add_argument("--niche", default=None)
    sp.add_argument("--extra", default=None, help="extra constraints (free text)")
    sp.add_argument("--llms", default=registry.DEFAULT_SPEC,
                    help="comma-separated, e.g. claude,deepseek or claude:sonnet,deepseek")
    sp.add_argument("--k-refs", type=int, default=8)
    sp.add_argument("--no-html", action="store_true")
    sp.add_argument("--json-out", default=None)
    sp.set_defaults(func=_cmd_generate)

    sp = sub.add_parser("compose", help="multi-agent generation (strategist→drafter pool→critic pool→refiner)")
    sp.add_argument("--topic", required=True)
    sp.add_argument("--angle", default="教程")
    sp.add_argument("--length", type=int, default=600)
    sp.add_argument("--cta", default="soft", choices=["none", "soft", "strong"])
    sp.add_argument("--niche", default=None)
    sp.add_argument("--extra", default=None)
    sp.add_argument("--strategist", default="claude:opus")
    sp.add_argument("--drafters", default="claude:opus,deepseek,openai")
    sp.add_argument("--critics", default="claude:sonnet,deepseek")
    sp.add_argument("--refiner", default="claude:opus")
    sp.add_argument("--skip-strategist", action="store_true")
    sp.add_argument("--skip-critics", action="store_true")
    sp.add_argument("--skip-refiner", action="store_true")
    sp.add_argument("--no-html", action="store_true")
    sp.add_argument("--json-out", default=None)
    sp.set_defaults(func=_cmd_compose)

    lib = sub.add_parser("library", help="library (corpus) management")
    lib_sub = lib.add_subparsers(dest="lib_cmd", required=True)
    sp = lib_sub.add_parser("list", help="list libraries")
    sp.set_defaults(func=_cmd_lib_list)
    sp = lib_sub.add_parser("active", help="show active library")
    sp.set_defaults(func=_cmd_lib_active)
    sp = lib_sub.add_parser("activate", help="switch active library")
    sp.add_argument("lib_id")
    sp.set_defaults(func=_cmd_lib_activate)
    sp = lib_sub.add_parser("add", help="add a library from a local .db file")
    sp.add_argument("path")
    sp.add_argument("--name", default=None)
    sp.add_argument("--id", dest="lib_id", default=None)
    sp.add_argument("--platform", default="xiaohongshu",
                    help="xiaohongshu|douyin|kuaishou|bilibili|youtube|reddit|x|other")
    sp.set_defaults(func=_cmd_lib_add)
    sp = lib_sub.add_parser("set-platform", help="change a library's platform tag")
    sp.add_argument("lib_id")
    sp.add_argument("platform")
    sp.set_defaults(func=_cmd_lib_set_platform)
    sp = lib_sub.add_parser("delete", help="delete a library")
    sp.add_argument("lib_id")
    sp.set_defaults(func=_cmd_lib_delete)

    sp = sub.add_parser("serve", help="run the FastAPI HTTP server")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8765)
    sp.add_argument("--reload", action="store_true")
    sp.set_defaults(func=_cmd_serve)

    sp = sub.add_parser("export-public", help="export DNA + drafts as static JSON for the frontend")
    sp.set_defaults(func=_cmd_export_public)

    args = p.parse_args(argv)
    return args.func(args)


def _cmd_compose(args: argparse.Namespace) -> int:
    db.apply_migrations(verbose=False)
    brief = Brief(
        topic=args.topic,
        angle=args.angle,
        target_length=args.length,
        cta_strength=args.cta,
        niche=args.niche or "",
        extra_constraints=args.extra or "",
    )
    cfg = agent_pipeline.PipelineConfig(
        strategist_spec=args.strategist,
        drafter_spec=args.drafters,
        critic_spec=args.critics,
        refiner_spec=args.refiner,
        skip_strategist=args.skip_strategist,
        skip_critics=args.skip_critics,
        skip_refiner=args.skip_refiner,
    )
    print(f"[compose] brief: {brief.to_json()}")
    print(f"[compose] drafters: {cfg.drafter_spec} / critics: {cfg.critic_spec}")
    bundle = asyncio.run(agent_pipeline.run_pipeline(brief, cfg))
    print(f"[compose] draft_id: {bundle['draft_id']}")
    for c in bundle["drafts"]:
        head = "✗" if c["error"] else "✓"
        avg = f" critique_avg={c['critique_avg']:.1f}" if c.get("critique_avg") else ""
        print(f"  {head} drafter {c['llm']}: {c['payload']['title'][:50]!r}"
              f" self={c['payload']['self_score']:.1f}{avg}")
    if bundle.get("refined"):
        r = bundle["refined"]
        print(f"  → refined: {r['payload']['title'][:60]!r}")
    if bundle.get("final"):
        f = bundle["final"]
        print(f"  ★ final: {f['payload']['title'][:60]!r} ({f['llm']})")
    t = bundle["totals"]
    print(f"[compose] elapsed {t['elapsed_s']}s · cost est ${t['cost_usd']:.4f}")

    if not args.no_html:
        from .agents import render_compose
        fp = render_compose.render(bundle)
        print(f"[compose] html: {fp}")
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[compose] json: {args.json_out}")
    return 0


def _cmd_lib_list(args: argparse.Namespace) -> int:
    libs = library.list_libraries()
    active = library.active_lib_id()
    out = [
        {
            "lib_id": l.lib_id,
            "display_name": l.display_name,
            "platform": l.platform,
            "notes": l.notes_count,
            "comments": l.comments_count,
            "size_mb": round(l.size_bytes / 1_048_576, 2),
            "active": l.lib_id == active,
            "source": l.source,
        }
        for l in libs
    ]
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def _cmd_lib_active(args: argparse.Namespace) -> int:
    lid = library.active_lib_id()
    meta = library.get_meta(lid)
    if meta is None:
        print(json.dumps({"lib_id": lid, "missing": True}))
        return 0
    print(json.dumps({"lib_id": lid, "display_name": meta.display_name,
                      "notes": meta.notes_count}, ensure_ascii=False))
    return 0


def _cmd_lib_activate(args: argparse.Namespace) -> int:
    library.set_active(args.lib_id)
    print(f"[library] active = {args.lib_id}")
    return 0


def _cmd_lib_add(args: argparse.Namespace) -> int:
    meta = library.register_existing(
        Path(args.path), display_name=args.name, lib_id=args.lib_id,
        platform=getattr(args, "platform", "xiaohongshu"),
    )
    print(json.dumps({"lib_id": meta.lib_id, "display_name": meta.display_name,
                      "notes": meta.notes_count, "platform": meta.platform},
                     ensure_ascii=False))
    return 0


def _cmd_lib_set_platform(args: argparse.Namespace) -> int:
    meta = library.set_platform(args.lib_id, args.platform)
    print(json.dumps({"lib_id": meta.lib_id, "platform": meta.platform}, ensure_ascii=False))
    return 0


def _cmd_lib_delete(args: argparse.Namespace) -> int:
    library.delete(args.lib_id)
    print(f"[library] deleted {args.lib_id}")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError:
        print("uvicorn not installed. Run: pip install uvicorn fastapi python-multipart",
              file=sys.stderr)
        return 2
    uvicorn.run("studio.api:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def _cmd_export_public(args: argparse.Namespace) -> int:
    from . import config
    from .api import export as api_export
    paths = api_export.export_all(config.PUBLIC_DATA_DIR)
    print(json.dumps(paths, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""FastAPI server. `studio serve` mounts this app.

Endpoints:
    GET    /api/health
    GET    /api/status
    GET    /api/libraries
    GET    /api/libraries/active
    POST   /api/libraries/upload          (multipart .db)
    POST   /api/libraries/{lib_id}/activate
    DELETE /api/libraries/{lib_id}
    POST   /api/libraries/{lib_id}/analyze   (run DNA + rag build for that lib)
    GET    /api/dna/latest
    GET    /api/dna/versions
    GET    /api/dna/{version}
    GET    /api/rag/search?q=...&k=...
    POST   /api/compose                   (multi-agent generate)
    GET    /api/drafts
    GET    /api/drafts/{draft_id}
    POST   /api/drafts/{draft_id}/candidates/{cid}/score   (human score 1-5)
    POST   /api/drafts/{draft_id}/candidates/{cid}/choose
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .. import config, db, library, project
from ..agents import pipeline as agent_pipeline
from ..analysis import extract_dna, promote_hooks, render_report
from ..brief import Brief
from ..rag import build_index, retrieve
from ..strategy import pipeline as strategy_pipeline
from ..strategy.models import AccountInput
from ..insight import pipeline as insight_pipeline

load_dotenv(dotenv_path=config.REPO_ROOT / ".env", override=True)

app = FastAPI(
    title="xhs Account Rise Studio API",
    version="0.2.0",
    description="Multi-agent xhs content studio backend.",
)

# Allow the static frontend (any origin during dev). Production deploys can
# narrow this via env var.
import os
_allowed = os.environ.get("STUDIO_CORS_ORIGINS", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed.split(",") if _allowed != "*" else ["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------- health / status -----------------

@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "ts": int(time.time())}


@app.get("/api/status")
def status() -> dict[str, Any]:
    db.apply_migrations(verbose=False)
    lid = library.active_lib_id()
    meta = library.get_meta(lid)
    with db.connect(read_only=True) as con:
        def _safe(t: str) -> int:
            try:
                return con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except Exception:
                return 0
        counts = {
            "notes": _safe("notes"),
            "comments": _safe("comments"),
            "fts_notes": _safe("studio_fts_notes"),
            "fts_comments": _safe("studio_fts_comments"),
            "drafts": _safe("studio_drafts"),
            "candidates": _safe("studio_draft_candidates"),
            "dna_artifacts": _safe("studio_dna_artifacts"),
            "agent_traces": _safe("studio_agent_traces"),
            "critiques": _safe("studio_critiques"),
            "hook_templates": _safe("studio_hook_templates"),
        }
    return {
        "active_library": {
            "lib_id": lid,
            "display_name": meta.display_name if meta else "(missing)",
            "notes": meta.notes_count if meta else 0,
        },
        "counts": counts,
        "providers": {
            "anthropic": bool(config.anthropic_key()),
            "deepseek": bool(config.deepseek_key()),
            "openai": bool(config.openai_key()),
        },
    }


# ---------------- projects -----------------------

class ProjectInput(BaseModel):
    name: str
    description: str = ""
    emoji: str = "📁"


@app.get("/api/projects")
def list_projects_endpoint(include_archived: bool = False) -> dict[str, Any]:
    project.ensure_bootstrap()
    items = project.list_projects(include_archived=include_archived)
    active = project.active_project_id()
    return {
        "projects": [
            {
                "project_id": p.project_id,
                "name": p.name,
                "description": p.description,
                "emoji": p.emoji,
                "is_default": p.is_default,
                "archived": p.archived,
                "created_at": p.created_at,
                "active": p.project_id == active,
            } for p in items
        ],
        "active": active,
    }


@app.post("/api/projects")
def create_project(req: ProjectInput) -> dict[str, Any]:
    try:
        p = project.create(req.name, req.description, req.emoji)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "project_id": p.project_id, "name": p.name,
        "emoji": p.emoji, "description": p.description,
    }


@app.post("/api/projects/{project_id}/activate")
def activate_project(project_id: str) -> dict[str, str]:
    try:
        project.set_active(project_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"active": project_id}


@app.patch("/api/projects/{project_id}")
def patch_project(project_id: str, req: ProjectInput) -> dict[str, Any]:
    try:
        p = project.update_meta(project_id, name=req.name,
                                description=req.description, emoji=req.emoji)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {
        "project_id": p.project_id, "name": p.name,
        "emoji": p.emoji, "description": p.description,
    }


@app.delete("/api/projects/{project_id}")
def archive_project(project_id: str) -> dict[str, str]:
    try:
        project.archive(project_id)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))
    return {"archived": project_id}


# ---------------- library ------------------------

@app.get("/api/libraries")
def list_libraries(all: bool = False) -> list[dict[str, Any]]:
    active = library.active_lib_id()
    libs = library.list_all_libraries() if all else library.list_libraries()
    return [
        {
            "lib_id": l.lib_id,
            "display_name": l.display_name,
            "uploaded_at": l.uploaded_at,
            "source": l.source,
            "notes_count": l.notes_count,
            "comments_count": l.comments_count,
            "size_bytes": l.size_bytes,
            "platform": l.platform,
            "project_id": l.project_id,
            "active": l.lib_id == active,
        }
        for l in libs
    ]


@app.get("/api/platforms")
def list_platforms() -> list[dict[str, str]]:
    return [
        {"id": p, "label": library.PLATFORM_LABELS[p]}
        for p in library.SUPPORTED_PLATFORMS
    ]


@app.get("/api/libraries/active")
def active_library() -> dict[str, Any]:
    lid = library.active_lib_id()
    meta = library.get_meta(lid)
    if not meta:
        return {"lib_id": lid, "missing": True}
    return {
        "lib_id": lid,
        "display_name": meta.display_name,
        "notes_count": meta.notes_count,
        "comments_count": meta.comments_count,
    }


@app.post("/api/libraries/upload")
async def upload_library(
    file: UploadFile = File(...),
    display_name: str = Form(...),
    platform: str = Form("xiaohongshu"),
) -> dict[str, Any]:
    blob = await file.read()
    if len(blob) < 4 or blob[:4] != b"SQLi":
        raise HTTPException(400, "uploaded file is not a SQLite database")

    final_platform = platform
    detected_scores: dict[str, int] = {}
    if platform == "auto":
        final_platform, detected_scores = library.detect_platform_from_blob(blob)

    try:
        meta = library.adopt_bytes(
            blob, display_name=display_name, platform=final_platform,
        )
    except Exception as e:
        raise HTTPException(400, f"failed to adopt library: {e}")
    return {
        "lib_id": meta.lib_id,
        "display_name": meta.display_name,
        "notes_count": meta.notes_count,
        "size_bytes": meta.size_bytes,
        "platform": meta.platform,
        "detected_platform": final_platform if platform == "auto" else None,
        "detection_scores": detected_scores,
    }


@app.post("/api/libraries/import")
async def import_library(
    file: UploadFile = File(...),
    display_name: str = Form(...),
    platform: str = Form("auto"),
    activate: str = Form("1"),
    analyze: str = Form("1"),
) -> dict[str, Any]:
    """One-shot import: detect platform + adopt + activate + analyze.

    This is the path the frontend hero dropzone calls — turns "drag a .db in"
    into "fully ready to compose" with no extra clicks.
    """
    blob = await file.read()
    if len(blob) < 4 or blob[:4] != b"SQLi":
        raise HTTPException(400, "uploaded file is not a SQLite database")

    final_platform = platform
    detected_scores: dict[str, int] = {}
    if platform == "auto":
        final_platform, detected_scores = library.detect_platform_from_blob(blob)

    try:
        meta = library.adopt_bytes(
            blob, display_name=display_name, platform=final_platform,
        )
    except Exception as e:
        raise HTTPException(400, f"failed to adopt library: {e}")

    result: dict[str, Any] = {
        "lib_id": meta.lib_id,
        "display_name": meta.display_name,
        "platform": meta.platform,
        "notes_count": meta.notes_count,
        "size_bytes": meta.size_bytes,
        "detected_platform": final_platform if platform == "auto" else None,
        "detection_scores": detected_scores,
    }

    # Activate first so subsequent analyze() targets the new lib.
    if activate in ("1", "true", "yes"):
        try:
            library.set_active(meta.lib_id)
            result["activated"] = True
        except Exception as e:
            result["activate_error"] = str(e)

    if analyze in ("1", "true", "yes"):
        try:
            db.apply_migrations(verbose=False)
            from ..analysis import extract_dna, promote_hooks, render_report
            from ..rag import build_index
            fts_stats = build_index.rebuild_all()
            artifact = extract_dna.build_dna()
            extract_dna.persist(artifact)
            render_report.render(artifact)
            promo = promote_hooks.promote()
            result["dna_version"] = artifact["version"]
            result["fts"] = fts_stats
            result["promoted_hooks"] = promo.get("promoted", [])
            result["analyzed"] = True
        except Exception as e:
            result["analyze_error"] = str(e)
            result["analyzed"] = False

    return result


@app.post("/api/libraries/detect-platform")
async def detect_platform_endpoint(file: UploadFile = File(...)) -> dict[str, Any]:
    """Sniff a .db without persisting it. For preview before commit.

    SQLite truncates poorly — opening a partial blob errors out. So we read
    the whole upload even for the preview (cost of one extra MB on a 100MB
    DB is negligible vs. the UX value of correct detection).
    """
    blob = await file.read()
    plat, scores = library.detect_platform_from_blob(blob)
    return {"platform": plat, "scores": scores}


class PlatformRequest(BaseModel):
    platform: str


@app.post("/api/libraries/{lib_id}/platform")
def set_library_platform(lib_id: str, req: PlatformRequest) -> dict[str, Any]:
    try:
        meta = library.set_platform(lib_id, req.platform)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"lib_id": meta.lib_id, "platform": meta.platform}


@app.post("/api/libraries/{lib_id}/activate")
def activate_library(lib_id: str) -> dict[str, str]:
    try:
        library.set_active(lib_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"active": lib_id}


@app.delete("/api/libraries/{lib_id}")
def delete_library(lib_id: str) -> dict[str, str]:
    try:
        library.delete(lib_id)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"deleted": lib_id}


@app.post("/api/libraries/{lib_id}/analyze")
def analyze_library(lib_id: str) -> dict[str, Any]:
    """Switch to lib_id, ensure migrations, rebuild FTS, run DNA + promote hooks."""
    prev = library.active_lib_id()
    library.set_active(lib_id)
    db.apply_migrations(verbose=False)
    fts_stats = build_index.rebuild_all()
    artifact = extract_dna.build_dna()
    extract_dna.persist(artifact)
    render_report.render(artifact)
    promo = promote_hooks.promote()
    if prev:
        try:
            library.set_active(prev)
        except ValueError:
            pass
    return {
        "library": lib_id,
        "fts": fts_stats,
        "dna_version": artifact["version"],
        "summary": artifact["summary"],
        "promoted_hooks": promo,
    }


# ---------------- DNA artifacts ------------------

@app.get("/api/dna/versions")
def list_dna_versions() -> list[dict[str, Any]]:
    with db.connect(read_only=True) as con:
        try:
            rows = list(con.execute(
                "SELECT version, created_at, summary FROM studio_dna_artifacts"
                " ORDER BY created_at DESC"
            ))
        except Exception:
            return []
    return [
        {
            "version": r["version"],
            "created_at": r["created_at"],
            "summary": json.loads(r["summary"]) if r["summary"] else {},
        }
        for r in rows
    ]


@app.get("/api/dna/latest")
def latest_dna() -> dict[str, Any]:
    with db.connect(read_only=True) as con:
        try:
            row = con.execute(
                "SELECT payload_json FROM studio_dna_artifacts"
                " ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        except Exception:
            row = None
    if not row:
        raise HTTPException(404, "no DNA artifact — run /api/libraries/{id}/analyze")
    return json.loads(row["payload_json"])


@app.get("/api/dna/{version}")
def get_dna(version: str) -> dict[str, Any]:
    with db.connect(read_only=True) as con:
        row = con.execute(
            "SELECT payload_json FROM studio_dna_artifacts WHERE version = ?",
            (version,),
        ).fetchone()
    if not row:
        raise HTTPException(404, "not found")
    return json.loads(row["payload_json"])


# ---------------- RAG debug ----------------------

@app.get("/api/rag/search")
def rag_search(q: str, k: int = 8, n: int = 15) -> dict[str, Any]:
    return retrieve.retrieve_for_brief(q, k_notes=k, n_comments=n)


# ---------------- compose ------------------------

class ComposeRequest(BaseModel):
    topic: str
    angle: str = "教程"
    target_length: int = Field(default=600, ge=120, le=3000)
    cta_strength: str = Field(default="soft", pattern="^(none|soft|strong)$")
    niche: str = ""
    extra_constraints: str = ""
    platform: str | None = None  # auto-inherit from active library if None
    strategist_spec: str = "claude:opus"
    drafter_spec: str = "claude:opus,deepseek,openai"
    critic_spec: str = "claude:sonnet,deepseek"
    refiner_spec: str = "claude:opus"
    synthesizer_spec: str = "claude:opus"
    planner_spec: str = "claude:opus"
    skip_strategist: bool = False
    skip_critics: bool = False
    skip_refiner: bool = False
    skip_synthesizer: bool = False
    skip_planner: bool = False


@app.post("/api/compose")
async def compose(req: ComposeRequest) -> dict[str, Any]:
    platform = req.platform or library.get_meta(library.active_lib_id()) and \
        library.get_meta(library.active_lib_id()).platform or "xiaohongshu"
    brief = Brief(
        topic=req.topic, angle=req.angle, target_length=req.target_length,
        cta_strength=req.cta_strength, niche=req.niche,
        extra_constraints=req.extra_constraints,
        platform=library.normalise_platform(platform),
    )
    cfg = agent_pipeline.PipelineConfig(
        strategist_spec=req.strategist_spec,
        drafter_spec=req.drafter_spec,
        critic_spec=req.critic_spec,
        refiner_spec=req.refiner_spec,
        synthesizer_spec=req.synthesizer_spec,
        planner_spec=req.planner_spec,
        skip_strategist=req.skip_strategist,
        skip_critics=req.skip_critics,
        skip_refiner=req.skip_refiner,
        skip_synthesizer=req.skip_synthesizer,
        skip_planner=req.skip_planner,
    )
    bundle = await agent_pipeline.run_pipeline(brief, cfg)
    return bundle


# ---------------- insight report (Claude × OpenAI) ----------

class InsightRequest(BaseModel):
    library_id: str
    claude_spec: str = "claude:opus"
    openai_spec: str = "openai"
    moderator_spec: str = "claude:opus"


@app.post("/api/insight/run")
async def insight_run(req: InsightRequest) -> dict[str, Any]:
    try:
        return await insight_pipeline.run(
            req.library_id,
            claude_spec=req.claude_spec,
            openai_spec=req.openai_spec,
            moderator_spec=req.moderator_spec,
        )
    except LookupError as e:
        raise HTTPException(404, str(e))
    except RuntimeError as e:
        raise HTTPException(409, str(e))


@app.get("/api/insight")
def list_insights(library_id: str | None = None, limit: int = 30) -> list[dict[str, Any]]:
    return insight_pipeline.list_reports(library_id=library_id, limit=limit)


@app.get("/api/insight/{report_id}")
def get_insight(report_id: str) -> dict[str, Any]:
    r = insight_pipeline.get_report(report_id)
    if not r:
        raise HTTPException(404, "report not found")
    return r


# ---------------- drafts -------------------------

@app.get("/api/drafts")
def list_drafts(limit: int = 50, library_id: str | None = None,
                all_projects: bool = False) -> list[dict[str, Any]]:
    project.ensure_bootstrap()
    pid = project.active_project_id()
    base_sql = (
        "SELECT d.draft_id, d.generated_at, d.mode, d.library_id, d.project_id,"
        " d.final_candidate_id, d.brief_json,"
        " (SELECT title FROM studio_draft_candidates"
        "  WHERE candidate_id = d.final_candidate_id) AS final_title,"
        " (SELECT COUNT(*) FROM studio_draft_candidates"
        "  WHERE draft_id = d.draft_id) AS candidate_count"
        " FROM studio_drafts d WHERE 1=1"
    )
    args: list[Any] = []
    if not all_projects:
        base_sql += " AND (d.project_id = ? OR d.project_id IS NULL)"
        args.append(pid)
    if library_id:
        base_sql += " AND d.library_id = ?"
        args.append(library_id)
    base_sql += " ORDER BY d.generated_at DESC LIMIT ?"
    args.append(limit)
    with db.connect(read_only=True) as con:
        rows = [dict(r) for r in con.execute(base_sql, args)]
    for r in rows:
        try:
            r["brief"] = json.loads(r.pop("brief_json"))
        except (json.JSONDecodeError, TypeError):
            r["brief"] = {}
    return rows


@app.get("/api/drafts/{draft_id}")
def get_draft(draft_id: str) -> dict[str, Any]:
    with db.connect(read_only=True) as con:
        d = con.execute(
            "SELECT * FROM studio_drafts WHERE draft_id = ?",
            (draft_id,),
        ).fetchone()
        if not d:
            raise HTTPException(404, "draft not found")
        cands = [dict(c) for c in con.execute(
            "SELECT * FROM studio_draft_candidates WHERE draft_id = ?"
            " ORDER BY chosen DESC, self_score DESC",
            (draft_id,),
        )]
        crits = [dict(c) for c in con.execute(
            "SELECT * FROM studio_critiques WHERE draft_id = ?",
            (draft_id,),
        )]
        trace = [dict(t) for t in con.execute(
            "SELECT * FROM studio_agent_traces WHERE draft_id = ?"
            " ORDER BY step_index ASC",
            (draft_id,),
        )]
    d_dict = dict(d)
    try:
        notes_payload = json.loads(d_dict.get("notes") or "{}")
    except (json.JSONDecodeError, TypeError):
        notes_payload = {}
    crit_by_cand: dict[str, list[dict[str, Any]]] = {}
    for c in crits:
        c["scores"] = json.loads(c.pop("scores_json") or "{}")
        c["risk_flags"] = json.loads(c.pop("risk_flags_json") or "[]")
        crit_by_cand.setdefault(c["candidate_id"], []).append(c)
    for c in cands:
        c["tags"] = json.loads(c.pop("tags_json") or "[]")
        c["meta"] = json.loads(c.pop("meta_json") or "{}")
        c["critiques"] = crit_by_cand.get(c["candidate_id"], [])
    return {
        "draft": d_dict | {"brief": json.loads(d["brief_json"])},
        "candidates": cands,
        "trace": trace,
        "plan": notes_payload.get("plan", {}),
        "strategy": notes_payload.get("strategy", {}),
    }


class ScoreRequest(BaseModel):
    score: int = Field(ge=1, le=5)


@app.post("/api/drafts/{draft_id}/candidates/{candidate_id}/score")
def score_candidate(draft_id: str, candidate_id: str, req: ScoreRequest) -> dict[str, Any]:
    with db.connect() as con:
        cur = con.execute(
            "UPDATE studio_draft_candidates SET human_score = ?"
            " WHERE candidate_id = ? AND draft_id = ?",
            (req.score, candidate_id, draft_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "candidate not found")
    return {"ok": True, "score": req.score}


# ---------------- strategy (起号策略) -----------------

class StrategyInput(BaseModel):
    positioning: str
    target_audience: str
    cycle_weeks: int = Field(default=4, ge=1, le=24)
    posts_per_week: int = Field(default=3, ge=1, le=14)
    personal_strengths: str = ""
    constraints: str = ""
    platform: str | None = None
    positioner_spec: str = "claude:opus"


class StrategyExpandRequest(BaseModel):
    chosen_direction_idx: int = Field(ge=0)
    topicgen_spec: str = "claude:opus,deepseek,openai"
    scheduler_spec: str = "claude:opus"
    resourcer_spec: str = "claude:opus"


@app.post("/api/strategy/propose")
async def strategy_propose(req: StrategyInput) -> dict[str, Any]:
    plat = req.platform
    if not plat:
        meta = library.get_meta(library.active_lib_id())
        plat = meta.platform if meta else "xiaohongshu"
    inp = AccountInput(
        positioning=req.positioning,
        target_audience=req.target_audience,
        cycle_weeks=req.cycle_weeks,
        posts_per_week=req.posts_per_week,
        personal_strengths=req.personal_strengths,
        constraints=req.constraints,
        platform=library.normalise_platform(plat),
    )
    try:
        result = await strategy_pipeline.propose(inp, positioner_spec=req.positioner_spec)
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    return result


@app.post("/api/strategy/{pack_id}/expand")
async def strategy_expand(pack_id: str, req: StrategyExpandRequest) -> dict[str, Any]:
    try:
        return await strategy_pipeline.expand(
            pack_id, req.chosen_direction_idx,
            topicgen_spec=req.topicgen_spec,
            scheduler_spec=req.scheduler_spec,
            resourcer_spec=req.resourcer_spec,
        )
    except LookupError as e:
        raise HTTPException(404, str(e))
    except IndexError as e:
        raise HTTPException(400, str(e))


@app.get("/api/strategy")
def list_strategies(limit: int = 30, all_projects: bool = False) -> list[dict[str, Any]]:
    project.ensure_bootstrap()
    pid = project.active_project_id()
    where = "" if all_projects else " WHERE (project_id = ? OR project_id IS NULL)"
    args = [] if all_projects else [pid]
    with db.connect(read_only=True) as con:
        try:
            rows = list(con.execute(
                "SELECT pack_id, library_id, platform, created_at, updated_at,"
                " status, input_json, chosen_direction_idx, elapsed_s, project_id"
                " FROM studio_strategies" + where +
                " ORDER BY created_at DESC LIMIT ?",
                (*args, limit),
            ))
        except Exception:
            return []
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["input"] = json.loads(d.pop("input_json"))
        except Exception:
            d["input"] = {}
        out.append(d)
    return out


@app.get("/api/strategy/{pack_id}")
def get_strategy(pack_id: str) -> dict[str, Any]:
    with db.connect(read_only=True) as con:
        row = con.execute(
            "SELECT * FROM studio_strategies WHERE pack_id = ?", (pack_id,)
        ).fetchone()
    if not row:
        raise HTTPException(404, "strategy pack not found")
    d = dict(row)
    try: d["input"] = json.loads(d.pop("input_json"))
    except Exception: d["input"] = {}
    try: d["directions"] = json.loads(d.pop("directions_json"))
    except Exception: d["directions"] = []
    pack_json = d.pop("pack_json", None)
    d["pack"] = json.loads(pack_json) if pack_json else None
    return d


@app.delete("/api/strategy/{pack_id}")
def delete_strategy(pack_id: str) -> dict[str, str]:
    with db.connect() as con:
        cur = con.execute("DELETE FROM studio_strategies WHERE pack_id = ?", (pack_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "not found")
    return {"deleted": pack_id}


@app.post("/api/drafts/{draft_id}/candidates/{candidate_id}/choose")
def choose_candidate(draft_id: str, candidate_id: str) -> dict[str, Any]:
    with db.connect() as con:
        # Verify candidate exists
        row = con.execute(
            "SELECT 1 FROM studio_draft_candidates"
            " WHERE candidate_id = ? AND draft_id = ?",
            (candidate_id, draft_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "candidate not found")
        # Clear chosen for this draft, then set this one
        con.execute(
            "UPDATE studio_draft_candidates SET chosen = 0 WHERE draft_id = ?",
            (draft_id,),
        )
        con.execute(
            "UPDATE studio_draft_candidates SET chosen = 1 WHERE candidate_id = ?",
            (candidate_id,),
        )
        con.execute(
            "UPDATE studio_drafts SET final_candidate_id = ? WHERE draft_id = ?",
            (candidate_id, draft_id),
        )
    return {"chosen": candidate_id}

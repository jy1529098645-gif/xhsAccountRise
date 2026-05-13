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

from .. import config, db, library
from ..agents import pipeline as agent_pipeline
from ..analysis import extract_dna, promote_hooks, render_report
from ..brief import Brief
from ..rag import build_index, retrieve

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


# ---------------- library ------------------------

@app.get("/api/libraries")
def list_libraries() -> list[dict[str, Any]]:
    active = library.active_lib_id()
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
            "active": l.lib_id == active,
        }
        for l in library.list_libraries()
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
    try:
        meta = library.adopt_bytes(blob, display_name=display_name, platform=platform)
    except Exception as e:
        raise HTTPException(400, f"failed to adopt library: {e}")
    return {
        "lib_id": meta.lib_id,
        "display_name": meta.display_name,
        "notes_count": meta.notes_count,
        "size_bytes": meta.size_bytes,
        "platform": meta.platform,
    }


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
    skip_strategist: bool = False
    skip_critics: bool = False
    skip_refiner: bool = False
    skip_synthesizer: bool = False


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
        skip_strategist=req.skip_strategist,
        skip_critics=req.skip_critics,
        skip_refiner=req.skip_refiner,
        skip_synthesizer=req.skip_synthesizer,
    )
    bundle = await agent_pipeline.run_pipeline(brief, cfg)
    return bundle


# ---------------- drafts -------------------------

@app.get("/api/drafts")
def list_drafts(limit: int = 50, library_id: str | None = None) -> list[dict[str, Any]]:
    with db.connect(read_only=True) as con:
        if library_id:
            cur = con.execute(
                "SELECT d.draft_id, d.generated_at, d.mode, d.library_id,"
                " d.final_candidate_id, d.brief_json,"
                " (SELECT title FROM studio_draft_candidates"
                "  WHERE candidate_id = d.final_candidate_id) AS final_title,"
                " (SELECT COUNT(*) FROM studio_draft_candidates"
                "  WHERE draft_id = d.draft_id) AS candidate_count"
                " FROM studio_drafts d WHERE d.library_id = ?"
                " ORDER BY d.generated_at DESC LIMIT ?",
                (library_id, limit),
            )
        else:
            cur = con.execute(
                "SELECT d.draft_id, d.generated_at, d.mode, d.library_id,"
                " d.final_candidate_id, d.brief_json,"
                " (SELECT title FROM studio_draft_candidates"
                "  WHERE candidate_id = d.final_candidate_id) AS final_title,"
                " (SELECT COUNT(*) FROM studio_draft_candidates"
                "  WHERE draft_id = d.draft_id) AS candidate_count"
                " FROM studio_drafts d"
                " ORDER BY d.generated_at DESC LIMIT ?",
                (limit,),
            )
        rows = [dict(r) for r in cur]
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
        "draft": dict(d) | {"brief": json.loads(d["brief_json"])},
        "candidates": cands,
        "trace": trace,
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

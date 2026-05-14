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
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import config, db, library, project
from ..agents import pipeline as agent_pipeline
from ..analysis import extract_dna, promote_hooks, render_report
from ..brief import Brief
from ..rag import build_index, retrieve
from ..strategy import pipeline as strategy_pipeline
from ..strategy.models import AccountInput
from ..insight import pipeline as insight_pipeline
from ..insight import external as external_reports
from .. import retrospective as retro
from .. import jobs as job_registry

load_dotenv(dotenv_path=config.REPO_ROOT / ".env", override=True)

__version__ = "0.17.0"

app = FastAPI(
    title="EZAccountRise API",
    version=__version__,
    description="Multi-agent social-media content studio backend.",
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
def delete_project(project_id: str, hard: bool = False) -> dict[str, Any]:
    """Default behaviour: soft-archive (project hidden but data retained).
    Pass ?hard=true to PERMANENTLY remove the project + all its data
    (drafts, strategies, reports, performance, etc.). Refuses to remove
    the default project. Returns per-table delete counts when hard."""
    try:
        if hard:
            counts = project.hard_delete(project_id)
            return {"deleted": project_id, "hard": True, "rows": counts}
        project.archive(project_id)
        return {"archived": project_id, "hard": False}
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))


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
    auto_adapt: str = Form("1"),
) -> dict[str, Any]:
    """One-shot import: schema-validate + detect platform + adopt + activate +
    optional AI schema-adapter + DNA analyze. Returns rich status so the
    frontend can decide whether to proceed to the insight step.
    """
    blob = await file.read()

    # Pre-flight: must be a valid SQLite. We *don't* fail-fast on schema
    # mismatch anymore — the AI adapter can normalise non-xhs schemas after
    # adoption. We only block if it's not even SQLite or is corrupt.
    validation = library.validate_schema_blob(blob)
    if validation.get("fatal") and not validation["tables"]:
        raise HTTPException(422, validation.get("fatal") or "not a valid SQLite file")

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
        "schema_warnings": validation.get("warnings", []),
    }

    # Activate first so subsequent analyze() targets the new lib.
    if activate in ("1", "true", "yes"):
        try:
            library.set_active(meta.lib_id)
            result["activated"] = True
        except Exception as e:
            result["activate_error"] = str(e)

    # Auto-adapter: if the source schema isn't canonical, ask Claude to propose
    # column mappings → save schema_map.json → subsequent db.connect()s see
    # canonical views automatically.
    if auto_adapt in ("1", "true", "yes"):
        try:
            from .. import adapt as _adapt
            source_info = _adapt.inspect_source(library.LIBRARIES_DIR / meta.lib_id / "xhs.db")
            if not _adapt.is_canonical(source_info):
                ad = await _adapt.adapt_library(meta.lib_id)
                result["adapter"] = {
                    "adapted": ad.get("adapted", False),
                    "notes_rows": ad.get("notes_rows"),
                    "source_tables": ad.get("source_tables", []),
                    "mapping_summary": _summarise_mapping(ad.get("mapping")),
                    "view_error": ad.get("view_error"),
                }
            else:
                result["adapter"] = {"adapted": False, "reason": "canonical schema"}
        except Exception as e:
            result["adapter_error"] = str(e)

    if analyze in ("1", "true", "yes"):
        # Run analysis in granular sub-tries so a partial failure (e.g. FTS
        # build crashes due to a weird view) STILL produces a DNA artifact.
        # The insight pipeline reads the latest persisted artifact, so it
        # matters more that *something* is saved than that everything works.
        from ..analysis import extract_dna, promote_hooks, render_report
        from ..rag import build_index

        try:
            db.apply_migrations(verbose=False)
        except Exception as e:
            result["migrate_error"] = repr(e)

        try:
            fts_stats = build_index.rebuild_all()
            result["fts"] = fts_stats
        except Exception as e:
            result["fts_error"] = repr(e)

        artifact: dict[str, Any] = {}
        try:
            artifact = extract_dna.build_dna()
        except Exception as e:
            # build_dna already swallows per-section errors; if it still
            # blew up we craft a minimal envelope so persist() can attach
            # raw_schema and we have *something* on disk.
            import time as _time
            from datetime import datetime as _dt, timezone as _tz, timedelta as _td
            artifact = {
                "version": _dt.now(_tz(_td(hours=8))).strftime("%Y-%m-%d"),
                "generated_at": int(_time.time()),
                "sections": {},
                "section_errors": {"build_dna": repr(e)},
                "summary": {"total_notes_analysed": 0, "dominant_hooks": [],
                            "generated_in_seconds": 0,
                            "section_errors": ["build_dna"]},
            }
            result["build_dna_error"] = repr(e)

        try:
            extract_dna.persist(artifact)  # attaches raw_schema inside
            result["dna_version"] = artifact["version"]
            result["section_errors"] = artifact.get("section_errors", {})
            result["analyzed"] = True
        except Exception as e:
            result["persist_error"] = repr(e)
            result["analyzed"] = False

        try:
            render_report.render(artifact)
        except Exception as e:
            result["render_error"] = repr(e)

        try:
            promo = promote_hooks.promote()
            result["promoted_hooks"] = promo.get("promoted", [])
        except Exception as e:
            result["promote_warning"] = repr(e)

    return result


def _summarise_mapping(mapping: dict[str, Any] | None) -> dict[str, Any]:
    """Render a small client-friendly summary of the adapter's column map."""
    if not mapping:
        return {}
    out: dict[str, Any] = {}
    for table in ("notes", "comments"):
        spec = mapping.get(table)
        if not spec:
            continue
        cols = spec.get("columns") or {}
        out[table] = {
            "source_table": spec.get("source_table"),
            "field_map": {
                k: (v.get("source") if isinstance(v, dict) else None)
                for k, v in cols.items()
            },
            "extra_filters": spec.get("extra_filters"),
        }
    if mapping.get("reasoning"):
        out["reasoning"] = mapping["reasoning"]
    return out


@app.post("/api/libraries/{lib_id}/adapt")
async def run_adapter(lib_id: str) -> dict[str, Any]:
    """Manually (re-)run the AI schema adapter on an existing library."""
    from .. import adapt as _adapt
    try:
        return await _adapt.adapt_library(lib_id)
    except LookupError as e:
        raise HTTPException(404, str(e))


@app.get("/api/libraries/{lib_id}/schema-map")
def get_schema_map(lib_id: str) -> dict[str, Any]:
    from .. import adapt as _adapt
    m = _adapt.load_map(lib_id)
    if m is None:
        return {"mapping": None, "summary": {}, "applied": False}
    return {"mapping": m, "summary": _summarise_mapping(m), "applied": True}


@app.delete("/api/libraries/{lib_id}/schema-map")
def clear_schema_map(lib_id: str) -> dict[str, str]:
    from .. import adapt as _adapt
    p = _adapt.schema_map_path(lib_id)
    if p.exists():
        p.unlink()
    return {"cleared": lib_id}


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
    # v0.52: multi-angle. When non-empty, drafter pool produces one candidate
    # per angle (cycling LLMs). `angle` stays as the singular fallback for
    # back-compat callers.
    angles: list[str] = Field(default_factory=list)
    target_length: int = Field(default=600, ge=120, le=3000)
    cta_strength: str = Field(default="soft", pattern="^(none|soft|strong)$")
    niche: str = ""
    extra_constraints: str = ""
    platform: str | None = None  # auto-inherit from active library if None
    # v0.51: Claude defaults dropped — too expensive. Reasoning roles → gpt-4o,
    # mechanical roles → deepseek. Users can override via the advanced UI.
    strategist_spec: str = "openai:gpt-4o"
    drafter_spec: str = "openai:gpt-4o"
    critic_spec: str = "deepseek"
    refiner_spec: str = "openai:gpt-4o"
    synthesizer_spec: str = "openai:gpt-4o"
    planner_spec: str = "deepseek"
    skip_strategist: bool = False
    skip_critics: bool = False
    skip_refiner: bool = False
    skip_synthesizer: bool = False
    skip_planner: bool = False
    fast_mode: bool = True  # default 2-stage pipeline (drafter → synth ∥ planner)


@app.post("/api/compose")
async def compose(req: ComposeRequest) -> dict[str, Any]:
    platform = req.platform or library.get_meta(library.active_lib_id()) and \
        library.get_meta(library.active_lib_id()).platform or "xiaohongshu"
    # De-dupe angles preserving order; ignore unknown values silently.
    from ..brief import ALL_ANGLES
    seen: set[str] = set()
    angles_clean: list[str] = []
    for a in req.angles or []:
        if a in ALL_ANGLES and a not in seen:
            angles_clean.append(a); seen.add(a)
    brief = Brief(
        topic=req.topic, angle=req.angle,
        angles=tuple(angles_clean),
        target_length=req.target_length,
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
        fast_mode=req.fast_mode,
    )
    bundle = await agent_pipeline.run_pipeline(brief, cfg)
    return bundle


# ---------------- insight report (Claude × OpenAI) ----------

class InsightRequest(BaseModel):
    library_id: str
    mode: str = "fast"  # "fast" (Sonnet × 2, no critique) | "deep" (Opus pipeline)
    claude_spec: str | None = None
    openai_spec: str = "openai"
    moderator_spec: str | None = None


@app.post("/api/insight/run")
async def insight_run(req: InsightRequest) -> dict[str, Any]:
    try:
        return await insight_pipeline.run(
            req.library_id,
            mode=req.mode,
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
    project.ensure_bootstrap()
    pid = project.active_project_id()
    r = insight_pipeline.get_report(report_id)
    if not r:
        raise HTTPException(404, "report not found")
    # Project isolation: a report only visible if it belongs to active project
    # (legacy reports with project_id=NULL are visible to all, as default fallback).
    rep_pid = r.get("project_id")
    if rep_pid and rep_pid != pid:
        raise HTTPException(404, "report not found in current project")
    return r


# ---------------- external (user-uploaded) reports + integration ----------

class ExternalReportUpload(BaseModel):
    name: str
    content: str
    library_id: str | None = None
    source: str = "粘贴文本"
    format: str = "text"


@app.post("/api/external_reports")
def upload_external_report(req: ExternalReportUpload) -> dict[str, Any]:
    if not req.name.strip():
        raise HTTPException(400, "name is required")
    if not req.content.strip():
        raise HTTPException(400, "content is required")
    return external_reports.save_external_report(
        name=req.name.strip(), content=req.content,
        library_id=req.library_id, source=req.source, format=req.format,
    )


@app.post("/api/external_reports/upload_file")
async def upload_external_report_file(
    file: UploadFile = File(...),
    name: str | None = Form(None),
    library_id: str | None = Form(None),
) -> dict[str, Any]:
    """Accept ANY file. Extract text best-effort (pypdf for .pdf, python-docx
    for .docx, plain decode for .tex / .md / .txt / anything else) and save
    whatever text we got. Returns the saved record plus an optional
    `extract_warning` if the parser hit issues but we kept the content.
    """
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    fname = file.filename or "uploaded"
    text, fmt, warn = external_reports.extract_text_from_bytes(fname, data)
    if not text.strip():
        # Even with parser failures we'd rather save a stub than 500.
        # But truly empty content → error so the user knows to try another file.
        raise HTTPException(400, warn or f"无法从 {fname} 提取出任何文字")
    saved = external_reports.save_external_report(
        name=(name or fname).strip() or fname,
        content=text,
        library_id=library_id or None,
        source=f"上传文件 · {fname}",
        format=fmt,
    )
    if warn:
        saved["extract_warning"] = warn
    return saved


@app.get("/api/external_reports")
def list_external_reports_api(library_id: str | None = None) -> list[dict[str, Any]]:
    return external_reports.list_external_reports(library_id=library_id)


@app.get("/api/external_reports/{report_id}")
def get_external_report_api(report_id: str) -> dict[str, Any]:
    r = external_reports.get_external_report(report_id)
    if not r:
        raise HTTPException(404, "external report not found")
    return r


@app.delete("/api/external_reports/{report_id}")
def delete_external_report_api(report_id: str) -> dict[str, bool]:
    ok = external_reports.delete_external_report(report_id)
    if not ok:
        raise HTTPException(404, "external report not found")
    return {"deleted": True}


class IntegrationRequest(BaseModel):
    source_ids: list[str]
    library_id: str | None = None
    include_consensus_report_id: str | None = None
    model_spec: str = "openai:gpt-4o"


@app.post("/api/external_reports/integrate")
async def integrate_external_reports_api(req: IntegrationRequest) -> dict[str, Any]:
    if not req.source_ids:
        raise HTTPException(400, "source_ids is required (at least one external report)")
    try:
        return await external_reports.integrate(
            req.source_ids,
            library_id=req.library_id,
            include_consensus_report_id=req.include_consensus_report_id,
            model_spec=req.model_spec,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(502, str(e))


@app.get("/api/integrated_reports")
def list_integrated_api(library_id: str | None = None) -> list[dict[str, Any]]:
    return external_reports.list_integrated_reports(library_id=library_id)


@app.get("/api/integrated_reports/{integrated_id}")
def get_integrated_api(integrated_id: str) -> dict[str, Any]:
    r = external_reports.get_integrated_report(integrated_id)
    if not r:
        raise HTTPException(404, "integrated report not found")
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
        " COALESCE("
        "   (SELECT title FROM studio_draft_candidates"
        "    WHERE candidate_id = d.final_candidate_id),"
        "   (SELECT title FROM studio_draft_candidates"
        "    WHERE draft_id = d.draft_id ORDER BY chosen DESC, self_score DESC LIMIT 1),"
        "   '(尚无候选)'"
        " ) AS final_title,"
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
    project.ensure_bootstrap()
    pid = project.active_project_id()
    with db.connect(read_only=True) as con:
        d = con.execute(
            "SELECT * FROM studio_drafts WHERE draft_id = ?"
            " AND (project_id = ? OR project_id IS NULL)",
            (draft_id, pid),
        ).fetchone()
        if not d:
            raise HTTPException(404, "draft not found in current project")
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

    # v0.53: bundle compliance + rag so DraftDetail.tsx makes one call.
    with db.connect(read_only=True) as con:
        comp_rows = list(con.execute(
            "SELECT check_id, candidate_id, checked_at, severity, hits_json"
            " FROM studio_compliance_checks WHERE draft_id = ?"
            " ORDER BY checked_at DESC",
            (draft_id,),
        ))
        var_children = list(con.execute(
            "SELECT draft_id, generated_at, variant_label,"
            " json_extract(brief_json,'$.angle') AS angle,"
            " (SELECT title FROM studio_draft_candidates"
            "  WHERE candidate_id = studio_drafts.final_candidate_id) AS final_title"
            " FROM studio_drafts WHERE parent_draft_id = ?"
            " ORDER BY generated_at DESC",
            (draft_id,),
        ))
    comp_by_cand: dict[str, dict] = {}
    for r in comp_rows:
        cid = r["candidate_id"]
        if cid in comp_by_cand: continue
        item = dict(r)
        try: item["hits"] = json.loads(item.pop("hits_json") or "[]")
        except Exception: item["hits"] = []
        comp_by_cand[cid] = item
    for c in cands:
        c["compliance"] = comp_by_cand.get(c["candidate_id"], {
            "severity": "pass", "hits": [],
        })
    try:
        rag_payload = json.loads(d_dict.get("rag_json") or "{}")
    except Exception:
        rag_payload = {}

    return {
        "draft": d_dict | {"brief": json.loads(d["brief_json"])},
        "candidates": cands,
        "trace": trace,
        "plan": notes_payload.get("plan", {}),
        "strategy": notes_payload.get("strategy", {}),
        "rag": {
            "refs": rag_payload.get("refs", []),
            "comments": rag_payload.get("comments", []),
            "hooks": rag_payload.get("hooks", []),
        },
        "variants": [dict(r) for r in var_children],
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
    # User-flow fix: positioning + target_audience used to be REQUIRED before
    # propose would run. But the user shouldn't have to know their positioning
    # before AI suggests it — that's literally what propose is for. Both are
    # now hints (empty = let AI propose with no user bias).
    positioning: str = ""
    target_audience: str = ""
    cycle_weeks: int = Field(default=4, ge=1, le=24)
    posts_per_week: int = Field(default=3, ge=1, le=14)
    personal_strengths: str = ""
    constraints: str = ""
    platform: str | None = None
    # v0.52: user can pre-pick which angles the schedule should cover.
    # Empty = AI picks freely (legacy).
    expected_angles: list[str] = Field(default_factory=list)
    # v0.55: anchor date for the cycle (ISO 'YYYY-MM-DD'). Empty = frontend
    # picks the next Monday as default. Backend stores it and includes it in
    # the pack so the Strategy page can show real calendar dates per slot.
    cycle_start_date: str = ""
    # v0.59: 8 大起号目标分类 — 决定 voice / 阶段权重 / 产品上下文必需性。
    # 见 studio.strategy.goals.GOAL_TYPES。Empty = 通用（兼容旧客户端）。
    goal_type: str = ""
    positioner_spec: str = "openai:gpt-4o"


class StrategyExpandRequest(BaseModel):
    # v0.59: legacy single-direction field kept for backward compat. New
    # frontend sends `chosen_direction_idxs` (list) for multi-direction.
    chosen_direction_idx: int = Field(default=0, ge=0)
    chosen_direction_idxs: list[int] = Field(default_factory=list)
    # v0.51: topic creativity gets gpt-4o + deepseek diversity; scheduling
    # (reasoning) → gpt-4o; resource compilation + body draft (volume) → deepseek.
    topicgen_spec: str = "openai:gpt-4o,deepseek"
    scheduler_spec: str = "openai:gpt-4o"
    resourcer_spec: str = "deepseek"
    drafter_spec: str = "deepseek"
    restart: bool = False  # cancel any in-flight expand for this pack + restart fresh


class StrategyAutofillRequest(BaseModel):
    personal_hint: str = ""
    constraints_hint: str = ""
    deep: bool = False  # default = 1 gpt-4o call (~10-15s); deep=true = dual-AI debate (~50s)
    claude_spec: str = "openai:gpt-4o"  # API kwarg name kept for back-compat
    openai_spec: str = "deepseek"       # API kwarg name kept for back-compat
    moderator_spec: str = "openai:gpt-4o"


# v0.59 ：起号目标分类列表（前端 GoalPicker 用这个）
@app.get("/api/strategy/goals")
def strategy_goals_list() -> list[dict[str, Any]]:
    from ..strategy.goals import list_goals_as_dicts
    return list_goals_as_dicts()


@app.post("/api/strategy/autofill")
async def strategy_autofill(req: StrategyAutofillRequest) -> dict[str, Any]:
    """AI multi-agent debate to produce a starter brief from the DB.

    Returns a prefilled AccountInput + per-field rationale + 共识/分歧 notes,
    so the frontend can show the form with intelligent defaults that the user
    can then edit (not the other way around).
    """
    from ..strategy import autofill as _af
    try:
        return await _af.autofill(
            personal_hint=req.personal_hint,
            constraints_hint=req.constraints_hint,
            deep=req.deep,
            claude_spec=req.claude_spec,
            openai_spec=req.openai_spec,
            moderator_spec=req.moderator_spec,
        )
    except RuntimeError as e:
        raise HTTPException(409, str(e))


@app.post("/api/strategy/propose")
async def strategy_propose(req: StrategyInput) -> dict[str, Any]:
    plat = req.platform
    if not plat:
        meta = library.get_meta(library.active_lib_id())
        plat = meta.platform if meta else "xiaohongshu"
    from ..brief import ALL_ANGLES as _ALL_ANGLES
    _exp_angles = [a for a in (req.expected_angles or []) if a in _ALL_ANGLES]
    inp = AccountInput(
        positioning=req.positioning,
        target_audience=req.target_audience,
        cycle_weeks=req.cycle_weeks,
        posts_per_week=req.posts_per_week,
        personal_strengths=req.personal_strengths,
        constraints=req.constraints,
        platform=library.normalise_platform(plat),
        expected_angles=_exp_angles,
        cycle_start_date=req.cycle_start_date,
        goal_type=req.goal_type,
    )
    try:
        result = await strategy_pipeline.propose(inp, positioner_spec=req.positioner_spec)
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    return result


@app.post("/api/strategy/propose/stream")
async def strategy_propose_stream(req: StrategyInput):
    """Same as /api/strategy/propose but streams the LLM's output via SSE.
    First direction visible in ~5-10s instead of blocking the whole 30-50s.
    See pipeline.propose_stream for event format."""
    plat = req.platform
    if not plat:
        meta = library.get_meta(library.active_lib_id())
        plat = meta.platform if meta else "xiaohongshu"
    from ..brief import ALL_ANGLES as _ALL_ANGLES
    _exp_angles = [a for a in (req.expected_angles or []) if a in _ALL_ANGLES]
    inp = AccountInput(
        positioning=req.positioning,
        target_audience=req.target_audience,
        cycle_weeks=req.cycle_weeks,
        posts_per_week=req.posts_per_week,
        personal_strengths=req.personal_strengths,
        constraints=req.constraints,
        platform=library.normalise_platform(plat),
        expected_angles=_exp_angles,
        cycle_start_date=req.cycle_start_date,
        goal_type=req.goal_type,
    )
    return StreamingResponse(
        strategy_pipeline.propose_stream(inp, positioner_spec=req.positioner_spec),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


@app.post("/api/strategy/{pack_id}/expand")
async def strategy_expand(pack_id: str, req: StrategyExpandRequest) -> dict[str, Any]:
    try:
        return await strategy_pipeline.expand(
            pack_id, req.chosen_direction_idx,
            topicgen_spec=req.topicgen_spec,
            scheduler_spec=req.scheduler_spec,
            resourcer_spec=req.resourcer_spec,
            drafter_spec=req.drafter_spec,
            restart=req.restart,
            chosen_idxs=req.chosen_direction_idxs or None,
        )
    except LookupError as e:
        raise HTTPException(404, str(e))
    except IndexError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        # Idempotency conflict — see expand() docstring. Frontend should
        # interpret this as "go poll, don't retry POST".
        if "expand 已经在跑" in str(e):
            raise HTTPException(409, str(e))
        raise


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
    project.ensure_bootstrap()
    pid = project.active_project_id()
    with db.connect(read_only=True) as con:
        row = con.execute(
            "SELECT * FROM studio_strategies WHERE pack_id = ?"
            " AND (project_id = ? OR project_id IS NULL)",
            (pack_id, pid),
        ).fetchone()
    if not row:
        raise HTTPException(404, "strategy pack not found in current project")
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
    project.ensure_bootstrap()
    pid = project.active_project_id()
    with db.connect() as con:
        cur = con.execute(
            "DELETE FROM studio_strategies WHERE pack_id = ?"
            " AND (project_id = ? OR project_id IS NULL)",
            (pack_id, pid),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "not found in current project")
    return {"deleted": pack_id}


# ---------------- pause / cancel running jobs --------------------

@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, Any]:
    """Cooperatively cancel a running pipeline. The pipeline detects the
    cancel flag at the next stage boundary, saves partial state, and the
    in-flight LLM call (if any) is aborted via the underlying asyncio.Task
    cancellation. Subsequent calls with the same primary key (pack_id /
    draft_id / etc.) resume from where it left off."""
    return job_registry.cancel(job_id)


@app.get("/api/jobs")
def list_running_jobs() -> list[dict[str, Any]]:
    return job_registry.list_jobs()


# ---------------- retrospective (复盘) endpoints --------------------

class PublishMarkRequest(BaseModel):
    published_title: str | None = None
    published_body: str | None = None
    published_url: str | None = None
    published_notes: str | None = None


@app.post("/api/drafts/{draft_id}/publish")
def mark_draft_published(draft_id: str, req: PublishMarkRequest) -> dict[str, Any]:
    try:
        return retro.mark_published(
            draft_id,
            published_title=req.published_title,
            published_body=req.published_body,
            published_url=req.published_url,
            published_notes=req.published_notes,
        )
    except LookupError as e:
        raise HTTPException(404, str(e))


@app.delete("/api/drafts/{draft_id}/publish")
def unmark_draft_published(draft_id: str) -> dict[str, Any]:
    try:
        return retro.unmark_published(draft_id)
    except LookupError as e:
        raise HTTPException(404, str(e))


class DraftPerformanceRequest(BaseModel):
    likes: int | None = None
    comments: int | None = None
    saves: int | None = None
    shares: int | None = None
    views: int | None = None
    follower_delta: int | None = None
    notes: str = ""


@app.post("/api/drafts/{draft_id}/performance")
def record_draft_performance(draft_id: str, req: DraftPerformanceRequest) -> dict[str, Any]:
    try:
        return retro.record_performance(
            draft_id,
            likes=req.likes, comments=req.comments, saves=req.saves,
            shares=req.shares, views=req.views,
            follower_delta=req.follower_delta, notes=req.notes,
        )
    except LookupError as e:
        raise HTTPException(404, str(e))


@app.get("/api/retrospective/published")
def list_published_drafts(library_id: str | None = None) -> list[dict[str, Any]]:
    return retro.list_published_with_perf(library_id=library_id)


class RetroAnalyzeRequest(BaseModel):
    draft_ids: list[str] | None = None
    library_id: str | None = None
    model_spec: str = "openai:gpt-4o"


@app.post("/api/retrospective/analyze")
async def retrospective_analyze(req: RetroAnalyzeRequest) -> dict[str, Any]:
    try:
        return await retro.analyze(
            draft_ids=req.draft_ids,
            library_id=req.library_id,
            model_spec=req.model_spec,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/retrospective/reviews")
def list_retrospective_reviews(library_id: str | None = None) -> list[dict[str, Any]]:
    return retro.list_reviews(library_id=library_id)


@app.get("/api/retrospective/reviews/{review_id}")
def get_retrospective_review(review_id: str) -> dict[str, Any]:
    r = retro.get_review(review_id)
    if not r:
        raise HTTPException(404, "review not found")
    return r


# ---------------- strategy iteration loop -------------------------------

from ..strategy import iterate as _iterate  # noqa: E402


class StrategyPerformancePayload(BaseModel):
    raw_notes: str = ""
    per_slot: list[dict[str, Any]] = Field(default_factory=list)
    overall: dict[str, Any] = Field(default_factory=dict)


@app.post("/api/strategy/{pack_id}/performance")
def save_strategy_performance(pack_id: str, req: StrategyPerformancePayload) -> dict[str, Any]:
    return _iterate.save_performance(
        pack_id=pack_id,
        raw_notes=req.raw_notes,
        per_slot=req.per_slot,
        overall=req.overall,
    )


@app.get("/api/strategy/{pack_id}/performance")
def list_strategy_performance(pack_id: str) -> list[dict[str, Any]]:
    return _iterate.list_performance(pack_id)


class StrategyIterateRequest(BaseModel):
    feedback_id: str
    iterator_spec: str = "openai:gpt-4o"


@app.post("/api/strategy/{pack_id}/iterate")
async def iterate_strategy_api(pack_id: str, req: StrategyIterateRequest) -> dict[str, Any]:
    try:
        return await _iterate.iterate_strategy(
            pack_id, req.feedback_id,
            iterator_spec=req.iterator_spec,
        )
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


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


# ============================================================================
# v0.53 — new endpoints: compliance / tracking / variants / provenance /
# feedback proposals. All gated behind "needs local backend" because they
# touch the DB.
# ============================================================================

# ---------------- compliance (hard redline gate) ------------------------

from .. import compliance as _compliance  # noqa: E402


class ComplianceCheckRequest(BaseModel):
    title: str = ""
    body: str = ""
    tags: list[str] = Field(default_factory=list)
    cover_prompt: str = ""


@app.post("/api/compliance/check")
def compliance_check(req: ComplianceCheckRequest) -> dict[str, Any]:
    return _compliance.check_candidate(req.model_dump())


class ComplianceRewriteRequest(BaseModel):
    text: str
    where: str = "body"  # 'title' | 'body' — controls which redlines apply


@app.post("/api/compliance/rewrite")
def compliance_rewrite(req: ComplianceRewriteRequest) -> dict[str, Any]:
    hits = _compliance.check_text(req.text, where=req.where)
    rewritten = _compliance.rewrite_safe(req.text, hits)
    return {
        "original": req.text,
        "rewritten": rewritten,
        "hits": [h.to_dict() for h in hits],
        "changed": rewritten != req.text,
    }


@app.get("/api/compliance/rules")
def compliance_rules() -> list[dict[str, Any]]:
    return _compliance.list_redlines()


@app.get("/api/drafts/{draft_id}/compliance")
def get_draft_compliance(draft_id: str) -> dict[str, Any]:
    """Latest compliance check per candidate, plus an overall draft-level
    severity for the chosen final."""
    with db.connect(read_only=True) as con:
        rows = list(con.execute(
            "SELECT check_id, candidate_id, checked_at, severity, hits_json,"
            " rewritten_body, rewritten_title"
            " FROM studio_compliance_checks WHERE draft_id = ?"
            " ORDER BY checked_at DESC",
            (draft_id,),
        ))
        d = con.execute(
            "SELECT final_candidate_id FROM studio_drafts WHERE draft_id = ?",
            (draft_id,),
        ).fetchone()
    final_cid = d["final_candidate_id"] if d else None
    by_cand: dict[str, dict] = {}
    for r in rows:
        cid = r["candidate_id"]
        if cid in by_cand:
            continue  # keep latest only
        item = dict(r)
        try: item["hits"] = json.loads(item.pop("hits_json") or "[]")
        except Exception: item["hits"] = []
        by_cand[cid] = item
    final_severity = (by_cand.get(final_cid, {}) or {}).get("severity", "pass") if final_cid else "pass"
    return {
        "draft_id": draft_id,
        "final_candidate_id": final_cid,
        "final_severity": final_severity,
        "by_candidate": by_cand,
    }


# ---------------- tracking (URL paste → reingest) -----------------------

from .. import tracking as _tracking  # noqa: E402


class TrackingRefreshRequest(BaseModel):
    url: str | None = None  # if omitted, uses draft.published_url


@app.post("/api/drafts/{draft_id}/refresh-from-url")
def tracking_refresh(draft_id: str, req: TrackingRefreshRequest) -> dict[str, Any]:
    try:
        return _tracking.refresh_draft(draft_id, force_url=req.url)
    except LookupError as e:
        raise HTTPException(404, str(e))


@app.get("/api/drafts/{draft_id}/fetches")
def tracking_list(draft_id: str, limit: int = 20) -> list[dict[str, Any]]:
    return _tracking.list_fetches(draft_id, limit=limit)


@app.get("/api/tracking/status")
def tracking_status() -> dict[str, Any]:
    """Tell the frontend whether auto-refresh is supported in this install."""
    return {
        "crawler_available": _tracking.crawler_available(),
        "hint": (
            "已就绪：粘贴小红书 URL 即可一键刷新数据。"
            if _tracking.crawler_available()
            else "未安装 curl_cffi。pip install curl_cffi 后即可启用自动刷新。"
        ),
    }


# ---------------- variants (one-click fan-out) --------------------------

from ..agents import variant as _variant  # noqa: E402


class VariantSpawnRequest(BaseModel):
    angles: list[str]


@app.post("/api/drafts/{draft_id}/variants")
async def variants_spawn(draft_id: str, req: VariantSpawnRequest) -> dict[str, Any]:
    try:
        return await _variant.spawn_variants(draft_id, req.angles)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/drafts/{draft_id}/variants")
def variants_list(draft_id: str) -> list[dict[str, Any]]:
    return _variant.list_variants(draft_id)


# ---------------- provenance (RAG refs for this draft) ------------------

@app.get("/api/drafts/{draft_id}/rag")
def draft_rag(draft_id: str) -> dict[str, Any]:
    """Pull the persisted Researcher refs/comments/hooks for a draft.
    Old drafts (before v0.53) have no rag_json — return {refs: [], ...}."""
    with db.connect(read_only=True) as con:
        row = con.execute(
            "SELECT rag_json FROM studio_drafts WHERE draft_id = ?",
            (draft_id,),
        ).fetchone()
    if not row:
        raise HTTPException(404, "draft not found")
    payload = {}
    try:
        payload = json.loads(row["rag_json"] or "{}")
    except Exception:
        payload = {}
    return {
        "draft_id": draft_id,
        "refs": payload.get("refs", []),
        "comments": payload.get("comments", []),
        "hooks": payload.get("hooks", []),
        "has_data": bool(payload),
    }


# ---------------- feedback aggregate (item 7) ---------------------------

from .. import feedback as _feedback  # noqa: E402


@app.get("/api/feedback/rollup")
def feedback_rollup(project_id: str | None = None) -> dict[str, Any]:
    return _feedback.rollup_for_project(project_id)


@app.get("/api/feedback/rollup/pack/{pack_id}")
def feedback_rollup_pack(pack_id: str) -> dict[str, Any]:
    return _feedback.rollup_for_pack(pack_id)


# ---------------- feedback proposals (item 8 — prompt versioning) -------

class ProposeFromReviewRequest(BaseModel):
    review_id: str
    generator_name: str = "title_body_gen"
    proposer_spec: str = "openai:gpt-4o"


@app.post("/api/feedback/propose-from-review")
async def feedback_propose(req: ProposeFromReviewRequest) -> dict[str, Any]:
    try:
        return await _feedback.propose_from_retrospective(
            req.review_id,
            generator_name=req.generator_name,
            proposer_spec=req.proposer_spec,
        )
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/feedback/proposals")
def feedback_list_proposals(status: str | None = None,
                            limit: int = 30) -> list[dict[str, Any]]:
    return _feedback.list_proposals(status=status, limit=limit)


@app.get("/api/feedback/proposals/{proposal_id}")
def feedback_get_proposal(proposal_id: str) -> dict[str, Any]:
    p = _feedback.get_proposal(proposal_id)
    if not p:
        raise HTTPException(404, "proposal not found")
    return p


class DecideProposalRequest(BaseModel):
    notes: str = ""


@app.post("/api/feedback/proposals/{proposal_id}/approve")
def feedback_approve(proposal_id: str, req: DecideProposalRequest) -> dict[str, Any]:
    try:
        return _feedback.approve_proposal(proposal_id, notes=req.notes)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/feedback/proposals/{proposal_id}/reject")
def feedback_reject(proposal_id: str, req: DecideProposalRequest) -> dict[str, Any]:
    try:
        return _feedback.reject_proposal(proposal_id, notes=req.notes)
    except LookupError as e:
        raise HTTPException(404, str(e))


# ============================================================================
# v0.58 — Product Context: project-level brand bible the LLM reads every time
# ============================================================================

from .. import product_context as _pc  # noqa: E402


@app.get("/api/product-context")
def product_context_list(active_only: bool = False) -> list[dict[str, Any]]:
    return _pc.list_contexts(active_only=active_only)


@app.get("/api/product-context/{context_id}")
def product_context_get(context_id: str) -> dict[str, Any]:
    r = _pc.get_context(context_id)
    if not r:
        raise HTTPException(404, "product context not found")
    return r


class ProductContextCreate(BaseModel):
    name: str
    body_text: str


@app.post("/api/product-context")
def product_context_create(req: ProductContextCreate) -> dict[str, Any]:
    try:
        return _pc.create_context(name=req.name, body_text=req.body_text)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/product-context/upload")
async def product_context_upload(
    file: UploadFile = File(...),
    name: str = Form(""),
) -> dict[str, Any]:
    data = await file.read()
    try:
        return _pc.upload_file_bytes(
            filename=file.filename or "uploaded",
            data=data,
            name=name or None,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/product-context/{context_id}")
def product_context_delete(context_id: str) -> dict[str, Any]:
    try:
        return _pc.delete_context(context_id)
    except LookupError as e:
        raise HTTPException(404, str(e))


class ProductContextActiveRequest(BaseModel):
    active: bool


@app.post("/api/product-context/{context_id}/active")
def product_context_set_active(
    context_id: str, req: ProductContextActiveRequest,
) -> dict[str, Any]:
    try:
        return _pc.set_active(context_id, req.active)
    except LookupError as e:
        raise HTTPException(404, str(e))


# ============================================================================
# v0.54 — Static frontend serving for one-image cloud deploys.
#
# When the container is built via the repo's Dockerfile, the React app's
# `dist/` ends up at `<repo>/frontend/dist`. Mount it under `/` so a single
# port + domain hosts BOTH the API and the SPA (Render free tier / Fly.io
# free tier / etc. each give one process, not two).
#
# SPA fallback: any path that isn't /api/* and isn't a real file resolves to
# index.html so React-Router deep links (e.g. /drafts/abc) work after refresh.
# ============================================================================
_FRONTEND_DIST = config.REPO_ROOT / "frontend" / "dist"
_FRONTEND_INDEX = _FRONTEND_DIST / "index.html"

if _FRONTEND_INDEX.exists():
    # Static asset files (anything under /assets/*) — long-cache them.
    app.mount(
        "/assets",
        StaticFiles(directory=str(_FRONTEND_DIST / "assets")),
        name="frontend-assets",
    )

    @app.get("/")
    def _spa_root() -> FileResponse:
        return FileResponse(str(_FRONTEND_INDEX))

    # SPA deep-link fallback. Must come LAST in the file (FastAPI matches in
    # registration order; this catch-all needs to lose to every concrete
    # /api/* route declared above).
    @app.get("/{full_path:path}")
    def _spa_fallback(full_path: str):
        # Guard: don't ever serve HTML for a missing /api/* — return a real
        # 404 JSON so client code surfaces a sane error.
        if full_path.startswith("api/"):
            raise HTTPException(404, "no such API route")
        # Real static file inside dist (e.g. /favicon.ico)? Serve it.
        candidate = _FRONTEND_DIST / full_path
        if candidate.is_file():
            return FileResponse(str(candidate))
        # Anything else (React-Router path) → index.html so SPA handles it.
        return FileResponse(str(_FRONTEND_INDEX))

"""Export DNA + drafts + library list as static JSON for the React frontend.

The frontend on GitHub Pages reads these files directly when no backend is
running, giving a fully functional read-only demo experience. When a local
backend IS running, the frontend prefers live API responses.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from .. import config, db, library


def export_all(out_dir: Path) -> dict[str, str]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}

    # Active library + libraries list
    active = library.active_lib_id()
    libs = library.list_libraries()
    libs_payload = [
        {
            "lib_id": l.lib_id,
            "display_name": l.display_name,
            "uploaded_at": l.uploaded_at,
            "source": l.source,
            "notes_count": l.notes_count,
            "comments_count": l.comments_count,
            "size_bytes": l.size_bytes,
            "active": l.lib_id == active,
        }
        for l in libs
    ]
    _write(out_dir / "libraries.json", libs_payload)
    paths["libraries"] = "libraries.json"

    # DNA artifact list + latest payload
    with db.connect(read_only=True) as con:
        try:
            versions = list(con.execute(
                "SELECT version, created_at, summary FROM studio_dna_artifacts"
                " ORDER BY created_at DESC"
            ))
            latest = con.execute(
                "SELECT version, payload_json FROM studio_dna_artifacts"
                " ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        except Exception:
            versions, latest = [], None

    versions_payload = [
        {
            "version": v["version"],
            "created_at": v["created_at"],
            "summary": json.loads(v["summary"]) if v["summary"] else {},
        }
        for v in versions
    ]
    _write(out_dir / "dna_versions.json", versions_payload)
    paths["dna_versions"] = "dna_versions.json"

    if latest:
        payload = json.loads(latest["payload_json"])
        _write(out_dir / "dna_latest.json", payload)
        paths["dna_latest"] = "dna_latest.json"

    # Recent drafts list with brief
    with db.connect(read_only=True) as con:
        try:
            drafts = list(con.execute(
                "SELECT d.draft_id, d.generated_at, d.mode, d.library_id,"
                " d.final_candidate_id, d.brief_json,"
                " (SELECT title FROM studio_draft_candidates"
                "  WHERE candidate_id = d.final_candidate_id) AS final_title,"
                " (SELECT COUNT(*) FROM studio_draft_candidates"
                "  WHERE draft_id = d.draft_id) AS candidate_count"
                " FROM studio_drafts d ORDER BY d.generated_at DESC LIMIT 100"
            ))
        except Exception:
            drafts = []

    drafts_payload = []
    for r in drafts:
        d = dict(r)
        try:
            d["brief"] = json.loads(d.pop("brief_json"))
        except (json.JSONDecodeError, TypeError):
            d["brief"] = {}
        drafts_payload.append(d)
    _write(out_dir / "drafts.json", drafts_payload)
    paths["drafts"] = "drafts.json"

    # Per-draft detail (only latest 20 to keep size sane)
    drafts_dir = out_dir / "drafts"
    drafts_dir.mkdir(exist_ok=True)
    with db.connect(read_only=True) as con:
        for r in drafts[:20]:
            d_id = r["draft_id"]
            full = _build_draft_payload(con, d_id)
            _write(drafts_dir / f"{d_id}.json", full)

    # Manifest: meta-info the frontend uses to decide what's available.
    _write(out_dir / "manifest.json", {
        "exported_at": _now(),
        "active_library": active,
        "libraries": len(libs_payload),
        "dna_versions": len(versions_payload),
        "drafts": len(drafts_payload),
    })
    paths["manifest"] = "manifest.json"

    return {k: str((out_dir / v).resolve()) for k, v in paths.items()}


def _build_draft_payload(con, draft_id: str) -> dict:
    d = con.execute(
        "SELECT * FROM studio_drafts WHERE draft_id = ?", (draft_id,)
    ).fetchone()
    if not d:
        return {}
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
    crit_by_cand: dict[str, list[dict]] = {}
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


def _write(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _now() -> int:
    import time
    return int(time.time())

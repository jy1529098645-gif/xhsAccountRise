"""Library manager: each library is a self-contained xhs.db on disk.

Layout:
    data/libraries/<lib_id>/xhs.db
    data/libraries/<lib_id>/meta.json   # display_name, uploaded_at, source, stats cache
    data/active_library.txt             # one-line pointer to lib_id

Why one-file-per-library: SQLite gives us free isolation. Switching a library
is just a pointer flip; nothing else in the app needs to be aware.

All studio.* modules import `current_db_path()` from here instead of pinning a
constant — that's the single source of truth.
"""
from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from . import config

LIBRARIES_DIR = config.DATA_DIR / "libraries"
ACTIVE_POINTER = config.DATA_DIR / "active_library.txt"

LIBRARIES_DIR.mkdir(parents=True, exist_ok=True)


_ID_OK = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,32}$")
_NAME_SANITIZER = re.compile(r"[^\w一-鿿\-]+", re.UNICODE)


@dataclass
class LibraryMeta:
    lib_id: str
    display_name: str
    uploaded_at: int
    source: str = "local"          # 'local' | 'upload' | 'crawler'
    notes_count: int = 0
    comments_count: int = 0
    size_bytes: int = 0
    description: str = ""
    platform: str = "xiaohongshu"  # xiaohongshu | douyin | kuaishou | bilibili
                                   # | youtube | reddit | x | other
    project_id: str = "default"    # which project this library belongs to


SUPPORTED_PLATFORMS: tuple[str, ...] = (
    "xiaohongshu", "douyin", "kuaishou", "bilibili",
    "youtube", "reddit", "x", "other",
)
PLATFORM_LABELS: dict[str, str] = {
    "xiaohongshu": "小红书",
    "douyin": "抖音",
    "kuaishou": "快手",
    "bilibili": "B站",
    "youtube": "YouTube",
    "reddit": "Reddit",
    "x": "X / Twitter",
    "other": "其他",
}


def normalise_platform(p: str | None) -> str:
    if not p:
        return "xiaohongshu"
    p = p.strip().lower()
    if p == "auto":
        return "auto"  # caller must detect later
    aliases = {
        "xhs": "xiaohongshu", "rednote": "xiaohongshu", "小红书": "xiaohongshu",
        "tiktok": "douyin", "抖音": "douyin",
        "快手": "kuaishou", "kwai": "kuaishou",
        "b站": "bilibili", "哔哩哔哩": "bilibili", "bili": "bilibili",
        "yt": "youtube", "油管": "youtube", "youtube": "youtube",
        "reddit": "reddit", "r": "reddit",
        "twitter": "x", "twt": "x",
    }
    return aliases.get(p, p if p in SUPPORTED_PLATFORMS else "other")


def validate_schema_blob(blob: bytes) -> dict[str, Any]:
    """Minimal sanity check on an uploaded .db.

    The user said: 'don't enforce anything, just submit to AI and let it
    figure things out'. So this function does exactly one thing: confirm
    the bytes are a valid SQLite header that the engine can open. Anything
    else — table names, column names, what the data is — is the AI's job.

    Returns {ok, fatal, tables (just for diagnostics), notes_count,
    comments_count}. No warnings, no suggestions, no judgement.
    """
    if len(blob) < 100 or blob[:16] != b"SQLite format 3\x00":
        return {"ok": False, "fatal": "上传的文件不是 SQLite 数据库",
                "tables": [], "notes_count": 0, "comments_count": 0}
    import sqlite3, tempfile, os
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp.write(blob)
        tmp_path = tmp.name
    out: dict[str, Any] = {
        "ok": True, "fatal": None, "tables": [],
        "notes_count": 0, "comments_count": 0,
    }
    try:
        con = sqlite3.connect(f"file:{tmp_path}?mode=ro", uri=True)
        cur = con.cursor()
        try:
            tables = sorted({r[0] for r in cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
                " AND name NOT LIKE 'sqlite_%'"
            )})
            out["tables"] = tables
        except sqlite3.OperationalError as e:
            out["fatal"] = f"SQLite 文件读取失败：{e}"
            out["ok"] = False
            return out
        # Lightweight diagnostics — used only for status display, not gating.
        if "notes" in tables:
            try:
                out["notes_count"] = cur.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
            except sqlite3.OperationalError:
                pass
        if "comments" in tables:
            try:
                out["comments_count"] = cur.execute("SELECT COUNT(*) FROM comments").fetchone()[0]
            except sqlite3.OperationalError:
                pass
        con.close()
    except sqlite3.Error as e:
        out["fatal"] = f"无法打开 SQLite：{e}"
        out["ok"] = False
    finally:
        try: os.unlink(tmp_path)
        except OSError: pass
    return out


# ---- Platform auto-detection from a SQLite blob -----------------------

# Per-platform schema fingerprints. Each entry maps a platform id to a set of
# distinguishing column or table substrings. Heuristic: count matches across
# all tables/columns; whoever wins by margin gets picked. If nothing matches
# strongly, return 'other'.
_FINGERPRINTS: dict[str, tuple[str, ...]] = {
    "xiaohongshu": (
        "xsec_token", "interaction_count", "collected_count",
        "discover_queue", "note_id", "liked_count",
    ),
    "douyin": (
        "aweme_id", "video_play_addr", "douyin", "tiktok",
        "share_url", "music_id",
    ),
    "kuaishou": (
        "photo_id", "kuaishou", "ksuid", "play_url",
    ),
    "bilibili": (
        "bvid", "aid", "cid", "bilibili", "danmaku", "up_mid",
    ),
    "youtube": (
        "video_id", "channel_id", "view_count", "watch_url",
        "youtube",
    ),
    "reddit": (
        "subreddit", "permalink", "submission_id", "praw",
    ),
    "x": (
        "tweet_id", "screen_name", "retweet_count", "favorite_count",
        "twitter",
    ),
}


def detect_platform_from_blob(blob: bytes) -> tuple[str, dict[str, int]]:
    """Sniff platform from a SQLite payload. Returns (best_match, scores).
    `best_match` is in SUPPORTED_PLATFORMS or 'other' if no clear winner.
    """
    if len(blob) < 100 or blob[:16] != b"SQLite format 3\x00":
        return "other", {}
    import sqlite3
    import tempfile
    scores = {p: 0 for p in _FINGERPRINTS}
    try:
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            tmp.write(blob)
            tmp_path = tmp.name
        try:
            con = sqlite3.connect(f"file:{tmp_path}?mode=ro", uri=True)
            cur = con.cursor()
            tables = [r[0] for r in cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
                " AND name NOT LIKE 'sqlite_%'"
            )]
            cols: list[str] = []
            for t in tables:
                try:
                    for col in cur.execute(f"PRAGMA table_info('{t}')"):
                        cols.append(str(col[1]).lower())
                except sqlite3.OperationalError:
                    continue
            haystack = " ".join(tables).lower() + " " + " ".join(cols)
            for plat, fps in _FINGERPRINTS.items():
                for fp in fps:
                    if fp.lower() in haystack:
                        scores[plat] += 1
            con.close()
        finally:
            import os
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    except sqlite3.Error:
        return "other", scores
    # Pick the platform with the highest score, requiring at least 2 hits and
    # a clear margin over runner-up.
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    if not ranked or ranked[0][1] < 2:
        return "other", scores
    if len(ranked) > 1 and ranked[0][1] - ranked[1][1] < 1:
        return "other", scores
    return ranked[0][0], scores


def _slug(text: str) -> str:
    s = _NAME_SANITIZER.sub("-", text.lower()).strip("-")[:24]
    return s or "lib"


def list_libraries(project_id: str | None = "__active__") -> list[LibraryMeta]:
    """List libraries. Pass project_id=None to get all, or a specific id."""
    out: list[LibraryMeta] = []
    if not LIBRARIES_DIR.exists():
        return out
    # Resolve "active" pointer lazily to avoid import cycle at module load.
    if project_id == "__active__":
        from . import project as _project
        project_id = _project.active_project_id()
    for child in sorted(LIBRARIES_DIR.iterdir()):
        if not child.is_dir():
            continue
        meta_path = child / "meta.json"
        if not meta_path.exists():
            continue
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            # Backwards-compat: older meta.json may lack `platform` / `project_id`.
            data.setdefault("platform", "xiaohongshu")
            data.setdefault("project_id", "default")
            meta = LibraryMeta(**data)
        except (json.JSONDecodeError, TypeError):
            continue
        if project_id is not None and meta.project_id != project_id:
            continue
        out.append(meta)
    return out


def list_all_libraries() -> list[LibraryMeta]:
    return list_libraries(project_id=None)


# v0.59.4: each library is a self-contained .db, so studio_composer_packs +
# studio_drafts + all other studio_* tables only exist inside the active
# library's .db file. If user runs propose in library A then switches active
# library to B, expand() looking up the pack_id finds nothing → 「strategy
# pack not found: 42bd...」 error。
#
# This helper scans every known library's .db file for a given pack_id so
# the strategy endpoints can auto-recover by switching active library.
def find_pack_lib_id(pack_id: str) -> str | None:
    """Scan every library's xhs.db for a studio_composer_packs row matching
    pack_id. Returns the lib_id that has it, or None if absent.

    Quick (1-N small SQLite SELECTs); cheap to call even with 10+ libs.
    """
    import sqlite3
    if not LIBRARIES_DIR.exists():
        return None
    for entry in sorted(LIBRARIES_DIR.iterdir()):
        if not entry.is_dir():
            continue
        db_path = entry / "xhs.db"
        if not db_path.exists():
            continue
        try:
            uri = f"file:{db_path.as_posix()}?mode=ro"
            con = sqlite3.connect(uri, uri=True)
            try:
                cur = con.execute(
                    "SELECT 1 FROM studio_composer_packs WHERE pack_id = ? LIMIT 1",
                    (pack_id,),
                )
                if cur.fetchone():
                    return entry.name
            finally:
                con.close()
        except Exception:
            # studio_composer_packs table might not exist yet in this lib
            # (pre-v0.40 lib). Skip silently.
            continue
    return None


def get_meta(lib_id: str) -> LibraryMeta | None:
    p = LIBRARIES_DIR / lib_id / "meta.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        data.setdefault("platform", "xiaohongshu")
        data.setdefault("project_id", "default")
        return LibraryMeta(**data)
    except (json.JSONDecodeError, TypeError):
        return None


def set_project(lib_id: str, project_id: str) -> LibraryMeta:
    meta = get_meta(lib_id)
    if meta is None:
        raise ValueError(f"library not found: {lib_id}")
    meta.project_id = project_id
    _write_meta(lib_id, meta)
    return meta


def active_lib_id(default: str = "default") -> str:
    """The active library *within the current project*.

    Resolution order:
      1. Per-project active pointer (validated: lib must belong to this project)
      2. Global pointer (validated similarly)
      3. First library in current project
      4. `default`
    """
    pid = _current_project_id()
    proj_dir = config.DATA_DIR / "projects" / pid
    proj_pointer = proj_dir / "active_library.txt"
    if proj_pointer.exists():
        text = proj_pointer.read_text(encoding="utf-8").strip()
        if text and _id_exists(text):
            meta = get_meta(text)
            if meta and meta.project_id == pid:
                return text
            # Stale pointer (lib moved to another project or deleted) — fall through
    if ACTIVE_POINTER.exists():
        text = ACTIVE_POINTER.read_text(encoding="utf-8").strip()
        if text and _id_exists(text):
            meta = get_meta(text)
            if meta and meta.project_id == pid:
                proj_dir.mkdir(parents=True, exist_ok=True)
                proj_pointer.write_text(text, encoding="utf-8")
                return text
    libs_in_project = list_libraries(project_id=pid)
    if libs_in_project:
        return libs_in_project[0].lib_id
    return default


def set_active(lib_id: str) -> None:
    if not _id_exists(lib_id):
        raise ValueError(f"library not found: {lib_id}")
    pid = _current_project_id()
    proj_dir = config.DATA_DIR / "projects" / pid
    proj_dir.mkdir(parents=True, exist_ok=True)
    (proj_dir / "active_library.txt").write_text(lib_id, encoding="utf-8")
    # Keep legacy global pointer in sync for backwards-compat.
    ACTIVE_POINTER.write_text(lib_id, encoding="utf-8")


def current_db_path() -> Path:
    lib_id = active_lib_id()
    return LIBRARIES_DIR / lib_id / "xhs.db"


def _id_exists(lib_id: str) -> bool:
    return (LIBRARIES_DIR / lib_id / "xhs.db").exists()


def _write_meta(lib_id: str, meta: LibraryMeta) -> None:
    p = LIBRARIES_DIR / lib_id / "meta.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(asdict(meta), ensure_ascii=False, indent=2),
                 encoding="utf-8")


def _alloc_id(hint: str | None) -> str:
    base = _slug(hint or "lib")
    if not _id_exists(base) and not (LIBRARIES_DIR / base).exists():
        return base
    return f"{base}-{uuid.uuid4().hex[:6]}"


def _resolve_platform(platform: str, blob: bytes | None = None) -> str:
    """Normalise + auto-detect. 'auto' triggers sniffing if blob provided."""
    p = normalise_platform(platform)
    if p == "auto":
        if blob:
            detected, _ = detect_platform_from_blob(blob)
            return detected if detected != "other" else "xiaohongshu"
        return "xiaohongshu"
    return p


def _current_project_id() -> str:
    from . import project as _project
    return _project.active_project_id()


def register_existing(db_path: Path, display_name: str | None = None,
                      lib_id: str | None = None,
                      source: str = "local",
                      platform: str = "xiaohongshu",
                      project_id: str | None = None) -> LibraryMeta:
    """Register a .db that's already on disk by copying it under
    data/libraries/{lib_id}/. Returns the canonical metadata."""
    src = Path(db_path)
    if not src.exists():
        raise FileNotFoundError(src)
    dn = display_name or src.stem
    if lib_id is None:
        lib_id = _alloc_id(dn)
    elif not _ID_OK.match(lib_id):
        raise ValueError(f"invalid lib_id: {lib_id!r}")
    dest_dir = LIBRARIES_DIR / lib_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_db = dest_dir / "xhs.db"
    shutil.copy2(src, dest_db)
    blob = dest_db.read_bytes() if platform == "auto" else None
    meta = _ingest_stats(
        LibraryMeta(
            lib_id=lib_id,
            display_name=dn,
            uploaded_at=int(time.time()),
            source=source,
            size_bytes=dest_db.stat().st_size,
            platform=_resolve_platform(platform, blob),
            project_id=project_id or _current_project_id(),
        )
    )
    _write_meta(lib_id, meta)
    return meta


def adopt_bytes(blob: bytes, display_name: str,
                lib_id: str | None = None,
                platform: str = "xiaohongshu",
                project_id: str | None = None) -> LibraryMeta:
    """Accept a raw .db payload (e.g. uploaded from the frontend) and persist."""
    if lib_id is None:
        lib_id = _alloc_id(display_name)
    elif not _ID_OK.match(lib_id):
        raise ValueError(f"invalid lib_id: {lib_id!r}")
    resolved_platform = _resolve_platform(platform, blob)
    dest_dir = LIBRARIES_DIR / lib_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_db = dest_dir / "xhs.db"
    dest_db.write_bytes(blob)
    meta = _ingest_stats(
        LibraryMeta(
            lib_id=lib_id,
            display_name=display_name,
            uploaded_at=int(time.time()),
            source="upload",
            size_bytes=dest_db.stat().st_size,
            platform=resolved_platform,
            project_id=project_id or _current_project_id(),
        )
    )
    _write_meta(lib_id, meta)
    return meta


def set_platform(lib_id: str, platform: str) -> LibraryMeta:
    meta = get_meta(lib_id)
    if meta is None:
        raise ValueError(f"library not found: {lib_id}")
    meta.platform = normalise_platform(platform)
    _write_meta(lib_id, meta)
    return meta


def _ingest_stats(meta: LibraryMeta) -> LibraryMeta:
    """Open the .db read-only and fill in note/comment counts."""
    import sqlite3
    db_path = LIBRARIES_DIR / meta.lib_id / "xhs.db"
    try:
        con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        meta.notes_count = con.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        try:
            meta.comments_count = con.execute(
                "SELECT COUNT(*) FROM comments"
            ).fetchone()[0]
        except sqlite3.OperationalError:
            meta.comments_count = 0
        con.close()
    except sqlite3.Error:
        # Library may not be a valid xhs DB; still register so user can delete.
        pass
    return meta


def delete(lib_id: str) -> None:
    if lib_id == active_lib_id():
        raise RuntimeError("cannot delete active library; switch first")
    target = LIBRARIES_DIR / lib_id
    if target.exists():
        shutil.rmtree(target)


def ensure_bootstrap() -> None:
    """Boot-time housekeeping.

    Fresh installs start *empty* — the user uploads their first .db from
    the Reports page. We only:
      - Set the active pointer if any libraries already exist (e.g. after
        an upload mid-session).
      - Leave existing libraries alone.

    We deliberately do NOT auto-adopt a legacy data/xhs.db anymore: hiding
    a 100 MB demo file from the user is more confusing than helpful, and
    different deployments shouldn't ship the same seed corpus.
    """
    libs = list_all_libraries()
    if libs and not ACTIVE_POINTER.exists():
        ACTIVE_POINTER.write_text(libs[0].lib_id, encoding="utf-8")


# Run bootstrap on import. Cheap (one file check); safe to be a no-op.
ensure_bootstrap()

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


def list_libraries() -> list[LibraryMeta]:
    out: list[LibraryMeta] = []
    if not LIBRARIES_DIR.exists():
        return out
    for child in sorted(LIBRARIES_DIR.iterdir()):
        if not child.is_dir():
            continue
        meta_path = child / "meta.json"
        if not meta_path.exists():
            continue
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            # Backwards-compat: older meta.json may lack `platform`.
            data.setdefault("platform", "xiaohongshu")
            out.append(LibraryMeta(**data))
        except (json.JSONDecodeError, TypeError):
            continue
    return out


def get_meta(lib_id: str) -> LibraryMeta | None:
    p = LIBRARIES_DIR / lib_id / "meta.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        data.setdefault("platform", "xiaohongshu")
        return LibraryMeta(**data)
    except (json.JSONDecodeError, TypeError):
        return None


def active_lib_id(default: str = "default") -> str:
    if ACTIVE_POINTER.exists():
        text = ACTIVE_POINTER.read_text(encoding="utf-8").strip()
        if text:
            return text
    return default


def set_active(lib_id: str) -> None:
    if not _id_exists(lib_id):
        raise ValueError(f"library not found: {lib_id}")
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


def register_existing(db_path: Path, display_name: str | None = None,
                      lib_id: str | None = None,
                      source: str = "local",
                      platform: str = "xiaohongshu") -> LibraryMeta:
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
        )
    )
    _write_meta(lib_id, meta)
    return meta


def adopt_bytes(blob: bytes, display_name: str,
                lib_id: str | None = None,
                platform: str = "xiaohongshu") -> LibraryMeta:
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
    """Boot-time: if no libraries exist but legacy data/xhs.db is there, adopt
    it as 'default' so existing users don't need to re-import."""
    if list_libraries():
        if not ACTIVE_POINTER.exists():
            ACTIVE_POINTER.write_text(
                list_libraries()[0].lib_id, encoding="utf-8"
            )
        return
    legacy = config.DATA_DIR / "xhs.db"
    if legacy.exists():
        register_existing(legacy, display_name="默认库 (default)", lib_id="default")
        ACTIVE_POINTER.write_text("default", encoding="utf-8")
        try:
            legacy.unlink()
            # also kill -wal/-shm if present
            for suffix in ("-wal", "-shm"):
                p = legacy.with_name(legacy.name + suffix)
                if p.exists():
                    p.unlink()
        except OSError:
            pass


# Run bootstrap on import. Cheap (one file check); safe to be a no-op.
ensure_bootstrap()

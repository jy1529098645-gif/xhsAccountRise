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
    meta = _ingest_stats(
        LibraryMeta(
            lib_id=lib_id,
            display_name=dn,
            uploaded_at=int(time.time()),
            source=source,
            size_bytes=dest_db.stat().st_size,
            platform=normalise_platform(platform),
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
            platform=normalise_platform(platform),
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

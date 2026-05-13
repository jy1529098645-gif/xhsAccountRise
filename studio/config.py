"""Centralised paths, defaults, and runtime config for the studio toolkit.

All other modules import from here so file layout can shift without churn.
"""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
# DB_PATH is library-scoped now; import studio.library.current_db_path() to
# get the active path. Kept as a hint for legacy callers but should not be
# used directly.
DB_PATH = DATA_DIR / "xhs.db"
EXPORTS_DIR = REPO_ROOT / "exports"
ANALYSIS_DIR = EXPORTS_DIR / "analysis"
DRAFTS_DIR = EXPORTS_DIR / "drafts"
MIGRATIONS_DIR = REPO_ROOT / "studio" / "migrations"
FRONTEND_DIR = REPO_ROOT / "frontend"
PUBLIC_DATA_DIR = FRONTEND_DIR / "public" / "data"

for _d in (DATA_DIR, EXPORTS_DIR, ANALYSIS_DIR, DRAFTS_DIR, PUBLIC_DATA_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# Substantive-body threshold: notes with body length below this are excluded
# from "real content" analyses (titles only, image-dump posts, etc.).
SUBSTANTIVE_BODY_MIN = 100

# Engagement cap on xhs: liked_count displays "100000" when actual >=100K. Any
# analysis that segments by likes should treat 100000 as a saturation marker,
# not the literal value.
LIKE_DISPLAY_CAP = 100_000

# LLM endpoints (W2+). Read at call time so .env edits don't need restart.
def anthropic_key() -> str | None:
    return os.environ.get("ANTHROPIC_API_KEY")


def deepseek_key() -> str | None:
    return os.environ.get("DEEPSEEK_API_KEY")


def openai_key() -> str | None:
    return os.environ.get("OPENAI_API_KEY")

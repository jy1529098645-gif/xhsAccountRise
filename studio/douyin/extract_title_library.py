"""One-time extraction script: PDF (1380-entry title library) → JSON.

Usage:
    python -m studio.douyin.extract_title_library

Idempotent — re-running just overwrites assets/title_library.json. The
result is committed to the repo so production deploys don't need pypdf
or the source PDF at runtime.

The PDF layout has columns 序号 / 类别 / 标题 with row data appearing per
page in the same table structure. We extract by walking pypdf's text output
line-by-line and matching a regex like `^\\d+\\s+<category>\\s+<title>$`.
Some titles wrap to multiple lines inside the cell — we glue continuation
lines onto the previous title until the next `^\\d+\\s+` row.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pypdf

# Paths
ASSETS = Path(__file__).parent / "assets"
PDF_PATH = ASSETS / "AcademiCats_抖音标题库.pdf"
OUT_PATH = ASSETS / "title_library.json"

# All 15 categories. Order matches the PDF's section sequence; we use it to
# disambiguate which category a row belongs to when the layout text loses
# the column boundary.
CATEGORIES = (
    "DDL急救夸张标题",
    "带梗夸张标题池",
    "留子精神状态梗标题",
    "Essay/Paper没写完标题",
    "文献检索标题",
    "引用格式标题",
    "Rubric/Assignment标题",
    "论文框架标题",
    "查重自查与修改标题",
    "AI工具与工作台标题",
    "毕业论文/开题标题",
    "评论互动标题",
    "反差故事标题",
    "黑色幽默标题",
    "效率对比标题",
)
CATEGORY_SET = set(CATEGORIES)

_HASHTAG_RE = re.compile(r"#([\w一-鿿]+)")
# row prefix: "<seq>\s+<category>\s+<title>" — category MUST come from
# CATEGORY_SET; "title" is everything after.
_ROW_RE = re.compile(
    r"^\s*(\d+)\s+([^\s][^\d]*?)\s{2,}(.+)$"
)


def _extract_hashtags(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for tag in _HASHTAG_RE.findall(text or ""):
        if tag not in seen:
            seen.add(tag); out.append(tag)
    return out


def _strip_hashtag_suffix(text: str) -> str:
    """Strip trailing ` #foo #bar` block so the stored title is the clean
    hook line; the hashtags live in their own field."""
    s = (text or "").strip()
    while True:
        new = re.sub(r"\s+#[\w一-鿿]+\s*$", "", s).rstrip()
        if new == s:
            break
        s = new
    return s


_SEQ_RE = re.compile(r"^\d+$")


def extract() -> list[dict[str, Any]]:
    """Walk pypdf's per-line output as a 3-line state machine.

    Pattern observed on every data row:
        line N    : "<seq>"               (just an integer)
        line N+1  : "<category>"          (one of the 15 known category labels)
        line N+2  : "<title>"             (the actual hook line, may contain #tags)

    Header lines like "DDL急救夸张标题（100条）" appear ALONE on their own
    line and are easy to skip — they include the "（n条）" suffix while the
    category column lines don't. We also tolerate weird whitespace and
    optional leading spaces from pypdf's column reconstruction.
    """
    if not PDF_PATH.exists():
        raise FileNotFoundError(f"source PDF not found: {PDF_PATH}")
    reader = pypdf.PdfReader(str(PDF_PATH))

    # Flatten all pages into one stream of stripped non-empty lines so a row
    # that spans a page boundary still parses correctly.
    stream: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        for raw in text.splitlines():
            s = raw.strip()
            if s:
                stream.append(s)

    rows: list[dict[str, Any]] = []
    seen_seqs: set[int] = set()
    i = 0
    while i < len(stream) - 2:
        a, b, c = stream[i], stream[i + 1], stream[i + 2]
        if _SEQ_RE.match(a) and b in CATEGORY_SET:
            seq = int(a)
            cat = b
            title = c.strip()
            if seq in seen_seqs or not title:
                i += 1; continue
            tags = _extract_hashtags(title)
            clean = _strip_hashtag_suffix(title)
            if not clean:
                i += 1; continue
            rows.append({
                "seq": seq,
                "category": cat,
                "title": clean,
                "hashtags": tags,
                "char_len": len(clean),
            })
            seen_seqs.add(seq)
            i += 3
        else:
            i += 1
    rows.sort(key=lambda r: r["seq"])
    return rows


def main() -> None:
    rows = extract()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    by_cat: dict[str, int] = {}
    for r in rows:
        by_cat[r["category"]] = by_cat.get(r["category"], 0) + 1
    print(f"Extracted {len(rows)} titles → {OUT_PATH}")
    for c in CATEGORIES:
        print(f"  · {c}: {by_cat.get(c, 0)}")


if __name__ == "__main__":
    main()

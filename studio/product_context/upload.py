"""File upload → text extraction → create_context. Reuses the same
extractor as studio.insight.external (PDF/DOCX/MD/TXT)."""
from __future__ import annotations

from typing import Any

from .crud import create_context


def upload_file_bytes(
    *,
    filename: str,
    data: bytes,
    name: str | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Extract text from uploaded file, save as a new product context.

    Reuses studio.insight.external.extract_text_from_bytes which knows about
    PDF (pypdf), DOCX (python-docx), MD/TXT (decode), and graceful warnings
    for missing extractors. Returns the created row + extract_warning if any.
    """
    from ..insight.external import extract_text_from_bytes

    text, fmt, warning = extract_text_from_bytes(filename, data)
    if not text or not text.strip():
        raise ValueError(
            f"无法从 {filename} 提取出任何文字。"
            f"{('原因: ' + warning) if warning else ''}"
        )

    display_name = name or filename.rsplit(".", 1)[0]
    row = create_context(
        name=display_name,
        body_text=text,
        source_format=fmt,
        source_filename=filename,
        project_id=project_id,
    )
    if warning:
        row["extract_warning"] = warning
    return row

"""Product context — user-uploaded description of "what your product actually is".

Distinct from external_reports（库分析）—— product_context 是关于 user's
own product/account/brand 的描述：核心功能 / 受众 / 声音 / 经典叙事 / 雷区 /
独有差异化。Strategy / Compose / Insight 三大流水线在生成时都会反复引用，
强制 LLM 不要 hallucinate generic AI-tool marketing copy。

CRUD：list / get / create / upload_file / delete / set_active.
Reading for prompts：context_block(project_id) — 返回拼好的 prompt 友好文本。
"""
from .crud import (
    list_contexts,
    get_context,
    create_context,
    delete_context,
    set_active,
    context_block,
)
from .upload import upload_file_bytes

__all__ = [
    "list_contexts", "get_context", "create_context", "delete_context",
    "set_active", "context_block", "upload_file_bytes",
]

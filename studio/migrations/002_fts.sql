-- FTS5 trigram indices for cheap RAG retrieval.
-- Trigram tokenizer needs queries >= 3 chars; this is fine for our use case
-- (topic words are typically 3-10 Chinese chars). Re-populate via `studio rag build`.

DROP TABLE IF EXISTS studio_fts_notes;
CREATE VIRTUAL TABLE studio_fts_notes USING fts5(
    note_id UNINDEXED,
    title,
    body,
    tokenize="trigram"
);

DROP TABLE IF EXISTS studio_fts_comments;
CREATE VIRTUAL TABLE studio_fts_comments USING fts5(
    comment_id UNINDEXED,
    note_id UNINDEXED,
    content,
    tokenize="trigram"
);

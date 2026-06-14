/**
 * v0.65 (P1) — Render body text with `[ref:<note_id>]` inline citation
 * markers replaced by clickable chips.
 *
 * The body drafter is now required to emit `[ref:xxxxxx]` after any specific
 * number / tool name / verbatim phrase that came from a RAG reference. The
 * renderer turns each marker into a small color-coded chip:
 *   - hover/click → opens the original post in a new tab if we have a URL
 *   - tooltip → 「来自 @author · 👍 likes · 标题摘要」
 *
 * `<...>` and `《...》` quoted spans (from real user comments) get a subtle
 * background highlight so the user can see "this came from a real comment".
 *
 * Usage:
 *   <GroundedBody text={candidate.body} refs={rag.refs ?? []} />
 */
import type { RagRef } from "../types";

interface Props {
  text: string;
  refs: RagRef[];
  className?: string;
  style?: React.CSSProperties;
}

const CHIP_STYLE: React.CSSProperties = {
  display: "inline-block",
  fontSize: 10.5,
  padding: "0 6px",
  margin: "0 2px",
  borderRadius: 4,
  background: "var(--primary-soft)",
  color: "var(--primary)",
  fontWeight: 600,
  verticalAlign: "0.05em",
  textDecoration: "none",
  cursor: "pointer",
  whiteSpace: "nowrap",
};

const MISSING_CHIP_STYLE: React.CSSProperties = {
  ...CHIP_STYLE,
  background: "#fff4dc",
  color: "#b06200",
  cursor: "help",
};

const QUOTE_STYLE: React.CSSProperties = {
  background: "#fff8e6",
  borderLeft: "2px solid #f6c265",
  padding: "0 4px",
  borderRadius: 2,
};

const REF_RE = /\[ref:([A-Za-z0-9_\-]+)\]/g;
const QUOTE_RE = /<([^<>\n]{2,60})>|《([^《》\n]{2,60})》/g;

interface Token {
  kind: "text" | "ref" | "quote";
  value: string;
  noteId?: string;
}

function tokenize(text: string): Token[] {
  const tokens: Token[] = [];
  // Two-pass: first ref markers, then quotes inside each text chunk.
  const pieces: { kind: "text" | "ref"; value: string; noteId?: string }[] = [];
  let last = 0;
  for (const m of text.matchAll(REF_RE)) {
    const start = m.index ?? 0;
    if (start > last) pieces.push({ kind: "text", value: text.slice(last, start) });
    pieces.push({ kind: "ref", value: m[0], noteId: m[1] });
    last = start + m[0].length;
  }
  if (last < text.length) pieces.push({ kind: "text", value: text.slice(last) });
  // Second pass on text chunks for quoted comments.
  for (const p of pieces) {
    if (p.kind !== "text") { tokens.push(p as Token); continue; }
    let l = 0;
    for (const m of p.value.matchAll(QUOTE_RE)) {
      const start = m.index ?? 0;
      if (start > l) tokens.push({ kind: "text", value: p.value.slice(l, start) });
      tokens.push({ kind: "quote", value: m[0] });
      l = start + m[0].length;
    }
    if (l < p.value.length) tokens.push({ kind: "text", value: p.value.slice(l) });
  }
  return tokens;
}

export default function GroundedBody({ text, refs, className, style }: Props) {
  if (!text) return null;
  const byId = new Map(refs.map(r => [r.note_id, r]));
  const tokens = tokenize(text);
  let chipIdx = 0;
  return (
    <div className={className} style={{ whiteSpace: "pre-wrap", lineHeight: 1.75, ...style }}>
      {tokens.map((t, i) => {
        if (t.kind === "ref" && t.noteId) {
          const r = byId.get(t.noteId);
          chipIdx += 1;
          if (!r) {
            return (
              <span key={i} style={MISSING_CHIP_STYLE}
                title={`ref:${t.noteId} — 在本稿持久化的 RAG 里没找到对应来源 ，AI 可能虚构了 note_id`}>
                ⚠ ref:{t.noteId.slice(0, 6)}
              </span>
            );
          }
          const tooltip =
            `[#${chipIdx}] 来自 @${r.author_nickname || "?"}` +
            ` · 👍${r.liked_count?.toLocaleString() ?? 0}` +
            (r.collected_count ? ` ⭐${r.collected_count.toLocaleString()}` : "") +
            `\n${(r.title || "").slice(0, 90)}` +
            (r.body_excerpt ? `\n\n${r.body_excerpt.slice(0, 200)}` : "");
          return r.url ? (
            <a key={i} href={r.url} target="_blank" rel="noreferrer"
              style={CHIP_STYLE} title={tooltip}>
              #{chipIdx}
            </a>
          ) : (
            <span key={i} style={CHIP_STYLE} title={tooltip}>#{chipIdx}</span>
          );
        }
        if (t.kind === "quote") {
          return <span key={i} style={QUOTE_STYLE}>{t.value}</span>;
        }
        return <span key={i}>{t.value}</span>;
      })}
    </div>
  );
}

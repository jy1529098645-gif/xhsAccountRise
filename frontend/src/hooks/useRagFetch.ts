import { useEffect, useState } from "react";
import { api } from "../api";
import { humaniseError } from "../errors";
import type { RagSearchResult } from "../types";

export interface RagFetchState {
  data: RagSearchResult | null;
  loading: boolean;
  err: string | null;
  retry: () => void;
}

// Used by StrategyRefsPanel + SlotRagPanel. Backend never 500s anymore
// (returns 200 with `error` field on internal failure), but transport errors
// and the data-present-but-error-set case still need consistent handling.
//
// When queryUsable=false, no request fires and loading flips to false
// immediately — callers should render null or a hidden state.
//
// extraDeps lets the caller invalidate the cache without changing the query
// (e.g., pack_id change re-fetches even if query text is identical).
export function useRagFetch(
  query: string,
  queryUsable: boolean,
  k: number,
  n: number,
  extraDeps: readonly unknown[] = [],
  label = "useRagFetch",
): RagFetchState {
  const [data, setData] = useState<RagSearchResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    if (!queryUsable) { setLoading(false); return; }
    let cancel = false;
    setLoading(true); setErr(null);
    api.ragSearch(query, k, n)
      .then((d) => {
        if (cancel) return;
        if (d?.error && (d.refs?.length ?? 0) === 0 && (d.comments?.length ?? 0) === 0) {
          setErr(String(d.error));
        }
        setData(d);
      })
      .catch((e: unknown) => {
        if (cancel) return;
        // eslint-disable-next-line no-console
        console.error(`[${label}] ragSearch failed`, e);
        // humaniseError sometimes translates "Failed to fetch" into a
        // misleading "backend not running" — append the raw message so
        // diagnosis stays possible.
        const human = humaniseError(e);
        const raw = e instanceof Error ? e.message : String(e ?? "");
        setErr(human === raw ? human : `${human}\n（原始 ：${raw.slice(0, 200)}）`);
      })
      .finally(() => { if (!cancel) setLoading(false); });
    return () => { cancel = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, queryUsable, k, n, reloadKey, ...extraDeps]);

  return { data, loading, err, retry: () => setReloadKey(k => k + 1) };
}

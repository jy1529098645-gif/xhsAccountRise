/**
 * Module-scope job tracker. Long-running operations (compose / insight /
 * expand / autofill / retrospective / integrate) survive page navigation
 * via this store — the user can switch sidebars freely and come back to
 * see the finished result.
 *
 * Without this, when a user clicks away mid-call:
 *   - The component unmounts
 *   - The fetch keeps running (good) but its resolve handler tries to
 *     setState on an unmounted component (no-op)
 *   - User comes back and sees the empty "ready to start" UI even though
 *     the operation completed
 *
 * With this store, the result is captured at module level. Pages query
 * the store on mount and render whatever they find.
 */
import { useEffect, useState } from "react";

export type JobKind =
  | "compose" | "insight" | "expand" | "autofill"
  | "propose" | "retrospective" | "integrate";

export interface JobState<T = any> {
  id: string;
  kind: JobKind;
  startedAt: number;
  finishedAt?: number;
  status: "running" | "done" | "failed" | "aborted";
  controller: AbortController;
  promise: Promise<T>;
  result?: T;
  error?: string;
  meta: Record<string, any>;
}

const _jobs = new Map<string, JobState>();
const _listeners = new Set<() => void>();

// ---- localStorage persistence -------------------------------------------
// Done jobs (i.e. their results) are persisted so a page reload / browser
// quit doesn't drop the last compose / autofill / insight output. We only
// persist the result + meta, not the live Promise/AbortController.

const _LS_KEY = "studio.jobs.done.v1";
const _LS_MAX_ENTRIES = 16;
const _LS_MAX_RESULT_KB = 256;  // cap each result blob

interface PersistedJob {
  id: string;
  kind: JobKind;
  startedAt: number;
  finishedAt?: number;
  status: "done" | "failed" | "aborted";
  result?: unknown;
  error?: string;
  meta: Record<string, any>;
}

function _persistDone() {
  try {
    const out: PersistedJob[] = [];
    for (const j of _jobs.values()) {
      if (j.status === "running") continue;
      // Skip very large results so we don't blow the 5 MB localStorage quota.
      let serialized: string | null = null;
      try {
        serialized = JSON.stringify(j.result ?? null);
      } catch { serialized = null; }
      if (serialized && serialized.length > _LS_MAX_RESULT_KB * 1024) continue;
      out.push({
        id: j.id, kind: j.kind,
        startedAt: j.startedAt, finishedAt: j.finishedAt,
        status: j.status as PersistedJob["status"],
        result: j.result, error: j.error, meta: j.meta,
      });
    }
    // Keep the newest N
    out.sort((a, b) => (b.finishedAt ?? 0) - (a.finishedAt ?? 0));
    localStorage.setItem(_LS_KEY, JSON.stringify(out.slice(0, _LS_MAX_ENTRIES)));
  } catch { /* quota / serialize failure — accept it */ }
}

function _hydrate() {
  try {
    const raw = localStorage.getItem(_LS_KEY);
    if (!raw) return;
    const items: PersistedJob[] = JSON.parse(raw) || [];
    for (const p of items) {
      if (_jobs.has(p.id)) continue;
      // Synthesize a "complete" JobState. Promise resolves immediately to
      // the cached result; controller is a stub.
      const stub: JobState = {
        id: p.id, kind: p.kind,
        startedAt: p.startedAt, finishedAt: p.finishedAt,
        status: p.status,
        controller: new AbortController(),
        promise: p.status === "done"
          ? Promise.resolve(p.result)
          : Promise.reject(new Error(p.error || "rehydrated failed job")),
        result: p.result, error: p.error,
        meta: p.meta || {},
      };
      // Swallow the rejection so it doesn't crash the page.
      stub.promise.catch(() => {});
      _jobs.set(p.id, stub);
    }
  } catch { /* malformed — ignore */ }
}
_hydrate();

function notify() {
  _persistDone();
  _listeners.forEach(l => l());
}

export function startJob<T>(
  id: string,
  kind: JobKind,
  fetcher: (signal: AbortSignal) => Promise<T>,
  meta: Record<string, any> = {},
): JobState<T> {
  // Don't double-start: if a job with this id is already running, return it.
  const existing = _jobs.get(id);
  if (existing && existing.status === "running") return existing as JobState<T>;

  const controller = new AbortController();
  const job: JobState<T> = {
    id, kind, startedAt: Date.now(),
    status: "running", controller, meta,
    promise: null as any,
  };
  job.promise = fetcher(controller.signal)
    .then(r => {
      job.result = r;
      job.status = "done";
      job.finishedAt = Date.now();
      notify();
      return r;
    })
    .catch(e => {
      if (e?.name === "AbortError" || /aborted|AbortError/i.test(e?.message || "")) {
        job.status = "aborted";
      } else {
        job.status = "failed";
        job.error = e?.message ?? String(e);
      }
      job.finishedAt = Date.now();
      notify();
      throw e;
    });
  _jobs.set(id, job);
  notify();
  return job;
}

export function getJob<T = any>(id: string): JobState<T> | undefined {
  return _jobs.get(id) as JobState<T> | undefined;
}

export function listJobs(filter?: (j: JobState) => boolean): JobState[] {
  const out = Array.from(_jobs.values());
  return filter ? out.filter(filter) : out;
}

export function cancelJob(id: string): boolean {
  const job = _jobs.get(id);
  if (!job || job.status !== "running") return false;
  job.controller.abort();
  return true;
}

/** Remove a finished job from the registry. */
export function clearJob(id: string): void {
  const job = _jobs.get(id);
  if (job && job.status === "running") return; // refuse to drop a live one
  _jobs.delete(id);
  notify();
}

/** React hook: re-render when any job status changes. */
export function useJobsList(): JobState[] {
  const [, force] = useState(0);
  useEffect(() => {
    const l = () => force(n => n + 1);
    _listeners.add(l);
    return () => { _listeners.delete(l); };
  }, []);
  return listJobs();
}

/** React hook: subscribe to a specific job. Returns undefined if not started. */
export function useJob<T = any>(id: string | null): JobState<T> | undefined {
  const [, force] = useState(0);
  useEffect(() => {
    const l = () => force(n => n + 1);
    _listeners.add(l);
    return () => { _listeners.delete(l); };
  }, []);
  return id ? (getJob(id) as JobState<T>) : undefined;
}

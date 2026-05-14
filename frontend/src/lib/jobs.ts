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
function notify() { _listeners.forEach(l => l()); }

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

"""In-process registry of cancellable background jobs.

Long-running pipelines (insight / strategy.expand / agents.compose /
retrospective / external_reports.integrate) register a job_id at start and
check the associated asyncio.Event periodically. The /api/jobs/{id}/cancel
endpoint sets the event; the pipeline detects it between LLM-call stages
and raises CancelRequested, which the caller turns into a 'paused' DB row
+ a 'paused' response shape.

For stages that finished before the cancel, partial state is checkpointed
into the originating DB row (e.g. studio_strategies.partial_state_json).
A subsequent retry of the same job (same pack_id / draft_id / etc.) reads
the partial state and skips already-completed stages — that's the
'resume' part of the feature.

Real LLM-call cancellation requires the asyncio.Task itself to be
cancelled; httpx's transport then aborts the connection mid-stream. We
keep a handle to the current task in _job_tasks for that fast-path.
"""
from __future__ import annotations

import asyncio
import contextlib
from typing import Any


class CancelRequested(Exception):
    """Raised by pipeline stages when their job's cancel flag is set.
    Caught by the orchestrator → marks the row as 'paused' instead of
    'failed' and returns a paused-shape response."""


_job_events: dict[str, asyncio.Event] = {}
_job_tasks: dict[str, asyncio.Task] = {}
_job_meta: dict[str, dict[str, Any]] = {}


def register(job_id: str, *, kind: str = "", label: str = "") -> asyncio.Event:
    """Register the currently-running asyncio.Task under job_id and return
    a cancel-flag event. If a previous event exists for the same id (e.g.
    user clicked start twice), clear it first."""
    ev = asyncio.Event()
    _job_events[job_id] = ev
    try:
        _job_tasks[job_id] = asyncio.current_task()  # type: ignore[assignment]
    except RuntimeError:
        pass
    _job_meta[job_id] = {"kind": kind, "label": label}
    return ev


def unregister(job_id: str) -> None:
    _job_events.pop(job_id, None)
    _job_tasks.pop(job_id, None)
    _job_meta.pop(job_id, None)


def is_canceled(job_id: str) -> bool:
    ev = _job_events.get(job_id)
    return bool(ev and ev.is_set())


def check(job_id: str) -> None:
    """Cooperative cancel check — call between stages."""
    if is_canceled(job_id):
        raise CancelRequested(f"job {job_id} canceled by user")


def cancel(job_id: str) -> dict[str, Any]:
    """Request cancellation. Sets the event AND cancels the task (so
    in-flight httpx connections abort, freeing the LLM call mid-stream)."""
    ev = _job_events.get(job_id)
    task = _job_tasks.get(job_id)
    meta = _job_meta.get(job_id, {})
    canceled = False
    if ev and not ev.is_set():
        ev.set()
        canceled = True
    if task and not task.done():
        task.cancel()
        canceled = True
    return {"job_id": job_id, "canceled": canceled, **meta}


def list_jobs() -> list[dict[str, Any]]:
    return [
        {"job_id": jid, **_job_meta.get(jid, {}),
         "canceled": _job_events[jid].is_set() if jid in _job_events else False}
        for jid in list(_job_events.keys())
    ]


@contextlib.asynccontextmanager
async def tracked(job_id: str, *, kind: str = "", label: str = ""):
    """Context manager that registers + auto-unregisters a job. Yields the
    cancel event. Usage:

        async with jobs.tracked(f"expand:{pack_id}", kind="expand") as cancel_ev:
            ...
            jobs.check(job_id)  # between stages
            ...
    """
    ev = register(job_id, kind=kind, label=label)
    try:
        yield ev
    finally:
        unregister(job_id)

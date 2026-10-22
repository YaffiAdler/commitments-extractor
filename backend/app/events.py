"""Server-sent events for one job (``GET /jobs/{id}/events``).

The stream polls the job store and sends:

- ``progress``: ``{"status", "forms_total", "forms_done"}`` whenever it changes;
- ``rows``: a JSON list of the forms whose reading finished since the last
  ``rows`` event (``FormGroup`` JSON, sent once each);
- ``end``: the whole job once it is ``done`` or ``failed`` (the final word:
  cross-form checks such as ``central_duplicate`` run after the last row);
- ``missing``: the job is unknown, deleted or expired;

then the stream closes. A ``:`` comment line keeps an idle connection open.
The events carry the same data as ``GET /jobs/{id}``; never log them.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field

from app.schemas import Job, JobStatus

EVENT_PROGRESS = "progress"
EVENT_ROWS = "rows"
EVENT_END = "end"
EVENT_MISSING = "missing"
KEEPALIVE = ": keepalive\n\n"
FINISHED_STATUSES = (JobStatus.DONE, JobStatus.FAILED)


def format_event(name: str, data: str) -> str:
    """One SSE message; ``data`` must be a single line (compact JSON is)."""
    return f"event: {name}\ndata: {data}\n\n"


@dataclass
class StreamState:
    """What a stream has already sent."""

    progress: dict | None = None
    sent_rows: set[str] = field(default_factory=set)
    ended: bool = False


def progress_of(job: Job) -> dict:
    return {
        "status": job.status.value,
        "forms_total": len(job.groups),
        "forms_done": sum(group.finished for group in job.groups),
    }


def job_events(job: Job | None, state: StreamState) -> list[str]:
    """The events that bring a client from ``state`` to ``job``; updates ``state``."""
    if job is None:
        state.ended = True
        return [format_event(EVENT_MISSING, "{}")]
    events = []
    progress = progress_of(job)
    if progress != state.progress:
        state.progress = progress
        events.append(format_event(EVENT_PROGRESS, json.dumps(progress)))
    new_rows = [group for group in job.groups if group.finished and group.id not in state.sent_rows]
    if new_rows:
        state.sent_rows.update(group.id for group in new_rows)
        rows = ",".join(group.model_dump_json() for group in new_rows)
        events.append(format_event(EVENT_ROWS, f"[{rows}]"))
    if job.status in FINISHED_STATUSES:
        state.ended = True
        events.append(format_event(EVENT_END, job.model_dump_json()))
    return events


async def stream_job_events(get_job: Callable[[], Awaitable[Job | None]], poll_interval_s: float,
                            keepalive_s: float) -> AsyncIterator[str]:
    """Poll ``get_job`` until the job ends or disappears, yielding SSE text.
    Starlette cancels the generator when the client disconnects."""
    state = StreamState()
    idle_s = 0.0
    while True:
        events = job_events(await get_job(), state)
        for event in events:
            yield event
        if state.ended:
            return
        idle_s = 0.0 if events else idle_s + poll_interval_s
        if idle_s >= keepalive_s:
            idle_s = 0.0
            yield KEEPALIVE
        await asyncio.sleep(poll_interval_s)

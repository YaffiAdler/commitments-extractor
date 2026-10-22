"""Server-sent events of a job: what is sent, once, and when the stream ends."""
import asyncio
import json

from app.events import (
    EVENT_END,
    EVENT_MISSING,
    EVENT_PROGRESS,
    EVENT_ROWS,
    KEEPALIVE,
    StreamState,
    job_events,
    stream_job_events,
)
from app.schemas import FormGroup, Job, JobStatus

POLL_S = 0.001


def parse(text: str) -> list[tuple[str, object]]:
    """SSE text -> [(event name, parsed data)]; comment lines are skipped."""
    events = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if not line.startswith(":"))
        if lines:
            events.append((lines["event"], json.loads(lines["data"])))
    return events


def _job(status: JobStatus, finished: list[bool]) -> Job:
    groups = [FormGroup(id=f"g{i}", source_file="f.pdf", pages=[i], finished=done) for i, done in enumerate(finished)]
    return Job(id="j1", status=status, groups=groups)


def test_progress_and_finished_rows():
    events = parse("".join(job_events(_job(JobStatus.RUNNING, [True, False]), StreamState())))
    assert events[0] == (EVENT_PROGRESS, {"status": "running", "forms_total": 2, "forms_done": 1})
    assert events[1][0] == EVENT_ROWS and [row["id"] for row in events[1][1]] == ["g0"]
    assert len(events) == 2


def test_rows_are_sent_once_and_unchanged_progress_is_not_repeated():
    state = StreamState()
    job_events(_job(JobStatus.RUNNING, [True, False]), state)
    assert job_events(_job(JobStatus.RUNNING, [True, False]), state) == []
    events = parse("".join(job_events(_job(JobStatus.RUNNING, [True, True]), state)))
    assert [name for name, _ in events] == [EVENT_PROGRESS, EVENT_ROWS]
    assert [row["id"] for row in events[1][1]] == ["g1"]


def test_finished_job_ends_the_stream_with_the_whole_job():
    state = StreamState()
    events = parse("".join(job_events(_job(JobStatus.DONE, [True]), state)))
    assert [name for name, _ in events] == [EVENT_PROGRESS, EVENT_ROWS, EVENT_END]
    assert events[-1][1]["id"] == "j1" and events[-1][1]["status"] == "done"
    assert state.ended


def test_missing_job_ends_the_stream():
    state = StreamState()
    assert parse("".join(job_events(None, state))) == [(EVENT_MISSING, {})]
    assert state.ended


def test_stream_follows_the_job_until_it_ends():
    snapshots = iter([
        _job(JobStatus.PENDING, []),
        _job(JobStatus.RUNNING, [False, False]),
        _job(JobStatus.RUNNING, [False, False]),  # no change: nothing sent, keepalive due
        _job(JobStatus.RUNNING, [True, False]),
        _job(JobStatus.DONE, [True, True]),
    ])

    async def get_job():
        return next(snapshots)

    async def collect() -> list[str]:
        return [chunk async for chunk in stream_job_events(get_job, POLL_S, keepalive_s=POLL_S)]

    chunks = asyncio.run(collect())
    assert KEEPALIVE in chunks
    events = parse("".join(chunks))
    assert [name for name, _ in events] == [EVENT_PROGRESS, EVENT_PROGRESS, EVENT_PROGRESS, EVENT_ROWS,
                                            EVENT_PROGRESS, EVENT_ROWS, EVENT_END]
    rows = [row["id"] for name, data in events if name == EVENT_ROWS for row in data]
    assert rows == ["g0", "g1"]

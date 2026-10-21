"""SQLite job store (``CE_STORAGE=sqlite``): jobs survive a server restart.

Only search/filter fields are real columns; each form group, with its full
``Commitment``, is stored as JSON (``commitments.data_json``). No images are
stored. Every operation opens its own connection, so the store is safe to use
from the request threads and the background tasks at the same time.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import BACKEND_DIR
from pydantic import TypeAdapter

from app.schemas import Commitment, FormGroup, IrrelevantPage, Job, JobStatus
from app.storage.base import INTERRUPTED_ERROR, UNFINISHED_STATUSES, JobExistsError
from app.storage.tmp import TempStorage

SCHEMA_PATH = BACKEND_DIR / "data" / "schema.sql"
# Seconds a writer waits for another connection's lock before failing.
BUSY_TIMEOUT_S = 10.0
# Columns added after the first schema; databases created before get them on init.
ADDED_JOB_COLUMNS = {
    "irrelevant_pages_json": "TEXT NOT NULL DEFAULT '[]'",
    "split_vlm_calls": "INTEGER NOT NULL DEFAULT 0",
}
_IRRELEVANT_PAGES = TypeAdapter(list[IrrelevantPage])


def connect(db_path: Path) -> sqlite3.Connection:
    """Open a connection with foreign keys (and so ON DELETE CASCADE) enforced."""
    connection = sqlite3.connect(db_path, timeout=BUSY_TIMEOUT_S)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db(db_path: Path, schema_path: Path = SCHEMA_PATH) -> None:
    """Create the database file and its tables if missing (idempotent)."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect(db_path)) as connection:
        connection.executescript(schema_path.read_text(encoding="utf-8"))
        _add_missing_columns(connection)


def _add_missing_columns(connection: sqlite3.Connection) -> None:
    existing = {row[1] for row in connection.execute("PRAGMA table_info(jobs)")}
    for name, definition in ADDED_JOB_COLUMNS.items():
        if name not in existing:
            connection.execute(f"ALTER TABLE jobs ADD COLUMN {name} {definition}")


def _timestamp(moment: datetime) -> str:
    # Fixed format and zone, so timestamps compare correctly as text.
    return moment.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _shared(values: Iterable[str | None]) -> str | None:
    """The value every form of a job has in common, else None."""
    distinct = set(values)
    return distinct.pop() if len(distinct) == 1 else None


def _job_row(job: Job) -> tuple:
    if len(job.files) > 1:
        raise ValueError("jobs with several files cannot be stored yet")
    commitments = [group.commitment for group in job.groups]
    return (
        job.id,
        _timestamp(job.created_at),
        job.status.value,
        job.files[0] if job.files else None,
        _shared(c.form_type_id for c in commitments),
        _shared(c.sender.value for c in commitments),
        job.error,
        any(group.needs_review for group in job.groups),
        _IRRELEVANT_PAGES.dump_json(job.irrelevant_pages).decode(),
        job.split_vlm_calls,
    )


def _group_rows(job: Job) -> list[tuple]:
    return [
        (group.id, job.id, seq, group.model_dump_json(), group.needs_review)
        for seq, group in enumerate(job.groups)
    ]


class SqliteJobStore:
    def __init__(self, db_path: Path, storage: TempStorage, ttl_s: int) -> None:
        self._db_path = db_path
        self._storage = storage
        self._ttl = timedelta(seconds=ttl_s)
        self._initialized = False

    def init(self) -> None:
        init_db(self._db_path)
        self._initialized = True

    def _connect(self) -> sqlite3.Connection:
        # Also covers apps used without their startup hook (e.g. a bare TestClient).
        if not self._initialized:
            self.init()
        return connect(self._db_path)

    def create(self, job: Job) -> None:
        with closing(self._connect()) as connection, connection:
            try:
                connection.execute(
                    "INSERT INTO jobs (id, created_at, status, source_file, form_type, sender, error,"
                    " needs_review, irrelevant_pages_json, split_vlm_calls)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", _job_row(job))
            except sqlite3.IntegrityError as exc:
                raise JobExistsError("job already exists") from exc
            self._insert_groups(connection, job)

    def get(self, job_id: str) -> Job | None:
        self.purge_expired()
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT id, created_at, status, source_file, error, irrelevant_pages_json, split_vlm_calls"
                " FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                return None
            groups = connection.execute(
                "SELECT data_json FROM commitments WHERE job_id = ? ORDER BY seq", (job_id,)
            ).fetchall()
        job_id, created_at, status, source_file, error, irrelevant_pages, split_vlm_calls = row
        return Job(
            id=job_id,
            created_at=datetime.fromisoformat(created_at),
            status=JobStatus(status),
            files=[source_file] if source_file else [],
            groups=[FormGroup.model_validate_json(data) for (data,) in groups],
            irrelevant_pages=_IRRELEVANT_PAGES.validate_json(irrelevant_pages),
            split_vlm_calls=split_vlm_calls,
            error=error,
        )

    def update(self, job: Job) -> bool:
        row = _job_row(job)
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                "UPDATE jobs SET created_at = ?, status = ?, source_file = ?, form_type = ?,"
                " sender = ?, error = ?, needs_review = ?, irrelevant_pages_json = ?, split_vlm_calls = ?"
                " WHERE id = ?",
                (*row[1:], job.id),
            )
            if cursor.rowcount == 0:
                return False
            connection.execute("DELETE FROM commitments WHERE job_id = ?", (job.id,))
            self._insert_groups(connection, job)
        return True

    def delete(self, job_id: str) -> bool:
        with closing(self._connect()) as connection, connection:
            existed = connection.execute("DELETE FROM jobs WHERE id = ?", (job_id,)).rowcount > 0
        self._storage.delete_job(job_id)
        return existed

    def list_commitments(self, job_id: str | None = None, needs_review: bool | None = None) -> list[Commitment]:
        self.purge_expired()
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT c.data_json FROM commitments c JOIN jobs j ON j.id = c.job_id"
                " WHERE (:job_id IS NULL OR c.job_id = :job_id)"
                " AND (:needs_review IS NULL OR c.needs_review = :needs_review)"
                " ORDER BY j.created_at, c.seq",
                {"job_id": job_id, "needs_review": needs_review},
            ).fetchall()
        return [FormGroup.model_validate_json(data).commitment for (data,) in rows]

    def fail_interrupted(self) -> int:
        unfinished = [status.value for status in UNFINISHED_STATUSES]
        with closing(self._connect()) as connection, connection:
            return connection.execute(
                f"UPDATE jobs SET status = ?, error = ? WHERE status IN ({', '.join('?' * len(unfinished))})",
                (JobStatus.FAILED.value, INTERRUPTED_ERROR, *unfinished),
            ).rowcount

    def purge_expired(self, now: datetime | None = None) -> None:
        cutoff = (now or datetime.now(timezone.utc)) - self._ttl
        with closing(self._connect()) as connection:
            expired = [
                job_id
                for (job_id,) in connection.execute("SELECT id FROM jobs WHERE created_at < ?", (_timestamp(cutoff),))
            ]
        for job_id in expired:
            self.delete(job_id)
        self._storage.purge_older_than(self._ttl.total_seconds())

    @staticmethod
    def _insert_groups(connection: sqlite3.Connection, job: Job) -> None:
        connection.executemany("INSERT INTO commitments VALUES (?, ?, ?, ?, ?)", _group_rows(job))

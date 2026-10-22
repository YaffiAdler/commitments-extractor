"""The job store shared by the SQL databases (``sqlite.py``, ``postgres.py``).

Both use one schema, written in portable SQL as numbered migrations
(``backend/data/migrations/NNNN_name.sql``). ``init`` applies the ones a
database has not seen yet, inside one locked transaction, and records each in
``schema_version``; so it is safe to run at every startup and from several
processes. Never edit a released migration: add the next one.

Tables: ``jobs`` (one row per job, filter fields as columns),
``job_files`` (stored file names), ``commitments`` (one row per form group,
the whole ``FormGroup`` with its ``Commitment`` as JSON), ``corrections``
(manual corrections, JSON). Child rows go with their job (ON DELETE
CASCADE). No images are stored.

Every change of a job is one transaction. A driver error is re-raised as
``JobStoreError`` with the driver's exception type only (``from None``, so the
original message and the query parameters do not reach a log through the
traceback either).
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from app.config import BACKEND_DIR
from app.schemas import Commitment, Correction, FormGroup, IrrelevantPage, Job, JobStatus
from app.storage.base import INTERRUPTED_ERROR, UNFINISHED_STATUSES, JobExistsError, JobStoreError
from app.storage.tmp import TempStorage

MIGRATIONS_DIR = BACKEND_DIR / "data" / "migrations"
_MIGRATION_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")
_SQL_COMMENT = re.compile(r"--[^\n]*")
SCHEMA_VERSION_DDL = ("CREATE TABLE IF NOT EXISTS schema_version"
                      " (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
JOB_COLUMNS = ("id", "created_at", "status", "form_type", "sender", "error", "needs_review",
               "irrelevant_pages_json", "split_vlm_calls", "started_at", "finished_at")
_IRRELEVANT_PAGES = TypeAdapter(list[IrrelevantPage])


@dataclass(frozen=True)
class Migration:
    version: int
    statements: tuple[str, ...]


def split_statements(sql: str) -> tuple[str, ...]:
    """The statements of a migration file, comments removed (so a comment
    must not contain a semicolon, and no string literal may contain ``--`` or ``;``)."""
    return tuple(part.strip() for part in _SQL_COMMENT.sub("", sql).split(";") if part.strip())


def load_migrations(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """Every ``NNNN_name.sql`` in ``directory``; versions must run 1, 2, 3, ..."""
    migrations = []
    for path in sorted(directory.glob("*.sql")):
        match = _MIGRATION_NAME.fullmatch(path.name)
        if match is None:
            raise ValueError(f"migration file name must be NNNN_name.sql: {path.name}")
        migrations.append(Migration(int(match.group(1)), split_statements(path.read_text(encoding="utf-8"))))
    if [m.version for m in migrations] != list(range(1, len(migrations) + 1)):
        raise ValueError("migration versions must be 1, 2, 3, ... without gaps")
    return migrations


def timestamp(moment: datetime) -> str:
    """Fixed format and zone, so timestamps compare correctly as text."""
    return moment.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _optional_timestamp(moment: datetime | None) -> str | None:
    return None if moment is None else timestamp(moment)


def _optional_datetime(text: str | None) -> datetime | None:
    return None if text is None else datetime.fromisoformat(text)


def _shared(values: Iterable[str | None]) -> str | None:
    """The value every form of a job has in common, else None."""
    distinct = set(values)
    return distinct.pop() if len(distinct) == 1 else None


def _job_row(job: Job) -> tuple:
    commitments = [group.commitment for group in job.groups]
    return (
        job.id,
        timestamp(job.created_at),
        job.status.value,
        _shared(c.form_type_id for c in commitments),
        _shared(c.sender.value for c in commitments),
        job.error,
        int(any(group.needs_review for group in job.groups)),
        _IRRELEVANT_PAGES.dump_json(job.irrelevant_pages).decode(),
        job.split_vlm_calls,
        _optional_timestamp(job.started_at),
        _optional_timestamp(job.finished_at),
    )


class SqlJobStore(ABC):
    """``JobStore`` over a DB-API connection; subclasses supply the driver."""

    # Parameter marker of the driver; queries below are written with "?".
    placeholder = "?"
    driver_errors: tuple[type[Exception], ...] = ()
    integrity_error: type[Exception] = Exception

    def __init__(self, storage: TempStorage, ttl_s: int, file_ttl_s: int | None = None) -> None:
        self._storage = storage
        self._ttl = timedelta(seconds=ttl_s)
        self._file_ttl_s = ttl_s if file_ttl_s is None else file_ttl_s
        self._initialized = False

    # --- driver-specific ------------------------------------------------------

    @abstractmethod
    def _connection(self) -> AbstractContextManager[Any]:
        """A connection inside one transaction: committed if the block ends
        normally, rolled back if it raises."""

    @abstractmethod
    def _locked_connection(self) -> AbstractContextManager[Any]:
        """Like ``_connection``, but holding a lock that serializes migrations
        across processes."""

    def _prepare_legacy(self, connection: Any) -> None:
        """Bring a database from before schema versions up to version 1's
        shape; called once, when ``schema_version`` is empty."""

    def close(self) -> None:
        """Nothing to release by default."""

    # --- JobStore -------------------------------------------------------------

    def init(self) -> None:
        with self._errors_hidden(), self._locked_connection() as connection:
            self._run(connection, SCHEMA_VERSION_DDL)
            (current,) = self._run(connection, "SELECT COALESCE(MAX(version), 0) FROM schema_version").fetchone()
            if current == 0:
                self._prepare_legacy(connection)
            for migration in load_migrations():
                if migration.version <= current:
                    continue
                for statement in migration.statements:
                    connection.execute(statement)
                self._run(connection, "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                          (migration.version, timestamp(datetime.now(timezone.utc))))
        self._initialized = True

    def create(self, job: Job) -> None:
        with self._transaction() as connection:
            try:
                self._run(connection, f"INSERT INTO jobs ({', '.join(JOB_COLUMNS)})"
                                      f" VALUES ({', '.join('?' * len(JOB_COLUMNS))})", _job_row(job))
            except self.integrity_error:
                raise JobExistsError("job already exists") from None
            self._insert_children(connection, job)

    def get(self, job_id: str) -> Job | None:
        self.purge_expired()
        with self._transaction() as connection:
            row = self._run(connection, "SELECT created_at, status, error, irrelevant_pages_json, split_vlm_calls,"
                                        " started_at, finished_at FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                return None
            files = self._column(connection, "SELECT stored_name FROM job_files WHERE job_id = ? ORDER BY seq", job_id)
            groups = self._column(connection, "SELECT data_json FROM commitments WHERE job_id = ? ORDER BY seq", job_id)
            corrections = self._column(connection, "SELECT data_json FROM corrections WHERE job_id = ? ORDER BY seq",
                                       job_id)
        created_at, status, error, irrelevant_pages, split_vlm_calls, started_at, finished_at = row
        return Job(
            id=job_id,
            created_at=datetime.fromisoformat(created_at),
            started_at=_optional_datetime(started_at),
            finished_at=_optional_datetime(finished_at),
            status=JobStatus(status),
            files=files,
            groups=[FormGroup.model_validate_json(data) for data in groups],
            irrelevant_pages=_IRRELEVANT_PAGES.validate_json(irrelevant_pages),
            split_vlm_calls=split_vlm_calls,
            corrections=[Correction.model_validate_json(data) for data in corrections],
            error=error,
        )

    def update(self, job: Job) -> bool:
        assignments = ", ".join(f"{column} = ?" for column in JOB_COLUMNS[1:])
        with self._transaction() as connection:
            cursor = self._run(connection, f"UPDATE jobs SET {assignments} WHERE id = ?", (*_job_row(job)[1:], job.id))
            if cursor.rowcount == 0:
                return False
            for table in ("commitments", "job_files", "corrections"):
                self._run(connection, f"DELETE FROM {table} WHERE job_id = ?", (job.id,))
            self._insert_children(connection, job)
        return True

    def delete(self, job_id: str) -> bool:
        with self._transaction() as connection:
            existed = self._run(connection, "DELETE FROM jobs WHERE id = ?", (job_id,)).rowcount > 0
        self._storage.delete_job(job_id)
        return existed

    def list_commitments(self, job_id: str | None = None, needs_review: bool | None = None) -> list[Commitment]:
        self.purge_expired()
        conditions, params = [], []
        if job_id is not None:
            conditions.append("c.job_id = ?")
            params.append(job_id)
        if needs_review is not None:
            conditions.append("c.needs_review = ?")
            params.append(int(needs_review))
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._transaction() as connection:
            rows = self._run(connection, "SELECT c.data_json FROM commitments c JOIN jobs j ON j.id = c.job_id"
                                         f"{where} ORDER BY j.created_at, c.seq", params).fetchall()
        return [FormGroup.model_validate_json(data).commitment for (data,) in rows]

    def fail_interrupted(self) -> int:
        unfinished = [status.value for status in UNFINISHED_STATUSES]
        with self._transaction() as connection:
            return self._run(connection,
                             f"UPDATE jobs SET status = ?, error = ? WHERE status IN ({', '.join('?' * len(unfinished))})",
                             (JobStatus.FAILED.value, INTERRUPTED_ERROR, *unfinished)).rowcount

    def purge_expired(self, now: datetime | None = None) -> None:
        cutoff = (now or datetime.now(timezone.utc)) - self._ttl
        with self._transaction() as connection:
            expired = self._column(connection, "SELECT id FROM jobs WHERE created_at < ?", timestamp(cutoff))
        for job_id in expired:
            self.delete(job_id)
        self._storage.purge_older_than(self._file_ttl_s)

    # --- helpers --------------------------------------------------------------

    @contextmanager
    def _errors_hidden(self) -> Iterator[None]:
        try:
            yield
        except self.driver_errors as exc:
            raise JobStoreError(type(exc).__name__) from None

    @contextmanager
    def _transaction(self) -> Iterator[Any]:
        # Also covers apps used without their startup hook (e.g. a bare TestClient).
        if not self._initialized:
            self.init()
        with self._errors_hidden(), self._connection() as connection:
            yield connection

    def _run(self, connection: Any, sql: str, params: Sequence[Any] = ()) -> Any:
        return connection.execute(sql.replace("?", self.placeholder), params)

    def _column(self, connection: Any, sql: str, param: str) -> list[Any]:
        return [value for (value,) in self._run(connection, sql, (param,)).fetchall()]

    def _insert_many(self, connection: Any, sql: str, rows: list[tuple]) -> None:
        if rows:
            connection.cursor().executemany(sql.replace("?", self.placeholder), rows)

    def _insert_children(self, connection: Any, job: Job) -> None:
        self._insert_many(connection, "INSERT INTO commitments (id, job_id, seq, data_json, needs_review)"
                                      " VALUES (?, ?, ?, ?, ?)",
                          [(group.id, job.id, seq, group.model_dump_json(), int(group.needs_review))
                           for seq, group in enumerate(job.groups)])
        self._insert_many(connection, "INSERT INTO job_files (job_id, seq, stored_name) VALUES (?, ?, ?)",
                          [(job.id, seq, name) for seq, name in enumerate(job.files)])
        self._insert_many(connection, "INSERT INTO corrections (job_id, seq, group_id, field, corrected_at, data_json)"
                                      " VALUES (?, ?, ?, ?, ?, ?)",
                          [(job.id, seq, c.group_id, c.field, timestamp(c.corrected_at), c.model_dump_json())
                           for seq, c in enumerate(job.corrections)])

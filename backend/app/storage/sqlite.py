"""SQLite job store: the local default (no ``DATABASE_URL``), or ``CE_STORAGE=sqlite``.

Jobs survive a server restart. The schema and queries are shared with
Postgres (``sql.py``). Every operation opens its own connection, so the store
is safe to use from the request threads and the background tasks at once.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

from app.storage.sql import SqlJobStore
from app.storage.tmp import TempStorage

# Seconds a writer waits for another connection's lock before failing.
BUSY_TIMEOUT_S = 10.0
# Columns added to ``jobs`` before schema versions existed; a database from then gets them on init.
LEGACY_JOB_COLUMNS = {
    "irrelevant_pages_json": "TEXT NOT NULL DEFAULT '[]'",
    "split_vlm_calls": "INTEGER NOT NULL DEFAULT 0",
}


def connect(db_path: Path) -> sqlite3.Connection:
    """Open a connection with foreign keys (and so ON DELETE CASCADE) enforced."""
    connection = sqlite3.connect(db_path, timeout=BUSY_TIMEOUT_S)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


class SqliteJobStore(SqlJobStore):
    driver_errors = (sqlite3.Error,)
    integrity_error = sqlite3.IntegrityError

    def __init__(self, db_path: Path, storage: TempStorage, ttl_s: int, file_ttl_s: int | None = None) -> None:
        super().__init__(storage, ttl_s, file_ttl_s)
        self._db_path = db_path

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        with closing(connect(self._db_path)) as connection, connection:
            yield connection

    @contextmanager
    def _locked_connection(self) -> Iterator[sqlite3.Connection]:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(connect(self._db_path)) as connection:
            connection.isolation_level = None  # manual transaction: DDL must not auto-commit halfway
            connection.execute("BEGIN IMMEDIATE")  # the write lock: one migrating process at a time
            try:
                yield connection
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            connection.execute("COMMIT")

    def _prepare_legacy(self, connection: sqlite3.Connection) -> None:
        existing = {row[1] for row in connection.execute("PRAGMA table_info(jobs)")}
        if not existing:
            return
        for name, definition in LEGACY_JOB_COLUMNS.items():
            if name not in existing:
                connection.execute(f"ALTER TABLE jobs ADD COLUMN {name} {definition}")

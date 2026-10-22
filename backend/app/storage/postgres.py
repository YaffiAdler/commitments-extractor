"""Postgres job store: chosen when ``DATABASE_URL`` is set (or ``CE_STORAGE=postgres``).

For deployment (e.g. a Render Postgres instance): jobs survive restarts and
redeploys of the server. The schema and queries are shared with SQLite
(``sql.py``). Connections come from a small pool, opened on first use; the
URL holds the password, so it is never logged.
"""
from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg_pool import ConnectionPool

from app.storage.sql import SqlJobStore
from app.storage.tmp import TempStorage

# Any fixed 64-bit number: the advisory lock held while migrating, shared by every server process.
MIGRATION_LOCK_ID = 7_202_610_220
POOL_MIN_SIZE = 1


class PostgresJobStore(SqlJobStore):
    placeholder = "%s"
    driver_errors = (psycopg.Error,)  # pool timeouts and a closed pool are psycopg.OperationalError too
    integrity_error = psycopg.errors.UniqueViolation

    def __init__(self, conninfo: str, storage: TempStorage, ttl_s: int, file_ttl_s: int | None = None, *,
                 pool_size: int, timeout_s: float) -> None:
        super().__init__(storage, ttl_s, file_ttl_s)
        self._timeout_s = timeout_s
        self._pool = ConnectionPool(conninfo, min_size=POOL_MIN_SIZE, max_size=max(pool_size, POOL_MIN_SIZE),
                                    timeout=timeout_s, open=False,
                                    kwargs={"connect_timeout": max(1, round(timeout_s))})
        self._opened = False
        self._open_lock = threading.Lock()

    def close(self) -> None:
        with self._open_lock:
            if self._opened:
                self._pool.close()
                self._opened = False

    @contextmanager
    def _connection(self) -> Iterator[psycopg.Connection]:
        with self._open_lock:
            if not self._opened:
                self._pool.open(wait=True, timeout=self._timeout_s)
                self._opened = True
        # The pool commits when the block ends normally and rolls back when it raises.
        with self._pool.connection() as connection:
            yield connection

    @contextmanager
    def _locked_connection(self) -> Iterator[psycopg.Connection]:
        with self._connection() as connection:
            # Released at the end of the transaction; DDL in Postgres is transactional.
            connection.execute("SELECT pg_advisory_xact_lock(%s)", (MIGRATION_LOCK_ID,))
            yield connection

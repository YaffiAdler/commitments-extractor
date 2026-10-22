"""Job stores behind the ``JobStore`` interface, plus temporary file storage."""
from __future__ import annotations

from app.config import Settings, StorageName
from app.storage.base import JobStore
from app.storage.memory import MemoryJobStore
from app.storage.sqlite import SqliteJobStore
from app.storage.tmp import TempStorage


def create_job_store(settings: Settings, storage: TempStorage) -> JobStore:
    """Build the job store selected in ``settings.storage``."""
    if settings.storage is StorageName.POSTGRES:
        # Imported here: only this store needs the Postgres driver loaded.
        from app.storage.postgres import PostgresJobStore

        return PostgresJobStore(settings.database_url, storage, settings.job_ttl_s, settings.file_ttl_s,
                                pool_size=settings.db_pool_size, timeout_s=settings.db_timeout_s)
    if settings.storage is StorageName.SQLITE:
        return SqliteJobStore(settings.db_path, storage, settings.job_ttl_s, settings.file_ttl_s)
    if settings.storage is StorageName.MEMORY:
        return MemoryJobStore(storage, settings.job_ttl_s, settings.file_ttl_s)
    raise ValueError(f"unknown storage: {settings.storage}")

"""Job stores behind the ``JobStore`` interface, plus temporary file storage."""
from __future__ import annotations

from app.config import Settings, StorageName
from app.storage.base import JobStore
from app.storage.memory import MemoryJobStore
from app.storage.sqlite import SqliteJobStore
from app.storage.tmp import TempStorage


def create_job_store(settings: Settings, storage: TempStorage) -> JobStore:
    """Build the job store selected in ``settings.storage``."""
    if settings.storage is StorageName.SQLITE:
        return SqliteJobStore(settings.db_path, storage, settings.job_ttl_s)
    if settings.storage is StorageName.MEMORY:
        return MemoryJobStore(storage, settings.job_ttl_s)
    raise ValueError(f"unknown storage: {settings.storage}")

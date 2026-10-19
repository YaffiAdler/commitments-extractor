"""Temporary per-job storage for uploads and outputs.

Uploaded files contain patient data: they are stored under a generated name
(never the user's original path) and the whole job directory is deleted when
the job is deleted or its TTL expires.
"""
from __future__ import annotations

import re
import shutil
import time
from pathlib import Path
from uuid import uuid4

_JOB_ID = re.compile(r"^[0-9a-f]{32}$")


class TempStorage:
    def __init__(self, root: Path) -> None:
        self._root = root

    def job_dir(self, job_id: str) -> Path:
        if not _JOB_ID.fullmatch(job_id):
            raise ValueError("invalid job id")
        return self._root / job_id

    def save_upload(self, job_id: str, data: bytes, suffix: str = ".pdf") -> Path:
        """Write an uploaded file under a generated name and return its path."""
        directory = self.job_dir(job_id)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{uuid4().hex}{suffix}"
        path.write_bytes(data)
        return path

    def output_path(self, job_id: str, name: str) -> Path:
        directory = self.job_dir(job_id)
        directory.mkdir(parents=True, exist_ok=True)
        return directory / name

    def delete_job(self, job_id: str) -> None:
        shutil.rmtree(self.job_dir(job_id), ignore_errors=True)

    def purge_older_than(self, ttl_s: float, now: float | None = None) -> list[str]:
        """Delete job directories last modified more than ``ttl_s`` seconds ago
        (including ones left over from a previous run); return their ids."""
        if not self._root.is_dir():
            return []
        cutoff = (time.time() if now is None else now) - ttl_s
        expired = [
            d.name for d in self._root.iterdir()
            if d.is_dir() and _JOB_ID.fullmatch(d.name) and d.stat().st_mtime < cutoff
        ]
        for job_id in expired:
            self.delete_job(job_id)
        return expired

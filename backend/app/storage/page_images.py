"""Page images of a job on disk, for the crop endpoint.

Each page is written to the job's temporary directory (``TempStorage``) as a
JPEG at the full resolution of ``PageImage.original``, the coordinate system
of ``Field.bbox``. The images hold patient data and live only as long as the
job directory: they are deleted with the job or by the file TTL, and lost when
a host with an ephemeral disk restarts. They are never stored in the database.
"""
from __future__ import annotations

import re
from pathlib import Path

import cv2
import numpy as np

from app.pipeline.ingest import PageImage
from app.storage.tmp import TempStorage

PAGES_DIR = "pages"
PAGE_SUFFIX = ".jpg"
JPEG_QUALITY = 90
# Stored file names are generated (uuid4 hex + suffix); anything else is refused.
_STORED_STEM = re.compile(r"^[0-9a-f]{32}$")


class PageImageError(RuntimeError):
    """A page image could not be written or read back."""


class PageImageStore:
    def __init__(self, storage: TempStorage) -> None:
        self._storage = storage

    def save(self, job_id: str, page: PageImage) -> None:
        if page.original is None:
            raise PageImageError("page has no original image in memory")
        ok, encoded = cv2.imencode(PAGE_SUFFIX, page.original, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        if not ok:
            raise PageImageError("page image could not be encoded")
        path = self._path(job_id, page.file_id, page.page_no)
        path.parent.mkdir(parents=True, exist_ok=True)
        # cv2.imwrite cannot write to non-ASCII paths on Windows, so encode and write separately.
        path.write_bytes(encoded.tobytes())

    def load_jpeg(self, job_id: str, source_file: str, page_no: int) -> bytes | None:
        """The stored page as JPEG bytes, or None if it was never saved or is already deleted."""
        try:
            return self._path(job_id, source_file, page_no).read_bytes()
        except FileNotFoundError:
            return None

    def load(self, job_id: str, source_file: str, page_no: int) -> np.ndarray | None:
        """The stored page (BGR), or None if it was never saved or is already deleted."""
        data = self.load_jpeg(job_id, source_file, page_no)
        if data is None:
            return None
        image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise PageImageError("stored page image cannot be decoded")
        return image

    def _path(self, job_id: str, source_file: str, page_no: int) -> Path:
        stem = Path(source_file).stem
        if not _STORED_STEM.fullmatch(stem) or page_no < 0:
            raise ValueError("invalid stored file name or page")
        return self._storage.job_dir(job_id) / PAGES_DIR / f"{stem}_{page_no}{PAGE_SUFFIX}"

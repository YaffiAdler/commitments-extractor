"""Runtime settings: paths, extraction provider and review thresholds.

Every value can be overridden by an environment variable of the same name in
upper case with the ``CE_`` prefix, e.g. ``CE_PROVIDER=local_vlm``.
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
ENV_PREFIX = "CE_"


class ProviderName(str, Enum):
    """Available extraction providers (see ``app/providers``)."""

    CLAUDE = "claude"
    GEMINI = "gemini"
    LOCAL_VLM = "local_vlm"
    FAKE = "fake"  # fixed dummy data, for tests and offline demos


class StorageName(str, Enum):
    """Available job stores (see ``app/storage``)."""

    SQLITE = "sqlite"  # survives a server restart
    MEMORY = "memory"  # lost on restart


_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


def _env(name: str, default: str) -> str:
    return os.environ.get(ENV_PREFIX + name.upper(), default)


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name, str(default)).strip().lower()
    if raw in _TRUE_VALUES:
        return True
    if raw in _FALSE_VALUES:
        return False
    raise ValueError(f"{ENV_PREFIX}{name.upper()} must be a boolean (1/0, true/false, yes/no, on/off)")


@dataclass(frozen=True)
class PreprocessSettings:
    """Image preprocessing chain (``app/pipeline/preprocess.py``).

    Every step can be switched off with ``CE_PREPROCESS_<STEP>=0``.
    """

    fix_perspective: bool = field(default_factory=lambda: _env_bool("preprocess_perspective", True))
    deskew: bool = field(default_factory=lambda: _env_bool("preprocess_deskew", True))
    fix_orientation: bool = field(default_factory=lambda: _env_bool("preprocess_orientation", True))
    normalize_illumination: bool = field(default_factory=lambda: _env_bool("preprocess_illumination", True))
    resize_for_vlm: bool = field(default_factory=lambda: _env_bool("preprocess_resize", True))
    binarize_for_ocr: bool = field(default_factory=lambda: _env_bool("preprocess_binarize", True))
    # Skew below the minimum is left alone; above the maximum the estimate is
    # not trusted (more likely a misreading than a real scan angle).
    deskew_min_angle_deg: float = field(default_factory=lambda: float(_env("deskew_min_angle_deg", "0.5")))
    deskew_max_angle_deg: float = field(default_factory=lambda: float(_env("deskew_max_angle_deg", "10")))
    # Tesseract OSD orientation confidence below this falls back to trying all 4 rotations.
    osd_min_confidence: float = field(default_factory=lambda: float(_env("osd_min_confidence", "2.0")))
    vlm_max_edge_px: int = field(default_factory=lambda: int(_env("vlm_max_edge_px", "2000")))

    def __post_init__(self) -> None:
        if not 0 <= self.deskew_min_angle_deg < self.deskew_max_angle_deg < 45:
            raise ValueError("deskew angles must satisfy 0 <= min < max < 45")
        if self.osd_min_confidence < 0 or self.vlm_max_edge_px <= 0:
            raise ValueError("osd_min_confidence and vlm_max_edge_px must be positive")


@dataclass(frozen=True)
class Settings:
    form_types_dir: Path = field(default_factory=lambda: Path(_env("form_types_dir", str(BACKEND_DIR / "form_types"))))
    senders_path: Path = field(default_factory=lambda: Path(_env("senders_path", str(BACKEND_DIR / "data" / "senders.json"))))
    tmp_dir: Path = field(default_factory=lambda: Path(_env("tmp_dir", str(Path(tempfile.gettempdir()) / "commitments-extractor"))))
    # DPI for rendering PDF pages that are not a single full-page scan
    # (scans are extracted at their native resolution).
    render_dpi: int = field(default_factory=lambda: int(_env("render_dpi", "250")))
    provider: ProviderName = field(default_factory=lambda: ProviderName(_env("provider", ProviderName.CLAUDE.value)))
    claude_model: str = field(default_factory=lambda: _env("claude_model", "claude-opus-5-5"))
    claude_timeout_s: float = field(default_factory=lambda: float(_env("claude_timeout_s", "60")))
    gemini_model: str = field(default_factory=lambda: _env("gemini_model", "gemini-3.8-flash"))
    # Gemini thinking models take well over a minute on a full scanned page.
    gemini_timeout_s: float = field(default_factory=lambda: float(_env("gemini_timeout_s", "180")))
    local_vlm_url: str = field(default_factory=lambda: _env("local_vlm_url", "http://localhost:11434"))
    # A field with confidence below this threshold is flagged for review.
    review_threshold: float = field(default_factory=lambda: float(_env("review_threshold", "0.85")))

    storage: StorageName = field(default_factory=lambda: StorageName(_env("storage", StorageName.SQLITE.value)))
    # Holds patient data: keep it on a protected disk, never in the repository.
    db_path: Path = field(default_factory=lambda: Path(_env("db_path", str(BACKEND_DIR / "data" / "app.db"))))

    # Jobs and their files are deleted this many seconds after creation.
    job_ttl_s: int = field(default_factory=lambda: int(_env("job_ttl_s", "3600")))
    max_upload_mb: int = field(default_factory=lambda: int(_env("max_upload_mb", "50")))
    max_pages: int = field(default_factory=lambda: int(_env("max_pages", "200")))

    preprocess: PreprocessSettings = field(default_factory=PreprocessSettings)
    # Empty = find ``tesseract`` on PATH.
    tesseract_cmd: str = field(default_factory=lambda: _env("tesseract_cmd", ""))
    ocr_languages: str = field(default_factory=lambda: _env("ocr_languages", "heb+eng"))
    ocr_timeout_s: float = field(default_factory=lambda: float(_env("ocr_timeout_s", "30")))

    def __post_init__(self) -> None:
        if self.render_dpi <= 0:
            raise ValueError("render_dpi must be positive")
        if self.claude_timeout_s <= 0 or self.gemini_timeout_s <= 0 or self.ocr_timeout_s <= 0 or self.job_ttl_s <= 0:
            raise ValueError("timeouts and TTL must be positive")
        if self.max_upload_mb <= 0 or self.max_pages <= 0:
            raise ValueError("upload and page limits must be positive")
        if not 0.0 <= self.review_threshold <= 1.0:
            raise ValueError("review_threshold must be between 0 and 1")

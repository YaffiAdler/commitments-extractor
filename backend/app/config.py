"""Runtime settings: paths, extraction provider and review thresholds.

Every value can be overridden by an environment variable of the same name in
upper case with the ``CE_`` prefix, e.g. ``CE_PROVIDER=local_vlm``. The
exceptions are set by the host and read without a prefix: ``DATABASE_URL``
(here) and ``PORT`` (the Docker image).
"""
from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
ENV_PREFIX = "CE_"
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
# A guessable demo code would make the access check pointless.
MIN_ACCESS_TOKEN_LENGTH = 16
# A browser ``Origin`` value: scheme and host (and port), nothing after.
_ORIGIN = re.compile(r"https?://[^/\s*]+")


class ProviderName(str, Enum):
    """Available extraction providers (see ``app/providers``)."""

    CLAUDE = "claude"
    GEMINI = "gemini"
    LOCAL_VLM = "local_vlm"
    FAKE = "fake"  # fixed dummy data, for tests and offline demos


class LabConnectorName(str, Enum):
    """Available lab system connectors (see ``app/lab_system``)."""

    DEMO = "demo"  # dummy samples from a JSON file, no real connection


class StorageName(str, Enum):
    """Available job stores (see ``app/storage``). Without ``CE_STORAGE`` the
    store is ``postgres`` when ``DATABASE_URL`` is set, else ``sqlite``."""

    MEMORY = "memory"      # no state on disk, lost on restart (tests, docker compose)
    SQLITE = "sqlite"      # local default: a file, survives a restart
    POSTGRES = "postgres"  # deployment: the database at ``DATABASE_URL``


# Read without the ``CE_`` prefix: hosts such as Render set this name.
DATABASE_URL_ENV = "DATABASE_URL"


_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


def _env(name: str, default: str) -> str:
    return os.environ.get(ENV_PREFIX + name.upper(), default)


def _default_storage() -> StorageName:
    explicit = _env("storage", "").strip()
    if explicit:
        return StorageName(explicit)
    return StorageName.POSTGRES if os.environ.get(DATABASE_URL_ENV, "").strip() else StorageName.SQLITE


def _env_list(name: str) -> tuple[str, ...]:
    """A comma-separated variable as a tuple; blanks are dropped, so unset or empty -> ``()``."""
    return tuple(item.strip() for item in _env(name, "").split(",") if item.strip())


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
class ExtractionSettings:
    """How provider calls are made while reading forms (``app/pipeline/calls.py``)."""

    # Provider calls in flight at once, over all forms of a job.
    concurrency: int = field(default_factory=lambda: int(_env("extract_concurrency", "4")))
    # Per call, around the provider's own HTTP timeout (a safety net, so it is longer).
    call_timeout_s: float = field(default_factory=lambda: float(_env("extract_call_timeout_s", "240")))
    # Attempts per call for retryable failures (timeout, rate limit, overload).
    max_attempts: int = field(default_factory=lambda: int(_env("extract_max_attempts", "3")))
    backoff_initial_s: float = field(default_factory=lambda: float(_env("extract_backoff_initial_s", "2")))
    backoff_max_s: float = field(default_factory=lambda: float(_env("extract_backoff_max_s", "30")))

    def __post_init__(self) -> None:
        if self.concurrency < 1 or self.max_attempts < 1:
            raise ValueError("extract concurrency and attempts must be at least 1")
        if self.call_timeout_s <= 0 or not 0 <= self.backoff_initial_s <= self.backoff_max_s:
            raise ValueError("extract timeout must be positive and 0 <= backoff initial <= backoff max")


def _env_float(name: str, default: str) -> float:
    return float(_env(name, default))


@dataclass(frozen=True)
class ConfidenceSettings:
    """Weights and review threshold of the field confidence score
    (``app/pipeline/confidence.py``). Hand-chosen starting values, not
    calibrated: the threshold is a temporary default until there is ground truth.
    """

    # Starting score by how the value was written.
    base_printed: float = field(default_factory=lambda: _env_float("confidence_base_printed", "0.8"))
    base_handwritten: float = field(default_factory=lambda: _env_float("confidence_base_handwritten", "0.55"))
    base_closed_list: float = field(default_factory=lambda: _env_float("confidence_base_closed_list", "0.75"))
    # Added when an independent reading agrees with the model's value.
    ocr_agreement: float = field(default_factory=lambda: _env_float("confidence_ocr_agreement", "0.25"))
    second_reading_agreement: float = field(
        default_factory=lambda: _env_float("confidence_second_reading_agreement", "0.15"))
    # Subtracted when an independent reading disagrees (an addition to the starting formula).
    ocr_disagreement: float = field(default_factory=lambda: _env_float("confidence_ocr_disagreement", "0.3"))
    second_reading_disagreement: float = field(
        default_factory=lambda: _env_float("confidence_second_reading_disagreement", "0.3"))
    # Added per agreeing reading beyond the first, when every extra reading of a name agrees (name second look).
    # Low on purpose: at 0.05 a handwritten name tops out at 0.8 (always reviewed); 0.1 with 3 variants reaches 0.9.
    additional_reading_agreement: float = field(
        default_factory=lambda: _env_float("confidence_additional_reading_agreement", "0.05"))
    # Subtracted once per failed validation rule.
    hard_fail: float = field(default_factory=lambda: _env_float("confidence_hard_fail", "0.3"))
    soft_fail: float = field(default_factory=lambda: _env_float("confidence_soft_fail", "0.1"))
    illegible: float = field(default_factory=lambda: _env_float("confidence_illegible", "0.2"))
    # A field scoring below this is flagged for review.
    review_threshold: float = field(default_factory=lambda: _env_float("confidence_review_threshold", "0.9"))

    def __post_init__(self) -> None:
        for name, value in vars(self).items():
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"confidence {name} must be between 0 and 1")


@dataclass(frozen=True)
class ReviewLevelSettings:
    """How much checking the review screen asks for per field (``app/review_levels.py``): "quick glance",
    "check carefully" or "must verify". The confidence score is not calibrated, so these are review levels,
    not accuracy; like the weights they are hand-chosen until there is ground truth.
    """

    # A value at or above this score (and not flagged by the confidence stage) needs only a quick glance.
    glance_min_confidence: float = field(default_factory=lambda: _env_float("review_level_glance_min", "0.9"))
    # A value below this score must be verified against the form, as must one with a must-verify signal.
    verify_below_confidence: float = field(default_factory=lambda: _env_float("review_level_verify_below", "0.5"))
    # Also show the raw score as a percentage. Off by default: it is not a probability of being right.
    show_confidence_percent: bool = field(default_factory=lambda: _env_bool("show_confidence_percent", False))

    def __post_init__(self) -> None:
        if not 0.0 <= self.verify_below_confidence <= self.glance_min_confidence <= 1.0:
            raise ValueError("review levels need 0 <= verify_below <= glance_min <= 1")


class NameVariant(str, Enum):
    """One extra reading of a name (``app/pipeline/reread_name.py``): a different input or prompt each,
    since the models are not sampled."""

    WIDE = "wide"        # wide padding around the name, colour
    NARROW = "narrow"    # narrow padding, colour
    INK = "ink"          # wide padding, handwriting separated from the printed form (``imaging/ink.py``)
    LETTERS = "letters"  # wide padding, colour, the letter-by-letter prompt


# Extra readings per name at most (2 name fields -> at most 6 extra calls per form).
MAX_NAME_VARIANTS = 3
DEFAULT_NAME_VARIANTS = (NameVariant.WIDE, NameVariant.LETTERS)


def _name_variants() -> tuple[NameVariant, ...]:
    return tuple(NameVariant(item) for item in _env_list("name_reread_variants")) or DEFAULT_NAME_VARIANTS


@dataclass(frozen=True)
class NameRereadSettings:
    """The focused second look at names, handwritten and printed (``app/pipeline/reread_name.py``). Off by default
    until the name evaluation (docs/handwriting_eval.md) shows that it helps."""

    enabled: bool = field(default_factory=lambda: _env_bool("name_reread", False))
    # Comma-separated ``NameVariant`` values, one extra reading (provider call) per name each.
    variants: tuple[NameVariant, ...] = field(default_factory=_name_variants)

    def __post_init__(self) -> None:
        if not 1 <= len(self.variants) <= MAX_NAME_VARIANTS:
            raise ValueError(f"name reread variants must be 1 to {MAX_NAME_VARIANTS}")
        if len(set(self.variants)) != len(self.variants):
            raise ValueError("name reread variants must not repeat")


# Providers that implement ``read_name`` (Claude only; fake for tests).
NAME_REREAD_PROVIDERS = frozenset({ProviderName.CLAUDE, ProviderName.FAKE})


def _segment_weight(name: str, default: str) -> float:
    return _env_float(f"segment_weight_{name}", default)


@dataclass(frozen=True)
class SegmentSettings:
    """Weight of each page-boundary signal (``app/pipeline/segment.py``): how
    often the signal is right on its own. Hand-chosen, not calibrated.
    ``Settings`` keeps ``vlm`` below its ``review_threshold``, so the model's
    answer alone never clears a boundary.
    """

    title: float = field(default_factory=lambda: _segment_weight("title", "0.9"))
    new_central_number: float = field(default_factory=lambda: _segment_weight("new_central_number", "0.9"))
    unread_central_sticker: float = field(default_factory=lambda: _segment_weight("unread_central_sticker", "0.6"))
    received_stamp: float = field(default_factory=lambda: _segment_weight("received_stamp", "0.8"))
    same_central_number: float = field(default_factory=lambda: _segment_weight("same_central_number", "0.9"))
    page_marker: float = field(default_factory=lambda: _segment_weight("page_marker", "0.9"))
    # "2/2" right after "1/2".
    page_marker_pair: float = field(default_factory=lambda: _segment_weight("page_marker_pair", "0.99"))
    first_of_several: float = field(default_factory=lambda: _segment_weight("first_of_several", "0.6"))
    sparse_page: float = field(default_factory=lambda: _segment_weight("sparse_page", "0.9"))
    previous_form_length: float = field(default_factory=lambda: _segment_weight("previous_form_length", "0.7"))
    vlm: float = field(default_factory=lambda: _segment_weight("vlm", "0.8"))
    # The model's answer contradicts itself (same_form vs. page_b_type).
    vlm_inconsistent: float = field(default_factory=lambda: _segment_weight("vlm_inconsistent", "0.5"))

    def __post_init__(self) -> None:
        for name, value in vars(self).items():
            if not 0.0 <= value < 1.0:
                raise ValueError(f"segment weight {name} must be in [0, 1)")


class MultiValueMode(str, Enum):
    """How the list fields (central / commitment numbers) are written to Excel."""

    JOINED = "joined"    # one cell, values separated by "; "
    COLUMNS = "columns"  # one cell per value, as many columns as the longest list in the job


# Column keys the Excel export knows (``app/export/xlsx.py`` maps them to Hebrew headers).
EXPORT_COLUMN_KEYS = ("first_name", "last_name", "id_number", "central_numbers", "received_date", "sender",
                      "commitment_numbers", "review_notes", "source_file", "pages")
# All but ``pages`` (1-based page numbers of the form inside its file), which is opt-in.
DEFAULT_EXPORT_COLUMNS = tuple(key for key in EXPORT_COLUMN_KEYS if key != "pages")


@dataclass(frozen=True)
class ExportSettings:
    """Layout of the Excel export (``app/export/xlsx.py``)."""

    # Comma-separated column keys, in order (any subset of ``EXPORT_COLUMN_KEYS``).
    columns: tuple[str, ...] = field(
        default_factory=lambda: _env_list("export_columns") or DEFAULT_EXPORT_COLUMNS)
    multi_values: MultiValueMode = field(
        default_factory=lambda: MultiValueMode(_env("export_multi_values", MultiValueMode.JOINED.value)))

    def __post_init__(self) -> None:
        unknown = [key for key in self.columns if key not in EXPORT_COLUMN_KEYS]
        if unknown:
            raise ValueError(f"unknown export columns {unknown}; known: {', '.join(EXPORT_COLUMN_KEYS)}")
        if len(set(self.columns)) != len(self.columns):
            raise ValueError("export columns must not repeat")


@dataclass(frozen=True)
class Settings:
    form_types_dir: Path = field(default_factory=lambda: Path(_env("form_types_dir", str(BACKEND_DIR / "form_types"))))
    senders_path: Path = field(default_factory=lambda: Path(_env("senders_path", str(BACKEND_DIR / "data" / "senders.json"))))
    tmp_dir: Path = field(default_factory=lambda: Path(_env("tmp_dir", str(Path(tempfile.gettempdir()) / "commitments-extractor"))))
    # DPI for rendering PDF pages that are not a single full-page scan
    # (scans are extracted at their native resolution unless ``downscale_scans``).
    render_dpi: int = field(default_factory=lambda: int(_env("render_dpi", "250")))
    # Scale scans finer than ``render_dpi`` down to it as they are loaded (less memory per page):
    # full-page PDF scans, and images whose DPI metadata says they are finer.
    downscale_scans: bool = field(default_factory=lambda: _env_bool("downscale_scans", False))
    provider: ProviderName = field(default_factory=lambda: ProviderName(_env("provider", ProviderName.CLAUDE.value)))
    claude_model: str = field(default_factory=lambda: _env("claude_model", "claude-opus-5-5"))
    claude_timeout_s: float = field(default_factory=lambda: float(_env("claude_timeout_s", "60")))
    # Log the shape of every provider request and answer (sizes, types, status; never values or text).
    debug_provider: bool = field(default_factory=lambda: _env_bool("debug_provider", False))
    gemini_model: str = field(default_factory=lambda: _env("gemini_model", "gemini-3.8-flash"))
    # Gemini thinking models take well over a minute on a full scanned page.
    gemini_timeout_s: float = field(default_factory=lambda: float(_env("gemini_timeout_s", "180")))
    local_vlm_url: str = field(default_factory=lambda: _env("local_vlm_url", "http://localhost:11434"))
    local_vlm_model: str = field(default_factory=lambda: _env("local_vlm_model", "qwen2.5vl:7b"))
    local_vlm_timeout_s: float = field(default_factory=lambda: float(_env("local_vlm_timeout_s", "300")))
    prompts_dir: Path = field(default_factory=lambda: Path(_env("prompts_dir", str(BACKEND_DIR / "prompts"))))
    prompt_version: str = field(default_factory=lambda: _env("prompt_version", "v1"))
    # A form boundary scoring below this is flagged ``split_uncertain`` (fields: ``confidence.review_threshold``).
    review_threshold: float = field(default_factory=lambda: float(_env("review_threshold", "0.85")))

    storage: StorageName = field(default_factory=_default_storage)
    # Only used with ``storage=sqlite``. Holds patient data: keep it on a protected disk, never in the repository.
    db_path: Path = field(default_factory=lambda: Path(_env("db_path", str(BACKEND_DIR / "data" / "app.db"))))
    # Only used with ``storage=postgres``. Holds the password: never logged (left out of repr).
    database_url: str = field(default_factory=lambda: os.environ.get(DATABASE_URL_ENV, "").strip(), repr=False)
    # Postgres connections kept open at most, and seconds to wait for one (connect or a free pooled one).
    db_pool_size: int = field(default_factory=lambda: int(_env("db_pool_size", "5")))
    db_timeout_s: float = field(default_factory=lambda: float(_env("db_timeout_s", "10")))

    # Jobs (database rows) are deleted this many seconds after creation.
    job_ttl_s: int = field(default_factory=lambda: int(_env("job_ttl_s", "3600")))
    # Temporary files of a job (upload, page images, export) are deleted this many seconds
    # after the job directory was last written, or with the job, whichever comes first.
    file_ttl_s: int = field(default_factory=lambda: int(_env("file_ttl_s", "3600")))
    # Per uploaded file; one upload (``POST /jobs``) holds at most ``max_files`` files.
    max_upload_mb: int = field(default_factory=lambda: int(_env("max_upload_mb", "50")))
    max_files: int = field(default_factory=lambda: int(_env("max_files", "20")))
    max_pages: int = field(default_factory=lambda: int(_env("max_pages", "200")))
    # Pixels per page image (width x height); also set as PIL's ``Image.MAX_IMAGE_PIXELS``.
    # 50M covers A3 at 400 DPI and keeps one RGB copy around 150 MB.
    max_image_pixels: int = field(default_factory=lambda: int(_env("max_image_pixels", "50000000")))
    # Pixels of all pages of one file once loaded (after ``downscale_scans``), checked at upload and
    # before the first page is decoded. The default (about 1.5 GB as BGR) only stops extreme files.
    max_file_pixels: int = field(default_factory=lambda: int(_env("max_file_pixels", "500000000")))
    # Jobs processed at once; further uploads wait as ``pending``.
    max_concurrent_jobs: int = field(default_factory=lambda: int(_env("max_concurrent_jobs", "2")))
    # Level of the app's logs on stdout (DEBUG, INFO, WARNING, ERROR, CRITICAL).
    log_level: str = field(default_factory=lambda: _env("log_level", "INFO").strip().upper())
    # Browser origins allowed to call the API (e.g. the Vercel frontend), comma-separated,
    # each exactly ``scheme://host[:port]``; ``*`` is refused.
    # Empty = no CORS headers at all (same-origin use, e.g. through the Vite proxy).
    allowed_origins: tuple[str, ...] = field(default_factory=lambda: _env_list("allowed_origins"))
    # Pages over all files of one upload, counted before the job is created (per file: ``max_pages``).
    max_job_pages: int = field(default_factory=lambda: int(_env("max_job_pages", "200")))

    # Shared access code of the demo, sent as ``Authorization: Bearer <token>`` (or ``?access_token=``
    # on GET, for EventSource). Empty = no check (local use). A secret: never logged (left out of repr).
    demo_access_token: str = field(default_factory=lambda: _env("demo_access_token", "").strip(), repr=False)
    # Refuse to start without ``demo_access_token`` (set on the deployment, so a forgotten token fails closed).
    require_access_token: bool = field(default_factory=lambda: _env_bool("require_access_token", False))
    # Requests per client IP per minute (``/health`` excluded), and uploads (``POST /jobs``) per hour. 0 = off.
    rate_limit_per_minute: int = field(default_factory=lambda: int(_env("rate_limit_per_minute", "300")))
    upload_limit_per_hour: int = field(default_factory=lambda: int(_env("upload_limit_per_hour", "30")))
    # Reverse proxies in front of the app that append to ``X-Forwarded-For``. 0 = the client IP is the
    # socket peer; n = the n-th address from the right (proxies append, so a client cannot forge it).
    trusted_proxy_hops: int = field(default_factory=lambda: int(_env("trusted_proxy_hops", "0")))
    # Seconds between purges of expired jobs and files while the server runs.
    purge_interval_s: float = field(default_factory=lambda: float(_env("purge_interval_s", "300")))
    # Delete every job directory under ``tmp_dir`` at startup and at shutdown.
    clear_tmp_on_restart: bool = field(default_factory=lambda: _env_bool("clear_tmp_on_restart", True))

    preprocess: PreprocessSettings = field(default_factory=PreprocessSettings)
    extraction: ExtractionSettings = field(default_factory=ExtractionSettings)
    confidence: ConfidenceSettings = field(default_factory=ConfidenceSettings)
    review_levels: ReviewLevelSettings = field(default_factory=ReviewLevelSettings)
    segment: SegmentSettings = field(default_factory=SegmentSettings)
    export: ExportSettings = field(default_factory=ExportSettings)
    name_reread: NameRereadSettings = field(default_factory=NameRereadSettings)
    # Empty = find ``tesseract`` on PATH.
    tesseract_cmd: str = field(default_factory=lambda: _env("tesseract_cmd", ""))
    ocr_languages: str = field(default_factory=lambda: _env("ocr_languages", "heb+eng"))
    ocr_timeout_s: float = field(default_factory=lambda: float(_env("ocr_timeout_s", "30")))

    # The lab system the job is checked against (``GET /jobs/{id}/lab-match``); only a demo exists.
    lab_connector: LabConnectorName = field(
        default_factory=lambda: LabConnectorName(_env("lab_connector", LabConnectorName.DEMO.value)))
    lab_demo_samples_path: Path = field(default_factory=lambda: Path(
        _env("lab_demo_samples_path", str(BACKEND_DIR / "data" / "demo_lab_samples.json"))))
    # Samples are queried from the job's earliest to its latest received date, widened by this many days.
    lab_match_margin_days: int = field(default_factory=lambda: int(_env("lab_match_margin_days", "7")))

    def __post_init__(self) -> None:
        if self.render_dpi <= 0:
            raise ValueError("render_dpi must be positive")
        if min(self.claude_timeout_s, self.gemini_timeout_s, self.local_vlm_timeout_s, self.ocr_timeout_s,
               self.job_ttl_s, self.file_ttl_s, self.db_timeout_s) <= 0:
            raise ValueError("timeouts and TTL must be positive")
        if min(self.max_upload_mb, self.max_files, self.max_pages, self.max_image_pixels, self.max_file_pixels,
               self.max_concurrent_jobs, self.db_pool_size) <= 0:
            raise ValueError("upload, file, page, pixel, job and pool limits must be positive")
        if self.storage is StorageName.POSTGRES and not self.database_url:
            raise ValueError(f"storage postgres needs {DATABASE_URL_ENV}")
        if not 0.0 <= self.review_threshold <= 1.0:
            raise ValueError("review_threshold must be between 0 and 1")
        if self.segment.vlm >= self.review_threshold:
            raise ValueError("segment weight vlm must stay below review_threshold")
        if self.lab_match_margin_days < 0:
            raise ValueError("lab_match_margin_days must not be negative")
        if self.name_reread.enabled and self.provider not in NAME_REREAD_PROVIDERS:
            raise ValueError(f"{ENV_PREFIX}NAME_REREAD needs provider claude (or fake), not {self.provider.value}")
        if self.log_level not in LOG_LEVELS:
            raise ValueError(f"log_level must be one of {', '.join(LOG_LEVELS)}")
        self._check_demo_protection()

    def _check_demo_protection(self) -> None:
        bad_origins = [origin for origin in self.allowed_origins if not _ORIGIN.fullmatch(origin)]
        if bad_origins:
            # The values are URLs, not secrets: naming them makes a typo on the host easy to find.
            raise ValueError(f"{ENV_PREFIX}ALLOWED_ORIGINS must list scheme://host[:port] origins without a path "
                             f"or trailing slash ('*' is not allowed): {bad_origins}")
        if self.require_access_token and not self.demo_access_token:
            raise ValueError(f"{ENV_PREFIX}REQUIRE_ACCESS_TOKEN is on but {ENV_PREFIX}DEMO_ACCESS_TOKEN is empty")
        if self.demo_access_token and len(self.demo_access_token) < MIN_ACCESS_TOKEN_LENGTH:
            raise ValueError(f"{ENV_PREFIX}DEMO_ACCESS_TOKEN must be at least {MIN_ACCESS_TOKEN_LENGTH} characters")
        if min(self.rate_limit_per_minute, self.upload_limit_per_hour, self.trusted_proxy_hops) < 0:
            raise ValueError("rate limits and trusted_proxy_hops must not be negative")
        if self.max_job_pages <= 0 or self.purge_interval_s <= 0:
            raise ValueError("max_job_pages and purge_interval_s must be positive")

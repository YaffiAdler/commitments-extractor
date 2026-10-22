import pytest

from app.config import (
    DEFAULT_EXPORT_COLUMNS,
    ConfidenceSettings,
    MultiValueMode,
    ProviderName,
    SegmentSettings,
    Settings,
    StorageName,
)


def test_defaults_are_valid():
    settings = Settings()
    assert settings.provider is ProviderName.CLAUDE
    assert settings.form_types_dir.is_dir()
    assert settings.senders_path.is_file()


def test_env_override(monkeypatch):
    monkeypatch.setenv("CE_PROVIDER", "local_vlm")
    monkeypatch.setenv("CE_REVIEW_THRESHOLD", "0.5")
    monkeypatch.setenv("CE_SEGMENT_WEIGHT_VLM", "0.4")  # must stay below the split threshold
    settings = Settings()
    assert settings.provider is ProviderName.LOCAL_VLM
    assert settings.review_threshold == 0.5


def test_invalid_threshold_rejected(monkeypatch):
    monkeypatch.setenv("CE_REVIEW_THRESHOLD", "1.5")
    with pytest.raises(ValueError):
        Settings()


def test_preprocess_steps_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("CE_PREPROCESS_DESKEW", "0")
    monkeypatch.setenv("CE_PREPROCESS_PERSPECTIVE", "off")
    settings = Settings()
    assert not settings.preprocess.deskew and not settings.preprocess.fix_perspective
    assert settings.preprocess.fix_orientation and settings.preprocess.binarize_for_ocr


def test_invalid_boolean_rejected(monkeypatch):
    monkeypatch.setenv("CE_PREPROCESS_RESIZE", "maybe")
    with pytest.raises(ValueError):
        Settings()


def test_invalid_page_limit_rejected(monkeypatch):
    monkeypatch.setenv("CE_MAX_PAGES", "0")
    with pytest.raises(ValueError):
        Settings()


def test_confidence_defaults_are_the_starting_formula():
    weights = Settings().confidence
    assert (weights.base_printed, weights.base_handwritten, weights.base_closed_list) == (0.8, 0.55, 0.75)
    assert (weights.ocr_agreement, weights.second_reading_agreement) == (0.25, 0.15)
    assert (weights.hard_fail, weights.soft_fail, weights.illegible) == (0.3, 0.1, 0.2)
    assert weights.review_threshold == 0.9


def test_confidence_weights_from_env(monkeypatch):
    monkeypatch.setenv("CE_CONFIDENCE_SOFT_FAIL", "0.05")
    monkeypatch.setenv("CE_CONFIDENCE_REVIEW_THRESHOLD", "0.7")
    weights = Settings().confidence
    assert weights.soft_fail == 0.05 and weights.review_threshold == 0.7
    assert Settings().review_threshold == 0.85  # the split threshold is separate


@pytest.mark.parametrize("name", ["base_printed", "illegible", "review_threshold"])
def test_confidence_weights_must_be_in_range(name):
    with pytest.raises(ValueError):
        ConfidenceSettings(**{name: 1.5})


def test_allowed_origins_default_empty(monkeypatch):
    monkeypatch.delenv("CE_ALLOWED_ORIGINS", raising=False)
    assert Settings().allowed_origins == ()


def test_allowed_origins_comma_separated(monkeypatch):
    monkeypatch.setenv("CE_ALLOWED_ORIGINS", " https://a.example.org , ,https://b.example.org,")
    assert Settings().allowed_origins == ("https://a.example.org", "https://b.example.org")


def test_segment_weight_defaults():
    weights = Settings().segment
    assert (weights.title, weights.page_marker_pair, weights.vlm, weights.vlm_inconsistent) == (0.9, 0.99, 0.8, 0.5)


def test_segment_weights_from_env(monkeypatch):
    monkeypatch.setenv("CE_SEGMENT_WEIGHT_SPARSE_PAGE", "0.75")
    assert Settings().segment.sparse_page == 0.75


@pytest.mark.parametrize("value", ["-0.1", "1.0"])
def test_segment_weights_must_be_in_range(monkeypatch, value):
    monkeypatch.setenv("CE_SEGMENT_WEIGHT_TITLE", value)
    with pytest.raises(ValueError):
        SegmentSettings()


def test_vlm_weight_must_stay_below_split_threshold(monkeypatch):
    monkeypatch.setenv("CE_SEGMENT_WEIGHT_VLM", "0.85")
    with pytest.raises(ValueError, match="vlm"):
        Settings()


def test_job_limits_and_log_level(monkeypatch):
    monkeypatch.setenv("CE_MAX_CONCURRENT_JOBS", "3")
    monkeypatch.setenv("CE_MAX_IMAGE_PIXELS", "1000")
    monkeypatch.setenv("CE_LOG_LEVEL", "debug")
    settings = Settings()
    assert (settings.max_concurrent_jobs, settings.max_image_pixels, settings.log_level) == (3, 1000, "DEBUG")


@pytest.mark.parametrize("name, value", [("CE_MAX_CONCURRENT_JOBS", "0"), ("CE_MAX_IMAGE_PIXELS", "0"),
                                         ("CE_LOG_LEVEL", "LOUD")])
def test_invalid_job_limits_and_log_level_rejected(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError):
        Settings()


def test_export_defaults(monkeypatch):
    monkeypatch.delenv("CE_EXPORT_COLUMNS", raising=False)
    monkeypatch.delenv("CE_EXPORT_MULTI_VALUES", raising=False)
    export = Settings().export
    assert export.columns == DEFAULT_EXPORT_COLUMNS
    assert "pages" not in export.columns
    assert export.multi_values is MultiValueMode.JOINED


def test_export_settings_from_env(monkeypatch):
    monkeypatch.setenv("CE_EXPORT_COLUMNS", "id_number, central_numbers,pages")
    monkeypatch.setenv("CE_EXPORT_MULTI_VALUES", "columns")
    export = Settings().export
    assert export.columns == ("id_number", "central_numbers", "pages")
    assert export.multi_values is MultiValueMode.COLUMNS


@pytest.mark.parametrize("name, value", [("CE_EXPORT_COLUMNS", "id_number,unknown"),
                                         ("CE_EXPORT_MULTI_VALUES", "rows")])
def test_invalid_export_settings_rejected(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError):
        Settings()


def test_storage_is_sqlite_without_database_url(monkeypatch):
    monkeypatch.delenv("CE_STORAGE")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert Settings().storage is StorageName.SQLITE


def test_storage_is_postgres_with_database_url(monkeypatch):
    monkeypatch.delenv("CE_STORAGE")
    monkeypatch.setenv("DATABASE_URL", "postgresql://ce:dummy-secret@db.example.org/ce")
    settings = Settings()
    assert settings.storage is StorageName.POSTGRES
    assert "dummy-secret" not in repr(settings)


def test_ce_storage_overrides_database_url(monkeypatch):
    monkeypatch.setenv("CE_STORAGE", "memory")
    monkeypatch.setenv("DATABASE_URL", "postgresql://ce@db.example.org/ce")
    assert Settings().storage is StorageName.MEMORY


def test_postgres_without_database_url_is_rejected(monkeypatch):
    monkeypatch.setenv("CE_STORAGE", "postgres")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValueError):
        Settings()


@pytest.mark.parametrize(("name", "value"), [("CE_FILE_TTL_S", "0"), ("CE_DB_POOL_SIZE", "0"), ("CE_DB_TIMEOUT_S", "0")])
def test_invalid_storage_limits_rejected(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError):
        Settings()

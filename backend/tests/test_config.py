import pytest

from app.config import ProviderName, Settings


def test_defaults_are_valid():
    settings = Settings()
    assert settings.provider is ProviderName.CLAUDE
    assert settings.form_types_dir.is_dir()
    assert settings.senders_path.is_file()


def test_env_override(monkeypatch):
    monkeypatch.setenv("CE_PROVIDER", "local_vlm")
    monkeypatch.setenv("CE_REVIEW_THRESHOLD", "0.5")
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

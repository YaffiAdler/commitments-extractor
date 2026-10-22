import pytest

from app.config import ConfidenceSettings, ProviderName, Settings


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

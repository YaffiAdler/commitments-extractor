from app.config import ReviewLevelSettings
from app.pipeline import extract, ocr_check, reread, reread_name
from app.pipeline.confidence import HARD_FAIL_REASONS, MUST_VERIFY_REASONS, SOFT_FAIL_REASONS
from app.pipeline.reason_texts import SINGLE_READING
from app.review_levels import review_level_config


def test_config_carries_the_settings_and_sorted_codes():
    config = review_level_config(ReviewLevelSettings(glance_min_confidence=0.8, verify_below_confidence=0.3,
                                                     show_confidence_percent=True))
    assert (config.glance_min_confidence, config.verify_below_confidence, config.show_confidence_percent) == (
        0.8, 0.3, True)
    assert config.must_verify_reasons == sorted(MUST_VERIFY_REASONS)


def test_must_verify_covers_disagreement_hard_fails_and_partial_values():
    assert HARD_FAIL_REASONS <= MUST_VERIFY_REASONS
    assert {ocr_check.OCR_DISAGREES, reread.REREAD_DISAGREES, extract.ILLEGIBLE} <= MUST_VERIFY_REASONS


def test_soft_signals_alone_do_not_force_must_verify():
    # A failed check digit or a single reading lowers the score; the threshold decides, not the code.
    assert not (SOFT_FAIL_REASONS - {reread_name.NAME_FILLED_FROM_SECOND_LOOK}) & MUST_VERIFY_REASONS
    assert SINGLE_READING not in MUST_VERIFY_REASONS

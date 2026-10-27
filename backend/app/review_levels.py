"""What the review screen needs to tell the reviewer how much checking a field
needs: the thresholds of ``config.ReviewLevelSettings`` and the reason codes
that always mean "must verify". The level itself is derived in the frontend
(``frontend/src/review.ts``) from each field's score and reasons.
"""
from __future__ import annotations

from pydantic import BaseModel

from app.config import ReviewLevelSettings
from app.pipeline.confidence import MUST_VERIFY_REASONS


class ReviewLevelConfig(BaseModel):
    glance_min_confidence: float
    verify_below_confidence: float
    must_verify_reasons: list[str]
    show_confidence_percent: bool


def review_level_config(settings: ReviewLevelSettings) -> ReviewLevelConfig:
    return ReviewLevelConfig(
        glance_min_confidence=settings.glance_min_confidence,
        verify_below_confidence=settings.verify_below_confidence,
        must_verify_reasons=sorted(MUST_VERIFY_REASONS),
        show_confidence_percent=settings.show_confidence_percent,
    )

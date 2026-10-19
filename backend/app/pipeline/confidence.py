"""Stage 7: combine signals into a confidence score and set ``needs_review``.

Signals are recorded as codes in ``Field.reasons`` (e.g. ``ocr_low_score``,
``handwritten``, ``id_checksum_failed``, ``conflicting_candidates``).
"""
from __future__ import annotations

from app.schemas import Commitment


def score(commitment: Commitment, review_threshold: float) -> Commitment:
    """Set ``confidence`` per field and flag fields below ``review_threshold``."""
    raise NotImplementedError

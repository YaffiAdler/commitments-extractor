"""Stage 5: read the raw field values of one form through a provider."""
from __future__ import annotations

from app.pipeline.ingest import PageImage
from app.providers.base import ExtractionProvider
from app.schemas import Commitment, FormType


def extract(pages: list[PageImage], form_type: FormType | None, provider: ExtractionProvider) -> Commitment:
    """Extract raw values; normalization and scoring happen in later stages."""
    commitment = provider.extract_form(pages, form_type)
    commitment.form_type_id = form_type.id if form_type else None
    return commitment

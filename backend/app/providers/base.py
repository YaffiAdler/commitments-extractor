"""The interface every extraction provider implements.

The pipeline depends only on this interface, so providers can be swapped by
configuration and replaced with fakes in tests.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

import numpy as np

from app.pipeline.ingest import PageImage
from app.schemas import FormClassification, FormReading, FormType, NameReading, NameWording, NumberReading, PageRelation


class ProviderError(RuntimeError):
    """A provider call failed. The message never contains field values."""


class RetryableProviderError(ProviderError):
    """A provider call failed in a way that may pass on a later attempt
    (timeout, rate limit, overload, connection error)."""


@runtime_checkable
class ExtractionProvider(Protocol):
    name: str

    def extract_form(self, pages: list[PageImage], form_type: FormType | None) -> FormReading:
        """Read the raw field values of one form, each with its box on the
        page, whether it is legible, how it was written and an optional note.

        ``form_type`` (when known) supplies labels and hints for each field.
        Fields that are not found must be left empty, never guessed; checking
        and scoring belong to the pipeline.
        """
        ...

    def read_number(self, crop: np.ndarray) -> NumberReading:
        """Read the number on one enlarged crop (a second look at a single
        value): digits only, or None if no number can be read with certainty."""
        ...

    def read_name(self, crop: np.ndarray, field_name: str, wording: NameWording) -> NameReading:
        """Read the handwritten name (``first_name`` or ``last_name``) on one
        enlarged crop, asked in ``wording``: up to 3 candidates, most likely
        first, with uncertain letters marked; none if no name can be read.
        Nothing is completed or corrected. Only Claude (and the fake) implement
        it; the others raise ``ProviderError``."""
        ...

    def classify(self, pages: list[PageImage], form_types: dict[str, FormType]) -> FormClassification:
        """Name the form type of ``pages`` (the first page of one form): one of
        the ``form_types`` ids or ``"unknown"``, plus the sender exactly as
        printed (not matched to the closed list — the pipeline does that)."""
        ...

    def compare_pages(self, page_a: PageImage, page_b: PageImage) -> PageRelation:
        """Say whether page B (right after page A in a file) belongs to the
        same form as A, and whether B starts a form, continues one or is
        irrelevant. Used for page pairs the segmentation rules cannot decide."""
        ...

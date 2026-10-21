"""The interface every extraction provider implements.

The pipeline depends only on this interface, so providers can be swapped by
configuration and replaced with fakes in tests.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.pipeline.ingest import PageImage
from app.schemas import Commitment, FormClassification, FormType, PageRelation


class ProviderError(RuntimeError):
    """A provider call failed. The message never contains field values."""


@runtime_checkable
class ExtractionProvider(Protocol):
    name: str

    def extract_form(self, pages: list[PageImage], form_type: FormType | None) -> Commitment:
        """Read the raw field values of one form.

        ``form_type`` (when known) supplies labels and hints for each field.
        Fields that are not found must be left empty, never guessed, and the
        provider must not set ``confidence`` — that is computed by the pipeline.
        """
        ...

    def reread_field(self, pages: list[PageImage], field_name: str, form_type: FormType | None) -> list[str]:
        """Read one ``Commitment`` field again; return every raw reading found
        (empty if the field is not on the form)."""
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

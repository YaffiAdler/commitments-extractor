"""Stage 3: split the pages of one file into forms.

Most forms are one page; some span two (Maccabi ONLINE "1/2",
Assuta "Page 1 of 2"). Each returned group becomes one ``FormGroup``.
"""
from __future__ import annotations

from app.pipeline.ingest import PageImage


def segment(pages: list[PageImage]) -> list[list[PageImage]]:
    """Group consecutive pages that belong to the same form."""
    raise NotImplementedError

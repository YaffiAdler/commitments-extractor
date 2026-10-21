"""Extraction through a locally hosted vision-language model, so patient data
does not leave the lab's network."""
from __future__ import annotations

from app.pipeline.ingest import PageImage
from app.schemas import Commitment, FormType, PageRelation


class LocalVlmProvider:
    name = "local_vlm"

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url

    def extract_form(self, pages: list[PageImage], form_type: FormType | None) -> Commitment:
        raise NotImplementedError

    def reread_field(self, pages: list[PageImage], field_name: str, form_type: FormType | None) -> list[str]:
        raise NotImplementedError

    def classify(self, pages: list[PageImage], form_types: dict[str, FormType]) -> str | None:
        raise NotImplementedError

    def compare_pages(self, page_a: PageImage, page_b: PageImage) -> PageRelation:
        raise NotImplementedError

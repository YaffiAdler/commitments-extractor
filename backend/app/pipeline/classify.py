"""Stage 4: decide which ``form_types/*.yaml`` layout a form matches."""
from __future__ import annotations

from pathlib import Path

import yaml

from app.pipeline.ingest import PageImage
from app.schemas import FormType


def load_form_types(form_types_dir: Path) -> dict[str, FormType]:
    """Load and validate every form type definition, keyed by id."""
    form_types: dict[str, FormType] = {}
    for path in sorted(form_types_dir.glob("*.yaml")):
        form_type = FormType.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        if form_type.id != path.stem:
            raise ValueError(f"{path.name}: id {form_type.id!r} does not match the file name")
        form_types[form_type.id] = form_type
    return form_types


def classify(pages: list[PageImage], form_types: dict[str, FormType]) -> str | None:
    """Return the matching form type id, or None if the layout is unknown."""
    raise NotImplementedError

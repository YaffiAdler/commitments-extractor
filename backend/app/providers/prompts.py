"""Versioned prompt files (``backend/prompts/``) and the per-form block built
from a form type's YAML.

The extraction prompt is a fixed template plus one block from the YAML, so
form-specific knowledge stays in ``form_types/*.yaml`` and the wording can be
versioned on its own (see ``backend/prompts/README.md``).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.config import BACKEND_DIR
from app.schemas import FormType

DEFAULT_PROMPTS_DIR = BACKEND_DIR / "prompts"
DEFAULT_PROMPT_VERSION = "v1"
EXTRACT_FILE = "extract_{version}.md"
READ_NUMBER_FILE = "read_number_{version}.md"
FORM_TYPE_PLACEHOLDER = "{{form_type}}"
UNKNOWN_LAYOUT_TEXT = "Unknown layout: rely on the rules above and the usual labels of each field."
YES, NO = "yes", "no"


class PromptError(ValueError):
    """A prompt file is missing or malformed."""


def form_type_block(form_type: FormType | None) -> str:
    """The layout part of the extraction prompt: one line per field with its
    printed labels, whether it may repeat or be handwritten, and the hint."""
    if form_type is None:
        return UNKNOWN_LAYOUT_TEXT
    lines = [f"Layout: {form_type.display_name}"]
    for name, spec in form_type.fields.items():
        labels = ", ".join(f'"{label}"' for label in spec.labels) or "none"
        lines.append(
            f"- {name}: labels {labels}; several possible: {YES if spec.multiple else NO}; "
            f"may be handwritten: {YES if spec.handwritten_possible else NO}. {spec.hint}")
    return "\n".join(lines)


@dataclass(frozen=True)
class Prompts:
    """One version of the prompt files."""

    version: str
    extract_template: str
    read_number: str

    @classmethod
    def load(cls, prompts_dir: Path = DEFAULT_PROMPTS_DIR, version: str = DEFAULT_PROMPT_VERSION) -> "Prompts":
        extract_template = _read(prompts_dir / EXTRACT_FILE.format(version=version))
        if extract_template.count(FORM_TYPE_PLACEHOLDER) != 1:
            raise PromptError(f"extract prompt {version} must contain {FORM_TYPE_PLACEHOLDER} exactly once")
        return cls(version, extract_template, _read(prompts_dir / READ_NUMBER_FILE.format(version=version)))

    def extract(self, form_type: FormType | None) -> str:
        return self.extract_template.replace(FORM_TYPE_PLACEHOLDER, form_type_block(form_type))


def _read(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise PromptError(f"prompt file {path.name} not found") from exc
    if not text:
        raise PromptError(f"prompt file {path.name} is empty")
    return text

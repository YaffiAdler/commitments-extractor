"""The folder format of a name test set.

A set is a directory holding images (whole dummy form pages, PNG/JPEG/TIFF/
WEBP or PDF) and ``labels.csv`` (UTF-8, a BOM as Excel writes it is fine)
with the columns ``image,first_name,last_name,kind``:

* ``image`` — file name relative to the set directory (no absolute path, no
  ``..``), each at most once;
* ``first_name`` / ``last_name`` — the name as written on the page; an empty
  cell means the field is deliberately left blank on that page (the right
  answer is then an empty field);
* ``kind`` — ``synthetic`` (rendered from a font) or ``handwritten`` (written
  by a person and scanned or photographed).

Further columns (e.g. ``writer``, ``pen``) are allowed and ignored. Errors
name the row number only, never a cell value.
"""
from __future__ import annotations

import csv
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePath

LABELS_FILE = "labels.csv"
COLUMNS = ("image", "first_name", "last_name", "kind")
CSV_ENCODING = "utf-8-sig"
FIRST_DATA_ROW = 2  # row 1 is the header, as a spreadsheet numbers them


class SampleKind(str, Enum):
    SYNTHETIC = "synthetic"
    HANDWRITTEN = "handwritten"


class NameSetError(ValueError):
    """The set cannot be used. The message never contains a name."""


@dataclass(frozen=True)
class NameSample:
    """One page of the set and the names written on it."""

    image: str
    first_name: str
    last_name: str
    kind: SampleKind


def load_name_set(directory: Path) -> list[NameSample]:
    """The samples of the set in ``directory``, in CSV order (see the module docstring)."""
    labels = directory / LABELS_FILE
    if not labels.is_file():
        raise NameSetError(f"{LABELS_FILE} not found in the set directory")
    with labels.open(encoding=CSV_ENCODING, newline="") as f:
        reader = csv.DictReader(f)
        missing = [column for column in COLUMNS if column not in (reader.fieldnames or [])]
        if missing:
            raise NameSetError(f"{LABELS_FILE} lacks the columns {', '.join(missing)}")
        samples = [_sample(row, row_no, directory) for row_no, row in enumerate(reader, FIRST_DATA_ROW)]
    if not samples:
        raise NameSetError(f"{LABELS_FILE} has no rows")
    _check_unique(samples)
    return samples


def write_name_set(directory: Path, samples: Iterable[NameSample]) -> Path:
    """Write ``labels.csv`` for ``samples`` (the images must already be in ``directory``)."""
    path = directory / LABELS_FILE
    with path.open("w", encoding=CSV_ENCODING, newline="") as f:
        writer = csv.writer(f)
        writer.writerow(COLUMNS)
        for sample in samples:
            writer.writerow((sample.image, sample.first_name, sample.last_name, sample.kind.value))
    return path


def _sample(row: dict[str, str | None], row_no: int, directory: Path) -> NameSample:
    image = (row["image"] or "").strip()
    if not _is_relative_name(image):
        raise NameSetError(f"row {row_no}: image must be a file name inside the set directory")
    if not (directory / image).is_file():
        raise NameSetError(f"row {row_no}: image file not found")
    try:
        kind = SampleKind((row["kind"] or "").strip().lower())
    except ValueError:
        raise NameSetError(f"row {row_no}: kind must be one of {', '.join(k.value for k in SampleKind)}") from None
    return NameSample(image=image, first_name=(row["first_name"] or "").strip(),
                      last_name=(row["last_name"] or "").strip(), kind=kind)


def _is_relative_name(image: str) -> bool:
    path = PurePath(image)
    return bool(image) and not path.is_absolute() and not path.anchor and ".." not in path.parts


def _check_unique(samples: list[NameSample]) -> None:
    seen: set[str] = set()
    for row_no, sample in enumerate(samples, FIRST_DATA_ROW):
        key = sample.image.lower()  # Windows file names are case-insensitive
        if key in seen:
            raise NameSetError(f"row {row_no}: image listed twice")
        seen.add(key)

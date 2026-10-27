"""A list of known first names and surnames as a soft hint.

**Not wired into the pipeline yet** (deferred on request: it is to be added
in a later step, after the list source is chosen). Kept with its tests so it
can be wired in as a stage after normalization (``hint_names``).

The lists are plain UTF-8 text files, one name per line (``#`` starts a
comment); none is bundled. Names are compared with ``hebrew_text.comparable_name``.

For a name with a value:

* listed exactly — ``name_in_list`` (information only: no confidence is added,
  so a name that is not listed is not worse off);
* not listed, but up to ``MAX_SUGGESTIONS`` listed names at Levenshtein
  distance 1 — ``name_list_suggestion`` plus a ``name_list`` signal whose
  ``candidates`` are those names in list order (the reviewer may pick one);
* otherwise nothing.

The value is never replaced, and neither code changes the score (see
``confidence``): many real names are in no list, and a near-miss of a common
name may well be the right, rarer name. Pure apart from ``load_name_list``.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rapidfuzz import process
from rapidfuzz.distance import Levenshtein

from app.hebrew_text import comparable_name
from app.schemas import Commitment, Field, FieldSignal, SignalSource

NAME_IN_LIST = "name_in_list"
NAME_LIST_SUGGESTION = "name_list_suggestion"

MAX_SUGGESTIONS = 3
MAX_DISTANCE = 1
COMMENT = "#"
_LIST_ENCODING = "utf-8-sig"


class NameListError(ValueError):
    """A name list file cannot be used. The message never quotes a line."""


@dataclass(frozen=True)
class NameList:
    """Listed names as written (``names``), and their comparable forms for matching."""

    names: tuple[str, ...]
    keys: tuple[str, ...]
    key_set: frozenset[str]

    @classmethod
    def of(cls, names: list[str]) -> NameList:
        """Blank lines dropped, repeats (by comparable form) kept once, in list order."""
        kept: dict[str, str] = {}
        for name in names:
            key = comparable_name(name)
            if key and key not in kept:
                kept[key] = name.strip()
        return cls(names=tuple(kept.values()), keys=tuple(kept), key_set=frozenset(kept))

    def __contains__(self, value: str) -> bool:
        return comparable_name(value) in self.key_set

    def near(self, value: str) -> list[str]:
        """Listed names at distance 1 from ``value``, in list order, at most ``MAX_SUGGESTIONS``."""
        matches = process.extract(comparable_name(value), self.keys, scorer=Levenshtein.distance,
                                  score_cutoff=MAX_DISTANCE, limit=None)
        indices = sorted(index for _, distance, index in matches if distance == MAX_DISTANCE)
        return [self.names[index] for index in indices[:MAX_SUGGESTIONS]]


@dataclass(frozen=True)
class NameLists:
    first: NameList | None = None
    last: NameList | None = None

    @property
    def enabled(self) -> bool:
        return self.first is not None or self.last is not None


def load_name_list(path: Path | None) -> NameList | None:
    """The list in ``path`` (None for no path)."""
    if path is None:
        return None
    try:
        lines = path.read_text(encoding=_LIST_ENCODING).splitlines()
    except FileNotFoundError:
        raise NameListError(f"name list {path.name} not found") from None
    except UnicodeDecodeError:
        raise NameListError(f"name list {path.name} is not UTF-8") from None
    names = NameList.of([line for line in lines if not line.lstrip().startswith(COMMENT)])
    if not names.names:
        raise NameListError(f"name list {path.name} has no names")
    return names


def hint_names(commitment: Commitment, lists: NameLists) -> None:
    """Add the list outcome to the first and last name of ``commitment`` (in place)."""
    for field, names in ((commitment.first_name, lists.first), (commitment.last_name, lists.last)):
        if names is not None and field.value is not None:
            hint_field(field, names)


def hint_field(field: Field[str], names: NameList) -> None:
    if field.value in names:
        field.reasons.append(NAME_IN_LIST)
        return
    suggestions = names.near(field.value)
    if suggestions:
        field.reasons.append(NAME_LIST_SUGGESTION)
        field.signals.append(FieldSignal(source=SignalSource.NAME_LIST, value=suggestions[0], candidates=suggestions))

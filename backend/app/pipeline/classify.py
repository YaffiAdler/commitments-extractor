"""Stage 4: decide which ``form_types/*.yaml`` layout a form matches and who
sent it.

One provider call per form, on its first page only, returns
``{form_type, sender_raw, evidence}`` (``FormClassification``). Then:

**Form type.** A known id selects that YAML. ``"unknown"`` (or anything that
is not a loaded id) selects ``generic.yaml`` and flags the whole form with
``form_type_unknown``; the form is still extracted, with generic hints.

**Sender** (closed list, ``senders.json``). Decision algorithm:

1. *Normalize* ``sender_raw`` and every canonical name, alias and abbreviation
   the same way: remove niqqud and cantillation marks, remove quote marks
   (gershayim/geresh, so ``רמב"ם`` -> ``רמבם``), turn hyphens and other
   punctuation into spaces, collapse spaces, lower-case Latin letters and drop
   whole generic phrases (``GENERIC_SENDER_PHRASES``: "המרכז הרפואי",
   "קופת חולים", "שירותי בריאות", ...) that many senders share.
2. *Score* the query against every normalized name with rapidfuzz ``WRatio``
   (0..100) and keep the best score per canonical sender. The top entry is
   what ``process.extractOne`` would return; the full ranking is needed for
   step 4.
3. *Not in list:* if the best score is below ``SENDER_MIN_SCORE`` (88), the
   sender stays empty (``sender_not_in_list``).
4. *Ambiguous:* if the runner-up (a *different* canonical sender) is within
   ``SENDER_MIN_GAP`` points of the best, the sender stays empty
   (``sender_ambiguous``) — choosing would be a guess.
5. *Matched* otherwise: ``value`` is the canonical name, ``raw_value`` the
   text as read, ``confidence`` the match score / 100 (one signal; the
   confidence stage may combine it with others). A score below 100 adds
   ``sender_fuzzy_match``.

An empty ``sender_raw`` leaves the sender empty (``not_found``); text that
is empty after normalization (only generic words, e.g. "קופת חולים") counts
as not in the list. When the sender stays empty its ``reasons`` list the best
candidates as ``sender_candidate:<canonical>=<score>``, so a reviewer can
pick one; nothing is ever filled in from them.

``evidence`` is model text that may quote the page: it is never logged or
stored. Sender names are organizations, not patient data, but the raw text is
still not logged.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import yaml
from rapidfuzz import fuzz, process

from app.pipeline.ingest import PageImage
from app.providers.base import ExtractionProvider
from app.schemas import FORM_TYPE_UNKNOWN, Field, FormType, Sender

logger = logging.getLogger(__name__)

GENERIC_FORM_TYPE_ID = "generic"

SENDER_MIN_SCORE = 88.0
# A different sender scoring within this many points of the best one makes the match ambiguous.
SENDER_MIN_GAP = 5.0
MAX_SCORE = 100.0
MAX_LISTED_CANDIDATES = 3

# Reason codes, stored in Field.reasons of the sender.
SENDER_NOT_IN_LIST = "sender_not_in_list"
SENDER_AMBIGUOUS = "sender_ambiguous"
SENDER_FUZZY_MATCH = "sender_fuzzy_match"
SENDER_CANDIDATE_PREFIX = "sender_candidate:"

# Generic words shared by many senders: they say nothing about which one it is,
# and left in they lift every HMO (or hospital) close to the best match.
# Text as printed on forms; normalized like the names before use.
GENERIC_SENDER_PHRASES = (
    "המרכז הרפואי", "מרכז רפואי", "בית החולים", "בית חולים", 'בי"ח',
    "קופת חולים", 'קופ"ח', "שירותי בריאות", "שרותי בריאות",
    "מעבדה מרכזית", "מעבדה שולחת", "מעבדה", 'בע"מ',
)
# Hebrew cantillation marks and niqqud; the maqaf (U+05BE) is a hyphen and becomes a space.
_MARKS = re.compile("[\u0591-\u05BD\u05BF-\u05C7]")
_QUOTES = re.compile("[\"'`\u05F3\u05F4\u2018\u2019\u201C\u201D]")  # incl. geresh/gershayim
_PUNCTUATION = re.compile(r"[^\w\s]|_")  # hyphens (incl. maqaf), brackets, dots, ...
_SPACES = re.compile(r"\s+")


def load_form_types(form_types_dir: Path) -> dict[str, FormType]:
    """Load and validate every form *layout*, keyed by id. ``generic.yaml``
    is a fallback, not a layout, and is left out (see ``load_generic_form_type``)."""
    form_types = {form_type.id: form_type for form_type in _load_all(form_types_dir)}
    form_types.pop(GENERIC_FORM_TYPE_ID, None)
    return form_types


def load_generic_form_type(form_types_dir: Path) -> FormType:
    """The fallback definition used when no layout matches."""
    return _load(form_types_dir / f"{GENERIC_FORM_TYPE_ID}.yaml")


def _load_all(form_types_dir: Path) -> list[FormType]:
    return [_load(path) for path in sorted(form_types_dir.glob("*.yaml"))]


def _load(path: Path) -> FormType:
    form_type = FormType.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    if form_type.id != path.stem:
        raise ValueError(f"{path.name}: id {form_type.id!r} does not match the file name")
    return form_type


# --- Sender matching ----------------------------------------------------------


def _clean(text: str) -> str:
    """Marks and quotes removed, other punctuation as spaces, single spaces, lower case."""
    text = _PUNCTUATION.sub(" ", _QUOTES.sub("", _MARKS.sub("", text)))
    return _SPACES.sub(" ", text).strip().casefold()


_GENERIC_PHRASES = tuple(_clean(phrase) for phrase in GENERIC_SENDER_PHRASES)


def normalize_sender_name(text: str) -> str:
    """Normalize a sender name for fuzzy matching (step 1 of the module docstring)."""
    padded = f" {_clean(text)} "
    for phrase in _GENERIC_PHRASES:
        padded = padded.replace(f" {phrase} ", " ")
    return padded.strip()


class SenderMatchStatus(str, Enum):
    MATCHED = "matched"
    NOT_IN_LIST = "not_in_list"
    AMBIGUOUS = "ambiguous"
    NOT_READ = "not_read"


@dataclass(frozen=True)
class SenderMatch:
    """Outcome of matching one raw sender name against the closed list.
    ``candidates`` are (canonical, score), best first."""

    raw: str | None
    status: SenderMatchStatus
    canonical: str | None = None
    score: float = 0.0
    candidates: tuple[tuple[str, float], ...] = ()

    def to_field(self) -> Field[str]:
        """The ``Commitment.sender`` field for this outcome."""
        if self.status is SenderMatchStatus.MATCHED:
            reasons = [SENDER_FUZZY_MATCH] if self.score < MAX_SCORE else []
            return Field[str](value=self.canonical, raw_value=self.raw, confidence=self.score / MAX_SCORE,
                              needs_review=False, reasons=reasons)
        candidates = [f"{SENDER_CANDIDATE_PREFIX}{name}={score:.0f}" for name, score in self.candidates]
        status_reasons = {SenderMatchStatus.NOT_IN_LIST: [SENDER_NOT_IN_LIST],
                          SenderMatchStatus.AMBIGUOUS: [SENDER_AMBIGUOUS]}
        return Field[str](raw_value=self.raw, reasons=[*status_reasons.get(self.status, []), *candidates])


class SenderMatcher:
    """Matches raw sender names against the closed list (steps 2-5 of the
    module docstring)."""

    def __init__(self, senders: list[Sender], min_score: float = SENDER_MIN_SCORE,
                 min_gap: float = SENDER_MIN_GAP) -> None:
        self._choices = _sender_choices(senders)
        self._min_score = min_score
        self._min_gap = min_gap

    def match(self, sender_raw: str | None) -> SenderMatch:
        if not sender_raw or not sender_raw.strip():
            return SenderMatch(raw=None, status=SenderMatchStatus.NOT_READ)
        query = normalize_sender_name(sender_raw)
        ranked = self._ranked(query) if query else []
        listed = tuple(ranked[:MAX_LISTED_CANDIDATES])
        if not ranked or ranked[0][1] < self._min_score:
            return SenderMatch(raw=sender_raw, status=SenderMatchStatus.NOT_IN_LIST, candidates=listed)
        best_name, best_score = ranked[0]
        if len(ranked) > 1 and best_score - ranked[1][1] < self._min_gap:
            return SenderMatch(raw=sender_raw, status=SenderMatchStatus.AMBIGUOUS, score=best_score,
                               candidates=listed)
        return SenderMatch(raw=sender_raw, status=SenderMatchStatus.MATCHED, canonical=best_name,
                           score=best_score, candidates=listed)

    def _ranked(self, query: str) -> list[tuple[str, float]]:
        """Best score per canonical sender, highest first."""
        best: dict[str, float] = {}
        for name, score, _ in process.extract(query, list(self._choices), scorer=fuzz.WRatio,
                                              processor=None, limit=None):
            canonical = self._choices[name]
            best[canonical] = max(score, best.get(canonical, 0.0))
        return sorted(best.items(), key=lambda item: item[1], reverse=True)


def _sender_choices(senders: list[Sender]) -> dict[str, str]:
    """Normalized name -> canonical name, over canonical names, aliases and
    abbreviations. Two senders that normalize to the same name are a data error."""
    choices: dict[str, str] = {}
    for sender in senders:
        for name in (sender.canonical, *sender.aliases, *sender.abbreviations):
            key = normalize_sender_name(name)
            if not key:
                continue
            if choices.setdefault(key, sender.canonical) != sender.canonical:
                raise ValueError(f"senders.json: {sender.canonical!r} and {choices[key]!r} share the name {key!r}")
    return choices


# --- Classification -----------------------------------------------------------


@dataclass(frozen=True)
class Classification:
    """The layout to extract with, the matched sender and form-level flags."""

    form_type: FormType  # the matched layout, or the generic one
    known: bool
    sender: SenderMatch
    form_reasons: tuple[str, ...]


def classify(pages: list[PageImage], form_types: dict[str, FormType], generic: FormType,
             matcher: SenderMatcher, provider: ExtractionProvider) -> Classification:
    """Classify one form from its first page (see the module docstring)."""
    answer = provider.classify(pages[:1], form_types)
    form_type = form_types.get(answer.form_type)
    sender = matcher.match(answer.sender_raw)
    logger.info("file %s page %d: form type %s, sender %s", pages[0].file_id, pages[0].page_no,
                form_type.id if form_type else GENERIC_FORM_TYPE_ID, sender.status.value)
    if form_type is None:
        return Classification(generic, known=False, sender=sender, form_reasons=(FORM_TYPE_UNKNOWN,))
    return Classification(form_type, known=True, sender=sender, form_reasons=())

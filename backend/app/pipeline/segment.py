"""Stage 3: split the pages of one file into forms.

Most forms are one page; some span two (Maccabi ONLINE "1/2", Assuta
"Page 1 of 2"). Every boundary between two adjacent pages is decided in
three steps:

1. **Fingerprint** each page from one OCR pass over the page: form-type
   keywords (``detect.keywords`` of ``form_types/*.yaml``) in the top 30%,
   a "מרכזי" sticker and its 6-digit numbers, a "התקבל" stamp, a page
   marker such as "2/2", the number of words and the share of ink.
2. **Rules** turn the fingerprints of pages A and B into signals for "B
   starts a new form" and "B continues A"; each side is scored as
   ``1 - prod(1 - weight)`` over its signals (weights: ``config.SegmentSettings``)
   and the boundary confidence is ``winner * (1 - loser)``.
3. **Vision model**, only where the rules stay below the review threshold:
   the pair is sent to ``ExtractionProvider.compare_pages`` and the answer
   is added as one more signal. It can also mark B irrelevant; such pages
   are listed in ``Segmentation.irrelevant_pages``, never dropped.

A group's ``split_confidence`` is the lowest confidence of the boundaries
that open it, run through it and close it; below the threshold the group
gets ``SPLIT_UNCERTAIN`` ("may be split wrongly").

Fingerprints hold OCR text and central numbers — never log them.
"""
from __future__ import annotations

import logging
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum

import numpy as np
from rapidfuzz import fuzz

from app.config import SegmentSettings
from app.imaging.illumination import binarize
from app.ocr.base import OcrEngine, OcrError, OcrWord
from app.pipeline.ingest import PageImage
from app.providers.base import ExtractionProvider
from app.schemas import SPLIT_UNCERTAIN, FormGroup, FormType, IrrelevantPage, PageRelation, Segmentation

logger = logging.getLogger(__name__)

HEADER_FRACTION = 0.3  # the top of the page searched for form titles
INK_LEVEL = 0  # value of text pixels in the binarized OCR image

# Form keywords and sticker/stamp words are matched with rapidfuzz scores (0..100).
LONG_KEYWORD_CHARS = 8  # keywords at least this long (without spaces) are matched fuzzily
KEYWORD_MIN_SCORE = 90
WORD_MIN_SCORE = 80  # one wrong letter in a 5-letter word
CENTRAL_STICKER_WORD = "מרכזי"
RECEIVED_STAMP_WORD = "התקבל"
# Text of the lab's own "התקבל" stamp, which is on almost every page: a
# keyword that matches it says nothing about the form type.
RECEIVED_STAMP_TEXT = "התקבל המעבדה המרכזית לנגיפים המרכז הרפואי המשולב"
CENTRAL_NUMBER_DIGITS = 6
# A 6-digit number belongs to a "מרכזי" word within this many word heights.
STICKER_RADIUS_IN_WORD_HEIGHTS = 6.0
# Page markers ("2/3", "Page 2 of 3", "עמוד 2 מתוך 3") count only near the top or bottom edge.
MARKER_EDGE_FRACTION = 0.15
MAX_MARKED_PAGES = 9
_SLASH_MARKER = re.compile(r"^\W*(\d)/(\d)\W*$")
_PAGE_WORD_MARKER = re.compile(r"(?:page|עמוד)\s+(\d)\s+(?:of|מתוך)\s+(\d)", re.IGNORECASE)
# A page with fewer words and less ink than this is a sparse (continuation) page.
SPARSE_MAX_WORDS = 15
SPARSE_MAX_INK = 0.01

FIRST_PAGE_CONFIDENCE = 1.0

# Signal codes, stored in FormGroup.split_reasons.
SIGNAL_TITLE = "title_match"
SIGNAL_NEW_CENTRAL_NUMBER = "new_central_number"
SIGNAL_UNREAD_CENTRAL_STICKER = "central_sticker_unread"
SIGNAL_RECEIVED_STAMP = "received_stamp"
SIGNAL_SAME_CENTRAL_NUMBER = "same_central_number"
SIGNAL_PAGE_MARKER = "page_marker"
SIGNAL_FIRST_OF_SEVERAL = "previous_page_first_of_several"
SIGNAL_SPARSE_PAGE = "sparse_page"
SIGNAL_PREVIOUS_FORM_ONE_PAGE = "previous_form_type_one_page"
SIGNAL_PREVIOUS_FORM_MULTI_PAGE = "previous_form_type_multi_page"
SIGNAL_VLM_NEW_FORM = "vlm_new_form"
SIGNAL_VLM_SAME_FORM = "vlm_same_form"
SIGNAL_VLM_IRRELEVANT = "vlm_irrelevant"
SIGNAL_VLM_INCONSISTENT = "vlm_inconsistent"
SIGNAL_OCR_FAILED = "ocr_failed"
SIGNAL_OCR_UNAVAILABLE = "ocr_unavailable"
SINGLE_PAGE = "single_page"


class Boundary(str, Enum):
    START = "start"  # page B opens a new form
    CONTINUATION = "continuation"  # page B belongs to the form on page A
    IRRELEVANT = "irrelevant"  # page B belongs to no form


@dataclass(frozen=True)
class PageFingerprint:
    """What the rules know about one page. Holds patient data — never log it."""

    page_no: int
    title_form_types: frozenset[str] = frozenset()  # ids whose keywords are in the header
    central_sticker: bool = False
    central_numbers: frozenset[str] = frozenset()
    received_stamp: bool = False
    page_marker: tuple[int, int] | None = None  # (page, of pages)
    word_count: int = 0
    ink_ratio: float = 0.0
    ocr_problem: str | None = None  # SIGNAL_OCR_FAILED / SIGNAL_OCR_UNAVAILABLE

    @property
    def sparse(self) -> bool:
        return self.ocr_problem is None and self.word_count < SPARSE_MAX_WORDS and self.ink_ratio < SPARSE_MAX_INK


@dataclass(frozen=True)
class Signal:
    code: str
    weight: float


@dataclass
class Evidence:
    """Signals for the two answers about a boundary between pages A and B."""

    start: list[Signal] = field(default_factory=list)
    continuation: list[Signal] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # codes that are not signals (e.g. OCR problems)

    def decision(self) -> tuple[Boundary, float]:
        start, continuation = _combined(self.start), _combined(self.continuation)
        if start >= continuation:
            return Boundary.START, start * (1.0 - continuation)
        return Boundary.CONTINUATION, continuation * (1.0 - start)

    def codes(self) -> list[str]:
        return [*self.notes, *(s.code for s in self.start), *(s.code for s in self.continuation)]


@dataclass(frozen=True)
class BoundaryDecision:
    page_no: int  # page B
    boundary: Boundary
    confidence: float
    reasons: tuple[str, ...]
    used_vlm: bool


def _combined(signals: Sequence[Signal]) -> float:
    """Chance that at least one of independent signals is right."""
    return 1.0 - math.prod(1.0 - s.weight for s in signals)


# --- Fingerprint --------------------------------------------------------------


def _letters(text: str) -> str:
    return "".join(ch for ch in text if ch.isalnum())


def _matches_word(word: str, target: str) -> bool:
    letters = _letters(word)
    return len(letters) == len(target) and fuzz.ratio(letters, target) >= WORD_MIN_SCORE


def _keyword_in(keyword: str, text: str, tokens: set[str]) -> bool:
    if len(keyword.replace(" ", "")) >= LONG_KEYWORD_CHARS:
        return fuzz.partial_ratio(keyword, text) >= KEYWORD_MIN_SCORE
    return _letters(keyword) in tokens if " " not in keyword else keyword in text


def title_keywords(form_types: dict[str, FormType]) -> dict[str, list[str]]:
    """The keywords of each form type that can identify a title, i.e. all
    except those that also match the lab's own "התקבל" stamp."""
    return {
        form_type.id: [kw for kw in form_type.detect.keywords
                       if fuzz.partial_ratio(kw, RECEIVED_STAMP_TEXT) < KEYWORD_MIN_SCORE]
        for form_type in form_types.values()
    }


def _title_form_types(header_words: list[OcrWord], keywords: dict[str, list[str]]) -> frozenset[str]:
    text = " ".join(w.text for w in header_words)
    tokens = {_letters(w.text) for w in header_words}
    return frozenset(form_id for form_id, kws in keywords.items() if any(_keyword_in(kw, text, tokens) for kw in kws))


def _central_numbers(words: list[OcrWord], sticker_words: list[OcrWord]) -> frozenset[str]:
    numbers = set()
    for sticker in sticker_words:
        radius = STICKER_RADIUS_IN_WORD_HEIGHTS * max(sticker.height, 1)
        for word in words:
            digits = _letters(word.text)
            near = math.dist(_center(word), _center(sticker)) <= radius
            if near and digits.isdigit() and len(digits) == CENTRAL_NUMBER_DIGITS:
                numbers.add(digits)
    return frozenset(numbers)


def _center(word: OcrWord) -> tuple[float, float]:
    return word.left + word.width / 2, word.top + word.height / 2


def _page_marker(words: list[OcrWord], page_height: int) -> tuple[int, int] | None:
    edge = page_height * MARKER_EDGE_FRACTION
    at_edge = [w for w in words if w.top <= edge or w.top + w.height >= page_height - edge]
    candidates = [m.groups() for w in at_edge if (m := _SLASH_MARKER.match(w.text))]
    candidates += _PAGE_WORD_MARKER.findall(" ".join(w.text for w in at_edge))
    for page, pages in candidates:
        if 1 <= int(page) <= int(pages) <= MAX_MARKED_PAGES and int(pages) > 1:
            return int(page), int(pages)
    return None


def fingerprint_from_words(page_no: int, words: list[OcrWord], page_height: int, ink_ratio: float,
                           keywords: dict[str, list[str]]) -> PageFingerprint:
    header = [w for w in words if w.top < page_height * HEADER_FRACTION]
    sticker_words = [w for w in words if _matches_word(w.text, CENTRAL_STICKER_WORD)]
    return PageFingerprint(
        page_no=page_no,
        title_form_types=_title_form_types(header, keywords),
        central_sticker=bool(sticker_words),
        central_numbers=_central_numbers(words, sticker_words),
        received_stamp=any(_matches_word(w.text, RECEIVED_STAMP_WORD) for w in words),
        page_marker=_page_marker(words, page_height),
        word_count=len(words),
        ink_ratio=ink_ratio,
    )


def fingerprint(page: PageImage, ocr: OcrEngine | None, keywords: dict[str, list[str]]) -> PageFingerprint:
    """Fingerprint one page with a single OCR pass over the binarized image."""
    binary = page.ocr_image if page.ocr_image is not None else binarize(page.original)
    ink_ratio = float(np.mean(binary == INK_LEVEL))
    if ocr is None:
        return PageFingerprint(page.page_no, ink_ratio=ink_ratio, ocr_problem=SIGNAL_OCR_UNAVAILABLE)
    try:
        words = ocr.read_words(binary)
    except OcrError:
        logger.warning("OCR failed on page %d of %s; its boundaries go to the VLM", page.page_no, page.file_id)
        return PageFingerprint(page.page_no, ink_ratio=ink_ratio, ocr_problem=SIGNAL_OCR_FAILED)
    return fingerprint_from_words(page.page_no, words, binary.shape[0], ink_ratio, keywords)


# --- Rules --------------------------------------------------------------------


def _central_signal(a: PageFingerprint, b: PageFingerprint, weights: SegmentSettings) -> tuple[str, Signal] | None:
    if not b.central_sticker:
        return None
    if not b.central_numbers:
        return "start", Signal(SIGNAL_UNREAD_CENTRAL_STICKER, weights.unread_central_sticker)
    if b.central_numbers <= a.central_numbers:
        return "continuation", Signal(SIGNAL_SAME_CENTRAL_NUMBER, weights.same_central_number)
    return "start", Signal(SIGNAL_NEW_CENTRAL_NUMBER, weights.new_central_number)


def _marker_signal(a: PageFingerprint, b: PageFingerprint, weights: SegmentSettings) -> tuple[str, Signal] | None:
    if b.page_marker is not None:
        page, pages = b.page_marker
        if page == 1:
            return "start", Signal(SIGNAL_PAGE_MARKER, weights.page_marker)
        follows_a = a.page_marker == (page - 1, pages)
        weight = weights.page_marker_pair if follows_a else weights.page_marker
        return "continuation", Signal(SIGNAL_PAGE_MARKER, weight)
    if a.page_marker is not None and a.page_marker[0] < a.page_marker[1]:
        return "continuation", Signal(SIGNAL_FIRST_OF_SEVERAL, weights.first_of_several)
    return None


def _form_length_signal(a: PageFingerprint, b: PageFingerprint,
                        typical_pages: dict[str, int], weights: SegmentSettings) -> tuple[str, Signal] | None:
    """``pages.typical`` of the form type(s) whose title is on page A, when
    they agree and B carries no page marker of its own. A multi-page type
    does not suggest a continuation if B has the title of another type."""
    lengths = {typical_pages[form_id] for form_id in a.title_form_types if form_id in typical_pages}
    if len(lengths) != 1 or b.page_marker is not None:
        return None
    if lengths == {1}:
        return "start", Signal(SIGNAL_PREVIOUS_FORM_ONE_PAGE, weights.previous_form_length)
    if b.title_form_types and not b.title_form_types & a.title_form_types:
        return None
    return "continuation", Signal(SIGNAL_PREVIOUS_FORM_MULTI_PAGE, weights.previous_form_length)


def rule_evidence(a: PageFingerprint, b: PageFingerprint, typical_pages: dict[str, int],
                  weights: SegmentSettings) -> Evidence:
    """Signals for the boundary between adjacent pages A and B;
    ``typical_pages`` maps form type ids to ``pages.typical``."""
    evidence = Evidence(notes=sorted({p for p in (a.ocr_problem, b.ocr_problem) if p}))
    if b.title_form_types:
        evidence.start.append(Signal(SIGNAL_TITLE, weights.title))
    if b.received_stamp:
        evidence.start.append(Signal(SIGNAL_RECEIVED_STAMP, weights.received_stamp))
    if b.sparse:
        evidence.continuation.append(Signal(SIGNAL_SPARSE_PAGE, weights.sparse_page))
    for found in (_central_signal(a, b, weights), _marker_signal(a, b, weights),
                  _form_length_signal(a, b, typical_pages, weights)):
        if found is not None:
            side, signal = found
            getattr(evidence, side).append(signal)
    return evidence


# --- Vision model -------------------------------------------------------------


def add_vlm_answer(evidence: Evidence, relation: PageRelation, weights: SegmentSettings) -> Boundary | None:
    """Add the model's answer as a signal; returns IRRELEVANT if it says page
    B belongs to no form (that answer is taken as is), else None."""
    if relation.page_b_type == Boundary.IRRELEVANT.value:
        evidence.notes.append(SIGNAL_VLM_IRRELEVANT)
        return Boundary.IRRELEVANT
    consistent = relation.same_form == (relation.page_b_type == Boundary.CONTINUATION.value)
    weight = weights.vlm if consistent else weights.vlm_inconsistent
    if not consistent:
        evidence.notes.append(SIGNAL_VLM_INCONSISTENT)
    if relation.same_form:
        evidence.continuation.append(Signal(SIGNAL_VLM_SAME_FORM, weight))
    else:
        evidence.start.append(Signal(SIGNAL_VLM_NEW_FORM, weight))
    return None


def decide_boundary(page_a: PageImage, page_b: PageImage, a: PageFingerprint, b: PageFingerprint,
                    typical_pages: dict[str, int], provider: ExtractionProvider,
                    threshold: float, weights: SegmentSettings) -> BoundaryDecision:
    evidence = rule_evidence(a, b, typical_pages, weights)
    boundary, confidence = evidence.decision()
    if confidence >= threshold:
        return BoundaryDecision(b.page_no, boundary, confidence, tuple(evidence.codes()), used_vlm=False)
    if add_vlm_answer(evidence, provider.compare_pages(page_a, page_b), weights) is Boundary.IRRELEVANT:
        return BoundaryDecision(b.page_no, Boundary.IRRELEVANT, weights.vlm, tuple(evidence.codes()), used_vlm=True)
    boundary, confidence = evidence.decision()
    return BoundaryDecision(b.page_no, boundary, confidence, tuple(evidence.codes()), used_vlm=True)


# --- Grouping -----------------------------------------------------------------


@dataclass
class _OpenGroup:
    pages: list[int]
    decisions: list[BoundaryDecision]  # the boundary that opened it and those inside it


def _form_group(source_file: str, group: _OpenGroup, closing: BoundaryDecision | None,
                rotations: dict[int, int], threshold: float) -> FormGroup:
    decisions = [*group.decisions, *([closing] if closing else [])]
    confidence = min(d.confidence for d in decisions)
    reasons = list(dict.fromkeys(code for d in decisions for code in d.reasons))
    if confidence < threshold:
        reasons.append(SPLIT_UNCERTAIN)
    return FormGroup(source_file=source_file, pages=group.pages, rotation=rotations[group.pages[0]],
                     split_confidence=confidence, split_reasons=reasons)


def group_pages(source_file: str, decisions: list[BoundaryDecision], rotations: dict[int, int],
                threshold: float) -> tuple[list[FormGroup], list[IrrelevantPage]]:
    """Turn boundary decisions (the first one opens page 0) into form groups.

    A continuation after an irrelevant page joins the group before it.
    A group is closed by the next START decision, whose confidence counts too.
    """
    groups: list[FormGroup] = []
    irrelevant: list[IrrelevantPage] = []
    current: _OpenGroup | None = None
    for decision in decisions:
        if decision.boundary is Boundary.IRRELEVANT:
            irrelevant.append(IrrelevantPage(source_file=source_file, page=decision.page_no))
        elif decision.boundary is Boundary.CONTINUATION and current is not None:
            current.pages.append(decision.page_no)
            current.decisions.append(decision)
        else:
            if current is not None:
                groups.append(_form_group(source_file, current, decision, rotations, threshold))
            current = _OpenGroup(pages=[decision.page_no], decisions=[decision])
    if current is not None:
        groups.append(_form_group(source_file, current, None, rotations, threshold))
    return groups, irrelevant


def _first_page(page: PageImage, reasons: tuple[str, ...] = ()) -> BoundaryDecision:
    return BoundaryDecision(page.page_no, Boundary.START, FIRST_PAGE_CONFIDENCE, reasons, used_vlm=False)


def segment(pages: list[PageImage], ocr: OcrEngine | None, form_types: dict[str, FormType],
            provider: ExtractionProvider, threshold: float,
            weights: SegmentSettings | None = None) -> Segmentation:
    """Group the pages of one file into forms (see the module docstring).

    ``weights`` default to ``SegmentSettings()`` (the ``CE_SEGMENT_WEIGHT_*`` env vars).

    A single-page file is one form without OCR or model calls.
    """
    if not pages:
        return Segmentation(groups=[])
    rotations = {p.page_no: p.rotation for p in pages}
    source_file = pages[0].file_id
    if len(pages) == 1:
        groups, _ = group_pages(source_file, [_first_page(pages[0], (SINGLE_PAGE,))], rotations, threshold)
        return Segmentation(groups=groups)

    weights = weights or SegmentSettings()
    keywords = title_keywords(form_types)
    typical_pages = {form_id: form_type.pages.typical for form_id, form_type in form_types.items()}
    prints = [fingerprint(p, ocr, keywords) for p in pages]
    decisions = [_first_page(pages[0])]
    for i in range(1, len(pages)):
        decisions.append(decide_boundary(pages[i - 1], pages[i], prints[i - 1], prints[i],
                                         typical_pages, provider, threshold, weights))
    groups, irrelevant = group_pages(source_file, decisions, rotations, threshold)
    vlm_calls = sum(d.used_vlm for d in decisions)
    logger.info("file %s: %d pages -> %d forms, %d irrelevant, %d VLM calls for %d boundaries",
                source_file, len(pages), len(groups), len(irrelevant), vlm_calls, len(pages) - 1)
    return Segmentation(groups=groups, irrelevant_pages=irrelevant, vlm_calls=vlm_calls)

"""Segmentation rules, VLM fallback and grouping — fake OCR and fake provider only."""
from pathlib import Path

import numpy as np
import pytest

from app.config import SegmentSettings
from app.ocr.base import OcrError, OcrWord
from app.pipeline import segment as seg
from app.pipeline.ingest import PageImage
from app.providers.fake import FakeProvider
from app.schemas import SPLIT_UNCERTAIN, FormType, PageRelation

THRESHOLD = 0.85
PAGE_HEIGHT = 1000
HEADER_Y = 50
BODY_Y = 500
FOOTER_Y = 980
TITLE = "טופס בדיקה לדוגמה במעבדה"
ONE_PAGE_TYPE, TWO_PAGE_TYPE = "dummy_one", "dummy_two"
TWO_PAGE_TITLE = "טופס דו עמודי לדוגמה"
UNKNOWN_TITLE = "כותרת שאינה ברשימה"
FILLER = [f"מילה{i}" for i in range(40)]


def _form_type(form_id: str, keyword: str, typical: int) -> FormType:
    spec = {"labels": [], "hint": "", "multiple": False, "handwritten_possible": False}
    fields = ["first_name", "last_name", "id_number", "central_numbers", "received_date", "sender",
              "commitment_numbers"]
    return FormType(id=form_id, display_name=form_id, certainty="certain", seen_in="test",
                    detect={"keywords": [keyword], "logo_hint": ""}, fields={f: spec for f in fields},
                    pages={"typical": typical})


FORM_TYPES = {
    ONE_PAGE_TYPE: _form_type(ONE_PAGE_TYPE, TITLE, 1),
    TWO_PAGE_TYPE: _form_type(TWO_PAGE_TYPE, TWO_PAGE_TITLE, 2),
}


def _word(text: str, top: int, left: int = 100) -> OcrWord:
    return OcrWord(text=text, confidence=0.9, left=left, top=top, width=60, height=20)


def _line(text: str, top: int) -> list[OcrWord]:
    return [_word(part, top, left=100 + 70 * i) for i, part in enumerate(text.split())]


def _sticker(number: str, top: int = BODY_Y) -> list[OcrWord]:
    return [_word("מרכזי", top, left=300), _word(number, top, left=370)]


def _page(page_no: int, ink: bool = True) -> PageImage:
    image = np.zeros((PAGE_HEIGHT, 700, 3), np.uint8)
    ocr_image = np.full((PAGE_HEIGHT, 700), 255, np.uint8)
    if ink:
        ocr_image[:100] = 0  # 10% ink: not a sparse page
    return PageImage(file_id="f.pdf", page_no=page_no, image=image, original_path=Path("f.pdf"),
                     original=image, ocr_image=ocr_image, rotation=90 if page_no == 0 else 0)


class ScriptedOcr:
    """Returns prepared words per page; pages are told apart by a marker pixel."""

    def __init__(self, pages: list[list[OcrWord] | None]) -> None:
        self.pages = pages
        self.calls = 0

    def detect_orientation(self, image):
        raise AssertionError("not expected")

    def text_confidence(self, image):
        raise AssertionError("not expected")

    def read_words(self, image: np.ndarray) -> list[OcrWord]:
        words = self.pages[self.calls]
        self.calls += 1
        if words is None:
            raise OcrError("dummy failure")
        return words


class RecordingProvider(FakeProvider):
    def __init__(self, answer: PageRelation | None = None) -> None:
        self.pairs: list[tuple[int, int]] = []
        self.answer = answer

    def compare_pages(self, page_a, page_b):
        self.pairs.append((page_a.page_no, page_b.page_no))
        return self.answer or super().compare_pages(page_a, page_b)


def _run(words: list[list[OcrWord] | None], provider=None, inks=None):
    provider = provider or RecordingProvider()
    pages = [_page(i, ink=True if inks is None else inks[i]) for i in range(len(words))]
    result = seg.segment(pages, ScriptedOcr(words), FORM_TYPES, provider, THRESHOLD)
    return result, provider


def _form(title: str = TITLE, number: str | None = None) -> list[OcrWord]:
    return [*_line(title, HEADER_Y), *(_sticker(number) if number else []), *_line(" ".join(FILLER), BODY_Y + 100)]


# --- fingerprint ----------------------------------------------------------------


def test_fingerprint_reads_title_sticker_stamp_and_marker():
    keywords = seg.title_keywords(FORM_TYPES)
    words = [*_line(TITLE, HEADER_Y), *_sticker("123456"), _word("התקבל", 700), _word("2/2", FOOTER_Y)]
    fp = seg.fingerprint_from_words(3, words, PAGE_HEIGHT, 0.05, keywords)
    assert fp.title_form_types == {ONE_PAGE_TYPE}
    assert fp.central_sticker and fp.central_numbers == {"123456"}
    assert fp.received_stamp
    assert fp.page_marker == (2, 2)
    assert not fp.sparse


def test_title_below_the_header_does_not_count():
    fp = seg.fingerprint_from_words(0, _line(TITLE, BODY_Y), PAGE_HEIGHT, 0.05, seg.title_keywords(FORM_TYPES))
    assert not fp.title_form_types


def test_title_is_matched_despite_one_ocr_error():
    garbled = TITLE.replace("בדיקה", "בריקה")
    fp = seg.fingerprint_from_words(0, _line(garbled, HEADER_Y), PAGE_HEIGHT, 0.05, seg.title_keywords(FORM_TYPES))
    assert fp.title_form_types == {ONE_PAGE_TYPE}


def test_number_far_from_the_sticker_word_is_not_a_central_number():
    words = [_word("מרכזי", 100, left=50), _word("123456", 900, left=600)]
    fp = seg.fingerprint_from_words(0, words, PAGE_HEIGHT, 0.05, {})
    assert fp.central_sticker and not fp.central_numbers


def test_dates_in_the_body_are_not_page_markers():
    fp = seg.fingerprint_from_words(0, [_word("2/3", BODY_Y), _word("12/08/2026", FOOTER_Y)], PAGE_HEIGHT, 0.05, {})
    assert fp.page_marker is None


def test_page_of_marker_in_words():
    fp = seg.fingerprint_from_words(0, _line("Page 1 of 2", HEADER_Y), PAGE_HEIGHT, 0.05, {})
    assert fp.page_marker == (1, 2)


def test_keywords_of_the_received_stamp_are_not_titles():
    stamp_like = _form_type("stamp_like", "המעבדה המרכזית לנגיפים", 1)
    assert seg.title_keywords({"stamp_like": stamp_like}) == {"stamp_like": []}


# --- rules ----------------------------------------------------------------------


def test_every_titled_page_is_its_own_form_without_vlm():
    result, provider = _run([_form(number="111111"), _form(number="222222"), _form()])
    assert [g.pages for g in result.groups] == [[0], [1], [2]]
    assert result.vlm_calls == 0 and provider.pairs == []
    assert all(SPLIT_UNCERTAIN not in g.split_reasons for g in result.groups)
    assert result.groups[0].rotation == 90  # rotation of the group's first page


def test_page_marker_pair_joins_pages():
    first = [*_form(TWO_PAGE_TITLE), _word("1/2", FOOTER_Y)]
    second = [*_line(" ".join(FILLER), BODY_Y), _word("2/2", FOOTER_Y)]
    result, provider = _run([first, second])
    assert [g.pages for g in result.groups] == [[0, 1]]
    assert provider.pairs == []
    assert result.groups[0].split_confidence >= THRESHOLD


def test_sparse_page_continues_the_form():
    result, provider = _run([_form(UNKNOWN_TITLE, "111111"), [_word("הערה", BODY_Y)]], inks=[True, False])
    assert [g.pages for g in result.groups] == [[0, 1]]
    assert provider.pairs == []


def test_same_central_number_continues_the_form():
    result, provider = _run([_form(UNKNOWN_TITLE, "111111"), [*_sticker("111111"), *_line(" ".join(FILLER), 800)]])
    assert [g.pages for g in result.groups] == [[0, 1]]
    assert provider.pairs == []


def test_sparse_page_after_a_one_page_form_type_is_unclear():
    # "the form type has 1 page" and "B is almost empty" contradict: ask the VLM.
    result, provider = _run([_form(TITLE), [_word("הערה", BODY_Y)]], inks=[True, False])
    assert provider.pairs == [(0, 1)]
    assert [g.pages for g in result.groups] == [[0], [1]]


# --- VLM fallback ---------------------------------------------------------------


def test_unclear_page_goes_to_the_vlm_only_once():
    unclear = _line(" ".join(FILLER), BODY_Y)
    answer = PageRelation(same_form=True, reason="dummy", page_b_type="continuation")
    result, provider = _run([_form(TWO_PAGE_TITLE), unclear, _form()], RecordingProvider(answer))
    assert provider.pairs == [(0, 1)]
    assert result.vlm_calls == 1
    assert [g.pages for g in result.groups] == [[0, 1], [2]]
    assert seg.SIGNAL_VLM_SAME_FORM in result.groups[0].split_reasons


def test_vlm_disagreeing_with_weak_rules_flags_the_group():
    unclear = _line(" ".join(FILLER), BODY_Y)
    answer = PageRelation(same_form=False, reason="dummy", page_b_type="start")
    result, _ = _run([_form(TWO_PAGE_TITLE), unclear], RecordingProvider(answer))
    assert [g.pages for g in result.groups] == [[0], [1]]
    assert all(SPLIT_UNCERTAIN in g.split_reasons for g in result.groups)
    assert all(g.needs_review for g in result.groups)


def test_vlm_answer_alone_is_flagged():
    unclear = _line(" ".join(FILLER), BODY_Y)
    result, _ = _run([unclear, unclear])
    assert SegmentSettings().vlm < THRESHOLD
    assert all(SPLIT_UNCERTAIN in g.split_reasons for g in result.groups)


def test_inconsistent_vlm_answer_is_weaker_and_noted():
    evidence = seg.Evidence()
    weights = SegmentSettings()
    seg.add_vlm_answer(evidence, PageRelation(same_form=True, reason="dummy", page_b_type="start"), weights)
    assert evidence.continuation[0].weight == weights.vlm_inconsistent
    assert seg.SIGNAL_VLM_INCONSISTENT in evidence.notes


def test_irrelevant_page_is_listed_not_dropped():
    answer = PageRelation(same_form=False, reason="dummy", page_b_type="irrelevant")
    blank_but_inked = _line(" ".join(FILLER), BODY_Y)
    provider = RecordingProvider(answer)
    pages = [_page(i) for i in range(3)]
    words = [_form(number="111111"), blank_but_inked, _form(number="222222")]
    result = seg.segment(pages, ScriptedOcr(words), FORM_TYPES, provider, THRESHOLD)
    assert [g.pages for g in result.groups] == [[0], [2]]
    assert [(p.source_file, p.page) for p in result.irrelevant_pages] == [("f.pdf", 1)]


def test_continuation_after_an_irrelevant_page_joins_the_form_before_it():
    decisions = [
        seg.BoundaryDecision(0, seg.Boundary.START, 1.0, (), False),
        seg.BoundaryDecision(1, seg.Boundary.IRRELEVANT, 0.9, (), True),
        seg.BoundaryDecision(2, seg.Boundary.CONTINUATION, 0.9, (), False),
    ]
    groups, irrelevant = seg.group_pages("f.pdf", decisions, {0: 0, 1: 0, 2: 0}, THRESHOLD)
    assert [g.pages for g in groups] == [[0, 2]]
    assert [p.page for p in irrelevant] == [1]


def test_ocr_failure_sends_the_boundary_to_the_vlm():
    result, provider = _run([_form(number="111111"), None])
    assert provider.pairs == [(0, 1)]
    assert seg.SIGNAL_OCR_FAILED in result.groups[1].split_reasons


def test_without_ocr_every_boundary_goes_to_the_vlm():
    provider = RecordingProvider()
    pages = [_page(i) for i in range(3)]
    result = seg.segment(pages, None, FORM_TYPES, provider, THRESHOLD)
    assert provider.pairs == [(0, 1), (1, 2)]
    assert result.vlm_calls == 2
    assert seg.SIGNAL_OCR_UNAVAILABLE in result.groups[0].split_reasons


# --- single page / empty --------------------------------------------------------


def test_single_page_skips_ocr_and_vlm():
    ocr = ScriptedOcr([])
    provider = RecordingProvider()
    result = seg.segment([_page(0)], ocr, FORM_TYPES, provider, THRESHOLD)
    assert [g.pages for g in result.groups] == [[0]]
    assert result.groups[0].split_confidence == 1.0
    assert ocr.calls == 0 and provider.pairs == []


def test_no_pages_no_groups():
    assert seg.segment([], None, FORM_TYPES, FakeProvider(), THRESHOLD).groups == []


# --- confidence -----------------------------------------------------------------


@pytest.mark.parametrize("start, continuation, expected", [
    ([0.9], [], (seg.Boundary.START, 0.9)),
    ([0.9], [0.9], (seg.Boundary.START, pytest.approx(0.09))),
    ([], [0.9, 0.9], (seg.Boundary.CONTINUATION, pytest.approx(0.99))),
    ([], [], (seg.Boundary.START, 0.0)),
])
def test_decision_confidence(start, continuation, expected):
    evidence = seg.Evidence(start=[seg.Signal("s", w) for w in start],
                            continuation=[seg.Signal("c", w) for w in continuation])
    assert evidence.decision() == expected


def test_group_confidence_includes_the_closing_boundary():
    decisions = [
        seg.BoundaryDecision(0, seg.Boundary.START, 1.0, (), False),
        seg.BoundaryDecision(1, seg.Boundary.START, 0.5, ("weak",), True),
    ]
    groups, _ = seg.group_pages("f.pdf", decisions, {0: 0, 1: 0}, THRESHOLD)
    assert [g.split_confidence for g in groups] == [0.5, 0.5]
    assert all(SPLIT_UNCERTAIN in g.split_reasons for g in groups)


def test_weights_come_from_settings():
    a = seg.PageFingerprint(page_no=0)
    b = seg.PageFingerprint(page_no=1, title_form_types=frozenset({ONE_PAGE_TYPE}))
    evidence = seg.rule_evidence(a, b, {}, SegmentSettings(title=0.42))
    assert [(s.code, s.weight) for s in evidence.start] == [(seg.SIGNAL_TITLE, 0.42)]

"""Stage 4: form type classification and sender matching. Fake provider, no network."""
from pathlib import Path

import numpy as np
import pytest

from app.config import Settings
from app.pipeline.classify import (
    GENERIC_FORM_TYPE_ID,
    SENDER_AMBIGUOUS,
    SENDER_CANDIDATE_PREFIX,
    SENDER_FUZZY_MATCH,
    SENDER_MIN_SCORE,
    SENDER_NOT_IN_LIST,
    SENDER_READINGS_AGREE,
    SENDER_READINGS_DISAGREE,
    SenderMatcher,
    SenderMatchStatus,
    classify,
    compare_sender_readings,
    load_form_types,
    load_generic_form_type,
    normalize_sender_name,
)
from app.pipeline.ingest import PageImage
from app.pipeline.normalize import load_senders
from app.providers.fake import FakeProvider
from app.schemas import FORM_TYPE_UNKNOWN, UNKNOWN_FORM_TYPE, FormClassification, FormGroup, Sender, SignalSource

FORM_TYPES_DIR = Settings().form_types_dir

# A small closed list, so the tests do not change when senders.json grows.
SENDERS = [
    Sender(canonical="כללית", type="hmo", aliases=["שירותי בריאות כללית", 'קופ"ח כללית'], abbreviations=['כ"ל']),
    Sender(canonical="מכבי", type="hmo", aliases=["מכבי שירותי בריאות"]),
    Sender(canonical='המרכז הרפואי רמב"ם', type="hospital", aliases=['רמב"ם']),
    Sender(canonical="המרכז הרפואי שערי צדק", type="hospital", aliases=["שערי צדק"], abbreviations=["szmc"]),
    Sender(canonical="המרכז הרפואי שמיר (אסף הרופא)", type="hospital", aliases=["שמיר", "אסף הרופא"]),
    Sender(canonical="המרכז הרפואי שמואל הרופא", type="hospital", aliases=["שמואל הרופא"]),
]


@pytest.fixture
def matcher() -> SenderMatcher:
    return SenderMatcher(SENDERS)


def _page(page_no: int = 0) -> PageImage:
    image = np.zeros((20, 20, 3), np.uint8)
    return PageImage(file_id="a.pdf", page_no=page_no, image=image, original_path=Path("a.pdf"), original=image)


# --- Normalization ------------------------------------------------------------


@pytest.mark.parametrize(("raw", "expected"), [
    ('רמב"ם', "רמבם"),
    ("רמב״ם", "רמבם"),                      # gershayim
    ("שמואל-הרופא", "שמואל הרופא"),
    ("שמואל־הרופא", "שמואל הרופא"),     # maqaf
    ("שְׁמוּאֵל הָרוֹפֵא", "שמואל הרופא"),        # niqqud
    ("המרכז הרפואי  שערי צדק", "שערי צדק"),
    ('קופ"ח כללית', "כללית"),
    ("SZMC", "szmc"),
    ("המרכז הרפואי צפון (פוריה)", "צפון פוריה"),
])
def test_normalize_sender_name(raw, expected):
    assert normalize_sender_name(raw) == expected


def test_generic_phrase_is_removed_only_as_whole_words():
    # "מרכזי" contains "מרכז" but is not the phrase "מרכז רפואי".
    assert normalize_sender_name("מרכזי רפואי") == "מרכזי רפואי"


# --- Sender matching ----------------------------------------------------------


@pytest.mark.parametrize(("raw", "canonical"), [
    ("שרותי בריאות כללית", "כללית"),              # spelling without yod
    ("מכבי שרותי בריאות", "מכבי"),
    ("שמואל-הרופא", "המרכז הרפואי שמואל הרופא"),   # hyphen
    ("רמבם", 'המרכז הרפואי רמב"ם'),                 # no gershayim
    ('בי"ח רמב"ם', 'המרכז הרפואי רמב"ם'),
    ("המרכז הרפואי שערי-צדק", "המרכז הרפואי שערי צדק"),
])
def test_different_spelling_matches(matcher, raw, canonical):
    result = matcher.match(raw)
    assert (result.status, result.canonical) == (SenderMatchStatus.MATCHED, canonical)


@pytest.mark.parametrize(("raw", "canonical"), [
    ('כ"ל', "כללית"),
    ("כ״ל", "כללית"),
    ("SZMC", "המרכז הרפואי שערי צדק"),
])
def test_abbreviation_matches(matcher, raw, canonical):
    assert matcher.match(raw).canonical == canonical


def test_exact_match_has_full_score_and_no_review(matcher):
    field = matcher.match("שערי צדק").to_field()
    assert (field.value, field.raw_value) == ("המרכז הרפואי שערי צדק", "שערי צדק")
    assert field.confidence == 1.0 and not field.needs_review and field.reasons == []


@pytest.mark.parametrize(("first", "second", "outcome"), [
    ("שירותי בריאות כללית", "כללית", SENDER_READINGS_AGREE),
    ("כללית", "מכבי שירותי בריאות", SENDER_READINGS_DISAGREE),
    ("כללית", "הדסה", None),   # second reading not in the list: no outcome
    ("כללית", None, None),      # not read the second time
    ("הדסה", "כללית", None),    # first not matched: stays empty, nothing filled in
])
def test_second_sender_reading_is_compared(matcher, first, second, outcome):
    field = matcher.match(first).to_field()
    value = field.value
    compare_sender_readings(field, matcher.match(second), second)
    outcomes = {SENDER_READINGS_AGREE, SENDER_READINGS_DISAGREE} & set(field.reasons)
    assert outcomes == ({outcome} if outcome else set())
    assert field.value == value
    assert [(s.source, s.value) for s in field.signals] == [(SignalSource.VLM, second)]


def test_fuzzy_match_says_so(matcher):
    result = matcher.match("כלליח")  # one letter misread
    assert result.status is SenderMatchStatus.MATCHED and SENDER_MIN_SCORE <= result.score < 100
    assert SENDER_FUZZY_MATCH in result.to_field().reasons


@pytest.mark.parametrize("raw", ["הדסה", "בית חולים אלישע", 'ללא קופ"ח'])
def test_name_not_in_list_stays_empty_with_candidates(matcher, raw):
    result = matcher.match(raw)
    assert result.status is SenderMatchStatus.NOT_IN_LIST
    field = result.to_field()
    assert field.value is None and field.needs_review and field.raw_value == raw
    assert SENDER_NOT_IN_LIST in field.reasons and "not_found" in field.reasons
    assert any(r.startswith(SENDER_CANDIDATE_PREFIX) for r in field.reasons)


def test_only_generic_words_is_not_in_list(matcher):
    result = matcher.match("קופת חולים")
    assert result.status is SenderMatchStatus.NOT_IN_LIST and result.candidates == ()


def test_two_close_candidates_are_ambiguous(matcher):
    # "הרופא" alone fits Shamir (Assaf Harofeh) and Shmuel Harofeh equally well.
    result = matcher.match("הרופא")
    assert result.status is SenderMatchStatus.AMBIGUOUS
    names = {name for name, _ in result.candidates[:2]}
    assert names == {"המרכז הרפואי שמיר (אסף הרופא)", "המרכז הרפואי שמואל הרופא"}
    field = result.to_field()
    assert field.value is None and SENDER_AMBIGUOUS in field.reasons
    assert f"{SENDER_CANDIDATE_PREFIX}המרכז הרפואי שמואל הרופא=" in " ".join(field.reasons)


def test_text_naming_two_listed_bodies_is_ambiguous():
    matcher = SenderMatcher(load_senders(Settings().senders_path))
    assert matcher.match("שירותי בריאות כללית - בית חולים השרון").status is SenderMatchStatus.AMBIGUOUS


def test_aliases_of_one_sender_are_not_rivals(matcher):
    # "רמבם" scores 100 on both the canonical name and the alias of the same sender.
    assert matcher.match("רמבם").status is SenderMatchStatus.MATCHED


def test_gap_rule_uses_the_configured_gap():
    strict = SenderMatcher(SENDERS, min_gap=50)
    assert strict.match("כלליח").status is SenderMatchStatus.AMBIGUOUS


@pytest.mark.parametrize("raw", [None, "", "  "])
def test_unread_sender_is_not_found(matcher, raw):
    result = matcher.match(raw)
    assert result.status is SenderMatchStatus.NOT_READ
    assert result.to_field().reasons == ["not_found"]


def test_two_senders_with_the_same_name_are_rejected():
    duplicate = [Sender(canonical="א", type="hmo", aliases=["שם"]), Sender(canonical="ב", type="hmo", aliases=["שם"])]
    with pytest.raises(ValueError):
        SenderMatcher(duplicate)


@pytest.mark.parametrize(("raw", "canonical"), [
    ("שירותי בריאות כללית", "כללית"),
    ('קופ"ח מכבי-מעבדה שולחת', "מכבי"),
    ("לאומית שירותי בריאות מעבדה מרכזית", "לאומית"),
    ("מכבי שירותי בריאות - מעבדה", "מכבי"),  # "מעבדה" must not pull towards the Rosenblatt lab
    ("ע\"ש אדית וולפסון", "המרכז הרפואי וולפסון"),
    ("משרד הבטחון", "משרד הביטחון"),
    ("הרשות הפלסטינאית", "הרשות הפלסטינית"),
])
def test_real_list_matches_names_printed_on_sample_layouts(raw, canonical):
    """The real senders.json: wording described in docs/form_mapping.md."""
    matcher = SenderMatcher(load_senders(Settings().senders_path))
    assert matcher.match(raw).canonical == canonical


# --- Form type ----------------------------------------------------------------


class _Answer(FakeProvider):
    def __init__(self, answer: FormClassification) -> None:
        self.answer = answer
        self.seen: list[list[int]] = []

    def classify(self, pages, form_types):
        self.seen.append([p.page_no for p in pages])
        return self.answer


def _classify(answer: FormClassification, matcher: SenderMatcher):
    provider = _Answer(answer)
    result = classify([_page(0), _page(1)], load_form_types(FORM_TYPES_DIR), load_generic_form_type(FORM_TYPES_DIR),
                      matcher, provider)
    return result, provider


def test_known_form_type_selects_its_yaml(matcher):
    result, provider = _classify(FormClassification(form_type="clalit_form17", sender_raw="כללית", evidence=""),
                                 matcher)
    assert result.known and result.form_type.id == "clalit_form17" and result.form_reasons == ()
    assert result.sender.canonical == "כללית"
    assert provider.seen == [[0]]  # first page only


def test_unknown_form_type_uses_generic_and_flags_the_form(matcher):
    result, _ = _classify(FormClassification(form_type=UNKNOWN_FORM_TYPE, sender_raw=None, evidence=""), matcher)
    assert not result.known and result.form_type.id == GENERIC_FORM_TYPE_ID
    assert result.form_reasons == (FORM_TYPE_UNKNOWN,)


def test_form_type_unknown_puts_the_form_in_review():
    assert FormGroup(source_file="a.pdf", pages=[0], form_reasons=[FORM_TYPE_UNKNOWN]).needs_review


def test_generic_is_not_offered_as_a_layout():
    assert GENERIC_FORM_TYPE_ID not in load_form_types(FORM_TYPES_DIR)
    assert load_generic_form_type(FORM_TYPES_DIR).id == GENERIC_FORM_TYPE_ID

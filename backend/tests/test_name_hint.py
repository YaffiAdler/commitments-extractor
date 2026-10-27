"""Stage 6b: the name list as a soft hint (dummy lists written by the tests; none is bundled)."""
import pytest

from app.config import ConfidenceSettings
from app.pipeline.confidence import FieldKind, WeightedScorer, score_field
from app.pipeline.name_hint import (
    MAX_SUGGESTIONS,
    NAME_IN_LIST,
    NAME_LIST_SUGGESTION,
    NameList,
    NameListError,
    NameLists,
    hint_field,
    hint_names,
    load_name_list,
)
from app.schemas import Commitment, Field, FieldSource, SignalSource

DUMMY_FIRST = NameList.of(["משה", "אברהם", "דוד", "רחל", "חנה"])
DUMMY_LAST = NameList.of(["כהן", "לוי", 'כ"ץ', "הורוביץ"])


def _name(value: str) -> Field[str]:
    return Field[str](value=value, raw_value=value, source=FieldSource.HANDWRITTEN, reasons=["handwritten"])


def test_listed_name_is_information_only():
    field = _name("אברהמ")  # final letter written as regular: still the listed name
    hint_field(field, DUMMY_FIRST)
    assert NAME_IN_LIST in field.reasons and NAME_LIST_SUGGESTION not in field.reasons
    assert not [s for s in field.signals if s.source is SignalSource.NAME_LIST]


def test_one_letter_away_is_suggested_never_replaced():
    field = _name("רוד")
    hint_field(field, DUMMY_FIRST)
    assert field.value == field.raw_value == "רוד"
    assert NAME_LIST_SUGGESTION in field.reasons
    [signal] = [s for s in field.signals if s.source is SignalSource.NAME_LIST]
    assert signal.value == "דוד" and signal.candidates == ["דוד"]


def test_suggestions_keep_list_order_and_are_capped():
    names = NameList.of(["חנה", "חנן", "חנך", "חנו", "חני"])
    field = _name("חנא")
    hint_field(field, names)
    [signal] = field.signals
    assert signal.candidates == ["חנה", "חנן", "חנך"] and len(signal.candidates) == MAX_SUGGESTIONS


@pytest.mark.parametrize("value", ["שלמה", "ליכטנשטיין", "דודי ה"])  # not listed, nothing near
def test_unlisted_name_gets_nothing(value):
    field = _name(value)
    hint_field(field, DUMMY_FIRST)
    assert field.reasons == ["handwritten"] and field.signals == []


def test_the_hint_does_not_change_the_score():
    weights = ConfidenceSettings()
    scorer = WeightedScorer(weights)
    scores = []
    for value in ("משה", "רוד", "שלמה"):  # listed, one letter away, unlisted
        field = _name(value)
        hint_field(field, DUMMY_FIRST)
        scores.append(score_field(field, FieldKind.HANDWRITTEN, scorer, weights.review_threshold).confidence)
    assert len(set(scores)) == 1


def test_hint_names_uses_each_list_for_its_field_and_skips_empty_names():
    commitment = Commitment(first_name=_name("לוי"), last_name=_name("כץ"))
    hint_names(commitment, NameLists(first=DUMMY_FIRST, last=DUMMY_LAST))
    assert NAME_IN_LIST not in commitment.first_name.reasons  # "לוי" is in the surname list only
    assert NAME_IN_LIST in commitment.last_name.reasons       # quotes ignored
    empty = Commitment()
    hint_names(empty, NameLists(first=DUMMY_FIRST))
    assert NAME_IN_LIST not in empty.first_name.reasons


def test_load_name_list(tmp_path):
    path = tmp_path / "first.txt"
    path.write_text("# dummy list\nמשה\n\n  דוד  \nמשה\n", encoding="utf-8-sig")
    names = load_name_list(path)
    assert names.names == ("משה", "דוד")
    assert load_name_list(None) is None
    assert NameLists().enabled is False and NameLists(last=names).enabled


@pytest.mark.parametrize("content, message", [(None, "not found"), (b"\xff\xfe\x00", "not UTF-8"),
                                              ("# only a comment\n".encode(), "no names")])
def test_unusable_lists_are_refused_without_quoting_lines(tmp_path, content, message):
    path = tmp_path / "list.txt"
    if content is not None:
        path.write_bytes(content)
    with pytest.raises(NameListError, match=message):
        load_name_list(path)

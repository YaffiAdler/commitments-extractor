import pytest

from app.evaluation.name_set import (
    LABELS_FILE,
    NameSample,
    NameSetError,
    SampleKind,
    load_name_set,
    write_name_set,
)

DUMMY_FIRST, DUMMY_LAST = "שלמה", "פרידמן"


def _write_csv(directory, text: str, images=("a.png",)):
    for image in images:
        (directory / image).write_bytes(b"dummy")
    (directory / LABELS_FILE).write_text(text, encoding="utf-8")


def test_roundtrip_through_csv(tmp_path):
    (tmp_path / "a.png").write_bytes(b"dummy")
    samples = [NameSample(image="a.png", first_name=DUMMY_FIRST, last_name='כ"ץ', kind=SampleKind.HANDWRITTEN)]
    write_name_set(tmp_path, samples)
    assert load_name_set(tmp_path) == samples


def test_bom_extra_columns_case_and_blanks_are_accepted(tmp_path):
    (tmp_path / "a.png").write_bytes(b"dummy")
    (tmp_path / LABELS_FILE).write_text(
        f"image,first_name,last_name,kind,writer\n a.png ,{DUMMY_FIRST}, ,Synthetic,W1\n", encoding="utf-8-sig")
    [sample] = load_name_set(tmp_path)
    assert sample == NameSample(image="a.png", first_name=DUMMY_FIRST, last_name="", kind=SampleKind.SYNTHETIC)


@pytest.mark.parametrize("text, message", [
    ("image,first_name,kind\na.png,x,synthetic\n", "lacks the columns last_name"),
    ("image,first_name,last_name,kind\n", "has no rows"),
    ("image,first_name,last_name,kind\na.png,x,y,typed\n", "row 2: kind"),
    ("image,first_name,last_name,kind\nmissing.png,x,y,synthetic\n", "row 2: image file not found"),
    ("image,first_name,last_name,kind\n../a.png,x,y,synthetic\n", "row 2: image must be"),
    ("image,first_name,last_name,kind\n,x,y,synthetic\n", "row 2: image must be"),
    ("image,first_name,last_name,kind\na.png,x,y,synthetic\nA.PNG,x,y,synthetic\n", "row 3: image listed twice"),
])
def test_invalid_sets_are_refused(tmp_path, text, message):
    _write_csv(tmp_path, text)
    with pytest.raises(NameSetError, match=message):
        load_name_set(tmp_path)


def test_absolute_paths_are_refused(tmp_path):
    _write_csv(tmp_path, f"image,first_name,last_name,kind\n{tmp_path / 'a.png'},x,y,synthetic\n")
    with pytest.raises(NameSetError, match="row 2: image must be"):
        load_name_set(tmp_path)


def test_missing_labels_file(tmp_path):
    with pytest.raises(NameSetError, match="not found"):
        load_name_set(tmp_path)


def test_errors_never_quote_a_name(tmp_path):
    _write_csv(tmp_path, f"image,first_name,last_name,kind\na.png,{DUMMY_FIRST},{DUMMY_LAST},unknown\n")
    with pytest.raises(NameSetError) as error:
        load_name_set(tmp_path)
    assert DUMMY_FIRST not in str(error.value) and DUMMY_LAST not in str(error.value)

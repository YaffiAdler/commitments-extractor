import pytest

from app.config import Settings
from app.pipeline.classify import load_form_types, load_generic_form_type
from app.providers.prompts import (
    FORM_TYPE_PLACEHOLDER,
    NAME_FIELD_PLACEHOLDER,
    NAME_FIELD_TEXTS,
    UNKNOWN_LAYOUT_TEXT,
    PromptError,
    Prompts,
    form_type_block,
)
from app.schemas import NameWording


def test_v1_holds_the_extraction_rules():
    prompts = Prompts.load()
    assert prompts.version == "v1"
    text = prompts.extract(None)
    for rule in ("Do not guess", "return null", "Do not complete missing digits", "legible to false",
                 '"דרישה" and "דגימה"', "every central number"):
        assert rule in text
    assert FORM_TYPE_PLACEHOLDER not in text and UNKNOWN_LAYOUT_TEXT in text
    assert "digits only" in prompts.read_number and "null" in prompts.read_number


def test_layout_block_comes_from_the_yaml():
    settings = Settings()
    form_type = load_form_types(settings.form_types_dir)["leumit_lab_worklist"]
    block = form_type_block(form_type)
    assert form_type.display_name in block
    for name, spec in form_type.fields.items():
        assert f"- {name}: " in block and spec.hint in block
    assert form_type_block(load_generic_form_type(settings.form_types_dir)).startswith("Layout: ")


def test_missing_version_or_placeholder_is_an_error(tmp_path):
    with pytest.raises(PromptError, match="not found"):
        Prompts.load(tmp_path, "v9")
    (tmp_path / "extract_v9.md").write_text("no placeholder", encoding="utf-8")
    (tmp_path / "read_number_v9.md").write_text("x", encoding="utf-8")
    with pytest.raises(PromptError, match="exactly once"):
        Prompts.load(tmp_path, "v9")


@pytest.mark.parametrize("wording", list(NameWording))
def test_name_prompts_name_the_field_and_hold_the_rules(wording):
    prompts = Prompts.load()
    for field_name, field_text in NAME_FIELD_TEXTS.items():
        text = prompts.read_name(field_name, wording)
        assert field_text in text and NAME_FIELD_PLACEHOLDER not in text
        for rule in ("Do not complete, correct", "ד/ר", "ו/ן/ז/ג", "ה/ח/ת", "כ/ב", "ס/ם/ט", "ע/צ", "ך/כ",
                     "up to 3 candidate", "square brackets", "empty list of candidates (null)", "Never guess"):
            assert rule in text
    assert prompts.read_name("first_name", wording) != prompts.read_name("last_name", wording)


def test_the_two_name_wordings_differ():
    prompts = Prompts.load()
    assert prompts.read_name("first_name", NameWording.STANDARD) != prompts.read_name("first_name", NameWording.LETTERS)
    assert "one at a time" in prompts.read_name("first_name", NameWording.LETTERS)


def test_name_prompt_needs_its_placeholder(tmp_path):
    (tmp_path / "extract_v9.md").write_text(FORM_TYPE_PLACEHOLDER, encoding="utf-8")
    (tmp_path / "read_number_v9.md").write_text("x", encoding="utf-8")
    (tmp_path / "read_name_v9.md").write_text("no placeholder", encoding="utf-8")
    (tmp_path / "read_name_letters_v9.md").write_text(NAME_FIELD_PLACEHOLDER, encoding="utf-8")
    with pytest.raises(PromptError, match="read_name_v9.md must contain"):
        Prompts.load(tmp_path, "v9")

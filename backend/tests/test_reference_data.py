import pytest
from pydantic import ValidationError

from app.config import Settings
from app.pipeline.classify import load_form_types
from app.pipeline.normalize import load_senders
from app.schemas import FormType


def test_all_form_types_load():
    form_types = load_form_types(Settings().form_types_dir)
    assert len(form_types) == 25
    assert all(ft.id == key for key, ft in form_types.items())


def test_form_type_requires_all_commitment_fields():
    doc = load_form_types(Settings().form_types_dir)["clalit_form17"].model_dump()
    del doc["fields"]["sender"]
    with pytest.raises(ValidationError):
        FormType.model_validate(doc)


def test_senders_load():
    senders = load_senders(Settings().senders_path)
    assert {"כללית", "מכבי", "מאוחדת", "לאומית"} <= {s.canonical for s in senders}

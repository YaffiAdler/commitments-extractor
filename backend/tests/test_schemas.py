import json
from datetime import date
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from app.schemas import (
    COMMITMENT_FIELDS,
    OPTIONAL_MISSING,
    BBox,
    Commitment,
    Field,
    FieldSource,
    FieldSpec,
    FormGroup,
    Job,
    JobStatus,
)

BACKEND = Path(__file__).resolve().parents[1]


def _sample_job() -> Job:
    # Dummy values only — never real patient data.
    commitment = Commitment(
        form_type_id="meuhedet_form17",
        first_name=Field[str](value="אברהם", raw_value="אברהם", confidence=0.9,
                              needs_review=False, source=FieldSource.PRINTED,
                              bbox=BBox(page=0, x0=10, y0=20, x1=110, y1=40)),
        last_name=Field[str](value="כהן", confidence=0.9, needs_review=False, source=FieldSource.PRINTED),
        id_number=Field[str](value="000000018", confidence=0.6, needs_review=True,
                             reasons=["id_checksum_failed"], source=FieldSource.HANDWRITTEN),
        central_numbers=[Field[str](value="900001", confidence=0.95, needs_review=False,
                                    source=FieldSource.STICKER)],
        received_date=Field[date](value=date(2026, 6, 18), raw_value="18-06-2026",
                                  confidence=0.8, needs_review=False, source=FieldSource.STAMP),
        sender=Field[str](value="מאוחדת", confidence=0.95, needs_review=False, source=FieldSource.PRINTED),
        commitment_numbers=[Field[str](value="123456789", confidence=0.9, needs_review=False)],
    )
    return Job(files=["a.pdf"], groups=[FormGroup(source_file="a.pdf", pages=[0], commitment=commitment)])


def test_job_json_roundtrip():
    job = _sample_job()
    data = json.loads(job.model_dump_json())
    assert data["status"] == JobStatus.PENDING.value
    assert data["groups"][0]["commitment"]["received_date"]["value"] == "2026-06-18"
    assert Job.model_validate(data) == job


def test_missing_field_is_empty_and_flagged():
    f = Field[str]()
    assert f.value is None and f.needs_review and f.confidence == 0.0 and "not_found" in f.reasons
    # Even if a caller tries to clear the flag, a missing value stays flagged.
    f2 = Field[str](needs_review=False, confidence=0.9)
    assert f2.needs_review and f2.confidence == 0.0


def test_empty_optional_field_may_skip_review():
    f = Field[str](reasons=[OPTIONAL_MISSING], confidence=0.9)
    assert not f.needs_review and f.confidence == 0.0 and "not_found" in f.reasons
    assert f.revalidated(value="1", reasons=[]).value == "1"
    assert f.revalidated(value=None, reasons=[]).needs_review


def test_empty_list_field_is_flagged_unless_optional():
    assert Commitment().field_needs_review("commitment_numbers")
    assert not Commitment(optional_fields=["commitment_numbers"]).field_needs_review("commitment_numbers")
    unreadable_item = Commitment(optional_fields=["commitment_numbers"], commitment_numbers=[Field[str]()])
    assert unreadable_item.field_needs_review("commitment_numbers")


def test_commitment_fields_are_the_extracted_fields():
    assert set(COMMITMENT_FIELDS) == set(Commitment.model_fields) - {"form_type_id", "optional_fields"}


def test_field_spec_is_required_by_default():
    assert FieldSpec(labels=[], hint="", multiple=False, handwritten_possible=False).optional is False


def test_empty_commitment_needs_review():
    assert Commitment().needs_review
    # The soft id-checksum flag alone keeps the form in review.
    assert _sample_job().groups[0].commitment.needs_review


def test_invalid_values_rejected():
    with pytest.raises(ValidationError):
        Field[str](value="x", confidence=1.5)
    with pytest.raises(ValidationError):
        BBox(page=0, x0=10, y0=0, x1=5, y1=1)
    with pytest.raises(ValidationError):
        FormGroup(source_file="a.pdf", pages=[])


REQUIRED_FIELDS = {"first_name", "last_name", "id_number", "central_numbers",
                   "received_date", "sender", "commitment_numbers"}


@pytest.mark.parametrize("path", sorted((BACKEND / "form_types").glob("*.yaml")), ids=lambda p: p.stem)
def test_form_type_yaml_structure(path):
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert doc["id"] == path.stem
    assert doc["display_name"]
    assert doc["certainty"] in {"certain", "uncertain"}
    assert isinstance(doc["detect"]["keywords"], list) and "logo_hint" in doc["detect"]
    assert set(doc["fields"]) == REQUIRED_FIELDS
    for name, spec in doc["fields"].items():
        assert set(spec) - {"pattern", "optional"} == {"labels", "hint", "multiple", "handwritten_possible"}
        assert isinstance(spec.get("optional", False), bool)
        assert "pattern" not in spec or name == "commitment_numbers"
    assert isinstance(doc["pages"]["typical"], int)


def test_senders_json_loads():
    data = json.loads((BACKEND / "data" / "senders.json").read_text(encoding="utf-8"))
    names = [s["canonical"] for s in data["senders"]]
    assert len(names) == len(set(names))
    assert {"כללית", "מכבי", "מאוחדת", "לאומית"} <= set(names)

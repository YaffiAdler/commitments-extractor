from datetime import date

import pytest

from app.config import Settings
from app.lab_system import create_lab_connector
from app.lab_system.base import LabSystemError
from app.lab_system.demo import DemoLabConnector
from app.providers import fake


def test_default_connector_reads_the_demo_file():
    samples = create_lab_connector(Settings()).get_received_samples(None, None)
    centrals = [s.central_number for s in samples]
    assert fake.FAKE_CENTRAL_NUMBER in centrals
    assert len(centrals) == len(set(centrals))


def test_filters_by_date_inclusive():
    connector = create_lab_connector(Settings())
    day = date(2025, 1, 2)
    assert {s.received_date for s in connector.get_received_samples(day, day)} == {day}


def test_unreadable_file_is_a_lab_system_error(tmp_path):
    path = tmp_path / "samples.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(LabSystemError):
        DemoLabConnector(path).get_received_samples(None, None)
    with pytest.raises(LabSystemError):
        DemoLabConnector(tmp_path / "missing.json").get_received_samples(None, None)

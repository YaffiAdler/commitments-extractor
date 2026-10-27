"""scripts/name_eval.py: the current pipeline run on a name set with FakeProvider (no API, no OCR)."""
import importlib.util
import sys
from pathlib import Path

import cv2
import numpy as np

from app.config import Settings
from app.evaluation.name_report import render
from app.evaluation.name_set import NameSample, SampleKind, load_name_set, write_name_set
from app.pipeline.classify import load_generic_form_type
from app.pipeline.orchestrator import Orchestrator
from app.providers import fake
from app.providers.fake import FakeProvider

_spec = importlib.util.spec_from_file_location(
    "name_eval", Path(__file__).resolve().parents[2] / "scripts" / "name_eval.py"
)
name_eval = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = name_eval
_spec.loader.exec_module(name_eval)

PAGE_SHAPE = (400, 300, 3)
WHITE = 255


def _orchestrator() -> Orchestrator:
    settings = Settings()
    return Orchestrator(settings, FakeProvider(), form_types={},
                        generic_form_type=load_generic_form_type(settings.form_types_dir), senders=[])


def _make_set(directory: Path) -> list[NameSample]:
    page = np.full(PAGE_SHAPE, WHITE, np.uint8)
    for image in ("a.png", "b.png"):
        cv2.imencode(".png", page)[1].tofile(str(directory / image))
    (directory / "broken.png").write_bytes(b"not an image")
    write_name_set(directory, [
        NameSample("a.png", fake.FAKE_FIRST_NAME, fake.FAKE_LAST_NAME, SampleKind.SYNTHETIC),
        NameSample("b.png", "יעקב", fake.FAKE_LAST_NAME, SampleKind.HANDWRITTEN),
        NameSample("broken.png", "רחל", "לוי", SampleKind.HANDWRITTEN),
    ])
    return load_name_set(directory)


def test_every_page_is_read_and_a_bad_page_does_not_stop_the_run(tmp_path):
    samples = _make_set(tmp_path)
    seen = []
    outcomes = name_eval.evaluate(tmp_path, samples, _orchestrator(), workers=2,
                                  on_done=lambda index, total, outcome: seen.append((index, total)))
    assert [o.sample for o in outcomes] == samples  # set order, whatever the finishing order
    assert sorted(seen) == [(1, 3), (2, 3), (3, 3)]
    good, other, broken = outcomes
    assert good.forms == 1 and good.error is None and good.both_exact
    assert [f.exact for f in other.fields] == [False, True]
    assert broken.forms == 0 and broken.error is not None
    # FakeProvider's names are printed: base 0.8 is below the 0.9 review threshold, so every name is flagged.
    assert all(f.needs_review for o in outcomes for f in o.fields)


def test_report_of_a_run_holds_no_names(tmp_path):
    samples = _make_set(tmp_path)
    text = render(name_eval.evaluate(tmp_path, samples, _orchestrator()), {"provider": "fake"})
    for name in (fake.FAKE_FIRST_NAME, fake.FAKE_LAST_NAME, "יעקב", "רחל", "לוי"):
        assert name not in text
    assert "| synthetic | both fields | 2 | 2 (100%) |" in text
    assert "| handwritten | both fields | 4 | 1 (25%) |" in text


def test_configuration_names_the_settings_without_secrets(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy-secret-key")
    monkeypatch.setenv("CE_PROVIDER", "claude")
    config = name_eval.configuration(Settings(), FakeProvider(), ocr=None)
    assert config == {"provider": "fake", "model": Settings().claude_model, "prompt": "v1",
                      "vlm_max_edge_px": "2000", "ocr": "off", "name_reread": "off"}
    monkeypatch.setenv("CE_PROVIDER", "fake")
    monkeypatch.setenv("CE_NAME_REREAD", "1")
    monkeypatch.setenv("CE_NAME_REREAD_VARIANTS", "ink,wide")
    config = name_eval.configuration(Settings(), FakeProvider(), ocr=None)
    assert "model" not in config and config["name_reread"] == "ink,wide"

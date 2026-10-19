import pymupdf
import pytest

from app.config import Settings
from app.pipeline.orchestrator import Orchestrator
from app.providers.fake import FakeProvider
from app.schemas import Job, JobStatus


def _orchestrator(monkeypatch) -> Orchestrator:
    monkeypatch.setenv("CE_RENDER_DPI", "72")
    return Orchestrator(Settings(), FakeProvider(), form_types={}, senders=[])


def test_one_file_is_one_form(tmp_path, monkeypatch):
    pdf = tmp_path / "f.pdf"
    with pymupdf.open() as doc:
        doc.new_page()
        doc.new_page()
        doc.save(pdf)
    job = _orchestrator(monkeypatch).run(Job(), [pdf])
    assert job.status is JobStatus.DONE
    assert len(job.groups) == 1
    assert job.groups[0].pages == [0, 1]


def test_pages_are_preprocessed_before_extraction(tmp_path, monkeypatch):
    seen = []

    class RecordingProvider(FakeProvider):
        def extract_form(self, pages, form_type):
            seen.extend(pages)
            return super().extract_form(pages, form_type)

    monkeypatch.setenv("CE_RENDER_DPI", "72")
    pdf = tmp_path / "f.pdf"
    with pymupdf.open() as doc:
        doc.new_page()
        doc.save(pdf)
    Orchestrator(Settings(), RecordingProvider(), form_types={}, senders=[]).run(Job(), [pdf])
    assert len(seen) == 1 and seen[0].ocr_image is not None
    assert "orientation_unavailable" in seen[0].flags


def test_failure_records_exception_type(tmp_path, monkeypatch):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-broken")
    job = Job()
    with pytest.raises(Exception):
        _orchestrator(monkeypatch).run(job, [bad])
    assert job.status is JobStatus.FAILED
    assert job.error and job.error.isidentifier()

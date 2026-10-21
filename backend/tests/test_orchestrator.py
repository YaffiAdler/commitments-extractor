import pymupdf
import pytest

from app.config import Settings
from app.pipeline.orchestrator import Orchestrator
from app.providers.fake import FakeProvider
from app.schemas import Job, JobStatus, PageRelation


def _orchestrator(monkeypatch) -> Orchestrator:
    monkeypatch.setenv("CE_RENDER_DPI", "72")
    return Orchestrator(Settings(), FakeProvider(), form_types={}, senders=[])


def _blank_pdf(path, pages: int):
    with pymupdf.open() as doc:
        for _ in range(pages):
            doc.new_page()
        doc.save(path)
    return path


def test_pages_are_split_into_forms(tmp_path, monkeypatch):
    # Without OCR the boundary goes to the provider; the fake one says "start".
    job = _orchestrator(monkeypatch).run(Job(), [_blank_pdf(tmp_path / "f.pdf", 2)])
    assert job.status is JobStatus.DONE
    assert [g.pages for g in job.groups] == [[0], [1]]
    assert job.split_vlm_calls == 1


def test_each_form_is_extracted_with_its_own_pages(tmp_path, monkeypatch):
    seen = []

    class SameFormProvider(FakeProvider):
        def compare_pages(self, page_a, page_b):
            return PageRelation(same_form=True, reason="dummy", page_b_type="continuation")

        def extract_form(self, pages, form_type):
            seen.append([p.page_no for p in pages])
            return super().extract_form(pages, form_type)

    monkeypatch.setenv("CE_RENDER_DPI", "72")
    job = Orchestrator(Settings(), SameFormProvider(), form_types={}, senders=[]).run(
        Job(), [_blank_pdf(tmp_path / "f.pdf", 3)])
    assert [g.pages for g in job.groups] == [[0, 1, 2]]
    assert seen == [[0, 1, 2]]


def test_irrelevant_pages_are_kept_on_the_job(tmp_path, monkeypatch):
    class IrrelevantProvider(FakeProvider):
        def compare_pages(self, page_a, page_b):
            return PageRelation(same_form=False, reason="dummy", page_b_type="irrelevant")

    monkeypatch.setenv("CE_RENDER_DPI", "72")
    job = Orchestrator(Settings(), IrrelevantProvider(), form_types={}, senders=[]).run(
        Job(), [_blank_pdf(tmp_path / "f.pdf", 2)])
    assert [g.pages for g in job.groups] == [[0]]
    assert [p.page for p in job.irrelevant_pages] == [1]


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

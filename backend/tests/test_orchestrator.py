import pymupdf
import pytest

from app.config import Settings
from app.pipeline.classify import GENERIC_FORM_TYPE_ID, load_form_types, load_generic_form_type
from app.pipeline.normalize import load_senders
from app.pipeline.orchestrator import Orchestrator
from app.providers import fake
from app.providers.base import ProviderError
from app.providers.fake import FakeProvider
from app.schemas import CENTRAL_DUPLICATE, EXTRACTION_FAILED, FORM_TYPE_UNKNOWN, Job, JobStatus, PageRelation


def _build(provider: FakeProvider) -> Orchestrator:
    settings = Settings()
    return Orchestrator(settings, provider, form_types={},
                        generic_form_type=load_generic_form_type(settings.form_types_dir), senders=[])


def _orchestrator(monkeypatch) -> Orchestrator:
    monkeypatch.setenv("CE_RENDER_DPI", "72")
    return _build(FakeProvider())


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
    job = _build(SameFormProvider()).run(
        Job(), [_blank_pdf(tmp_path / "f.pdf", 3)])
    assert [g.pages for g in job.groups] == [[0, 1, 2]]
    assert seen == [[0, 1, 2]]


def test_irrelevant_pages_are_kept_on_the_job(tmp_path, monkeypatch):
    class IrrelevantProvider(FakeProvider):
        def compare_pages(self, page_a, page_b):
            return PageRelation(same_form=False, reason="dummy", page_b_type="irrelevant")

    monkeypatch.setenv("CE_RENDER_DPI", "72")
    job = _build(IrrelevantProvider()).run(
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
    _build(RecordingProvider()).run(Job(), [pdf])
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


def test_each_form_is_classified_and_gets_the_matched_sender(tmp_path, monkeypatch):
    monkeypatch.setenv("CE_RENDER_DPI", "72")
    settings = Settings()
    seen = []

    class RecordingProvider(FakeProvider):
        def extract_form(self, pages, form_type):
            seen.append(form_type.id)
            return super().extract_form(pages, form_type)

    orchestrator = Orchestrator(settings, RecordingProvider(), form_types=load_form_types(settings.form_types_dir),
                                generic_form_type=load_generic_form_type(settings.form_types_dir),
                                senders=load_senders(settings.senders_path))
    job = orchestrator.run(Job(), [_blank_pdf(tmp_path / "f.pdf", 1)])
    commitment = job.groups[0].commitment
    assert seen == [fake.FAKE_FORM_TYPE_ID] and commitment.form_type_id == fake.FAKE_FORM_TYPE_ID
    assert (commitment.sender.value, commitment.sender.raw_value) == ("כללית", fake.FAKE_SENDER_RAW)
    assert not commitment.sender.needs_review and job.groups[0].form_reasons == []


def test_unknown_form_type_is_extracted_with_the_generic_yaml(tmp_path, monkeypatch):
    seen = []

    class UnknownProvider(FakeProvider):
        def extract_form(self, pages, form_type):
            seen.append(form_type.id)
            return super().extract_form(pages, form_type)

    monkeypatch.setenv("CE_RENDER_DPI", "72")
    job = _build(UnknownProvider()).run(Job(), [_blank_pdf(tmp_path / "f.pdf", 1)])
    assert seen == [GENERIC_FORM_TYPE_ID]
    assert job.groups[0].form_reasons == [FORM_TYPE_UNKNOWN] and job.groups[0].needs_review
    # senders=[]: nothing to match against, so the sender stays empty.
    assert job.groups[0].commitment.sender.value is None


def test_a_failing_form_is_marked_and_the_others_are_still_read(tmp_path, monkeypatch):
    class FailsOnSecondPage(FakeProvider):
        def extract_form(self, pages, form_type):
            if pages[0].page_no == 1:
                raise ProviderError("dummy failure")
            return super().extract_form(pages, form_type)

    monkeypatch.setenv("CE_RENDER_DPI", "72")
    job = _build(FailsOnSecondPage()).run(Job(), [_blank_pdf(tmp_path / "f.pdf", 3)])
    assert job.status is JobStatus.DONE and job.error is None
    failed = [g for g in job.groups if g.error]
    assert [g.pages for g in failed] == [[1]] and failed[0].error == "ProviderError"
    assert EXTRACTION_FAILED in failed[0].form_reasons and failed[0].needs_review
    assert all(g.commitment.id_number.value == fake.FAKE_ID_NUMBER for g in job.groups if not g.error)


def test_job_fails_when_every_form_failed(tmp_path, monkeypatch):
    class AlwaysFails(FakeProvider):
        def classify(self, pages, form_types):
            raise ProviderError("dummy failure")

    monkeypatch.setenv("CE_RENDER_DPI", "72")
    job = _build(AlwaysFails()).run(Job(), [_blank_pdf(tmp_path / "f.pdf", 2)])
    assert job.status is JobStatus.FAILED and job.error == "ProviderError"
    assert len(job.groups) == 2


def test_provider_calls_are_counted_per_form(tmp_path, monkeypatch):
    monkeypatch.setenv("CE_RENDER_DPI", "72")
    job = _orchestrator(monkeypatch).run(Job(), [_blank_pdf(tmp_path / "f.pdf", 2)])
    assert [g.vlm_calls for g in job.groups] == [2, 2]  # classify + extract


def test_forms_sharing_a_central_number_are_flagged(tmp_path, monkeypatch):
    # The fake provider gives every form the same dummy central number.
    monkeypatch.setenv("CE_RENDER_DPI", "72")
    job = _orchestrator(monkeypatch).run(Job(), [_blank_pdf(tmp_path / "f.pdf", 2)])
    assert all(CENTRAL_DUPLICATE in g.form_reasons for g in job.groups) and len(job.groups) == 2
    assert all(g.needs_review for g in job.groups)


def test_a_single_form_is_not_a_duplicate(tmp_path, monkeypatch):
    monkeypatch.setenv("CE_RENDER_DPI", "72")
    job = _orchestrator(monkeypatch).run(Job(), [_blank_pdf(tmp_path / "f.pdf", 1)])
    assert CENTRAL_DUPLICATE not in job.groups[0].form_reasons

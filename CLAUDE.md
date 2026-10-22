# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

An engine that reads commitment forms received by the Central Virology Laboratory (scanned PDFs, Hebrew), extracts fields, flags anything uncertain, and exports to Excel. **Currently Phase 5:** upload (PDF or image) -> `ingest` -> `preprocess` -> `segment` (pages -> forms) -> per form, concurrently: `classify` (form type + sender) -> `extract` (provider reading -> `Commitment`) -> `ocr_check` (Tesseract on printed numbers) -> `reread` (one narrow second look) -> job store -> Excel export, with a one-page React UI. normalize/confidence and the `local_vlm` provider are still stubs that raise `NotImplementedError`.

## Commands

Requires 64-bit Python 3.11 (PyMuPDF has no 32-bit wheel). The venv lives at the repo root; pytest runs from `backend/` (`pythonpath = ["."]`, so imports are `from app.schemas import ...`).

```bash
py -3.11 -m venv .venv
SETUPTOOLS_USE_DISTUTILS=stdlib .venv/Scripts/python -m pip install -e "backend[dev]"   # env var works around a setuptools/distutils error on this machine
cd backend && ../.venv/Scripts/python -m pytest                                   # all tests
cd backend && ../.venv/Scripts/python -m pytest tests/test_schemas.py::test_job_json_roundtrip
cd backend && ../.venv/Scripts/python -m pytest -k "form_type_yaml_structure and clalit"   # one YAML
.venv/Scripts/python scripts/render_previews.py [out_dir] [--dpi 150]             # samples/ pages -> PNG
.venv/Scripts/python scripts/make_synthetic_variants.py [out_dir]                # 6 distortions of a dummy form
.venv/Scripts/python scripts/preprocess_examples.py                              # -> docs/preprocess_examples/
.venv/Scripts/python scripts/classify_report.py                                  # stage 4 on samples/ -> eval/classify_report.md (needs a real provider)
.venv/Scripts/python scripts/extract_report.py                                   # stage 5 on samples/ -> eval/extract_report.md (counts only)
cd backend && ../.venv/Scripts/uvicorn app.main:app --reload                       # API on :8000
.venv/Scripts/python eval/run_eval.py predictions.csv                              # accuracy -> eval/report.md

cd frontend && npm install && npm run dev      # :5173, proxies /api/* to the backend (BACKEND_URL)
cd frontend && npm run build                   # tsc + vite build
cd frontend && npm run lint                    # oxlint

docker compose up --build                      # backend + frontend
```

No Python linter or formatter is configured. `eval/run_eval.py` tests live in `backend/tests/test_run_eval.py`.

## Architecture

**Pipeline** (`backend/app/pipeline/`, one module per stage): `ingest` (PDF/image -> `PageImage` per page; type from magic bytes; typed `IngestError`s) -> `preprocess` (perspective -> deskew -> orientation -> illumination -> resize, plus a binarized `ocr_image`; image ops in `app/imaging/`, OCR behind `app/ocr/base.OcrEngine`) -> `segment` (pages -> forms: one OCR fingerprint per page, weighted rules per adjacent pair, `provider.compare_pages` only for pairs below the review threshold; irrelevant pages go to `Job.irrelevant_pages`) -> `classify` (one provider call on the first page -> form type id, or `generic.yaml` + `FormGroup.form_reasons ∋ form_type_unknown`; the sender as read is matched to `senders.json` by `SenderMatcher`: rapidfuzz WRatio ≥ 88 with a 5-point gap to any other sender, else empty with `sender_candidate:` reasons) -> `extract` (`provider.extract_form` returns a raw `FormReading` — value, relative bbox, legible, source, note per field; `extract.to_commitment` turns it into a `Commitment`, its sender is replaced by the matched one) -> `ocr_check` (printed ID/central/commitment numbers: crop + Tesseract `--psm 7` digits whitelist, `ocr_*` reasons) -> `reread` (unconfirmed or illegible numbers: enlarged colour crop to `provider.read_number`, `reread_*` reasons) -> `normalize` (digits, dates) -> `confidence` (score + `needs_review`). OCR and second-look results are extra `Field.signals`, never overwrites. `read_form.FormReader` reads one form; `orchestrator.Orchestrator` runs the forms of a file concurrently (`asyncio.run`; `calls.ProviderCalls`: shared semaphore, tenacity retries on `RetryableProviderError`/timeout, per-call timeout) and fills `Job.groups`; a failed form gets `FormGroup.error` (exception type) + `extraction_failed`, and the job fails only if every form failed (`Job.error` = exception type).

**Providers** (`backend/app/providers/`): the pipeline depends only on `ExtractionProvider` (`base.py`); `ExtractionProvider` is a Protocol (`extract_form`, `read_number`, `classify`, `compare_pages`); output JSON schemas, short instructions, image encoding and output parsing are shared in `common.py`, and the extraction / second-look prompts are versioned files in `backend/prompts/` (`prompts.py`: template + a block built from the form type's YAML; never edit a released version), so a vision provider only handles its own request/response format; `create_provider(settings)` picks `claude`, `gemini`, `local_vlm` or `fake` (fixed dummy data, used by tests) from `CE_PROVIDER`. `main.create_app(settings, provider)` builds the API so tests can inject `FakeProvider`; **Job stores** (`backend/app/storage/`): the API depends only on the `JobStore` Protocol (`base.py`); `create_job_store(settings, tmp)` picks `sqlite` (default, `CE_DB_PATH`, schema in `backend/data/schema.sql`, `init()` on startup) or `memory` from `CE_STORAGE`. `get` returns a copy, so a changed job must be saved with `update`. Both expire jobs after `CE_JOB_TTL_S`; `delete` also removes the job's temp files. Tests run `test_main.py`/`test_e2e.py` against both stores (`conftest.py`) and `test_job_stores.py` is the shared contract. **Settings** (`config.py`) are read from `CE_*` env vars. `storage/tmp.py` keeps uploads under generated names in a per-job dir and deletes it; `export/xlsx.py` writes the Excel output.

The reference data is tied to the model by field names and ids, and the tests enforce the coupling:

- **`backend/app/schemas.py`** — Pydantic v2 model. `Job` → `FormGroup` (a run of consecutive pages in one file = one form) → `Commitment` (the 7 extracted fields) → generic `Field[T]` (value, raw_value, confidence, needs_review, reasons, source, bbox).
- **`backend/form_types/<id>.yaml`** — one file per observed form *layout* (25 types), plus `generic.yaml`, the fallback for unknown layouts (left out by `load_form_types`, loaded by `load_generic_form_type`). The file stem must equal `id`, and `Commitment.form_type_id` refers to it. `fields` must have exactly the 7 `Commitment` field names, each with exactly `labels`, `hint`, `multiple`, `handwritten_possible`. `certainty` is `certain` | `uncertain` (uncertain = no explicit commitment number found). Loaded and validated as `FormType` models by `classify.load_form_types`.
- **`backend/data/senders.json`** — closed list for `Commitment.sender` (loaded as `Sender` models by `normalize.load_senders`); the value must be a `canonical` name from it. Aliases/abbreviations are used for matching.
- **`docs/form_mapping.md`** — the human-readable table behind the YAML files; **`docs/edge_cases.md`** — numbered edge cases observed in `samples/`. Keep these in sync when you add or change a form type.

`tests/test_schemas.py` validates every YAML (parametrized) and senders.json, so a new form type or schema field change must update the YAML files and/or `REQUIRED_FIELDS` together.

## Rules that are not obvious from the code

- **Missing means empty and flagged, never guessed.** `Field` with `value=None` is forced by a validator to `needs_review=True`, `confidence=0`, `reasons ∋ "not_found"` — callers cannot clear it. Do not infer commitment numbers from barcodes/stickers, and do not fill in a missing year on a handwritten date silently (if ever inferred, flag it with a reason).
- `confidence` is computed by the (future) pipeline from several signals; `reasons` stores the signal codes (e.g. `id_checksum_failed`, `ocr_low_score`, `handwritten`, `conflicting_candidates`). A failed ID check digit is a soft signal only.
- `central_numbers` and `commitment_numbers` are lists: forms can carry several "מרכזי" stickers and several numbers; keep all (dedupe identical repeats). Numbers are strings (leading zeros matter).
- On Leumit, LabOS and Rambam forms the sample number ("דגימה") *is* the commitment number and goes in `commitment_numbers`.
- **Sender** = the body that sent the sample and is billed (HMO on HMO forms, hospital on hospital forms, "שם מבטח" on Sheba screenshots) — not the HMO printed on a hospital admission sticker.
- Page rotation must be detected from the image: PDF `/Rotate` is 0 on every sample page. All samples are image-only scans (OCR always needed).
- `PageImage.image` (sent to the VLM) is never binarized; binarization goes only into `ocr_image`. `original` keeps full resolution for crops; `image_scale` maps between them.
- Orientation is never guessed: if neither OSD nor the 4-rotation trial is clear, the page stays as is with flag `orientation_uncertain` (`orientation_unavailable` without Tesseract).
- **Segmentation never trusts the VLM alone:** its weight (0.8) is below the review threshold, so a boundary decided only by the model is flagged `split_uncertain`. `tests/test_segment_samples.py` needs `samples/` and Tesseract; on Windows `TESSDATA_PREFIX` must be an ASCII path (a Hebrew path makes Tesseract return garbage).
- Tesseract is optional at runtime (`create_ocr_engine` returns None); tests use fake `OcrEngine`s and must not require it.
- Names/IDs in `samples/` were replaced with handwriting for anonymization, so accuracy on samples will not reflect real data.

## Code quality and architecture

Follow clean-code principles and keep a clear architecture as the extraction code is added:

- **One responsibility per module.** Split the pipeline into separate modules by stage (e.g. PDF loading/rendering, rotation detection, OCR, field extraction, normalization/validation, Excel export) instead of one large file. Each stage takes and returns typed objects from `app/schemas.py`, not loose dicts.
- **Keep layers separate.** Domain logic (extraction rules, normalization, confidence scoring) must not depend on I/O, the web/API layer, or a specific OCR provider. Put external services (OCR engines, file storage) behind small interfaces so they can be swapped and mocked in tests.
- **Data over code for form-specific knowledge.** Form-type details belong in `backend/form_types/*.yaml` and sender names in `senders.json`; do not hard-code per-form `if` branches in the pipeline when a YAML field can express it.
- **Small, well-named functions** with type hints, no duplicated logic, no magic numbers or strings (name them as constants), and no dead or commented-out code.
- **Explicit errors.** Raise or record specific errors; never swallow exceptions silently. Uncertainty is expressed through `Field.reasons` / `needs_review`, not through guessed values.
- **Tests mirror the code layout:** every new module gets a matching test file under `backend/tests/`.

## Privacy

The models, `samples/` and the job database (`app.db`) contain patient data; the database holds no images and is git/docker-ignored. Never log field values (log ids/counts only); `Job.error` must not contain values. Write preview images to a temp directory and delete them afterward. Docs describe location and format only, never real values; test data must be dummy values.

## Language convention

Everything is in English (docs, comments, YAML descriptions) **except** text that must match or be exported verbatim: YAML `keywords`/`labels` (text printed on the forms, matched by OCR), sender names/aliases in `senders.json`, and quoted form text inside descriptions.

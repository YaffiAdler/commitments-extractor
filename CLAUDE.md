# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

An engine that reads commitment forms received by the Central Virology Laboratory (scanned PDFs, Hebrew), extracts fields, flags anything uncertain, and exports to Excel. **Currently Phase 2:** upload (PDF or image) -> `ingest` -> `preprocess` -> `extract` (provider) -> in-memory job -> Excel export, with a one-page React UI. segment/classify/normalize/confidence and the `local_vlm` provider are still stubs that raise `NotImplementedError`; each uploaded file is treated as one form.

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
cd backend && ../.venv/Scripts/uvicorn app.main:app --reload                       # API on :8000
.venv/Scripts/python eval/run_eval.py predictions.csv                              # accuracy -> eval/report.md

cd frontend && npm install && npm run dev      # :5173, proxies /api/* to the backend (BACKEND_URL)
cd frontend && npm run build                   # tsc + vite build
cd frontend && npm run lint                    # oxlint

docker compose up --build                      # backend + frontend
```

No Python linter or formatter is configured. `eval/run_eval.py` tests live in `backend/tests/test_run_eval.py`.

## Architecture

**Pipeline** (`backend/app/pipeline/`, one module per stage): `ingest` (PDF/image -> `PageImage` per page; type from magic bytes; typed `IngestError`s) -> `preprocess` (perspective -> deskew -> orientation -> illumination -> resize, plus a binarized `ocr_image`; image ops in `app/imaging/`, OCR behind `app/ocr/base.OcrEngine`) -> `segment` (pages -> forms) -> `classify` (form type id) -> `extract` (raw values via a provider) -> `normalize` (digits, dates, sender from the closed list) -> `confidence` (score + `needs_review`). `orchestrator.Orchestrator` runs them per file and fills `Job.groups`; on failure `Job.error` holds only the exception type.

**Providers** (`backend/app/providers/`): the pipeline depends only on `ExtractionProvider` (`base.py`); `ExtractionProvider` is a Protocol (`extract_form`, `reread_field`, `classify`); `create_provider(settings)` picks `claude`, `local_vlm` or `fake` (fixed dummy data, used by tests) from `CE_PROVIDER`. `main.create_app(settings, provider)` builds the API so tests can inject `FakeProvider`; `jobs.JobStore` keeps jobs in memory and expires them after `CE_JOB_TTL_S`. **Settings** (`config.py`) are read from `CE_*` env vars. `storage/tmp.py` keeps uploads under generated names in a per-job dir and deletes it; `export/xlsx.py` writes the Excel output.

The reference data is tied to the model by field names and ids, and the tests enforce the coupling:

- **`backend/app/schemas.py`** — Pydantic v2 model. `Job` → `FormGroup` (a run of consecutive pages in one file = one form) → `Commitment` (the 7 extracted fields) → generic `Field[T]` (value, raw_value, confidence, needs_review, reasons, source, bbox).
- **`backend/form_types/<id>.yaml`** — one file per observed form *layout* (25 types). The file stem must equal `id`, and `Commitment.form_type_id` refers to it. `fields` must have exactly the 7 `Commitment` field names, each with exactly `labels`, `hint`, `multiple`, `handwritten_possible`. `certainty` is `certain` | `uncertain` (uncertain = no explicit commitment number found). Loaded and validated as `FormType` models by `classify.load_form_types`.
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

The models and `samples/` contain patient data. Never log field values (log ids/counts only); `Job.error` must not contain values. Write preview images to a temp directory and delete them afterward. Docs describe location and format only, never real values; test data must be dummy values.

## Language convention

Everything is in English (docs, comments, YAML descriptions) **except** text that must match or be exported verbatim: YAML `keywords`/`labels` (text printed on the forms, matched by OCR), sender names/aliases in `senders.json`, and quoted form text inside descriptions.

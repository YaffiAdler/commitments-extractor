# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

An engine that reads commitment forms received by the Central Virology Laboratory (scanned PDFs, Hebrew), extracts fields, flags anything uncertain, and exports to Excel. **Currently Phase 9:** upload (PDF or image) -> `ingest` -> `preprocess` -> `segment` (pages -> forms) -> per form, concurrently: `classify` (form type + sender) -> `extract` (provider reading -> `Commitment`) -> `ocr_check` (Tesseract on printed numbers) -> `reread` (one narrow second look) -> `normalize` (pure normalizers + ok/soft_fail/hard_fail rules -> reason codes; duplicate central numbers across forms -> `central_duplicate`) -> `confidence` (weighted signals -> score + `needs_review`; weights/threshold in `config.ConfidenceSettings`, not calibrated) -> job store -> Excel export (`export/xlsx.py`: columns and multi-value mode from `config.ExportSettings`, uncertain fields orange with their reasons in a notes column), with a one-page React UI. The `local_vlm` provider is still a stub that raises `NotImplementedError`.

## Commands

Requires 64-bit Python 3.11 (PyMuPDF has no 32-bit wheel). The venv lives at the repo root; pytest runs from `backend/` (`pythonpath = ["."]`, so imports are `from app.schemas import ...`).

```bash
py -3.11 -m venv .venv
SETUPTOOLS_USE_DISTUTILS=stdlib .venv/Scripts/python -m pip install -c backend/requirements.lock -e "backend[dev]"   # env var works around a setuptools/distutils error on this machine
.venv/Scripts/python scripts/lock_requirements.py                                # after a dependency change: rewrite backend/requirements.lock (exact pins)
cd backend && ../.venv/Scripts/python -m pytest                                   # all tests
cd backend && ../.venv/Scripts/python -m pytest tests/test_schemas.py::test_job_json_roundtrip
cd backend && ../.venv/Scripts/python -m pytest -k "form_type_yaml_structure and clalit"   # one YAML
cd backend && TEST_DATABASE_URL=<postgres url> ../.venv/Scripts/python -m pytest  # also run the store/API tests on Postgres
.venv/Scripts/python scripts/render_previews.py [out_dir] [--dpi 150]             # samples/ pages -> PNG
.venv/Scripts/python scripts/make_synthetic_variants.py [out_dir]                # 6 distortions of a dummy form
.venv/Scripts/python scripts/preprocess_examples.py                              # -> docs/preprocess_examples/
.venv/Scripts/python scripts/classify_report.py                                  # stage 4 on samples/ -> eval/classify_report.md (needs a real provider)
.venv/Scripts/python scripts/extract_report.py                                   # stage 5 on samples/ -> eval/extract_report.md (counts only)
.venv/Scripts/python scripts/make_sample_output.py [out] [--columns]             # docs/sample_output.xlsx from fake-provider dummy data
.venv/Scripts/python scripts/check_gemini.py [model]                             # Gemini connection check with a blank dummy page
cd backend && ../.venv/Scripts/uvicorn app.main:app --reload                       # API on :8000 (default CE_PROVIDER=claude needs ANTHROPIC_API_KEY)
cd backend && CE_PROVIDER=fake ../.venv/Scripts/uvicorn app.main:app --reload      # no API key, fixed dummy data
cd backend && DATABASE_URL=<postgres url> ../.venv/Scripts/uvicorn app.main:app --reload  # jobs in Postgres (default: SQLite backend/data/app.db)
.venv/Scripts/python eval/run_eval.py predictions.csv                              # accuracy -> eval/report.md

cd frontend && npm ci && npm run dev           # :5173, proxies /api/* to the backend (BACKEND_URL); versions pinned exactly
cd frontend && npm run build                   # tsc + vite build
cd frontend && npm run lint                    # oxlint

docker compose up --build                      # backend + frontend
```

No Python linter or formatter is configured. `eval/run_eval.py` tests live in `backend/tests/test_run_eval.py`. All `CE_*` settings with defaults are listed in the README "Settings" table. README.md also holds the numbered design assumptions per phase and "Phase N changes to existing interfaces"; when a phase changes behaviour or an interface, add to those sections (mark superseded assumptions instead of deleting them).

## Architecture

**Pipeline** (`backend/app/pipeline/`, one module per stage): `ingest` (PDF/image -> `PageImage` per page; type from magic bytes; typed `IngestError`s) -> `preprocess` (perspective -> deskew -> orientation -> illumination -> resize, plus a binarized `ocr_image`; image ops in `app/imaging/`, OCR behind `app/ocr/base.OcrEngine`) -> `segment` (pages -> forms: one OCR fingerprint per page, weighted rules per adjacent pair, `provider.compare_pages` only for pairs below the review threshold; irrelevant pages go to `Job.irrelevant_pages`) -> `classify` (one provider call on the first page -> form type id, or `generic.yaml` + `FormGroup.form_reasons ∋ form_type_unknown`; the sender as read is matched to `senders.json` by `SenderMatcher`: rapidfuzz WRatio ≥ 88 with a 5-point gap to any other sender, else empty with `sender_candidate:` reasons) -> `extract` (`provider.extract_form` returns a raw `FormReading` — value, relative bbox, legible, source, note per field; `extract.to_commitment` turns it into a `Commitment`, its sender is replaced by the matched one) -> `ocr_check` (printed ID/central/commitment numbers: crop + Tesseract `--psm 7` digits whitelist, `ocr_*` reasons) -> `reread` (unconfirmed or illegible numbers: enlarged colour crop to `provider.read_number`, `reread_*` reasons) -> `normalize` (digits, dates, names from `raw_value`; rule outcomes become `Field.reasons`, `raw_value` is never changed; `pattern` from the YAML for commitment numbers) -> `confidence` (`score_fields`, pure: `FieldSignals` from the reason codes -> a `Scorer` — `WeightedScorer` now, a fitted `predict_proba` model via `ProbabilityModelScorer` later; `needs_review` below `CE_CONFIDENCE_REVIEW_THRESHOLD` or `illegible`; `Field.explanations` = reasons as Hebrew text from `reason_texts.py`). The sender's two readings (classify call and extract call) are compared by `classify.compare_sender_readings`. OCR and second-look results are extra `Field.signals`, never overwrites. `read_form.FormReader` reads one form; `orchestrator.Orchestrator` runs the forms of a file concurrently (`asyncio.run`; `calls.ProviderCalls`: shared semaphore, tenacity retries on `RetryableProviderError`/timeout, per-call timeout) and fills `Job.groups`; a failed form gets `FormGroup.error` (exception type) + `extraction_failed`, and the job fails only if every form failed (`Job.error` = exception type).

**Providers** (`backend/app/providers/`): the pipeline depends only on `ExtractionProvider` (`base.py`); `ExtractionProvider` is a Protocol (`extract_form`, `read_number`, `classify`, `compare_pages`); output JSON schemas, short instructions, image encoding and output parsing are shared in `common.py`, and the extraction / second-look prompts are versioned files in `backend/prompts/` (`prompts.py`: template + a block built from the form type's YAML; never edit a released version), so a vision provider only handles its own request/response format; `create_provider(settings)` picks `claude`, `gemini`, `local_vlm` or `fake` (fixed dummy data, used by tests) from `CE_PROVIDER`. `main.create_app(settings, provider)` builds the API so tests can inject `FakeProvider`; `runner.JobRunner` runs uploads in the background, at most `CE_MAX_CONCURRENT_JOBS` at once (the rest wait as `pending`), and saves progress (`Orchestrator.run(on_progress=...)`, `FormGroup.finished`) so `GET /jobs/{id}/events` (`events.py`, SSE) can stream it; **Job stores** (`backend/app/storage/`): the API depends only on the `JobStore` Protocol (`base.py`); `create_job_store(settings, tmp)` picks the store from `CE_STORAGE` if set, else `postgres` when `DATABASE_URL` is set (unprefixed, as Render names it), else `sqlite` (`CE_DB_PATH`); `memory` keeps nothing on disk (docker compose, most tests). `sqlite` and `postgres` share every query in `storage/sql.py` (`SqlJobStore`); the schema is portable SQL as numbered migrations in `backend/data/migrations/` (never edit a released one, add the next), applied by `init()` at startup under a lock and recorded in `schema_version`. Driver errors become `JobStoreError` carrying the driver's exception type only (API 503). `get` returns a copy, so a changed job must be saved with `update`. Stores expire jobs after `CE_JOB_TTL_S` and temp dirs after `CE_FILE_TTL_S`; `delete` removes the rows of every table and the job's temp files. Page images for the crop endpoint live in the temp dir (`storage/page_images.py`), never in the DB. Manual corrections (`PATCH .../fields/{field}`) are in `app/corrections.py`. Tests run `test_main.py`/`test_e2e.py` against every store (`conftest.py`; Postgres only with `TEST_DATABASE_URL`, one schema per test) and `test_job_stores.py` is the shared contract. **Settings** (`config.py`) are read from `CE_*` env vars. `storage/tmp.py` keeps uploads under generated names in a per-job dir and deletes it; `export/xlsx.py` writes the Excel output.

The reference data is tied to the model by field names and ids, and the tests enforce the coupling:

- **`backend/app/schemas.py`** — Pydantic v2 model. `Job` → `FormGroup` (a run of consecutive pages in one file = one form) → `Commitment` (the 7 extracted fields) → generic `Field[T]` (value, raw_value, confidence, needs_review, reasons, source, bbox).
- **`backend/form_types/<id>.yaml`** — one file per observed form *layout* (25 types), plus `generic.yaml`, the fallback for unknown layouts (left out by `load_form_types`, loaded by `load_generic_form_type`). The file stem must equal `id`, and `Commitment.form_type_id` refers to it. `fields` must have exactly the 7 `Commitment` field names, each with exactly `labels`, `hint`, `multiple`, `handwritten_possible`, plus an optional `pattern` (regex, `commitment_numbers` only) and an optional `optional` (bool, default false: the layout may lack the field, so an empty value is not flagged). `certainty` is `certain` | `uncertain` (uncertain = no explicit commitment number found). Loaded and validated as `FormType` models by `classify.load_form_types`.
- **`backend/data/senders.json`** — closed list for `Commitment.sender` (loaded as `Sender` models by `normalize.load_senders`); the value must be a `canonical` name from it. Aliases/abbreviations are used for matching.
- **`docs/form_mapping.md`** — the human-readable table behind the YAML files; **`docs/edge_cases.md`** — numbered edge cases observed in `samples/`. Keep these in sync when you add or change a form type.

`tests/test_schemas.py` validates every YAML (parametrized) and senders.json, so a new form type or schema field change must update the YAML files and/or `REQUIRED_FIELDS` together.

## Rules that are not obvious from the code

- **Missing means empty and flagged, never guessed.** `Field` with `value=None` is forced by a validator to `needs_review=True`, `confidence=0`, `reasons ∋ "not_found"` — callers cannot clear it; the only exception is a field marked `optional: true` in the form type's YAML, tagged `optional_missing` by the confidence stage (no YAML sets it yet). Do not infer commitment numbers from barcodes/stickers, and do not fill in a missing year on a handwritten date silently (if ever inferred, flag it with a reason).
- `confidence` is computed by the confidence stage from several signals, never from the model's own claim; `reasons` stores the signal codes (e.g. `id_checksum_failed`, `ocr_agrees`, `handwritten`, `single_reading`). A failed ID check digit is a soft signal only. A new reason code needs a Hebrew text in `reason_texts.py`, and a new stage 6 rule code needs a severity in `confidence.HARD_FAIL_REASONS`/`SOFT_FAIL_REASONS` (tests enforce both). Weights live in `config.py`, never in code.
- `central_numbers` and `commitment_numbers` are lists: forms can carry several "מרכזי" stickers and several numbers; keep all (dedupe identical repeats). Numbers are strings (leading zeros matter).
- On Leumit, LabOS and Rambam forms the sample number ("דגימה") *is* the commitment number and goes in `commitment_numbers`.
- **Sender** = the body that sent the sample and is billed (HMO on HMO forms, hospital on hospital forms, "שם מבטח" on Sheba screenshots) — not the HMO printed on a hospital admission sticker.
- Page rotation must be detected from the image: PDF `/Rotate` is 0 on every sample page. All samples are image-only scans (OCR always needed).
- `PageImage.image` (sent to the VLM) is never binarized; binarization goes only into `ocr_image`. `original` keeps full resolution for crops; `image_scale` maps between them.
- Orientation is never guessed: if neither OSD nor the 4-rotation trial is clear, the page stays as is with flag `orientation_uncertain` (`orientation_unavailable` without Tesseract).
- **Segmentation never trusts the VLM alone:** its weight (`CE_SEGMENT_WEIGHT_VLM`, 0.8; `Settings` refuses a value at or above `CE_REVIEW_THRESHOLD`) is below the review threshold, so a boundary decided only by the model is flagged `split_uncertain`. `tests/test_segment_samples.py` needs `samples/` and Tesseract; on Windows `TESSDATA_PREFIX` must be an ASCII path (a Hebrew path makes Tesseract return garbage).
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

Deployment guidelines (apply to ALL stages): The project must run in a Linux container and be deployable as a demo (frontend on Vercel, backend on Render). Therefore: use pathlib only for paths; all settings and secrets come from environment variables (including PORT, TMP_DIR, TESSERACT_CMD, ALLOWED_ORIGINS); no persistent state on disk; heavy processing runs in the background with SSE progress; memory, file-size and page-count limits live in config; provide a lightweight unauthenticated /health endpoint; logs go to stdout with no field values; pin dependency versions. If a stage requires deviating from any of these, report it before implementing. The `CE_` prefix on environment variables is deliberate: the guideline names map to `CE_TMP_DIR`, `CE_TESSERACT_CMD`, `CE_ALLOWED_ORIGINS` and so on. `PORT` is the exception, read without a prefix because Render sets it.

## Privacy

The models, `samples/` and the job database (SQLite `app.db` or the Postgres at `DATABASE_URL`) contain patient data; the database holds no images, and `app.db` is git/docker-ignored. `DATABASE_URL` holds a password: never log it (it is left out of `Settings`' repr). Never log field values (log ids/counts only); for exceptions log the type only, never the message or traceback (`logger.exception` is not used); `Job.error` must not contain values. Write preview images to a temp directory and delete them afterward. Docs describe location and format only, never real values; test data must be dummy values.


## Language convention

Everything is in English (docs, comments, YAML descriptions) **except** text that must match or be exported verbatim: YAML `keywords`/`labels` (text printed on the forms, matched by OCR), sender names/aliases in `senders.json`, and quoted form text inside descriptions.

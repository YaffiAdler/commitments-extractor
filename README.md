# commitments-extractor

An engine that reads commitment forms received by the Central Virology Laboratory, extracts their fields, flags anything uncertain and exports to Excel.

**Status: Phase 3 (splitting a page stack into forms)** — upload a PDF or an image, straighten and clean every page, split the pages into forms, extract each form through a provider, download an Excel file. Wired: `ingest -> preprocess -> segment -> extract`; classify, normalize and confidence are still stubs (`NotImplementedError`). No extraction accuracy has been measured.

## Layout
```
backend/app/main.py           FastAPI app: /jobs upload, status, export, delete
backend/app/config.py         settings: paths, provider, review threshold (env vars CE_*)
backend/app/schemas.py        data model (Pydantic v2)
backend/app/pipeline/         one module per stage: ingest -> preprocess -> segment -> classify
                              -> extract -> normalize -> confidence, wired by orchestrator.py
backend/app/imaging/          pure image operations used by preprocess (geometry, deskew,
                              perspective, illumination, orientation)
backend/app/ocr/              OcrEngine Protocol (base.py) + Tesseract
backend/app/providers/        ExtractionProvider Protocol (base.py) + claude / gemini / local_vlm / fake (shared prompt/schema/parsing in common.py)
backend/app/export/xlsx.py    Excel export
backend/app/storage/          JobStore Protocol (base.py) + sqlite / memory job stores, tmp.py: temporary per-job files
backend/form_types/*.yaml     one definition per observed form type (25 types)
backend/data/senders.json     closed list of senders
backend/data/schema.sql       SQLite job store schema
backend/tests/                pytest
frontend/                     React + Vite + TypeScript
eval/                         ground_truth.csv, run_eval.py -> report.md
docs/form_mapping.md          form type mapping table
docs/edge_cases.md            edge cases actually observed
scripts/render_previews.py    renders samples/ pages to PNG for manual review
scripts/make_synthetic_variants.py   6 synthetic distortions of a page (used by the tests)
scripts/preprocess_examples.py       before/after images -> docs/preprocess_examples/
```

## Running
```bash
# backend — Python 3.11, 64-bit (PyMuPDF has no 32-bit wheel)
# Tesseract 5 with heb + osd data is needed for orientation detection
# (Windows: winget install UB-Mannheim.TesseractOCR; Debian: tesseract-ocr tesseract-ocr-heb).
# Without it the pipeline still runs, but pages are not turned and are flagged.
py -3.11 -m venv .venv
SETUPTOOLS_USE_DISTUTILS=stdlib .venv/Scripts/python -m pip install -e "backend[dev]"
cd backend && ../.venv/Scripts/python -m pytest
cd backend && ../.venv/Scripts/uvicorn app.main:app --reload          # http://localhost:8000

# run without an API key (fixed dummy data):
CE_PROVIDER=fake ../.venv/Scripts/uvicorn app.main:app --reload       # from backend/
# run with Claude:
ANTHROPIC_API_KEY=... ../.venv/Scripts/uvicorn app.main:app --reload  # from backend/
# run with Gemini:
CE_PROVIDER=gemini GEMINI_API_KEY=... ../.venv/Scripts/uvicorn app.main:app --reload  # from backend/

# frontend — proxies /api to the backend
cd frontend && npm install && npm run dev                             # http://localhost:5173

# both in Docker
docker compose up --build

# evaluation
.venv/Scripts/python eval/run_eval.py predictions.csv                 # writes eval/report.md
```

### Trying it (Phase 1)
1. Start the backend (e.g. with `CE_PROVIDER=fake`) and the frontend.
2. Open http://localhost:5173, click "העלאת קובץ (PDF או תמונה)" and pick a file.
3. When the status shows "הסתיים", a row appears in the table; click "ייצוא לאקסל".
Fields that need review are highlighted (in the table and in the Excel file).

### API
| Method | Path | |
|---|---|---|
| POST | `/jobs` | multipart `file` (PDF, PNG, JPEG, TIFF or WEBP — detected from the content, up to `CE_MAX_UPLOAD_MB`); returns the job, processing runs in the background |
| GET | `/jobs/{id}` | job status and extracted groups |
| POST | `/jobs/{id}/export` | the xlsx file (409 if the job is not `done`) |
| DELETE | `/jobs/{id}` | deletes the job and its files |

### Settings (env vars)
| Variable | Default | |
|---|---|---|
| `CE_PROVIDER` | `claude` | `claude` / `gemini` / `local_vlm` / `fake` |
| `ANTHROPIC_API_KEY` | — | required for `claude` |
| `CE_CLAUDE_MODEL` | `claude-opus-5-5` | |
| `CE_CLAUDE_TIMEOUT_S` | `60` | per API call |
| `GEMINI_API_KEY` | — | required for `gemini` |
| `CE_GEMINI_MODEL` | `gemini-3.8-flash` | |
| `CE_GEMINI_TIMEOUT_S` | `180` | per API call (thinking models are slow on full pages) |
| `CE_STORAGE` | `sqlite` | `sqlite` (survives restarts) / `memory` |
| `CE_DB_PATH` | `backend/data/app.db` | SQLite file; created at startup |
| `CE_JOB_TTL_S` | `3600` | jobs and their files are deleted after this (both stores) |
| `CE_MAX_UPLOAD_MB` | `50` | |
| `CE_TMP_DIR` | system temp `/commitments-extractor` | |
| `CE_RENDER_DPI` | `250` | for PDF pages that are not a single full-page scan |
| `CE_MAX_PAGES` | `200` | per file (PDF pages or TIFF frames) |
| `CE_TESSERACT_CMD` | — | path to `tesseract`; empty = find on PATH |
| `CE_OCR_LANGUAGES` | `heb+eng` | languages that are not installed are dropped |
| `CE_OCR_TIMEOUT_S` | `30` | per Tesseract call |
| `TESSDATA_PREFIX` | — | Tesseract's own variable: folder with the `heb`/`eng`/`osd` `.traineddata` files. On Windows it must be an ASCII path (see 31) |
| `CE_PREPROCESS_PERSPECTIVE` | `1` | every preprocessing step can be switched off with `0` |
| `CE_PREPROCESS_DESKEW` | `1` | |
| `CE_PREPROCESS_ORIENTATION` | `1` | |
| `CE_PREPROCESS_ILLUMINATION` | `1` | |
| `CE_PREPROCESS_RESIZE` | `1` | |
| `CE_PREPROCESS_BINARIZE` | `1` | the separate Tesseract copy |
| `CE_DESKEW_MIN_ANGLE_DEG` | `0.5` | smaller skew is left alone |
| `CE_DESKEW_MAX_ANGLE_DEG` | `10` | larger estimates are not trusted |
| `CE_OSD_MIN_CONFIDENCE` | `2.0` | below this, the 4-rotation OCR trial decides |
| `CE_VLM_MAX_EDGE_PX` | `2000` | longer side of the image sent to the VLM |

## Assumptions (Phase 1)
10. *(Superseded by 25.)* **One file = one form.** Segmentation is not implemented yet, so all pages of an uploaded file are sent as a single form and produce a single row. A multi-form scan therefore gives one (wrong) row until Phase "segment".
11. **No confidence yet.** Every extracted field keeps `confidence=0` and `needs_review=True` (highlighted) because scoring is not wired. The provider never sets confidence.
12. *(Superseded by 24.)* **Jobs are kept in memory** (lost on restart); leftover job directories on disk are deleted at startup and on access once older than the TTL. TTL is checked lazily on requests, not by a timer.
13. **Claude provider** uses the Messages API over `httpx` with a forced tool call for structured JSON output. Pages are downscaled to at most 2000 px and sent as JPEG (minimal data out). A date the model cannot give as `YYYY-MM-DD` stays empty with reason `unparsed_date`. Errors carry only the HTTP status, never the response body.
14. *(Superseded by 15.)* Upload validation used to check the `%PDF-` header only.

## Assumptions (Phase 2)
15. **File type comes from the magic bytes** (PDF, PNG, JPEG, TIFF, WEBP), never from the name; a PNG named `.pdf` is processed as a PNG. HEIC (iPhone) is **not** supported yet.
16. **Full-page scans are extracted, not rendered.** A PDF page whose only content is one upright image covering ≥95% of the page is read at the image's native resolution (300 DPI on every sample page). Any other page (text, drawings, several images, rotated placement, soft mask) is rendered at `CE_RENDER_DPI` (250). `PageImage.dpi` is `None` for photos without DPI metadata.
17. **Chain order: perspective → deskew → orientation → illumination → resize → binarize.** Geometry runs first on full resolution, so `original` (for crops) and `ocr_image` share coordinates, and `image` (for the VLM) is the same picture scaled by `PageImage.image_scale`. Deskew comes before orientation because OSD reads straight lines better and the skew estimate does not depend on the quarter turn.
18. **Orientation is never guessed.** OSD with confidence ≥ `CE_OSD_MIN_CONFIDENCE` is used; otherwise all 4 quarter turns are OCR'd and the best wins only if it beats the runner-up by 25%. Otherwise the page stays as is and is flagged `orientation_uncertain`; without Tesseract, `orientation_unavailable`. These codes live in `PageImage.flags` and are not yet carried into `Field.reasons` (that belongs to the confidence stage).
19. **Deskew range 0.5°–10°.** Below 0.5° nothing is done (no resampling for nothing); above 10° the estimate is treated as unreliable and ignored. Rotation enlarges the canvas with white so no corner is cut off.
20. **Perspective is corrected only when the page outline is clear:** a convex quadrilateral covering 20%–97% of the frame whose inside is clearly brighter (≥40 gray levels) than the outside. A table border on a white scan does not qualify, so scans are left untouched.
21. **Illumination works on the LAB lightness channel only**, so stamp and sticker colours reach the VLM. Pixels at ≥95% of the local background brightness become white (removes banding on dark photos); faint pencil darker than that survives.
22. **Iron rule:** the VLM image is never binarized; `ocr_image` is a separate adaptive-threshold copy for Tesseract.
23. **Gemini provider** (`CE_PROVIDER=gemini`) calls `generateContent` over `httpx` with `responseMimeType: application/json` and `responseJsonSchema` set to the same output schema the Claude tool uses (`providers/common.py`), so both providers get the same prompt, the same image encoding and the same parsing. A blocked prompt or a truncated answer (`finishReason` other than `STOP`) is a `ProviderError` carrying only the reason code. HTTP 503 (overload) and 429 (rate limit) are retried up to 4 attempts with waits of 2, 4 and 8 s.

24. **Jobs are stored in SQLite by default** (`CE_STORAGE=sqlite`, file `CE_DB_PATH`), so they survive a restart; `CE_STORAGE=memory` keeps the old behaviour. The TTL still applies to both: jobs older than `CE_JOB_TTL_S` are deleted with their temporary files.

## Assumptions (Phase 3 — segmentation)
25. **Splitting.** `pipeline/segment.py` decides every boundary between two adjacent pages of a file. A single-page file (and every single-image upload) is one form, with no OCR and no VLM call. Output: `Segmentation{groups: [FormGroup{pages, split_confidence, split_reasons}], irrelevant_pages, vlm_calls}`; the orchestrator then extracts each group from its own pages only.
26. **Fingerprint = one OCR pass per page** (`OcrEngine.read_words`, word boxes): keywords from `detect.keywords` in the top 30% of the page, the word "מרכזי" and any 6-digit number within 6 word heights of it, the word "התקבל", a page marker ("2/2", "Page 1 of 2", "עמוד 1 מתוך 2") within 15% of the top/bottom edge, the word count and the share of ink. Matching is fuzzy (rapidfuzz: ≥80 on single words, `partial_ratio` ≥90 on keywords of 8+ letters). Keywords that also match the lab's own "התקבל" stamp text are not used as titles, because that stamp is on almost every page.
27. **Rules and confidence.** Signals for "B starts a form": a known title, a new central number, a "מרכזי" sticker whose number was not read, a "התקבל" stamp, marker "1/N", the form type on A has `pages.typical: 1`. Signals for "B continues A": a sparse page (<15 words and <1% ink), the same central number as A, marker "k/N" (strongest when A is "k-1/N"), A was "1/N" or its type has `pages.typical` > 1. Each signal has a fixed weight (`WEIGHT_*` in `segment.py`, **chosen by hand, not fitted**); each side scores `1 − ∏(1 − w)`, and the boundary confidence is `winner × (1 − loser)`. A group's `split_confidence` is the lowest confidence among the boundary that opens it, those inside it and the one that closes it.
28. **The VLM is asked only below `CE_REVIEW_THRESHOLD`** (0.85): pages A and B are sent together to `ExtractionProvider.compare_pages`, which returns `{"same_form", "reason", "page_b_type": "start|continuation|irrelevant"}` (`PageRelation`, schema `PAGE_RELATION_SCHEMA`). The answer is added as one more signal with weight 0.8 (0.5 if `same_form` and `page_b_type` contradict each other), so **the model alone never clears a boundary**: it must agree with a rule, otherwise the group gets `split_uncertain` ("ייתכן שפוצל לא נכון", in its own Excel column and in the UI). `reason` is model text that may quote the page, so it is never logged or stored. Without Tesseract, or when OCR fails on a page, the rules have nothing to go on and every such boundary goes to the VLM (`ocr_unavailable` / `ocr_failed` in `split_reasons`).
29. **Irrelevant pages** (only the VLM can mark a page irrelevant) belong to no group; they are kept in `Job.irrelevant_pages`, listed on a second Excel sheet and under the table in the UI. A continuation after an irrelevant page joins the form before it, so a group's page list may skip that page.
30. **Expected split of the samples — from my own review of the page images** (a contact sheet of all 48 pages, cross-checked with `docs/form_mapping.md`): **every page is a separate one-page form** — part 1: 26 forms (pages 1–26), part 2: 22 forms (pages 1–22). Boundaries I am less sure of, recorded as assumptions: consecutive pages with the same layout (P1 p2–4, p15–16, p17–18, p22–23; P2 p4–5, p6–7, p8–9, p10–11, p12–13, p14–15, p16–17, p18–19, p20–22) — I took each page as a separate form, as in `form_mapping.md` (each has its own sticker/stamp and its own request). P2 p3 (Assuta) says "Page 1 of 2", but page 2 is not in the file, so it is one form; the same goes for Maccabi ONLINE (P1 p10, "1/2"). The low-resolution review is not enough to compare patient details between pages of the same layout; *this needs confirmation from the lab.*
31. **Windows:** Tesseract cannot read a `tessdata` folder under a non-ASCII path (e.g. a Hebrew user name); OCR then runs but returns garbage. Put the `.traineddata` files in an ASCII folder (e.g. `C:\Users\Public\tessdata`) and point `TESSDATA_PREFIX` at it. `heb.traineddata` (from `tessdata_fast`) is not part of the UB-Mannheim installer.

### Segmentation — what was checked
- **Unit tests** (`tests/test_segment.py`, fake OCR and a fake provider, no Tesseract): fingerprint, each rule, VLM fallback only for unclear pairs, a VLM-only answer is flagged, irrelevant pages are kept, OCR failure / no OCR, single page, the confidence arithmetic.
- **Samples** (`tests/test_segment_samples.py`; needs `samples/` and Tesseract with heb, otherwise skipped; ~2.5 minutes): both files split exactly as in assumption 30, with no irrelevant pages. **VLM calls: 2 of 25 boundaries in part 1 and 8 of 21 in part 2 — 10 of 46 (22%); 36 boundaries (78%) were decided by rules alone.** In that test the provider is a fake that answers "start" (the correct answer for these files), so the result does not depend on a real model; **the answer of a real Claude/Gemini on those 10 pairs was not measured.** With a provider that always answers wrong ("continuation"), the wrong merges come out flagged `split_uncertain`.

### Segmentation — what does not work / was not checked
- The rules have only seen one-page forms. Joining pages (markers, sparse pages, `pages.typical: 2`) is tested on synthetic data only, because the samples have no complete multi-page form.
- The weights are hand-picked, and the 22% VLM share comes from 2 files with 48 pages; nothing was measured on a larger set.
- The pages the rules did not decide are mostly faxes, rotated or faint pages where OCR found no title (Bnei Zion, Barzilai, Laniado, Meir, Sheba). Better keywords in their YAML files would lower the VLM share; I did not change the YAML files in this phase.
- OCR time: about 6 s per page (one full-page pass, heb+eng) on top of preprocessing.
- `local_vlm.compare_pages` raises `NotImplementedError`, like the rest of that provider.
- Forms whose pages are not adjacent in the file (interleaved scans) are not supported.

### Phase 3 changes to existing interfaces
- `segment.segment(pages)` (stub) → `segment(pages, ocr, form_types, provider, threshold) -> Segmentation`.
- `ExtractionProvider`: new method `compare_pages(page_a, page_b) -> PageRelation` (claude, gemini, fake; local_vlm is a stub).
- `OcrEngine`: new method `read_words(image) -> list[OcrWord]`.
- `FormGroup`: new `split_confidence`, `split_reasons` and a `needs_review` property (commitment or split). `Job`: new `irrelevant_pages`, `split_vlm_calls`. New models: `IrrelevantPage`, `PageRelation`, `Segmentation`.
- SQLite `jobs`: new columns `irrelevant_pages_json`, `split_vlm_calls`. The `needs_review` columns now include the split flag.
- Excel: new columns "עמודים" and "פיצול", plus a sheet "עמודים לא רלוונטיים" when there are such pages.
- New dependency: `rapidfuzz`.

## Storage
- **Interface:** `app/storage/base.py` defines the `JobStore` Protocol (`init`, `create`, `get`, `update`, `delete`, `list_commitments`, `fail_interrupted`, `purge_expired`); `create_job_store(settings, tmp)` builds `SqliteJobStore` (`storage/sqlite.py`) or `MemoryJobStore` (`storage/memory.py`). The API endpoints did not change.
- **Schema** (`backend/data/schema.sql`, applied by `init_db()` at FastAPI startup; idempotent, no migrations):
  - `jobs(id, created_at, status, source_file, form_type, sender, error, needs_review, irrelevant_pages_json, split_vlm_calls)` — `form_type`/`sender` are set when all forms of the job share them; columns added after the first schema are added to an older database by `init_db()`.
  - `commitments(id, job_id → jobs ON DELETE CASCADE, seq, data_json, needs_review)` — `data_json` is the whole `FormGroup` (pages, rotation and the full `Commitment` with every `Field`); only filter fields are columns.
- Every connection sets `PRAGMA foreign_keys = ON`; deleting a job deletes its commitment rows and its temporary upload directory.
- **No images** are stored in the database, and no field values are logged.
- **Privacy:** the database holds patient data. `*.db` is git- and docker-ignored; in Docker it lives in the `job-db` volume (`/var/lib/commitments-extractor/app.db`). Delete the file (or the volume) to wipe all jobs.
- **Limits:** a job is saved when it is created, when it starts running and when it finishes. A job that was `pending` or `running` when the server stopped is not resumed: at startup it is marked `failed` with error `Interrupted`, and the file has to be uploaded again. Run a single server process per database (startup would fail the jobs of another running process). A job may hold one file only (the API uploads one per job); storing more raises `ValueError` instead of dropping files.

### Phase 2 changes to existing interfaces
- `ingest.load_pages(pdf_path, dpi)` → `ingest.load_file(path, limits)`.
- `PageImage`: `source_file` → `file_id`, `page_index` → `page_no`; `image` is now a BGR `numpy` array (was PNG bytes); new `original_path`, `original`, `ocr_image`, `skew_deg`, `flags`, `image_scale`; `dpi` may be `None`.
- `preprocess.preprocess(page)` → `preprocess(page, settings.preprocess, ocr)`; `detect_rotation` is replaced by `app.imaging.orientation.detect_orientation`.
- `Orchestrator(...)` takes an optional `ocr: OcrEngine`; the `CE_RENDER_DPI` default changed from 300 to 250.
- `app/jobs.py` (`JobStore` class) → `app/storage/` (`JobStore` Protocol + `create_job_store`); `add` → `create`, and `get` returns a copy, so changes are saved with `update`.
- `ClaudeProvider` encodes the numpy image with OpenCV (still ≤2000 px, JPEG quality 85).

### Preprocessing — what was checked and what was not
**Tests** (pytest, synthetic dummy form only): all 6 variants (rotate 90/180, skew 5°, dark, perspective, noise) come out upright with the top table rule within **0.5°** of level, measured by an independent check (`tests/image_checks.py`); the 5° skew is measured as −5.0° ± 0.3°; corrupt, encrypted, oversized and unsupported files raise a typed `IngestError` and fail the job without crashing the server. Orientation is tested with fake OCR engines, because **Tesseract is not installed on the development machine** (its installer was cancelled). So `test_real_tesseract_rotated_dummy_form` is skipped, and real OSD accuracy on the samples is **not measured**.

**Samples** (48 pages, run once, only angles and sizes printed): 10 pages were deskewed (0.5°–5.1°), none got a perspective warp, every VLM image is ≤2000 px, about 0.9 s per page without OCR. Three deskewed pages were checked visually and are straight; no accuracy number is claimed.

**Not handled yet:**
- Orientation without Tesseract: pages stay as scanned and are flagged.
- A page with little text (e.g. mostly a sticker) may stay `orientation_uncertain` even with Tesseract.
- Skew above 10° and non-planar distortion (folded or curled paper) are not corrected.
- Perspective when the page fills the whole photo or lies on a white table: no outline is found, so there is no warp (left alone by design).
- Several pages or forms in one photo, HEIC, and PDF pages made of several images (rendered, so not at native resolution).
- Heavy blur and JPEG artifacts are not treated.
- `docs/preprocess_examples/` shows the synthetic dummy form (skew, perspective, dark), not samples/ pages, because those contain patient data.

## Assumptions (Phase 0)
1. **Sender** = the body that sent the sample and is billed: the HMO on HMO forms, the hospital on hospital forms, and on the Sheba screenshots — the "שם מבטח" (insurer name). The HMO shown on a hospital admission sticker is **not** the sender. *Needs confirmation from the lab.*
2. **The closed list** also includes senders that are neither an HMO nor a hospital, because they were observed in the samples: the Ministry of Defense, the Palestinian Authority, medical tourism, the IDF, forensic medicine and Rosenblatt. *Needs confirmation.*
3. **"Central number"** = a 6-digit sticker with the word "מרכזי", or a number handwritten in its place. All stickers on a form are kept.
4. **"דגימה" (sample number) as the commitment number** — on Leumit, LabOS and Rambam forms the form itself states that the sample number serves as the commitment number, so it is stored in `commitment_numbers`.
5. **9 types have no explicit commitment number** (marked `uncertain`). During extraction the number will stay empty and flagged; we will not guess a number from a barcode or sticker.
6. **Handwritten received date without a year** (e.g. `-31.8`) — the year is not filled in silently. If in the future the year is inferred from context, the value will be flagged for review with a matching reason.
7. **Names and ID numbers in the samples were replaced by handwriting** for anonymization. Real forms are assumed to have them printed or on a sticker, so accuracy on the samples will not reflect accuracy on real data.
8. **Form type = layout** — every distinct layout gets its own YAML, even within the same organization (e.g. Clalit: Form 17, 293/17, LabOS).
9. **Rotation** is determined from the image, because the PDF `/Rotate` is 0 on every page.

## Privacy
- Never write field values to logs. `Job.error` must not contain values.
- Preview images contain medical data: write them to a temp directory and delete them after review.
- The documents in `docs/` describe location and format only, never values.

## Schema principles
- A field that was not found: `value=None`, `needs_review=True`, `reasons` includes `not_found`, `confidence=0`. The validator enforces this, and the flag cannot be "cleared".
- `confidence` is computed in the pipeline from several signals; `reasons` keeps the signal codes.
- A failed ID-number check digit is a soft reason (`id_checksum_failed`) and does not invalidate the value.

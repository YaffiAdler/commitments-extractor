# commitments-extractor

An engine that reads commitment forms received by the Central Virology Laboratory, extracts their fields, flags anything uncertain and exports to Excel.

**Status: Phase 2 (ingest + image preprocessing)** — upload a PDF or an image, straighten and clean every page, extract through a provider, download an Excel file. Wired: `ingest -> preprocess -> extract`; segment, classify, normalize and confidence are still stubs (`NotImplementedError`). No extraction accuracy has been measured.

## Layout
```
backend/app/main.py           FastAPI app: /jobs upload, status, export, delete
backend/app/jobs.py           in-memory job registry with TTL expiry
backend/app/config.py         settings: paths, provider, review threshold (env vars CE_*)
backend/app/schemas.py        data model (Pydantic v2)
backend/app/pipeline/         one module per stage: ingest -> preprocess -> segment -> classify
                              -> extract -> normalize -> confidence, wired by orchestrator.py
backend/app/imaging/          pure image operations used by preprocess (geometry, deskew,
                              perspective, illumination, orientation)
backend/app/ocr/              OcrEngine Protocol (base.py) + Tesseract
backend/app/providers/        ExtractionProvider Protocol (base.py) + claude / local_vlm / fake
backend/app/export/xlsx.py    Excel export
backend/app/storage/tmp.py    temporary per-job files + deletion
backend/form_types/*.yaml     one definition per observed form type (25 types)
backend/data/senders.json     closed list of senders
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
| `CE_PROVIDER` | `claude` | `claude` / `local_vlm` / `fake` |
| `ANTHROPIC_API_KEY` | — | required for `claude` |
| `CE_CLAUDE_MODEL` | `claude-opus-5-5` | |
| `CE_CLAUDE_TIMEOUT_S` | `60` | per API call |
| `CE_JOB_TTL_S` | `3600` | jobs and their files are deleted after this |
| `CE_MAX_UPLOAD_MB` | `50` | |
| `CE_TMP_DIR` | system temp `/commitments-extractor` | |
| `CE_RENDER_DPI` | `250` | for PDF pages that are not a single full-page scan |
| `CE_MAX_PAGES` | `200` | per file (PDF pages or TIFF frames) |
| `CE_TESSERACT_CMD` | — | path to `tesseract`; empty = find on PATH |
| `CE_OCR_LANGUAGES` | `heb+eng` | languages that are not installed are dropped |
| `CE_OCR_TIMEOUT_S` | `30` | per Tesseract call |
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
10. **One file = one form.** Segmentation is not implemented yet, so all pages of an uploaded file are sent as a single form and produce a single row. A multi-form scan therefore gives one (wrong) row until Phase "segment".
11. **No confidence yet.** Every extracted field keeps `confidence=0` and `needs_review=True` (highlighted) because scoring is not wired. The provider never sets confidence.
12. **Jobs are kept in memory** (lost on restart); leftover job directories on disk are deleted at startup and on access once older than the TTL. TTL is checked lazily on requests, not by a timer.
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

### Phase 2 changes to existing interfaces
- `ingest.load_pages(pdf_path, dpi)` → `ingest.load_file(path, limits)`.
- `PageImage`: `source_file` → `file_id`, `page_index` → `page_no`; `image` is now a BGR `numpy` array (was PNG bytes); new `original_path`, `original`, `ocr_image`, `skew_deg`, `flags`, `image_scale`; `dpi` may be `None`.
- `preprocess.preprocess(page)` → `preprocess(page, settings.preprocess, ocr)`; `detect_rotation` is replaced by `app.imaging.orientation.detect_orientation`.
- `Orchestrator(...)` takes an optional `ocr: OcrEngine`; the `CE_RENDER_DPI` default changed from 300 to 250.
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

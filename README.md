אתה מהנדס תוכנה בכיר שבונה את "commitments-extractor": מנוע שקורא
טפסי התחייבות של המעבדה המרכזית לנגיפים (מודפסים, סרוקים, מצולמים,
כתובים ביד), מחלץ שדות, מסמן מה שאינו בטוח בו, ומייצא לאקסל.
שדות: שם פרטי, שם משפחה, ת.ז., מספר מרכזי (כמה אפשריים, שומרים
את כולם), תאריך קבלת הדגימה, גורם שולח (רשימה סגורה), מספר
התחייבות ("דרישה" ובלאומית "דגימה"; כמה אפשריים, שומרים את כולם).
מיקומים: מספר מרכזי על מדבקה עם המילה "מרכזי" או בכתב יד; תאריך
קבלה בחותמת "התקבל" או בכתב יד מתחת למספר המרכזי.
עקרונות: (1) שדה שלא נמצא נשאר ריק ומסומן, לעולם לא מנוחש;
(2) ביטחון נגזר מכמה אותות ולא מהצהרת המודל; (3) פרטיות: אין ערכי
שדות בלוגים, מחיקת קבצים זמניים, שליחה מינימלית לשירותים חיצוניים,
הספק מאחורי ממשק ExtractionProvider; (4) כשל ספרת ביקורת בת.ז. הוא
אות רך בלבד (פרטי הדוגמה שונו); (5) אין לוגיקה קשיחה לכל קופה בקוד,
הגדרות בקובצי YAML.
טכנולוגיה: Python 3.11, FastAPI, Pydantic, PyMuPDF, OpenCV,
Tesseract (heb+eng), rapidfuzz, openpyxl, pytest; React+Vite+TS.
מבנה: backend/app/{pipeline,providers,export,storage}, backend/form_types/,
backend/data/senders.json, frontend/, eval/, samples/.
כללי עבודה: קרא את הקוד הקיים לפני שאתה כותב. אל תשנה ממשקים של
שלבים קודמים בלי לדווח. אם משהו לא ברור, כתוב הנחה מפורשת והוסף
אותה ל-README. אל תציג דיוק שלא נמדד. בסוף: מה נבנה, איך בדקת, מה
לא עובד.

# commitments-extractor

An engine that reads commitment forms received by the Central Virology Laboratory, extracts their fields, flags anything uncertain and exports to Excel.

**Status: Phase 9 (Excel export)** — upload a PDF or an image, straighten and clean every page, split the pages into forms, classify each form and match its sender to the closed list, extract each form through a provider (value, box, legibility per field), cross-check printed numbers with Tesseract, take one second look at unconfirmed numbers, normalize every value and flag what fails validation, score every field from several signals and flag what is uncertain, download an Excel file (orange = uncertain, with the reasons in a "הערות לבדיקה" column). Forms are read concurrently; a failed form is marked and does not stop the others. Wired: `ingest -> preprocess -> segment -> classify -> extract -> OCR check -> second look -> normalize -> confidence`. The confidence weights and threshold are hand-set and **not calibrated**. No extraction, classification or confidence accuracy has been measured.

## Layout
```
backend/app/main.py           FastAPI app: /jobs upload, status, events (SSE), export, delete; logging to stdout
backend/app/runner.py         JobRunner: at most CE_MAX_CONCURRENT_JOBS jobs at once, the rest wait as pending
backend/app/events.py         server-sent events of a job (progress, finished rows, end)
backend/app/config.py         settings: paths, provider, thresholds, confidence and segmentation weights (env vars CE_*)
backend/app/schemas.py        data model (Pydantic v2)
backend/app/pipeline/         one module per stage: ingest -> preprocess -> segment -> classify
                              -> extract -> ocr_check -> reread -> normalize -> confidence, wired by
                              orchestrator.py; read_form.py reads one form, calls.py runs provider
                              calls (semaphore, retries, timeout), crops.py cuts single values
backend/app/imaging/          pure image operations used by preprocess (geometry, deskew,
                              perspective, illumination, orientation)
backend/app/ocr/              OcrEngine Protocol (base.py) + Tesseract
backend/app/providers/        ExtractionProvider Protocol (base.py) + claude / gemini / local_vlm / fake (shared schema/parsing in common.py, prompt files in prompts.py)
backend/prompts/              versioned prompt files: extract_v1.md (template + YAML block), read_number_v1.md
backend/app/export/xlsx.py    Excel export (columns and multi-value mode from config.ExportSettings)
backend/app/storage/          JobStore Protocol (base.py) + memory / sqlite / postgres job stores (sql.py: shared SQL), tmp.py: temporary per-job files, page_images.py: page images for crops
backend/form_types/*.yaml     one definition per observed form type (25 types) + generic.yaml (fallback)
backend/data/senders.json     closed list of senders
backend/data/migrations/      job store schema as numbered migrations (SQLite and Postgres)
backend/tests/                pytest
frontend/                     React + Vite + TypeScript, the review screen:
frontend/src/api.ts           types mirroring schemas.py + every API call
frontend/src/review.ts        pure review logic: cells, fields to review, Tab order, form flags, dates
frontend/src/messages.ts      Hebrew texts for failed requests
frontend/src/components/      UploadPanel (react-dropzone, SSE progress), ReviewTable (TanStack Table),
                              FieldPanel (crop, page with the box, reasons, raw value, editing), ExportButton
frontend/src/**/*.test.ts(x)  Vitest + Testing Library (jsdom)
frontend/e2e/                 Playwright: upload -> correct an orange field -> download (fake provider)
docs/screenshots/             screenshots of the review screen, written by the e2e test (dummy data)
eval/                         ground_truth.csv, run_eval.py -> report.md
docs/form_mapping.md          form type mapping table
docs/edge_cases.md            edge cases actually observed
scripts/render_previews.py    renders samples/ pages to PNG for manual review
scripts/make_synthetic_variants.py   6 synthetic distortions of a page (used by the tests)
scripts/preprocess_examples.py       before/after images -> docs/preprocess_examples/
scripts/classify_report.py    stage 4 on samples/: type, sender as read, matched sender, score
scripts/extract_report.py     stage 5 on samples/: fields filled and model calls (counts only)
scripts/make_sample_output.py docs/sample_output.xlsx from fake-provider dummy data
docs/sample_output.xlsx       an example export (dummy data only)
```

## Running
```bash
# backend — Python 3.11, 64-bit (PyMuPDF has no 32-bit wheel)
# Tesseract 5 with heb + osd data is needed for orientation detection
# (Windows: winget install UB-Mannheim.TesseractOCR; Debian: tesseract-ocr tesseract-ocr-heb).
# Without it the pipeline still runs, but pages are not turned and are flagged.
py -3.11 -m venv .venv
SETUPTOOLS_USE_DISTUTILS=stdlib .venv/Scripts/python -m pip install -c backend/requirements.lock -e "backend[dev]"
.venv/Scripts/python scripts/lock_requirements.py                    # after a dependency change: rewrite the lock
cd backend && ../.venv/Scripts/python -m pytest
cd backend && ../.venv/Scripts/uvicorn app.main:app --reload          # http://localhost:8000 (jobs in backend/data/app.db)

# job store: SQLite by default; Postgres when DATABASE_URL is set; CE_STORAGE overrides both
DATABASE_URL=postgresql://<user>:<password>@localhost:5432/<db> ../.venv/Scripts/uvicorn app.main:app --reload  # from backend/
CE_STORAGE=memory ../.venv/Scripts/uvicorn app.main:app --reload      # nothing on disk, lost on restart
# run the store tests against Postgres too (each test gets its own schema; skipped when unset)
cd backend && TEST_DATABASE_URL=postgresql://<user>:<password>@localhost:5432/<db> ../.venv/Scripts/python -m pytest

# run without an API key (fixed dummy data):
CE_PROVIDER=fake ../.venv/Scripts/uvicorn app.main:app --reload       # from backend/
# run with Claude:
ANTHROPIC_API_KEY=... ../.venv/Scripts/uvicorn app.main:app --reload  # from backend/
# run with Gemini:
CE_PROVIDER=gemini GEMINI_API_KEY=... ../.venv/Scripts/uvicorn app.main:app --reload  # from backend/

# frontend — proxies /api to the backend
cd frontend && npm ci && npm run dev                                  # http://localhost:5173
cd frontend && npm test                                               # Vitest (table, editing, review logic)
cd frontend && npx playwright install chromium && npm run e2e         # starts a fake-provider backend (:8765) and Vite (:5175)
cd frontend && PLAYWRIGHT_CHANNEL=chrome npm run e2e                  # with the installed Chrome instead (no download)
cd frontend && PLAYWRIGHT_CHANNEL=chrome npm run e2e:demo             # demo set-up: built frontend (:5176) -> backend (:8766) with access code + CORS
# deployed frontend: VITE_API_URL=<backend URL> at build time (see frontend/.env.example); `npm run build` fails without it
cd frontend && VITE_API_URL=http://localhost:8000 npm run build
# deploying the demo (Render + Vercel): docs/DEPLOY.md

# both in Docker (jobs in memory: CE_STORAGE=memory in docker-compose.yml)
docker compose up --build
# local only: keep jobs in SQLite across restarts (volume job-db)
docker compose -f docker-compose.yml -f docker-compose.sqlite.yml up --build

# evaluation
.venv/Scripts/python eval/run_eval.py predictions.csv                 # writes eval/report.md
```

### Trying it (Phase 8)
1. Start the backend (e.g. with `CE_PROVIDER=fake`) and the frontend.
2. Open http://localhost:5173, drop one or more PDFs / images on the dashed area (or click it), then "התחלת עיבוד".
3. Rows appear as forms are read. Orange = to review; click a value (or Tab to it and press Enter) to open the side panel.
4. In the panel type the right value (or keep it) and press Enter: the field turns green ("תוקן ידנית") and the next orange field opens. Tab / Shift+Tab move between orange fields, Esc closes.
5. "ייצוא לאקסל": with fields still orange, a warning asks whether to export anyway.

### API
| Method | Path | |
|---|---|---|
| POST | `/jobs` | multipart `files`, repeated once per file (1 to `CE_MAX_FILES`; PDF, PNG, JPEG, TIFF or WEBP — detected from the content, each up to `CE_MAX_UPLOAD_MB`); one job over all of them, read in the order sent. 413 too large / too many files, 415 unsupported type (nothing is stored if any file is refused); returns the job, processing runs in the background |
| GET | `/senders` | the canonical sender names (sorted), for the reviewer's sender field |
| GET | `/jobs/{id}` | job status and extracted groups (while running: all groups found so far, `finished` marks the read ones) |
| GET | `/jobs/{id}/events` | `text/event-stream`: `progress` (`status`, `forms_total`, `forms_done`), `rows` (newly finished groups, each once), `end` (the whole job, then the stream closes), `missing` (job gone); 404 if unknown |
| POST | `/jobs/{id}/export` | the xlsx file (409 if the job is not `done`) |
| PATCH | `/jobs/{id}/commitments/{cid}/fields/{field}` | `cid` = the form's id (`FormGroup.id`). JSON `{"value": "...", "item": n}`: a reviewer's value (`source=manual`, `needs_review=false`, `raw_value` keeps the text as read); `item` only for `central_numbers` / `commitment_numbers` (an index, or the list length to add one); dates as `YYYY-MM-DD`; the sender must be a canonical name. 409 if the job is not `done`, 422 for an invalid value; returns the job |
| GET | `/jobs/{id}/crop/{cid}/{field}` | PNG of the field's box (`?item=n` for list fields), cut on request, never written to disk. 404 with `detail`: `image_gone` (the page image was deleted: file TTL or server restart), `no_box`, `field_not_found` |
| GET | `/jobs/{id}/pages/{cid}/{page}` | JPEG of one page of the form (`page` 0-based in the source file, one of `FormGroup.pages`), at the resolution of `Field.bbox`. 404 `page_not_in_form` / `image_gone` |
| DELETE | `/jobs/{id}` | deletes the job's rows in every table and its files (upload, page images, export) |

Every endpoint under `/jobs/{id}` is 404 `job not found` for an unknown, deleted or expired job, and those with `{cid}` are 404 `commitment not found` for an unknown form.

With `CE_DEMO_ACCESS_TOKEN` set, every endpoint but `/health` answers 401 `access token missing or invalid` without the code (see 109). Any endpoint may answer 429 `rate limit exceeded` (with `Retry-After`), and a request with a body 411 without `Content-Length` or 413 `request too large`. `POST /jobs` also gives 413 `too many pages` (a file over `CE_MAX_PAGES` or an upload over `CE_MAX_JOB_PAGES`).

### Settings (env vars)
| Variable | Default | |
|---|---|---|
| `CE_PROVIDER` | `claude` | `claude` / `gemini` / `local_vlm` / `fake` |
| `ANTHROPIC_API_KEY` | — | required for `claude` |
| `CE_CLAUDE_MODEL` | `claude-opus-5-5` | |
| `CE_CLAUDE_TIMEOUT_S` | `60` | per API call |
| `CE_DEBUG_PROVIDER` | `0` | `1`: log the shape of every Claude/Gemini request and answer (sizes, image dimensions and real media type, attempt, HTTP status, API error type/status and quota ids, request/response id, stop/finish reason, block/part kinds, token counts, exception chain); never values, text or the key. `scripts/check_provider.py` and `scripts/diagnose_extract.py` turn it on |
| `GEMINI_API_KEY` | — | required for `gemini` |
| `CE_GEMINI_MODEL` | `gemini-3.8-flash` | |
| `CE_GEMINI_TIMEOUT_S` | `180` | per API call (thinking models are slow on full pages) |
| `CE_LOCAL_VLM_URL` | `http://localhost:11434` | Ollama server for `local_vlm` |
| `CE_LOCAL_VLM_MODEL` | `qwen2.5vl:7b` | any vision model pulled on that server |
| `CE_LOCAL_VLM_TIMEOUT_S` | `300` | per call |
| `CE_PROMPT_VERSION` | `v1` | picks `backend/prompts/extract_<v>.md` and `read_number_<v>.md` |
| `CE_PROMPTS_DIR` | `backend/prompts` | |
| `CE_EXTRACT_CONCURRENCY` | `4` | provider calls in flight at once while reading the forms of a job |
| `CE_EXTRACT_CALL_TIMEOUT_S` | `240` | per call, around the provider's own HTTP timeout |
| `CE_EXTRACT_MAX_ATTEMPTS` | `3` | per call, for timeouts / rate limits / overloads |
| `CE_EXTRACT_BACKOFF_INITIAL_S` | `2` | exponential backoff (2, 4, 8 … s) plus up to this much jitter |
| `CE_EXTRACT_BACKOFF_MAX_S` | `30` | |
| `CE_STORAGE` | — | override of the job store: `memory` (nothing on disk, lost on restart) / `sqlite` / `postgres`. Unset: `postgres` if `DATABASE_URL` is set, else `sqlite` |
| `DATABASE_URL` | — | Postgres connection URL (no `CE_` prefix: Render sets this name). Holds the password: never logged |
| `CE_DB_PATH` | `backend/data/app.db` | SQLite file; created at startup |
| `CE_DB_POOL_SIZE` | `5` | Postgres: connections kept open at most |
| `CE_DB_TIMEOUT_S` | `10` | Postgres: seconds to wait for a connection |
| `CE_JOB_TTL_S` | `3600` | jobs (database rows) are deleted this long after creation, with their files |
| `CE_FILE_TTL_S` | `3600` | temporary files (upload, page images, export) are deleted this long after the job directory was last written, even if the job stays |
| `CE_MAX_UPLOAD_MB` | `50` | per file |
| `CE_MAX_FILES` | `20` | files in one upload (`POST /jobs`) |
| `CE_MAX_IMAGE_PIXELS` | `50000000` | per page image (width × height), checked before decoding / rendering; also set as Pillow's `Image.MAX_IMAGE_PIXELS` |
| `CE_MAX_CONCURRENT_JOBS` | `2` | jobs processed at once; further uploads wait as `pending` |
| `CE_LOG_LEVEL` | `INFO` | level of the app's logs on stdout (`DEBUG` … `CRITICAL`) |
| `CE_ALLOWED_ORIGINS` | — | browser origins allowed to call the API (CORS), comma-separated, each exactly `https://host[:port]` (no path, no trailing slash; `*` refused at startup), e.g. the Vercel URL; empty = no CORS headers |
| `CE_DEMO_ACCESS_TOKEN` | — | shared demo access code (at least 16 characters): every endpoint but `/health` needs `Authorization: Bearer <code>` (GET also `?access_token=`, for EventSource); empty = no check. A secret: never logged, not in `Settings`' repr |
| `CE_REQUIRE_ACCESS_TOKEN` | `0` | `1`: refuse to start without `CE_DEMO_ACCESS_TOKEN` (set in `render.yaml`) |
| `CE_RATE_LIMIT_PER_MINUTE` | `300` | requests per client IP per minute (`/health` excluded); `0` = off |
| `CE_UPLOAD_LIMIT_PER_HOUR` | `30` | `POST /jobs` per client IP per hour; `0` = off |
| `CE_TRUSTED_PROXY_HOPS` | `0` | proxies in front that append to `X-Forwarded-For`; the client IP is the n-th entry from the right (0 = the socket peer) |
| `CE_MAX_JOB_PAGES` | `200` | pages over all files of one upload, counted before the job is created |
| `CE_PURGE_INTERVAL_S` | `300` | seconds between purges of expired jobs and files while the server runs |
| `CE_CLEAR_TMP_ON_RESTART` | `1` | delete every job directory under `CE_TMP_DIR` at startup and shutdown |
| `PORT` | `8000` | Docker image only: port uvicorn listens on (set by Render; no `CE_` prefix) |
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
| `CE_REVIEW_THRESHOLD` | `0.85` | form boundaries scoring below this are `split_uncertain` (segmentation only); must stay above `CE_SEGMENT_WEIGHT_VLM` |
| `CE_SEGMENT_WEIGHT_TITLE` | `0.9` | segmentation signal weights, each in [0, 1) (see 27, 83): form title in the header |
| `CE_SEGMENT_WEIGHT_NEW_CENTRAL_NUMBER` | `0.9` | a central number not on the previous page |
| `CE_SEGMENT_WEIGHT_UNREAD_CENTRAL_STICKER` | `0.6` | a "מרכזי" sticker whose number was not read |
| `CE_SEGMENT_WEIGHT_RECEIVED_STAMP` | `0.8` | a "התקבל" stamp |
| `CE_SEGMENT_WEIGHT_SAME_CENTRAL_NUMBER` | `0.9` | the central number of the previous page |
| `CE_SEGMENT_WEIGHT_PAGE_MARKER` | `0.9` | a page marker ("1/2", "Page 2 of 2") |
| `CE_SEGMENT_WEIGHT_PAGE_MARKER_PAIR` | `0.99` | a marker right after the previous one ("2/2" after "1/2") |
| `CE_SEGMENT_WEIGHT_FIRST_OF_SEVERAL` | `0.6` | the previous page is "1 of n" |
| `CE_SEGMENT_WEIGHT_SPARSE_PAGE` | `0.9` | an almost empty page |
| `CE_SEGMENT_WEIGHT_PREVIOUS_FORM_LENGTH` | `0.7` | `pages.typical` of the previous page's form type |
| `CE_SEGMENT_WEIGHT_VLM` | `0.8` | the model's answer; must stay below `CE_REVIEW_THRESHOLD` |
| `CE_SEGMENT_WEIGHT_VLM_INCONSISTENT` | `0.5` | the model's answer when it contradicts itself |
| `CE_CONFIDENCE_BASE_PRINTED` | `0.8` | field confidence (Phase 7): starting score of a printed value (also sticker / stamp) |
| `CE_CONFIDENCE_BASE_HANDWRITTEN` | `0.55` | … of a handwritten value |
| `CE_CONFIDENCE_BASE_CLOSED_LIST` | `0.75` | … of a value matched to a closed list (the sender) |
| `CE_CONFIDENCE_OCR_AGREEMENT` | `0.25` | added when OCR agrees |
| `CE_CONFIDENCE_SECOND_READING_AGREEMENT` | `0.15` | added when the second reading agrees |
| `CE_CONFIDENCE_OCR_DISAGREEMENT` | `0.3` | subtracted when OCR disagrees |
| `CE_CONFIDENCE_SECOND_READING_DISAGREEMENT` | `0.3` | subtracted when the second reading disagrees |
| `CE_CONFIDENCE_HARD_FAIL` | `0.3` | subtracted per failed hard rule |
| `CE_CONFIDENCE_SOFT_FAIL` | `0.1` | subtracted per failed soft rule |
| `CE_CONFIDENCE_ILLEGIBLE` | `0.2` | subtracted when the value is not fully legible |
| `CE_CONFIDENCE_REVIEW_THRESHOLD` | `0.9` | a field below this is flagged; **a temporary default, not calibrated** |
| `CE_EXPORT_COLUMNS` | `first_name,last_name,id_number,central_numbers,received_date,sender,commitment_numbers,review_notes,source_file` | Excel columns, in order (Phase 9, see 84); any subset of these keys plus `pages` |
| `CE_EXPORT_MULTI_VALUES` | `joined` | `joined`: several central / commitment numbers in one cell, separated by `; `; `columns`: one column per number |

## Assumptions (Phase 1)
10. *(Superseded by 25.)* **One file = one form.** Segmentation is not implemented yet, so all pages of an uploaded file are sent as a single form and produce a single row. A multi-form scan therefore gives one (wrong) row until Phase "segment".
11. *(Superseded by Phase 7, 60–70.)* **No confidence yet.** Every extracted field keeps `confidence=0` and `needs_review=True` (highlighted) because scoring is not wired. The provider never sets confidence.
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

24. *(Superseded by 71: the default is now `memory`.)* **Jobs are stored in SQLite by default** (`CE_STORAGE=sqlite`, file `CE_DB_PATH`), so they survive a restart; `CE_STORAGE=memory` keeps the old behaviour. The TTL still applies to both: jobs older than `CE_JOB_TTL_S` are deleted with their temporary files.

## Assumptions (Phase 3 — segmentation)
25. **Splitting.** `pipeline/segment.py` decides every boundary between two adjacent pages of a file. A single-page file (and every single-image upload) is one form, with no OCR and no VLM call. Output: `Segmentation{groups: [FormGroup{pages, split_confidence, split_reasons}], irrelevant_pages, vlm_calls}`; the orchestrator then extracts each group from its own pages only.
26. **Fingerprint = one OCR pass per page** (`OcrEngine.read_words`, word boxes): keywords from `detect.keywords` in the top 30% of the page, the word "מרכזי" and any 6-digit number within 6 word heights of it, the word "התקבל", a page marker ("2/2", "Page 1 of 2", "עמוד 1 מתוך 2") within 15% of the top/bottom edge, the word count and the share of ink. Matching is fuzzy (rapidfuzz: ≥80 on single words, `partial_ratio` ≥90 on keywords of 8+ letters). Keywords that also match the lab's own "התקבל" stamp text are not used as titles, because that stamp is on almost every page.
27. **Rules and confidence.** Signals for "B starts a form": a known title, a new central number, a "מרכזי" sticker whose number was not read, a "התקבל" stamp, marker "1/N", the form type on A has `pages.typical: 1`. Signals for "B continues A": a sparse page (<15 words and <1% ink), the same central number as A, marker "k/N" (strongest when A is "k-1/N"), A was "1/N" or its type has `pages.typical` > 1. Each signal has a fixed weight (`WEIGHT_*` in `segment.py`, **chosen by hand, not fitted**); each side scores `1 − ∏(1 − w)`, and the boundary confidence is `winner × (1 − loser)`. A group's `split_confidence` is the lowest confidence among the boundary that opens it, those inside it and the one that closes it.
28. **The VLM is asked only below `CE_REVIEW_THRESHOLD`** (0.85): pages A and B are sent together to `ExtractionProvider.compare_pages`, which returns `{"same_form", "reason", "page_b_type": "start|continuation|irrelevant"}` (`PageRelation`, schema `PAGE_RELATION_SCHEMA`). The answer is added as one more signal with weight 0.8 (0.5 if `same_form` and `page_b_type` contradict each other), so **the model alone never clears a boundary**: it must agree with a rule, otherwise the group gets `split_uncertain` ("ייתכן שפוצל לא נכון", in its own Excel column — *since Phase 9 a line in "הערות לבדיקה", see 87* — and in the UI). `reason` is model text that may quote the page, so it is never logged or stored. Without Tesseract, or when OCR fails on a page, the rules have nothing to go on and every such boundary goes to the VLM (`ocr_unavailable` / `ocr_failed` in `split_reasons`).
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

## Assumptions (Phase 4 — form type and sender)
32. **One classification call per form, on its first page only** (`ExtractionProvider.classify`). The answer is `{"form_type": <id> | "unknown", "sender_raw", "evidence"}` (`FormClassification`, schema `form_classification_schema`). The prompt lists every type with its `display_name` and the `sender.hint` of its YAML, and repeats the sender rule (the billed body; not the HMO on an admission sticker, not a clinic). `evidence` may quote the page, so it is neither logged nor stored.
33. **Unknown layout** → `backend/form_types/generic.yaml`: the form is still extracted, with generic hints, and gets `form_type_unknown` in the new `FormGroup.form_reasons`, which puts it in review ("לבדיקה" in Excel; *since Phase 9 a line in "הערות לבדיקה", see 87*). `generic.yaml` is not a layout: `load_form_types` leaves it out, so it is never offered to the model and gives segmentation no title keywords. `Commitment.form_type_id` is the YAML used, so it is `generic` for an unknown layout.
34. **Sender matching** (`classify.SenderMatcher`; the decision algorithm is in the `classify.py` docstring): both sides are normalized (niqqud, quotes/gershayim, hyphens and maqaf, and whole generic phrases such as "המרכז הרפואי", "קופת חולים", "שירותי בריאות", "מעבדה", "בי"ח" are removed), then scored with rapidfuzz `WRatio` against every canonical name, alias and abbreviation, keeping the best score per sender. **≥ 88** is accepted unless a *different* sender is within **5 points** (ambiguous). Not in the list / ambiguous / not read → `sender = null`, `needs_review`, with `sender_not_in_list` / `sender_ambiguous` and up to 3 `sender_candidate:<name>=<score>` reasons. A match keeps `raw_value` and `confidence = score/100` (one signal only; *replaced by the confidence stage, see 63*), plus `sender_fuzzy_match` below 100. 88 is from the task; the 5-point gap was chosen by hand. Neither was fitted.
35. **The sender read by `extract_form` is replaced** by the classified one, so the sender has a single source. The extraction schema is unchanged; its sender is ignored.
36. **Generic words are dropped on purpose.** Without that, "קופ"ח מכבי-מעבדה שולחת" scored Maccabi 90 against Clalit/Meuhedet 86 (ambiguous), and "מכבי ... מעבדה" came close to the Rosenblatt lab. Side effect: text made *only* of generic words ("קופת חולים", "מרכז רפואי") is "not in list", which is the right answer for it. `extractOne` is not called directly: the full ranking (`process.extract`) is needed for the gap rule; its top entry is the same.
37. **Text naming two listed bodies** (e.g. an HMO and a hospital campus on one line) comes out ambiguous, not as the first one. The model is asked for the single billed body, so this should be rare; it is flagged, not guessed.
38. **Cost:** one extra provider call per form (classify), on top of extraction and the segmentation calls.

### Phase 4 — what was checked
- **Unit tests** (`tests/test_classify.py`, no network): normalization (gershayim, maqaf, niqqud, generic phrases as whole words only); different spellings, abbreviations ("כ"ל", "SZMC"), a name not in the list, two close candidates ("הרופא": Shamir/Assaf Harofeh vs. Shmuel Harofeh), aliases of one sender are not rivals, a blank sender, duplicate names in the list are rejected; names printed on the sample layouts against the real `senders.json`; known type → its YAML, `unknown` → `generic.yaml` + flag; first page only. Provider tests: Claude/Gemini requests carry the type list and the new schema; unknown ids become `unknown`; a malformed answer is a `ProviderError`. Orchestrator: the matched sender and `form_type_id` land in the `Commitment`. Full suite: 313 passed, 1 skipped (real Tesseract); `test_segment_samples.py` was not re-run (segmentation is unchanged).
- **Report on samples/** (`scripts/classify_report.py` → `eval/classify_report.md`, git-ignored; organization names only, no patient fields): **not run with a real model** — this environment has no `ANTHROPIC_API_KEY` / `GEMINI_API_KEY`. Only a mechanical run with `CE_PROVIDER=fake` on 2 pages per file was made (its answers are fixed dummy values and mean nothing). Expected types come from `seen_in` of the YAML files (all 48 pages). To run: `CE_PROVIDER=gemini GEMINI_API_KEY=... TESSDATA_PREFIX=C:/Users/Public/tessdata .venv/Scripts/python scripts/classify_report.py`.

### Phase 4 — what does not work / was not checked
- **No classification or sender accuracy is known**, on samples/ or elsewhere.
- Candidates and alias suggestions in the report are for a human; nothing is added to `senders.json` automatically.
- The threshold and gap were not tuned on real model output.
- `local_vlm.classify` is still `NotImplementedError`.
- The UI shows `form_type_unknown` only through review highlighting of the row; there is no form-type column yet.

### Phase 4 changes to existing interfaces
- `ExtractionProvider.classify(pages, form_types)` returns `FormClassification` (was `str | None`). `common.form_type_schema` / `form_type_from_output` → `form_classification_schema` / `form_classification_from_output`; `classify_instruction` asks for the sender too. `FakeProvider.classify` returns `clalit_form17` (when loaded) and a Clalit alias.
- `classify.classify(pages, form_types)` (stub) → `classify(pages, form_types, generic, matcher, provider) -> Classification`. New: `load_generic_form_type`, `SenderMatcher`, `normalize_sender_name`. `load_form_types` skips `generic.yaml`.
- `Orchestrator(...)` takes a new required `generic_form_type`.
- `FormGroup`: new `form_reasons`; `needs_review` also covers `form_type_unknown`. New model `FormClassification`; constants `UNKNOWN_FORM_TYPE`, `FORM_TYPE_UNKNOWN`. Stored inside `commitments.data_json`, so SQLite needs no schema change (old rows load with `form_reasons=[]`).

## Assumptions (Phase 5 — extraction core)
39. **Two routes.** The vision model reads every field of the form (`ExtractionProvider.extract_form` → `FormReading`); per value it returns `value`, `bbox` (fractions 0–1 of the image plus the 0-based index of the image it is on), `legible`, `note` and — beyond the four keys of the task — `source` (`printed` / `handwritten` / `sticker` / `stamp`), because the OCR route needs to know which values are printed. That is the model's own claim and is not checked. Tesseract then re-reads only printed numbers (stage 5b) and the model takes one narrow second look at numbers that are still not confirmed (5c).
40. **Prompt = fixed template + YAML block.** `backend/prompts/extract_v1.md` holds the rules (no guessing → null; digits copied as seen, nothing completed or corrected; a cut sticker is `legible=false` with the partial value; "דרישה"/"דגימה" = commitment number; every central and commitment number); `{{form_type}}` is replaced by one line per field from the form type's YAML (labels, may repeat, may be handwritten, hint). Versions are separate files chosen by `CE_PROMPT_VERSION`; a released version is never edited (`backend/prompts/README.md`). The output schema stays in code (`common.FORM_READING_SCHEMA`), forced as a Claude tool / Gemini `responseJsonSchema` / Ollama `format`.
41. **"commitments" in the task = `commitment_numbers`** (the name used by `Commitment` and the YAML files). Several commitments on one form are several items of that list in **one** row — one form is still one `Commitment`; nothing is split into extra Excel rows.
42. *(2-digit years: superseded by 56.)* **Dates are copied as written** (changed from Phase 1, where the model was asked for ISO). `extract` parses `D.M.YYYY` (also `/` or `-`, day first as on Israeli forms) or `YYYY-MM-DD` (`normalize.parse_date`); a 2-digit or missing year is not completed: the date stays empty with `unparsed_date` and the text in `raw_value`.
43. **A box is used only when it is plausible for one value:** on a page of the form, inside the image (±2% overshoot is clipped), corners in order, at most 15% of the page area. Otherwise `Field.bbox` is empty and the field gets `bbox_implausible`. Boxes are stored in `original` pixels (same geometry as the image sent to the model, see 17).
44. **OCR cross-check (stage 5b)** — only ID, central and commitment numbers whose value is not handwritten: crop = box + 10 px (in `original` pixels) from the binarized `ocr_image` (grayscale `original` if binarizing is off), Tesseract `--psm 7` with a `0123456789` whitelist (languages as configured). Digits only are compared, so spaces/dashes do not count. Outcome codes: `ocr_agrees`, `ocr_disagrees`, `ocr_empty` (no digits read), `ocr_unavailable` (no Tesseract, no plausible box, crop under 8 px, a value with letters, or an OCR error). `ocr_empty`/`ocr_unavailable` are **not** a disagreement. The OCR digits and their confidence are stored as an `ocr` signal in the new `Field.signals`; the model's value is never changed.
45. **Second look (stage 5c)** — the confidence stage did not exist yet (it runs after this stage), so "needs review" here means *not confirmed by OCR*: a numeric field is looked at again if it is `illegible` (always, even if OCR agreed with the partial digits), or OCR disagreed / read nothing / was unavailable, or it is handwritten, or it is missing but has a box. A missing value without a box that is not marked illegible is just not on the form. Only numeric fields get it, because the narrow prompt asks for digits ("Which number is written here? Digits only, or null" — `read_number_v1.md`); names and dates do not. Crop = box + 10 px from the **colour** `original` (never binarized), enlarged 2.5× (capped at 1600 px), sent alone. Once per field, at most 8 per form (`reread_limit` beyond). The answer is a `reread` signal, never an overwrite — not even when the value is missing and the second look finds digits (`reread_found_value`). Codes: `reread_agrees` / `reread_disagrees` / `reread_null` / `reread_found_value` / `reread_unavailable` / `reread_failed`.
46. **Concurrency.** After segmentation, all forms of a file are read concurrently (`asyncio`); every provider call (classify, extract, second looks) runs in a worker thread under one `asyncio.Semaphore` (`CE_EXTRACT_CONCURRENCY`, 4). tenacity retries `RetryableProviderError` (timeout, connection error, HTTP 408/429/5xx, Anthropic 529) and the per-call timeout (`CE_EXTRACT_CALL_TIMEOUT_S`, 240 s) up to 3 attempts, waiting 2, 4 … s (max 30) plus jitter; other errors are not retried. The semaphore is not held while waiting to retry. A timed-out call is abandoned but its thread runs on until the provider's HTTP timeout ends it, so the per-call timeout is set above the HTTP timeouts. Gemini keeps its own 429/503 retry loop (Phase 2) and raises a non-retryable error when it gives up, so its calls are not retried twice for overload. Segmentation calls are still made one by one, outside this.
47. **A failed form does not fail the job:** its `FormGroup.error` holds the exception type, `form_reasons` gets `extraction_failed` (row flagged for review), and the other forms are read. The job is `failed` only when every form failed (`Job.error` = the first form's error type) — e.g. a missing API key. A failed second look only flags its field (`reread_failed`).
48. **Call counting:** `FormGroup.vlm_calls` counts every provider call made to read that form, retries included; segmentation calls stay in `Job.split_vlm_calls`.
49. *(Superseded by Phase 7, 60–70.)* **Still no confidence.** Signals and reason codes are collected for stage 7; until then every field keeps `confidence=0` and `needs_review` (assumption 11). Model `note` text is stored in the VLM signal for a reviewer and never logged.

### Phase 5 — what was checked
- **Unit tests, no network and no Tesseract** (fake provider, scripted OCR): per edge case — several central numbers (each OCR'd and second-looked separately), several commitment numbers (several items, repeats once, leading zeros kept), null fields (empty, flagged, no extra call), a cut sticker (partial value kept, `illegible`, second look even when OCR agrees with the partial digits), an unreadable sticker with no value (kept as an item, second look may find digits but never fills them in); box mapping and implausible boxes; date parsing; OCR agree/disagree/empty/unavailable; second look triggers, crop size and colour, cap, failure; `ProviderCalls` retries, no retry for other errors, timeout, semaphore bound across forms, call counts; orchestrator: one failing form is marked and the others are read, all failing → job failed; providers: Claude/Gemini/Ollama request shape, prompt from the versioned file with the YAML block, retryable vs. non-retryable errors, cut-off answer, error texts without bodies.
- **Real Tesseract:** `read_line` (`--psm 7` + whitelist) reads a printed dummy number correctly (`test_tesseract.py`, skipped where Tesseract is missing).
- **Run on samples/** (`scripts/extract_report.py` → `eval/extract_report.md`, git-ignored, counts only): **with `CE_PROVIDER=fake` only** (no API key here) and real Tesseract. 2 files, 48 forms, 0 failed, 304 s. Model calls: 10 for segmentation (as in Phase 3) + 96 to read the forms (classify + extract, 2 per form) = 106. Every field "filled" in 48 of 48 forms, 48 central and 48 commitment items — **these are the fake provider's fixed dummy values, not readings of the pages**, so they say nothing about real filling rates. The fake gives no boxes, so all 144 numeric fields came out `ocr_unavailable` and `reread_unavailable` and no second look was made. What the run does show: the whole chain (segmentation → concurrent reading → OCR check → second look → report) runs on the real scans without errors. To run with a real model: `CE_PROVIDER=gemini GEMINI_API_KEY=... CE_TESSERACT_CMD=... TESSDATA_PREFIX=C:/Users/Public/tessdata .venv/Scripts/python scripts/extract_report.py`.

### Phase 5 — what does not work / was not checked
- **Not run with a real model**: this environment has no `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` and no Ollama server. Whether a real model returns usable boxes, sensible `legible`/`source` values and follows the rules is unknown; so is the share of OCR agreement and second looks on real output.
- The 15% box-area limit, 10 px padding, 2.5× enlargement and the cap of 8 second looks are chosen by hand, not fitted.
- `local_vlm` is a base: request/response shape tested against a mock only; the default model name is an assumption.
- OCR checks digits only; a commitment number with letters is never cross-checked (`ocr_unavailable`).
- Names and dates get no second look (the narrow prompt is digits-only).
- The UI and the Excel file do not show `FormGroup.error` or the signals yet; a failed form shows only as a flagged, empty row.
- Two forms that fail with different errors are reported on the job with the first error only.

### Phase 5 changes to existing interfaces
- `ExtractionProvider.extract_form` returns `FormReading` (was `Commitment`); `reread_field(pages, field_name, form_type) -> list[str]` is replaced by `read_number(crop) -> NumberReading`. New `RetryableProviderError(ProviderError)`: Claude/Gemini/local timeouts and connection errors, and Claude HTTP 408/429/5xx/529, now raise it (existing `except ProviderError` still catches them). Claude: `max_tokens` 2048 → 4096, and an answer cut off at `max_tokens` is an error.
- `common.py`: `COMMITMENT_SCHEMA`, `READINGS_SCHEMA`, `commitment_from_output`, `readings_from_output`, `extract_instruction`, `reread_instruction`, `UNPARSED_DATE_REASON` removed → `FORM_READING_SCHEMA`, `NUMBER_SCHEMA`, `form_reading_from_output`, `number_from_output`, `encode_image`; prompts in `providers/prompts.py` + `backend/prompts/`. Provider constructors take an optional `prompts`; `LocalVlmProvider(base_url, model, timeout_s)`.
- `extract.extract(pages, form_type, provider)` → `extract.to_commitment(reading, pages, form_type)`; the provider call moved to `read_form.FormReader`. `Orchestrator.run` now runs its own event loop (`asyncio.run`), so it must not be called from inside a running loop (FastAPI runs it in a worker thread, which is fine).
- `OcrEngine`: new method `read_line(image, allowed_chars) -> OcrLine`.
- Schema: `Field.signals` (new `FieldSignal`, `SignalSource`), `FormGroup.vlm_calls` and `FormGroup.error`, `FormGroup.needs_review` also covers `extraction_failed`; new models `RelativeBox`, `FieldReading`, `FormReading`, `NumberReading`. All are inside `commitments.data_json`, so SQLite needs no schema change.
- New settings (table above), new dependency `tenacity`, the Docker image copies `backend/prompts`.

## Assumptions (Phase 6 — normalization and validation)
50. **Pure functions.** `pipeline/normalize.py`: `normalize_id`, `normalize_central`, `normalize_commitment_number`, `normalize_name`, `normalize_date` take the text as read and return `Normalized(value, raw_value, reasons)`; `raw_value` is the input, unchanged. `check_id_length`, `check_id_checksum`, `check_date_range`, `check_commitment_pattern` return `Check(status, reason)` with status `ok` / `soft_fail` / `hard_fail`. `normalize(commitment, form_type, today)` applies them to a copy of the `Commitment`, from each field's `raw_value`; `today` is passed in, so the functions do not read the clock.
51. **The status is not stored on the field.** `Field` has no status slot, so a failed rule adds its reason code to `Field.reasons`; how much each code weighs (and `hard_fail` vs. `soft_fail`) is for the confidence stage (*Phase 7: see 62*). A `hard_fail` value is **kept**, not erased, so the reviewer sees what was read.
52. **ID:** invisible marks (RLM/LRM/ALM, bidi embeddings and isolates, zero-width, BOM), whitespace and dashes (hyphen, Unicode dashes, maqaf, minus) are removed; every Unicode decimal digit (Arabic-Indic, Persian, full-width) becomes ASCII. **"Hebrew digits" in the task = these Unicode digit forms; Hebrew letters (gematria) are never read as numbers** — an ID with any letter gets no value (`id_not_digits`), nothing is dropped silently. Dots are not ID separators (`id_not_digits`). Padding with leading zeros only for 7–8 digits (`id_padded`); other lengths are kept as read and fail `check_id_length` (`id_length_invalid`, hard). The check digit (`id_checksum_failed`) is soft and is judged only on 9 digits.
53. **Central number:** the same cleaning, plus dots and slashes between digit groups; anything else gives no value (`central_not_digits`). Leading zeros kept. Items that become identical after normalizing are kept once.
54. **Commitment number:** invisible marks removed, ASCII digits, whitespace collapsed — separators are **kept** (Maccabi prints "branch/number"). An optional `pattern` (regex, full match) under `fields.commitment_numbers` in the YAML gives `commitment_pattern_mismatch` (soft: the patterns come from one or two samples each). Only `commitment_numbers` may have a pattern (the model rejects others). Patterns were added only where `docs/form_mapping.md` documents a fixed length: `[0-9]{9}` for `meuhedet_form17`, `clalit_form17`, `maccabi_online`, `leumit_lab_worklist`, `rambam_external`.
55. **Names:** runs of whitespace become one space and the ends are trimmed. Nothing else (letters, hyphens, digits, case) is touched.
56. **Dates:** day first, separators `/`, `.`, `-` (any of them, with spaces as on the received stamp), year of 4 or 2 digits, or ISO. A 2-digit year goes into **2000–2099** and is always flagged (`date_two_digit_year`) — this replaces the Phase 5 rule (42) that left 2-digit years empty; stage 6 re-reads `raw_value`, so the `unparsed_date` of stage 5 is re-decided. A missing year stays empty (`date_year_missing`). **Month > 12 with a day ≤ 12** (e.g. `03/15/2025`) is a suspected day/month swap: the date is **not swapped**, it stays empty with `date_day_month_swapped`. Any other impossible date (`15/13/2025`, `31.02.2025`) → `date_invalid`; other text → `unparsed_date`.
57. **Date range (soft):** after `today` → `date_in_future`; before 2000 (the start of the 2-digit window) → `date_before_window`. "today" is the server's local date when the form is read.
58. **Duplicate central numbers across forms** are flagged on the form (`FormGroup.form_reasons ∋ central_duplicate`, which puts the form in review), on every form involved, after all forms of the job are read. Compared as normalized strings (leading zeros count); empty items are not compared; a repeat inside one form is not a duplicate. The field values stay as they are.
59. **The sender is not touched** by stage 6; it was matched to the closed list in stage 4.

### Phase 6 — what was checked
- `tests/test_normalize.py` (no network, no model, no Tesseract): every normalizer and every rule, parametrized with at least 8 cases each, incl. empty / blank / None input, invisible marks, dashes and maqaf, Arabic-Indic and Persian digits, Hebrew letters, reversed day/month, 2-digit years at both ends of the window, missing years, impossible dates; duplicate central numbers across forms; `normalize()` on a whole `Commitment` (raw values kept, input not changed, reasons, dedupe); the YAML `pattern` (only on `commitment_numbers`, must compile, set on the 5 documented types). **Line and branch coverage of `normalize.py`: 100%** (measured with `coverage run --branch`; coverage is not a project dependency).
- `test_read_form.py` and `test_orchestrator.py`: values are normalized after the checks; forms sharing a central number are flagged, a single form is not. Full suite: 543 passed, 4 skipped (all 4 need a real Tesseract, not installed on the test machine: 2 in `test_tesseract.py`, 2 in `test_segment_samples.py`; segmentation is unchanged in this phase).

### Phase 6 — what does not work / was not checked
- No real model output was normalized; how often each rule fires on real readings is unknown.
- The `pattern`s are based on the few samples in `docs/form_mapping.md`; other types have none.
- A date before 2000 written with a 4-digit year is kept and only flagged; a 2-digit year is never mapped to the 1900s.
- The Excel file and the UI do not show the new reason codes; flagged fields are only highlighted as before, and `central_duplicate` only flags the row.

### Phase 6 changes to existing interfaces
- `normalize.normalize(commitment, senders)` (stub) → `normalize(commitment, form_type, today)`; new pure functions and `Normalized`, `Check`, `CheckStatus` (see 50). `parse_date` is unchanged and still used by stage 5; `UNPARSED_DATE` moved from `extract.py` to `normalize.py` (still importable from `extract`).
- `FormReader(...)` takes an optional `today` callable (default `date.today`).
- `Orchestrator.run` calls `normalize.flag_duplicate_centrals(job.groups)` after reading.
- Schema: `FieldSpec.pattern` (optional, `commitment_numbers` only — `PATTERN_FIELDS`); new form reason `CENTRAL_DUPLICATE`; `FormGroup.needs_review` checks `REVIEW_FORM_REASONS` (`form_type_unknown`, `extraction_failed`, `central_duplicate`). Stored inside `commitments.data_json`, so SQLite needs no schema change.
- `tests/test_schemas.py` allows the optional `pattern` key in the YAML files.

## Assumptions (Phase 7 — multi-signal confidence)
60. **Signals, not the model's word.** `pipeline/confidence.py` reads, per field, the reason codes the earlier stages left (`FieldSignals.of`): the field kind, OCR agreement/disagreement (`ocr_agrees` / `ocr_disagrees`), second-reading agreement/disagreement (`reread_*`; for the sender `sender_readings_*`, see 63), failed rules (hard/soft, see 62) and `illegible`. Nothing the model says about its own certainty is used.
61. **Formula** (`WeightedScorer`): base by kind — printed 0.8 (stickers and stamps count as printed), handwritten 0.55, closed list 0.75 (the sender) — `+0.25` OCR agrees, `+0.15` second reading agrees, `−0.30` per hard fail, `−0.10` per soft fail, `−0.20` illegible, clipped to [0, 1] and rounded to 6 decimals. **Added to the starting formula:** `−0.30` when OCR disagrees and `−0.30` when the second reading disagrees. Without them a value whose OCR disagreed but whose second look agreed would score 0.95 (green), against the criterion "disagreement → orange". All weights and the threshold are in `config.ConfidenceSettings` (`CE_CONFIDENCE_*`, table above). Each failed rule subtracts its weight once (two soft fails → −0.20).
62. **Rule severity** (stage 6 stores only codes, assumption 51): *hard* = `id_length_invalid`, `id_not_digits`, `central_not_digits`, `date_invalid`, `date_day_month_swapped`, `date_year_missing`, `unparsed_date` (all but the first leave the value empty anyway); *soft* = `id_checksum_failed` (principle 4), `id_padded`, `date_two_digit_year`, `date_in_future`, `date_before_window`, `commitment_pattern_mismatch`, `sender_fuzzy_match`. A test fails if stage 6 gets a new code without a severity.
63. **The sender's second reading.** Both the classification call and the extraction call read the sender; until now the extraction reading was dropped. It is now matched to the closed list the same way and compared (`classify.compare_sender_readings`): same listed sender → `sender_readings_agree`, a different listed sender → `sender_readings_disagree`, otherwise no outcome. The value is still the classified sender and is never replaced. The extraction text is kept as a `vlm` signal. No extra provider call. The two readings come from the same model on the same page, so they are not fully independent.
64. **Review threshold 0.9 — a temporary default, not calibrated.** `needs_review = confidence < 0.9`, or the value is `illegible` (a partial value is never accepted, whatever the weights). 0.9 rather than the split threshold 0.85, so that an illegible value whose partial digits OCR confirmed (0.8 + 0.25 − 0.2 = 0.85) is not green even before the illegible rule. With the defaults: green = a printed number confirmed by OCR (1.0) or by the second look (0.95), also with one soft fail (e.g. a wrong ID check digit: 0.95 — "a slight drop only"); a sender read the same twice and matched exactly (0.9). Orange = everything else, including **every handwritten value** (max 0.55 + 0.15 = 0.7) and **every value read once only** (see 66). The split threshold `CE_REVIEW_THRESHOLD` is separate and unchanged.
65. **Empty fields** stay `confidence=0`, `needs_review=True`, `not_found` — unless the form type's YAML marks the field `optional: true` (new optional key in `fields.<name>`, default false). Then an empty scalar field gets `optional_missing` and is not flagged, and an empty list field is not flagged (`Commitment.optional_fields`, copied from the YAML by this stage). An unreadable list item (a sticker kept with no value) is always flagged. **No YAML file sets `optional` yet:** the `uncertain` types (no commitment number found in the samples) are candidates for `commitment_numbers: optional: true`, but that would hide a number the model missed, so it is left for the domain experts to decide.
66. **`single_reading`.** A value that no independent reading was compared with gets the code `single_reading` ("read once only, with no confirmation from another source"). That is every name and date (no OCR or second look for them, assumption 45 and Phase 5 limits) and every number whose OCR and second look were both unavailable.
67. **Readable reasons.** `Field.reasons` keeps the machine codes, which later stages and tests read. The new `Field.explanations` holds the same reasons as Hebrew text for the reviewer (`pipeline/reason_texts.py`, e.g. `reread_disagrees` → "הערכים נבדלו בין שתי הקריאות"). The texts describe the check, never the value; a sender candidate names the listed organization. A test fails if a field reason code has no text.
68. **Extension point for a learned model.** `score_fields(commitment, form_type, settings, scorer=None)` is pure (returns a copy). `scorer` is any `Scorer` (`score(FieldSignals) -> float`); the default is `WeightedScorer(settings)`. `ProbabilityModelScorer(model)` wraps any fitted classifier with `predict_proba` (e.g. scikit-learn `LogisticRegression`) trained on `FieldSignals.as_features()` rows (named by `FieldSignals.feature_names()`, label 1 = the value was right). scikit-learn is **not** a dependency yet; nothing is fitted.
69. **Order.** Confidence runs per form right after normalize (`read_form.FormReader`). `central_duplicate` (across forms) is decided later and stays form-level: it flags the row, not the field's score.
70. **Not used yet as signals:** the OCR confidence value (only agree/disagree counts), `bbox_implausible`, `reread_null` on a value that was read, page flags such as `orientation_uncertain`, and `split_uncertain`. They are kept in `reasons` / on the page or form.

### Phase 7 — what was checked
- `tests/test_confidence.py` (no network, no model, no Tesseract): the completion criteria with the default weights — full agreement → green (1.0; also 0.95 on the second look alone); five kinds of disagreement → orange; empty (not read, and read but not normalizable) → orange with confidence 0; an ID with a wrong check digit → 0.95, still green, a smaller drop than a hard fail. Also: the formula on 11 parametrized signal combinations (incl. clipping at both ends), weights and threshold taken from the settings, illegible always reviewed, `single_reading`, the sender (two readings agree → green; fuzzy match or one reading → orange), handwritten stays orange, every stage 6 code has a severity, field kinds, optional scalar and list fields, an unreadable item of an optional list, purity (input unchanged), idempotence, raw value and signals kept, a fake `predict_proba` model replacing the formula, feature names.
- `test_reason_texts.py` (every field reason code of extract / normalize / ocr_check / reread / classify has a Hebrew text), `test_classify.py` (the sender comparison: agree, disagree, no outcome, value never changed), `test_read_form.py` (one form end to end with scripted OCR: OCR-confirmed ID green, disagreeing central number orange with its explanation, names `single_reading`, sender green / orange, settings passed through), `test_schemas.py`, `test_config.py`, `test_xlsx.py` (an empty optional list is not highlighted). Full suite: 616 passed, 4 skipped (the same 4 that need a real Tesseract). Frontend `npm run build` and `npm run lint` pass.

### Phase 7 — what does not work / was not checked
- **No calibration, no accuracy.** The weights and the 0.9 threshold are the hand-set starting point; nothing was measured against ground truth, so no claim is made about how often green fields are right. Not run on `samples/` with a real model (no API key here).
- **With the defaults practically every form is orange:** names and dates have no second signal (66), and handwritten values cannot reach 0.9. Green fields are limited to printed numbers confirmed by OCR or the second look, and a sender read the same twice. This is the honest outcome of the formula, not a bug. It changes only with more signals (e.g. a second look at names and dates, OCR on the date stamp) or with calibration.
- The two sender readings and the numeric second look come from the same model, so their agreement is weaker evidence than OCR agreement; the weights do not reflect that beyond the smaller bonus.
- The Excel file still shows only the highlight; `explanations` are in the API/JSON but not shown in the UI or Excel yet. *(Excel: done in Phase 9, column "הערות לבדיקה"; the UI still does not show them.)*
- Coverage of the new modules was not measured (`coverage` is not installed in this environment).

### Phase 7 changes to existing interfaces
- `confidence.score(commitment, review_threshold)` (stub) → `score_fields(commitment, form_type, settings, scorer=None) -> Commitment`; new `FieldKind`, `FieldSignals`, `Scorer`, `WeightedScorer`, `ProbabilityModel`, `ProbabilityModelScorer`, `score_field`, `field_kind`, `optional_fields`. New module `pipeline/reason_texts.py` (`REASON_TEXTS`, `explain`, `SINGLE_READING`).
- Settings: new `ConfidenceSettings` at `Settings.confidence` (`CE_CONFIDENCE_*`). `Settings.review_threshold` keeps its value and is now documented as the split threshold only.
- `FormReader(...)` takes an optional `confidence_settings` (default `ConfidenceSettings()`); the orchestrator passes `settings.confidence`. `FormReader.read` now also runs the confidence stage and compares the sender's two readings.
- `classify`: new `compare_sender_readings`, codes `sender_readings_agree` / `sender_readings_disagree`. A matched sender's `confidence` (score/100) is replaced by the confidence stage.
- Schema: `Field.explanations`; `Field.revalidated(**update)` (moved from `normalize._rebuilt`); the `Field` validator leaves an empty field unflagged only when its reasons hold `optional_missing`; `NOT_FOUND` and `OPTIONAL_MISSING` constants (`normalize.NOT_FOUND` is now imported from `schemas`); `COMMITMENT_FIELDS`; `Commitment.optional_fields` and `Commitment.field_needs_review(name)` (used by `needs_review`, the Excel export and — mirrored — the UI); `FieldSpec.optional` (YAML key, default false). Stored inside `commitments.data_json`, so SQLite needs no schema change.
- `tests/test_schemas.py` allows the optional `optional` key in the YAML files. Frontend: `Commitment.optional_fields` in `api.ts`.

## Assumptions (Phase 8 — review API and review screen)
99. **Paths of the review endpoints follow the task:** `PATCH /jobs/{id}/commitments/{cid}/fields/{field}` and `GET /jobs/{id}/crop/{cid}/{field}`, where `cid` is the form's id (`FormGroup.id`): a form holds exactly one commitment, so no separate commitment id was added. The old `/groups/...` paths are removed rather than kept as aliases (one path per resource; only our frontend used them). **A corrected field keeps `raw_value`** — the text as read from the form, `None` for an added list item or a field that was not found — instead of taking the reviewer's text (supersedes that part of 93): `value` is the reviewer's, `raw_value` the reading, and `Job.corrections` still records the previous raw value and source.
100. **Several files per upload, one job:** `POST /jobs` takes the multipart field `files` once per file (up to `CE_MAX_FILES`, default 20; each file up to `CE_MAX_UPLOAD_MB`). All files are checked (size, type from the content) before any is written, so a refused upload leaves nothing on disk. The files are read one after the other, in the order sent; rows are sorted by file, then page. In the table each row then says "קובץ n, pages" (n = upload order); the original file names are still not kept (88).
101. **Full page for the reviewer:** `GET /jobs/{id}/pages/{cid}/{page}` returns the stored page JPEG as is (full resolution, the coordinate system of `Field.bbox`, `Cache-Control: no-store`), only for pages of that form (`page_not_in_form` otherwise). The browser scales the image and draws the box over it in percent of the natural size, so no second resolution is stored. Like the crop, nothing is written outside the job's temporary directory.
102. **`GET /senders`** returns the canonical sender names, so the sender field can offer the closed list (`<datalist>`); the backend still validates (422).
103. **Upload screen:** react-dropzone (drag or click, several files, PDF / PNG / JPEG / TIFF / WEBP by MIME type and extension; refused files are listed by name), a removable list of the chosen files, then one upload. Progress comes from the SSE stream (polling as before when it fails): "ממתין בתור…" while `pending`, an indeterminate bar until segmentation has counted the forms, then "נקראו n מתוך m טפסים". Rows fill in as forms finish. Starting a new upload deletes the previous job from the server (one job on screen at a time), and "מחיקת העבודה מהשרת" deletes it on request.
104. **Review table and side panel** (TanStack Table v9, `useTable` with no extra features: the "only uncertain" filter is applied to the data, since nothing else needs table state). One row per commitment; a cell is orange when any of its values needs review (`Commitment.field_needs_review`; an empty required list is one orange "—" whose correction adds item 0); every value is a button. Clicking it (or Enter / Space on it) opens the panel with: the status, the value as read (`raw_value`), the reasons (`Field.explanations`, or the codes if empty), the crop and the whole page with the box outlined. **Keys in the edit box:** Enter confirms — it sends the text as it is, so an unchanged value is confirmed too — and then opens the next orange field in reading order (row by row, column by column, wrapping around); Tab / Shift+Tab move to the next / previous orange field *without* saving; Esc closes the panel and returns focus to the cell. In the table the arrow keys move between cells (in RTL, ← is the next column). Editing is possible only on a `done` job. Dates are typed as DD/MM/YYYY (also `.` or `-`, or YYYY-MM-DD) and sent as YYYY-MM-DD; a two-digit year is refused, never completed (the century is not guessed).
105. **"N לבדיקה"** counts values, not rows: each orange list item counts once, an empty required list once. Forms with a form-level flag (`split_uncertain`, `form_type_unknown`, `extraction_failed`, `central_duplicate`) get a red label in the "דגלי טופס" column (full text on hover) and are counted separately ("n טפסים עם דגל"). "הצג רק לא בטוחים" keeps the rows with `FormGroup.needs_review` (an orange field or a form flag). A form flag cannot be cleared from the screen (the API has no such operation).
106. **Export never blocks.** With orange values or flagged forms left, "ייצוא לאקסל" first shows a dialog with both counts and the note that they stay orange in the file, with the reasons in the notes column; "ייצוא בכל זאת" exports, "חזרה לבדיקה" (or Esc) returns. With nothing left the file downloads at once.
107. **Colours:** the UI now uses the Excel orange (`F4B183`) for cells to review (supersedes "the UI keeps its yellow" in 87); a manually confirmed value is light green. Numbers, IDs and dates are shown in a `dir="ltr"` span inside the right-to-left cell, as is the raw value in the panel.
108. **The Hebrew texts of the form flags are copied** from `reason_texts.FORM_REASON_TEXTS` into `frontend/src/review.ts` (there is no endpoint for them); field reasons come from the backend (`explanations`). Change both places together.

### Phase 8 — what was checked
- Backend (`tests/test_main.py`, fake provider, every job store that is configured): several files → one job with the files in order; one refused file refuses the upload and writes nothing; too many files 413; no files 422; **every** endpoint under `/jobs/{id}` is 404 `job not found` for an unknown job, and every `{cid}` endpoint 404 `commitment not found`; PATCH sets `source=manual`, `needs_review=false`, confidence 1 and keeps `raw_value`, and logs neither the new nor the old value; the page endpoint returns the stored JPEG at full size, 404 `page_not_in_form` / `image_gone`; **a failed form** (provider raises on the first of two files): the job is `done`, the failed form has `error` + `extraction_failed`, the other is read, the SSE stream sends both rows and ends, the failed form can be corrected and the job exported, and the exception message (with a dummy value) is not in the logs; **DELETE** removes the upload, the page images and the export from disk and the rows from the store, after which the job, its events and its pages are 404, a second DELETE is 404, and another job is untouched. `test_corrections.py` checks the kept raw value. Full suite: 789 passed, 77 skipped (Postgres without `TEST_DATABASE_URL`, and real-Tesseract tests).
- Frontend, Vitest (37 tests): review logic (cells, empty lists, Tab order forward / backward / wrap / after a confirmed field, form flags, sorting, dates incl. a refused two-digit year); the table (orange cells and values, counter, `dir="ltr"`, the toggle, form flags, click / Enter / arrow keys); the panel (raw value, reasons, crop and page, the box position in percent, Enter saves, an unchanged value is confirmed, date conversion and refusal, Tab / Shift+Tab / Esc, a 422 in Hebrew, missing image and missing box, read-only while running); the app (upload → rows → counter; a confirmed field turns green, the counter drops and the next orange field opens; the export warning with "חזרה לבדיקה" and "ייצוא בכל זאת"; no warning when nothing is left; a failed upload in Hebrew).
- Playwright (Chrome, real backend with `CE_PROVIDER=fake`, memory store): upload a dummy page → 6 to review → correct the ID with Enter → the cell is not orange, "5 לבדיקה", the next field opened, the raw value is still the reading → export warning → "ייצוא בכל זאת" → `commitments.xlsx` downloaded and not empty. A second test uploads two files → two rows, "קובץ 2, 1", "12 לבדיקה". It writes `docs/screenshots/review_table.png`, `side_panel.png` and `export_warning.png`.
- `npm run build`, `npm run lint` (oxlint) and `tsc -b` pass.

### Phase 8 — what does not work / was not checked
- **The box on the page and the crop were not seen in a real browser:** the fake provider reads no boxes, so the e2e panel shows "no box" and the page without a highlight. The box drawing is checked only in Vitest (positions in percent), and the crop endpoint only in pytest.
- Playwright ran with the installed Chrome (`PLAYWRIGHT_CHANNEL=chrome`): downloading Playwright's own Chromium failed on this machine (network / certificate). Firefox and Safari were not run; no screen reader was tried; the layout was not checked on a narrow screen.
- The content of the downloaded xlsx is not read in the e2e test (only name and size); the content is covered by `test_xlsx.py` / `test_e2e.py`.
- Inside the edit box Tab does not reach the panel's other controls (it jumps between orange fields, as asked); the close button is reachable with Esc or the mouse.
- A value cannot be cleared, and a form flag cannot be cleared (no API). A correction does not re-run normalization or the cross-form `central_duplicate` check (as in 93).
- Postgres: the API tests were not run against Postgres here (no `TEST_DATABASE_URL`).
- The table is not virtualized; a job with hundreds of forms was not tried.

### Phase 8 changes to existing interfaces
- `POST /jobs`: the multipart field is `files` (repeated), was `file`. A client sending `file` now gets 422.
- `PATCH /jobs/{id}/groups/{group}/fields/{field}` → `PATCH /jobs/{id}/commitments/{cid}/fields/{field}`; `GET /jobs/{id}/groups/{group}/fields/{field}/crop` → `GET /jobs/{id}/crop/{cid}/{field}`. The old paths are gone; an unknown form is 404 `commitment not found` (was `form not found`).
- A corrected field's `raw_value` is the reading (was the reviewer's text).
- New: `GET /jobs/{id}/pages/{cid}/{page}`, `GET /senders`, `Settings.max_files` (`CE_MAX_FILES`), `PageImageStore.load_jpeg`, `field_crops.form_page_jpeg` and `PAGE_NOT_IN_FORM`.
- `JobRunner.submit(job, stored)` / `run(job, stored)` take a list of paths (was one path).
- Frontend `api.ts`: `uploadFile(file)` → `uploadFiles(files)`; `getCrop` uses the new path and returns `ImageResult` (`missing` with the `detail`, replacing `gone`); new `getPage`, `correctField`, `getSenders`, `ApiError` (failed requests carry status and `detail`); `Field` now has `raw_value`, `confidence`, `reasons`, `explanations`, `source`; `SPLIT_UNCERTAIN` moved to `review.ts`. The single-file `App.tsx` is split into components.
- `frontend/package.json`: new `@tanstack/react-table`, `react-dropzone`; dev `vitest`, `jsdom`, `@testing-library/*`, `@playwright/test`; scripts `test`, `e2e`. `.gitignore`: Playwright's `test-results/` and `playwright-report/`.

## Assumptions (Phase 9 — Excel export)
84. **No template from the laboratory**, so the format is our choice. One sheet "התחייבויות", one row per form (as before), columns by default: שם פרטי | שם משפחה | ת.ז. | מספר מרכזי | תאריך קבלה | גורם שולח | מספר התחייבות | הערות לבדיקה | קובץ מקור. The selection and order come from `CE_EXPORT_COLUMNS` (`config.ExportSettings`, keys in `EXPORT_COLUMN_KEYS`); `Settings` refuses an unknown or repeated key. The Hebrew headers are fixed in `export/xlsx.py` (`HEADERS`). An optional `pages` column ("עמודים", 1-based) is available but not in the default list, because the task's list does not have it. The sheet "עמודים לא רלוונטיים" stays (headers "קובץ מקור", "עמוד").
85. **Sheet layout:** right-to-left, header row bold and frozen (`freeze_panes="A2"`), column width = longest line in the column + 2 characters, between 8 and 60. openpyxl cannot measure rendered text, so this is a character count. The notes cell wraps, aligned to the top.
86. **Cell types.** ID, central numbers, commitment numbers, names and sender are text cells with `number_format="@"` (leading zeros kept); the received date is a real date cell with `number_format="DD/MM/YYYY"`. Both formats are also set on **empty** cells, so a value the reviewer types in keeps its leading zeros / is read as a date. Several numbers: `CE_EXPORT_MULTI_VALUES=joined` (default) → one cell, `; `-separated; `columns` → "מספר מרכזי 1", "מספר מרכזי 2", …, as many columns as the longest list in the job (at least one).
87. **Uncertain fields:** a field with `needs_review` (the same rule as the UI: `Commitment.field_needs_review`) gets an orange background (`F4B183`, instead of the light yellow `FFF2CC` used until now; the UI keeps its yellow). In `columns` mode each number is coloured on its own; an empty list that must not be empty is coloured in its first column only. The value is never changed and an empty field stays empty — it is coloured, not filled. **"הערות לבדיקה"** sums up why, one line per cause: first form-level causes (`split_uncertain`, `form_type_unknown`, `extraction_failed`, `central_duplicate`; Hebrew texts in the new `reason_texts.FORM_REASON_TEXTS`), then each flagged field as "<header>: <reason>; <reason>" — `Field.explanations` (Phase 7), or the codes turned into text if those are empty, or "סומן לבדיקה" if a field is flagged with no reason at all. A flagged list item is numbered ("מספר מרכזי 2: …") when the list has more than one item. All reasons of a flagged field are listed, including confirming ones (e.g. OCR agreed), so the reviewer sees the whole picture; the texts describe checks, never values. This replaces the columns "פיצול" and "לבדיקה" ("כן"): a row needs review exactly when its notes cell is not empty.
88. *("One file per job" is superseded by 100.)* **"קובץ מקור" is the stored file name** (`FormGroup.source_file`, a generated name such as `3f2a….pdf`), not the name the user uploaded: the original name is not kept (it may contain a patient name, see Privacy). With one file per job, which is how the UI uploads, every row has the same value.
89. **`docs/sample_output.xlsx`** is written by `scripts/make_sample_output.py` from `FakeProvider` dummy readings only (three blank PDF pages run through the real `Orchestrator`, without OCR): a complete row, a row with two central and two commitment numbers and a handwritten short-year date, and a row with no ID and an illegible commitment number. `.gitignore` ignores `*.xlsx` except this file.

### Phase 9 — what was checked
- `tests/test_xlsx.py` writes a workbook, opens it again with openpyxl and compares: headers in the default order; values and types of a full row; ID / central / commitment numbers with a leading zero are text cells with format `@`; the date is a date cell (2025-03-09) with `DD/MM/YYYY`; RTL, `A2` frozen, bold headers; column widths follow content within the cap; empty fields stay empty, are orange and get "השדה לא נמצא בטופס" in the notes; an uncertain ID keeps its value, is orange and its two reasons are in the notes (wrapped); stored explanations are preferred; a reasonless flag gets the generic text; a flagged list item is numbered; form-level reasons come first; an optional empty list is not orange and has no note; `columns` mode (3 central numbers → 3 columns, per-item colour, an empty list coloured once, `@` kept); custom column order with `pages`; the irrelevant-pages sheet; an empty job; invalid column settings rejected. `test_config.py` (defaults, env override, invalid values), `test_reason_texts.py` (every review form reason has a Hebrew text), `test_e2e.py` (upload → export with the fake provider, default headers). Full suite: 681 passed, 4 skipped (the same 4 that need a real Tesseract).
- `docs/sample_output.xlsx` was generated and read back with openpyxl: 3 rows, RTL, `A2` frozen, orange on the expected cells.

### Phase 9 — what does not work / was not checked
- **Not opened in Excel or LibreOffice** here — only read back with openpyxl. Column widths, RTL and wrapping are checked as stored settings, not as they look.
- The notes can be long: a handwritten field read once already has two reasons, so a typical row has 5–7 lines. Nothing is shortened.
- The original upload name is not shown (88). There is no per-field confidence column and no column with the reason codes.
- *(Fixed in Phase 8: the UI uses the same orange and shows the explanations in the side panel.)* The UI still highlights in light yellow and does not show the explanations.

### Phase 9 changes to existing interfaces
- `write_xlsx(job, out_path, settings: ExportSettings | None = None)` — new optional argument (None = `ExportSettings()` from env); the API passes `settings.export`.
- Excel layout: column "קובץ" → "קובץ מקור"; "עמודים" now optional (`pages`, off by default); "פיצול" and "לבדיקה" removed, their content moved to the new "הערות לבדיקה"; highlight colour light yellow → orange; bold header, first row frozen, column widths set. `xlsx.COLUMNS`, `EXTRA_HEADERS`, `NEEDS_REVIEW_YES`, `SPLIT_UNCERTAIN_TEXT` and the other header constants are gone; `HEADERS`, `plan_columns` and `review_notes(group)` are new.
- Settings: new `ExportSettings` at `Settings.export` (`CE_EXPORT_COLUMNS`, `CE_EXPORT_MULTI_VALUES`), `MultiValueMode`, `EXPORT_COLUMN_KEYS`, `DEFAULT_EXPORT_COLUMNS`.
- `pipeline/reason_texts.py`: new `FORM_REASON_TEXTS`.

## Assumptions (deployment fixes — sync report)
71. *(Default superseded by 90.)* **Jobs are kept in memory by default** (`CE_STORAGE=memory`), because the deployment guidelines allow no persistent state on disk and Render's disk is ephemeral anyway. `sqlite` stays for local use only; `docker-compose.yml` no longer mounts the `job-db` volume, and `docker-compose.sqlite.yml` adds it back on request. Supersedes 24.
72. **The container listens on `0.0.0.0:${PORT:-8000}`** (`backend/Dockerfile`), so Render can assign the port. `PORT` is the only variable read without the `CE_` prefix; the `CE_` prefix on all other settings is deliberate (`CE_TMP_DIR`, `CE_TESSERACT_CMD`, `CE_ALLOWED_ORIGINS`, ...).
73. *(Allowed headers and origin validation extended by 111.)* **CORS is off unless `CE_ALLOWED_ORIGINS` is set.** Then `CORSMiddleware` allows exactly those origins, methods `GET`/`POST`/`DELETE`, no credentials. An origin not in the list gets no CORS headers (the browser blocks it) and its preflight gets 400. Same-origin use (the Vite proxy, docker compose) needs nothing.
74. *(The `/api` fallback in a build is superseded by 113.)* **Frontend API base from `VITE_API_URL`** (build time, `frontend/.env.example`), default `/api` (the Vite dev proxy). On Vercel set it to the backend root URL (the backend has no `/api` prefix) and add the Vercel origin to the backend's `CE_ALLOWED_ORIGINS`. An empty value also falls back to `/api`; a trailing slash is dropped. `frontend/vercel.json` builds with Vite and rewrites every path to `index.html` (SPA fallback). `.gitignore` keeps `.env.*` ignored except `.env.example`.

### Deployment fixes changes to existing interfaces
- `Settings.storage` default: `sqlite` → `memory`. `StorageName` members are unchanged.
- `Settings`: new `allowed_origins: tuple[str, ...]` (`CE_ALLOWED_ORIGINS`, default empty). `create_app` adds `CORSMiddleware` only when it is not empty; endpoints unchanged.
- Frontend `api.ts`: the base URL comes from `VITE_API_URL` (default `/api`); the exported functions are unchanged.

## Assumptions (deployment fixes, round 2 — sync report)
75. **Logs go to stdout** (`main.configure_logging`, level `CE_LOG_LEVEL`, default `INFO`). `logging.basicConfig` is a no-op when the root logger already has handlers (pytest, a custom uvicorn `--log-config`). uvicorn's own `uvicorn.error` logger still writes to stderr (uvicorn's default config); only the app's `app.*` logs were changed.
76. **A failed job logs its id and exception type only** (`job <id> failed: ValueError`), at ERROR, with no message and no traceback: a library message can quote field text (e.g. pydantic's `input_value`). The traceback is deliberately not in the logs; reproduce locally to debug. `Job.error` keeps the exception type as before.
77. **At most `CE_MAX_CONCURRENT_JOBS` (2) jobs run at once** (`runner.JobRunner`, an `asyncio.Semaphore` on the server's event loop). Further uploads are accepted (201) and wait with status `pending` (the existing status; no new `queued` value) without holding a worker thread, and start as slots free up. The queue has no length limit other than the job TTL. A server restart fails waiting jobs like running ones (`fail_interrupted`, SQLite only).
78. **Pixel cap per page** (`CE_MAX_IMAGE_PIXELS`, default 50 M ≈ A3 at 400 DPI, ~150 MB per RGB copy). Ingest checks width × height before decoding an image, before rendering a PDF page (page size × `CE_RENDER_DPI`) and before decoding an embedded full-page scan, and raises `FileTooLargeError` (the job fails with that type). The same value is set as Pillow's `Image.MAX_IMAGE_PIXELS` at startup (process-wide), so Pillow itself refuses an image over twice the cap when opening it.
79. **Progress through server-sent events.** `GET /jobs/{id}/events` polls the job store every 0.5 s and sends only changes (see the API table); a `:` comment every 15 s keeps idle connections open, and `X-Accel-Buffering: no` asks proxies not to buffer. To have something to send, the orchestrator now adds a file's groups to the job right after segmentation, sets `FormGroup.finished` when a form's reading ends (also on error) and the runner saves the job after each step. Rows are sent before the cross-form `central_duplicate` check, so the final `end` event (the whole job) is the authoritative state. *(The last sentence is superseded by 109.)* The API has no authentication, so there is no access token to accept in the query string.
80. **Frontend: EventSource first, polling as the fallback.** The UI follows `/events`; if `EventSource` is missing, the job disappears or the stream breaks before `end`, it closes the stream (no browser auto-reconnect) and polls `GET /jobs/{id}` every second as before. Only `finished` rows are shown, in page order, with "n/m טפסים" while the job runs.
81. **Pinned dependencies.** `backend/requirements.lock` pins the whole backend dependency closure (runtime + dev) to the versions the tests ran with. `scripts/lock_requirements.py` writes it from the venv, with environment markers evaluated for the Docker image (Linux, CPython 3.11). It is a pip constraints file: the Docker image runs `pip install -c requirements.lock .` (runtime only), development `pip install -c backend/requirements.lock -e "backend[dev]"`. `pyproject.toml` keeps its `>=` ranges as the compatibility statement. The frontend pins exact versions in `package.json` and installs with `npm ci` (Dockerfile, README).
82. **Ingest limits come from `Settings` only** (`IngestLimits.from_settings`); `IngestLimits` has no defaults of its own and `load_file` requires it.
83. **Segmentation weights are settings** (`config.SegmentSettings`, `CE_SEGMENT_WEIGHT_*`, same defaults as before, each in [0, 1)). `Settings` refuses a VLM weight at or above `CE_REVIEW_THRESHOLD`, so "the model alone never clears a boundary" (28) holds for any configuration: lowering the split threshold below 0.8 now needs a lower `CE_SEGMENT_WEIGHT_VLM` too.

### Deployment fixes round 2 changes to existing interfaces
- New endpoint `GET /jobs/{id}/events`. `POST /jobs` is a plain `def` now (runs in the thread pool); its request and response are unchanged.
- Schema: `FormGroup.finished: bool` (default false; stored inside `commitments.data_json`, so SQLite needs no schema change). While a job runs, `GET /jobs/{id}` now returns the groups found so far, unread ones with `finished=false` (before, groups appeared only at the end of each file).
- `Orchestrator.run(job, files, on_progress=None)`: `on_progress(job)` is called after each segmented file and each read form. New `runner.JobRunner(jobs, pipeline, max_concurrent_jobs)` replaces the `run_job` closure in `main.py`.
- `Settings`: new `max_image_pixels`, `max_concurrent_jobs`, `log_level`, `segment: SegmentSettings`; new check `segment.vlm < review_threshold`.
- `ingest.IngestLimits`: new field `max_image_pixels`, no defaults, `from_settings(settings)`; `load_file(path, limits)` requires `limits`.
- `segment`: the `WEIGHT_*` constants are gone; `rule_evidence`, `add_vlm_answer` and `decide_boundary` take a `SegmentSettings`; `segment(..., threshold, weights=None)` (None = `SegmentSettings()` from env).
- Frontend `api.ts`: `FormGroup.finished`, `JobProgress`, `streamJob(jobId, handlers)`.

## Assumptions (job data in the database — SQLite / Postgres)
90. **Store selection:** `CE_STORAGE` when set (`memory` / `sqlite` / `postgres`); otherwise `postgres` if `DATABASE_URL` is set, else `sqlite`. This replaces the `memory` default of 71: without any setting, jobs now go to `backend/data/app.db`. `docker-compose.yml` still sets `memory`; on Render, `DATABASE_URL` makes it Postgres. **Deviation from the deployment guideline "no persistent state on disk"** — only for the local default without `DATABASE_URL`; the deployment keeps its state in Postgres, not on the container disk. `DATABASE_URL` is read without the `CE_` prefix (as Render names it) and left out of `Settings`' repr.
91. **One schema, versioned migrations** (`backend/data/migrations`, see Storage). The full `FormGroup` stays one JSON column (`commitments.data_json`) rather than one row per field: it round-trips every field exactly, including signals and boxes, and new model fields need no migration. Only filter fields are columns.
92. **Kept now that was dropped before:** the classification of each form (`FormGroup.classification`: form type, `known`, sender as read, match status, best score and up to 3 candidates; before, score and candidates were lost for a matched sender), the read time of each form (`FormGroup.read_ms`), the job's `started_at` / `finished_at`, and several files per job (`job_files`). The model's free-text `evidence` / page-pair `reason` are still not stored (they may quote the page).
93. *(The `raw_value` part and the paths are superseded by 99; the UI part by 104.)* **Manual corrections** (`PATCH .../fields/{field}`, `app/corrections.py`): the value replaces `value` and `raw_value`, `source=manual`, confidence 1, `needs_review=false`, reasons `[manual_correction]` (text "הוזן ידנית על ידי בודק"); the box and earlier readings stay. Each correction is recorded in `Job.corrections` (`corrections` table) with the previous raw value and source. Only on a `done` job. Not re-run after a correction: normalization rules and the cross-form `central_duplicate` check (a form flagged so stays flagged). A value cannot be cleared through the API, and the UI has no editing yet.
94. **Page images on disk, not in the database:** each preprocessed page is written as JPEG (quality 90, full resolution of `original`, the coordinates of `Field.bbox`) to `<CE_TMP_DIR>/<job>/pages/`. The crop is the box plus 10 px, cut on request as PNG with `Cache-Control: no-store`.
95. **Separate file TTL** (`CE_FILE_TTL_S`, default 3600 like `CE_JOB_TTL_S`): a long database retention no longer keeps uploads and page images on disk as long.
96. **Database errors are reported by type only** (`JobStoreError`, API 503); see Storage.
97. **Postgres connections** come from a pool (`CE_DB_POOL_SIZE`, wait `CE_DB_TIMEOUT_S`), opened on first use and closed at shutdown (`JobStore.close`).
98. **Tests on Postgres** run when `TEST_DATABASE_URL` is set: `conftest.pg_conninfo` creates a fresh schema per test (dropped afterwards), and the store contract (`test_job_stores.py`), the API tests (`test_main.py`, `test_e2e.py`) and `test_postgres_store.py` run against it. Without it they are skipped.

### Job data in the database — changes to existing interfaces
- Defaults: `Settings.storage` comes from `CE_STORAGE`, else `DATABASE_URL`, else `sqlite` (was `memory`). `StorageName.POSTGRES` is new.
- `Settings`: new `database_url`, `db_pool_size`, `db_timeout_s`, `file_ttl_s`; `config.DATABASE_URL_ENV`.
- `JobStore` Protocol: new `close()`. New `JobStoreError`. Store constructors take an optional `file_ttl_s`; new `PostgresJobStore(conninfo, storage, ttl_s, file_ttl_s=None, *, pool_size, timeout_s)`.
- `app/storage/sqlite.py`: `init_db()`, `SCHEMA_PATH` and `ADDED_JOB_COLUMNS` are gone (now `SqlJobStore.init` and migrations); `backend/data/schema.sql` moved to `backend/data/migrations/0001_base.sql`. A job may now hold several files (was `ValueError`).
- Schema (additive; defaults keep old JSON valid): `Job.started_at`, `Job.finished_at`, `Job.corrections: list[Correction]`, `Job.group(id)`; `FormGroup.classification: ClassificationResult | None`, `FormGroup.read_ms`; new `SenderCandidate`, `ClassificationResult`, `Correction`, `MANUAL_CORRECTION`.
- `classify.Classification.result()`. `Orchestrator(..., page_images=None)`; `crops.crop_bbox(image, bbox)`.
- API: new `PATCH /jobs/{id}/groups/{group}/fields/{field}` and `GET .../fields/{field}/crop`; CORS methods include `PATCH`; a database failure is 503. `app.state.jobs` exposes the store (tests).
- Frontend `api.ts`: `Field.bbox`, `BBox`, `IMAGE_GONE`, `getCrop()`; the table has a "תמונה" button per value with a box.

## Assumptions (demo deployment — Vercel + Render)
109. **One shared access code for the demo** (`CE_DEMO_ACCESS_TOKEN`), not user accounts. `access.AccessTokenMiddleware` checks every HTTP request except `/health` (Render's health check), in constant time: `Authorization: Bearer <code>`, or on GET only the query parameter `access_token` (EventSource cannot send headers). Writes must send the header. Without the variable there is no check (local use, tests); `CE_REQUIRE_ACCESS_TOKEN=1` (set in `render.yaml`) makes a missing code a startup error, so a forgotten secret fails closed. A code under 16 characters is refused. The names keep the `CE_` prefix (the task said `DEMO_ACCESS_TOKEN` / `ALLOWED_ORIGINS`; see 72).
110. **The code is never logged.** Refusals log `request refused: no valid access token` with no path, IP or value. uvicorn's access log prints the path with its query, so `log_filters.RedactQueryTokenFilter` masks it there (`access_token=***`); `configure_logging` attaches it. Proxies in front (Render) may log full URLs themselves; that is outside the app.
111. **CORS for the code header:** `Authorization` is the one allowed non-simple header; credentials (cookies) stay off. `Settings` refuses `*` and any origin with a path or trailing slash: the browser's `Origin` never has one, and the mismatch would only show as a blocked request. Middleware order: CORS (outermost, so 401 / 429 / 413 carry CORS headers and the frontend can read them) → body size → rate limit (before the code check, so guessing is limited too) → access code → endpoint.
112. **Limits before any work:** `body_limit.BodySizeLimitMiddleware` refuses a body over `CE_MAX_FILES × CE_MAX_UPLOAD_MB` + 1 MB (413) or without `Content-Length` (411) before Starlette spools it to disk. `POST /jobs` counts pages without rendering (`ingest.count_pages`) and refuses a file over `CE_MAX_PAGES` or an upload over `CE_MAX_JOB_PAGES` (413 `too many pages`, nothing stored); a file that cannot be opened is still accepted and fails in ingest with its specific type, as before. `rate_limit` keeps fixed one-minute / one-hour windows per client IP in memory (per process, lost on restart). The client IP is the socket peer, or with `CE_TRUSTED_PROXY_HOPS=n` the n-th `X-Forwarded-For` entry from the right (the ones proxies appended; entries further left are client-controlled). `render.yaml` assumes one appending proxy (`1`); not verified on Render.
113. **The frontend calls the backend directly** at `VITE_API_URL`, the event stream too; `vite build` fails without an absolute `http(s)` URL, so a Vercel build cannot silently call its own host. `npm run dev` keeps the `/api` proxy. `vercel.json` has only the SPA fallback (no API rewrites).
114. **Access screen** (`components/AccessGate.tsx`): on load the frontend calls `GET /senders` with the stored code (or none): 200 → the app, 401 → the code screen, network error → a retry button (the first request to a sleeping Render instance can take about a minute; the screen says so). The code is kept in `sessionStorage` only (gone with the tab). A later 401 anywhere (`api.ts`) drops the code and shows the screen again. `components/DemoBanner.tsx` shows "הדגמה, יש להעלות רק טפסי דוגמה, לא נתוני אמת" on every screen, fixed at the bottom so it never covers the sticky table header or side panel.
115. **Short-lived files:** at startup and at shutdown every job directory under `CE_TMP_DIR` is deleted (`TempStorage.clear`, only directories named like a job id), and a background task purges expired jobs and files every `CE_PURGE_INTERVAL_S`, not only on the next request. `render.yaml` sets `memory` storage and 30-minute TTLs. Deviation: with SQLite locally, a restart (also `--reload`) now deletes page images too, so crops of older jobs show `image_gone`; `CE_CLEAR_TMP_ON_RESTART=0` keeps them.
116. **Container:** Tesseract with `heb`, `eng` and `osd` data; runs as a non-root user; still `0.0.0.0:${PORT:-8000}`. `render.yaml` (repo root) is a Blueprint: Docker, health check `/health`, free plan, no auto-deploy. The secrets and `CE_ALLOWED_ORIGINS` are `sync: false` (entered in the dashboard). The demo limits have values (`CE_MAX_PAGES=6`, `CE_MAX_FILES=3`, `CE_MAX_UPLOAD_MB=10`, one job at a time), based on estimates, not measurements (docs/DEPLOY.md).

### Demo deployment — what was checked
- Backend tests: the code check (header; query on GET only; 401 on every endpoint including SSE, crop and page images, with CORS headers on the refusal), CORS refusing a foreign origin (preflight 400, no `Access-Control-Allow-Origin` even with the right code), the rate limits, the body and page limits, the log masking, the start/stop cleanup and the new settings checks.
- Frontend: Vitest for the Bearer header, the code in the EventSource query, 401 handling, the access screen and the banner. `npm run e2e:demo` (Playwright) builds the frontend with `VITE_API_URL` and serves it on another origin than the backend (code, CORS and rate limits on). It goes through a wrong code, the right code, an upload, the event stream and a reload.

### Demo deployment — what was not checked
- The deployment itself (Render, Vercel), the Docker image build (no Docker daemon on the development machine), and `render.yaml` against Render's schema.
- How Render fills `X-Forwarded-For` (the rate limit's client IP), and memory use on a 512 MB instance.

### Demo deployment changes to existing interfaces
- `Settings`: new `demo_access_token` (not in repr), `require_access_token`, `rate_limit_per_minute`, `upload_limit_per_hour`, `trusted_proxy_hops`, `max_job_pages`, `purge_interval_s`, `clear_tmp_on_restart`; `allowed_origins` is validated. New `config.MIN_ACCESS_TOKEN_LENGTH`.
- New modules `app/access.py`, `app/rate_limit.py`, `app/body_limit.py`, `app/log_filters.py`; `main.add_middleware(app, settings)`, `main.purge_periodically(jobs, interval_s)`.
- `ingest.count_pages(data, kind)`; `TempStorage.clear()`.
- API: 401 / 429 / 411 and 413 `too many pages` / `request too large` as above; CORS allows the `Authorization` header.
- Frontend: `api.checkAccess(token)`, and every call sends the stored code; new `access.ts`, `AccessGate`, `DemoBanner`; `vite build` requires `VITE_API_URL`; new `npm run e2e:demo` (`playwright.demo.config.ts`, `e2e/demo.spec.ts`), which the default `npm run e2e` ignores.

## Storage
- **Interface:** `app/storage/base.py` defines the `JobStore` Protocol (`init`, `close`, `create`, `get`, `update`, `delete`, `list_commitments`, `fail_interrupted`, `purge_expired`); `create_job_store(settings, tmp)` builds `PostgresJobStore` (`storage/postgres.py`), `SqliteJobStore` (`storage/sqlite.py`) or `MemoryJobStore` (`storage/memory.py`). Both SQL stores share all queries (`storage/sql.py`); they differ only in the connection (SQLite: one per operation; Postgres: a `psycopg_pool` pool) and the migration lock.
- **Choosing the store:** `CE_STORAGE` if set; otherwise `postgres` when `DATABASE_URL` is set, else `sqlite` (`backend/data/app.db`). Local work with Postgres: set `DATABASE_URL` to a local database (see Running). Deployment: [docs/DEPLOY.md](docs/DEPLOY.md).
- **Schema** (`backend/data/migrations/NNNN_name.sql`, portable SQL, the same in both databases). At startup `init()` applies the migrations a database has not seen yet, in one transaction under a lock (SQLite `BEGIN IMMEDIATE`, Postgres `pg_advisory_xact_lock`), and records each in `schema_version(version, applied_at)`. So startup is idempotent and safe with several processes, and a failed migration leaves nothing behind. Never edit a released migration; add the next one. A SQLite file from before versions (the old `schema.sql`) is migrated in place without losing jobs.
  - `jobs(id, created_at, status, form_type, sender, error, needs_review, irrelevant_pages_json, split_vlm_calls, started_at, finished_at)` — `form_type`/`sender` are set when all forms of the job share them.
  - `job_files(job_id, seq, stored_name)` — the generated names of the uploaded files (several per job are allowed now).
  - `commitments(id, job_id, seq, data_json, needs_review)` — `data_json` is the whole `FormGroup`: pages, rotation, `split_confidence` and reasons, form reasons and error, classification (form type, sender as read, match status, score and candidates), provider calls and read time, and the full `Commitment` with every `Field` (value, raw_value, confidence, needs_review, reasons, explanations, source, bbox, signals).
  - `corrections(job_id, seq, group_id, field, corrected_at, data_json)` — manual corrections, with the previous raw value and source.
  - Every child table references `jobs ON DELETE CASCADE`, so `DELETE /jobs/{id}` removes all of a job's rows; it also deletes the job's temporary directory.
- **Not in the database:** images. Uploads, page images (JPEG, for the crop endpoint) and exports live in the job's temporary directory (`CE_TMP_DIR`), deleted with the job or after `CE_FILE_TTL_S`. Crops are cut on request and never stored. When a page image is gone (TTL, or a restart on Render's ephemeral disk) the crop endpoint returns 404 `image_gone` and the UI shows "התמונה כבר לא זמינה"; the job's data stays.
- **Errors:** a driver error becomes `JobStoreError` carrying the driver's exception type only (raised `from None`, so no message, value or query parameter reaches a log through the traceback); the API answers 503 `job store unavailable` and logs `job store failed: <type>`.
- **Privacy:** the database holds patient data. `*.db` is git- and docker-ignored. With SQLite, delete the file (or the `job-db` volume of `docker-compose.sqlite.yml`) to wipe all jobs.
- **Limits:** a job is saved when it is created, when it starts, after each segmented file and read form, and when it finishes. A job that was `pending` or `running` when the server stopped is not resumed: at startup it is marked `failed` with error `Interrupted` (its upload may be gone, e.g. on Render). Two reviewers correcting the same job at the same moment: the last write wins.

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
- A field that was not found: `value=None`, `needs_review=True`, `reasons` includes `not_found`, `confidence=0`. The validator enforces this, and the flag cannot be "cleared" — the one exception is a field the form type's YAML marks `optional`, which the confidence stage tags `optional_missing` (assumption 65).
- `confidence` is computed in the pipeline from several signals; `reasons` keeps the signal codes and `explanations` the same reasons as Hebrew text.
- A failed ID-number check digit is a soft reason (`id_checksum_failed`) and does not invalidate the value.

# commitments-extractor

An engine that reads commitment forms received by the Central Virology Laboratory, extracts their fields, flags anything uncertain and exports to Excel.

**Status: Phase 5 (extraction core)** — upload a PDF or an image, straighten and clean every page, split the pages into forms, classify each form and match its sender to the closed list, extract each form through a provider (value, box, legibility per field), cross-check printed numbers with Tesseract, take one second look at unconfirmed numbers, download an Excel file. Forms are read concurrently; a failed form is marked and does not stop the others. Wired: `ingest -> preprocess -> segment -> classify -> extract -> OCR check -> second look`; normalize and confidence are still stubs (`NotImplementedError`), so every field stays flagged for review. No extraction or classification accuracy has been measured.

## Layout
```
backend/app/main.py           FastAPI app: /jobs upload, status, export, delete
backend/app/config.py         settings: paths, provider, review threshold (env vars CE_*)
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
backend/app/export/xlsx.py    Excel export
backend/app/storage/          JobStore Protocol (base.py) + sqlite / memory job stores, tmp.py: temporary per-job files
backend/form_types/*.yaml     one definition per observed form type (25 types) + generic.yaml (fallback)
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
scripts/classify_report.py    stage 4 on samples/: type, sender as read, matched sender, score
scripts/extract_report.py     stage 5 on samples/: fields filled and model calls (counts only)
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

## Assumptions (Phase 4 — form type and sender)
32. **One classification call per form, on its first page only** (`ExtractionProvider.classify`). The answer is `{"form_type": <id> | "unknown", "sender_raw", "evidence"}` (`FormClassification`, schema `form_classification_schema`). The prompt lists every type with its `display_name` and the `sender.hint` of its YAML, and repeats the sender rule (the billed body; not the HMO on an admission sticker, not a clinic). `evidence` may quote the page, so it is neither logged nor stored.
33. **Unknown layout** → `backend/form_types/generic.yaml`: the form is still extracted, with generic hints, and gets `form_type_unknown` in the new `FormGroup.form_reasons`, which puts it in review ("לבדיקה" in Excel). `generic.yaml` is not a layout: `load_form_types` leaves it out, so it is never offered to the model and gives segmentation no title keywords. `Commitment.form_type_id` is the YAML used, so it is `generic` for an unknown layout.
34. **Sender matching** (`classify.SenderMatcher`; the decision algorithm is in the `classify.py` docstring): both sides are normalized (niqqud, quotes/gershayim, hyphens and maqaf, and whole generic phrases such as "המרכז הרפואי", "קופת חולים", "שירותי בריאות", "מעבדה", "בי"ח" are removed), then scored with rapidfuzz `WRatio` against every canonical name, alias and abbreviation, keeping the best score per sender. **≥ 88** is accepted unless a *different* sender is within **5 points** (ambiguous). Not in the list / ambiguous / not read → `sender = null`, `needs_review`, with `sender_not_in_list` / `sender_ambiguous` and up to 3 `sender_candidate:<name>=<score>` reasons. A match keeps `raw_value` and `confidence = score/100` (one signal only; the confidence stage may combine it), plus `sender_fuzzy_match` below 100. 88 is from the task; the 5-point gap was chosen by hand. Neither was fitted.
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
42. **Dates are copied as written** (changed from Phase 1, where the model was asked for ISO). `extract` parses `D.M.YYYY` (also `/` or `-`, day first as on Israeli forms) or `YYYY-MM-DD` (`normalize.parse_date`); a 2-digit or missing year is not completed: the date stays empty with `unparsed_date` and the text in `raw_value`.
43. **A box is used only when it is plausible for one value:** on a page of the form, inside the image (±2% overshoot is clipped), corners in order, at most 15% of the page area. Otherwise `Field.bbox` is empty and the field gets `bbox_implausible`. Boxes are stored in `original` pixels (same geometry as the image sent to the model, see 17).
44. **OCR cross-check (stage 5b)** — only ID, central and commitment numbers whose value is not handwritten: crop = box + 10 px (in `original` pixels) from the binarized `ocr_image` (grayscale `original` if binarizing is off), Tesseract `--psm 7` with a `0123456789` whitelist (languages as configured). Digits only are compared, so spaces/dashes do not count. Outcome codes: `ocr_agrees`, `ocr_disagrees`, `ocr_empty` (no digits read), `ocr_unavailable` (no Tesseract, no plausible box, crop under 8 px, a value with letters, or an OCR error). `ocr_empty`/`ocr_unavailable` are **not** a disagreement. The OCR digits and their confidence are stored as an `ocr` signal in the new `Field.signals`; the model's value is never changed.
45. **Second look (stage 5c)** — the confidence stage does not exist yet, so "needs review" here means *not confirmed by OCR*: a numeric field is looked at again if it is `illegible` (always, even if OCR agreed with the partial digits), or OCR disagreed / read nothing / was unavailable, or it is handwritten, or it is missing but has a box. A missing value without a box that is not marked illegible is just not on the form. Only numeric fields get it, because the narrow prompt asks for digits ("Which number is written here? Digits only, or null" — `read_number_v1.md`); names and dates do not. Crop = box + 10 px from the **colour** `original` (never binarized), enlarged 2.5× (capped at 1600 px), sent alone. Once per field, at most 8 per form (`reread_limit` beyond). The answer is a `reread` signal, never an overwrite — not even when the value is missing and the second look finds digits (`reread_found_value`). Codes: `reread_agrees` / `reread_disagrees` / `reread_null` / `reread_found_value` / `reread_unavailable` / `reread_failed`.
46. **Concurrency.** After segmentation, all forms of a file are read concurrently (`asyncio`); every provider call (classify, extract, second looks) runs in a worker thread under one `asyncio.Semaphore` (`CE_EXTRACT_CONCURRENCY`, 4). tenacity retries `RetryableProviderError` (timeout, connection error, HTTP 408/429/5xx, Anthropic 529) and the per-call timeout (`CE_EXTRACT_CALL_TIMEOUT_S`, 240 s) up to 3 attempts, waiting 2, 4 … s (max 30) plus jitter; other errors are not retried. The semaphore is not held while waiting to retry. A timed-out call is abandoned but its thread runs on until the provider's HTTP timeout ends it, so the per-call timeout is set above the HTTP timeouts. Gemini keeps its own 429/503 retry loop (Phase 2) and raises a non-retryable error when it gives up, so its calls are not retried twice for overload. Segmentation calls are still made one by one, outside this.
47. **A failed form does not fail the job:** its `FormGroup.error` holds the exception type, `form_reasons` gets `extraction_failed` (row flagged for review), and the other forms are read. The job is `failed` only when every form failed (`Job.error` = the first form's error type) — e.g. a missing API key. A failed second look only flags its field (`reread_failed`).
48. **Call counting:** `FormGroup.vlm_calls` counts every provider call made to read that form, retries included; segmentation calls stay in `Job.split_vlm_calls`.
49. **Still no confidence.** Signals and reason codes are collected for stage 7; until then every field keeps `confidence=0` and `needs_review` (assumption 11). Model `note` text is stored in the VLM signal for a reviewer and never logged.

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

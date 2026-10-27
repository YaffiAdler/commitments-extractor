# Measuring handwritten name reading

How to build a small test set of **handwritten dummy names**, run the current pipeline on it and read the report. The set is how we decide whether a change to name reading helps (focused second look, ink separation, a name list, …): every such change stays opt-in until this set shows it does.

**Never use real patient data.** Every name on every page is an invented dummy name, written for this purpose. Do not scan filled-in forms from the laboratory, not even with the names covered.

## 1. What a set is

A set is one folder:

```
my_set/
  labels.csv
  001.jpg
  002.jpg
  ...
```

- Each image is **one whole page** (the pipeline reads whole pages, as in production): PNG, JPEG, TIFF, WEBP or PDF. Phone photos in HEIC must be converted to JPEG first.
- `labels.csv` (UTF-8) has one row per page and the columns:

| Column | Content |
|---|---|
| `image` | file name inside the folder, e.g. `001.jpg` |
| `first_name` | the first name **exactly as written on the page** |
| `last_name` | the last name exactly as written on the page |
| `kind` | `handwritten` (written by a person) or `synthetic` (made by the script below) |

Further columns are allowed and ignored by the tool; `writer` (a code such as `W3`) and `pen` (e.g. `blue ballpoint`) are useful for your own bookkeeping. An empty `first_name` / `last_name` cell means that field was deliberately left blank on the page; the right answer is then an empty field.

Keep sets **outside the repository** or under `eval/name_sets/`, which is git-ignored. File names must not contain names: use `001.jpg`, `002.jpg`, ….

## 2. Collecting ~40–50 handwritten pages

### The page
Print the blank dummy form, which has "שם פרטי" and "שם משפחה" boxes and a few printed dummy rows:

```bash
.venv/Scripts/python scripts/make_synthetic_names.py <out_dir> --font <hebrew font .ttf> --count 1 --template
# -> <out_dir>/template.pdf (A4)
```

If you have **blank, never-filled** copies of the real form layouts (e.g. an empty Clalit or Leumit form), use those as well: the layout, the printed lines and the label positions then match production better. A blank form has no patient data.

### The names
Prepare a list of 40–50 dummy name pairs before writing, so it covers the hard cases on purpose:

- **Final letters** ך ם ן ף ץ — at least 5 occurrences of each over the whole set (e.g. ברוך, אברהם, מנחם, אהרן, נחמן, יוסף, וולף, הורוביץ, רבינוביץ).
- **Confusable letters**: ד/ר (דוד, דרוק), ו/ן/ז/ג (זאב, גיטל), ה/ח/ת (חנה, בתיה), כ/ב (כהן, ברגמן), ס/ם/ט (סגל, שטרן), ע/צ (עזריאל, צוקרמן).
- **Long surnames** (8+ letters: ליכטנשטיין, זילברשטיין, מנדלבאום) and a few short ones (לוי, סגל).
- **Quote marks and two-part names**: כ"ץ, בן ציון, a hyphenated surname.
- A few pages with **one field left blank** (leave the cell empty in the CSV).

The lists in `scripts/make_synthetic_names.py` (`FIRST_NAMES`, `LAST_NAMES`) can be used as a starting point.

### Writers and pens
- **Several writers**: 5–8 people, 6–10 pages each. Different hands matter more than more pages from one hand. Ask each writer for consent and keep only a code (`W1`…) in the CSV.
- **Several pens**: blue and black ballpoint, gel pen, a thin marker; a few pages in a hurry, as at a reception desk.
- Write inside the boxes as on a real form. Do not trace or print in block letters unless that is how the writer normally writes.

### Scanning or photographing
- Scan most pages **in colour at 300 DPI** (like the samples); colour matters for the later ink-separation test.
- Photograph some with a phone (daylight, no flash, the whole page in the frame, held roughly flat), since some forms arrive that way.
- Save one page per file and number them.

### Filling in the CSV
- Fill it in Excel or LibreOffice and save as **"CSV UTF-8"** (Excel adds a BOM; that is fine). Write each name as it appears **on the page**: if a writer misspelt a name, record the misspelling — the tool measures reading, not spelling.
- Check every row against its image once; a wrong label is counted as a reading error.

## 3. Synthetic pages (sanity check only)

```bash
.venv/Scripts/python scripts/make_synthetic_names.py <out_dir> --font <handwriting-style .ttf> [--label-font <printed Hebrew .ttf>] [--count 40] [--seed 7]
```

The script renders dummy names in a handwriting-style font you provide (fonts are not bundled; use one whose licence allows this) onto the dummy form, with random size, slant, position and ink colour, then turns the page slightly, blurs it, adds noise and saves a JPEG. The folder is a ready set with `kind=synthetic`.

**Synthetic results are only a sanity check**: they show that the tooling works and catch gross failures. A font is far more regular than handwriting, so these numbers say nothing reliable about real accuracy; the report says so whenever synthetic pages are present, and reports them apart from handwritten ones.

## 4. Running the evaluation

```bash
# without an API key (fixed dummy answers: checks the tooling only)
CE_PROVIDER=fake .venv/Scripts/python scripts/name_eval.py <set_dir>

# with Claude (your own key); add CE_TESSERACT_CMD / TESSDATA_PREFIX if Tesseract is installed
ANTHROPIC_API_KEY=... .venv/Scripts/python scripts/name_eval.py <set_dir> [--out eval/name_report.md] [--workers 2]
```

Each page runs through the **current** pipeline as its own job (preprocess → segment → classify → extract → … → confidence), with the settings from the environment. With Claude a page currently costs 2 model calls (classify + extract), plus one second look per number OCR did not confirm (on the dummy form: the printed ID number, when Tesseract is not installed). The report goes to `eval/name_report.md` (git-ignored). It holds counts, rates and single letters only — never a name or a file name — so it can be shared.

To compare a change, run the same set twice, once with the new `CE_*` setting off and once on, and compare the two reports. For the name second look: `CE_NAME_REREAD=0` versus `CE_NAME_REREAD=1`, then the variants one by one (e.g. `CE_NAME_REREAD_VARIANTS=wide`, `wide,ink`, `wide,letters`) to see which input helps. The configuration line at the top of each report records the settings that shape name reading.

## 5. Reading the report

| Column | Meaning |
|---|---|
| Exact | the name read equals the name written, after trimming, collapsing spaces, removing quotes and niqqud and unifying final letters |
| Exact, finals as written | the same, but a final letter written as a regular one (or the other way round) counts as wrong |
| Left empty | the pipeline left the field empty (flagged). The safe failure: nothing wrong is exported |
| CER | character error rate: edits needed / letters written, over all fields of the row |
| Flagged for review | `needs_review` was set |
| **Wrong and not flagged** | a wrong name that would be exported without review. The number that must stay at 0 |

With the name second look on, the "Name second look" table splits the fields by outcome (all extra readings agree / some disagree / not compared) and shows how many were right in each, and how many wrong names had the right one among the candidates. This is the evidence for any change to the confidence weights: agreement may only let a name pass review if the "agree" row has (almost) no wrong names over several writers.

The letter tables count, for names that were read, which letter was written and which was read (substitutions), and letters dropped or added. The watched groups are the usual handwriting confusions plus final/regular pairs.

**Small sets are noisy.** With 40–50 pages (80–100 name fields), a difference of a few percentage points between two runs can be chance; a change is convincing when it moves "Exact" by 10+ points, or moves "Wrong and not flagged", consistently over several writers.

## Names from review work

Once reviewers correct or approve names in the review screen, each finished job can be exported as a name-review CSV (`GET /jobs/{id}/name-review.csv`): one row per name a reviewer saved, with whether the reading was right, the edit distance and the single letters that were confused, and **no names**. Download it before the job expires and sum up several files:

```bash
.venv/Scripts/python scripts/name_review_report.py job1.csv job2.csv   # -> eval/name_review_report.md
```

The report has the same columns as the name report above, per source as read and second look outcome. It only counts names that a reviewer saved, so it describes reviewed fields, not every field. Keep the CSV files inside the lab: a short name with one confused letter reveals that letter and the name's length.

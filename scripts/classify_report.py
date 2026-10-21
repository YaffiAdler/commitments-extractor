"""Stage 4 report over samples/: form type, sender as read, matched sender and score.

Runs ingest -> preprocess -> segment -> classify on every PDF in samples/
with the configured provider (``CE_PROVIDER``; a real one needs its API key)
and writes a Markdown table per form, with the expected form type taken from
``seen_in`` of the YAML files. Senders that are not matched are listed with a
suggested alias — nothing is added to senders.json automatically.

The report holds organization names only (form types and senders), never
patient fields, and the model's ``evidence`` text is not written. It goes to
eval/classify_report.md (git-ignored) unless ``--out`` is given.

Usage: python scripts/classify_report.py [--samples DIR] [--out FILE] [--limit N]
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from dataclasses import dataclass
from itertools import islice
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_DIR / "backend"))

from app.config import Settings  # noqa: E402
from app.ocr import create_ocr_engine  # noqa: E402
from app.pipeline import ingest, preprocess, segment  # noqa: E402
from app.pipeline.classify import (  # noqa: E402
    GENERIC_FORM_TYPE_ID,
    SenderMatch,
    SenderMatcher,
    SenderMatchStatus,
    classify,
    load_form_types,
    load_generic_form_type,
)
from app.pipeline.normalize import load_senders  # noqa: E402
from app.providers import create_provider  # noqa: E402
from app.schemas import FormType  # noqa: E402

DEFAULT_SAMPLES_DIR = REPO_DIR / "samples"
DEFAULT_OUT = REPO_DIR / "eval" / "classify_report.md"
# "part 1 p. 2,3,4,7,11,25; part 2 p. 20, 21" in seen_in; "חלק 1" in the sample file names.
_SEEN_IN_PART = re.compile(r"part\s+(\d+)\s+p\.\s*([\d,\s]+)")
_FILE_PART = re.compile(r"חלק\s+(\d+)")
NO_EXPECTATION = "—"
OK_MARK = "✓"
WRONG_MARK = "✗"
# Below this a not-in-list name probably is a new sender rather than another spelling.
ALIAS_SUGGESTION_MIN_SCORE = 70.0


@dataclass(frozen=True)
class Row:
    file_name: str
    pages: list[int]
    expected: str | None
    form_type: str
    sender: SenderMatch


def expected_form_types(form_types: dict[str, FormType]) -> dict[tuple[int, int], str]:
    """(part, 1-based page) -> form type id, from ``seen_in`` of every YAML."""
    expected: dict[tuple[int, int], str] = {}
    for form_type in form_types.values():
        for part, pages in _SEEN_IN_PART.findall(form_type.seen_in):
            for page in re.findall(r"\d+", pages):
                expected[(int(part), int(page))] = form_type.id
    return expected


def file_part(file_name: str) -> int | None:
    found = _FILE_PART.search(file_name)
    return int(found.group(1)) if found else None


def suggestion(match: SenderMatch) -> str:
    """What to do about a sender that was not matched (for a human to decide)."""
    if match.status is SenderMatchStatus.NOT_READ:
        return "no sender read"
    best = match.candidates[0] if match.candidates else None
    if match.status is SenderMatchStatus.AMBIGUOUS and best:
        names = " / ".join(name for name, _ in match.candidates[:2])
        return f"ambiguous between {names}: add '{match.raw}' as an alias of the right one"
    if best and best[1] >= ALIAS_SUGGESTION_MIN_SCORE:
        return f"add '{match.raw}' as an alias of '{best[0]}' (score {best[1]:.0f}) if it is the same body"
    return f"no close entry: new sender '{match.raw}'? (confirm with the lab)"


def classify_file(path: Path, settings: Settings, form_types: dict[str, FormType], generic: FormType,
                  matcher: SenderMatcher, provider, ocr, limit: int | None) -> list[Row]:
    pages = [preprocess.preprocess(p, settings.preprocess, ocr) for p in islice(ingest.load_file(path), limit)]
    by_page_no = {page.page_no: page for page in pages}
    result = segment.segment(pages, ocr, form_types, provider, settings.review_threshold)
    part = file_part(path.name)
    expected = expected_form_types(form_types)
    rows = []
    for group in result.groups:
        found = classify([by_page_no[n] for n in group.pages], form_types, generic, matcher, provider)
        rows.append(Row(path.name, group.pages, expected.get((part, group.pages[0] + 1)) if part else None,
                        found.form_type.id, found.sender))
    return rows


def _cell(text: str | None) -> str:
    return (text or "").replace("|", "\\|")


def render(rows: list[Row], provider_name: str, seconds: float) -> str:
    known = [r for r in rows if r.expected]
    right = sum(r.form_type == r.expected for r in known)
    matched = sum(r.sender.status is SenderMatchStatus.MATCHED for r in rows)
    lines = [
        "# Stage 4 report — form type and sender on samples/",
        "",
        f"Provider: `{provider_name}`. Forms: {len(rows)}. Time: {seconds:.0f} s.",
        f"Form type equal to `seen_in` of the YAML files: {right} of {len(known)}. "
        f"Unknown (`{GENERIC_FORM_TYPE_ID}`): {sum(r.form_type == GENERIC_FORM_TYPE_ID for r in rows)}. "
        f"Sender matched: {matched} of {len(rows)}.",
        "",
        "| File | Pages | Expected type | Type | | Sender as read | Matched sender | Status | Score | Candidates |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        mark = NO_EXPECTATION if not r.expected else OK_MARK if r.form_type == r.expected else WRONG_MARK
        candidates = "; ".join(f"{name} {score:.0f}" for name, score in r.sender.candidates)
        lines.append(
            f"| {_cell(r.file_name)} | {', '.join(str(p + 1) for p in r.pages)} | {r.expected or NO_EXPECTATION} "
            f"| {r.form_type} | {mark} | {_cell(r.sender.raw)} | {_cell(r.sender.canonical)} "
            f"| {r.sender.status.value} | {r.sender.score:.0f} | {_cell(candidates)} |")
    unmatched = [r for r in rows if r.sender.status is not SenderMatchStatus.MATCHED]
    lines += ["", "## Senders not matched — suggestions (not applied)", ""]
    lines += [f"- {_cell(r.file_name)} p. {r.pages[0] + 1}: {suggestion(r.sender)}" for r in unmatched] or ["None."]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--samples", type=Path, default=DEFAULT_SAMPLES_DIR)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--limit", type=int, default=None, help="first N pages of each file (for a quick run)")
    args = parser.parse_args()

    settings = Settings()
    form_types = load_form_types(settings.form_types_dir)
    generic = load_generic_form_type(settings.form_types_dir)
    matcher = SenderMatcher(load_senders(settings.senders_path))
    provider = create_provider(settings)
    ocr = create_ocr_engine(settings)
    started = time.monotonic()
    rows = [row for path in sorted(args.samples.glob("*.pdf"))
            for row in classify_file(path, settings, form_types, generic, matcher, provider, ocr, args.limit)]
    args.out.write_text(render(rows, provider.name, time.monotonic() - started), encoding="utf-8")
    print(f"{len(rows)} forms -> {args.out}")


if __name__ == "__main__":
    main()

"""Stage 5 report over samples/: how many fields were filled and how many
model calls were made.

Runs the whole pipeline (``Orchestrator``) on every PDF in samples/ with the
configured provider (``CE_PROVIDER``; a real one needs its API key) and OCR
(Tesseract if installed). The report holds counts only — never field values,
notes or boxes — and goes to eval/extract_report.md (git-ignored) unless
``--out`` is given. It does not measure accuracy (that is stage 10).

Usage: python scripts/extract_report.py [--samples DIR] [--out FILE]
"""
from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_DIR / "backend"))

from app.config import Settings  # noqa: E402
from app.ocr import create_ocr_engine  # noqa: E402
from app.pipeline.classify import load_form_types, load_generic_form_type  # noqa: E402
from app.pipeline.normalize import load_senders  # noqa: E402
from app.pipeline.orchestrator import Orchestrator  # noqa: E402
from app.providers import create_provider  # noqa: E402
from app.schemas import Commitment, FormGroup, Job, SignalSource  # noqa: E402

DEFAULT_SAMPLES_DIR = REPO_DIR / "samples"
DEFAULT_OUT = REPO_DIR / "eval" / "extract_report.md"
SCALAR_FIELDS = ("first_name", "last_name", "id_number", "received_date", "sender")
LIST_FIELDS = ("central_numbers", "commitment_numbers")
REPORTED_REASON_PREFIXES = ("ocr_", "reread_", "illegible", "handwritten", "bbox_implausible", "unparsed_date")


def _fields(commitment: Commitment):
    for name in SCALAR_FIELDS:
        yield getattr(commitment, name)
    for name in LIST_FIELDS:
        yield from getattr(commitment, name)


def render(jobs: list[tuple[str, Job, float]], provider_name: str, ocr_on: bool) -> str:
    groups: list[FormGroup] = [g for _, job, _ in jobs for g in job.groups]
    read = [g for g in groups if not g.error]
    split_calls = sum(job.split_vlm_calls for _, job, _ in jobs)
    form_calls = sum(g.vlm_calls for g in groups)
    rereads = sum(s.source is SignalSource.REREAD for g in read for f in _fields(g.commitment) for s in f.signals)
    reasons = Counter(r for g in read for f in _fields(g.commitment) for r in f.reasons
                      if r.startswith(REPORTED_REASON_PREFIXES))
    lines = [
        "# Stage 5 report — extraction on samples/",
        "",
        f"Provider: `{provider_name}`. OCR: {'Tesseract' if ocr_on else 'not available'}. "
        f"Files: {len(jobs)}. Forms: {len(groups)} (failed: {len(groups) - len(read)}). "
        f"Time: {sum(seconds for _, _, seconds in jobs):.0f} s.",
        "",
        "Counts only; no accuracy is measured here (stage 10).",
        "",
        "## Model calls",
        "",
        "| | Calls |",
        "|---|---|",
        f"| Segmentation (page pairs) | {split_calls} |",
        f"| Reading forms (classify + extract + second looks, retries included) | {form_calls} |",
        f"| — of which second looks answered | {rereads} |",
        f"| Total | {split_calls + form_calls} |",
        f"| Per form (reading only) | {form_calls / len(groups):.2f} |" if groups else "| Per form | — |",
        "",
        "## Fields filled (forms read without error)",
        "",
        "| Field | Forms with a value | Items |",
        "|---|---|---|",
    ]
    for name in SCALAR_FIELDS:
        filled = sum(getattr(g.commitment, name).value is not None for g in read)
        lines.append(f"| {name} | {filled} of {len(read)} | — |")
    for name in LIST_FIELDS:
        filled = sum(any(f.value is not None for f in getattr(g.commitment, name)) for g in read)
        items = sum(len(getattr(g.commitment, name)) for g in read)
        lines.append(f"| {name} | {filled} of {len(read)} | {items} |")
    lines += ["", "## Check outcomes (reason codes, over all fields)", "", "| Code | Fields |", "|---|---|"]
    lines += [f"| {code} | {count} |" for code, count in sorted(reasons.items())] or ["| — | 0 |"]
    errors = Counter(g.error for g in groups if g.error)
    lines += ["", "## Failed forms", ""]
    lines += [f"- {error}: {count}" for error, count in errors.items()] or ["None."]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--samples", type=Path, default=DEFAULT_SAMPLES_DIR)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    settings = Settings()
    ocr = create_ocr_engine(settings)
    provider = create_provider(settings)
    orchestrator = Orchestrator(settings, provider, load_form_types(settings.form_types_dir),
                                load_generic_form_type(settings.form_types_dir),
                                load_senders(settings.senders_path), ocr)
    jobs = []
    for path in sorted(args.samples.glob("*.pdf")):
        started = time.monotonic()
        job = orchestrator.run(Job(), [path])
        jobs.append((path.name, job, time.monotonic() - started))
        print(f"{path.name}: {len(job.groups)} forms, {job.status.value}")
    args.out.write_text(render(jobs, provider.name, ocr is not None), encoding="utf-8")
    print(f"-> {args.out}")


if __name__ == "__main__":
    main()

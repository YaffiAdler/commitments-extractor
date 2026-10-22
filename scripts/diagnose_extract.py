"""Read one form from ``samples/`` through the real pipeline with provider
diagnostics on (``CE_DEBUG_PROVIDER=1``), to see why its provider calls fail.

Runs ingest -> preprocess -> segment on the first pages of a sample file, then
reads one form (classify -> extract -> OCR check -> second look -> normalize ->
confidence) with the provider from ``CE_PROVIDER``. Prints the shape of every
provider call and, for the form, the error's exception chain, its reasons and
how many fields were filled. Never prints field values, answer text or the key.

Usage: python scripts/diagnose_extract.py [sample.pdf] [--pages 4] [--form 0]
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_DIR / "backend"))
os.environ.setdefault("CE_DEBUG_PROVIDER", "1")

from app.config import ProviderName, Settings  # noqa: E402
from app.ocr import create_ocr_engine  # noqa: E402
from app.pipeline import classify, ingest, preprocess, segment  # noqa: E402
from app.pipeline.calls import ProviderCalls  # noqa: E402
from app.pipeline.read_form import FormReader  # noqa: E402
from app.pipeline.normalize import load_senders  # noqa: E402
from app.providers import create_provider  # noqa: E402
from app.providers.diagnostics import exception_chain  # noqa: E402
from app.schemas import FormGroup  # noqa: E402

SAMPLES_DIR = REPO_DIR / "samples"
DEFAULT_PAGES = 4
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sample", nargs="?", type=Path, help="default: the first PDF in samples/")
    parser.add_argument("--pages", type=int, default=DEFAULT_PAGES, help="pages to segment from the start of the file")
    parser.add_argument("--form", type=int, default=0, help="index of the form to read among the segmented ones")
    return parser.parse_args()


def _filled_counts(group: FormGroup) -> tuple[int, int]:
    """(fields with a value, all fields) of the form's commitment; counts only."""
    fields = [getattr(group.commitment, name) for name in type(group.commitment).model_fields
              if hasattr(getattr(group.commitment, name), "value")]
    return sum(field.value not in (None, []) for field in fields), len(fields)


def main() -> int:
    args = _parse_args()
    logging.basicConfig(stream=sys.stdout, level=logging.INFO, format=LOG_FORMAT)
    sys.stdout.reconfigure(encoding="utf-8")
    settings = Settings()
    sample = args.sample or sorted(SAMPLES_DIR.glob("*.pdf"))[0]
    model, http_timeout_s = ((settings.gemini_model, settings.gemini_timeout_s)
                             if settings.provider is ProviderName.GEMINI
                             else (settings.claude_model, settings.claude_timeout_s))
    print(f"provider={settings.provider.value} model={model} "
          f"http_timeout_s={http_timeout_s} call_timeout_s={settings.extraction.call_timeout_s} "
          f"attempts={settings.extraction.max_attempts} debug_provider={settings.debug_provider}")
    print("proxy variables set:", sorted(k for k in os.environ if "proxy" in k.lower()) or "none")

    provider = create_provider(settings)
    ocr = create_ocr_engine(settings)
    form_types = classify.load_form_types(settings.form_types_dir)
    pages = [preprocess.preprocess(page, settings.preprocess, ocr)
             for page in ingest.load_file(sample, ingest.IngestLimits.from_settings(settings))[:args.pages]]
    result = segment.segment(pages, ocr, form_types, provider, settings.review_threshold, settings.segment)
    print(f"segmented {len(pages)} pages into {len(result.groups)} forms ({result.vlm_calls} provider calls)")
    group = result.groups[args.form]
    by_page_no = {page.page_no: page for page in pages}
    reader = FormReader(provider, form_types, classify.load_generic_form_type(settings.form_types_dir),
                        classify.SenderMatcher(load_senders(settings.senders_path)), ocr,
                        confidence_settings=settings.confidence)

    async def read() -> ProviderCalls:
        calls = ProviderCalls(asyncio.Semaphore(settings.extraction.concurrency), settings.extraction)
        await reader.read(group, [by_page_no[n] for n in group.pages], calls)
        return calls

    started = time.perf_counter()
    try:
        calls = asyncio.run(read())
    except Exception as exc:  # the point of the script: report the failure's shape
        print(f"FORM FAILED after {time.perf_counter() - started:.1f} s: {exception_chain(exc)}")
        return 1
    filled, total = _filled_counts(group)
    print(f"form read in {time.perf_counter() - started:.1f} s, {calls.count} provider calls, "
          f"{filled}/{total} fields filled, form reasons: {group.form_reasons or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

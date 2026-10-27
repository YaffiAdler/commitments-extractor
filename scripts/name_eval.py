"""Run the current pipeline on a name test set and report name accuracy.

Each page of the set (see ``app/evaluation/name_set.py`` for the folder
format) is processed as its own job by the real ``Orchestrator`` with the
configured provider (``CE_PROVIDER``; ``claude`` needs ``ANTHROPIC_API_KEY``,
``fake`` returns fixed dummy names) and OCR (Tesseract if installed). The
report (``app/evaluation/name_report.py``) holds counts and single letters
only and goes to eval/name_report.md (git-ignored) unless ``--out`` is given.
Nothing printed or logged here contains a name.

Usage: python scripts/name_eval.py SET_DIR [--out FILE] [--workers N]
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_DIR / "backend"))

from app.config import ProviderName, Settings  # noqa: E402
from app.evaluation.name_metrics import ImageOutcome, image_outcome  # noqa: E402
from app.evaluation.name_report import render  # noqa: E402
from app.evaluation.name_set import NameSample, load_name_set  # noqa: E402
from app.ocr import create_ocr_engine  # noqa: E402
from app.ocr.base import OcrEngine  # noqa: E402
from app.pipeline.classify import load_form_types, load_generic_form_type  # noqa: E402
from app.pipeline.normalize import load_senders  # noqa: E402
from app.pipeline.orchestrator import Orchestrator  # noqa: E402
from app.providers import create_provider  # noqa: E402
from app.providers.base import ExtractionProvider  # noqa: E402
from app.schemas import Job  # noqa: E402

DEFAULT_OUT = REPO_DIR / "eval" / "name_report.md"
DEFAULT_WORKERS = 1

Progress = Callable[[int, int, ImageOutcome], None]


def configuration(settings: Settings, provider: ExtractionProvider, ocr: OcrEngine | None) -> dict[str, str]:
    """The settings that shape name reading, for the report header (no secrets)."""
    config = {"provider": provider.name}
    if settings.provider is ProviderName.CLAUDE:
        config["model"] = settings.claude_model
    config["prompt"] = settings.prompt_version
    config["vlm_max_edge_px"] = str(settings.preprocess.vlm_max_edge_px)
    config["ocr"] = "tesseract" if ocr is not None else "off"
    names = settings.name_reread
    config["name_reread"] = ",".join(v.value for v in names.variants) if names.enabled else "off"
    return config


def evaluate(set_dir: Path, samples: list[NameSample], orchestrator: Orchestrator, workers: int = DEFAULT_WORKERS,
             on_done: Progress | None = None) -> list[ImageOutcome]:
    """One job per page, ``workers`` at a time; outcomes in set order. A page whose
    job fails keeps the exception type (``ImageOutcome.error``) and the run goes on."""

    def run(index_sample: tuple[int, NameSample]) -> ImageOutcome:
        index, sample = index_sample
        job = Job()
        try:
            orchestrator.run(job, [set_dir / sample.image])
        except Exception as exc:  # one bad page must not stop the run; the type is reported, never the message
            job.error = job.error or type(exc).__name__
        outcome = image_outcome(sample, job)
        if on_done is not None:
            on_done(index, len(samples), outcome)
        return outcome

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(run, enumerate(samples, 1)))


def _print_progress(index: int, total: int, outcome: ImageOutcome) -> None:
    status = outcome.error or f"{outcome.forms} form(s)"
    print(f"page {index}/{total}: {status}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("set_dir", type=Path)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                        help="pages processed at once (each page's provider calls are limited by CE_EXTRACT_CONCURRENCY)")
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be at least 1")

    samples = load_name_set(args.set_dir)
    settings = Settings()
    ocr = create_ocr_engine(settings)
    provider = create_provider(settings)
    orchestrator = Orchestrator(settings, provider, load_form_types(settings.form_types_dir),
                                load_generic_form_type(settings.form_types_dir),
                                load_senders(settings.senders_path), ocr)
    outcomes = evaluate(args.set_dir, samples, orchestrator, args.workers, _print_progress)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(outcomes, configuration(settings, provider, ocr)), encoding="utf-8")
    print(f"-> {args.out}")


if __name__ == "__main__":
    main()

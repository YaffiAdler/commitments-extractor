"""Peak memory per pipeline stage, with the demo deployment's limits.

Runs the real ``Orchestrator.run`` on the first pages of a sample file, with
the non-secret values from ``render.yaml`` (DPI, pixel and page limits,
concurrency) and ``CE_PROVIDER=fake`` (no API calls). Each stage is wrapped so
a sampler thread can record the peak resident memory (this process plus its
Tesseract child processes) while that stage runs. Prints sizes and counts
only, never pixels or field values.

The numbers come from the machine the script runs on: Windows working-set
figures differ from a Linux container, and the uvicorn server adds its own
baseline. Use them to compare stages and changes, not as the exact Render figure.

With ``--dump``, the finished job is also written as JSON, to compare the
output of two versions (it holds OCR readings of the sample: keep it in a
temporary directory and delete it afterwards).

Usage: python scripts/measure_memory.py [sample.pdf] [--pages N] [--dump job.json]
       (Tesseract: CE_TESSERACT_CMD=... TESSDATA_PREFIX=<ASCII path>)
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import psutil
import pymupdf
import yaml

REPO_DIR = Path(__file__).resolve().parents[1]
SAMPLES_DIR = REPO_DIR / "samples"
RENDER_YAML = REPO_DIR / "render.yaml"
# Not needed to measure memory: the token check and the store are not part of the pipeline.
SKIPPED_RENDER_KEYS = frozenset({"CE_REQUIRE_ACCESS_TOKEN", "CE_STORAGE", "CE_PROVIDER"})
SAMPLE_INTERVAL_S = 0.005
MB = 1024 * 1024
BASELINE = "baseline"


def _apply_render_settings() -> dict[str, str]:
    """Set the non-secret env values of ``render.yaml`` unless already set; return what applies."""
    service = yaml.safe_load(RENDER_YAML.read_text(encoding="utf-8"))["services"][0]
    applied = {}
    for var in service["envVars"]:
        if "value" in var and var["key"] not in SKIPPED_RENDER_KEYS:
            os.environ.setdefault(var["key"], str(var["value"]))
            applied[var["key"]] = os.environ[var["key"]]
    os.environ["CE_PROVIDER"] = "fake"
    return applied


_RENDER_SETTINGS = _apply_render_settings()
sys.path.insert(0, str(REPO_DIR / "backend"))

from app.config import Settings  # noqa: E402
from app.ocr import create_ocr_engine  # noqa: E402
from app.pipeline import ingest, orchestrator, preprocess, segment  # noqa: E402
from app.pipeline.classify import load_form_types, load_generic_form_type  # noqa: E402
from app.pipeline.normalize import load_senders  # noqa: E402
from app.providers import create_provider  # noqa: E402
from app.schemas import Job  # noqa: E402
from app.storage.page_images import PageImageStore  # noqa: E402
from app.storage.tmp import TempStorage  # noqa: E402


def _rss_bytes(process: psutil.Process) -> int:
    total = process.memory_info().rss
    for child in process.children(recursive=True):
        try:
            total += child.memory_info().rss
        except psutil.Error:  # the child ended between listing and reading
            pass
    return total


@dataclass
class _StageStats:
    calls: int = 0
    seconds: float = 0.0
    peak: int = 0
    after: int = 0


@dataclass
class MemoryRecorder:
    """Samples RSS on a thread; the peak of each sample goes to the stage that is current."""

    process: psutil.Process = field(default_factory=psutil.Process)
    stages: dict[str, _StageStats] = field(default_factory=dict)
    current: str = BASELINE
    _stop: threading.Event = field(default_factory=threading.Event)

    def start(self) -> None:
        self.stages[BASELINE] = _StageStats(after=_rss_bytes(self.process))
        threading.Thread(target=self._sample, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _sample(self) -> None:
        while not self._stop.is_set():
            stats = self.stages.setdefault(self.current, _StageStats())
            stats.peak = max(stats.peak, _rss_bytes(self.process))
            time.sleep(SAMPLE_INTERVAL_S)

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        previous = self.current
        self.current = name
        stats = self.stages.setdefault(name, _StageStats())
        stats.peak = max(stats.peak, _rss_bytes(self.process))
        started = time.perf_counter()
        try:
            yield
        finally:
            stats.calls += 1
            stats.seconds += time.perf_counter() - started
            stats.after = _rss_bytes(self.process)
            self.current = previous


def _wrap(owner: Any, name: str, recorder: MemoryRecorder, stage: str,
          on_result: Callable[[Any], None] | None = None) -> None:
    original = getattr(owner, name)

    def wrapped(*args: Any, **kwargs: Any) -> Any:
        with recorder.stage(stage):
            result = original(*args, **kwargs)
        if on_result is not None:
            on_result(result)
        return result

    setattr(owner, name, wrapped)


@dataclass
class _PageSizes:
    """Bytes of the arrays each preprocessed page keeps, and the image shapes."""

    original: int = 0
    image: int = 0
    ocr_image: int = 0
    shapes: list[tuple[tuple[int, ...], tuple[int, ...]]] = field(default_factory=list)

    def add(self, page: ingest.PageImage) -> None:
        self.original += page.original.nbytes
        self.image += page.image.nbytes
        self.ocr_image += page.ocr_image.nbytes if isinstance(page.ocr_image, np.ndarray) else 0
        self.shapes.append((page.original.shape, page.image.shape))


def _first_pages(sample: Path, pages: int, out_dir: Path) -> Path:
    """A copy of the first ``pages`` pages (embedded scans are copied unchanged)."""
    target = out_dir / "measure.pdf"
    with pymupdf.open(sample) as source, pymupdf.open() as copy:
        copy.insert_pdf(source, from_page=0, to_page=min(pages, source.page_count) - 1)
        copy.save(target)
    return target


def _parse_args(default_pages: int) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sample", nargs="?", type=Path, help="default: the first PDF in samples/")
    parser.add_argument("--pages", type=int, default=default_pages, help="default: CE_MAX_JOB_PAGES")
    parser.add_argument("--dump", type=Path, help="write the finished job as JSON to this path")
    return parser.parse_args()


def _print_report(recorder: MemoryRecorder, sizes: _PageSizes, total_peak: int) -> None:
    baseline = recorder.stages[BASELINE].after
    print(f"\n{'stage':<12}{'calls':>6}{'seconds':>9}{'peak MB':>10}{'+ baseline':>12}{'after MB':>10}")
    for name, stats in recorder.stages.items():
        if name == BASELINE:
            print(f"{name:<12}{'':>6}{'':>9}{baseline / MB:>10.0f}{'':>12}{baseline / MB:>10.0f}")
            continue
        print(f"{name:<12}{stats.calls:>6}{stats.seconds:>9.1f}{stats.peak / MB:>10.0f}"
              f"{(stats.peak - baseline) / MB:>+12.0f}{stats.after / MB:>10.0f}")
    print(f"\nwhole run: peak {total_peak / MB:.0f} MB (baseline {baseline / MB:.0f} MB)")
    print(f"arrays kept per preprocessed page, all pages: original {sizes.original / MB:.0f} MB, "
          f"image {sizes.image / MB:.0f} MB, ocr_image {sizes.ocr_image / MB:.0f} MB")
    for page_no, (original, image) in enumerate(sizes.shapes):
        print(f"  page {page_no}: original {original[1]}x{original[0]}, model image {image[1]}x{image[0]}")


def main() -> int:
    settings = Settings()
    args = _parse_args(settings.max_job_pages)
    sys.stdout.reconfigure(encoding="utf-8")
    sample = args.sample or sorted(SAMPLES_DIR.glob("*.pdf"))[0]
    print("render.yaml settings:", ", ".join(f"{k}={v}" for k, v in _RENDER_SETTINGS.items()
                                              if k.startswith(("CE_RENDER", "CE_MAX", "CE_EXTRACT"))))
    ocr = create_ocr_engine(settings)
    print(f"provider=fake ocr={'tesseract' if ocr else 'none'} pages={args.pages}")

    recorder = MemoryRecorder()
    sizes = _PageSizes()
    # One page is decoded per call, whether ingest returns a list or yields the pages.
    _wrap(ingest, "_pdf_page", recorder, "ingest")
    _wrap(ingest, "_image_page", recorder, "ingest")
    _wrap(orchestrator.preprocess, "preprocess", recorder, "preprocess", sizes.add)
    _wrap(PageImageStore, "save", recorder, "save_pages")
    _wrap(orchestrator.segment, "segment", recorder, "segment")
    read_forms = orchestrator.Orchestrator._read_forms

    # classify + extract + OCR check + second look + normalize + confidence, all forms.
    async def recorded_read_forms(self: Any, *a: Any, **kw: Any) -> None:
        with recorder.stage("extract"):
            await read_forms(self, *a, **kw)

    orchestrator.Orchestrator._read_forms = recorded_read_forms

    with tempfile.TemporaryDirectory() as tmp:
        storage = TempStorage(Path(tmp) / "jobs")
        pipeline = orchestrator.Orchestrator(
            settings=settings, provider=create_provider(settings),
            form_types=load_form_types(settings.form_types_dir),
            generic_form_type=load_generic_form_type(settings.form_types_dir),
            senders=load_senders(settings.senders_path), ocr=ocr, page_images=PageImageStore(storage))
        job = Job()
        path = storage.save_upload(job.id, _first_pages(sample, args.pages, Path(tmp)).read_bytes())
        recorder.start()
        total_peak = 0
        try:
            with recorder.stage("run"):
                pipeline.run(job, [path])
        finally:
            recorder.stop()
            total_peak = max(stats.peak for stats in recorder.stages.values())
            storage.delete_job(job.id)
    print(f"job {job.status.value}: {len(job.groups)} forms, error type {job.error or 'none'}")
    if args.dump is not None:
        args.dump.write_text(job.model_dump_json(indent=1), encoding="utf-8")
    _print_report(recorder, sizes, total_peak)
    return 0


if __name__ == "__main__":
    sys.exit(main())

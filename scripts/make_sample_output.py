"""Write docs/sample_output.xlsx from the ``fake`` provider's dummy data only.

Runs the real pipeline (``Orchestrator``, no OCR) on blank one-page PDFs,
each with its own ``FakeProvider`` reading, so the file shows the export's
cases: a complete row, several central numbers, a handwritten date, an empty
ID, an illegible number. Every value is invented. Temporary files are written
to a temp directory and deleted.

Usage: python scripts/make_sample_output.py [out_file] [--columns]
  --columns  one column per central / commitment number instead of "; "
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

import pymupdf

REPO_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_DIR / "backend"))

from app.config import ExportSettings, MultiValueMode, ProviderName, Settings  # noqa: E402
from app.export.xlsx import write_xlsx  # noqa: E402
from app.pipeline.classify import load_form_types, load_generic_form_type  # noqa: E402
from app.pipeline.normalize import load_senders  # noqa: E402
from app.pipeline.orchestrator import Orchestrator  # noqa: E402
from app.providers.fake import FakeProvider, default_reading  # noqa: E402
from app.schemas import FieldReading, FormReading, Job  # noqa: E402

DEFAULT_OUT = REPO_DIR / "docs" / "sample_output.xlsx"
RENDER_DPI = 72  # the pages are blank; nothing is read from them


def _readings() -> list[FormReading]:
    complete = default_reading()
    several = default_reading()
    several.first_name = FieldReading(value="שרה", source="handwritten")
    several.last_name = FieldReading(value="לוי", source="handwritten")
    several.central_numbers = [FieldReading(value="0234567", source="sticker"),
                               FieldReading(value="0234568", source="sticker")]
    several.received_date = FieldReading(value="9.3.25", source="handwritten")
    several.commitment_numbers = [FieldReading(value="0007654321"), FieldReading(value="0007654322")]
    gaps = default_reading()
    gaps.id_number = FieldReading()
    gaps.central_numbers = [FieldReading(value="345678", source="sticker")]
    gaps.commitment_numbers = [FieldReading(value="00012", legible=False)]
    return [complete, several, gaps]


def _blank_pdf(path: Path) -> Path:
    with pymupdf.open() as doc:
        doc.new_page()
        doc.save(path)
    return path


def build_job(tmp_dir: Path) -> Job:
    settings = Settings(provider=ProviderName.FAKE, render_dpi=RENDER_DPI, tmp_dir=tmp_dir)
    job = Job()
    for index, reading in enumerate(_readings(), start=1):
        orchestrator = Orchestrator(
            settings=settings,
            provider=FakeProvider(reading),
            form_types=load_form_types(settings.form_types_dir),
            generic_form_type=load_generic_form_type(settings.form_types_dir),
            senders=load_senders(settings.senders_path),
            ocr=None,
        )
        part = orchestrator.run(Job(), [_blank_pdf(tmp_dir / f"sample_{index}.pdf")])
        job.groups.extend(part.groups)
        job.files.extend(part.files)
    return job


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", nargs="?", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--columns", action="store_true", help="one column per number")
    args = parser.parse_args()
    mode = MultiValueMode.COLUMNS if args.columns else MultiValueMode.JOINED
    with tempfile.TemporaryDirectory() as tmp:
        job = build_job(Path(tmp))
    write_xlsx(job, args.out, ExportSettings(multi_values=mode))
    print(f"{len(job.groups)} rows -> {args.out}")


if __name__ == "__main__":
    main()

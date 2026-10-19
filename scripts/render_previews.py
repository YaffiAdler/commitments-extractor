"""Render every page of every PDF in samples/ to PNG for manual review.

Usage:  python scripts/render_previews.py [out_dir] [--dpi 150]
Previews contain patient data — write them to a temp dir and delete afterwards.
"""
import argparse
import pathlib
import tempfile

import pymupdf

ROOT = pathlib.Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("out_dir", nargs="?", default=str(pathlib.Path(tempfile.gettempdir()) / "preview"))
    parser.add_argument("--dpi", type=int, default=150)
    args = parser.parse_args()

    out = pathlib.Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for file_no, pdf in enumerate(sorted((ROOT / "samples").glob("*.pdf")), 1):
        with pymupdf.open(pdf) as doc:
            for page_no, page in enumerate(doc, 1):
                page.get_pixmap(dpi=args.dpi).save(out / f"p{file_no}_{page_no:02d}.png")
            print(f"file {file_no}: {doc.page_count} pages")
    print(f"written to {out}")


if __name__ == "__main__":
    main()

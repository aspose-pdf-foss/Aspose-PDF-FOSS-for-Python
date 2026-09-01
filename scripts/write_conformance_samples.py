#!/usr/bin/env python3
"""Write one sample PDF per conformance level, for an external validator.

The PDF/A and PDF/UA checks in this library are heuristic by design and say so;
the documentation points users at veraPDF for a real verdict. This script
produces the files to point it at, so that verdict can be part of CI rather
than something someone runs by hand once.

Usage::

    python scripts/write_conformance_samples.py <output directory>
    verapdf --format mrr --recurse <output directory>

Each sample is an ordinary document — text at two sizes, an outline, a form
field, an attachment — converted to the level named in its filename.
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

_PDFA_LEVELS = ("1b", "2b", "2u", "3b", "4", "4e", "4f")
_PDFUA_PARTS = (1, 2)


def _document():
    from aspose_pdf import Document
    from aspose_pdf.outlines import OutlineItem

    document = Document()
    page = document.pages.add()
    page.add_text("Sample heading", 60, 720, font_size=20)
    page.add_text("Body text for a conformance sample.", 60, 700, font_size=11)
    document.outlines.add(OutlineItem("Chapter one", 0))
    document.form.add_text_field("nickname", 0, (60, 100, 260, 130), value="typed")
    document.add_attachment("notes.txt", b"an attachment", mime="text/plain")
    document.info["Title"] = "Conformance sample"
    buffer = io.BytesIO()
    document.save(buffer)
    return Document(io.BytesIO(buffer.getvalue()))


def write_samples(directory: Path) -> list[Path]:
    """Write every sample into *directory*, returning the paths written."""
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    for level in _PDFA_LEVELS:
        document = _document()
        document.convert_to_pdfa(level)
        target = directory / f"pdfa-{level}.pdf"
        document.save(str(target))
        written.append(target)

    for part in _PDFUA_PARTS:
        document = _document()
        document.convert_to_pdfua(part=part, title="Conformance sample", auto_tag=True)
        target = directory / f"pdfua-{part}.pdf"
        document.save(str(target))
        written.append(target)

    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="where to write the samples")
    args = parser.parse_args()

    for path in write_samples(args.directory):
        print(f"wrote {path} ({path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

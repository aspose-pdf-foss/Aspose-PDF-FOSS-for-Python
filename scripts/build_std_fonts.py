#!/usr/bin/env python3
"""Regenerate the bundled Standard-14 substitute font programs.

The page renderer fills glyph outlines for the PDF Standard-14 fonts
(Helvetica/Times/Courier families) from metric-compatible open fonts, because
those fonts are never embedded in a PDF. We bundle the **Liberation** fonts
(SIL Open Font License 1.1), which are metric-compatible with
Arial/Times New Roman/Courier New -- themselves metric-compatible with
Helvetica/Times/Courier -- so glyph advances match the Standard-14 metrics.

To keep the wheel small the fonts are subset to a Latin-focused Unicode set
(Basic Latin, Latin-1, Latin Extended-A, plus the punctuation/symbols used by
the WinAnsi/MacRoman/Standard encodings) and stored zlib-compressed. The page
renderer decompresses them lazily with the stdlib ``zlib`` module -- no runtime
dependency on fontTools or Brotli.

Usage::

    python scripts/build_std_fonts.py --src /path/to/liberation/ttf

``--src`` defaults to ``$LIBERATION_SRC`` then to the LibreOffice bundle. The
source directory must contain the canonical ``Liberation{Sans,Serif,Mono}-*``
``.ttf`` files. fontTools is required only to run this script, not at runtime.
"""

from __future__ import annotations

import argparse
import os
import sys
import zlib
from pathlib import Path

# bundled key -> source Liberation face
FACES = {
    "sans-regular": "LiberationSans-Regular.ttf",
    "sans-bold": "LiberationSans-Bold.ttf",
    "sans-italic": "LiberationSans-Italic.ttf",
    "sans-bolditalic": "LiberationSans-BoldItalic.ttf",
    "serif-regular": "LiberationSerif-Regular.ttf",
    "serif-bold": "LiberationSerif-Bold.ttf",
    "serif-italic": "LiberationSerif-Italic.ttf",
    "serif-bolditalic": "LiberationSerif-BoldItalic.ttf",
    "mono-regular": "LiberationMono-Regular.ttf",
    "mono-bold": "LiberationMono-Bold.ttf",
    "mono-italic": "LiberationMono-Italic.ttf",
    "mono-bolditalic": "LiberationMono-BoldItalic.ttf",
}

# Glyph coverage: every code the WinAnsi/MacRoman/Standard encodings can map,
# plus common Latin. Anything outside degrades gracefully to a glyph box.
_RANGES = [(0x20, 0x7E), (0xA0, 0xFF), (0x100, 0x17F)]
_EXTRA = [
    0x192, 0x2C6, 0x2C7, 0x2C9, 0x2D8, 0x2D9, 0x2DA, 0x2DB, 0x2DC, 0x2DD,
    0x2013, 0x2014, 0x2018, 0x2019, 0x201A, 0x201C, 0x201D, 0x201E,
    0x2020, 0x2021, 0x2022, 0x2026, 0x2030, 0x2039, 0x203A,
    0x20AC, 0x2122, 0x2202, 0x2206, 0x2211, 0x2212, 0x221A, 0x2260, 0x2264,
    0x2265, 0x25CA, 0xFB01, 0xFB02,
]

_DEFAULT_SRC = (
    "/Users/sergey/.cache/codex-runtimes/codex-primary-runtime/dependencies/"
    "native/libreoffice-headless/libreoffice/LibreOfficeDev.app/Contents/"
    "Resources/fonts/truetype"
)

_OUT_DIR = Path(__file__).resolve().parent.parent / "src/aspose_pdf/engine/data/fonts"


def _unicodes() -> list[int]:
    unis = set(_EXTRA)
    for lo, hi in _RANGES:
        unis.update(range(lo, hi + 1))
    return sorted(unis)


def _subset(src_path: Path) -> bytes:
    import io

    from fontTools import subset
    from fontTools.ttLib import TTFont

    opts = subset.Options(
        glyph_names=False,
        recalc_bounds=False,
        recalc_timestamp=False,
        drop_tables=["GPOS", "GSUB", "GDEF", "GSUB", "DSIG", "kern", "FFTM"],
    )
    opts.notdef_outline = True
    opts.name_IDs = []  # name table only bloats a substitute; identity is in this repo
    font = TTFont(str(src_path))
    sub = subset.Subsetter(options=opts)
    sub.populate(unicodes=_unicodes())
    sub.subset(font)
    buf = io.BytesIO()
    font.save(buf)
    return buf.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--src",
        default=os.environ.get("LIBERATION_SRC", _DEFAULT_SRC),
        help="directory containing the Liberation*.ttf source faces",
    )
    args = parser.parse_args()
    src_dir = Path(args.src)
    if not src_dir.is_dir():
        print(f"source directory not found: {src_dir}", file=sys.stderr)
        return 2

    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    total = 0
    for key, face in FACES.items():
        src_path = src_dir / face
        if not src_path.is_file():
            print(f"missing source face: {src_path}", file=sys.stderr)
            return 2
        data = zlib.compress(_subset(src_path), 9)
        out_path = _OUT_DIR / f"{key}.ttf.zlib"
        out_path.write_bytes(data)
        total += len(data)
        print(f"  {key:18s} <- {face:32s} {len(data):>7d} bytes")
    print(f"wrote {len(FACES)} fonts, {total} bytes total -> {_OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

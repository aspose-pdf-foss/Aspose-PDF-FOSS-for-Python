#!/usr/bin/env python3
"""Build the bundled Adobe Glyph List (glyph name -> Unicode) table.

The source is the Adobe Glyph List (``glyphlist.txt``), as vendored by fontTools
in ``fontTools.agl.LEGACY_AGL2UV`` (4281 names), plus ReportLab's transcription
of MacExpertEncoding (PDF 32000-1 Annex D.4), which fontTools does not carry.
Both are build-time dependencies only: the runtime library never imports them
and loads only the generated blob below. The output is deterministic and no
network is used.

Usage::

    python scripts/build_agl_data.py              # (re)write the bundle
    python scripts/build_agl_data.py --check      # verify the bundle is current
"""

from __future__ import annotations

import argparse
import hashlib
import json
import zlib
from pathlib import Path

_OUTPUT = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "aspose_pdf"
    / "engine"
    / "data"
    / "agl"
    / "glyphlist.json.zlib"
)


def _win_ansi_names() -> list[str]:
    """Build the WinAnsiEncoding code->name table (PDF 32000-1, Annex D)."""
    from fontTools.agl import UV2AGL

    # The five codes WinAnsi leaves undefined, and the handful whose canonical
    # PDF names are not in the small AGLFN reverse map.
    overrides = {
        0x81: "", 0x8D: "", 0x8F: "", 0x90: "", 0x9D: "",
        0xA0: "space", 0xAD: "hyphen",
        0xB2: "twosuperior", 0xB3: "threesuperior", 0xB9: "onesuperior",
    }
    names = [""] * 256
    for code in range(256):
        if code in overrides:
            names[code] = overrides[code]
            continue
        try:
            char = bytes([code]).decode("cp1252")
        except UnicodeDecodeError:
            continue
        names[code] = UV2AGL.get(ord(char), "")
    return names


def _clean(names: list[str]) -> list[str]:
    return [
        "" if not name or name == ".notdef" else str(name) for name in names
    ]


def build_bundle() -> bytes:
    """Return the deterministic zlib-compressed glyph-name and encoding bundle."""
    from fontTools.agl import LEGACY_AGL2UV
    from fontTools.cffLib import (
        cffExpertSubsetStrings,
        cffIExpertStrings,
        cffStandardStrings,
    )
    from fontTools.encodings.MacRoman import MacRoman
    from fontTools.encodings.StandardEncoding import StandardEncoding
    from reportlab.pdfbase._fontdata_enc_macexpert import MacExpertEncoding

    def codepoints(value: object) -> list[int]:
        # A few AGL names map to a sequence of scalars, not a single one.
        seq = value if isinstance(value, (list, tuple)) else [value]
        return [int(scalar) for scalar in seq]

    glyphs = {str(name): codepoints(value) for name, value in LEGACY_AGL2UV.items()}
    payload = {
        "format": 1,
        "glyphs": glyphs,
        # Predefined base encodings as code -> glyph name (256 entries each).
        "encodings": {
            "StandardEncoding": _clean(list(StandardEncoding)),
            "WinAnsiEncoding": _clean(_win_ansi_names()),
            "MacRomanEncoding": _clean(list(MacRoman)),
            "MacExpertEncoding": _clean(list(MacExpertEncoding)),
        },
        # The 391 predefined CFF strings (SID -> name) for charset resolution.
        "cff_standard_strings": list(cffStandardStrings),
        # The two predefined CFF charsets, as glyph id -> name. Unlike the
        # ISOAdobe charset (glyph id == SID) these are arbitrary orderings, so
        # they have to be carried rather than computed.
        "cff_predefined_charsets": {
            "Expert": list(cffIExpertStrings),
            "ExpertSubset": list(cffExpertSubsetStrings),
        },
    }
    raw = json.dumps(
        payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("ascii")
    return zlib.compress(raw, 9)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=_OUTPUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the checked-in bundle matches a fresh build",
    )
    args = parser.parse_args()

    compressed = build_bundle()
    digest = hashlib.sha256(compressed).hexdigest()

    if args.check:
        current = args.output.read_bytes()
        if current != compressed:
            raise SystemExit(
                "AGL bundle is stale; re-run scripts/build_agl_data.py"
            )
        print(f"AGL bundle OK: {len(compressed)} bytes, SHA-256 {digest}")
        return

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(compressed)
    print(f"wrote {args.output} ({len(compressed)} bytes), SHA-256 {digest}")


if __name__ == "__main__":
    main()

"""Adobe Glyph List resolver, encoding tables, and bundle integrity."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from aspose_pdf.engine.agl import (
    base_encoding_table,
    cff_standard_strings,
    glyph_name_to_unicode,
)


def test_agl_named_glyphs() -> None:
    assert glyph_name_to_unicode("aacute") == "á"
    assert glyph_name_to_unicode("Euro") == "€"
    assert glyph_name_to_unicode("afii10017") == "А"  # Cyrillic A
    assert glyph_name_to_unicode("bullet") == "•"
    assert glyph_name_to_unicode("Lcommaaccent") == "Ļ"


def test_agl_ligature_and_variant_components() -> None:
    assert glyph_name_to_unicode("f_f_i") == "ffi"
    assert glyph_name_to_unicode("A.sc") == "A"
    assert glyph_name_to_unicode("fi") == "ﬁ"  # a real AGL ligature name


def test_agl_algorithmic_uni_forms() -> None:
    assert glyph_name_to_unicode("uni0041") == "A"
    assert glyph_name_to_unicode("uni00410042") == "AB"
    assert glyph_name_to_unicode("u1F600") == "\U0001f600"


def test_agl_unresolved_and_bad_input() -> None:
    assert glyph_name_to_unicode(".notdef") is None
    assert glyph_name_to_unicode("nonexistentglyphname") is None
    assert glyph_name_to_unicode("") is None
    assert glyph_name_to_unicode("x" * 200) is None  # bounded name length
    assert glyph_name_to_unicode("uniD800") is None  # surrogate rejected


def test_base_encoding_tables() -> None:
    standard = base_encoding_table("StandardEncoding")
    win = base_encoding_table("WinAnsiEncoding")
    mac = base_encoding_table("MacRomanEncoding")
    assert standard is not None and len(standard) == 256
    assert standard[65] == "A"
    assert win[0x80] == "Euro" and win[0xA0] == "space" and win[0x95] == "bullet"
    assert win[0x81] == ""  # undefined WinAnsi code
    assert mac[65] == "A"
    assert base_encoding_table("ExpertEncoding") is None  # not bundled
    assert base_encoding_table("bogus") is None


def test_cff_standard_strings() -> None:
    strings = cff_standard_strings()
    assert len(strings) == 391
    assert strings[0] == ".notdef"
    assert strings[1] == "space"


def test_bundle_is_deterministic() -> None:
    """The checked-in bundle matches a fresh build (SHA-verified generator)."""
    script = Path(__file__).resolve().parents[1] / "scripts" / "build_agl_data.py"
    result = subprocess.run(
        [sys.executable, str(script), "--check"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr + result.stdout

"""Adobe Glyph List resolver, encoding tables, and bundle integrity."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from aspose_pdf.engine.agl import (
    base_encoding_table,
    cff_predefined_charset,
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
    # PDF 32000-1 Table 114 allows exactly four names; anything else is malformed.
    assert base_encoding_table("ExpertEncoding") is None  # a Type 1 name, not PDF's
    assert base_encoding_table("bogus") is None


def test_macexpert_encoding_table() -> None:
    mac_expert = base_encoding_table("MacExpertEncoding")
    assert mac_expert is not None and len(mac_expert) == 256
    assert mac_expert[0x21] == "exclamsmall"
    assert mac_expert[0x30] == "zerooldstyle"
    assert mac_expert[0x41] == ""  # undefined -- *not* Latin "A"
    # Every name it defines belongs to the Adobe Expert glyph repertoire, and
    # resolves to a scalar, so an Expert-encoded font can also extract as text.
    expert = set(cff_predefined_charset(1))
    defined = [name for name in mac_expert if name]
    assert len(defined) == 165
    assert all(name in expert for name in defined)
    assert all(glyph_name_to_unicode(name) is not None for name in defined)


def test_cff_predefined_charsets() -> None:
    expert, subset = cff_predefined_charset(1), cff_predefined_charset(2)
    assert len(expert) == 166 and len(subset) == 87
    assert expert[0] == ".notdef" and expert[2] == "exclamsmall"
    assert subset[0] == ".notdef"
    # 0 is ISOAdobe, which needs no table (glyph id is the SID); 3+ do not exist.
    assert cff_predefined_charset(0) is None
    assert cff_predefined_charset(3) is None


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

"""MacRomanEncoding is a subset of Mac OS Roman, and the difference matters.

PDF 32000-1 Annex D.2 defines MacRomanEncoding over 208 codes. The Mac OS Roman
character set names all 256, and the bundled table used to be that one — so 48
codes carried a name the specification says belong to the *font's own* encoding,
and one code (0xCA) carried ``nbspace`` where the table shows ``space``.

Imposing a name for those codes is not a harmless extra: once a name resolves,
nothing looks at the font's encoding again. A font whose own encoding puts
something else at 0xB0 had the glyph named ``infinity`` kept and the glyph it
actually shows there erased.

The 48 names are still useful — a Mac-produced font usually does put
``infinity`` at 0xB0 — so they are kept as a *supplement* consulted after the
font's encoding rather than before it. Twelve of the original names were ASCII
control mnemonics (``CR``, ``DEL``) that are not glyph names at all; they are
dropped, which is checkable: a name the Adobe Glyph List does not know cannot be
a glyph a viewer would draw.
"""

from __future__ import annotations

import io

import pytest

pytest.importorskip("fontTools")

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.t2CharStringPen import T2CharStringPen
from fontTools.ttLib import TTFont

from aspose_pdf import OptimizationOptions
from aspose_pdf.engine.agl import (
    base_encoding_table,
    glyph_name_to_unicode,
    mac_os_roman_supplement,
)
from aspose_pdf.engine.cos import PdfDictionary, PdfName, PdfNumber, PdfStream
from aspose_pdf.engine.simple_pdf import SimplePdf

# The font carries a glyph called "infinity" *and* puts something else at 0xB0
# in its own encoding -- exactly the collision the precedence decides.
_ORDER = [".notdef", "A", "infinity", "B"]


# ---------------------------------------------------------------------------
# The tables
# ---------------------------------------------------------------------------


def test_the_base_table_is_the_one_pdf_defines():
    table = base_encoding_table("MacRomanEncoding")
    assert sum(1 for name in table if name) == 208
    assert table[0x41] == "A"
    # Codes PDF leaves to the font, which the Mac OS Roman set names.
    assert table[0xB0] == "" and table[0xF0] == "" and table[0x0D] == ""


def test_the_non_breaking_space_is_named_space():
    # Annex D.2 shows "space" at both 0x20 and 0xCA; the glyph is the same one,
    # and few fonts define anything called "nbspace".
    table = base_encoding_table("MacRomanEncoding")
    assert table[0x20] == "space"
    assert table[0xCA] == "space"


def test_the_supplement_covers_what_the_base_table_leaves_out():
    table = base_encoding_table("MacRomanEncoding")
    supplement = mac_os_roman_supplement()

    assert supplement[0xB0] == "infinity"
    assert supplement[0xF0] == "apple"
    # It supplements; it never disagrees.
    assert not [code for code in supplement if table[code]]


def test_the_supplement_holds_only_real_glyph_names():
    # "CR", "DEL" and friends were in the Mac OS Roman table and are not glyphs.
    supplement = mac_os_roman_supplement()
    assert len(supplement) == 36
    assert all(glyph_name_to_unicode(name) is not None for name in supplement.values())
    assert 0x0D not in supplement  # was "CR"
    assert 0x7F not in supplement  # was "DEL"


# ---------------------------------------------------------------------------
# What the precedence decides
# ---------------------------------------------------------------------------


def _font_with_builtin_encoding(order: list[str] | None = None) -> bytes:
    """A CFF whose own encoding puts "B" — not "infinity" — at 0xB0."""
    order = order or _ORDER
    charstrings = {}
    for index, name in enumerate(order):
        pen = T2CharStringPen(600, None)
        if name != ".notdef":
            pen.moveTo((0, 0))
            for step in range(25):  # big enough that erasing it shrinks the font
                pen.lineTo((100 + index * 10 - step, step))
                pen.lineTo((400, 400))
            pen.closePath()
        charstrings[name] = pen.getCharString()
    builder = FontBuilder(unitsPerEm=1000, isTTF=False)
    builder.setupGlyphOrder(order)
    builder.setupCharacterMap({0x41: "A", 0x42: "B"})
    builder.setupCFF("MacTest", {}, charstrings, {})
    builder.setupHorizontalMetrics({name: (600, 0) for name in order})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": "MacTest", "styleName": "Regular"})
    builder.setupOS2()
    builder.setupPost()
    table = builder.font["CFF "]
    encoding = [".notdef"] * 256
    encoding[0xB0] = "B"  # the font says 0xB0 is "B"
    table.cff[table.cff.fontNames[0]].Encoding = encoding
    buffer = io.BytesIO()
    builder.save(buffer)
    return TTFont(buffer)["CFF "].compile(TTFont(buffer))


def _embed(program: bytes, shown_codes: list[int], *, encoding: str | None):
    content = "BT /F0 12 Tf <" + "".join(f"{c:02x}" for c in shown_codes) + "> Tj ET"
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 200, 200)]
    pdf.page_contents = [content.encode("latin-1")]
    pdf._ensure_cos()
    cos = pdf._cos_doc
    font_file = cos.register_object(
        PdfStream(
            program,
            {
                PdfName("Length"): PdfNumber(len(program)),
                PdfName("Subtype"): PdfName("Type1C"),
            },
        )
    )
    descriptor = cos.register_object(
        PdfDictionary(
            {
                PdfName("Type"): PdfName("FontDescriptor"),
                PdfName("FontName"): PdfName("AAAAAA+MacTest"),
                PdfName("FontFile3"): font_file,
            }
        )
    )
    field: dict[PdfName, object] = {
        PdfName("Type"): PdfName("Font"),
        PdfName("Subtype"): PdfName("Type1"),
        PdfName("BaseFont"): PdfName("AAAAAA+MacTest"),
        PdfName("FontDescriptor"): descriptor,
    }
    if encoding is not None:
        field[PdfName("Encoding")] = PdfName(encoding)
    font_obj = cos.register_object(PdfDictionary(field))
    pdf._get_page_dict(0).mapping[PdfName("Resources")] = PdfDictionary(
        {PdfName("Font"): PdfDictionary({PdfName("F0"): font_obj})}
    )
    return pdf, font_file.object_number


def _charstrings(cff_bytes: bytes):
    from fontTools.cffLib import CFFFontSet

    font_set = CFFFontSet()
    font_set.decompile(io.BytesIO(cff_bytes), None)
    return font_set[font_set.fontNames[0]].CharStrings


def _font_with_apple() -> bytes:
    """Like the other font, but with an "apple" glyph and no encoding for it."""
    return _font_with_builtin_encoding(order=[".notdef", "A", "apple", "B"])


def _subset(
    shown_codes: list[int],
    *,
    encoding: str | None = "MacRomanEncoding",
    program: bytes | None = None,
):
    program = program if program is not None else _font_with_builtin_encoding()
    pdf, font_num = _embed(program, shown_codes, encoding=encoding)
    original = pdf._cos_doc.objects[font_num].content
    pdf.optimize(
        OptimizationOptions(
            remove_unused_objects=False,
            remove_unused_streams=False,
            link_duplicate_streams=False,
            remove_duplicate_images=False,
            compress_fonts=False,
            subset_fonts=True,
        )
    )
    return original, pdf._cos_doc.objects[font_num].content


def test_the_fonts_own_encoding_decides_a_code_pdf_leaves_undefined():
    """0xB0 is the font's "B", not the "infinity" Mac OS Roman would name.

    Reading the supplement first kept ``infinity`` and erased ``B`` — the glyph
    the page actually shows.
    """
    original, subset = _subset([0xB0])

    assert len(subset) < len(original)
    kept = _charstrings(subset)
    assert kept["B"].bytecode != b"\x0e"  # the glyph shown, kept
    assert kept["infinity"].bytecode == b"\x0e"  # the glyph named, erased


def test_a_code_the_base_table_defines_is_unaffected():
    original, subset = _subset([0x41])  # "A", which Annex D.2 defines
    assert len(subset) < len(original)
    kept = _charstrings(subset)
    assert kept["A"].bytecode != b"\x0e"
    assert kept["B"].bytecode == b"\x0e"


def test_the_supplement_still_resolves_when_the_font_says_nothing():
    """The 48 names are kept because they are usually right, just not first."""
    program = _font_with_builtin_encoding()
    pdf, _ = _embed(program, [0xF0], encoding="MacRomanEncoding")
    # 0xF0 is "apple" in Mac OS Roman and absent from this font's own encoding,
    # so nothing resolves it and the font is kept whole rather than mis-subset.
    names = pdf._simple_encoding_names(
        pdf._resolve(
            pdf._resolve(
                pdf._resolve(
                    pdf._get_page_dict(0).mapping[PdfName("Resources")]
                ).mapping[PdfName("Font")]
            ).mapping[PdfName("F0")]
        )
    )
    base_map, _diff_map, late_map = names
    assert 0xF0 not in base_map
    assert late_map[0xF0] == "apple"


def test_another_base_encoding_gets_no_supplement():
    program = _font_with_builtin_encoding()
    pdf, _ = _embed(program, [0x41], encoding="WinAnsiEncoding")
    font_dict = pdf._resolve(
        pdf._resolve(
            pdf._resolve(
                pdf._get_page_dict(0).mapping[PdfName("Resources")]
            ).mapping[PdfName("Font")]
        ).mapping[PdfName("F0")]
    )
    _base, _diff, late_map = pdf._simple_encoding_names(font_dict)
    assert late_map == {}


def test_the_supplement_resolves_a_code_the_fonts_encoding_skips():
    """The fallback earns its place: 0xF0 is "apple" when nothing else says.

    The font here does carry an ``apple`` glyph but leaves 0xF0 out of its own
    encoding, which is where the supplement -- and only the supplement -- can
    still resolve the code instead of the whole font being kept.
    """
    original, subset = _subset([0xF0], program=_font_with_apple())

    assert len(subset) < len(original)
    kept = _charstrings(subset)
    assert kept["apple"].bytecode != b"\x0e"
    assert kept["A"].bytecode == b"\x0e"


# ---------------------------------------------------------------------------
# A Type 1 font resolves its codes in the same order
# ---------------------------------------------------------------------------


def _type1_with_builtin_encoding() -> tuple[bytes, int, int]:
    """A Type 1 program whose own encoding puts "B" at 0xB0."""
    from aspose_pdf.engine.font_subset_type1 import _encrypt, _encrypt_charstring
    from aspose_pdf.engine.type1_outlines import _EEXEC_R

    def number(value: int) -> bytes:
        return bytes([value + 139]) if -107 <= value <= 107 else (
            bytes([255]) + value.to_bytes(4, "big", signed=True)
        )

    def box(segments: int) -> bytes:
        cs = number(0) + number(700) + bytes([13])  # hsbw
        cs += number(100) + number(100) + bytes([21])  # rmoveto
        for _ in range(segments):
            cs += number(10) + bytes([6]) + number(5) + bytes([7])
        return cs + bytes([9, 14])  # closepath, endchar

    glyphs = [(b".notdef", box(1)), (b"A", box(25)), (b"infinity", box(25)),
              (b"B", box(25))]
    private = (
        b"dup /Private 8 dict dup begin\n/lenIV 4 def\n"
        b"/CharStrings 4 dict dup begin\n"
    )
    for name, charstring in glyphs:
        encrypted = _encrypt_charstring(charstring, 4)
        private += (
            b"/" + name + b" " + str(len(encrypted)).encode() + b" RD "
            + encrypted + b" ND\n"
        )
    private += b"end\nend\nreadonly put\nnoaccess put\n"
    eexec = _encrypt(b"\x00\x00\x00\x00" + private, _EEXEC_R)
    clear = (
        b"%!FontType1-1.0: MacTest\n/FontType 1 def\n"
        b"/FontMatrix [0.001 0 0 0.001 0 0] def\n"
        b"/Encoding 256 array\n0 1 255 {1 index exch /.notdef put} for\n"
        b"dup 176 /B put\nreadonly def\n"  # 0xB0 -> "B", as the font sees it
        b"currentdict end\ncurrentfile eexec\n"
    )
    trailer = b"\n" + b"0" * 512 + b"\ncleartomark\n"
    return clear + eexec + trailer, len(clear), len(eexec)


def test_a_type1_font_keeps_the_glyph_its_own_encoding_names():
    program, length1, length2 = _type1_with_builtin_encoding()
    content = b"BT /F0 12 Tf <b0> Tj ET"
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 200, 200)]
    pdf.page_contents = [content]
    pdf._ensure_cos()
    cos = pdf._cos_doc
    font_file = cos.register_object(
        PdfStream(
            program,
            {
                PdfName("Length"): PdfNumber(len(program)),
                PdfName("Length1"): PdfNumber(length1),
                PdfName("Length2"): PdfNumber(length2),
                PdfName("Length3"): PdfNumber(len(program) - length1 - length2),
            },
        )
    )
    descriptor = cos.register_object(
        PdfDictionary(
            {
                PdfName("Type"): PdfName("FontDescriptor"),
                PdfName("FontName"): PdfName("AAAAAA+MacTest"),
                PdfName("FontFile"): font_file,
            }
        )
    )
    font_obj = cos.register_object(
        PdfDictionary(
            {
                PdfName("Type"): PdfName("Font"),
                PdfName("Subtype"): PdfName("Type1"),
                PdfName("BaseFont"): PdfName("AAAAAA+MacTest"),
                PdfName("Encoding"): PdfName("MacRomanEncoding"),
                PdfName("FontDescriptor"): descriptor,
            }
        )
    )
    pdf._get_page_dict(0).mapping[PdfName("Resources")] = PdfDictionary(
        {PdfName("Font"): PdfDictionary({PdfName("F0"): font_obj})}
    )

    original = cos.objects[font_file.object_number].content
    pdf.optimize(
        OptimizationOptions(
            remove_unused_objects=False,
            remove_unused_streams=False,
            link_duplicate_streams=False,
            remove_duplicate_images=False,
            compress_fonts=False,
            subset_fonts=True,
        )
    )
    subset = cos.objects[font_file.object_number].content

    assert len(subset) < len(original)
    from aspose_pdf.engine.type1_outlines import Type1Outlines

    outlines = Type1Outlines(subset, length1, len(subset) - length1 - 513)
    drawn = {
        name: bool(outlines.outline(gid))
        for name, gid in outlines.name_to_gid.items()
    }
    assert drawn["B"] is True  # the glyph the font shows at 0xB0
    assert drawn["infinity"] is False  # the glyph Mac OS Roman would have named


# ---------------------------------------------------------------------------
# Rendering follows the same order
# ---------------------------------------------------------------------------


def _code_to_gid(program: bytes, encoding: str | None):
    from aspose_pdf.engine.rasterizer import _PageRasterizer

    pdf, _ = _embed(program, [0xB0], encoding=encoding)
    saved = SimplePdf.from_bytes(pdf.to_bytes())
    page = saved._get_page_dict(0)
    fonts = saved._resolve(
        saved._resolve(page.mapping[PdfName("Resources")]).mapping[PdfName("Font")]
    )
    font_dict = saved._resolve(fonts.mapping[PdfName("F0")])
    renderer = _PageRasterizer(
        saved, 0, dpi=72, scale=1.0, background=(255, 255, 255)
    )
    glyph_font = renderer._build_simple_cff_font(font_dict)
    return glyph_font, saved


def test_the_renderer_draws_the_glyph_the_font_puts_at_that_code():
    program = _font_with_builtin_encoding()
    glyph_font, _ = _code_to_gid(program, "MacRomanEncoding")

    assert glyph_font is not None
    # "B" is glyph 3 in this font's order; "infinity" is glyph 2.
    assert glyph_font.code_to_gid(0xB0) == _ORDER.index("B")


def test_the_renderer_falls_back_to_the_supplement():
    """Nothing else names 0xF0, so the Mac OS Roman "apple" is used."""
    program = _font_with_builtin_encoding()
    glyph_font, _ = _code_to_gid(program, "MacRomanEncoding")
    # This font has no "apple" glyph, so the name resolves to no glyph id --
    # what matters is that the supplement is what supplied the name at all.
    assert glyph_font.code_to_unicode is not None
    assert glyph_font.code_to_unicode(0xF0) == ord("")

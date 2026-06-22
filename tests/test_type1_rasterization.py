"""Tests for Type 1 (`/FontFile`) glyph outline extraction and rasterization.

Exercises the eexec/charstring decryption and the Type 1 charstring interpreter
(:mod:`aspose_pdf.engine.type1_outlines`) plus the page renderer's use of it.
"""

import struct

from aspose_pdf import Document
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfDictionary,
    PdfName,
    PdfNumber,
    PdfStream,
)
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.engine.type1_outlines import Type1Outlines


# ---------------------------------------------------------------------------
# Minimal Type 1 builder
# ---------------------------------------------------------------------------


def _t1num(v: int) -> bytes:
    v = int(v)
    if -107 <= v <= 107:
        return bytes([v + 139])
    if 108 <= v <= 1131:
        v -= 108
        return bytes([(v >> 8) + 247, v & 0xFF])
    if -1131 <= v <= -108:
        v = -108 - v
        return bytes([(v >> 8) + 251, v & 0xFF])
    return bytes([255]) + struct.pack(">i", v)


def _encrypt(plain: bytes, r: int, n_prefix: int) -> bytes:
    data = bytes(n_prefix) + plain
    c1, c2 = 52845, 22719
    out = bytearray()
    for p in data:
        c = p ^ (r >> 8)
        out.append(c)
        r = ((c + r) * c1 + c2) & 0xFFFF
    return bytes(out)


def _box_cs(x0, y0, x1, y1) -> bytes:
    w, h = x1 - x0, y1 - y0
    return (
        _t1num(0) + _t1num(0) + b"\x0d"  # hsbw
        + _t1num(x0) + _t1num(y0) + b"\x15"  # rmoveto
        + _t1num(w) + b"\x06"  # hlineto
        + _t1num(h) + b"\x07"  # vlineto
        + _t1num(-w) + b"\x06"  # hlineto
        + b"\x09"  # closepath
        + b"\x0e"  # endchar
    )


def _arch_cs() -> bytes:
    return (
        _t1num(0) + _t1num(0) + b"\x0d"  # hsbw
        + _t1num(0) + _t1num(0) + b"\x15"  # rmoveto
        + _t1num(0) + _t1num(500) + _t1num(500) + _t1num(0) + _t1num(500) + _t1num(-500)
        + b"\x08"  # rrcurveto
        + b"\x0e"  # endchar
    )


def _box_via_subr(x0, y0, x1, y1):
    w, h = x1 - x0, y1 - y0
    subr = _t1num(w) + b"\x06" + _t1num(h) + b"\x07" + _t1num(-w) + b"\x06" + b"\x0b"
    main = (
        _t1num(0) + _t1num(0) + b"\x0d"  # hsbw
        + _t1num(x0) + _t1num(y0) + b"\x15"  # rmoveto
        + _t1num(0) + b"\x0a"  # 0 callsubr
        + b"\x0e"  # endchar
    )
    return main, [subr]


def _make_type1(charstrings, *, encoding=None, subrs=None):
    """Build a Type 1 program; return ``(font_bytes, length1, length2)``."""
    header = bytearray()
    header += b"%!FontType1-1.0: Test 001.001\n"
    header += b"/FontMatrix [0.001 0 0 0.001 0 0] readonly def\n"
    if encoding:
        header += b"/Encoding 256 array\n"
        header += b"0 1 255 {1 index exch /.notdef put} for\n"
        for code, name in sorted(encoding.items()):
            header += b"dup %d /%s put\n" % (code, name.encode("latin-1"))
        header += b"readonly def\n"
    else:
        header += b"/Encoding StandardEncoding def\n"
    header += b"currentdict end\ncurrentfile eexec\n"

    private = bytearray(b"dup /Private 12 dict dup begin\n/lenIV 4 def\n")
    if subrs:
        private += b"/Subrs %d array\n" % len(subrs)
        for i, sub in enumerate(subrs):
            enc = _encrypt(sub, 4330, 4)
            private += b"dup %d %d RD " % (i, len(enc)) + enc + b" NP\n"
        private += b"ND\n"
    private += b"2 index /CharStrings %d dict dup begin\n" % len(charstrings)
    for name, cs in charstrings.items():
        enc = _encrypt(cs, 4330, 4)
        private += b"/%s %d RD " % (name.encode("latin-1"), len(enc)) + enc + b" ND\n"
    private += b"end\nend\nreadonly put\nnoaccess put\n"

    eexec = _encrypt(bytes(private), 55665, 4)
    trailer = b"\n" + (b"0" * 64 + b"\n") * 8 + b"cleartomark\n"
    font = bytes(header) + eexec + trailer
    return font, len(header), len(eexec)


# ---------------------------------------------------------------------------
# Decoder unit tests
# ---------------------------------------------------------------------------


def test_type1_decodes_box_charstring():
    font, l1, l2 = _make_type1(
        {".notdef": _box_cs(0, 0, 0, 0), "A": _box_cs(100, 200, 800, 900)},
        encoding={65: "A"},
    )
    outlines = Type1Outlines(font, l1, l2)

    assert outlines.ok
    assert outlines.units_per_em == 1000
    assert outlines.builtin_encoding == {65: "A"}
    assert "A" in outlines.name_to_gid

    contour = outlines.outline(outlines.name_to_gid["A"])
    assert len(contour) == 1
    xs = [p[0] for p in contour[0]]
    ys = [p[1] for p in contour[0]]
    assert round(min(xs)) == 100 and round(max(xs)) == 800
    assert round(min(ys)) == 200 and round(max(ys)) == 900


def test_type1_flattens_curve():
    font, l1, l2 = _make_type1({".notdef": _box_cs(0, 0, 0, 0), "C": _arch_cs()})
    outlines = Type1Outlines(font, l1, l2)

    points = outlines.outline(outlines.name_to_gid["C"])[0]
    peak = max(p[1] for p in points)
    assert 330 <= peak <= 400
    assert any(150 < p[1] < 330 for p in points)


def test_type1_executes_subr():
    main, subrs = _box_via_subr(0, 0, 600, 700)
    font, l1, l2 = _make_type1(
        {".notdef": _box_cs(0, 0, 0, 0), "B": main}, subrs=subrs
    )
    outlines = Type1Outlines(font, l1, l2)

    contour = outlines.outline(outlines.name_to_gid["B"])
    assert len(contour) == 1
    assert round(max(p[0] for p in contour[0])) == 600
    assert round(max(p[1] for p in contour[0])) == 700


def test_type1_splits_via_eexec_without_lengths():
    font, _l1, _l2 = _make_type1(
        {".notdef": _box_cs(0, 0, 0, 0), "A": _box_cs(0, 0, 500, 500)},
        encoding={65: "A"},
    )
    # No Length1/Length2: the parser locates the eexec section heuristically.
    outlines = Type1Outlines(font)
    assert outlines.ok
    assert round(max(p[0] for p in outlines.outline(outlines.name_to_gid["A"])[0])) == 500


def test_type1_rejects_junk():
    assert not Type1Outlines(b"not a font").ok
    assert Type1Outlines(b"junk").outline(0) == []


# ---------------------------------------------------------------------------
# End-to-end rendering
# ---------------------------------------------------------------------------


def _count_black(raster, x0, y0, x1, y1):
    return sum(
        raster.get_pixel(x, y) == (0, 0, 0)
        for y in range(y0, y1)
        for x in range(x0, x1)
    )


def _embed_type1(pdf, font, l1, l2, *, encoding_obj=None):
    pdf._ensure_cos()
    cos = pdf._cos_doc
    ff = cos.register_object(
        PdfStream(
            font,
            {
                PdfName("Length"): PdfNumber(len(font)),
                PdfName("Length1"): PdfNumber(l1),
                PdfName("Length2"): PdfNumber(l2),
                PdfName("Length3"): PdfNumber(len(font) - l1 - l2),
            },
        )
    )
    descriptor = cos.register_object(
        PdfDictionary(
            {
                PdfName("Type"): PdfName("FontDescriptor"),
                PdfName("FontName"): PdfName("Test"),
                PdfName("FontFile"): ff,
            }
        )
    )
    font_entries = {
        PdfName("Type"): PdfName("Font"),
        PdfName("Subtype"): PdfName("Type1"),
        PdfName("BaseFont"): PdfName("Test"),
        PdfName("FirstChar"): PdfNumber(0x41),
        PdfName("Widths"): PdfArray([PdfNumber(1000)]),
        PdfName("FontDescriptor"): descriptor,
    }
    if encoding_obj is not None:
        font_entries[PdfName("Encoding")] = encoding_obj
    font_obj = cos.register_object(PdfDictionary(font_entries))
    pdf._get_page_dict(0).mapping[PdfName("Resources")] = PdfDictionary(
        {PdfName("Font"): PdfDictionary({PdfName("F1"): font_obj})}
    )
    return Document(), pdf


def test_render_type1_outline_not_box():
    # Bottom-bar glyph resolved through the font's own built-in encoding.
    font, l1, l2 = _make_type1(
        {".notdef": _box_cs(0, 0, 0, 0), "A": _box_cs(0, 0, 1000, 300)},
        encoding={65: "A"},
    )
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 40, 40)]
    pdf.page_contents = [b"BT /F1 30 Tf 1 0 0 1 5 5 Tm (A) Tj ET"]
    doc, pdf = _embed_type1(pdf, font, l1, l2)
    doc._engine_pdf = pdf

    raster = doc.pages[0].render()

    assert _count_black(raster, 8, 27, 33, 35) > 30  # bottom bar filled
    assert _count_black(raster, 8, 9, 33, 19) == 0  # upper cell empty (not a box)


def test_render_type1_via_pdf_encoding_differences():
    # Glyph named "boxA" resolved through a PDF /Encoding /Differences override.
    font, l1, l2 = _make_type1(
        {".notdef": _box_cs(0, 0, 0, 0), "boxA": _box_cs(100, 0, 900, 900)}
    )
    encoding = PdfDictionary(
        {
            PdfName("Type"): PdfName("Encoding"),
            PdfName("Differences"): PdfArray([PdfNumber(0x41), PdfName("boxA")]),
        }
    )
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 40, 40)]
    pdf.page_contents = [b"BT /F1 30 Tf 1 0 0 1 5 5 Tm (A) Tj ET"]
    doc, pdf = _embed_type1(pdf, font, l1, l2, encoding_obj=encoding)
    doc._engine_pdf = pdf

    raster = doc.pages[0].render()

    assert raster.get_pixel(20, 20) == (0, 0, 0)

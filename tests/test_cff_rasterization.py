"""Tests for CFF (`/FontFile3`) glyph outline extraction and rasterization.

Exercises the Type 2 charstring interpreter
(:mod:`aspose_pdf.engine.cff_outlines`) and the page renderer's use of it for
embedded CFF fonts -- name-keyed (`/Type1C`) and CID-keyed (`/CIDFontType0C`).
"""

import struct

from aspose_pdf import Document
from aspose_pdf.engine.cff_outlines import CffOutlines
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfDictionary,
    PdfName,
    PdfNumber,
    PdfStream,
)
from aspose_pdf.engine.simple_pdf import SimplePdf


# ---------------------------------------------------------------------------
# Minimal CFF builder
# ---------------------------------------------------------------------------


def _t2num(v: int) -> bytes:
    """Encode a Type 2 charstring integer operand."""
    v = int(v)
    if -107 <= v <= 107:
        return bytes([v + 139])
    if 108 <= v <= 1131:
        v -= 108
        return bytes([(v >> 8) + 247, v & 0xFF])
    if -1131 <= v <= -108:
        v = -108 - v
        return bytes([(v >> 8) + 251, v & 0xFF])
    return b"\x1c" + struct.pack(">h", v)  # operator 28 + int16


def _enc_off(v: int) -> bytes:
    """A fixed-width 5-byte DICT integer (operator 29) for relocatable offsets."""
    return b"\x1d" + struct.pack(">i", v)


def _cff_index(items: list[bytes]) -> bytes:
    if not items:
        return b"\x00\x00"
    data = b"".join(items)
    offsets = [1]
    for item in items:
        offsets.append(offsets[-1] + len(item))
    last = offsets[-1]
    off_size = 1 if last < 0x100 else 2 if last < 0x10000 else 3 if last < 0x1000000 else 4
    out = bytearray(struct.pack(">H", len(items)) + bytes([off_size]))
    for off in offsets:
        out += off.to_bytes(off_size, "big")
    return bytes(out) + data


def _box_charstring(x0: int, y0: int, x1: int, y1: int) -> bytes:
    """rmoveto + hlineto/vlineto rectangle + endchar."""
    w, h = x1 - x0, y1 - y0
    return (
        _t2num(x0) + _t2num(y0) + b"\x15"  # rmoveto
        + _t2num(w) + b"\x06"  # hlineto
        + _t2num(h) + b"\x07"  # vlineto
        + _t2num(-w) + b"\x06"  # hlineto
        + b"\x0e"  # endchar
    )


def _arch_charstring() -> bytes:
    """rmoveto then one rrcurveto arch peaking near y=375; endchar."""
    return (
        _t2num(0) + _t2num(0) + b"\x15"  # rmoveto to (0,0)
        + _t2num(0) + _t2num(500)  # ctrl1 rel -> (0,500)
        + _t2num(500) + _t2num(0)  # ctrl2 rel -> (500,500)
        + _t2num(500) + _t2num(-500)  # end rel -> (1000,0)
        + b"\x08"  # rrcurveto
        + b"\x0e"  # endchar
    )


def _box_via_subr_charstrings(x0, y0, x1, y1):
    """A main charstring that calls local subr #0 to draw the box body."""
    w, h = x1 - x0, y1 - y0
    lsubr = (
        _t2num(w) + b"\x06" + _t2num(h) + b"\x07" + _t2num(-w) + b"\x06" + b"\x0b"
    )  # hlineto vlineto hlineto, then return
    # one local subr -> bias 107; index 0 is called as (0 - 107) callsubr.
    main = _t2num(x0) + _t2num(y0) + b"\x15" + _t2num(-107) + b"\x0a" + b"\x0e"
    return main, [lsubr]


def _make_simple_cff(charstrings, *, encoding=None, lsubrs=None):
    """Assemble a minimal name-keyed CFF program."""
    header = bytes([1, 0, 4, 1])
    name_index = _cff_index([b"Test"])
    string_index = _cff_index([])
    gsubr_index = _cff_index([])
    charstrings_index = _cff_index(charstrings)

    encoding_blob = b""
    if encoding is not None:
        gid_to_code = {g: c for c, g in encoding.items()}
        codes = [gid_to_code[g] for g in range(1, max(encoding.values()) + 1)]
        encoding_blob = bytes([0, len(codes)]) + bytes(codes)

    private_blob = b""
    if lsubrs is not None:
        lsubr_index = _cff_index(lsubrs)
        private_dict = _enc_off(6) + b"\x13"  # Subrs at relative offset 6
        private_blob = private_dict + lsubr_index

    def top_dict(cs_off, enc_off, priv_off):
        out = _enc_off(cs_off) + b"\x11"  # CharStrings
        if encoding is not None:
            out += _enc_off(enc_off) + b"\x10"  # Encoding
        if lsubrs is not None:
            out += _enc_off(6) + _enc_off(priv_off) + b"\x12"  # Private [size off]
        return out

    # Two passes: the Top DICT length is offset-value-independent (5-byte ints).
    topdict_len = len(_cff_index([top_dict(0, 0, 0)]))
    pos = len(header) + len(name_index) + topdict_len
    pos += len(string_index) + len(gsubr_index)
    cs_off = pos
    pos += len(charstrings_index)
    enc_off = pos
    pos += len(encoding_blob)
    priv_off = pos

    topdict_index = _cff_index([top_dict(cs_off, enc_off, priv_off)])
    return (
        header
        + name_index
        + topdict_index
        + string_index
        + gsubr_index
        + charstrings_index
        + encoding_blob
        + private_blob
    )


def _make_cid_cff(charstrings, cids):
    """Assemble a minimal CID-keyed CFF (ROS + charset + FDArray + FDSelect)."""
    header = bytes([1, 0, 4, 1])
    name_index = _cff_index([b"Test-CID"])
    string_index = _cff_index([b"Adobe", b"Identity"])  # SIDs 391, 392
    gsubr_index = _cff_index([])
    charstrings_index = _cff_index(charstrings)
    num_glyphs = len(charstrings)

    charset_blob = bytes([0]) + b"".join(struct.pack(">H", c) for c in cids)
    fdselect_blob = bytes([0]) + bytes([0] * num_glyphs)
    fd_dict = _enc_off(0) + _enc_off(0) + b"\x12"  # Private [size=0 off=0]
    fdarray_blob = _cff_index([fd_dict])

    def top_dict(cs_off, charset_off, fdarray_off, fdselect_off):
        return (
            _enc_off(391) + _enc_off(392) + _enc_off(0) + bytes([12, 30])  # ROS
            + _enc_off(cs_off) + b"\x11"  # CharStrings
            + _enc_off(charset_off) + b"\x0f"  # charset
            + _enc_off(fdarray_off) + bytes([12, 36])  # FDArray
            + _enc_off(fdselect_off) + bytes([12, 37])  # FDSelect
        )

    topdict_len = len(_cff_index([top_dict(0, 0, 0, 0)]))
    pos = len(header) + len(name_index) + topdict_len
    pos += len(string_index) + len(gsubr_index)
    cs_off = pos
    pos += len(charstrings_index)
    charset_off = pos
    pos += len(charset_blob)
    fdselect_off = pos
    pos += len(fdselect_blob)
    fdarray_off = pos

    topdict_index = _cff_index(
        [top_dict(cs_off, charset_off, fdarray_off, fdselect_off)]
    )
    return (
        header
        + name_index
        + topdict_index
        + string_index
        + gsubr_index
        + charstrings_index
        + charset_blob
        + fdselect_blob
        + fdarray_blob
    )


# ---------------------------------------------------------------------------
# Interpreter unit tests
# ---------------------------------------------------------------------------


def test_cff_decodes_box_charstring():
    cff = _make_simple_cff([b"\x0e", _box_charstring(100, 200, 800, 900)])
    outlines = CffOutlines(cff)

    assert outlines.ok
    assert outlines.units_per_em == 1000
    assert outlines.num_glyphs == 2
    assert outlines.outline(0) == []  # .notdef: bare endchar

    contour = outlines.outline(1)
    assert len(contour) == 1
    xs = [p[0] for p in contour[0]]
    ys = [p[1] for p in contour[0]]
    assert round(min(xs)) == 100 and round(max(xs)) == 800
    assert round(min(ys)) == 200 and round(max(ys)) == 900


def test_cff_flattens_cubic_curve():
    outlines = CffOutlines(_make_simple_cff([b"\x0e", _arch_charstring()]))

    points = outlines.outline(1)[0]
    peak = max(p[1] for p in points)
    assert 330 <= peak <= 400  # cubic apex ~375, below the control y=500
    assert any(150 < p[1] < 330 for p in points)  # curve subdivided


def test_cff_executes_local_subr():
    main, lsubrs = _box_via_subr_charstrings(0, 0, 600, 700)
    outlines = CffOutlines(_make_simple_cff([b"\x0e", main], lsubrs=lsubrs))

    contour = outlines.outline(1)
    assert len(contour) == 1
    xs = [p[0] for p in contour[0]]
    ys = [p[1] for p in contour[0]]
    assert round(max(xs)) == 600 and round(max(ys)) == 700


def test_cff_encoding_code_to_gid():
    cff = _make_simple_cff(
        [b"\x0e", _box_charstring(0, 0, 500, 500)], encoding={0x41: 1}
    )
    assert CffOutlines(cff).encoding_code_to_gid() == {0x41: 1}


def test_cff_unwraps_otto_wrapper():
    cff = _make_simple_cff([b"\x0e", _box_charstring(0, 0, 500, 500)])
    # Wrap the CFF as the sole table of an OTTO OpenType font.
    tag = b"CFF "
    table_off = 12 + 16
    directory = tag + struct.pack(">III", 0, table_off, len(cff))
    otto = b"OTTO" + struct.pack(">HHHH", 1, 0, 0, 0) + directory + cff
    outlines = CffOutlines(otto)
    assert outlines.ok
    assert round(max(p[0] for p in outlines.outline(1)[0])) == 500


def test_cff_rejects_cff2_and_junk():
    assert not CffOutlines(b"\x02\x00\x04\x01").ok  # CFF2 major version
    assert not CffOutlines(b"junk").ok
    assert CffOutlines(b"junk").outline(0) == []


# ---------------------------------------------------------------------------
# End-to-end rendering
# ---------------------------------------------------------------------------


def _render_first_page(pdf):
    doc = Document()
    doc._engine_pdf = pdf
    return doc.pages[0].render()


def _count_black(raster, x0, y0, x1, y1):
    return sum(
        raster.get_pixel(x, y) == (0, 0, 0)
        for y in range(y0, y1)
        for x in range(x0, x1)
    )


def test_render_simple_cff_outline_not_box():
    # Glyph fills only the bottom 30% of the cell: a real outline leaves the top
    # empty, where the placeholder-box fallback would paint.
    cff = _make_simple_cff(
        [b"\x0e", _box_charstring(0, 0, 1000, 300)], encoding={0x41: 1}
    )
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 40, 40)]
    pdf.page_contents = [b"BT /F1 30 Tf 1 0 0 1 5 5 Tm (A) Tj ET"]
    pdf._ensure_cos()
    cos = pdf._cos_doc
    ff = cos.register_object(
        PdfStream(
            cff,
            {PdfName("Length"): PdfNumber(len(cff)), PdfName("Subtype"): PdfName("Type1C")},
        )
    )
    descriptor = cos.register_object(
        PdfDictionary(
            {
                PdfName("Type"): PdfName("FontDescriptor"),
                PdfName("FontName"): PdfName("Test"),
                PdfName("FontFile3"): ff,
            }
        )
    )
    font = cos.register_object(
        PdfDictionary(
            {
                PdfName("Type"): PdfName("Font"),
                PdfName("Subtype"): PdfName("Type1"),
                PdfName("BaseFont"): PdfName("Test"),
                PdfName("FirstChar"): PdfNumber(0x41),
                PdfName("Widths"): PdfArray([PdfNumber(1000)]),
                PdfName("FontDescriptor"): descriptor,
            }
        )
    )
    pdf._get_page_dict(0).mapping[PdfName("Resources")] = PdfDictionary(
        {PdfName("Font"): PdfDictionary({PdfName("F1"): font})}
    )

    raster = _render_first_page(pdf)

    assert _count_black(raster, 8, 27, 33, 35) > 30  # bottom bar filled
    assert _count_black(raster, 8, 9, 33, 19) == 0  # upper cell empty (not a box)


def test_render_cid_keyed_cff_glyph():
    cff = _make_cid_cff([b"\x0e", _box_charstring(100, 0, 900, 900)], cids=[1])
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 40, 40)]
    pdf.page_contents = [b"BT /F0 30 Tf 1 0 0 1 5 5 Tm <0001> Tj ET"]
    pdf._ensure_cos()
    cos = pdf._cos_doc
    ff = cos.register_object(
        PdfStream(
            cff,
            {
                PdfName("Length"): PdfNumber(len(cff)),
                PdfName("Subtype"): PdfName("CIDFontType0C"),
            },
        )
    )
    descriptor = cos.register_object(
        PdfDictionary(
            {
                PdfName("Type"): PdfName("FontDescriptor"),
                PdfName("FontName"): PdfName("Test-CID"),
                PdfName("FontFile3"): ff,
            }
        )
    )
    cidfont = cos.register_object(
        PdfDictionary(
            {
                PdfName("Type"): PdfName("Font"),
                PdfName("Subtype"): PdfName("CIDFontType0"),
                PdfName("BaseFont"): PdfName("Test-CID"),
                PdfName("FontDescriptor"): descriptor,
            }
        )
    )
    type0 = cos.register_object(
        PdfDictionary(
            {
                PdfName("Type"): PdfName("Font"),
                PdfName("Subtype"): PdfName("Type0"),
                PdfName("BaseFont"): PdfName("Test-CID"),
                PdfName("Encoding"): PdfName("Identity-H"),
                PdfName("DescendantFonts"): PdfArray([cidfont]),
            }
        )
    )
    pdf._get_page_dict(0).mapping[PdfName("Resources")] = PdfDictionary(
        {PdfName("Font"): PdfDictionary({PdfName("F0"): type0})}
    )

    raster = _render_first_page(pdf)

    assert raster.get_pixel(20, 20) == (0, 0, 0)  # glyph body filled

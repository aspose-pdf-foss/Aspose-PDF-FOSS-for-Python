"""Tests for TrueType ``glyf`` outline extraction and glyph rasterization.

These exercise both the dependency-free outline decoder
(:mod:`aspose_pdf.engine.glyph_outlines`) and the page renderer's use of it:
real glyph contours are filled instead of the placeholder boxes used for fonts
whose outlines cannot be decoded.
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
from aspose_pdf.engine.glyph_outlines import TrueTypeOutlines
from aspose_pdf.engine.simple_pdf import SimplePdf


# ---------------------------------------------------------------------------
# Minimal TrueType builder with real glyph outlines
# ---------------------------------------------------------------------------


def _box_glyph(x0: int, y0: int, x1: int, y1: int) -> bytes:
    """A simple glyph: one rectangular contour with four on-curve points."""
    header = struct.pack(">hhhhh", 1, x0, y0, x1, y1)  # numContours + bbox
    end_pts = struct.pack(">H", 3)  # one contour ending at point index 3
    instructions = struct.pack(">H", 0)
    flags = bytes([0x01, 0x01, 0x01, 0x01])  # all on-curve, int16 deltas
    xs = [x0, x1, x1, x0]
    ys = [y0, y0, y1, y1]
    x_bytes = b""
    prev = 0
    for x in xs:
        x_bytes += struct.pack(">h", x - prev)
        prev = x
    y_bytes = b""
    prev = 0
    for y in ys:
        y_bytes += struct.pack(">h", y - prev)
        prev = y
    return header + end_pts + instructions + flags + x_bytes + y_bytes


def _ring_glyph() -> bytes:
    """A glyph with a counter: an outer contour (CCW) and an inner hole (CW).

    The opposite winding makes the nonzero rule leave the centre unfilled, the
    way an ``O``/``o`` counter stays open.
    """
    outer_x = [50, 950, 950, 50]
    outer_y = [50, 50, 950, 950]
    inner_x = [350, 350, 650, 650]  # reversed orientation vs the outer ring
    inner_y = [350, 650, 650, 350]
    xs = outer_x + inner_x
    ys = outer_y + inner_y
    header = struct.pack(">hhhhh", 2, 0, 0, 1000, 1000)
    end_pts = struct.pack(">HH", 3, 7)
    instructions = struct.pack(">H", 0)
    flags = bytes([0x01] * 8)
    x_bytes = b""
    prev = 0
    for x in xs:
        x_bytes += struct.pack(">h", x - prev)
        prev = x
    y_bytes = b""
    prev = 0
    for y in ys:
        y_bytes += struct.pack(">h", y - prev)
        prev = y
    return header + end_pts + instructions + flags + x_bytes + y_bytes


def _arc_glyph() -> bytes:
    """A 3-point contour: on-curve (0,0), off-curve ctrl (500,1000), on (1000,0).

    The quadratic apex sits at (500, 500) -- halfway to the control point -- so a
    correctly flattened outline peaks near y=500, not the straight-line y=0 nor
    the control y=1000.
    """
    header = struct.pack(">hhhhh", 1, 0, 0, 1000, 1000)
    end_pts = struct.pack(">H", 2)
    instructions = struct.pack(">H", 0)
    flags = bytes([0x01, 0x00, 0x01])  # on, off (control), on
    xs = [0, 500, 1000]
    ys = [0, 1000, 0]
    x_bytes = b""
    prev = 0
    for x in xs:
        x_bytes += struct.pack(">h", x - prev)
        prev = x
    y_bytes = b""
    prev = 0
    for y in ys:
        y_bytes += struct.pack(">h", y - prev)
        prev = y
    return header + end_pts + instructions + flags + x_bytes + y_bytes


def _composite_glyph(component_gid: int, dx: int, dy: int) -> bytes:
    header = struct.pack(">hhhhh", -1, 0, 0, 1000, 1000)
    flags = 0x0001 | 0x0002  # ARG_1_AND_2_ARE_WORDS | ARGS_ARE_XY_VALUES
    component = struct.pack(">HH", flags, component_gid) + struct.pack(">hh", dx, dy)
    return header + component


def _pad4(data: bytes) -> bytes:
    return data + b"\x00" * ((4 - len(data) % 4) % 4)


def _build_ttf(glyphs: list[bytes], extra_tables: dict[str, bytes] | None = None) -> bytes:
    num_glyphs = len(glyphs)
    glyf = b"".join(glyphs)
    offsets = [0]
    for g in glyphs:
        offsets.append(offsets[-1] + len(g))
    loca = b"".join(struct.pack(">I", o) for o in offsets)  # long format

    head = (
        struct.pack(">III", 0x00010000, 0, 0)
        + struct.pack(">I", 0x5F0F3CF5)
        + struct.pack(">HH", 0, 1000)  # flags, unitsPerEm
        + struct.pack(">qq", 0, 0)
        + struct.pack(">hhhh", 0, 0, 1000, 1000)
        + struct.pack(">HHh", 0, 8, 0)
        + struct.pack(">hh", 1, 0)  # indexToLocFormat (long), glyphDataFormat
    )
    maxp = struct.pack(">IH", 0x00010000, num_glyphs)

    tables = {"head": head, "maxp": maxp, "loca": loca, "glyf": glyf}
    if extra_tables:
        tables.update(extra_tables)
    tags = sorted(tables)

    offset = 12 + 16 * len(tags)
    directory = bytearray()
    body = bytearray()
    for tag in tags:
        data = tables[tag]
        directory += tag.encode("ascii") + struct.pack(">III", 0, offset, len(data))
        padded = _pad4(data)
        body += padded
        offset += len(padded)
    header = struct.pack(">IHHHH", 0x00010000, len(tags), 0, 0, 0)
    return bytes(header + directory + body)


def _cmap_format0(gids: list[int]) -> bytes:
    return struct.pack(">HHH", 0, 262, 0) + bytes(gids)


def _cmap_table(platform: int, encoding: int, subtable: bytes) -> bytes:
    return struct.pack(">HHHHI", 0, 1, platform, encoding, 12) + subtable


# ---------------------------------------------------------------------------
# Outline decoder unit tests
# ---------------------------------------------------------------------------


def test_outline_decodes_simple_box_glyph():
    font = _build_ttf([b"", _box_glyph(100, 200, 900, 800)])
    outlines = TrueTypeOutlines(font)

    assert outlines.ok
    assert outlines.units_per_em == 1000
    assert outlines.num_glyphs == 2
    assert outlines.outline(0) == []  # .notdef is empty

    contour = outlines.outline(1)
    assert len(contour) == 1
    xs = [p[0] for p in contour[0]]
    ys = [p[1] for p in contour[0]]
    assert min(xs) == 100 and max(xs) == 900
    assert min(ys) == 200 and max(ys) == 800


def test_outline_flattens_quadratic_curve():
    outlines = TrueTypeOutlines(_build_ttf([b"", _arc_glyph()]))

    contour = outlines.outline(1)
    assert len(contour) == 1
    points = contour[0]
    peak = max(p[1] for p in points)
    # Apex of the quadratic is at y=500; allow slack for segment sampling.
    assert 450 <= peak <= 510
    # Intermediate samples must leave the chord (proof of curve subdivision).
    assert any(200 < p[1] < 480 for p in points)


def test_outline_decodes_composite_glyph_with_offset():
    glyphs = [b"", _box_glyph(0, 0, 200, 200), _composite_glyph(1, 300, 400)]
    outlines = TrueTypeOutlines(_build_ttf(glyphs))

    contour = outlines.outline(2)
    assert len(contour) == 1
    xs = [p[0] for p in contour[0]]
    ys = [p[1] for p in contour[0]]
    assert min(xs) == 300 and max(xs) == 500  # shifted by +300
    assert min(ys) == 400 and max(ys) == 600  # shifted by +400


def test_outline_rejects_non_truetype():
    assert not TrueTypeOutlines(b"OTTO\x00\x00\x00\x00").ok
    assert not TrueTypeOutlines(b"not a font").ok
    assert TrueTypeOutlines(b"junk").outline(0) == []


# ---------------------------------------------------------------------------
# End-to-end rendering
# ---------------------------------------------------------------------------


def _count_black(raster, x0: int, y0: int, x1: int, y1: int) -> int:
    count = 0
    for y in range(y0, y1):
        for x in range(x0, x1):
            if raster.get_pixel(x, y) == (0, 0, 0):
                count += 1
    return count


def _make_type0_pdf(font_bytes: bytes, content: bytes):
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 40, 40)]
    pdf.page_contents = [content]
    pdf._ensure_cos()
    cos = pdf._cos_doc

    ff = cos.register_object(
        PdfStream(
            font_bytes,
            {
                PdfName("Length"): PdfNumber(len(font_bytes)),
                PdfName("Length1"): PdfNumber(len(font_bytes)),
            },
        )
    )
    descriptor = cos.register_object(
        PdfDictionary(
            {
                PdfName("Type"): PdfName("FontDescriptor"),
                PdfName("FontName"): PdfName("AAAAAA+Test"),
                PdfName("FontFile2"): ff,
            }
        )
    )
    cidfont = cos.register_object(
        PdfDictionary(
            {
                PdfName("Type"): PdfName("Font"),
                PdfName("Subtype"): PdfName("CIDFontType2"),
                PdfName("BaseFont"): PdfName("AAAAAA+Test"),
                PdfName("CIDToGIDMap"): PdfName("Identity"),
                PdfName("FontDescriptor"): descriptor,
            }
        )
    )
    type0 = cos.register_object(
        PdfDictionary(
            {
                PdfName("Type"): PdfName("Font"),
                PdfName("Subtype"): PdfName("Type0"),
                PdfName("BaseFont"): PdfName("AAAAAA+Test"),
                PdfName("Encoding"): PdfName("Identity-H"),
                PdfName("DescendantFonts"): PdfArray([cidfont]),
            }
        )
    )
    page = pdf._get_page_dict(0)
    page.mapping[PdfName("Resources")] = PdfDictionary(
        {PdfName("Font"): PdfDictionary({PdfName("F0"): type0})}
    )
    return pdf


def test_render_fills_real_glyph_outline_not_box():
    # Glyph 1 fills only the bottom 30% of the em (a low bar). A real outline
    # fill leaves the upper part of the cell empty, whereas the placeholder-box
    # fallback would paint the whole cell -- so the top band distinguishes them.
    font = _build_ttf([b"", _box_glyph(0, 0, 1000, 300)])
    content = b"BT /F0 30 Tf 1 0 0 1 5 5 Tm <0001> Tj ET"
    doc = Document()
    doc._engine_pdf = _make_type0_pdf(font, content)

    raster = doc.pages[0].render()

    bottom = _count_black(raster, 8, 27, 33, 35)
    top = _count_black(raster, 8, 9, 33, 19)
    assert bottom > 30  # the bar is filled
    assert top == 0  # the empty upper cell proves outline-aware fill


def test_render_glyph_counter_stays_open():
    # A ring glyph at a large size: the centre (counter) must stay background
    # while the surrounding ring is painted -- proof of nonzero-winding fill
    # across multiple contours rather than per-contour filling.
    font = _build_ttf([b"", _ring_glyph()])
    content = b"BT /F0 36 Tf 1 0 0 1 2 2 Tm <0001> Tj ET"
    doc = Document()
    doc._engine_pdf = _make_type0_pdf(font, content)

    raster = doc.pages[0].render()

    assert raster.get_pixel(20, 20) == (255, 255, 255)  # counter (hole)
    # Ring body: sample the left wall of the glyph (around x in font units ~200).
    assert raster.get_pixel(10, 20) == (0, 0, 0)


def test_render_type0_glyph_marks_expected_pixels():
    font = _build_ttf([b"", _box_glyph(100, 0, 900, 900)])
    content = b"BT /F0 30 Tf 1 0 0 1 5 5 Tm <0001> Tj ET"
    doc = Document()
    doc._engine_pdf = _make_type0_pdf(font, content)

    raster = doc.pages[0].render()

    # Centre of the glyph cell is inside the filled box.
    assert raster.get_pixel(20, 20) == (0, 0, 0)


def test_render_simple_truetype_via_symbol_cmap():
    gids = [0] * 256
    gids[0x41] = 1  # 'A' -> glyph 1
    cmap = _cmap_table(1, 0, _cmap_format0(gids))  # (1,0) Mac symbol subtable
    font = _build_ttf([b"", _box_glyph(100, 0, 900, 900)], {"cmap": cmap})

    pdf = SimplePdf()
    pdf.pages = [(0, 0, 40, 40)]
    pdf.page_contents = [b"BT /F1 30 Tf 1 0 0 1 5 5 Tm (A) Tj ET"]
    pdf._ensure_cos()
    cos = pdf._cos_doc
    ff = cos.register_object(
        PdfStream(font, {PdfName("Length"): PdfNumber(len(font))})
    )
    descriptor = cos.register_object(
        PdfDictionary(
            {
                PdfName("Type"): PdfName("FontDescriptor"),
                PdfName("FontName"): PdfName("Test"),
                PdfName("FontFile2"): ff,
            }
        )
    )
    tt = cos.register_object(
        PdfDictionary(
            {
                PdfName("Type"): PdfName("Font"),
                PdfName("Subtype"): PdfName("TrueType"),
                PdfName("BaseFont"): PdfName("Test"),
                PdfName("FirstChar"): PdfNumber(0x41),
                PdfName("Widths"): PdfArray([PdfNumber(900)]),
                PdfName("FontDescriptor"): descriptor,
            }
        )
    )
    page = pdf._get_page_dict(0)
    page.mapping[PdfName("Resources")] = PdfDictionary(
        {PdfName("Font"): PdfDictionary({PdfName("F1"): tt})}
    )
    doc = Document()
    doc._engine_pdf = pdf

    raster = doc.pages[0].render()

    assert raster.get_pixel(20, 20) == (0, 0, 0)


def test_render_falls_back_to_box_for_standard14_font():
    # No embedded program: the Standard-14 path must still mark pixels (boxes).
    doc = Document()
    doc._engine_pdf = SimplePdf(
        pages=[(0.0, 0.0, 40.0, 30.0)],
        page_contents=[b"0 g BT /F1 8 Tf 1 0 0 1 22 10 Tm (Hi) Tj ET"],
    )

    raster = doc.pages[0].render()

    assert raster.get_pixel(24, 17) == (0, 0, 0)

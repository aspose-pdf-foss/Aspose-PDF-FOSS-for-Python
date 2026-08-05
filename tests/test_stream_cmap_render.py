"""Rendering Type0 fonts whose ``/Encoding`` is an embedded CMap *stream*.

The bundled predefined CJK CMaps already render (see
``test_cjk_cmap_rendering``); this covers the remaining common case where the
font ships its own CMap as a stream. ``rasterizer._build_type0_font`` previously
returned ``None`` for any non-Identity, non-bundled encoding, so these drew
boxes. It now decodes the stream through the same parser extraction uses and
fills the descendant font's real glyphs.
"""

from __future__ import annotations

import struct

from aspose_pdf import Document
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfDictionary,
    PdfName,
    PdfNumber,
    PdfStream,
    PdfString,
)
from aspose_pdf.engine.rasterizer import _PageRasterizer
from aspose_pdf.engine.simple_pdf import SimplePdf
from tests.test_glyph_rasterization import _box_glyph, _build_ttf, _count_black

# A minimal horizontal CMap stream: 0x0041 -> CID 1, 0x0042 -> CID 2.
_H_CMAP = b"""%!PS-Adobe-3.0 Resource-CMap
/CIDInit /ProcSet findresource begin
12 dict begin
begincmap
/CMapName /Custom-H def
/CMapType 1 def
/WMode 0 def
1 begincodespacerange
<0000> <FFFF>
endcodespacerange
2 begincidrange
<0041> <0041> 1
<0042> <0042> 2
endcidrange
endcmap
CMapName currentdict /CMap defineresource pop
end
end
"""


def _make_stream_cmap_pdf(cmap_bytes: bytes, content: bytes) -> tuple[SimplePdf, PdfDictionary]:
    """One-page PDF with a CIDFontType2 under an embedded /Encoding CMap stream."""
    glyphs = [b"", _box_glyph(100, 0, 900, 900), _box_glyph(150, 0, 850, 800)]
    font_bytes = _build_ttf(glyphs)

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
                PdfName("FontName"): PdfName("AAAAAA+CJK"),
                PdfName("FontFile2"): ff,
            }
        )
    )
    cid_gid = bytearray(6)  # CID 1 -> GID 1, CID 2 -> GID 2
    struct.pack_into(">H", cid_gid, 2, 1)
    struct.pack_into(">H", cid_gid, 4, 2)
    cid_to_gid = cos.register_object(
        PdfStream(bytes(cid_gid), {PdfName("Length"): PdfNumber(len(cid_gid))})
    )
    cidfont = cos.register_object(
        PdfDictionary(
            {
                PdfName("Type"): PdfName("Font"),
                PdfName("Subtype"): PdfName("CIDFontType2"),
                PdfName("BaseFont"): PdfName("AAAAAA+CJK"),
                PdfName("CIDSystemInfo"): PdfDictionary(
                    {
                        PdfName("Registry"): PdfString(b"Adobe"),
                        PdfName("Ordering"): PdfString(b"Identity"),
                        PdfName("Supplement"): PdfNumber(0),
                    }
                ),
                PdfName("DW"): PdfNumber(1000),
                PdfName("W"): PdfArray(
                    [PdfNumber(1), PdfArray([PdfNumber(600), PdfNumber(600)])]
                ),
                PdfName("CIDToGIDMap"): cid_to_gid,
                PdfName("FontDescriptor"): descriptor,
            }
        )
    )
    encoding = cos.register_object(
        PdfStream(cmap_bytes, {PdfName("Length"): PdfNumber(len(cmap_bytes))})
    )
    type0 = PdfDictionary(
        {
            PdfName("Type"): PdfName("Font"),
            PdfName("Subtype"): PdfName("Type0"),
            PdfName("BaseFont"): PdfName("AAAAAA+CJK"),
            PdfName("Encoding"): encoding,
            PdfName("DescendantFonts"): PdfArray([cidfont]),
        }
    )
    type0_ref = cos.register_object(type0)
    page = pdf._get_page_dict(0)
    page.mapping[PdfName("Resources")] = PdfDictionary(
        {PdfName("Font"): PdfDictionary({PdfName("F0"): type0_ref})}
    )
    return pdf, type0


def _render(pdf: SimplePdf):
    doc = Document()
    doc._engine_pdf = pdf
    return doc.pages[0].render(antialias=False)


def test_stream_cmap_builds_and_renders_real_glyphs():
    content = b"BT /F0 20 Tf 1 0 0 1 2 2 Tm <00410042> Tj ET"
    pdf, type0 = _make_stream_cmap_pdf(_H_CMAP, content)

    renderer = _PageRasterizer(
        pdf, 0, dpi=72.0, scale=1.0, background=(255, 255, 255), antialias=False
    )
    font = renderer._build_type0_font(type0)
    assert font is not None and font.cmap is not None
    assert font.cmap.vertical is False

    raster = _render(pdf)
    assert _count_black(raster, 0, 0, 40, 40) > 0  # real glyphs, not blank


def test_stream_cmap_matches_identity_for_same_cids():
    stream_pdf, _ = _make_stream_cmap_pdf(
        _H_CMAP, b"BT /F0 20 Tf 1 0 0 1 2 2 Tm <00410042> Tj ET"
    )
    # Identity-H equivalent: the show string is already the CID pair 0001 0002.
    identity_pdf, _ = _make_stream_cmap_pdf(
        _H_CMAP, b"BT /F0 20 Tf 1 0 0 1 2 2 Tm <00410042> Tj ET"
    )
    stream_raster = _render(stream_pdf)
    identity_raster = _render(identity_pdf)
    stream_px = [stream_raster.get_pixel(x, y) for y in range(40) for x in range(40)]
    identity_px = [identity_raster.get_pixel(x, y) for y in range(40) for x in range(40)]
    assert stream_px == identity_px


def test_stream_cmap_unmapped_code_draws_nothing():
    # 0x0043 is outside the CMap's cidranges: no glyph, advance by DW.
    content = b"BT /F0 20 Tf 1 0 0 1 2 2 Tm <0043> Tj ET"
    pdf, _ = _make_stream_cmap_pdf(_H_CMAP, content)
    raster = _render(pdf)
    assert _count_black(raster, 0, 0, 40, 40) == 0


# The same CMap as _H_CMAP but declared vertical (WMode 1).
_V_CMAP = _H_CMAP.replace(b"/WMode 0 def", b"/WMode 1 def").replace(
    b"/Custom-H", b"/Custom-V"
)


def _ink_bbox(raster) -> tuple[int, int, int, int]:
    xs, ys = [], []
    for y in range(40):
        for x in range(40):
            r, g, b = raster.get_pixel(x, y)
            if r < 128 and g < 128 and b < 128:
                xs.append(x)
                ys.append(y)
    assert xs and ys, "expected some ink"
    return min(xs), min(ys), max(xs), max(ys)


def test_build_type0_font_marks_vertical_encoding():
    content = b"BT /F0 14 Tf 1 0 0 1 20 34 Tm <00410042> Tj ET"
    pdf, type0 = _make_stream_cmap_pdf(_V_CMAP, content)
    renderer = _PageRasterizer(
        pdf, 0, dpi=72.0, scale=1.0, background=(255, 255, 255), antialias=False
    )
    font = renderer._build_type0_font(type0)
    assert font is not None
    assert font.cmap.vertical is True
    assert font.vertical is True
    assert font.vertical_metrics_1000 is not None


def test_vertical_stream_cmap_stacks_glyphs_downward():
    show = b"<00410042> Tj"
    v_pdf, _ = _make_stream_cmap_pdf(
        _V_CMAP, b"BT /F0 14 Tf 1 0 0 1 20 34 Tm " + show + b" ET"
    )
    h_pdf, _ = _make_stream_cmap_pdf(
        _H_CMAP, b"BT /F0 14 Tf 1 0 0 1 6 20 Tm " + show + b" ET"
    )

    vx0, vy0, vx1, vy1 = _ink_bbox(_render(v_pdf))
    hx0, hy0, hx1, hy1 = _ink_bbox(_render(h_pdf))

    # Vertical writing stacks two glyphs into a tall, narrow ink region; the
    # horizontal run of the same two glyphs is wide and short.
    assert (vy1 - vy0) > (hy1 - hy0)  # taller when vertical
    assert (vx1 - vx0) < (hx1 - hx0)  # narrower when vertical
    assert (vy1 - vy0) > (vx1 - vx0)  # the vertical run is taller than it is wide

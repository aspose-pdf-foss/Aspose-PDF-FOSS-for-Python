"""Rendering Type0 fonts that use the bundled predefined CJK CMaps.

Extraction and editing already resolve the eight allowlisted Adobe CMaps; these
tests cover the page renderer, which previously drew boxes for any non-Identity
``/Encoding`` (rasterizer ``_build_type0_font`` returned ``None``). The renderer
now splits the show string on the CMap's codespaces, maps each code to a CID,
and fills the descendant font's real glyph outlines.

``90ms-RKSJ-H`` is used deliberately: it mixes single-byte and double-byte codes
in one string, so it exercises the variable-length codespace decoder. Exact
code/CID pairs are queried from the resolver so the tests do not hard-code the
bundle's contents.
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
from aspose_pdf.engine.predefined_cmaps import (
    CharacterCollection,
    resolve_predefined_cmap_encoding,
)
from aspose_pdf.engine.rasterizer import _GlyphFont, _PageRasterizer
from aspose_pdf.engine.simple_pdf import SimplePdf
from tests.test_glyph_rasterization import _box_glyph, _build_ttf, _count_black

# Adobe-Japan1 collection for the 90ms-RKSJ family.
_JAPAN1 = CharacterCollection("Adobe", "Japan1", 0)
_MIXED_CMAP = "90ms-RKSJ-H"

# Codes resolved live below; kept as module constants for the fixtures.
_SINGLE_BYTE_CODE = bytes.fromhex("41")  # ASCII 'A'
_DOUBLE_BYTE_CODE = bytes.fromhex("889F")  # a JIS lead+trail byte pair


def _cids_for(*codes: bytes) -> list[int]:
    encoding = resolve_predefined_cmap_encoding(_MIXED_CMAP, _JAPAN1)
    assert encoding is not None, "expected 90ms-RKSJ-H to be bundled"
    cids = []
    for code in codes:
        cid = encoding.cid_for(code)
        assert cid is not None, f"no CID for {code.hex()}"
        cids.append(cid)
    return cids


def _cid_to_gid_stream(cos, mapping: dict[int, int]):
    """A CIDToGIDMap stream (two big-endian bytes of GID per CID index)."""
    data = bytearray((max(mapping) + 1) * 2)
    for cid, gid in mapping.items():
        struct.pack_into(">H", data, cid * 2, gid)
    return cos.register_object(
        PdfStream(bytes(data), {PdfName("Length"): PdfNumber(len(data))})
    )


def _make_cjk_pdf(
    *,
    encoding: str,
    content: bytes,
    cid_gid: dict[int, int],
    widths: dict[int, float],
    ordering: str = "Japan1",
    glyph_count: int = 2,
) -> tuple[SimplePdf, PdfDictionary, PdfDictionary]:
    """Build a one-page PDF with an embedded CIDFontType2 under *encoding*.

    Returns the engine plus the resolved Type0 and CIDFont dictionaries so tests
    can render through the public API or call ``_build_type0_font`` directly.
    """
    glyphs = [b""] + [_box_glyph(100, 0, 900, 900) for _ in range(glyph_count)]
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
    width_items: list[object] = []
    for cid, width in sorted(widths.items()):
        width_items.extend([PdfNumber(cid), PdfArray([PdfNumber(width)])])
    cidfont = PdfDictionary(
        {
            PdfName("Type"): PdfName("Font"),
            PdfName("Subtype"): PdfName("CIDFontType2"),
            PdfName("BaseFont"): PdfName("AAAAAA+CJK"),
            PdfName("CIDSystemInfo"): PdfDictionary(
                {
                    PdfName("Registry"): PdfString(b"Adobe"),
                    PdfName("Ordering"): PdfString(ordering.encode("ascii")),
                    PdfName("Supplement"): PdfNumber(0),
                }
            ),
            PdfName("DW"): PdfNumber(1000),
            PdfName("W"): PdfArray(width_items),
            PdfName("CIDToGIDMap"): _cid_to_gid_stream(cos, cid_gid),
            PdfName("FontDescriptor"): descriptor,
        }
    )
    cidfont_ref = cos.register_object(cidfont)
    type0 = PdfDictionary(
        {
            PdfName("Type"): PdfName("Font"),
            PdfName("Subtype"): PdfName("Type0"),
            PdfName("BaseFont"): PdfName("AAAAAA+CJK"),
            PdfName("Encoding"): PdfName(encoding),
            PdfName("DescendantFonts"): PdfArray([cidfont_ref]),
        }
    )
    type0_ref = cos.register_object(type0)
    page = pdf._get_page_dict(0)
    page.mapping[PdfName("Resources")] = PdfDictionary(
        {PdfName("Font"): PdfDictionary({PdfName("F0"): type0_ref})}
    )
    return pdf, type0, cidfont


def _render(pdf: SimplePdf):
    doc = Document()
    doc._engine_pdf = pdf
    return doc.pages[0].render(antialias=False)


def _all_pixels(raster) -> list[tuple[int, int, int]]:
    return [raster.get_pixel(x, y) for y in range(40) for x in range(40)]


# ---------------------------------------------------------------------------
# Decode contract at the _GlyphFont seam (no font program needed)
# ---------------------------------------------------------------------------


def test_iter_glyphs_decodes_mixed_width_predefined_cmap():
    single_cid, double_cid = _cids_for(_SINGLE_BYTE_CODE, _DOUBLE_BYTE_CODE)
    assert single_cid != double_cid
    encoding = resolve_predefined_cmap_encoding(_MIXED_CMAP, _JAPAN1)

    widths = {single_cid: 500.0, double_cid: 800.0}
    gids = {single_cid: 1, double_cid: 2}
    font = _GlyphFont(
        outlines=None,
        code_to_gid=gids.get,
        width_1000=lambda cid: widths.get(cid, 1000.0),
        bytes_per_code=2,
        cmap=encoding,
        default_width_1000=1000.0,
    )

    glyphs = list(font.iter_glyphs(_SINGLE_BYTE_CODE + _DOUBLE_BYTE_CODE))

    # One single-byte unit then one double-byte unit: variable-length split,
    # code -> CID -> GID, CID-keyed widths, and the resolved CID last.
    assert glyphs == [
        (1, 500.0, False, single_cid),
        (2, 800.0, False, double_cid),
    ]


def test_iter_glyphs_applies_word_spacing_only_to_single_byte_32():
    encoding = resolve_predefined_cmap_encoding(_MIXED_CMAP, _JAPAN1)
    font = _GlyphFont(
        outlines=None,
        code_to_gid=lambda cid: None,
        width_1000=lambda cid: 1000.0,
        bytes_per_code=2,
        cmap=encoding,
        default_width_1000=1000.0,
    )

    (space_unit,) = list(font.iter_glyphs(b"\x20"))
    assert space_unit[2] is True  # single-byte code 32 receives word spacing

    # A double-byte code that happens to contain 0x20 must not trigger it.
    two_byte = list(font.iter_glyphs(_DOUBLE_BYTE_CODE))
    assert all(applies is False for _gid, _w, applies, _cid in two_byte)


def test_iter_glyphs_undecodable_code_draws_nothing_and_advances_by_dw():
    encoding = resolve_predefined_cmap_encoding(_MIXED_CMAP, _JAPAN1)
    font = _GlyphFont(
        outlines=None,
        code_to_gid=lambda cid: 7,
        width_1000=lambda cid: 111.0,
        bytes_per_code=2,
        cmap=encoding,
        default_width_1000=1234.0,
    )

    # 0x81 is a lead byte with no trailing byte: outside every single-byte
    # codespace and truncated as a double-byte code.
    (unit,) = list(font.iter_glyphs(b"\x81"))
    assert unit == (None, 1234.0, False, None)


# ---------------------------------------------------------------------------
# Full render pipeline
# ---------------------------------------------------------------------------


def test_render_predefined_cmap_matches_identity_for_same_cids():
    single_cid, double_cid = _cids_for(_SINGLE_BYTE_CODE, _DOUBLE_BYTE_CODE)
    cid_gid = {single_cid: 1, double_cid: 2}
    widths = {single_cid: 600.0, double_cid: 600.0}

    cjk_pdf, _, _ = _make_cjk_pdf(
        encoding=_MIXED_CMAP,
        content=b"BT /F0 20 Tf 1 0 0 1 2 2 Tm <"
        + (_SINGLE_BYTE_CODE + _DOUBLE_BYTE_CODE).hex().upper().encode("ascii")
        + b"> Tj ET",
        cid_gid=cid_gid,
        widths=widths,
    )
    identity_codes = struct.pack(">HH", single_cid, double_cid)
    identity_pdf, _, _ = _make_cjk_pdf(
        encoding="Identity-H",
        content=b"BT /F0 20 Tf 1 0 0 1 2 2 Tm <"
        + identity_codes.hex().upper().encode("ascii")
        + b"> Tj ET",
        cid_gid=cid_gid,
        widths=widths,
    )

    cjk_raster = _render(cjk_pdf)
    identity_raster = _render(identity_pdf)

    # Real glyphs were drawn (not blank, not boxes), and the predefined-CMap
    # path produced exactly what Identity-H produces for the same CID run.
    assert _count_black(cjk_raster, 0, 0, 40, 40) > 0
    assert _all_pixels(cjk_raster) == _all_pixels(identity_raster)


def test_build_type0_font_resolves_bundled_cmap_and_rejects_unbundled():
    single_cid, double_cid = _cids_for(_SINGLE_BYTE_CODE, _DOUBLE_BYTE_CODE)
    cid_gid = {single_cid: 1, double_cid: 2}
    widths = {single_cid: 600.0, double_cid: 600.0}
    content = b"BT /F0 20 Tf 1 0 0 1 2 2 Tm <41889F> Tj ET"

    bundled_pdf, bundled_type0, _ = _make_cjk_pdf(
        encoding=_MIXED_CMAP, content=content, cid_gid=cid_gid, widths=widths
    )
    renderer = _PageRasterizer(
        bundled_pdf, 0, dpi=72.0, scale=1.0, background=(255, 255, 255),
        antialias=False,
    )
    font = renderer._build_type0_font(bundled_type0)
    assert font is not None
    assert font.cmap is not None

    # A real Adobe name that is not in the eight-CMap allowlist stays opaque, so
    # the renderer keeps drawing boxes rather than guessing an approximate map.
    unbundled_pdf, unbundled_type0, _ = _make_cjk_pdf(
        encoding="UniJIS-UCS2-H", content=content, cid_gid=cid_gid, widths=widths
    )
    unbundled_renderer = _PageRasterizer(
        unbundled_pdf, 0, dpi=72.0, scale=1.0, background=(255, 255, 255),
        antialias=False,
    )
    assert unbundled_renderer._build_type0_font(unbundled_type0) is None


def test_render_unbundled_cmap_does_not_crash_and_differs_from_glyphs():
    single_cid, double_cid = _cids_for(_SINGLE_BYTE_CODE, _DOUBLE_BYTE_CODE)
    cid_gid = {single_cid: 1, double_cid: 2}
    widths = {single_cid: 600.0, double_cid: 600.0}
    content = b"BT /F0 20 Tf 1 0 0 1 2 2 Tm <41889F> Tj ET"

    bundled_pdf, _, _ = _make_cjk_pdf(
        encoding=_MIXED_CMAP, content=content, cid_gid=cid_gid, widths=widths
    )
    unbundled_pdf, _, _ = _make_cjk_pdf(
        encoding="UniJIS-UCS2-H", content=content, cid_gid=cid_gid, widths=widths
    )

    bundled = _all_pixels(_render(bundled_pdf))
    unbundled = _all_pixels(_render(unbundled_pdf))

    # The box fallback still renders something, but not the resolved glyphs.
    assert bundled != unbundled


def test_build_type0_font_marks_bundled_vertical_cmap():
    # A bundled *vertical* predefined CMap now drives vertical positioning
    # (`/W2`/`/DW2` displacement + position vector), not just glyph resolution.
    single_cid, double_cid = _cids_for(_SINGLE_BYTE_CODE, _DOUBLE_BYTE_CODE)
    cid_gid = {single_cid: 1, double_cid: 2}
    widths = {single_cid: 600.0, double_cid: 600.0}
    pdf, type0, _ = _make_cjk_pdf(
        encoding="90ms-RKSJ-V",
        content=b"BT /F0 20 Tf <41889F> Tj ET",
        cid_gid=cid_gid,
        widths=widths,
    )
    renderer = _PageRasterizer(
        pdf, 0, dpi=72.0, scale=1.0, background=(255, 255, 255), antialias=False
    )
    font = renderer._build_type0_font(type0)
    assert font is not None
    assert font.vertical is True
    assert font.vertical_metrics_1000 is not None

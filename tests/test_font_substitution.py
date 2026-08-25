"""Drawing non-embedded fonts with faces the caller makes available.

The renderer ships substitute faces for the Latin Standard 14 plus Symbol and
ZapfDingbats. Anything else with no embedded program -- a CJK face, a
non-embedded Wingdings, a corporate text font -- had no outlines to draw and
fell back to glyph boxes, and a composite (Type0) font had no substitute path
at all: ``_build_type0_font`` returned ``None`` the moment the descendant
carried no ``/FontFile2`` or ``/FontFile3``.

``FontSubstitutionOptions`` opens that up. These tests cover the three
resolution steps (by name, by character-collection preference, by ``cmap``
coverage), both font flavours, and the guarantee that rendering without options
is unchanged.

Every fixture font is synthesised here, so nothing depends on what the machine
running the suite happens to have installed. Glyph 1 of the synthetic faces is
a *low bar* filling the bottom 30% of the em: a real outline fill leaves the
upper cell empty, while the box fallback paints the whole cell, which is what
separates "substituted" from "still boxed" in the raster assertions.
"""

from __future__ import annotations

import struct

import pytest

from aspose_pdf import Document, FontSubstitutionOptions
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfDictionary,
    PdfName,
    PdfNumber,
    PdfStream,
    PdfString,
)
from aspose_pdf.engine.font_resolver import FontResolver, resolver_for
from aspose_pdf.engine.predefined_cmaps import (
    CharacterCollection,
    cid_to_unicode_text,
    resolve_predefined_cmap_encoding,
)
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.exceptions import PdfValidationException
from tests.test_glyph_rasterization import (
    _box_glyph,
    _build_ttf,
    _cmap_table,
    _count_black,
)

_HAN_ONE = 0x4E00  # 一
_HAN_TWO = 0x4E8C  # 二
_GB1 = CharacterCollection("Adobe", "GB1", 2)


# ---------------------------------------------------------------------------
# Synthetic font fixtures
# ---------------------------------------------------------------------------


def _cmap_format4(mapping: dict[int, int]) -> bytes:
    """A ``cmap`` format 4 subtable, one segment per mapped scalar."""
    codes = sorted(mapping)
    starts = [*codes, 0xFFFF]
    ends = [*codes, 0xFFFF]
    deltas = [(mapping[c] - c) & 0xFFFF for c in codes] + [1]
    seg_count = len(starts)
    body = (
        b"".join(struct.pack(">H", value) for value in ends)
        + struct.pack(">H", 0)  # reservedPad
        + b"".join(struct.pack(">H", value) for value in starts)
        + b"".join(struct.pack(">H", value) for value in deltas)
        + b"".join(struct.pack(">H", 0) for _ in range(seg_count))
    )
    length = 14 + len(body)
    header = struct.pack(
        ">HHHHHHH", 4, length, 0, seg_count * 2, 2, 0, seg_count * 2 - 2
    )
    return header + body


def _low_bar_font(mapping: dict[int, int] | None = None) -> bytes:
    """A face whose glyph 1 is a low bar and glyph 2 a full box."""
    glyphs = [b"", _box_glyph(0, 0, 1000, 300), _box_glyph(0, 0, 1000, 1000)]
    extra = {}
    if mapping:
        extra["cmap"] = _cmap_table(3, 1, _cmap_format4(mapping))
    return _build_ttf(glyphs, extra_tables=extra or None)


def _rebase_sfnt(face: bytes, base: int) -> bytes:
    """Shift every table offset in *face* by *base* for use inside a TTC."""
    out = bytearray(face)
    num_tables = struct.unpack_from(">H", out, 4)[0]
    for i in range(num_tables):
        record = 12 + 16 * i
        offset = struct.unpack_from(">I", out, record + 8)[0]
        struct.pack_into(">I", out, record + 8, offset + base)
    return bytes(out)


def _build_ttc(faces: list[bytes]) -> bytes:
    """Pack *faces* into a TrueType Collection with fixed-up directories."""
    base = 12 + 4 * len(faces)
    offsets: list[int] = []
    body = bytearray()
    for face in faces:
        offsets.append(base)
        body += _rebase_sfnt(face, base)
        base += len(face)
    header = struct.pack(">4sHHI", b"ttcf", 1, 0, len(faces))
    header += b"".join(struct.pack(">I", offset) for offset in offsets)
    return bytes(header + body)


# ---------------------------------------------------------------------------
# PDF fixtures
# ---------------------------------------------------------------------------


def _simple_font_pdf(base_font: str, content: bytes) -> SimplePdf:
    """A page whose only font is a non-embedded simple ``/TrueType``."""
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 40, 40)]
    pdf.page_contents = [content]
    pdf._ensure_cos()
    cos = pdf._cos_doc
    descriptor = cos.register_object(
        PdfDictionary(
            {
                PdfName("Type"): PdfName("FontDescriptor"),
                PdfName("FontName"): PdfName(base_font),
                PdfName("Flags"): PdfNumber(32),
            }
        )
    )
    font = cos.register_object(
        PdfDictionary(
            {
                PdfName("Type"): PdfName("Font"),
                PdfName("Subtype"): PdfName("TrueType"),
                PdfName("BaseFont"): PdfName(base_font),
                PdfName("FirstChar"): PdfNumber(65),
                PdfName("LastChar"): PdfNumber(65),
                PdfName("Widths"): PdfArray([PdfNumber(1000)]),
                PdfName("FontDescriptor"): descriptor,
            }
        )
    )
    page = pdf._get_page_dict(0)
    page.mapping[PdfName("Resources")] = PdfDictionary(
        {PdfName("Font"): PdfDictionary({PdfName("F0"): font})}
    )
    return pdf


_TO_UNICODE = b"""/CIDInit /ProcSet findresource begin
12 dict begin
begincmap
/CMapName /Custom-UCS2 def
/CMapType 2 def
1 begincodespacerange
<0000> <FFFF>
endcodespacerange
1 beginbfchar
<0001> <4E00>
endbfchar
endcmap
CMapName currentdict /CMap defineresource pop
end
end
"""


def _composite_font_pdf(
    content: bytes,
    *,
    base_font: str = "SimSun",
    encoding: str = "Identity-H",
    ordering: str = "GB1",
    to_unicode: bytes | None = None,
    subtype: str = "CIDFontType2",
) -> SimplePdf:
    """A page whose only font is a composite font with **no** embedded program."""
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 40, 40)]
    pdf.page_contents = [content]
    pdf._ensure_cos()
    cos = pdf._cos_doc
    descriptor = cos.register_object(
        PdfDictionary(
            {
                PdfName("Type"): PdfName("FontDescriptor"),
                PdfName("FontName"): PdfName(base_font),
                PdfName("Flags"): PdfNumber(4),
            }
        )
    )
    cidfont = cos.register_object(
        PdfDictionary(
            {
                PdfName("Type"): PdfName("Font"),
                PdfName("Subtype"): PdfName(subtype),
                PdfName("BaseFont"): PdfName(base_font),
                PdfName("CIDSystemInfo"): PdfDictionary(
                    {
                        PdfName("Registry"): PdfString(b"Adobe"),
                        PdfName("Ordering"): PdfString(ordering.encode("ascii")),
                        PdfName("Supplement"): PdfNumber(2),
                    }
                ),
                PdfName("DW"): PdfNumber(1000),
                PdfName("FontDescriptor"): descriptor,
            }
        )
    )
    entries = {
        PdfName("Type"): PdfName("Font"),
        PdfName("Subtype"): PdfName("Type0"),
        PdfName("BaseFont"): PdfName(base_font),
        PdfName("Encoding"): PdfName(encoding),
        PdfName("DescendantFonts"): PdfArray([cidfont]),
    }
    if to_unicode is not None:
        entries[PdfName("ToUnicode")] = cos.register_object(
            PdfStream(
                to_unicode, {PdfName("Length"): PdfNumber(len(to_unicode))}
            )
        )
    font = cos.register_object(PdfDictionary(entries))
    page = pdf._get_page_dict(0)
    page.mapping[PdfName("Resources")] = PdfDictionary(
        {PdfName("Font"): PdfDictionary({PdfName("F0"): font})}
    )
    return pdf


def _render(pdf: SimplePdf, **kwargs):
    doc = Document()
    doc._engine_pdf = pdf
    return doc.pages[0].render(antialias=False, **kwargs)


def _drew_real_outline(raster) -> bool:
    """Low bar filled and upper cell empty -- an outline, not a glyph box."""
    return _count_black(raster, 8, 27, 33, 35) > 30 and _count_black(
        raster, 8, 9, 33, 19
    ) == 0


# ---------------------------------------------------------------------------
# Simple fonts
# ---------------------------------------------------------------------------


def test_simple_font_draws_with_a_face_from_a_directory(tmp_path):
    (tmp_path / "AcmeText.ttf").write_bytes(_low_bar_font({0x41: 1}))
    pdf = _simple_font_pdf("AcmeText", b"BT /F0 30 Tf 1 0 0 1 5 5 Tm (A) Tj ET")

    raster = _render(
        pdf, font_substitution=FontSubstitutionOptions([tmp_path])
    )

    assert _drew_real_outline(raster)


def test_simple_font_without_options_keeps_the_bundled_substitute(tmp_path):
    # The same page with no font sources: "AcmeText" carries no family signal
    # and the descriptor is non-symbolic, so the bundled sans face draws an 'A'
    # -- a real glyph, but a full-height one, not the fixture's low bar.
    (tmp_path / "AcmeText.ttf").write_bytes(_low_bar_font({0x41: 1}))
    pdf = _simple_font_pdf("AcmeText", b"BT /F0 30 Tf 1 0 0 1 5 5 Tm (A) Tj ET")

    raster = _render(pdf)

    assert not _drew_real_outline(raster)
    assert _count_black(raster, 8, 9, 33, 35) > 0  # the bundled 'A' is drawn


def test_simple_font_takes_a_program_supplied_directly():
    pdf = _simple_font_pdf("AcmeText", b"BT /F0 30 Tf 1 0 0 1 5 5 Tm (A) Tj ET")
    options = FontSubstitutionOptions(
        fonts={"AcmeText": _low_bar_font({0x41: 1})}
    )

    assert _drew_real_outline(_render(pdf, font_substitution=options))


def test_symbolic_simple_font_uses_the_faces_symbol_cmap(tmp_path):
    # A (3,0) symbol cmap keyed at 0xF000 + code, the way Wingdings ships.
    font = _build_ttf(
        [b"", _box_glyph(0, 0, 1000, 300)],
        extra_tables={"cmap": _cmap_table(3, 0, _cmap_format4({0xF041: 1}))},
    )
    (tmp_path / "Dingfont.ttf").write_bytes(font)
    pdf = _simple_font_pdf("Dingfont", b"BT /F0 30 Tf 1 0 0 1 5 5 Tm (A) Tj ET")

    raster = _render(
        pdf, font_substitution=FontSubstitutionOptions([tmp_path])
    )

    assert _drew_real_outline(raster)


# ---------------------------------------------------------------------------
# Composite fonts
# ---------------------------------------------------------------------------


def test_composite_font_without_a_program_draws_boxes_by_default():
    pdf = _composite_font_pdf(
        b"BT /F0 30 Tf 1 0 0 1 5 5 Tm <0001> Tj ET", to_unicode=_TO_UNICODE
    )

    raster = _render(pdf)

    # Unchanged behaviour: a filled placeholder box, not an outline.
    assert not _drew_real_outline(raster)
    assert _count_black(raster, 8, 9, 33, 19) > 0


def test_composite_identity_font_resolves_through_to_unicode(tmp_path):
    (tmp_path / "SimSun.ttf").write_bytes(_low_bar_font({_HAN_ONE: 1}))
    pdf = _composite_font_pdf(
        b"BT /F0 30 Tf 1 0 0 1 5 5 Tm <0001> Tj ET", to_unicode=_TO_UNICODE
    )

    raster = _render(
        pdf, font_substitution=FontSubstitutionOptions([tmp_path])
    )

    # CID 1 -> ToUnicode U+4E00 -> the face's cmap -> glyph 1 (the low bar).
    assert _drew_real_outline(raster)


def test_composite_predefined_cmap_resolves_through_adobe_cid_to_unicode(tmp_path):
    # No /ToUnicode at all: the code goes through the bundled UniGB-UCS2-H CMap
    # to a GB1 CID, and Adobe's GB1 CID-to-Unicode table supplies the scalar.
    encoding = resolve_predefined_cmap_encoding("UniGB-UCS2-H", _GB1)
    assert encoding is not None
    cid = encoding.cid_for(struct.pack(">H", _HAN_ONE))
    assert cid is not None
    assert cid_to_unicode_text("GB1", cid) == chr(_HAN_ONE)

    (tmp_path / "SimSun.ttf").write_bytes(_low_bar_font({_HAN_ONE: 1}))
    pdf = _composite_font_pdf(
        b"BT /F0 30 Tf 1 0 0 1 5 5 Tm <4E00> Tj ET",
        encoding="UniGB-UCS2-H",
        ordering="GB1",
    )

    raster = _render(
        pdf, font_substitution=FontSubstitutionOptions([tmp_path])
    )

    assert _drew_real_outline(raster)


def test_composite_font_falls_back_to_a_preferred_family(tmp_path):
    # The document names a face the machine does not have; a well-known family
    # for its character collection stands in.
    (tmp_path / "Microsoft YaHei.ttf").write_bytes(_low_bar_font({_HAN_ONE: 1}))
    pdf = _composite_font_pdf(
        b"BT /F0 30 Tf 1 0 0 1 5 5 Tm <0001> Tj ET",
        base_font="FZShuSong-Z01",
        to_unicode=_TO_UNICODE,
    )

    raster = _render(
        pdf, font_substitution=FontSubstitutionOptions([tmp_path])
    )

    assert _drew_real_outline(raster)


def test_composite_font_falls_back_to_any_face_that_covers_the_text(tmp_path):
    # Neither the document's name nor any preferred family is installed, so the
    # last step picks a face whose cmap actually carries the scalars.
    (tmp_path / "UnknownHan.ttf").write_bytes(
        _low_bar_font({_HAN_ONE: 1, _HAN_TWO: 2})
    )
    pdf = _composite_font_pdf(
        b"BT /F0 30 Tf 1 0 0 1 5 5 Tm <0001> Tj ET",
        base_font="FZShuSong-Z01",
        to_unicode=_TO_UNICODE,
    )

    raster = _render(
        pdf, font_substitution=FontSubstitutionOptions([tmp_path])
    )

    assert _drew_real_outline(raster)


def test_composite_font_keeps_boxes_when_nothing_covers_the_text(tmp_path):
    # A Latin-only face must not be pressed into service for Han text.
    (tmp_path / "LatinOnly.ttf").write_bytes(_low_bar_font({0x41: 1}))
    pdf = _composite_font_pdf(
        b"BT /F0 30 Tf 1 0 0 1 5 5 Tm <0001> Tj ET",
        base_font="FZShuSong-Z01",
        to_unicode=_TO_UNICODE,
    )

    raster = _render(
        pdf, font_substitution=FontSubstitutionOptions([tmp_path])
    )

    assert not _drew_real_outline(raster)


def test_composite_cidfonttype0_without_a_program_substitutes(tmp_path):
    (tmp_path / "SimSun.ttf").write_bytes(_low_bar_font({_HAN_ONE: 1}))
    pdf = _composite_font_pdf(
        b"BT /F0 30 Tf 1 0 0 1 5 5 Tm <0001> Tj ET",
        to_unicode=_TO_UNICODE,
        subtype="CIDFontType0",
    )

    raster = _render(
        pdf, font_substitution=FontSubstitutionOptions([tmp_path])
    )

    assert _drew_real_outline(raster)


def test_substituted_composite_font_keeps_the_pdf_advance_widths(tmp_path):
    """A substituted face changes which glyphs are drawn, never where."""
    (tmp_path / "SimSun.ttf").write_bytes(_low_bar_font({_HAN_ONE: 1}))
    pdf = _composite_font_pdf(
        b"BT /F0 30 Tf 1 0 0 1 5 5 Tm <0001> Tj ET", to_unicode=_TO_UNICODE
    )
    doc = Document()
    doc._engine_pdf = pdf
    from aspose_pdf.engine.rasterizer import _PageRasterizer

    renderer = _PageRasterizer(
        pdf,
        0,
        dpi=72.0,
        scale=1.0,
        background=(255, 255, 255),
        font_substitution=FontSubstitutionOptions([tmp_path]),
    )
    resources = pdf._get_page_dict(0).mapping[PdfName("Resources")]
    font = renderer._resolve_glyph_font("F0", resources)

    assert font is not None
    # /DW is 1000 and the font carries no /W: the advance comes from the PDF,
    # not from the substitute's own hmtx.
    assert font.width_1000(1) == 1000.0
    assert font.code_to_gid(1) == 1


# ---------------------------------------------------------------------------
# Resolver behaviour
# ---------------------------------------------------------------------------


def test_resolver_extracts_a_face_from_a_collection(tmp_path):
    ttc = _build_ttc([_low_bar_font({0x41: 1}), _low_bar_font({0x42: 2})])
    (tmp_path / "Family.ttc").write_bytes(ttc)
    resolver = FontResolver(directories=(tmp_path,))

    face = resolver.by_coverage((0x42,))

    assert face is not None
    assert face.data[:4] != b"ttcf"  # a standalone SFNT, not the collection
    from aspose_pdf.engine.font_subset import read_unicode_cmap
    from aspose_pdf.engine.glyph_outlines import TrueTypeOutlines

    gid = read_unicode_cmap(face.data).get(0x42)
    assert gid == 2
    outlines = TrueTypeOutlines(face.data)
    assert outlines.ok
    assert outlines.outline(gid)  # the lifted face still yields real contours


def test_collection_extraction_matches_the_strict_embedding_path(tmp_path):
    """The renderer's fast extractor differs from the strict one only in checksums.

    ``font_authoring._select_sfnt_face`` recomputes every table checksum and
    rewrites ``head.checkSumAdjustment``, which costs seconds on a 50 MB CJK
    collection; the renderer copies the collection's own values instead. The
    table set and every table body must still agree exactly, and
    ``checkSumAdjustment`` (``head`` bytes 8..12) must be the *only* divergence
    -- nothing the outline, cmap or metric parsers read.
    """
    from aspose_pdf.engine.font_authoring import _select_sfnt_face
    from aspose_pdf.engine.font_resolver import _extract_collection_face

    ttc = _build_ttc([_low_bar_font({0x41: 1}), _low_bar_font({0x42: 2})])

    for index in (0, 1):
        fast = _extract_collection_face(ttc, index)
        strict = _select_sfnt_face(ttc, index)
        assert fast is not None
        fast_tables = _sfnt_tables(fast)
        strict_tables = _sfnt_tables(strict)
        assert fast_tables.keys() == strict_tables.keys()
        for tag, body in fast_tables.items():
            if tag == "head":
                assert body[:8] == strict_tables[tag][:8]
                assert body[12:] == strict_tables[tag][12:]
            else:
                assert body == strict_tables[tag], tag

    assert _extract_collection_face(ttc, 2) is None
    assert _extract_collection_face(b"not a collection", 0) is None


def _sfnt_tables(data: bytes) -> dict[str, bytes]:
    num_tables = struct.unpack_from(">H", data, 4)[0]
    tables = {}
    for i in range(num_tables):
        record = 12 + 16 * i
        tag = data[record : record + 4].decode("latin-1")
        offset, length = struct.unpack_from(">II", data, record + 8)
        tables[tag] = data[offset : offset + length]
    return tables


def test_resolver_prefers_the_closest_style(tmp_path):
    (tmp_path / "Acme.ttf").write_bytes(_low_bar_font({0x41: 1}))
    (tmp_path / "Acme-Bold.ttf").write_bytes(_low_bar_font({0x41: 2}))
    resolver = FontResolver(directories=(tmp_path,))

    from aspose_pdf.engine.font_subset import read_unicode_cmap

    regular = resolver.by_name("Acme")
    bold = resolver.by_name("Acme-Bold")

    assert regular is not None and bold is not None
    assert read_unicode_cmap(regular.data).get(0x41) == 1
    assert read_unicode_cmap(bold.data).get(0x41) == 2


def test_resolver_ignores_files_that_are_not_fonts(tmp_path):
    (tmp_path / "notes.txt").write_text("not a font")
    (tmp_path / "broken.ttf").write_bytes(b"\x00\x01\x00\x00 truncated")
    resolver = FontResolver(directories=(tmp_path,))

    assert resolver.by_name("broken") is None
    assert resolver.by_coverage((0x41,)) is None


def test_resolver_is_reused_across_renders():
    options = FontSubstitutionOptions()

    assert resolver_for(options) is resolver_for(options)
    assert resolver_for(None) is None


def test_missing_directory_is_skipped(tmp_path):
    resolver = FontResolver(directories=(tmp_path / "nope",))

    assert resolver.by_name("Anything") is None


# ---------------------------------------------------------------------------
# Public options and the document setting
# ---------------------------------------------------------------------------


def test_options_reject_a_program_that_is_not_bytes():
    with pytest.raises(PdfValidationException):
        FontSubstitutionOptions(fonts={"Acme": "not bytes"})


def test_options_accept_a_single_directory(tmp_path):
    options = FontSubstitutionOptions(tmp_path)

    assert options.directories == (tmp_path,)
    assert options.use_system_fonts is False
    assert FontSubstitutionOptions.system().use_system_fonts is True


def test_document_setting_applies_to_every_render_path(tmp_path):
    (tmp_path / "SimSun.ttf").write_bytes(_low_bar_font({_HAN_ONE: 1}))
    pdf = _composite_font_pdf(
        b"BT /F0 30 Tf 1 0 0 1 5 5 Tm <0001> Tj ET", to_unicode=_TO_UNICODE
    )
    doc = Document()
    doc._engine_pdf = pdf
    doc.font_substitution = FontSubstitutionOptions([tmp_path])

    assert doc.font_substitution is not None
    assert _drew_real_outline(doc.render_page(0, antialias=False))
    # save_page_as_image renders through the same setting.
    out = doc.save_page_as_image(0, tmp_path / "page.png", antialias=False)
    assert out.exists() and out.stat().st_size > 0


def test_document_setting_rejects_a_foreign_value():
    doc = Document()
    doc._engine_pdf = _simple_font_pdf("AcmeText", b"")

    with pytest.raises(PdfValidationException):
        doc.font_substitution = "/usr/share/fonts"

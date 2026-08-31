"""Tests for CFF2 glyph erasure (``subset_cff2``).

CFF2 is the same erasure as CFF with a different container, and one substantive
difference: it removed ``endchar``. A charstring ends where its INDEX entry
does, so an erased CFF2 glyph is the *empty string*, not a one-byte operator,
and its advance width is unaffected because CFF2 keeps widths in ``hmtx``.

Fixtures come from fontTools -- the same encoder real CFF2 fonts come from --
which then serves as the oracle: kept glyphs must draw identically afterwards,
and a variable font must still instantiate. One fixture is built by hand
instead, because ``setupCFF2`` cannot emit an FDSelect and there is no other way
to cover a font whose glyphs are split across two Font DICTs.
"""

from __future__ import annotations

import io
import struct

import pytest

pytest.importorskip("fontTools")

from fontTools.designspaceLib import (
    AxisDescriptor,
    DesignSpaceDocument,
    SourceDescriptor,
)
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.recordingPen import RecordingPen
from fontTools.pens.t2CharStringPen import T2CharStringPen
from fontTools.ttLib import TTFont
from fontTools.varLib import build as varlib_build

from aspose_pdf import OptimizationOptions
from aspose_pdf.engine.cff_outlines import CffOutlines
from aspose_pdf.engine.font_subset_cff import (
    _OP_CHARSTRINGS,
    _build_index2,
    _dict_int,
    _encode_int,
    _parse_dict,
    _read_index2,
    _sfnt_tables,
    is_cff2,
    subset_cff,
    subset_cff2,
)

_GLYPHS = 6
_NAMES = [".notdef"] + [f"g{i}" for i in range(1, _GLYPHS)]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _toothed(width: int, height: int = 700, teeth: int = 20, *, cff2: bool = True):
    """A saw-toothed box: enough charstring bytes that erasing one shows up."""
    pen = T2CharStringPen(None if cff2 else 600, None, CFF2=cff2)
    pen.moveTo((50, 0))
    pen.lineTo((50 + width, 0))
    pen.lineTo((50 + width, height))
    for i in range(teeth):
        x = 50 + width - (width * (i + 1)) / teeth
        pen.lineTo((x + 7, height - 40 if i % 2 else height))
        pen.lineTo((x, height))
    pen.lineTo((50, height))
    pen.closePath()
    return pen.getCharString()


def _base_builder(scale: float, *, cff2: bool):
    fb = FontBuilder(unitsPerEm=1000, isTTF=False)
    fb.setupGlyphOrder(_NAMES)
    fb.setupCharacterMap({0x41 + i: name for i, name in enumerate(_NAMES[1:])})
    charstrings = {".notdef": _toothed(60, cff2=cff2)}
    for i in range(1, _GLYPHS):
        charstrings[f"g{i}"] = _toothed(int(100 * i * scale), cff2=cff2)
    if cff2:
        fb.setupCFF2(charstrings)
    else:
        fb.setupCFF("SubTest2", {}, charstrings, {})
    fb.setupHorizontalMetrics({name: (600 + 7 * i, 50) for i, name in enumerate(_NAMES)})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "SubTest2", "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()
    return fb


def _build_cff2_otf() -> bytes:
    buf = io.BytesIO()
    _base_builder(1.0, cff2=True).save(buf)
    return buf.getvalue()


def _build_variable_cff2_otf() -> bytes:
    """A two-master variable CFF2, so the ItemVariationStore has to relocate."""
    doc = DesignSpaceDocument()
    axis = AxisDescriptor()
    axis.tag, axis.name = "wght", "Weight"
    axis.minimum, axis.default, axis.maximum = 100, 100, 900
    doc.addAxis(axis)
    for scale, location in ((1.0, 100), (3.0, 900)):
        buf = io.BytesIO()
        _base_builder(scale, cff2=False).save(buf)
        source = SourceDescriptor()
        source.font = TTFont(buf)
        source.location = {"Weight": location}
        if location == 100:
            source.copyLib = source.copyInfo = True
            source.copyGroups = source.copyFeatures = True
        doc.addSource(source)
    variable, _, _ = varlib_build(doc)
    out = io.BytesIO()
    variable.save(out)
    return out.getvalue()


def _cff2_table(data: bytes) -> bytes:
    offset, length = _sfnt_tables(data)["CFF2"]
    return data[offset : offset + length]


def _charstrings(cff2_table: bytes) -> list[bytes]:
    header_size = cff2_table[2]
    top_length = struct.unpack_from(">H", cff2_table, 3)[0]
    entries = _parse_dict(cff2_table[header_size : header_size + top_length])
    items, _end = _read_index2(cff2_table, _dict_int(entries, _OP_CHARSTRINGS))
    return items


def _outlines(data: bytes) -> dict[str, list]:
    font = TTFont(io.BytesIO(data))
    glyph_set = font.getGlyphSet()
    result = {}
    for name in font.getGlyphOrder():
        pen = RecordingPen()
        glyph_set[name].draw(pen)
        result[name] = pen.value
    return result


# ---------------------------------------------------------------------------
# Core erasure
# ---------------------------------------------------------------------------


def test_erased_charstrings_are_empty_not_endchar():
    # CFF2 removed endchar, so the empty glyph is a zero-length charstring.
    data = _build_cff2_otf()
    out = subset_cff2(data, {1, 3})
    assert out is not None
    got = _charstrings(_cff2_table(out))
    assert got[2] == b"" and got[4] == b"" and got[5] == b""
    assert b"\x0e" not in got[2]  # not an endchar, not anything


def test_kept_glyphs_draw_identically():
    data = _build_cff2_otf()
    out = subset_cff2(data, {1, 3})
    before, after = _outlines(data), _outlines(out)
    for name in (".notdef", "g1", "g3"):
        assert after[name] == before[name]
    for name in ("g2", "g4", "g5"):
        assert after[name] == []


def test_subset_is_smaller():
    data = _build_cff2_otf()
    out = subset_cff2(data, {1})
    assert out is not None
    assert len(out) < len(data)


def test_glyph_order_and_cmap_survive():
    # Glyph numbering is preserved, so the PDF's own CID->GID mapping stays valid.
    data = _build_cff2_otf()
    out = subset_cff2(data, {1, 3})
    original, subset = TTFont(io.BytesIO(data)), TTFont(io.BytesIO(out))
    assert subset.getGlyphOrder() == original.getGlyphOrder()
    assert subset.getBestCmap() == original.getBestCmap()


def test_erased_glyph_keeps_its_advance_width():
    # The width lives in hmtx, which erasure never touches -- so an erased glyph
    # still advances the text position exactly as it did.
    data = _build_cff2_otf()
    out = subset_cff2(data, {1})
    original, subset = TTFont(io.BytesIO(data)), TTFont(io.BytesIO(out))
    assert dict(subset["hmtx"].metrics) == dict(original["hmtx"].metrics)
    assert subset["hmtx"]["g4"][0] == original["hmtx"]["g4"][0]


def test_returns_none_when_nothing_to_erase():
    data = _build_cff2_otf()
    assert subset_cff2(data, set(range(_GLYPHS))) is None


def test_keeping_glyph_zero_is_implicit():
    data = _build_cff2_otf()
    out = subset_cff2(data, set())
    assert out is not None
    got = _charstrings(_cff2_table(out))
    assert got[0] != b""  # .notdef survives even when not asked for
    assert all(item == b"" for item in got[1:])


def test_subset_is_defensive():
    assert subset_cff2(b"", {1}) is None
    assert subset_cff2(b"not a font at all", {1}) is None
    assert subset_cff2(b"\x02\x00\x05\x00", {1}) is None  # header only, truncated
    assert subset_cff2(b"\x01\x00\x04\x02rest", {1}) is None  # CFF1, not CFF2


def test_a_huge_index_count_is_rejected_rather_than_walked():
    # A CFF2 INDEX count is a uint32, so a corrupt one can ask for four billion
    # offsets out of a few bytes. Bounding it keeps a malformed font from
    # stalling the optimizer.
    blob = b"\xff\xff\xff\xf0\x04" + b"\x00" * 40
    with pytest.raises(ValueError):
        _read_index2(blob, 0)


def test_subset_survives_a_corrupt_top_dict_length():
    data = bytearray(_build_cff2_otf())
    offset, _length = _sfnt_tables(bytes(data))["CFF2"]
    struct.pack_into(">H", data, offset + 3, 0xFFF0)  # nonsense topDictLength
    assert subset_cff2(bytes(data), {1}) is None


def test_subset_rejects_an_sfnt_without_a_cff2_table():
    buf = io.BytesIO()
    _base_builder(1.0, cff2=False).save(buf)  # OpenType/CFF, no CFF2
    assert subset_cff2(buf.getvalue(), {1}) is None


def test_cff1_subsetter_still_refuses_cff2():
    assert subset_cff(_cff2_table(_build_cff2_otf()), {1}) is None


def test_is_cff2_recognises_bare_and_wrapped_programs():
    data = _build_cff2_otf()
    assert is_cff2(data)  # sfnt wrapper
    assert is_cff2(_cff2_table(data))  # bare table
    buf = io.BytesIO()
    _base_builder(1.0, cff2=False).save(buf)
    assert not is_cff2(buf.getvalue())  # OpenType/CFF
    assert not is_cff2(b"\x01\x00\x04\x02")  # bare CFF1
    assert not is_cff2(b"")


def test_bare_cff2_table_subsets_to_a_bare_cff2_table():
    table = _cff2_table(_build_cff2_otf())
    out = subset_cff2(table, {1})
    assert out is not None
    assert out[0] == 2  # still a bare CFF2, not re-wrapped in an sfnt
    assert _sfnt_tables(out) is None


# ---------------------------------------------------------------------------
# Variable CFF2: the ItemVariationStore has to move with everything else
# ---------------------------------------------------------------------------


def test_variable_cff2_still_instantiates_after_subsetting():
    from fontTools.varLib.instancer import instantiateVariableFont

    data = _build_variable_cff2_otf()
    out = subset_cff2(data, {1, 3})
    assert out is not None

    def draw(blob, weight):
        font = instantiateVariableFont(
            TTFont(io.BytesIO(blob)), {"wght": weight}, inplace=False
        )
        glyph_set = font.getGlyphSet()
        drawn = {}
        for name in font.getGlyphOrder():
            pen = RecordingPen()
            glyph_set[name].draw(pen)
            drawn[name] = pen.value
        return drawn

    for weight in (100, 300, 500, 900):
        before, after = draw(data, weight), draw(out, weight)
        for name in (".notdef", "g1", "g3"):
            assert after[name] == before[name], f"{name} moved at wght {weight}"
        for name in ("g2", "g4", "g5"):
            assert after[name] == []


def test_variable_cff2_interpolates_for_our_own_reader_after_subsetting():
    data = _build_variable_cff2_otf()
    out = subset_cff2(data, {1})
    widths = []
    for weight in (100.0, 900.0):
        outlines = CffOutlines(out, variation={"wght": weight})
        xs = [x for contour in outlines.outline(1) for x, _ in contour]
        widths.append(max(xs) - min(xs))
    # The masters are 1x and 3x, so the light and heavy instances differ.
    assert widths[1] == pytest.approx(widths[0] * 3, rel=0.02)


# ---------------------------------------------------------------------------
# FDSelect, several Font DICTs, and local subrs
# ---------------------------------------------------------------------------


def _number(value: int) -> bytes:
    """A Type 2 charstring integer operand."""
    if -107 <= value <= 107:
        return bytes([value + 139])
    return b"\x1c" + struct.pack(">h", value)


def _handmade_cff2(num_glyphs: int = 6) -> bytes:
    """A CFF2 whose glyphs are split over two Font DICTs and drawn by local subrs.

    ``FontBuilder.setupCFF2`` hardcodes ``fdSelect = None``, so this is the only
    way to cover the FDSelect and local-subr relocations. Each glyph does nothing
    but call its Font DICT's local subr 0, which means a mis-moved FDSelect or
    subr INDEX shows up as the wrong box -- or no box at all.
    """
    rmoveto, rlineto, callsubr, ret = b"\x15", b"\x05", b"\x0a", b"\x0b"

    def box(width, height):
        return (
            _number(50) + _number(0) + rmoveto
            + _number(width) + _number(0) + rlineto
            + _number(0) + _number(height) + rlineto
            + _number(-width) + _number(0) + rlineto
            + ret
        )

    privates = []
    for width, height in ((100, 300), (400, 600)):
        private_dict = _encode_int(2) + b"\x13"  # Subrs (op 19) at relative 2
        assert len(private_dict) == 2  # ...which is its own length
        privates.append((private_dict, _build_index2([box(width, height)])))

    glyph = _number(-107) + callsubr  # subr 0, biased by 107
    charstrings = _build_index2([glyph] * num_glyphs)
    half = num_glyphs // 2
    fdselect = (
        b"\x03"
        + struct.pack(">H", 2)
        + struct.pack(">HB", 0, 0)
        + struct.pack(">HB", half, 1)
        + struct.pack(">H", num_glyphs)
    )

    def top_dict(charstrings_off, fdarray_off, fdselect_off):
        return (
            b"\x1d" + struct.pack(">I", charstrings_off) + b"\x11"
            + b"\x1d" + struct.pack(">I", fdarray_off) + b"\x0c\x24"
            + b"\x1d" + struct.pack(">I", fdselect_off) + b"\x0c\x25"
        )

    def font_dict(size, offset):
        return _encode_int(size) + b"\x1d" + struct.pack(">I", offset) + b"\x12"

    top_length = len(top_dict(0, 0, 0))
    header = bytes([2, 0, 5]) + struct.pack(">H", top_length)
    gsubrs = b"\x00\x00\x00\x00"  # an empty CFF2 INDEX is its count alone

    charstrings_off = len(header) + top_length + len(gsubrs)
    fdselect_off = charstrings_off + len(charstrings)
    fdarray_off = fdselect_off + len(fdselect)
    # Both Font DICTs encode to the same length, so one sizing pass is enough.
    fdarray_length = len(_build_index2([font_dict(0, 0)] * len(privates)))

    font_dicts, cursor = [], fdarray_off + fdarray_length
    for private_dict, subrs in privates:
        font_dicts.append(font_dict(len(private_dict), cursor))
        cursor += len(private_dict) + len(subrs)
    fdarray = _build_index2(font_dicts)
    assert len(fdarray) == fdarray_length

    out = bytearray(header)
    out += top_dict(charstrings_off, fdarray_off, fdselect_off)
    out += gsubrs + charstrings + fdselect + fdarray
    for private_dict, subrs in privates:
        out += private_dict + subrs
    return bytes(out)


def _width(outlines: CffOutlines, gid: int) -> float | None:
    xs = [x for contour in outlines.outline(gid) for x, _ in contour]
    return max(xs) - min(xs) if xs else None


def test_handmade_fixture_draws_a_different_box_per_font_dict():
    # Guards the test below: without this the fixture could be uniform and the
    # FDSelect assertion would pass for the wrong reason.
    outlines = CffOutlines(_handmade_cff2())
    assert outlines.ok
    assert [_width(outlines, gid) for gid in range(6)] == [100, 100, 100, 400, 400, 400]


def test_fdselect_and_local_subrs_survive_relocation():
    data = _handmade_cff2()
    out = subset_cff2(data, {1, 4})
    assert out is not None
    original, subset = CffOutlines(data), CffOutlines(out)
    assert subset.outline(1) == original.outline(1)  # Font DICT 0
    assert subset.outline(4) == original.outline(4)  # Font DICT 1
    assert _width(subset, 4) == 400  # ...and still the *right* Font DICT
    for gid in (2, 3, 5):
        assert subset.outline(gid) == []


# ---------------------------------------------------------------------------
# Integration: optimize() subsets an embedded CFF2 program
# ---------------------------------------------------------------------------


def _subset_opts(**kwargs):
    base = dict(
        remove_unused_objects=False,
        remove_unused_streams=False,
        link_duplicate_streams=False,
        remove_duplicate_images=False,
        compress_fonts=False,
    )
    base.update(kwargs)
    return OptimizationOptions(**base)


def _new_pdf(content: bytes):
    from aspose_pdf.engine.simple_pdf import SimplePdf

    pdf = SimplePdf()
    pdf.pages = [(0, 0, 200, 200)]
    pdf.page_contents = [content]
    pdf._ensure_cos()
    return pdf


def _font_file(cos, program: bytes):
    from aspose_pdf.engine.cos import PdfName, PdfNumber, PdfStream

    return cos.register_object(
        PdfStream(
            program,
            {
                PdfName("Length"): PdfNumber(len(program)),
                PdfName("Subtype"): PdfName("OpenType"),
            },
        )
    )


def _embed_cidfonttype0_cff2(program: bytes, shown_cids):
    from aspose_pdf.engine.cos import PdfArray, PdfDictionary, PdfName

    hexcodes = "".join(f"{cid:04x}" for cid in shown_cids)
    pdf = _new_pdf(("BT /F0 12 Tf <" + hexcodes + "> Tj ET").encode("latin-1"))
    cos = pdf._cos_doc
    font_file = _font_file(cos, program)
    descriptor = cos.register_object(
        PdfDictionary(
            {
                PdfName("Type"): PdfName("FontDescriptor"),
                PdfName("FontName"): PdfName("AAAAAA+SubTest2"),
                PdfName("FontFile3"): font_file,
            }
        )
    )
    cidfont = cos.register_object(
        PdfDictionary(
            {
                PdfName("Type"): PdfName("Font"),
                PdfName("Subtype"): PdfName("CIDFontType0"),
                PdfName("BaseFont"): PdfName("AAAAAA+SubTest2"),
                PdfName("FontDescriptor"): descriptor,
            }
        )
    )
    font_obj = cos.register_object(
        PdfDictionary(
            {
                PdfName("Type"): PdfName("Font"),
                PdfName("Subtype"): PdfName("Type0"),
                PdfName("BaseFont"): PdfName("AAAAAA+SubTest2"),
                PdfName("Encoding"): PdfName("Identity-H"),
                PdfName("DescendantFonts"): PdfArray([cidfont]),
            }
        )
    )
    pdf._get_page_dict(0).mapping[PdfName("Resources")] = PdfDictionary(
        {PdfName("Font"): PdfDictionary({PdfName("F0"): font_obj})}
    )
    return pdf, font_file.object_number


def _embed_simple_cff2(program: bytes, shown_codes, *, encoding="WinAnsiEncoding"):
    from aspose_pdf.engine.cos import PdfDictionary, PdfName

    hexcodes = "".join(f"{code:02x}" for code in shown_codes)
    pdf = _new_pdf(("BT /F0 12 Tf <" + hexcodes + "> Tj ET").encode("latin-1"))
    cos = pdf._cos_doc
    font_file = _font_file(cos, program)
    descriptor = cos.register_object(
        PdfDictionary(
            {
                PdfName("Type"): PdfName("FontDescriptor"),
                PdfName("FontName"): PdfName("AAAAAA+SubTest2"),
                PdfName("FontFile3"): font_file,
            }
        )
    )
    font_obj = cos.register_object(
        PdfDictionary(
            {
                PdfName("Type"): PdfName("Font"),
                PdfName("Subtype"): PdfName("Type1"),
                PdfName("BaseFont"): PdfName("AAAAAA+SubTest2"),
                PdfName("Encoding"): PdfName(encoding),
                PdfName("FontDescriptor"): descriptor,
            }
        )
    )
    pdf._get_page_dict(0).mapping[PdfName("Resources")] = PdfDictionary(
        {PdfName("Font"): PdfDictionary({PdfName("F0"): font_obj})}
    )
    return pdf, font_file.object_number


def test_optimize_subsets_an_embedded_cidfonttype0_cff2():
    # CFF2 has no charset, so a CIDFontType0's CIDs are glyph ids directly.
    program = _build_cff2_otf()
    pdf, font_num = _embed_cidfonttype0_cff2(program, shown_cids=[2])
    original = pdf._cos_doc.objects[font_num].content

    pdf.optimize(_subset_opts(subset_fonts=True))

    subset = pdf._cos_doc.objects[font_num].content
    assert len(subset) < len(original)
    got = _charstrings(_cff2_table(subset))
    assert got[2] != b""  # the shown glyph survives
    assert got[1] == b"" and got[3] == b""


def test_optimize_subsets_a_simple_font_backed_by_opentype_cff2():
    # A simple font resolves its codes through the sfnt cmap: 0x42 -> "g2".
    program = _build_cff2_otf()
    pdf, font_num = _embed_simple_cff2(program, shown_codes=[0x42])
    original = pdf._cos_doc.objects[font_num].content

    pdf.optimize(_subset_opts(subset_fonts=True))

    subset = pdf._cos_doc.objects[font_num].content
    assert len(subset) < len(original)
    got = _charstrings(_cff2_table(subset))
    assert got[2] != b""  # 'B' at 0x42 is glyph 2
    assert got[1] == b"" and got[4] == b""


def test_optimize_leaves_cff2_whole_when_a_used_code_cannot_be_resolved():
    # 0x7E ('asciitilde') has no glyph in this font, so nothing may be erased.
    program = _build_cff2_otf()
    pdf, font_num = _embed_simple_cff2(program, shown_codes=[0x7E])
    original = pdf._cos_doc.objects[font_num].content

    pdf.optimize(_subset_opts(subset_fonts=True))

    assert pdf._cos_doc.objects[font_num].content == original


def test_optimize_leaves_cff2_untouched_when_subsetting_is_off():
    program = _build_cff2_otf()
    pdf, font_num = _embed_cidfonttype0_cff2(program, shown_cids=[2])
    original = pdf._cos_doc.objects[font_num].content

    pdf.optimize(_subset_opts())  # subset_fonts defaults off

    assert pdf._cos_doc.objects[font_num].content == original


def test_optimized_cff2_survives_a_save_roundtrip():
    from aspose_pdf.engine.simple_pdf import SimplePdf

    program = _build_cff2_otf()
    pdf, _ = _embed_cidfonttype0_cff2(program, shown_cids=[2])
    pdf.optimize(_subset_opts(subset_fonts=True))

    reloaded = SimplePdf.from_bytes(pdf.to_bytes())
    assert reloaded.page_count == 1

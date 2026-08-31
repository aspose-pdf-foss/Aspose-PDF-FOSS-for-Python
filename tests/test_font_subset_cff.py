"""Tests for the dependency-free CFF (/FontFile3) glyph-erasure subsetter.

Fixtures are built with fontTools (the same encoder real CFF fonts come from),
which is also used as an oracle: the kept glyphs must draw the same outlines
after subsetting. The module is skipped when fontTools is unavailable.
"""

from __future__ import annotations

import io
import struct

import pytest

pytest.importorskip("fontTools")

from fontTools.cffLib import CFFFontSet
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.recordingPen import RecordingPen
from fontTools.pens.t2CharStringPen import T2CharStringPen
from fontTools.ttLib import TTFont

from aspose_pdf import OptimizationOptions
from aspose_pdf.engine.agl import cff_predefined_charset
from aspose_pdf.engine.cff_outlines import CffOutlines
from aspose_pdf.engine.font_subset_cff import (
    _OP_CHARSET,
    _build_index,
    _encode_int,
    _op_bytes,
    _parse_dict,
    _read_index,
    _slice_private,
    cff_charset_cid_to_gid,
    subset_cff,
)

_ORDER = [".notdef", "A", "B", "C", "D"]
# Glyph names out of the Expert repertoire, for MacExpertEncoding coverage.
_EXPERT_ORDER = [".notdef", "zerooldstyle", "oneoldstyle", "twooldstyle", "Asmall"]
_BOXES = {
    # .notdef draws too, so that erasing it would be visible -- it never should be.
    ".notdef": (0, 0, 120, 120),
    "A": (100, 0, 300, 400),
    "B": (50, 50, 350, 300),
    "C": (0, 0, 250, 450),
    "D": (10, 10, 480, 360),
}


def _build_cff(order: list[str] | None = None) -> bytes:
    """A name-keyed CFF with several many-segment glyphs (so erasure shrinks it)."""
    order = order or _ORDER
    boxes = dict(zip(order[1:], list(_BOXES.values())[1:]))
    boxes[".notdef"] = _BOXES[".notdef"]
    charstrings = {}
    for name in order:
        pen = T2CharStringPen(600, None)
        if name in boxes:
            x0, y0, x1, y1 = boxes[name]
            pen.moveTo((x0, y0))
            for k in range(25):  # lots of segments -> big charstring
                pen.lineTo((x1 - k, y0 + k))
                pen.lineTo((x1, y1))
            pen.closePath()
        charstrings[name] = pen.getCharString()
    fb = FontBuilder(unitsPerEm=1000, isTTF=False)
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap({0x41 + i: name for i, name in enumerate(order[1:])})
    fb.setupCFF("SubTest", {}, charstrings, {})
    fb.setupHorizontalMetrics({g: (600, 0) for g in order})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "SubTest", "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()
    buf = io.BytesIO()
    fb.save(buf)
    font = TTFont(buf)
    return font["CFF "].compile(font)


def _charstrings(cff_bytes: bytes):
    cff = CFFFontSet()
    cff.decompile(io.BytesIO(cff_bytes), None)
    return cff[cff.fontNames[0]].CharStrings


def _outline(charstrings, name):
    pen = RecordingPen()
    charstrings[name].draw(pen)
    return pen.value


# ---------------------------------------------------------------------------
# Core erasure
# ---------------------------------------------------------------------------


def test_subset_keeps_used_and_erases_unused():
    cff = _build_cff()
    out = subset_cff(cff, {2})  # keep gid 2 ('B'); .notdef forced in.
    assert out is not None
    assert len(out) < len(cff)

    original, got = _charstrings(cff), _charstrings(out)
    # The kept glyph still draws the exact same outline.
    assert _outline(got, "B") == _outline(original, "B")
    # .notdef survives without being asked for; every other glyph is erased.
    assert _outline(got, ".notdef") == _outline(original, ".notdef")
    for name in ("A", "C", "D"):
        assert got[name].bytecode == b"\x0e"


def test_subset_keeps_multiple_glyphs():
    cff = _build_cff()
    out = subset_cff(cff, {1, 3})  # 'A' and 'C'
    assert out is not None
    original, got = _charstrings(cff), _charstrings(out)
    assert _outline(got, "A") == _outline(original, "A")
    assert _outline(got, "C") == _outline(original, "C")
    assert got["B"].bytecode == b"\x0e"
    assert got["D"].bytecode == b"\x0e"


def test_subset_preserves_glyph_order_and_charset():
    cff = _build_cff()
    out = subset_cff(cff, {2})
    cff_set = CFFFontSet()
    cff_set.decompile(io.BytesIO(out), None)
    # charset relocation is correct: the glyph order (GID->name) is unchanged.
    assert cff_set[cff_set.fontNames[0]].getGlyphOrder() == _ORDER


def test_subset_returns_none_when_nothing_to_erase():
    cff = _build_cff()
    # Keeping every glyph leaves nothing to erase -> no benefit.
    assert subset_cff(cff, set(range(len(_ORDER)))) is None


def test_subset_is_defensive():
    assert subset_cff(b"", {1}) is None
    assert subset_cff(b"not a CFF font", {1}) is None
    assert subset_cff(b"\x02\x00\x04\x02junk", {1}) is None  # CFF2 (major 2)
    assert subset_cff(b"\x01\x00\x04\x01", {1}) is None  # header only, truncated


def test_slice_private_includes_local_subrs():
    # Private DICT = Subrs(rel=2) op; local subrs sit right after it.
    private_dict = _encode_int(2) + bytes([19])  # operand 2, operator 19 (Subrs)
    assert len(private_dict) == 2  # so rel == 2 is self-consistent
    local_subrs = _build_index([b"\x0b"])  # one subr ('return')
    prefix, suffix = b"\x00\x00\x00", b"\xff\xff"
    buf = prefix + private_dict + local_subrs + suffix
    poff = len(prefix)
    entries = [(18, _encode_int(2) + _encode_int(poff))]  # Private: size=2, offset

    blob, size = _slice_private(buf, entries)
    assert size == 2
    # The relocated blob carries the Private DICT *and* its local subrs.
    assert blob == private_dict + local_subrs


# ---------------------------------------------------------------------------
# The predefined Expert / ExpertSubset charsets
# ---------------------------------------------------------------------------


def _force_predefined_charset(cff_bytes: bytes, index: int) -> bytes:
    """Rewrite the Top DICT's charset operand to the predefined *index*.

    fontTools only ever writes a charset stored in the font, so an Expert font
    has to be made by hand. The operand is rewritten in place at the same byte
    length, which leaves every other offset in the CFF valid.
    """
    header_size = cff_bytes[2]
    _names, pos = _read_index(cff_bytes, header_size)
    topdicts, _pos = _read_index(cff_bytes, pos)
    entries = _parse_dict(topdicts[0])
    out = bytearray()
    for key, operand in entries:
        if key == _OP_CHARSET:
            assert len(operand) == 1, "expected a one-byte charset offset to swap"
            out += bytes([index + 139])
        else:
            out += operand
        out += _op_bytes(key)
    assert len(out) == len(topdicts[0])
    return cff_bytes.replace(topdicts[0], bytes(out), 1)


@pytest.mark.parametrize("index", [1, 2])
def test_predefined_charset_supplies_the_glyph_names(index):
    # A charset offset of 1 or 2 stores no names in the font at all: the glyph
    # ordering is the one the CFF specification fixes.
    cff = _force_predefined_charset(_build_cff(order=_EXPERT_ORDER), index)
    names = CffOutlines(cff).name_to_gid()
    expected = cff_predefined_charset(index)[: len(_EXPERT_ORDER)]
    assert names == {name: gid for gid, name in enumerate(expected)}
    # ...which is *not* the font's own glyph order, so this cannot pass by luck.
    assert set(names) != set(_EXPERT_ORDER)


def test_predefined_charset_is_clamped_to_the_font_s_glyph_count():
    # The Expert charset lists 166 glyphs; this font has five.
    cff = _force_predefined_charset(_build_cff(order=_EXPERT_ORDER), 1)
    names = CffOutlines(cff).name_to_gid()
    assert len(names) == len(_EXPERT_ORDER)
    assert max(names.values()) == len(_EXPERT_ORDER) - 1


def test_optimize_subsets_a_macexpert_font_with_a_predefined_expert_charset():
    # The two Expert halves together: MacExpertEncoding maps 0x21 to
    # "exclamsmall", and the predefined Expert charset puts it at glyph 2.
    cff = _force_predefined_charset(_build_cff(order=_EXPERT_ORDER), 1)
    assert cff_predefined_charset(1)[2] == "exclamsmall"
    pdf, ff_num = _embed_simple_type1(cff, [0x21], pdf_encoding="MacExpertEncoding")
    original = pdf._cos_doc.objects[ff_num].content

    pdf.optimize(_subset_opts(subset_fonts=True))

    subset = pdf._cos_doc.objects[ff_num].content
    assert len(subset) < len(original)
    # The font now answers to the Expert names, not the ones it was built with.
    kept = _charstrings(subset)
    assert kept["exclamsmall"].bytecode != b"\x0e"
    for name in ("space", "Hungarumlautsmall", "dollaroldstyle"):
        assert kept[name].bytecode == b"\x0e"


# ---------------------------------------------------------------------------
# Integration: optimize() subsets an embedded CIDFontType0 (CFF) program
# ---------------------------------------------------------------------------


def _embed_cidfonttype0(cff_bytes: bytes, shown_cids):
    """One-page COS PDF embedding *cff_bytes* as a Type0/CIDFontType0 font."""
    from aspose_pdf.engine.cos import (
        PdfArray,
        PdfDictionary,
        PdfName,
        PdfNumber,
        PdfStream,
    )
    from aspose_pdf.engine.simple_pdf import SimplePdf

    hexcodes = "".join(f"{c:04x}" for c in shown_cids)
    content = ("BT /F0 12 Tf <" + hexcodes + "> Tj ET").encode("latin-1")
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 200, 200)]
    pdf.page_contents = [content]
    pdf._ensure_cos()
    cos = pdf._cos_doc

    ff = cos.register_object(
        PdfStream(
            cff_bytes,
            {
                PdfName("Length"): PdfNumber(len(cff_bytes)),
                PdfName("Subtype"): PdfName("Type1C"),
            },
        )
    )
    descriptor = cos.register_object(
        PdfDictionary(
            {
                PdfName("Type"): PdfName("FontDescriptor"),
                PdfName("FontName"): PdfName("AAAAAA+SubTest"),
                PdfName("FontFile3"): ff,
            }
        )
    )
    cidfont = cos.register_object(
        PdfDictionary(
            {
                PdfName("Type"): PdfName("Font"),
                PdfName("Subtype"): PdfName("CIDFontType0"),
                PdfName("BaseFont"): PdfName("AAAAAA+SubTest"),
                PdfName("FontDescriptor"): descriptor,
            }
        )
    )
    type0 = cos.register_object(
        PdfDictionary(
            {
                PdfName("Type"): PdfName("Font"),
                PdfName("Subtype"): PdfName("Type0"),
                PdfName("BaseFont"): PdfName("AAAAAA+SubTest"),
                PdfName("Encoding"): PdfName("Identity-H"),
                PdfName("DescendantFonts"): PdfArray([cidfont]),
            }
        )
    )
    page = pdf._get_page_dict(0)
    page.mapping[PdfName("Resources")] = PdfDictionary(
        {PdfName("Font"): PdfDictionary({PdfName("F0"): type0})}
    )
    return pdf, ff.object_number


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


def test_optimize_subsets_embedded_cidfonttype0_cff():
    cff = _build_cff()
    pdf, ff_num = _embed_cidfonttype0(cff, shown_cids=[2])  # CID 2 == gid 2 ('B')
    original = pdf._cos_doc.objects[ff_num].content

    pdf.optimize(_subset_opts(subset_fonts=True))

    new_program = pdf._cos_doc.objects[ff_num].content
    assert len(new_program) < len(original)
    got = _charstrings(new_program)
    assert got["B"].bytecode != b"\x0e"  # shown glyph kept
    assert got["A"].bytecode == b"\x0e"  # unused glyphs erased
    assert got["C"].bytecode == b"\x0e"


def test_optimize_leaves_cff_untouched_when_subset_off():
    cff = _build_cff()
    pdf, ff_num = _embed_cidfonttype0(cff, shown_cids=[2])
    before = pdf._cos_doc.objects[ff_num].content

    pdf.optimize(_subset_opts())  # subset_fonts defaults off

    assert pdf._cos_doc.objects[ff_num].content == before


def test_optimize_subset_survives_save_roundtrip():
    from aspose_pdf.engine.simple_pdf import SimplePdf

    cff = _build_cff()
    pdf, _ = _embed_cidfonttype0(cff, shown_cids=[2])
    pdf.optimize(_subset_opts(subset_fonts=True))

    out = pdf.to_bytes()
    assert out.startswith(b"%PDF")
    reopened = SimplePdf.from_bytes(out)
    try:
        assert len(reopened.pages) == 1
    finally:
        reopened.dispose()


# ---------------------------------------------------------------------------
# CID-keyed CFF (/CIDFontType0C): FDArray / FDSelect / non-identity charset
# ---------------------------------------------------------------------------
# CIDs are deliberately not equal to glyph ids (cid = gid * 10) so the charset
# and the CID->GID resolution are actually exercised.
_CID_BY_GID = [0, 10, 20, 30]
_CID_BOXES = [None, (0, 0, 100, 200), (10, 10, 200, 300), (5, 5, 150, 260)]
_PH = b"\x1d\x00\x00\x00\x00"  # CFF 5-byte integer operand, value zero.


def _cid_charstrings() -> list[bytes]:
    out = []
    for box in _CID_BOXES:
        pen = T2CharStringPen(600, None)
        if box:
            x0, y0, x1, y1 = box
            pen.moveTo((x0, y0))
            for k in range(20):
                pen.lineTo((x1 - k, y0 + k))
                pen.lineTo((x1, y1))
            pen.closePath()
        cs = pen.getCharString()
        cs.compile()
        out.append(cs.bytecode)
    return out


def _build_cid_cff() -> bytes:
    """Hand-assemble a minimal but valid CID-keyed CFF (validated by fontTools).

    Two Font DICTs (FDSelect splits the glyphs between them), a format-0 charset
    mapping gid -> cid, and per-FD Private DICTs — i.e. every block the subsetter
    has to relocate.
    """
    charstrings = _cid_charstrings()
    n = len(charstrings)
    header = bytes([1, 0, 4, 2])
    name_index = _build_index([b"CIDFont"])
    string_index = _build_index([b"Adobe", b"Identity"])  # SIDs 391, 392
    gsubr = _build_index([])
    cs_index = _build_index(charstrings)
    charset = bytes([0]) + b"".join(
        struct.pack(">H", _CID_BY_GID[g]) for g in range(1, n)
    )
    fdselect = bytes([0]) + bytes([0, 0, 1, 1])  # format 0: gid -> FD index

    def private() -> bytes:
        return _encode_int(0) + bytes([20]) + _encode_int(0) + bytes([21])

    def fd_dict(priv_size: int):
        size_bytes = _encode_int(priv_size)
        return size_bytes + _PH + bytes([18]), len(size_bytes) + 1

    priv0, priv1 = private(), private()
    fd0, rel0 = fd_dict(len(priv0))
    fd1, rel1 = fd_dict(len(priv1))
    fdarray = bytearray(_build_index([fd0, fd1]))
    fdarray_hdr = len(fdarray) - (len(fd0) + len(fd1))

    top = bytearray()
    top += _encode_int(391) + _encode_int(392) + _encode_int(0) + bytes([12, 30])  # ROS
    cs_patch = len(top) + 1
    top += _PH + bytes([17])
    charset_patch = len(top) + 1
    top += _PH + bytes([15])
    fdarray_patch = len(top) + 1
    top += _PH + bytes([12, 36])
    fdselect_patch = len(top) + 1
    top += _PH + bytes([12, 37])
    topdict_index = bytearray(_build_index([bytes(top)]))
    topdict_hdr = len(topdict_index) - len(top)

    off = len(header) + len(name_index) + len(topdict_index)
    off += len(string_index) + len(gsubr)
    cs_off = off
    off += len(cs_index)
    charset_off = off
    off += len(charset)
    fdselect_off = off
    off += len(fdselect)
    fdarray_off = off
    off += len(fdarray)
    priv0_off = off
    off += len(priv0)
    priv1_off = off

    struct.pack_into(">I", topdict_index, topdict_hdr + cs_patch, cs_off)
    struct.pack_into(">I", topdict_index, topdict_hdr + charset_patch, charset_off)
    struct.pack_into(">I", topdict_index, topdict_hdr + fdarray_patch, fdarray_off)
    struct.pack_into(">I", topdict_index, topdict_hdr + fdselect_patch, fdselect_off)
    struct.pack_into(">I", fdarray, fdarray_hdr + rel0, priv0_off)
    struct.pack_into(">I", fdarray, fdarray_hdr + len(fd0) + rel1, priv1_off)

    return bytes(
        bytearray(header) + name_index + topdict_index + string_index + gsubr
        + cs_index + charset + fdselect + fdarray + priv0 + priv1
    )


def _cid_glyph_order(cff_bytes: bytes):
    cff = CFFFontSet()
    cff.decompile(io.BytesIO(cff_bytes), None)
    top = cff[cff.fontNames[0]]
    return top, top.getGlyphOrder()


def test_cid_fixture_is_valid_cid_keyed_cff():
    cff = _build_cid_cff()
    top, order = _cid_glyph_order(cff)
    assert hasattr(top, "ROS"), "fixture must be a CID-keyed CFF"
    # Glyph names are synthesised from CIDs (cid = gid * 10).
    assert order == [".notdef", "cid00010", "cid00020", "cid00030"]


def test_cff_charset_cid_to_gid_maps_non_identity():
    cff = _build_cid_cff()
    assert cff_charset_cid_to_gid(cff) == {0: 0, 10: 1, 20: 2, 30: 3}


def test_cff_charset_cid_to_gid_none_for_name_keyed():
    # A name-keyed CFF means "use the CID as the glyph id directly".
    assert cff_charset_cid_to_gid(_build_cff()) is None


def test_subset_cid_keyed_keeps_used_and_erases_unused():
    cff = _build_cid_cff()
    top, order = _cid_glyph_order(cff)
    before = _outline(top.CharStrings, order[2])

    out = subset_cff(cff, {2})  # keep gid 2 (cid 20).
    assert out is not None and len(out) < len(cff)

    top2, order2 = _cid_glyph_order(out)
    assert hasattr(top2, "ROS"), "subset must stay CID-keyed"
    assert order2 == order, "glyph-to-CID order must be preserved"
    assert _outline(top2.CharStrings, order2[2]) == before
    for gid in (1, 3):
        assert top2.CharStrings[order2[gid]].bytecode == b"\x0e"


def test_optimize_subsets_embedded_cid_keyed_cff():
    cff = _build_cid_cff()
    # Show CID 20, which resolves through the charset to glyph id 2.
    pdf, ff_num = _embed_cidfonttype0(cff, shown_cids=[20])
    original = pdf._cos_doc.objects[ff_num].content

    pdf.optimize(_subset_opts(subset_fonts=True))

    new_program = pdf._cos_doc.objects[ff_num].content
    assert len(new_program) < len(original)
    top, order = _cid_glyph_order(new_program)
    assert top.CharStrings[order[2]].bytecode != b"\x0e"  # CID 20 kept
    for gid in (1, 3):
        assert top.CharStrings[order[gid]].bytecode == b"\x0e"  # others erased


# ---------------------------------------------------------------------------
# Integration: optimize() subsets a simple (Type1) name-keyed CFF font
# ---------------------------------------------------------------------------


def _build_encoded_cff() -> bytes:
    """A name-keyed CFF carrying a custom built-in encoding (0x80..0x83 -> A..D)."""
    charstrings = {}
    for name in _ORDER:
        pen = T2CharStringPen(600, None)
        if name in _BOXES:
            x0, y0, x1, y1 = _BOXES[name]
            pen.moveTo((x0, y0))
            for k in range(25):
                pen.lineTo((x1 - k, y0 + k))
                pen.lineTo((x1, y1))
            pen.closePath()
        charstrings[name] = pen.getCharString()
    fb = FontBuilder(unitsPerEm=1000, isTTF=False)
    fb.setupGlyphOrder(_ORDER)
    fb.setupCharacterMap({0x41: "A", 0x42: "B", 0x43: "C", 0x44: "D"})
    fb.setupCFF("SubTest", {}, charstrings, {})
    fb.setupHorizontalMetrics({g: (600, 0) for g in _ORDER})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "SubTest", "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()
    cff = fb.font["CFF "]
    top = cff.cff[cff.cff.fontNames[0]]
    encoding = [".notdef"] * 256
    encoding[0x80], encoding[0x81], encoding[0x82], encoding[0x83] = "A", "B", "C", "D"
    top.Encoding = encoding  # a custom CFF encoding (not StandardEncoding)
    buf = io.BytesIO()
    fb.save(buf)
    return TTFont(buf)["CFF "].compile(TTFont(buf))


def _embed_simple_type1(
    cff_bytes: bytes, shown_codes, *, pdf_encoding=None, differences=None
):
    from aspose_pdf.engine.cos import (
        PdfArray,
        PdfDictionary,
        PdfName,
        PdfNumber,
        PdfStream,
    )
    from aspose_pdf.engine.simple_pdf import SimplePdf

    content = ("BT /F0 12 Tf <" + "".join(f"{c:02x}" for c in shown_codes) + "> Tj ET")
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 200, 200)]
    pdf.page_contents = [content.encode("latin-1")]
    pdf._ensure_cos()
    cos = pdf._cos_doc
    ff = cos.register_object(
        PdfStream(
            cff_bytes,
            {PdfName("Length"): PdfNumber(len(cff_bytes)), PdfName("Subtype"): PdfName("Type1C")},
        )
    )
    descriptor = cos.register_object(
        PdfDictionary(
            {
                PdfName("Type"): PdfName("FontDescriptor"),
                PdfName("FontName"): PdfName("AAAAAA+SubTest"),
                PdfName("FontFile3"): ff,
            }
        )
    )
    font_map = {
        PdfName("Type"): PdfName("Font"),
        PdfName("Subtype"): PdfName("Type1"),
        PdfName("BaseFont"): PdfName("AAAAAA+SubTest"),
        PdfName("FontDescriptor"): descriptor,
    }
    if differences is not None:
        code, glyph = differences
        encoding_map = {
            PdfName("Type"): PdfName("Encoding"),
            PdfName("Differences"): PdfArray(
                [PdfNumber(code), PdfName(glyph)]
            ),
        }
        if pdf_encoding is not None:
            encoding_map[PdfName("BaseEncoding")] = PdfName(pdf_encoding)
        font_map[PdfName("Encoding")] = cos.register_object(
            PdfDictionary(encoding_map)
        )
    elif pdf_encoding is not None:
        font_map[PdfName("Encoding")] = PdfName(pdf_encoding)
    font_obj = cos.register_object(PdfDictionary(font_map))
    pdf._get_page_dict(0).mapping[PdfName("Resources")] = PdfDictionary(
        {PdfName("Font"): PdfDictionary({PdfName("F0"): font_obj})}
    )
    return pdf, ff.object_number


def test_optimize_subsets_simple_cff_via_builtin_encoding():
    cff = _build_encoded_cff()
    pdf, ff_num = _embed_simple_type1(cff, shown_codes=[0x81])  # 0x81 -> 'B' (gid 2)
    original = pdf._cos_doc.objects[ff_num].content

    pdf.optimize(_subset_opts(subset_fonts=True))

    new_program = pdf._cos_doc.objects[ff_num].content
    assert len(new_program) < len(original)
    got = _charstrings(new_program)
    assert got["B"].bytecode != b"\x0e"  # shown glyph kept
    for name in ("A", "C", "D"):
        assert got[name].bytecode == b"\x0e"  # unused glyphs erased


def test_simple_cff_with_pdf_encoding_is_subset():
    # WinAnsiEncoding leaves 0x81 undefined, so the CFF's own built-in encoding
    # still governs that code and the font can be subset.
    cff = _build_encoded_cff()
    pdf, ff_num = _embed_simple_type1(cff, [0x81], pdf_encoding="WinAnsiEncoding")
    original = pdf._cos_doc.objects[ff_num].content
    pdf.optimize(_subset_opts(subset_fonts=True))
    assert len(pdf._cos_doc.objects[ff_num].content) < len(original)


def test_simple_cff_pdf_encoding_differences_resolve_through_the_charset():
    # /Differences names a glyph directly; the CFF charset maps it to a gid.
    cff = _build_cff()
    pdf, ff_num = _embed_simple_type1(
        cff, [0x30], pdf_encoding="WinAnsiEncoding", differences=(0x30, "C")
    )
    original = pdf._cos_doc.objects[ff_num].content
    pdf.optimize(_subset_opts(subset_fonts=True))
    subset = pdf._cos_doc.objects[ff_num].content
    assert len(subset) < len(original)
    got = _charstrings(subset)
    assert got["C"].bytecode != b"\x0e"  # the named glyph survives
    for name in ("A", "B", "D"):
        assert got[name].bytecode == b"\x0e"  # the rest are erased


def test_simple_cff_standard_encoding_is_subset():
    # A predefined (Standard) CFF encoding means StandardEncoding: 0x41 -> "A",
    # resolved to a gid through the charset.
    cff = _build_cff()  # StandardEncoding
    pdf, ff_num = _embed_simple_type1(cff, [0x41])
    original = pdf._cos_doc.objects[ff_num].content
    pdf.optimize(_subset_opts(subset_fonts=True))
    subset = pdf._cos_doc.objects[ff_num].content
    assert len(subset) < len(original)
    got = _charstrings(subset)
    assert got["A"].bytecode != b"\x0e"
    for name in ("B", "C", "D"):
        assert got[name].bytecode == b"\x0e"


def test_simple_cff_macexpert_base_encoding_is_subset():
    # MacExpertEncoding is bundled, so 0x30 resolves to "zerooldstyle" and the
    # font is subset around it instead of being kept whole.
    cff = _build_cff(order=_EXPERT_ORDER)
    pdf, ff_num = _embed_simple_type1(cff, [0x30], pdf_encoding="MacExpertEncoding")
    original = pdf._cos_doc.objects[ff_num].content

    pdf.optimize(_subset_opts(subset_fonts=True))

    subset = pdf._cos_doc.objects[ff_num].content
    assert len(subset) < len(original)
    got = _charstrings(subset)
    assert got["zerooldstyle"].bytecode != b"\x0e"
    for name in ("oneoldstyle", "twooldstyle", "Asmall"):
        assert got[name].bytecode == b"\x0e"


def test_simple_cff_macexpert_does_not_read_0x41_as_latin_a():
    # 0x41 is "A" in Standard/WinAnsi/MacRoman but undefined in MacExpert, where
    # it falls through to the font's own encoding. Reading the wrong table here
    # would keep the wrong glyph and erase the used one.
    cff = _build_cff(order=_EXPERT_ORDER)
    pdf, ff_num = _embed_simple_type1(cff, [0x41], pdf_encoding="MacExpertEncoding")
    pdf.optimize(_subset_opts(subset_fonts=True))
    got = _charstrings(pdf._cos_doc.objects[ff_num].content)
    # The CFF's built-in encoding is Standard, under which 0x41 is "A" -- a name
    # this font does not define, so nothing can be resolved and it stays whole.
    assert all(got[name].bytecode != b"\x0e" for name in _EXPERT_ORDER[1:])


def test_simple_cff_with_unrecognised_base_encoding_is_not_subset():
    # PDF 32000-1 Table 114 allows four /BaseEncoding names; anything else is
    # malformed, and guessing which was meant could erase a used glyph.
    cff = _build_cff()
    pdf, ff_num = _embed_simple_type1(cff, [0x41], pdf_encoding="NotAnEncoding")
    original = pdf._cos_doc.objects[ff_num].content
    pdf.optimize(_subset_opts(subset_fonts=True))
    assert pdf._cos_doc.objects[ff_num].content == original

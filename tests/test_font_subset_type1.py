"""Type 1 (/FontFile) font subsetting by glyph erasure."""

from __future__ import annotations

import struct

from aspose_pdf.engine.font_subset_type1 import (
    _encrypt,
    _encrypt_charstring,
    subset_type1,
)
from aspose_pdf.engine.type1_outlines import _EEXEC_R, Type1Outlines


def _t1num(n: int) -> bytes:
    n = int(n)
    if -107 <= n <= 107:
        return bytes([n + 139])
    if 108 <= n <= 1131:
        n -= 108
        return bytes([247 + (n >> 8), n & 0xFF])
    if -1131 <= n <= -108:
        n = -n - 108
        return bytes([251 + (n >> 8), n & 0xFF])
    return bytes([255]) + struct.pack(">i", n)


def _box(segments: int) -> bytes:
    cs = _t1num(0) + _t1num(700) + bytes([13])  # hsbw
    cs += _t1num(100) + _t1num(100) + bytes([21])  # rmoveto
    for _k in range(segments):
        cs += _t1num(10) + bytes([6])  # hlineto
        cs += _t1num(5) + bytes([7])   # vlineto
    cs += bytes([9, 14])  # closepath, endchar
    return cs


def _build_type1():
    """A minimal Type 1 program with a built-in encoding (0x80..0x83 -> A..D)."""
    glyphs = [
        (b".notdef", _box(1)),
        (b"A", _box(25)),
        (b"B", _box(25)),
        (b"C", _box(25)),
        (b"D", _box(25)),
    ]
    private = b"dup /Private 8 dict dup begin\n/lenIV 4 def\n/CharStrings 5 dict dup begin\n"
    for name, cs in glyphs:
        enc = _encrypt_charstring(cs, 4)
        private += b"/" + name + b" " + str(len(enc)).encode() + b" RD " + enc + b" ND\n"
    private += b"end\nend\nreadonly put\nnoaccess put\n"
    eexec = _encrypt(b"\x00\x00\x00\x00" + private, _EEXEC_R)
    clear = (
        b"%!FontType1-1.0: Test\n/FontType 1 def\n"
        b"/FontMatrix [0.001 0 0 0.001 0 0] def\n"
        b"/Encoding 256 array\n0 1 255 {1 index exch /.notdef put} for\n"
        b"dup 128 /A put\ndup 129 /B put\ndup 130 /C put\ndup 131 /D put\nreadonly def\n"
        b"currentdict end\ncurrentfile eexec\n"
    )
    trailer = b"\n" + b"0" * 512 + b"\ncleartomark\n"
    program = clear + eexec + trailer
    return program, len(clear), len(eexec), len(trailer)


# ---------------------------------------------------------------------------
# Core erasure
# ---------------------------------------------------------------------------


def _outline_points(outlines, name):
    return sum(len(c) for c in outlines.outline(outlines.name_to_gid[name]))


def test_subset_keeps_used_and_empties_unused():
    program, l1, l2, l3 = _build_type1()
    out = subset_type1(program, {"B"}, l1, l2)
    assert out is not None
    assert len(out) < len(program)

    reparsed = Type1Outlines(out, l1, len(out) - l1 - l3)
    assert reparsed.ok
    assert _outline_points(reparsed, "B") == _outline_points(
        Type1Outlines(program, l1, l2), "B"
    )  # kept glyph unchanged
    for name in ("A", "C", "D"):
        assert _outline_points(reparsed, name) == 0  # emptied


def test_subset_returns_none_when_nothing_to_erase():
    program, l1, l2, _l3 = _build_type1()
    assert subset_type1(program, {"A", "B", "C", "D"}, l1, l2) is None


def test_notdef_is_always_kept():
    program, l1, l2, l3 = _build_type1()
    out = subset_type1(program, {"A"}, l1, l2)
    reparsed = Type1Outlines(out, l1, len(out) - l1 - l3)
    assert _outline_points(reparsed, ".notdef") > 0


# ---------------------------------------------------------------------------
# Integration: optimize() subsets an embedded Type 1 font
# ---------------------------------------------------------------------------


def _embed_type1(shown_codes, *, pdf_encoding=None, differences=None):
    from aspose_pdf.engine.cos import (
        PdfArray,
        PdfDictionary,
        PdfName,
        PdfNumber,
        PdfStream,
    )
    from aspose_pdf.engine.simple_pdf import SimplePdf

    program, l1, l2, l3 = _build_type1()
    content = ("BT /F0 12 Tf <" + "".join(f"{c:02x}" for c in shown_codes) + "> Tj ET")
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 200, 200)]
    pdf.page_contents = [content.encode("latin-1")]
    pdf._ensure_cos()
    cos = pdf._cos_doc
    ff = cos.register_object(
        PdfStream(
            program,
            {
                PdfName("Length"): PdfNumber(len(program)),
                PdfName("Length1"): PdfNumber(l1),
                PdfName("Length2"): PdfNumber(l2),
                PdfName("Length3"): PdfNumber(l3),
            },
        )
    )
    descriptor = cos.register_object(
        PdfDictionary(
            {
                PdfName("Type"): PdfName("FontDescriptor"),
                PdfName("FontName"): PdfName("AAAAAA+Test"),
                PdfName("FontFile"): ff,
            }
        )
    )
    font_map = {
        PdfName("Type"): PdfName("Font"),
        PdfName("Subtype"): PdfName("Type1"),
        PdfName("BaseFont"): PdfName("AAAAAA+Test"),
        PdfName("FontDescriptor"): descriptor,
    }
    if pdf_encoding is not None:
        font_map[PdfName("Encoding")] = PdfName(pdf_encoding)
    elif differences is not None:
        font_map[PdfName("Encoding")] = PdfDictionary(
            {PdfName("Differences"): PdfArray(differences)}
        )
    font_obj = cos.register_object(PdfDictionary(font_map))
    pdf._get_page_dict(0).mapping[PdfName("Resources")] = PdfDictionary(
        {PdfName("Font"): PdfDictionary({PdfName("F0"): font_obj})}
    )
    return pdf, ff.object_number, (l1, l2, l3)


def _subset_opts(**kwargs):
    from aspose_pdf import OptimizationOptions

    base = dict(
        remove_unused_objects=False,
        remove_unused_streams=False,
        link_duplicate_streams=False,
        remove_duplicate_images=False,
        compress_fonts=False,
        subset_fonts=True,
    )
    base.update(kwargs)
    return OptimizationOptions(**base)


def _charstring_points(program, l1, l2, name):
    o = Type1Outlines(program, l1, l2)
    return sum(len(c) for c in o.outline(o.name_to_gid[name]))


def test_optimize_subsets_type1_via_builtin_encoding():
    from aspose_pdf.engine.cos import PdfName

    pdf, ff_num, (l1, _l2, l3) = _embed_type1(shown_codes=[0x81])  # 0x81 -> 'B'
    original = pdf._cos_doc.objects[ff_num].content

    pdf.optimize(_subset_opts())

    stream = pdf._cos_doc.objects[ff_num]
    new_program = stream.content
    assert len(new_program) < len(original)
    new_l2 = int(stream.mapping[PdfName("Length2")].value)
    assert int(stream.mapping[PdfName("Length1")].value) == l1  # cleartext unchanged
    assert new_l2 == len(new_program) - l1 - l3  # only eexec resized
    assert _charstring_points(new_program, l1, new_l2, "B") > 0  # shown glyph kept
    for name in ("A", "C", "D"):
        assert _charstring_points(new_program, l1, new_l2, name) == 0  # erased


def test_type1_differences_resolve_without_a_table():
    from aspose_pdf.engine.cos import PdfName, PdfNumber

    # /Differences maps code 0x20 to 'C' directly (a Type 1 glyph name); no base
    # encoding table is needed.
    pdf, ff_num, (l1, _l2, l3) = _embed_type1(
        shown_codes=[0x20], differences=[PdfNumber(0x20), PdfName("C")]
    )
    pdf.optimize(_subset_opts())
    stream = pdf._cos_doc.objects[ff_num]
    new_l2 = int(stream.mapping[PdfName("Length2")].value)
    assert _charstring_points(stream.content, l1, new_l2, "C") > 0
    assert _charstring_points(stream.content, l1, new_l2, "A") == 0


def test_type1_with_named_base_encoding_is_not_subset():
    # A predefined base encoding needs a code->name table we do not ship, so the
    # font is kept whole (never erase a used glyph).
    pdf, ff_num, _ = _embed_type1(shown_codes=[0x41], pdf_encoding="WinAnsiEncoding")
    original = pdf._cos_doc.objects[ff_num].content
    pdf.optimize(_subset_opts())
    assert pdf._cos_doc.objects[ff_num].content == original

"""Unit tests for the dependency-free TrueType glyph-erasure subsetter."""

import struct

from aspose_pdf.engine.font_subset import (
    read_symbol_code_to_gid,
    read_unicode_cmap,
    subset_truetype,
)
from aspose_pdf.engine.sfnt import parse_faces

_CHECKSUM_MAGIC = 0xB1B0AFBA


# ---------------------------------------------------------------------------
# Minimal TrueType builder
# ---------------------------------------------------------------------------


def _simple_glyph(payload_len: int) -> bytes:
    """A 'simple' glyph: numberOfContours >= 0 then arbitrary opaque bytes.

    The subsetter copies glyph bytes verbatim and only inspects the leading
    numberOfContours (and, for composites, the component records), so opaque
    bodies are sufficient for exercising glyph erasure and closure.
    """
    return struct.pack(">hHHHH", 1, 0, 0, 100, 100) + b"\xAB" * payload_len


def _composite_glyph(component_gid: int) -> bytes:
    """A composite glyph (numberOfContours < 0) referencing *component_gid*."""
    header = struct.pack(">hhhhh", -1, 0, 0, 100, 100)  # numContours + bbox
    flags = 0x0001  # ARG_1_AND_2_ARE_WORDS, no MORE_COMPONENTS
    component = struct.pack(">HH", flags, component_gid) + struct.pack(">hh", 0, 0)
    return header + component


def _pad4(data: bytes) -> bytes:
    return data + b"\x00" * ((4 - len(data) % 4) % 4)


def _build_ttf(
    glyphs: list[bytes],
    *,
    index_to_loc: int = 1,
    extra_tables: dict[str, bytes] | None = None,
) -> bytes:
    """Assemble a minimal TrueType font from raw *glyphs* (one blob per gid)."""
    num_glyphs = len(glyphs)

    glyf = b"".join(glyphs)
    offsets = [0]
    for g in glyphs:
        offsets.append(offsets[-1] + len(g))
    if index_to_loc == 0:
        loca = b"".join(struct.pack(">H", o // 2) for o in offsets)
    else:
        loca = b"".join(struct.pack(">I", o) for o in offsets)

    head = (
        struct.pack(">III", 0x00010000, 0, 0)  # version, fontRevision, checkSumAdj
        + struct.pack(">I", 0x5F0F3CF5)  # magicNumber
        + struct.pack(">HH", 0, 1000)  # flags, unitsPerEm
        + struct.pack(">qq", 0, 0)  # created, modified
        + struct.pack(">hhhh", 0, 0, 100, 100)  # bbox
        + struct.pack(">HHh", 0, 8, 0)  # macStyle, lowestRecPPEM, fontDirectionHint
        + struct.pack(">hh", index_to_loc, 0)  # indexToLocFormat, glyphDataFormat
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


def _parse_tables(font: bytes) -> dict[str, tuple[int, int]]:
    num_tables = struct.unpack_from(">H", font, 4)[0]
    out = {}
    for i in range(num_tables):
        rec = 12 + 16 * i
        tag = font[rec : rec + 4].decode("latin-1")
        off, length = struct.unpack_from(">II", font, rec + 8)
        out[tag] = (off, length)
    return out


def _loca_offsets(font: bytes) -> list[int]:
    tables = _parse_tables(font)
    head_off = tables["head"][0]
    maxp_off = tables["maxp"][0]
    index_to_loc = struct.unpack_from(">h", font, head_off + 50)[0]
    num_glyphs = struct.unpack_from(">H", font, maxp_off + 4)[0]
    loca_off = tables["loca"][0]
    offsets = []
    for i in range(num_glyphs + 1):
        if index_to_loc == 0:
            offsets.append(struct.unpack_from(">H", font, loca_off + i * 2)[0] * 2)
        else:
            offsets.append(struct.unpack_from(">I", font, loca_off + i * 4)[0])
    return offsets


def _whole_file_checksum(data: bytes) -> int:
    if len(data) % 4:
        data = data + b"\x00" * (4 - len(data) % 4)
    total = 0
    for i in range(0, len(data), 4):
        total = (total + struct.unpack_from(">I", data, i)[0]) & 0xFFFFFFFF
    return total


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def _five_glyph_font(index_to_loc: int = 1) -> bytes:
    # gid 0 .notdef (empty), 1..3 simple of growing size, 4 composite -> gid 3.
    return _build_ttf(
        [
            b"",
            _simple_glyph(40),
            _simple_glyph(80),
            _simple_glyph(120),
            _composite_glyph(3),
        ],
        index_to_loc=index_to_loc,
    )


def test_subset_keeps_used_and_empties_unused():
    font = _five_glyph_font()
    # Keep gid 2 only; gid 0 is forced in. gids 1, 3, 4 should be emptied.
    out = subset_truetype(font, {2})
    assert out is not None

    offsets = _loca_offsets(out)
    assert len(offsets) == 6  # numGlyphs (5) + 1, unchanged
    # gid 2 retains its outline; 1/3/4 are emptied (zero-length loca slots).
    assert offsets[3] - offsets[2] > 0  # gid 2 present
    assert offsets[2] - offsets[1] == 0  # gid 1 emptied
    assert offsets[4] - offsets[3] == 0  # gid 3 emptied
    assert offsets[5] - offsets[4] == 0  # gid 4 emptied


def test_subset_pulls_in_composite_components():
    font = _five_glyph_font()
    # Keeping the composite gid 4 must retain its component gid 3.
    out = subset_truetype(font, {4})
    assert out is not None
    offsets = _loca_offsets(out)
    assert offsets[5] - offsets[4] > 0  # gid 4 (composite) kept
    assert offsets[4] - offsets[3] > 0  # gid 3 (component) pulled in
    assert offsets[2] - offsets[1] == 0  # gid 1 still emptied


def test_subset_shrinks_and_switches_to_long_loca():
    font = _five_glyph_font(index_to_loc=0)  # source uses short loca
    out = subset_truetype(font, {1})
    assert out is not None
    assert len(out) < len(font)
    head_off = _parse_tables(out)["head"][0]
    assert struct.unpack_from(">h", out, head_off + 50)[0] == 1  # long loca


def test_subset_output_has_valid_checksums():
    font = _five_glyph_font()
    out = subset_truetype(font, {1, 2})
    assert out is not None
    # After checkSumAdjustment, the whole-file checksum equals the magic value.
    assert _whole_file_checksum(out) == _CHECKSUM_MAGIC


def test_subset_output_still_parses_as_truetype():
    font = _five_glyph_font()
    out = subset_truetype(font, {2})
    assert out is not None
    faces = parse_faces(out)
    assert len(faces) == 1
    assert faces[0].font_type == "TrueType"
    assert "glyf" in faces[0].table_tags


def test_subset_returns_none_for_non_truetype():
    assert subset_truetype(b"OTTO\x00\x00\x00\x00\x00\x00\x00\x00", set()) is None
    assert subset_truetype(b"not a font at all", {1}) is None
    assert subset_truetype(b"", {1}) is None


def test_subset_returns_none_when_not_smaller():
    # A font whose only glyph is kept cannot shrink -> None.
    font = _build_ttf([b"", _simple_glyph(20)])
    assert subset_truetype(font, {0, 1}) is None


# ---------------------------------------------------------------------------
# Symbol cmap reader (best-effort simple /TrueType subsetting)
# ---------------------------------------------------------------------------


def _cmap_wrapper(platform: int, encoding: int, subtable: bytes) -> bytes:
    header = struct.pack(">HH", 0, 1)  # version, numTables
    record = struct.pack(">HHI", platform, encoding, 12)  # plat, enc, offset
    return header + record + subtable


def _cmap_format0(glyph_ids: list[int]) -> bytes:
    sub = struct.pack(">HHH", 0, 262, 0) + bytes(glyph_ids)  # format, length, lang
    return _cmap_wrapper(3, 0, sub)


def _cmap_format4(segments: list[tuple[int, int, int]]) -> bytes:
    # Each segment is (startCode, endCode, idDelta); a 0xFFFF terminator is added.
    segs = list(segments) + [(0xFFFF, 0xFFFF, 1)]
    end_codes = b"".join(struct.pack(">H", e) for _, e, _ in segs)
    start_codes = b"".join(struct.pack(">H", s) for s, _, _ in segs)
    id_deltas = b"".join(struct.pack(">h", d) for _, _, d in segs)
    id_ranges = b"".join(struct.pack(">H", 0) for _ in segs)
    body = (
        struct.pack(">H", len(segs) * 2)  # segCountX2
        + struct.pack(">HHH", 0, 0, 0)  # search params (unused by the reader)
        + end_codes
        + struct.pack(">H", 0)  # reservedPad
        + start_codes
        + id_deltas
        + id_ranges
    )
    sub = struct.pack(">HHH", 4, 6 + len(body), 0) + body  # format, length, lang
    return _cmap_wrapper(3, 0, sub)


def test_read_symbol_code_to_gid_format4():
    cmap = _cmap_format4([(0x41, 0x43, 5)])  # 'A'..'C' -> gid = code + 5
    font = _build_ttf([b"", _simple_glyph(10)], extra_tables={"cmap": cmap})
    mapping = read_symbol_code_to_gid(font)
    assert mapping[0x41] == 0x46
    assert mapping[0x42] == 0x47
    assert mapping[0x43] == 0x48
    assert 0x40 not in mapping


def test_read_symbol_code_to_gid_format0():
    ids = [0] * 256
    ids[0x41], ids[0x42] = 7, 8
    font = _build_ttf([b"", _simple_glyph(10)], extra_tables={"cmap": _cmap_format0(ids)})
    mapping = read_symbol_code_to_gid(font)
    assert mapping[0x41] == 7
    assert mapping[0x42] == 8
    assert 0x43 not in mapping  # glyph id 0 means "unmapped"


def test_read_symbol_code_to_gid_absent_cmap():
    font = _build_ttf([b"", _simple_glyph(10)])
    assert read_symbol_code_to_gid(font) == {}


# ---------------------------------------------------------------------------
# Integration: SimplePdf.optimize(subset_fonts=True) on embedded fonts
# ---------------------------------------------------------------------------


def _make_pdf_with_type0_font(font_bytes, shown_cids, *, fontfile_key="FontFile2"):
    """One-page COS PDF embedding *font_bytes* as a Type0/CIDFontType2 font."""
    from aspose_pdf.engine.simple_pdf import SimplePdf
    from aspose_pdf.engine.cos import (
        PdfArray,
        PdfDictionary,
        PdfName,
        PdfNumber,
        PdfStream,
    )

    hexcodes = "".join(f"{c:04x}" for c in shown_cids)
    content = ("BT /F0 12 Tf <" + hexcodes + "> Tj ET").encode("latin-1")

    pdf = SimplePdf()
    pdf.pages = [(0, 0, 200, 200)]
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
                PdfName(fontfile_key): ff,
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
    return pdf, ff.object_number


def _subset_opts(**kwargs):
    from aspose_pdf import OptimizationOptions

    base = dict(
        remove_unused_objects=False,
        remove_unused_streams=False,
        link_duplicate_streams=False,
        remove_duplicate_images=False,
        compress_fonts=False,  # keep the subset program readable (uncompressed)
    )
    base.update(kwargs)
    return OptimizationOptions(**base)


def test_optimize_subsets_embedded_type0_truetype():
    font = _five_glyph_font()
    pdf, ff_num = _make_pdf_with_type0_font(font, shown_cids=[2])
    original = pdf._cos_doc.objects[ff_num].content

    pdf.optimize(_subset_opts(subset_fonts=True))

    new_program = pdf._cos_doc.objects[ff_num].content
    assert len(new_program) < len(original)
    offsets = _loca_offsets(new_program)
    assert offsets[3] - offsets[2] > 0  # gid 2 (CID 2 via Identity) kept
    assert offsets[2] - offsets[1] == 0  # gid 1 emptied
    assert offsets[4] - offsets[3] == 0  # gid 3 emptied
    assert parse_faces(new_program)[0].font_type == "TrueType"


def test_optimize_subset_keeps_composite_components_via_type0():
    font = _five_glyph_font()
    pdf, ff_num = _make_pdf_with_type0_font(font, shown_cids=[4])  # composite gid 4

    pdf.optimize(_subset_opts(subset_fonts=True))

    offsets = _loca_offsets(pdf._cos_doc.objects[ff_num].content)
    assert offsets[5] - offsets[4] > 0  # composite gid 4 kept
    assert offsets[4] - offsets[3] > 0  # component gid 3 pulled in
    assert offsets[2] - offsets[1] == 0  # gid 1 still emptied


def test_optimize_leaves_fonts_untouched_when_subset_off():
    font = _five_glyph_font()
    pdf, ff_num = _make_pdf_with_type0_font(font, shown_cids=[2])
    before = pdf._cos_doc.objects[ff_num].content

    pdf.optimize(_subset_opts())  # subset_fonts defaults off

    assert pdf._cos_doc.objects[ff_num].content == before


def test_optimize_skips_non_truetype_fontfile3():
    font = _five_glyph_font()
    # Embedded as FontFile3 (CFF) -> not a TrueType program, must be left whole.
    pdf, ff_num = _make_pdf_with_type0_font(
        font, shown_cids=[2], fontfile_key="FontFile3"
    )
    before = pdf._cos_doc.objects[ff_num].content

    pdf.optimize(_subset_opts(subset_fonts=True))

    assert pdf._cos_doc.objects[ff_num].content == before


def test_optimize_subset_survives_save_roundtrip():
    from aspose_pdf.engine.simple_pdf import SimplePdf

    font = _five_glyph_font()
    pdf, _ = _make_pdf_with_type0_font(font, shown_cids=[2])
    pdf.optimize(_subset_opts(subset_fonts=True))

    out = pdf.to_bytes()
    assert out.startswith(b"%PDF")
    reopened = SimplePdf.from_bytes(out)
    try:
        assert len(reopened.pages) == 1
    finally:
        reopened.dispose()


def _make_pdf_with_simple_truetype_font(font_bytes, shown_text):
    """One-page COS PDF embedding *font_bytes* as a simple /TrueType font."""
    from aspose_pdf.engine.simple_pdf import SimplePdf
    from aspose_pdf.engine.cos import (
        PdfDictionary,
        PdfName,
        PdfNumber,
        PdfStream,
    )

    content = ("BT /F0 12 Tf (" + shown_text + ") Tj ET").encode("latin-1")
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 200, 200)]
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
    font = cos.register_object(
        PdfDictionary(
            {
                PdfName("Type"): PdfName("Font"),
                PdfName("Subtype"): PdfName("TrueType"),
                PdfName("BaseFont"): PdfName("AAAAAA+Test"),
                PdfName("FontDescriptor"): descriptor,
            }
        )
    )
    page = pdf._get_page_dict(0)
    page.mapping[PdfName("Resources")] = PdfDictionary(
        {PdfName("Font"): PdfDictionary({PdfName("F0"): font})}
    )
    return pdf, ff.object_number


def test_optimize_subsets_simple_truetype_via_symbol_cmap():
    glyphs = [b"", _simple_glyph(40), _simple_glyph(80), _simple_glyph(120)]
    cmap = _cmap_format4([(0x41, 0x42, -0x40)])  # 'A' -> gid 1, 'B' -> gid 2
    font = _build_ttf(glyphs, extra_tables={"cmap": cmap})
    pdf, ff_num = _make_pdf_with_simple_truetype_font(font, "A")  # draws gid 1 only
    original = pdf._cos_doc.objects[ff_num].content

    pdf.optimize(_subset_opts(subset_fonts=True))

    new_program = pdf._cos_doc.objects[ff_num].content
    assert len(new_program) < len(original)
    offsets = _loca_offsets(new_program)
    assert offsets[2] - offsets[1] > 0  # gid 1 ('A') kept
    assert offsets[3] - offsets[2] == 0  # gid 2 ('B') erased
    assert offsets[4] - offsets[3] == 0  # gid 3 erased


# ---------------------------------------------------------------------------
# Simple /TrueType subsetting via the PDF /Encoding + a Unicode cmap
# ---------------------------------------------------------------------------


def _cmap_format4_unicode(segments):
    """A (3, 1) Windows-Unicode format-4 cmap subtable."""
    segs = list(segments) + [(0xFFFF, 0xFFFF, 1)]
    end_codes = b"".join(struct.pack(">H", e) for _, e, _ in segs)
    start_codes = b"".join(struct.pack(">H", s) for s, _, _ in segs)
    id_deltas = b"".join(struct.pack(">h", d) for _, _, d in segs)
    id_ranges = b"".join(struct.pack(">H", 0) for _ in segs)
    body = (
        struct.pack(">H", len(segs) * 2)
        + struct.pack(">HHH", 0, 0, 0)
        + end_codes
        + struct.pack(">H", 0)
        + start_codes
        + id_deltas
        + id_ranges
    )
    sub = struct.pack(">HHH", 4, 6 + len(body), 0) + body
    return _cmap_wrapper(3, 1, sub)


def _latin_font():
    # gid 0 .notdef, 1='A', 2='B', 3='C' via a (3,1) cmap.
    glyphs = [b"", _simple_glyph(40), _simple_glyph(80), _simple_glyph(120)]
    cmap = _cmap_format4_unicode([(0x41, 0x43, -0x40)])  # 'A'->1,'B'->2,'C'->3
    return _build_ttf(glyphs, extra_tables={"cmap": cmap})


def _embed_simple_tt(font_bytes, shown: bytes, encoding=None):
    from aspose_pdf.engine.cos import (
        PdfDictionary,
        PdfName,
        PdfNumber,
        PdfStream,
    )
    from aspose_pdf.engine.simple_pdf import SimplePdf

    content = b"BT /F0 12 Tf (" + shown + b") Tj ET"
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 200, 200)]
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
    font_map = {
        PdfName("Type"): PdfName("Font"),
        PdfName("Subtype"): PdfName("TrueType"),
        PdfName("BaseFont"): PdfName("AAAAAA+Test"),
        PdfName("FontDescriptor"): descriptor,
    }
    if encoding is not None:
        font_map[PdfName("Encoding")] = encoding
    font = cos.register_object(PdfDictionary(font_map))
    page = pdf._get_page_dict(0)
    page.mapping[PdfName("Resources")] = PdfDictionary(
        {PdfName("Font"): PdfDictionary({PdfName("F0"): font})}
    )
    return pdf, ff.object_number


def test_read_unicode_cmap():
    assert read_unicode_cmap(_latin_font()) == {0x41: 1, 0x42: 2, 0x43: 3}


def test_simple_truetype_subset_via_winansi_encoding():
    from aspose_pdf.engine.cos import PdfName

    pdf, ff_num = _embed_simple_tt(
        _latin_font(), b"A", encoding=PdfName("WinAnsiEncoding")
    )
    original = pdf._cos_doc.objects[ff_num].content

    pdf.optimize(_subset_opts(subset_fonts=True))

    new_program = pdf._cos_doc.objects[ff_num].content
    assert len(new_program) < len(original)
    offsets = _loca_offsets(new_program)
    assert offsets[2] - offsets[1] > 0  # gid 1 ('A') kept
    assert offsets[3] - offsets[2] == 0  # gid 2 ('B') erased
    assert offsets[4] - offsets[3] == 0  # gid 3 ('C') erased


def test_simple_truetype_subset_via_encoding_differences():
    from aspose_pdf.engine.cos import PdfArray, PdfDictionary, PdfName, PdfNumber

    # Remap code 0x80 to 'B' (U+0042) through /Differences with a uniXXXX name.
    encoding = PdfDictionary(
        {
            PdfName("Type"): PdfName("Encoding"),
            PdfName("BaseEncoding"): PdfName("WinAnsiEncoding"),
            PdfName("Differences"): PdfArray([PdfNumber(0x80), PdfName("uni0042")]),
        }
    )
    pdf, ff_num = _embed_simple_tt(_latin_font(), b"\x80", encoding=encoding)

    pdf.optimize(_subset_opts(subset_fonts=True))

    offsets = _loca_offsets(pdf._cos_doc.objects[ff_num].content)
    assert offsets[3] - offsets[2] > 0  # gid 2 ('B') kept
    assert offsets[2] - offsets[1] == 0  # gid 1 ('A') erased
    assert offsets[4] - offsets[3] == 0  # gid 3 ('C') erased


def test_simple_truetype_unresolved_encoding_left_whole():
    # An unsupported base (StandardEncoding, no codec) cannot resolve the used
    # code, so the subsetter must keep the font intact rather than risk erasing
    # a used glyph.
    from aspose_pdf.engine.cos import PdfName

    pdf, ff_num = _embed_simple_tt(
        _latin_font(), b"A", encoding=PdfName("StandardEncoding")
    )
    before = pdf._cos_doc.objects[ff_num].content

    pdf.optimize(_subset_opts(subset_fonts=True))

    assert pdf._cos_doc.objects[ff_num].content == before


def test_simple_truetype_unknown_difference_name_left_whole():
    from aspose_pdf.engine.cos import PdfArray, PdfDictionary, PdfName, PdfNumber

    # A non-algorithmic glyph name on a *used* code is unresolvable -> bail.
    encoding = PdfDictionary(
        {
            PdfName("BaseEncoding"): PdfName("WinAnsiEncoding"),
            PdfName("Differences"): PdfArray([PdfNumber(0x41), PdfName("oddball")]),
        }
    )
    pdf, ff_num = _embed_simple_tt(_latin_font(), b"A", encoding=encoding)
    before = pdf._cos_doc.objects[ff_num].content

    pdf.optimize(_subset_opts(subset_fonts=True))

    assert pdf._cos_doc.objects[ff_num].content == before

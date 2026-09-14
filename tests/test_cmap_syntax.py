"""CMaps are PostScript: line breaks mean nothing in them.

Every CMap reader -- ToUnicode, and an embedded Encoding CMap's codespaces,
``cidrange``, ``cidchar`` and ``WMode`` -- went line by line and recognised an
operator only at the end of a line. A CMap written on one line, or with its
first entry on the ``beginbfchar`` line, or an entry split across two lines,
gave no mappings: the text came out as raw codes. ``bfrange`` destinations were
read as one integer, so a ligature (``<00660066>``) or a surrogate pair
(``<D835DC00>``) overflowed ``chr`` and the range was dropped.

Every expectation below is what pdfium, MuPDF and pdfminer.six extract from the
same file (pdfminer does not read embedded Encoding CMaps; pdfium and MuPDF
agree there).
"""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.content_stream_parser import (
    parse_encoding_cmap,
    parse_encoding_cmap_codespaces,
    parse_encoding_cmap_wmode,
    parse_to_unicode_cmap,
)

_CODESPACE = b"1 begincodespacerange <0000> <FFFF> endcodespacerange\n"


def _to_unicode(body: bytes) -> dict[bytes, str]:
    return parse_to_unicode_cmap(_CODESPACE + body)


# --- ToUnicode layouts -----------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        b"2 beginbfchar\n<0001> <005A>\n<0002> <0051>\nendbfchar",
        b"2 beginbfchar <0001> <005A> <0002> <0051> endbfchar",
        b"2 beginbfchar <0001> <005A>\n<0002> <0051> endbfchar",
        b"2 beginbfchar\n<0001>\n<005A>\n<0002>\n<0051>\nendbfchar",
        b"2 beginbfchar\r<0001> <005A>\r<0002> <0051>\rendbfchar",
        b"2 beginbfchar % codes\n<0001> <005A> % zed\n<0002> <0051>\nendbfchar",
        b"2 beginbfchar <00 01> <00 5A> <0002> <0051> endbfchar",
    ],
    ids=["lines", "one-line", "on-begin-line", "split-entries", "cr-only", "comments", "spaced-hex"],
)
def test_bfchar_maps_however_it_is_laid_out(body):
    assert _to_unicode(body) == {b"\x00\x01": "Z", b"\x00\x02": "Q"}


def test_two_blocks_on_one_line_both_map():
    cmap = _to_unicode(b"1 beginbfchar <0001> <005A> endbfchar 1 beginbfrange <0002> <0003> <0051> endbfrange")
    assert cmap == {b"\x00\x01": "Z", b"\x00\x02": "Q", b"\x00\x03": "R"}


def test_a_one_line_bfrange_counts_up():
    assert _to_unicode(b"1 beginbfrange <0001> <0003> <0041> endbfrange") == {
        b"\x00\x01": "A",
        b"\x00\x02": "B",
        b"\x00\x03": "C",
    }


def test_a_bfrange_destination_counts_up_in_its_last_code_unit():
    ligatures = _to_unicode(b"1 beginbfrange\n<0001> <0003> <00660066>\nendbfrange")
    assert list(ligatures.values()) == ["ff", "fg", "fh"]
    astral = _to_unicode(b"1 beginbfrange\n<0001> <0003> <D835DC00>\nendbfrange")
    assert list(astral.values()) == ["\U0001d400", "\U0001d401", "\U0001d402"]


def test_a_bfrange_destination_array_is_used_entry_by_entry():
    cmap = _to_unicode(b"1 beginbfrange <0001> <0002> [<00660069> <0041>]endbfrange")
    assert cmap == {b"\x00\x01": "fi", b"\x00\x02": "A"}
    # A name in the array is skipped and keeps its place, as in pdfium.
    cmap = _to_unicode(b"1 beginbfrange <0001> <0003> [<0041> /B <0043>] endbfrange")
    assert cmap == {b"\x00\x01": "A", b"\x00\x03": "C"}


def test_a_bfrange_counts_to_the_top_of_the_code_unit_and_stops():
    # pdfminer and MuPDF map U+FFFE and U+FFFF; beyond that they disagree.
    cmap = _to_unicode(b"1 beginbfrange <0001> <0003> <FFFE> endbfrange")
    assert cmap[b"\x00\x01"] == "\ufffe" and cmap[b"\x00\x02"] == "\uffff"


def test_a_bfrange_bound_shorter_than_the_other_still_counts():
    # pdfium and MuPDF read <0001> <03> as codes 0001 to 0003.
    cmap = _to_unicode(b"1 beginbfrange <0001> <03> <0041> endbfrange")
    assert cmap == {b"\x00\x01": "A", b"\x00\x02": "B", b"\x00\x03": "C"}


def test_a_one_byte_destination_is_not_text():
    # None of pdfium, MuPDF or pdfminer maps it.
    assert _to_unicode(b"1 beginbfrange <0001> <0003> <41> endbfrange") == {}


def test_strings_and_comments_do_not_open_blocks():
    cmap = parse_to_unicode_cmap(
        b"/CMapName (x 1 beginbfchar <0001> <0058> endbfchar) def\n"
        b"% 1 beginbfchar <0001> <0059> endbfchar\n"
        b"1 beginbfchar <0001> <005A> endbfchar"
    )
    assert cmap == {b"\x00\x01": "Z"}


def test_an_escaped_parenthesis_does_not_end_a_string():
    # pdfminer and MuPDF; pdfium ends the string early and maps X.
    cmap = parse_to_unicode_cmap(
        b"1 beginbfchar <0001> <005A> endbfchar\n"
        b"/Foo (a\\) 1 beginbfchar <0001> <0058> endbfchar) def"
    )
    assert cmap == {b"\x00\x01": "Z"}


def test_an_odd_final_hex_digit_is_followed_by_zero():
    # 7.3.4.3, and MuPDF: <005> is <0050>, "P".
    assert _to_unicode(b"1 beginbfchar <0001> <005> endbfchar") == {b"\x00\x01": "P"}


def test_a_short_destination_array_maps_the_codes_it_covers():
    # pdfminer and MuPDF map A and B and leave the third code unmapped.
    cmap = _to_unicode(b"1 beginbfrange <0001> <0003> [<0041> <0042>] endbfrange")
    assert cmap == {b"\x00\x01": "A", b"\x00\x02": "B"}


def test_a_block_that_never_ends_maps_nothing_and_the_next_one_still_does():
    # pdfminer reads it this way; pdfium and MuPDF each differ from the other.
    cmap = _to_unicode(b"1 beginbfchar <0001> <005A> 1 beginbfrange <0002> <0003> <0051> endbfrange")
    assert cmap == {b"\x00\x02": "Q", b"\x00\x03": "R"}


def test_a_malformed_entry_is_skipped_not_the_block():
    # As pdfium and pdfminer read it; MuPDF abandons the whole CMap.
    cmap = _to_unicode(b"3 beginbfchar <0001> /space <0002> <0051> <0003> <0052> endbfchar")
    assert cmap[b"\x00\x02"] == "Q" and cmap[b"\x00\x03"] == "R"
    assert b"\x00\x01" not in cmap


# --- embedded Encoding CMaps ------------------------------------------------------------------

_ENCODING_ONE_LINE = (
    b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CMapName /Mixed def "
    b"/CMapType 1 def /WMode 1 def 2 begincodespacerange <00> <80> <8140> <FFFF> endcodespacerange "
    b"1 begincidrange <00> <80> 0 endcidrange 1 begincidchar <8140> 200 endcidchar "
    b"endcmap CMapName currentdict /CMap defineresource pop end end"
)


def test_a_one_line_encoding_cmap_is_read_whole():
    code_to_cid, lengths = parse_encoding_cmap(_ENCODING_ONE_LINE)
    assert lengths == [1, 2]
    assert code_to_cid[b"\x41"] == 0x41
    assert code_to_cid[b"\x81\x40"] == 200
    assert parse_encoding_cmap_codespaces(_ENCODING_ONE_LINE) == (
        (b"\x00", b"\x80"),
        (b"\x81\x40", b"\xff\xff"),
    )
    assert parse_encoding_cmap_wmode(_ENCODING_ONE_LINE) == 1


def test_code_lengths_come_from_the_codespaces_as_well():
    code_to_cid, lengths = parse_encoding_cmap(
        b"2 begincodespacerange <00> <80> <8140> <FFFF> endcodespacerange 1 begincidrange <00> <80> 0 endcidrange"
    )
    assert lengths == [1, 2]
    assert len(code_to_cid) == 0x81
    assert parse_encoding_cmap_codespaces(b"1 begincodespacerange <FF> <00> endcodespacerange") == ()


def test_encoding_entries_without_a_numeric_cid_are_skipped():
    code_to_cid, _lengths = parse_encoding_cmap(
        b"2 begincidrange <00> <01> /x <02> <03> 7 endcidrange 2 begincidchar <8140> (y) <8141> 9 endcidchar"
    )
    assert code_to_cid == {b"\x02": 7, b"\x03": 8, b"\x81\x41": 9}


# --- through a document -----------------------------------------------------------------------


def _stream(body: bytes, dictionary: bytes = b"") -> bytes:
    return b"<< %s /Length %d >>\nstream\n" % (dictionary, len(body)) + body + b"\nendstream"


def _document(to_unicode: bytes, codes: bytes, encoding: bytes | None = None) -> Document:
    head = (
        b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CIDSystemInfo << /Registry (Adobe) "
        b"/Ordering (UCS) /Supplement 0 >> def /CMapName /Adobe-Identity-UCS def /CMapType 2 def\n"
    )
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
        3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 100] /Resources << /Font << /F1 11 0 R >> >> "
        b"/Contents 4 0 R >>",
        4: _stream(b"BT /F1 20 Tf 10 40 Td " + codes + b" Tj ET"),
        11: b"<< /Type /Font /Subtype /Type0 /BaseFont /ArialMT /Encoding "
        + (b"15 0 R" if encoding else b"/Identity-H")
        + b" /DescendantFonts [12 0 R] /ToUnicode 13 0 R >>",
        12: b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /ArialMT /CIDSystemInfo << /Registry (Adobe) "
        b"/Ordering (Identity) /Supplement 0 >> /DW 600 /FontDescriptor 14 0 R >>",
        13: _stream(head + to_unicode + b"\nendcmap CMapName currentdict /CMap defineresource pop end end"),
        14: b"<< /Type /FontDescriptor /FontName /ArialMT /Flags 32 /FontBBox [0 -200 1000 900] /ItalicAngle 0 "
        b"/Ascent 900 /Descent -200 /CapHeight 700 /StemV 80 >>",
    }
    if encoding:
        objects[15] = _stream(encoding, b"/Type /CMap /CMapName /Mixed")
    raw = bytearray(b"%PDF-1.7\n")
    offsets = {}
    for number in sorted(objects):
        offsets[number] = len(raw)
        raw += b"%d 0 obj\n" % number + objects[number] + b"\nendobj\n"
    start = len(raw)
    size = max(objects) + 1
    raw += b"xref\n0 %d\n" % size
    for number in range(size):
        raw += (b"%010d 00000 n \n" % offsets[number]) if number in offsets else b"0000000000 65535 f \n"
    raw += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (size, start)
    return Document(io.BytesIO(bytes(raw)))


@pytest.mark.parametrize(
    ("to_unicode", "codes", "expected"),
    [
        (_CODESPACE + b"2 beginbfchar <0001> <005A> <0002> <0051> endbfchar", b"<00010002>", "ZQ"),
        (_CODESPACE + b"1 beginbfrange\n<0001> <0003> <00660066>\nendbfrange", b"<000100020003>", "fffgfh"),
        (_CODESPACE + b"1 beginbfrange\n<0001> <0003> <D835DC00>\nendbfrange", b"<000100020003>", "\U0001d400\U0001d401\U0001d402"),
    ],
    ids=["one-line", "ligature-range", "astral-range"],
)
def test_page_text_decodes_through_the_cmap(to_unicode, codes, expected):
    assert _document(to_unicode, codes).pages[0].extract_text().strip() == expected


def test_a_one_line_encoding_cmap_segments_mixed_length_codes():
    to_unicode = (
        b"1 begincodespacerange <00> <80> endcodespacerange\n1 begincodespacerange <8140> <FFFF> endcodespacerange\n"
        b"3 beginbfchar\n<41> <0041>\n<8140> <3042>\n<42> <0042>\nendbfchar"
    )
    encoding = (
        b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CMapName /Mixed def /CMapType 1 def "
        b"2 begincodespacerange <00> <80> <8140> <FFFF> endcodespacerange 1 begincidrange <00> <80> 0 endcidrange "
        b"1 begincidrange <8140> <FFFF> 200 endcidrange endcmap CMapName currentdict /CMap defineresource pop end end"
    )
    document = _document(to_unicode, b"<41814042>", encoding)
    assert document.pages[0].extract_text().strip() == "AあB"

# XRef Streams and Object Streams Tests

"""Tests for parsing PDFs that use XRef streams and Object streams.

The tests construct minimal PDFs as raw byte strings and feed them to the
`PdfCosParser`.  They verify that the parser produces a trailer dictionary
with expected entries and that objects stored in an object stream are
correctly extracted.

Only the standard library and the aspose_pdf engine are used.  No external
PDF libraries are required.
"""

import zlib


from aspose_pdf.engine.pdf_parser_cos import PdfCosParser
from aspose_pdf.engine.cos import (
    PdfName,
    PdfDictionary,
    PdfNumber,
    PdfIndirectReference,
)


def _build_xref_stream_pdf() -> bytes:
    """Create a minimal PDF that uses an XRef stream.

    The PDF contains three objects: a catalog (1 0), a pages tree (2 0) and the
    XRef stream itself (3 0).  The XRef stream dictionary includes the required
    entries ``/W``, ``/Size`` and ``/Root`` and uses FlateDecode compression.
    """
    header = b"%PDF-1.7\n"
    obj1 = b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    obj2 = b"2 0 obj\n<< /Type /Pages /Count 0 >>\nendobj\n"

    # Offsets for objects (starting from the beginning of the file)
    offset1 = len(header)
    offset2 = offset1 + len(obj1)
    # The XRef stream object will follow obj2
    xref_offset = offset2 + len(obj2)

    # XRef stream entries: /W [1 4 0] -> each entry is 5 bytes (type + offset)
    entry0 = b"\x00" + (0).to_bytes(4, "big")  # free entry
    entry1 = b"\x01" + offset1.to_bytes(4, "big")
    entry2 = b"\x01" + offset2.to_bytes(4, "big")
    entry3 = b"\x01" + xref_offset.to_bytes(4, "big")
    xref_stream_data = entry0 + entry1 + entry2 + entry3
    compressed = zlib.compress(xref_stream_data)

    # Build XRef stream object with stream length
    xref_stream_dict = (
        b"<< /Type /XRef /Filter /FlateDecode /W [1 4 0] /Size 4 /Root 1 0 R /Length "
        + str(len(compressed)).encode()
        + b" >>\n"
    )
    xref_obj = (
        b"3 0 obj\n" + xref_stream_dict + b"stream\n" + compressed + b"\nendstream\n"
        b"endobj\n"
    )

    startxref_section = b"startxref\n" + str(xref_offset).encode() + b"\n%%EOF\n"
    return header + obj1 + obj2 + xref_obj + startxref_section


def _build_object_stream_pdf() -> bytes:
    """Create a minimal PDF that uses a traditional xref with an Object Stream.

    The catalog references an ObjStm which contains object 4.
    """
    header = b"%PDF-1.7\n"

    # Build object stream content first
    obj4_content = b"<< /Type /Example /Value 42 >>"
    pair = b"4 0 "  # object number 4, offset 0
    stream_body = pair + obj4_content
    compressed_body = zlib.compress(stream_body)

    obj1 = b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    obj2 = b"2 0 obj\n<< /Type /Pages /Count 0 >>\nendobj\n"

    offset1 = len(header)
    offset2 = offset1 + len(obj1)
    objst_offset = offset2 + len(obj2)

    objst = (
        b"3 0 obj\n"
        b"<< /Type /ObjStm /Filter /FlateDecode /N 1 /First "
        + str(len(pair)).encode()
        + b" /Length "
        + str(len(compressed_body)).encode()
        + b" >>\n"
        b"stream\n" + compressed_body + b"\nendstream\n"
        b"endobj\n"
    )

    # Traditional xref table
    xref_offset = objst_offset + len(objst)
    xref = (
        b"xref\n0 4\n0000000000 65535 f \n%010d 00000 n \n%010d 00000 n \n%010d 00000 n \n"
        % (offset1, offset2, objst_offset)
    )
    trailer = b"trailer\n<< /Size 4 /Root 1 0 R >>\n"
    startxref = b"startxref\n" + str(xref_offset).encode() + b"\n%%EOF\n"

    return header + obj1 + obj2 + objst + xref + trailer + startxref


def test_parse_xref_stream_minimal():
    """Test parsing a PDF with XRef Stream."""
    data = _build_xref_stream_pdf()
    parser = PdfCosParser(data)
    parser.parse()

    trailer = parser.trailer
    assert isinstance(trailer, PdfDictionary)

    size_obj = trailer.mapping.get(PdfName("Size"))
    assert isinstance(size_obj, PdfNumber)
    assert size_obj.value == 4

    root_obj = trailer.mapping.get(PdfName("Root"))
    assert isinstance(root_obj, PdfIndirectReference)

    # Verify that the referenced object is a catalog dictionary
    catalog = parser.get_object(root_obj)
    assert isinstance(catalog, PdfDictionary)
    assert catalog.mapping.get(PdfName("Type")).name == "/Catalog"


def test_parse_object_stream():
    """Test parsing a PDF with Object Stream."""
    data = _build_object_stream_pdf()
    parser = PdfCosParser(data)
    parser.parse()

    # Object 3 should be the ObjStm
    objstm_ref = PdfIndirectReference(3, 0)
    objstm = parser.get_object(objstm_ref)
    assert objstm is not None

    # Manually extract object 4 from the object stream
    from aspose_pdf.engine.filters import StreamDecoder
    from aspose_pdf.engine.cos import PdfStream

    if isinstance(objstm, PdfStream):
        content = StreamDecoder.decode(objstm.content, "FlateDecode", None)
        # Parse the header and object
        text = content.decode("latin-1")
        # Verify content contains our object
        assert "/Example" in text
        assert "42" in text


def test_xref_stream_round_trip():
    """Test that XRef stream parsing produces correct trailer."""
    data = _build_xref_stream_pdf()
    parser = PdfCosParser(data)
    parser.parse()

    # Verify we can access objects
    catalog = parser.get_object(PdfIndirectReference(1, 0))
    assert catalog is not None

    pages = parser.get_object(PdfIndirectReference(2, 0))
    assert pages is not None


def test_traditional_xref_still_works():
    """Verify traditional xref tables still parse correctly."""
    # Build a minimal PDF with traditional xref
    header = b"%PDF-1.7\n"
    obj1 = b"1 0 obj\n<< /Type /Catalog >>\nendobj\n"
    offset1 = len(header)
    xref = b"xref\n0 2\n0000000000 65535 f \n%010d 00000 n \n" % offset1
    xref_pos = len(header) + len(obj1)
    trailer = b"trailer\n<< /Size 2 /Root 1 0 R >>\n"
    startxref = b"startxref\n%d\n%%EOF" % xref_pos
    pdf_bytes = header + obj1 + xref + trailer + startxref

    parser = PdfCosParser(pdf_bytes)
    doc = parser.parse()

    assert PdfName("Size") in doc.trailer.mapping
    assert doc.trailer.mapping[PdfName("Size")].value == 2


def test_traditional_xref_variable_spacing_between_fields():
    """XRef rows with extra spaces between fields (not fixed 20-byte lines)."""
    header = b"%PDF-1.7\n"
    obj1 = b"1 0 obj\n<< /Type /Catalog >>\nendobj\n"
    offset1 = len(header)
    xref = (
        b"xref\n0 2\n"
        b"0000000000   65535   f\n"
        b"%010d  00000  n\n" % offset1
    )
    xref_pos = len(header) + len(obj1)
    trailer = b"trailer\n<< /Size 2 /Root 1 0 R >>\n"
    startxref = b"startxref\n%d\n%%EOF" % xref_pos
    pdf_bytes = header + obj1 + xref + trailer + startxref

    doc = PdfCosParser(pdf_bytes).parse()
    assert doc.trailer.mapping[PdfName("Size")].value == 2


def test_traditional_xref_unpadded_numeric_fields():
    """XRef rows with minimal numeric fields (no leading-zero padding)."""
    header = b"%PDF-1.7\n"
    obj1 = b"1 0 obj\n<< /Type /Catalog >>\nendobj\n"
    offset1 = len(header)
    xref = b"xref\n0 2\n0 65535 f\n%d 0 n\n" % offset1
    xref_pos = len(header) + len(obj1)
    trailer = b"trailer\n<< /Size 2 /Root 1 0 R >>\n"
    startxref = b"startxref\n%d\n%%EOF" % xref_pos
    pdf_bytes = header + obj1 + xref + trailer + startxref

    doc = PdfCosParser(pdf_bytes).parse()
    assert doc.trailer.mapping[PdfName("Size")].value == 2


def test_traditional_xref_blank_lines_comments_and_dense_trailer():
    """Blank lines, %% comments between rows, and ``trailer<<`` without space."""
    header = b"%PDF-1.7\n"
    obj1 = b"1 0 obj\n<< /Type /Catalog >>\nendobj\n"
    offset1 = len(header)
    xref = (
        b"xref\n0 2\n"
        b"0000000000 65535 f\n"
        b"%% generated xref\n"
        b"\n"
        b"%010d 00000 n\n\n" % offset1
    )
    xref_pos = len(header) + len(obj1)
    trailer = b"trailer<< /Size 2 /Root 1 0 R >>\n"
    startxref = b"startxref\n%d\n%%EOF" % xref_pos
    pdf_bytes = header + obj1 + xref + trailer + startxref

    doc = PdfCosParser(pdf_bytes).parse()
    assert doc.trailer.mapping[PdfName("Size")].value == 2


# End of tests

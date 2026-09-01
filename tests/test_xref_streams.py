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

from aspose_pdf.engine.cos import (
    PdfDictionary,
    PdfIndirectReference,
    PdfName,
    PdfNumber,
)
from aspose_pdf.engine.pdf_parser_cos import PdfCosParser


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


def _build_predicted_xref_stream_pdf() -> bytes:
    """An XRef stream filtered through the PNG Up predictor, as producers write.

    ``/Predictor 12`` is what qpdf, Ghostscript and Acrobat all emit, so this
    is the shape most real cross-reference streams have. Every row carries a
    leading filter-type byte, which makes the inflated data one byte per entry
    longer than the entries themselves and has to be undone before the entries
    can be read.
    """
    header = b"%PDF-1.7\n"
    obj1 = b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    obj2 = b"2 0 obj\n<< /Type /Pages /Count 0 >>\nendobj\n"

    offset1 = len(header)
    offset2 = offset1 + len(obj1)
    xref_offset = offset2 + len(obj2)

    rows = [
        b"\x00" + (0).to_bytes(4, "big"),
        b"\x01" + offset1.to_bytes(4, "big"),
        b"\x01" + offset2.to_bytes(4, "big"),
        b"\x01" + xref_offset.to_bytes(4, "big"),
    ]
    # PNG Up: each row is stored as its difference from the row above, behind a
    # filter-type byte of 2.
    previous = b"\x00" * 5
    predicted = bytearray()
    for row in rows:
        predicted.append(2)
        predicted.extend(
            bytes((row[i] - previous[i]) & 0xFF for i in range(len(row)))
        )
        previous = row
    compressed = zlib.compress(bytes(predicted))

    xref_dict = (
        b"<< /Type /XRef /Filter /FlateDecode"
        b" /DecodeParms << /Predictor 12 /Columns 5 >>"
        b" /W [1 4 0] /Size 4 /Root 1 0 R /Length "
        + str(len(compressed)).encode()
        + b" >>\n"
    )
    xref_obj = (
        b"3 0 obj\n" + xref_dict + b"stream\n" + compressed + b"\nendstream\n"
        b"endobj\n"
    )
    startxref = b"startxref\n" + str(xref_offset).encode() + b"\n%%EOF\n"
    return header + obj1 + obj2 + xref_obj + startxref


def test_parse_xref_stream_with_a_png_predictor():
    """A predictor'd cross-reference stream has to parse like any other.

    Two things used to stop it. The decode limit was sized to the entries
    rather than to the predictor'd rows inflate actually produces, so the
    stream was rejected as oversized; and ``/DecodeParms`` reached the filter
    layer as a COS dictionary, whose ``get("Predictor")`` misses, so the
    predictor was silently skipped and the entries read one byte out of step.
    """
    doc = PdfCosParser(_build_predicted_xref_stream_pdf()).parse()

    root = doc.trailer.mapping.get(PdfName("Root"))
    assert isinstance(root, PdfIndirectReference)
    catalog = doc.objects[root.object_number]
    assert isinstance(catalog, PdfDictionary)
    assert catalog.mapping.get(PdfName("Type")).name == "/Catalog"
    pages = doc.objects[2]
    assert pages.mapping.get(PdfName("Type")).name == "/Pages"


def _build_predicted_object_stream_pdf() -> bytes:
    """An object stream whose ``/DecodeParms`` declares a PNG predictor.

    Rare but legal, and the only way to tell whether the parser reads
    ``/DecodeParms`` on an object stream at all: a COS dictionary handed to the
    filter layer looks exactly like no parameters, so the predictor would be
    skipped and the objects parsed out of predictor rows.
    """
    header = b"%PDF-1.7\n"
    obj4 = b"<< /Type /Example /Value 42 >>"
    pair = b"4 0 "
    body = pair + obj4

    columns = 8
    rows = [body[i : i + columns] for i in range(0, len(body), columns)]
    rows[-1] = rows[-1].ljust(columns, b" ")
    previous = b"\x00" * columns
    predicted = bytearray()
    for row in rows:
        predicted.append(2)  # PNG Up
        predicted.extend(bytes((row[i] - previous[i]) & 0xFF for i in range(columns)))
        previous = row
    compressed = zlib.compress(bytes(predicted))

    obj1 = b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    obj2 = b"2 0 obj\n<< /Type /Pages /Count 0 >>\nendobj\n"
    offset1 = len(header)
    offset2 = offset1 + len(obj1)
    objstm_offset = offset2 + len(obj2)

    objstm = (
        b"3 0 obj\n"
        b"<< /Type /ObjStm /Filter /FlateDecode"
        b" /DecodeParms << /Predictor 12 /Columns " + str(columns).encode() + b" >>"
        b" /N 1 /First " + str(len(pair)).encode() +
        b" /Length " + str(len(compressed)).encode() + b" >>\n"
        b"stream\n" + compressed + b"\nendstream\nendobj\n"
    )

    xref_offset = objstm_offset + len(objstm)
    xref = (
        b"xref\n0 4\n0000000000 65535 f \n"
        b"%010d 00000 n \n%010d 00000 n \n%010d 00000 n \n"
        % (offset1, offset2, objstm_offset)
    )
    trailer = b"trailer\n<< /Size 5 /Root 1 0 R >>\n"
    startxref = b"startxref\n" + str(xref_offset).encode() + b"\n%%EOF\n"
    return header + obj1 + obj2 + objstm + xref + trailer + startxref


def test_parse_object_stream_with_a_png_predictor():
    """``/DecodeParms`` has to reach the filter layer as a plain mapping."""
    from aspose_pdf.engine.cos import PdfStream
    from aspose_pdf.engine.filters import StreamDecoder

    parser = PdfCosParser(_build_predicted_object_stream_pdf())
    parser.parse()
    objstm = parser.get_object(PdfIndirectReference(3, 0))
    assert isinstance(objstm, PdfStream)

    extracted = parser._parse_object_stream(objstm)
    assert set(extracted) == {4}
    assert extracted[4].mapping[PdfName("Value")].value == 42

    # Without the predictor undone, the row bytes would still be in the way.
    plain = StreamDecoder.decode(
        objstm.content,
        "FlateDecode",
        {"Predictor": 12, "Columns": 8},
    )
    assert plain.startswith(b"4 0 << /Type /Example")


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
    from aspose_pdf.engine.cos import PdfStream
    from aspose_pdf.engine.filters import StreamDecoder

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

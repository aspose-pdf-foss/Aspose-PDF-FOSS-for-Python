from aspose_pdf.engine.cos import (
    PdfArray,
    PdfDictionary,
    PdfName,
    PdfNumber,
    PdfString,
    PdfNull,
    PdfBoolean,
    PdfIndirectReference,
)
from aspose_pdf.engine.pdf_parser_cos import PdfCosParser
from aspose_pdf.engine.pdf_writer_cos import PdfCosWriter
from aspose_pdf.engine.cos import PdfDocument


def test_pdf_object_nesting():
    """Create nested COS objects and verify their structure."""
    inner_dict = PdfDictionary({PdfName("Key"): PdfNumber(42)})
    array = PdfArray([PdfString("hello"), inner_dict, PdfNull()])
    outer_dict = PdfDictionary(
        {PdfName("Array"): array, PdfName("Flag"): PdfBoolean(True)}
    )
    # Simple assertions
    assert isinstance(outer_dict[PdfName("Array")], PdfArray)
    assert outer_dict[PdfName("Flag")].value is True
    assert outer_dict[PdfName("Array")].items[0].value == b"hello"
    assert isinstance(outer_dict[PdfName("Array")].items[1], PdfDictionary)
    assert outer_dict[PdfName("Array")].items[1][PdfName("Key")].value == 42


def _build_minimal_pdf_bytes():
    header = b"%PDF-1.7\n"
    obj1 = b"1 0 obj\n<< /Type /Catalog >>\nendobj\n"
    offset1 = len(header)
    xref = b"xref\n0 2\n0000000000 65535 f \n%010d 00000 n \n" % offset1
    trailer = b"trailer\n<< /Size 2 /Root 1 0 R >>\n"
    xref_pos = len(header) + len(obj1)
    xref = b"xref\n0 2\n0000000000 65535 f \n%010d 00000 n \n" % offset1
    trailer = b"trailer\n<< /Size 2 /Root 1 0 R >>\n"
    startxref = b"startxref\n%d\n%%EOF" % xref_pos
    return header + obj1 + xref + trailer + startxref


def test_pdf_cos_parser_minimal():
    """Parse a hand‑crafted minimal PDF and verify trailer entries."""
    pdf_bytes = _build_minimal_pdf_bytes()
    doc = PdfCosParser(pdf_bytes).parse()
    # Trailer should contain Size and Root entries
    assert PdfName("Size") in doc.trailer.mapping
    assert doc.trailer.mapping[PdfName("Size")].value == 2
    root_ref = doc.trailer.mapping[PdfName("Root")]
    assert isinstance(root_ref, PdfIndirectReference)
    # The catalog object should be parsed as a dictionary with /Type /Catalog
    catalog = doc.objects.get(root_ref.object_number)
    assert isinstance(catalog, PdfDictionary)
    assert catalog[PdfName("Type")].name == "/Catalog"


def test_pdf_cos_writer_round_trip():
    """Write a document to bytes and parse it back, ensuring equality."""
    doc = PdfDocument()
    catalog = PdfDictionary({PdfName("Type"): PdfName("Catalog")})
    catalog_ref = doc.register_object(catalog)
    doc.trailer[PdfName("Root")] = catalog_ref
    writer = PdfCosWriter(doc)
    pdf_bytes = writer.write()
    parsed_doc = PdfCosParser(pdf_bytes).parse()
    assert len(parsed_doc.objects) == len(doc.objects) == 1
    parsed_root = parsed_doc.trailer.mapping[PdfName("Root")]
    assert isinstance(parsed_root, PdfIndirectReference)
    assert parsed_root.object_number == catalog_ref.object_number
    parsed_catalog = parsed_doc.objects[catalog_ref.object_number]
    assert isinstance(parsed_catalog, PdfDictionary)
    assert parsed_catalog[PdfName("Type")].name == "/Catalog"


# End of tests

"""AUDIT #7: PdfCosParser.parse() must not swallow arbitrary Exception during xref parse."""

from __future__ import annotations

import pytest

from aspose_pdf.engine.cos import PdfDictionary, PdfIndirectReference, PdfName
from aspose_pdf.engine.pdf_parser_cos import PdfCosParser


def _build_minimal_pdf_bytes():
    """Valid traditional xref (same layout as tests/test_cos.py)."""
    header = b"%PDF-1.7\n"
    obj1 = b"1 0 obj\n<< /Type /Catalog >>\nendobj\n"
    offset1 = len(header)
    xref_pos = len(header) + len(obj1)
    xref = b"xref\n0 2\n0000000000 65535 f \n%010d 00000 n \n" % offset1
    trailer = b"trailer\n<< /Size 2 /Root 1 0 R >>\n"
    startxref = b"startxref\n%d\n%%EOF" % xref_pos
    return header + obj1 + xref + trailer + startxref


def _build_pdf_with_broken_xref_needing_scan():
    """Objects are valid; xref subsection is invalid so primary parse raises PdfParseException."""
    header = b"%PDF-1.7\n"
    obj1 = b"1 0 obj\n<< /Type /Catalog >>\nendobj\n"
    xref_pos = len(header) + len(obj1)
    bad_xref = b"xref\nnot_a_number\n"
    trailer = b"trailer\n<< /Size 2 /Root 1 0 R >>\n"
    startxref = b"startxref\n%d\n%%EOF" % xref_pos
    return header + obj1 + bad_xref + trailer + startxref


def test_parse_propagates_non_xref_exceptions():
    """Internal errors during xref parsing must not fall through to xref reconstruction."""
    pdf_bytes = _build_minimal_pdf_bytes()
    parser = PdfCosParser(pdf_bytes)

    def _broken_section(offset):  # noqa: ARG001
        raise RuntimeError("simulated non-xref failure")

    parser._parse_xref_section = _broken_section  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="simulated non-xref failure"):
        parser.parse()


def test_parse_reconstructs_after_primary_xref_parse_failure():
    """PdfParseException on traditional xref still triggers full-file xref scan."""
    data = _build_pdf_with_broken_xref_needing_scan()
    doc = PdfCosParser(data).parse()
    root_ref = doc.trailer.mapping.get(PdfName("Root"))
    assert isinstance(root_ref, PdfIndirectReference)
    catalog = doc.objects[root_ref.object_number]
    assert isinstance(catalog, PdfDictionary)
    assert catalog[PdfName("Type")].name == "/Catalog"


def test_parse_reconstructs_after_xref_stream_zlib_failure():
    """Corrupt Flate in xref stream yields zlib.error; recovery scan should still run."""
    header = b"%PDF-1.7\n"
    obj1 = b"1 0 obj\n<< /Type /Catalog >>\nendobj\n"
    offset1 = len(header)
    xref_offset = offset1 + len(obj1)
    bad_compressed = b"\x00\x01not_valid_zlib"
    xref_stream_dict = (
        b"<< /Type /XRef /Filter /FlateDecode /W [1 4 0] /Size 2 /Root 1 0 R /Length "
        + str(len(bad_compressed)).encode()
        + b" >>\n"
    )
    xref_obj = (
        b"2 0 obj\n"
        + xref_stream_dict
        + b"stream\n"
        + bad_compressed
        + b"\nendstream\nendobj\n"
    )
    startxref_section = b"startxref\n" + str(xref_offset).encode() + b"\n%%EOF\n"
    data = header + obj1 + xref_obj + startxref_section

    doc = PdfCosParser(data).parse()
    root_ref = doc.trailer.mapping.get(PdfName("Root"))
    assert isinstance(root_ref, PdfIndirectReference)
    catalog = doc.objects[root_ref.object_number]
    assert isinstance(catalog, PdfDictionary)
    assert catalog[PdfName("Type")].name == "/Catalog"

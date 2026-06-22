"""AUDIT #9: XRef stream /W and /Size validation; ObjStm map after xref reconstruction."""

from __future__ import annotations

import zlib

import pytest

from aspose_pdf.engine.cos import PdfDictionary, PdfIndirectReference, PdfName
from aspose_pdf.engine.pdf_parser_cos import PdfCosParser
from aspose_pdf.exceptions import PdfParseException


def test_xref_stream_missing_w_raises():
    header = b"%PDF-1.7\n"
    obj1 = b"1 0 obj\n<< /Type /Catalog >>\nendobj\n"
    xref_off = len(header) + len(obj1)
    xref_obj = (
        b"2 0 obj\n<< /Type /XRef /Size 2 /Length 0 >>\nstream\n\nendstream\nendobj\n"
    )
    data = (
        header
        + obj1
        + xref_obj
        + b"startxref\n"
        + str(xref_off).encode()
        + b"\n%%EOF\n"
    )
    parser = PdfCosParser(data)
    with pytest.raises(PdfParseException, match="missing or invalid /W"):
        parser._parse_xref_stream(xref_off)


def test_xref_stream_missing_size_raises():
    header = b"%PDF-1.7\n"
    obj1 = b"1 0 obj\n<< /Type /Catalog >>\nendobj\n"
    xref_off = len(header) + len(obj1)
    xref_obj = (
        b"2 0 obj\n<< /Type /XRef /W [1 4 1] /Length 6 >>\nstream\n"
        + (b"\x00" + (0).to_bytes(4, "big") + b"\x00")
        + b"\nendstream\nendobj\n"
    )
    data = (
        header
        + obj1
        + xref_obj
        + b"startxref\n"
        + str(xref_off).encode()
        + b"\n%%EOF\n"
    )
    parser = PdfCosParser(data)
    with pytest.raises(PdfParseException, match="missing or invalid /Size"):
        parser._parse_xref_stream(xref_off)


def test_xref_stream_w_sum_zero_raises():
    header = b"%PDF-1.7\n"
    obj1 = b"1 0 obj\n<< /Type /Catalog >>\nendobj\n"
    xref_off = len(header) + len(obj1)
    xref_obj = b"2 0 obj\n<< /Type /XRef /W [0 0 0] /Size 2 /Length 0 >>\nstream\n\nendstream\nendobj\n"
    data = (
        header
        + obj1
        + xref_obj
        + b"startxref\n"
        + str(xref_off).encode()
        + b"\n%%EOF\n"
    )
    parser = PdfCosParser(data)
    with pytest.raises(
        PdfParseException,
        match="positive entry size",
    ):
        parser._parse_xref_stream(xref_off)


def _build_xref_stream_with_objstm_corrupt_xref_flate() -> bytes:
    """XRef stream has compressed obj 4 in ObjStm 3; xref Flate is broken → scan + ObjStm map."""
    header = b"%PDF-1.7\n"
    obj4_body = b"<< /Type /Example /Marker /Issue9 >>"
    pair = b"4 0 "
    stream_body = pair + obj4_body
    compressed_objstm = zlib.compress(stream_body)

    obj1 = b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    obj2 = b"2 0 obj\n<< /Type /Pages /Count 0 >>\nendobj\n"
    o1 = len(header)
    o2 = o1 + len(obj1)
    o3 = o2 + len(obj2)
    obj3 = (
        b"3 0 obj\n<< /Type /ObjStm /Filter /FlateDecode /N 1 /First "
        + str(len(pair)).encode()
        + b" /Length "
        + str(len(compressed_objstm)).encode()
        + b" >>\nstream\n"
        + compressed_objstm
        + b"\nendstream\nendobj\n"
    )
    o5 = o3 + len(obj3)

    bad_xref_zlib = b"\x00not_valid_zlib_data"

    xref_obj = (
        b"5 0 obj\n<< /Type /XRef /Filter /FlateDecode /W [1 4 1] /Size 6 "
        b"/Root 1 0 R /Index [0 6] /Length "
        + str(len(bad_xref_zlib)).encode()
        + b" >>\nstream\n"
        + bad_xref_zlib
        + b"\nendstream\nendobj\n"
    )
    tail = b"startxref\n" + str(o5).encode() + b"\n%%EOF\n"
    return header + obj1 + obj2 + obj3 + xref_obj + tail


def test_reconstruct_recovers_object_stream_entries_after_bad_xref_stream():
    data = _build_xref_stream_with_objstm_corrupt_xref_flate()
    doc = PdfCosParser(data).parse()
    ex = doc.objects[4]
    assert isinstance(ex, PdfDictionary)
    assert ex.mapping.get(PdfName("Type")).name == "/Example"
    assert ex.mapping.get(PdfName("Marker")).name == "/Issue9"
    root_ref = doc.trailer.mapping.get(PdfName("Root"))
    assert isinstance(root_ref, PdfIndirectReference)
    cat = doc.objects[root_ref.object_number]
    assert isinstance(cat, PdfDictionary)
    assert cat.mapping.get(PdfName("Type")).name == "/Catalog"

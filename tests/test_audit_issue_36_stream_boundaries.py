"""AUDIT #36: Stream /Length and endstream detection must stay within the object (COS)."""

from __future__ import annotations

import pytest

from aspose_pdf.exceptions import PdfParseException
from aspose_pdf.engine.pdf_parser_cos import PdfCosParser


def _pdf_with_stream(*, inner_dict: bytes, payload: bytes) -> bytes:
    """Single object ``1 0`` stream + traditional xref; Root points to unused 1 0 R."""
    header = b"%PDF-1.7\n"
    obj = b"1 0 obj\n" + inner_dict + b"stream\n" + payload + b"\nendstream\nendobj\n"
    offset1 = len(header)
    xref_pos = len(header) + len(obj)
    xref = b"xref\n0 2\n0000000000 65535 f \n%010d 00000 n \n" % offset1
    trailer = b"trailer\n<< /Size 2 /Root 1 0 R >>\n"
    startxref = b"startxref\n%d\n%%EOF" % xref_pos
    return header + obj + xref + trailer + startxref


def test_stream_correct_length_loads():
    payload = b"log"
    inner = b"<< /Length 3 >>\n"
    doc = PdfCosParser(_pdf_with_stream(inner_dict=inner, payload=payload)).parse()
    stm = doc.objects[1]
    assert stm.content == payload


def test_stream_length_misaligned_raises():
    """Declared length must end immediately before ``endstream`` (whitespace only)."""
    payload = b"ABCDEF"
    inner = b"<< /Length 2 >>\n"  # too short: leaves ``CDEF`` before keyword
    doc = PdfCosParser(_pdf_with_stream(inner_dict=inner, payload=payload)).parse()
    with pytest.raises(PdfParseException, match="align"):
        _ = doc.objects[1]


def test_stream_length_past_object_raises():
    """/Length must not extend beyond the bytes reserved for this object body."""
    payload = b"X"
    inner = b"<< /Length 99999 >>\n"
    doc = PdfCosParser(_pdf_with_stream(inner_dict=inner, payload=payload)).parse()
    with pytest.raises(PdfParseException, match="extends past object"):
        _ = doc.objects[1]


def test_stream_negative_length_raises():
    inner = b"<< /Length -1 >>\n"
    doc = PdfCosParser(_pdf_with_stream(inner_dict=inner, payload=b"")).parse()
    with pytest.raises(PdfParseException, match="non-negative"):
        _ = doc.objects[1]


def test_stream_without_length_uses_rightmost_endstream_token():
    """Embedded ``endstream``-shaped bytes must not truncate when /Length is absent."""
    # Two token-shaped occurrences; closing one is right before endobj span ends.
    payload = b"pref endstream moredata\n"
    inner = b"<< >>\n"
    doc = PdfCosParser(_pdf_with_stream(inner_dict=inner, payload=payload)).parse()
    stm = doc.objects[1]
    assert stm.content == payload + b"\n"


def test_stream_without_length_bounded_to_object_not_later_file():
    """No /Length: ``endstream`` search must not match a later object in the file."""
    header = b"%PDF-1.7\n"
    obj1 = b"1 0 obj\n<< >>\nstream\nONLY_A\nendstream\nendobj\n"
    obj2 = b"2 0 obj\n<< /Length 3 >>\nstream\nBBB\nendstream\nendobj\n"
    body = header + obj1 + obj2
    xref_pos = len(body)
    xref = (
        b"xref\n0 3\n"
        b"0000000000 65535 f \n"
        + b"%010d 00000 n \n" % len(header)
        + b"%010d 00000 n \n" % (len(header) + len(obj1))
    )
    trailer = b"trailer\n<< /Size 3 /Root 1 0 R >>\n"
    startxref = b"startxref\n%d\n%%EOF" % xref_pos
    pdf = body + xref + trailer + startxref
    doc = PdfCosParser(pdf).parse()
    stm1 = doc.objects[1]
    assert stm1.content == b"ONLY_A\n"

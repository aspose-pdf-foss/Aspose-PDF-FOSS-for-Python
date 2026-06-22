"""AUDIT #12: Lazy object load must not turn parse failures into misleading KeyError."""

from __future__ import annotations

import pytest

from aspose_pdf.exceptions import PdfParseException
from aspose_pdf.engine.pdf_parser_cos import PdfCosParser


def _pdf_with_obj1_tokenizer_valueerror() -> bytes:
    """Valid xref; object 1 body is ``+`` so :meth:`_Tokenizer._read_number` raises ValueError."""
    header = b"%PDF-1.7\n"
    obj1 = b"1 0 obj\n+\nendobj\n"
    offset1 = len(header)
    xref_pos = len(header) + len(obj1)
    xref = b"xref\n0 2\n0000000000 65535 f \n%010d 00000 n \n" % offset1
    trailer = b"trailer\n<< /Size 2 /Root 1 0 R >>\n"
    startxref = b"startxref\n%d\n%%EOF" % xref_pos
    return header + obj1 + xref + trailer + startxref


def test_lazy_load_malformed_object_raises_pdf_parse_exception_with_chain():
    doc = PdfCosParser(_pdf_with_obj1_tokenizer_valueerror()).parse()
    with pytest.raises(PdfParseException) as ei:
        _ = doc.objects[1]
    msg = str(ei.value)
    assert "object 1" in msg
    assert "byte offset" in msg
    assert isinstance(ei.value.__cause__, ValueError)


def test_lazy_load_malformed_object_is_not_keyerror():
    doc = PdfCosParser(_pdf_with_obj1_tokenizer_valueerror()).parse()
    with pytest.raises(PdfParseException):
        _ = doc.objects[1]

"""AUDIT #13: object-stream members must not be dropped on tokenizer errors silently."""

import zlib

import pytest

from aspose_pdf.exceptions import PdfParseException
from aspose_pdf.engine.cos import PdfIndirectReference
from aspose_pdf.engine.pdf_parser_cos import PdfCosParser

_W124 = [1, 4, 2]
_ENTRY_LEN = sum(_W124)


def _entry_free() -> bytes:
    return b"\x00" + (0).to_bytes(4, "big") + (0).to_bytes(2, "big")


def _entry_unc(off: int) -> bytes:
    return b"\x01" + off.to_bytes(4, "big") + (0).to_bytes(2, "big")


def _entry_comp(stm: int, idx: int) -> bytes:
    return b"\x02" + stm.to_bytes(4, "big") + idx.to_bytes(2, "big")


def _build_pdf_objstm_plus_xref_stream(*, pair_and_payload: bytes, first: int) -> bytes:
    """PDF 1.7 with XRef stream; object 5 is compressed in ObjStm 3."""
    header = b"%PDF-1.7\n"
    obj1 = b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    obj2 = b"2 0 obj\n<< /Type /Pages /Count 0 >>\nendobj\n"

    compressed_objstm = zlib.compress(pair_and_payload)

    obj3 = (
        b"3 0 obj\n"
        b"<< /Type /ObjStm /Filter /FlateDecode /N 1 /First "
        + str(first).encode()
        + b" /Length "
        + str(len(compressed_objstm)).encode()
        + b" >>\n"
        b"stream\n" + compressed_objstm + b"\nendstream\n"
        b"endobj\n"
    )

    offset1 = len(header)
    offset2 = offset1 + len(obj1)
    offset3 = offset2 + len(obj2)
    offset4 = offset3 + len(obj3)

    xref_stream_data = (
        _entry_free()
        + _entry_unc(offset1)
        + _entry_unc(offset2)
        + _entry_unc(offset3)
        + _entry_unc(offset4)
        + _entry_comp(3, 0)
    )
    assert len(xref_stream_data) == 6 * _ENTRY_LEN

    compressed_xref = zlib.compress(xref_stream_data)
    xref_dict = (
        b"<< /Type /XRef /Filter /FlateDecode /W [1 4 2] /Size 6 /Root 1 0 R /Length "
        + str(len(compressed_xref)).encode()
        + b" >>\n"
    )
    obj4 = (
        b"4 0 obj\n"
        + xref_dict
        + b"stream\n"
        + compressed_xref
        + b"\nendstream\n"
        b"endobj\n"
    )

    tail = b"startxref\n" + str(offset4).encode() + b"\n%%EOF\n"
    return header + obj1 + obj2 + obj3 + obj4 + tail


def test_object_stream_invalid_hex_raises_pdf_parse_exception():
    """Invalid hex in a member body used to raise ValueError and was swallowed."""
    member = 5
    pair = f"{member} 0 ".encode()
    first = len(pair)
    data = _build_pdf_objstm_plus_xref_stream(
        pair_and_payload=pair + b"<GG>",
        first=first,
    )
    parser = PdfCosParser(data)
    doc = parser.parse()

    _ = doc.objects[3]
    with pytest.raises(PdfParseException, match=f"object {member}"):
        _ = doc.objects[member]


def test_object_stream_member_offset_out_of_range_raises():
    member = 5
    pair = f"{member} 500 ".encode()
    first = len(pair)
    data = _build_pdf_objstm_plus_xref_stream(
        pair_and_payload=pair,
        first=first,
    )
    parser = PdfCosParser(data)
    doc = parser.parse()

    with pytest.raises(PdfParseException, match="out of range"):
        _ = doc.objects[member]


def test_object_stream_via_indirect_reference_raises():
    member = 5
    pair = f"{member} 0 ".encode()
    first = len(pair)
    data = _build_pdf_objstm_plus_xref_stream(
        pair_and_payload=pair + b"<GG>",
        first=first,
    )
    parser = PdfCosParser(data)
    parser.parse()

    with pytest.raises(PdfParseException):
        _ = parser.get_object(PdfIndirectReference(member, 0))

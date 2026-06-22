"""AUDIT issue #22: JBIG2 decode semantics.

``StreamDecoder`` must not silently return undecodable JBIG2 bytes as if they were
a bitmap. Failures surface as :class:`~aspose_pdf.exceptions.PdfValidationException`
for direct decode; ``CosExtractor._decode_stream`` still returns raw stream bytes
after catching decode errors (existing contract).
"""

import struct

import pytest

from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.engine.cos import PdfDictionary, PdfDocument, PdfName, PdfStream
from aspose_pdf.engine.filters import StreamDecoder
from aspose_pdf.engine.simple_pdf import CosExtractor


def _make_jbig2_stream(seg_type: int, seg_data: bytes, header: bool = True) -> bytes:
    stream = b""
    if header:
        stream += b"\x97JBIG2\x00\r"
    stream += b"\x00\x00\x00\x00"
    stream += bytes([0, seg_type])
    stream += struct.pack(">I", len(seg_data))
    stream += seg_data
    return stream


def test_jbig2_header_only_raises_on_direct_decode():
    stream = b"\x97JBIG2\x00\r"
    with pytest.raises(PdfValidationException, match="could not produce bitmap"):
        StreamDecoder.decode(stream, "JBIG2Decode", None)


def test_jbig2_decode_success_returns_bitmap():
    payload = b"\xaa\xbb\xcc"
    region_info = bytearray(13)
    region_info[0] = 0x00
    seg_data = bytes(region_info) + payload
    data = _make_jbig2_stream(seg_type=0x08, seg_data=seg_data)
    out = StreamDecoder.decode(data, "JBIG2Decode", None)
    assert out == payload


def test_cos_extractor_jbig2_undecodable_returns_raw_stream():
    """Engine image path: avoid empty/silent corruption — fall back to encoded bytes."""
    doc = PdfDocument()
    doc.trailer = PdfDictionary({PdfName("Root"): PdfDictionary({})})
    ext = CosExtractor(doc, b"")
    raw = b"\x97JBIG2\x00\r"
    stream = PdfStream(
        content=raw,
        mapping={PdfName("Filter"): PdfName("JBIG2Decode")},
    )
    assert ext._decode_stream(stream) == raw

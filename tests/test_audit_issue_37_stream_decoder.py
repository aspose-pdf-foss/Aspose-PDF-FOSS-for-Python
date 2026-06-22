"""AUDIT issue #37: StreamDecoder must not swallow errors in fallback paths.

Unsupported filters, Crypt, corrupt LZW, and truncated RunLengthDecode must not
return misleading decoded bytes. Direct :meth:`StreamDecoder.decode` raises
:class:`~aspose_pdf.exceptions.PdfValidationException`. :meth:`CosExtractor._decode_stream`
still returns raw stream bytes when decode fails (same pattern as AUDIT #22–#23).
"""

import pytest

from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.engine.cos import (
    PdfDictionary,
    PdfDocument,
    PdfName,
    PdfStream,
)
from aspose_pdf.engine.filters import StreamDecoder
from aspose_pdf.engine.simple_pdf import CosExtractor


def test_unknown_filter_direct_decode_raises():
    with pytest.raises(PdfValidationException, match="UnknownFilterXYZ"):
        StreamDecoder.decode(b"abc", "UnknownFilterXYZ", None)


def test_crypt_filter_direct_decode_raises():
    with pytest.raises(PdfValidationException, match="Crypt filter cannot be decoded"):
        StreamDecoder.decode(b"cipher", "Crypt", None)


def test_unknown_filter_bytes_name_raises():
    with pytest.raises(PdfValidationException, match="Unsupported"):
        StreamDecoder.decode(b"x", b"/NoSuchFilter", None)


def test_lzw_invalid_code_raises():
    # First 9-bit code is 258 while ``prev_entry`` is still empty — illegal in LZW.
    lzw_corrupt = bytes.fromhex("8100")
    with pytest.raises(PdfValidationException, match="LZWDecode failed"):
        StreamDecoder.decode(lzw_corrupt, "LZWDecode", None)


def test_run_length_truncated_literal_raises():
    # Length 2 => copy 3 literal bytes but only 1 byte follows (no EOD).
    truncated = bytes([2, ord("A")])
    with pytest.raises(PdfValidationException, match="RunLengthDecode failed"):
        StreamDecoder.decode(truncated, "RunLengthDecode", None)


def test_run_length_missing_repeat_byte_raises():
    # Repeat control without the byte to repeat.
    truncated = bytes([253])
    with pytest.raises(PdfValidationException, match="RunLengthDecode failed"):
        StreamDecoder.decode(truncated, "RunLengthDecode", None)


def test_cos_extractor_unknown_filter_returns_raw_stream():
    doc = PdfDocument()
    doc.trailer = PdfDictionary({PdfName("Root"): PdfDictionary({})})
    ext = CosExtractor(doc, b"")
    raw = b"payload"
    stream = PdfStream(
        content=raw,
        mapping={PdfName("Filter"): PdfName("TotallyUnsupportedFilter")},
    )
    assert ext._decode_stream(stream) == raw

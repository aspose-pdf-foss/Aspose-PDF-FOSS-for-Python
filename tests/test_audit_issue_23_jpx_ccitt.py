"""AUDIT issue #23: JPX and CCITT stream decode semantics.

Direct :meth:`StreamDecoder.decode` must not swallow failures with broad handlers
or silent pass-through of undecodable image bytes. Failures surface as
:class:`~aspose_pdf.exceptions.PdfValidationException`. :meth:`CosExtractor._decode_stream`
still returns raw stream bytes after catching decode errors (same contract as JBIG2).
"""

import pytest

from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.engine.cos import (
    PdfDictionary,
    PdfDocument,
    PdfName,
    PdfNumber,
    PdfStream,
)
from aspose_pdf.engine.filters import StreamDecoder
from aspose_pdf.engine.jpx import HAS_PILLOW
from aspose_pdf.engine.simple_pdf import CosExtractor


def test_ccitt_malformed_group4_raises_on_direct_decode():
    # Bitstream that reaches an unsupported G4 mode in the reference decoder.
    raw = b"\x01"
    parms = {"K": -1, "Columns": 8, "Rows": 1, "BlackIs1": True}
    with pytest.raises(PdfValidationException, match="CCITTFaxDecode"):
        StreamDecoder.decode(raw, "CCITTFaxDecode", parms)


def test_cos_extractor_ccitt_undecodable_returns_raw_stream():
    doc = PdfDocument()
    doc.trailer = PdfDictionary({PdfName("Root"): PdfDictionary({})})
    ext = CosExtractor(doc, b"")
    raw = b"\x01"
    stream = PdfStream(
        content=raw,
        mapping={
            PdfName("Filter"): PdfName("CCITTFaxDecode"),
            PdfName("DecodeParms"): PdfDictionary(
                {
                    PdfName("K"): PdfNumber(-1),
                    PdfName("Columns"): PdfNumber(8),
                    PdfName("Rows"): PdfNumber(1),
                }
            ),
        },
    )
    assert ext._decode_stream(stream) == raw


def test_jpx_invalid_raises_when_pillow_present():
    if not HAS_PILLOW:
        pytest.skip("Pillow not installed")
    with pytest.raises(PdfValidationException, match="JPXDecode failed"):
        StreamDecoder.decode(b"\x00not-jp2\x00", "JPXDecode", None)


def test_jpx_missing_pillow_raises_on_direct_decode():
    if HAS_PILLOW:
        pytest.skip("Pillow is available")
    with pytest.raises(PdfValidationException, match="JPXDecode requires Pillow"):
        StreamDecoder.decode(b"\x00", "JPXDecode", None)


def test_cos_extractor_jpx_undecodable_returns_raw_stream():
    doc = PdfDocument()
    doc.trailer = PdfDictionary({PdfName("Root"): PdfDictionary({})})
    ext = CosExtractor(doc, b"")
    raw = b"\x00not-jp2\x00"
    stream = PdfStream(
        content=raw,
        mapping={PdfName("Filter"): PdfName("JPXDecode")},
    )
    assert ext._decode_stream(stream) == raw

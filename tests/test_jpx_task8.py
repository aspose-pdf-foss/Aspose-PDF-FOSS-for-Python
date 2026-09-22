"""JPXDecode integration: invalid payloads surface as PdfValidationException (AUDIT #23)."""

import pytest

from aspose_pdf.engine import jpx
from aspose_pdf.engine.filters import StreamDecoder
from aspose_pdf.exceptions import PdfValidationException


@pytest.mark.parametrize("use_pillow", [False, True])
def test_jpx_decoder_invalid_payload_raises(monkeypatch, use_pillow):
    if use_pillow and not jpx.HAS_PILLOW:
        pytest.skip("Pillow not installed")
    monkeypatch.setattr(jpx, "HAS_PILLOW", use_pillow)
    message = (
        "JPXDecode failed while decoding"
        if use_pillow
        else "not a JPEG 2000 codestream or JP2 file"
    )
    with pytest.raises(PdfValidationException, match=message):
        StreamDecoder.decode(b"jpx data", "JPXDecode", None)

"""JPXDecode integration: invalid payloads surface as PdfValidationException (AUDIT #23)."""

import pytest

from aspose_pdf.engine.filters import StreamDecoder
from aspose_pdf.engine.jpx import HAS_PILLOW
from aspose_pdf.exceptions import PdfValidationException


def test_jpx_decoder_invalid_payload_raises():
    data = b"jpx data"
    if not HAS_PILLOW:
        with pytest.raises(PdfValidationException, match="JPXDecode requires Pillow"):
            StreamDecoder.decode(data, "JPXDecode", None)
    else:
        with pytest.raises(
            PdfValidationException, match="JPXDecode failed while decoding"
        ):
            StreamDecoder.decode(data, "JPXDecode", None)

# JPEG 2000 (JPX) Decoder using Pillow
from typing import Any, Dict

from aspose_pdf.exceptions import PDF_STREAM_DECODE_ERRORS, PdfValidationException

try:
    from PIL import Image
    import io

    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False


class Decoder:
    """JPEG 2000 (JPX) Stream Decoder."""

    @staticmethod
    def decode(data: bytes, parms: Dict[str, Any] = None) -> bytes:
        """Decode JPEG 2000 data using Pillow.

        Args:
            data: JPX encoded bytes
            parms: Optional DecodeParms (usually ignored for JPX in PDF)

        Returns:
            Decoded raw pixel data
        """
        if not HAS_PILLOW:
            raise PdfValidationException(
                "JPXDecode requires Pillow (JPEG 2000 decode is not available)"
            )

        try:
            with io.BytesIO(data) as bio:
                with Image.open(bio) as img:
                    # PDF usually expects raw pixel data (RGB, Gray, CMYK, etc.)
                    # Pillow can convert to these modes.
                    if img.mode not in ("RGB", "RGBA", "L", "CMYK"):
                        # Convert to something standard
                        if "A" in img.mode:
                            img = img.convert("RGBA")
                        else:
                            img = img.convert("RGB")

                    return img.tobytes()
        except PDF_STREAM_DECODE_ERRORS as exc:
            raise PdfValidationException(
                "JPXDecode failed while decoding the image stream"
            ) from exc

"""AUDIT issue #6: from_bytes must not mask COS parse failures as an empty stub PDF."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from aspose_pdf.engine.simple_pdf import SimplePdf


def _minimal_valid_pdf() -> bytes:
    return (
        b"%PDF-1.7\n"
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Count 1 /Kids [3 0 R] >>\nendobj\n"
        b"3 0 obj\n<< /Type /Page /MediaBox [0 0 612 792] >>\nendobj\n"
        b"xref\n0 4\n"
        b"0000000000 65535 f \n"
        b"0000000009 00000 n \n"
        b"0000000058 00000 n \n"
        b"0000000115 00000 n \n"
        b"trailer\n<< /Root 1 0 R /Size 4 >>\n"
        b"startxref\n170\n%%EOF"
    )


def test_from_bytes_propagates_valueerror_from_parser():
    """Parse errors must surface; do not return a stub document (AUDIT #6)."""
    mock_parser = MagicMock()
    mock_parser.parse.side_effect = ValueError("simulated COS parse failure")

    with patch("aspose_pdf.engine.simple_pdf.PdfCosParser", return_value=mock_parser):
        with pytest.raises(ValueError, match="simulated COS parse failure"):
            SimplePdf.from_bytes(b"%PDF-1.7\n%EOF%\n")

    mock_parser.parse.assert_called_once()


def test_from_bytes_valid_pdf_still_loads():
    """Regression: normal eager load still works after removing ValueError stub."""
    pdf = SimplePdf.from_bytes(_minimal_valid_pdf())
    assert pdf._cos_doc is not None
    assert len(pdf.pages) >= 1


def test_from_bytes_safe_still_tolerates_broken_bytes():
    """Callers that need a fallback continue to use from_bytes_safe."""
    broken = b"%PDF-1.7\nthis is not a valid pdf structure\n%%EOF"
    pdf = SimplePdf.from_bytes_safe(broken)
    assert pdf is not None
    assert len(pdf.pages) >= 1

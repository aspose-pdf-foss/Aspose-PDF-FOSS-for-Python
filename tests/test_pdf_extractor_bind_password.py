"""PdfExtractor.bind_pdf must forward password to the engine (AUDIT #14)."""

from unittest.mock import MagicMock, patch

import pytest

from aspose_pdf.exceptions import PdfSecurityException
from aspose_pdf.facades import PdfExtractor


@patch("aspose_pdf.engine.simple_pdf.SimplePdf")
def test_bind_pdf_passes_password_to_from_bytes(mock_sp):
    fake = MagicMock()
    mock_sp.from_bytes.return_value = fake
    ex = PdfExtractor()
    ex.bind_pdf(b"%PDF-1.4 stub", password="userpw")
    mock_sp.from_bytes.assert_called_once_with(b"%PDF-1.4 stub", "userpw")


@patch("aspose_pdf.engine.simple_pdf.SimplePdf")
def test_bind_pdf_passes_password_to_from_file(mock_sp):
    fake = MagicMock()
    mock_sp.from_file.return_value = fake
    ex = PdfExtractor()
    ex.bind_pdf("/tmp/a.pdf", password="filepw")
    mock_sp.from_file.assert_called_once_with("/tmp/a.pdf", "filepw")


@patch("aspose_pdf.engine.simple_pdf.SimplePdf")
def test_bind_pdf_uses_password_property_when_arg_omitted(mock_sp):
    mock_sp.from_bytes.return_value = MagicMock()
    ex = PdfExtractor()
    ex.password = "from-prop"
    ex.bind_pdf(b"data")
    mock_sp.from_bytes.assert_called_once_with(b"data", "from-prop")


@patch("aspose_pdf.engine.simple_pdf.SimplePdf")
def test_bind_pdf_explicit_password_overrides_property(mock_sp):
    mock_sp.from_file.return_value = MagicMock()
    ex = PdfExtractor()
    ex.password = "wrong"
    ex.bind_pdf("/x.pdf", password="right")
    mock_sp.from_file.assert_called_once_with("/x.pdf", "right")


def test_bind_pdf_encrypted_without_password_surfaces_security_error():
    """When the engine requires a password, bind_pdf does not swallow it."""

    def _require_password(_data, password=None):
        if password is None:
            raise PdfSecurityException("Password required for encrypted document")
        return MagicMock()

    with patch("aspose_pdf.engine.simple_pdf.SimplePdf") as mock_sp:
        mock_sp.from_bytes.side_effect = _require_password
        ex = PdfExtractor()
        with pytest.raises(PdfSecurityException, match="Password required"):
            ex.bind_pdf(b"%PDF-encrypted")

"""Integration tests for encrypted PDF loading validation.

These tests verify that the :class:`aspose_pdf.engine.simple_pdf.SimplePdf`
class correctly handles encrypted documents.
"""

import pytest
import tempfile
from pathlib import Path

from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.exceptions import PdfSecurityException


def _create_encrypted_pdf_bytes(password: str) -> bytes:
    """Create a minimal encrypted PDF and return its bytes.

    The PDF contains a single empty page and the supplied password.
    """
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 612, 792)]
    pdf.page_contents = [b"BT /F1 12 Tf 100 700 Td (Secret) Tj ET"]
    pdf.encrypt(password)
    return pdf.to_bytes()


def test_encrypted_pdf_requires_password():
    """Loading an encrypted PDF without a password should raise ``PdfSecurityException``."""
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 612, 792)]
    pdf.page_contents = [b""]
    pdf.encrypt("s3cr3t")
    data = pdf.to_bytes()
    with pytest.raises(PdfSecurityException, match="Password required for encrypted document"):
        SimplePdf.from_bytes(data)


def test_encrypted_pdf_loads_with_correct_password():
    """Loading with the correct password should succeed and mark the PDF as encrypted."""
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 612, 792)]
    pdf.page_contents = [b"BT /F1 12 Tf 100 700 Td (Secret) Tj ET"]
    pdf.encrypt("secret123")
    data = pdf.to_bytes()
    loaded = SimplePdf.from_bytes(data, password="secret123")
    assert loaded.encrypted is True


def test_encrypted_pdf_from_file_requires_password():
    """Loading an encrypted PDF from a file without a password should raise ``PdfSecurityException``."""
    data = _create_encrypted_pdf_bytes("correct")
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)
    try:
        with pytest.raises(PdfSecurityException, match="Password required"):
            SimplePdf.from_file(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)


def test_encrypted_pdf_from_file_with_password():
    """Loading an encrypted PDF from a file with the correct password should succeed."""
    data = _create_encrypted_pdf_bytes("correct")
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)
    try:
        pdf = SimplePdf.from_file(tmp_path, password="correct")
        assert pdf.encrypted is True
    finally:
        tmp_path.unlink(missing_ok=True)


def test_load_from_encrypted_bytes_requires_password():
    """SimplePdf.load_from on encrypted bytes without a password should raise ``PdfSecurityException``."""
    data = _create_encrypted_pdf_bytes("mypwd")
    with pytest.raises(PdfSecurityException, match="Password required"):
        SimplePdf.load_from(data)


def test_non_encrypted_pdf_loads_without_password():
    """A non‑encrypted PDF should load without providing a password."""
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 612, 792)]
    pdf.page_contents = [b"Hello World"]
    data = pdf.to_bytes()
    loaded = SimplePdf.from_bytes(data)
    assert loaded.encrypted is False


def test_encrypted_pdf_with_wrong_password():
    """Providing an incorrect password should raise ``PdfSecurityException``."""
    data = _create_encrypted_pdf_bytes("correct")
    with pytest.raises(PdfSecurityException, match="Incorrect password"):
        SimplePdf.from_bytes(data, password="wrong")

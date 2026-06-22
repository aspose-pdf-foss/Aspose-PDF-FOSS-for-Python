"""AUDIT #18: Encrypted document load failures use ``PdfSecurityException``.

Callers can rely on this type (not a bare ``Exception``) for security-specific UX
and logging, consistent with ``aspose_pdf.exceptions``.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from aspose_pdf.document import Document
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.exceptions import AsposePdfException, PdfSecurityException


def _encrypted_pdf_bytes(password: str) -> bytes:
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 612, 792)]
    pdf.page_contents = [b""]
    pdf.encrypt(password)
    return pdf.to_bytes()


def test_simplepdf_from_bytes_missing_password_raises_pdf_security_exception() -> None:
    data = _encrypted_pdf_bytes("secret-a")
    with pytest.raises(PdfSecurityException, match="Password required") as ei:
        SimplePdf.from_bytes(data)
    assert isinstance(ei.value, AsposePdfException)


def test_simplepdf_from_bytes_wrong_password_raises_pdf_security_exception() -> None:
    data = _encrypted_pdf_bytes("right")
    with pytest.raises(PdfSecurityException, match="Incorrect password") as ei:
        SimplePdf.from_bytes(data, password="wrong")
    assert isinstance(ei.value, AsposePdfException)


def test_simplepdf_load_from_encrypted_bytes_raises_pdf_security_exception() -> None:
    data = _encrypted_pdf_bytes("cls")
    with pytest.raises(PdfSecurityException, match="Password required"):
        SimplePdf.load_from(data)


def test_simplepdf_from_file_encrypted_missing_password_raises_pdf_security_exception(
    tmp_path: Path,
) -> None:
    path = tmp_path / "enc.pdf"
    path.write_bytes(_encrypted_pdf_bytes("disk"))
    with pytest.raises(PdfSecurityException, match="Password required"):
        SimplePdf.from_file(path)


def test_document_load_from_bytes_encrypted_raises_pdf_security_exception() -> None:
    data = _encrypted_pdf_bytes("doc-bytes")
    doc = Document()
    with pytest.raises(PdfSecurityException, match="Password required"):
        doc.load_from(data)


def test_document_load_from_path_encrypted_raises_pdf_security_exception(
    tmp_path: Path,
) -> None:
    path = tmp_path / "enc-path.pdf"
    path.write_bytes(_encrypted_pdf_bytes("doc-path"))
    doc = Document()
    with pytest.raises(PdfSecurityException, match="Password required"):
        doc.load_from(path)


def test_document_load_from_stream_encrypted_raises_pdf_security_exception() -> None:
    data = _encrypted_pdf_bytes("stream-pwd")
    doc = Document()
    with pytest.raises(PdfSecurityException, match="Password required"):
        doc.load_from(io.BytesIO(data))

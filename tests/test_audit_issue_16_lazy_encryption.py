"""AUDIT #16: Lazy open must not treat whitespace as a password or skip crypto check.

Before any high-level structure (metadata, MediaBox list, outlines) is populated, an
encrypted PDF must have a non-empty stripped password that unlocks the /Encrypt
dictionary when U/O (and UE/OE for AES-256) are present.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aspose_pdf.document import Document
from aspose_pdf.exceptions import PdfSecurityException
from aspose_pdf.engine.simple_pdf import SimplePdf


def _encrypted_aes256_path(tmp_path: Path, password: str) -> Path:
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 612, 792)]
    pdf.page_contents = [b"BT /F1 12 Tf 0 0 Td (x) Tj ET"]
    pdf.encrypt(password)
    path = tmp_path / "audit16_enc.pdf"
    path.write_bytes(pdf.to_bytes())
    return path


def test_from_file_lazy_encrypted_whitespace_password_raises(tmp_path: Path) -> None:
    path = _encrypted_aes256_path(tmp_path, "secret")
    with pytest.raises(PdfSecurityException, match="Password required"):
        SimplePdf.from_file_lazy(path, password="   \t")


def test_open_streaming_encrypted_whitespace_password_raises(tmp_path: Path) -> None:
    path = _encrypted_aes256_path(tmp_path, "sec2")
    with pytest.raises(PdfSecurityException, match="Password required"):
        Document.open_streaming(path, password=" \n")


def test_from_file_lazy_encrypted_wrong_password_raises(tmp_path: Path) -> None:
    path = _encrypted_aes256_path(tmp_path, "right")
    with pytest.raises(PdfSecurityException, match="Incorrect password"):
        SimplePdf.from_file_lazy(path, password="wrong")


def test_from_file_lazy_encrypted_correct_password_loads(tmp_path: Path) -> None:
    path = _encrypted_aes256_path(tmp_path, "okpwd")
    pdf = SimplePdf.from_file_lazy(path, password="okpwd")
    try:
        assert pdf._lazy is True
        assert len(pdf.pages) >= 1
    finally:
        pdf.dispose()


def test_from_bytes_encrypted_wrong_password_raises(tmp_path: Path) -> None:
    path = _encrypted_aes256_path(tmp_path, "good")
    data = path.read_bytes()
    with pytest.raises(PdfSecurityException, match="Incorrect password"):
        SimplePdf.from_bytes(data, password="bad")

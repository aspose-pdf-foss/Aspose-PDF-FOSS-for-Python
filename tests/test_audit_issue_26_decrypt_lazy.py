"""AUDIT #26: ``SimplePdf.decrypt`` and lazy/streaming content paths.

``decrypt`` must not clear crypto state before lazy page streams are materialized.
After ``decrypt``, cached ``page_contents`` must still yield decoded plaintext.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.exceptions import PdfSecurityException


_MARK = b"BT /F1 12 Tf (Audit26Hello) Tj ET"


def _encrypted_pdf(tmp_path: Path, password: str) -> Path:
    plain = tmp_path / "plain.pdf"
    enc = tmp_path / "enc.pdf"
    doc = SimplePdf([(0, 0, 612, 792)], page_contents=[_MARK])
    doc.save(plain)
    loaded = SimplePdf.from_file(plain)
    loaded.encrypt(password, algorithm="AES-256")
    loaded.save(enc)
    return enc


def test_lazy_encrypted_page_content_is_decoded(tmp_path: Path) -> None:
    path = _encrypted_pdf(tmp_path, "lazy-sec")
    pdf = SimplePdf.from_file_lazy(path, password="lazy-sec")
    try:
        raw = pdf.get_page_content(0)
        assert _MARK in raw
        assert raw.strip().startswith(b"BT")
    finally:
        pdf.dispose()


def test_decrypt_materializes_lazy_pages_then_clear_crypto(tmp_path: Path) -> None:
    path = _encrypted_pdf(tmp_path, "d-sec")
    pdf = SimplePdf.from_file_lazy(path, password="d-sec")
    try:
        pdf.decrypt("d-sec")
        assert pdf.encrypted is False
        assert pdf.encryption_key is None
        assert _MARK in pdf.get_page_content(0)
        assert len(pdf.page_contents) >= 1
        assert _MARK in pdf.page_contents[0]
    finally:
        pdf.dispose()


def test_decrypt_wrong_password_raises(tmp_path: Path) -> None:
    path = _encrypted_pdf(tmp_path, "ok-pass")
    pdf = SimplePdf.from_file_lazy(path, password="ok-pass")
    try:
        with pytest.raises(PdfSecurityException, match="Incorrect password"):
            pdf.decrypt("other")
    finally:
        pdf.dispose()


def test_eager_encrypted_load_decrypts_streams_in_page_contents(tmp_path: Path) -> None:
    path = _encrypted_pdf(tmp_path, "eg-sec")
    pdf = SimplePdf.from_bytes(path.read_bytes(), password="eg-sec")
    assert pdf.encrypted is True
    assert pdf.page_contents
    assert _MARK in pdf.page_contents[0]

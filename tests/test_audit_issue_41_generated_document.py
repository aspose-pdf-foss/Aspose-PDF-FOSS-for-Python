"""AUDIT #41: generated Document must not swallow load errors; align with root semantics."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from aspose_pdf.exceptions import (
    AsposePdfException,
    PdfParseException,
    PdfSecurityException,
)
from aspose_pdf.generated.document import Document
from tests.helpers_make_pdfs import write_min_pdf


def test_generated_document_constructor_propagates_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "nope.pdf"
    with pytest.raises(FileNotFoundError):
        Document(str(missing))


def test_generated_document_load_from_propagates_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "nope.pdf"
    doc = Document()
    with pytest.raises(FileNotFoundError):
        doc.load_from(missing)


def test_generated_document_load_from_invalid_pdf_bytes() -> None:
    doc = Document()
    with pytest.raises(PdfParseException, match="PDF header"):
        doc.load_from(b"not a pdf")


def test_generated_document_load_from_stream_bad_header() -> None:
    """Stream loads use the same engine path as bytes (header check before parse)."""
    doc = Document()
    stream = io.BytesIO(b"not a pdf")
    with pytest.raises(PdfParseException, match="PDF header"):
        doc.load_from(stream)


def test_generated_document_encrypted_requires_password(tmp_path: Path) -> None:
    plain = tmp_path / "plain.pdf"
    enc = tmp_path / "enc.pdf"
    write_min_pdf(plain, page_count=1)

    encrypter = Document(str(plain))
    encrypter.encrypt("secret")
    encrypter.save(str(enc))

    with pytest.raises(PdfSecurityException, match="Password required"):
        Document(str(enc))

    with pytest.raises(PdfSecurityException, match="Password required"):
        Document().load_from(enc)

    opened = Document(str(enc), password="secret")
    assert opened.page_count == 1


def test_generated_document_save_returns_self_and_respects_overwrite(
    tmp_path: Path,
) -> None:
    path = tmp_path / "out.pdf"
    write_min_pdf(path, page_count=1)
    doc = Document(str(path))
    ret = doc.save(path, overwrite=True)
    assert ret is doc
    dst = tmp_path / "new.pdf"
    ret2 = doc.save(dst)
    assert ret2 is doc
    with pytest.raises(FileExistsError):
        doc.save(dst, overwrite=False)


def test_generated_document_load_populates_public_attrs(tmp_path: Path) -> None:
    """Loading must populate (not clobber) the public pages/file_name attrs."""
    path = tmp_path / "doc.pdf"
    write_min_pdf(path, page_count=2)

    doc = Document(str(path))
    assert doc.pages is not None
    assert doc.page_count == 2
    assert doc.file_name == str(path)
    assert doc.is_encrypted is False


def test_generated_document_maintenance_ops_delegate_and_chain(tmp_path: Path) -> None:
    """optimize/repair/flatten delegate to the engine and stay chainable."""
    path = tmp_path / "doc.pdf"
    write_min_pdf(path, page_count=1)

    doc = Document(str(path))
    assert doc.optimize() is doc
    assert doc.optimize_resources() is doc
    assert doc.repair() is doc
    assert doc.flatten() is doc
    assert doc.page_count == 1

    doc.dispose()
    with pytest.raises(AsposePdfException):
        doc.optimize()

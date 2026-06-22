"""PdfCosParser avoids eager full-buffer read() for mmap-able files and BytesIO."""

from __future__ import annotations

import io
from pathlib import Path

from aspose_pdf.engine.cos import PdfName
from aspose_pdf.engine.pdf_parser_cos import PdfCosParser, _cos_buffer_rfind


def _minimal_pdf_bytes() -> bytes:
    return (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >>\n"
        b"endobj\n"
        b"2 0 obj << /Type /Pages /Count 1 /Kids [3 0 R] >>\n"
        b"endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\n"
        b"endobj\n"
        b"xref\n0 4\n"
        b"0000000000 65535 f \n"
        b"0000000009 00000 n \n"
        b"0000000062 00000 n \n"
        b"0000000126 00000 n \n"
        b"trailer << /Root 1 0 R /Size 4 >>\n"
        b"startxref\n210\n%%EOF"
    )


class _NoReadBinaryFile:
    """File-like with fileno(); ``read()`` must not be used when mmap suffices."""

    def __init__(self, path: Path) -> None:
        self._f = open(path, "rb")

    def read(self, n: int = -1) -> bytes:
        raise AssertionError("read() should not be called when fileno() is available")

    def fileno(self) -> int:
        return self._f.fileno()

    def close(self) -> None:
        self._f.close()


class _NoReadBytesIO(io.BytesIO):
    def read(self, n: int = -1) -> bytes:
        raise AssertionError("read() should not be called for BytesIO sources")


def test_pdf_cos_parser_uses_mmap_for_real_file_not_read(tmp_path: Path) -> None:
    pdf_path = tmp_path / "tiny.pdf"
    pdf_path.write_bytes(_minimal_pdf_bytes())
    holder = _NoReadBinaryFile(pdf_path)
    try:
        doc = PdfCosParser(holder).parse()
    finally:
        holder.close()
    root = doc.trailer.get(PdfName("Root"))
    assert root is not None


def test_pdf_cos_parser_bytesio_uses_buffer_not_read() -> None:
    buf = _NoReadBytesIO(_minimal_pdf_bytes())
    doc = PdfCosParser(buf).parse()
    root = doc.trailer.get(PdfName("Root"))
    assert root is not None


def test_pdf_cos_parser_plain_bytesio_still_parses() -> None:
    buf = io.BytesIO(_minimal_pdf_bytes())
    doc = PdfCosParser(buf).parse()
    assert doc.trailer.get(PdfName("Root")) is not None


def test_pdf_cos_parser_path_via_open_file_object(tmp_path: Path) -> None:
    pdf_path = tmp_path / "x.pdf"
    pdf_path.write_bytes(_minimal_pdf_bytes())
    with open(pdf_path, "rb") as f:
        doc = PdfCosParser(f).parse()
    assert doc.trailer.get(PdfName("Root")) is not None


def test_cos_buffer_rfind_memoryview() -> None:
    """_cos_buffer_rfind supports buffers that do not implement ``rfind``."""
    raw = _minimal_pdf_bytes()
    idx = _cos_buffer_rfind(memoryview(raw), b"startxref")
    assert idx == raw.rfind(b"startxref")

"""AUDIT #33: ``SimplePdf`` mmap and hidden file handle lifecycle.

Memory-mapped loads (:meth:`~aspose_pdf.engine.simple_pdf.SimplePdf.from_file_lazy`
and large-file :meth:`~aspose_pdf.engine.simple_pdf.SimplePdf.from_file`) must
close the mapping in :meth:`~aspose_pdf.engine.simple_pdf.SimplePdf.dispose` so
file descriptors are not leaked when scripts omit explicit cleanup.
"""

from __future__ import annotations

import mmap
from pathlib import Path

from aspose_pdf.document import Document
from aspose_pdf.engine.simple_pdf import SimplePdf


def _tiny_valid_pdf(path: Path) -> None:
    doc = SimplePdf([(0, 0, 612, 792)], page_contents=[b"% audit33"])
    doc.save(path)


def test_from_file_lazy_dispose_closes_mmap(tmp_path: Path) -> None:
    pdf_path = tmp_path / "lazy.pdf"
    _tiny_valid_pdf(pdf_path)
    pdf = SimplePdf.from_file_lazy(pdf_path)
    raw = pdf._raw_bytes
    assert isinstance(raw, mmap.mmap)
    assert raw.closed is False
    pdf.dispose()
    assert raw.closed is True
    assert pdf._raw_bytes is None


def test_from_file_large_uses_mmap_and_dispose_closes(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(SimplePdf, "MIN_MMAP_SIZE", 0)
    pdf_path = tmp_path / "bigpath.pdf"
    _tiny_valid_pdf(pdf_path)
    pdf = SimplePdf.from_file(pdf_path)
    raw = pdf._raw_bytes
    assert isinstance(raw, mmap.mmap)
    pdf.dispose()
    assert raw.closed is True


def test_dispose_idempotent_after_mmap_load(tmp_path: Path) -> None:
    pdf_path = tmp_path / "idempotent.pdf"
    _tiny_valid_pdf(pdf_path)
    pdf = SimplePdf.from_file_lazy(pdf_path)
    pdf.dispose()
    pdf.dispose()
    assert pdf._disposed is True


def test_document_close_disposes_engine_mmap(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(SimplePdf, "MIN_MMAP_SIZE", 0)
    pdf_path = tmp_path / "doc.pdf"
    _tiny_valid_pdf(pdf_path)
    doc = Document()
    doc.load_from(str(pdf_path))
    engine = doc._engine_pdf
    assert engine is not None
    raw = engine._raw_bytes
    assert isinstance(raw, mmap.mmap)
    doc.close()
    assert raw.closed is True

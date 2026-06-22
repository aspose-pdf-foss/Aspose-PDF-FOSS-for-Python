"""AUDIT #34: ``dispose`` / ``close`` consistency (Document, facades, engine).

- :class:`~aspose_pdf.document.Document` follows ``subset_api.yaml``: primary
  :meth:`~aspose_pdf.document.Document.dispose`, :meth:`~aspose_pdf.document.Document.close` as alias.
- :class:`~aspose_pdf.facades.PdfFileEditor` path operations dispose every
  :class:`~aspose_pdf.engine.simple_pdf.SimplePdf` loaded via ``from_file``,
  including merged/extracted outputs (mmap and handles are not left open).
"""

from __future__ import annotations

import mmap
from pathlib import Path

from aspose_pdf.document import Document
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.facades import PdfFileEditor, PdfExtractor


def _tiny_valid_pdf(path: Path) -> None:
    doc = SimplePdf([(0, 0, 612, 792)], page_contents=[b"% audit34"])
    doc.save(path)


def test_document_dispose_canonical_close_is_alias(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(SimplePdf, "MIN_MMAP_SIZE", 0)
    pdf_path = tmp_path / "doc34.pdf"
    _tiny_valid_pdf(pdf_path)
    doc = Document()
    doc.load_from(str(pdf_path))
    engine = doc._engine_pdf
    assert engine is not None
    raw = engine._raw_bytes
    assert isinstance(raw, mmap.mmap)
    doc.dispose()
    assert raw.closed is True
    assert doc._disposed is True
    doc.dispose()  # idempotent
    doc.close()
    assert doc._disposed is True


def test_document_close_releases_mmap_same_as_dispose(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(SimplePdf, "MIN_MMAP_SIZE", 0)
    pdf_path = tmp_path / "close34.pdf"
    _tiny_valid_pdf(pdf_path)
    doc = Document()
    doc.load_from(str(pdf_path))
    engine = doc._engine_pdf
    raw = engine._raw_bytes
    assert isinstance(raw, mmap.mmap)
    doc.close()
    assert raw.closed is True


def _patch_dispose_count(monkeypatch, disposed: list[int]) -> None:
    _orig = SimplePdf.dispose

    def _dispose(self) -> None:  # type: ignore[no-untyped-def]
        disposed.append(1)
        _orig(self)

    monkeypatch.setattr(SimplePdf, "dispose", _dispose)


def test_pdf_file_editor_extract_disposes_source_and_result(
    tmp_path: Path, monkeypatch
) -> None:
    src = tmp_path / "in.pdf"
    out = tmp_path / "out.pdf"
    _tiny_valid_pdf(src)
    disposed: list[int] = []
    _patch_dispose_count(monkeypatch, disposed)
    ed = PdfFileEditor()
    assert ed.extract(str(src), str(out), 1, 1) is True
    assert sum(disposed) == 2  # full doc + extract_pages() result


def test_pdf_file_editor_insert_disposes_both_inputs(
    tmp_path: Path, monkeypatch
) -> None:
    base_p = tmp_path / "base.pdf"
    ins_p = tmp_path / "ins.pdf"
    out = tmp_path / "merged.pdf"
    _tiny_valid_pdf(base_p)
    _tiny_valid_pdf(ins_p)
    disposed: list[int] = []
    _patch_dispose_count(monkeypatch, disposed)
    ed = PdfFileEditor()
    assert ed.insert(str(base_p), str(ins_p), str(out), 1) is True
    assert sum(disposed) == 2


def test_pdf_file_editor_delete_disposes_source(tmp_path: Path, monkeypatch) -> None:
    src = tmp_path / "del.pdf"
    out = tmp_path / "del_out.pdf"
    _tiny_valid_pdf(src)
    disposed: list[int] = []
    _patch_dispose_count(monkeypatch, disposed)
    ed = PdfFileEditor()
    assert ed.delete(str(src), str(out), [1]) is True
    assert sum(disposed) == 1


def test_pdf_file_editor_add_page_break_disposes_source(
    tmp_path: Path, monkeypatch
) -> None:
    src = tmp_path / "break.pdf"
    out = tmp_path / "break_out.pdf"
    _tiny_valid_pdf(src)
    disposed: list[int] = []
    _patch_dispose_count(monkeypatch, disposed)
    ed = PdfFileEditor()
    assert ed.add_page_break(str(src), str(out)) is True
    assert sum(disposed) == 1


def test_pdf_file_editor_concatenate_disposes_inputs_and_merge_result(
    tmp_path: Path, monkeypatch
) -> None:
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    out = tmp_path / "cat.pdf"
    _tiny_valid_pdf(a)
    _tiny_valid_pdf(b)
    disposed: list[int] = []
    _patch_dispose_count(monkeypatch, disposed)
    ed = PdfFileEditor()
    assert ed.concatenate([str(a), str(b)], str(out)) is True
    assert sum(disposed) == 3  # two loads + merged SimplePdf from merge()


def test_pdf_extractor_close_disposes_bound_engine(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(SimplePdf, "MIN_MMAP_SIZE", 0)
    pdf_path = tmp_path / "ext.pdf"
    _tiny_valid_pdf(pdf_path)
    ex = PdfExtractor()
    ex.bind_pdf(str(pdf_path))
    bound = ex._bound_pdf
    assert bound is not None
    raw = bound._raw_bytes
    assert isinstance(raw, mmap.mmap)
    ex.close()
    assert raw.closed is True
    assert ex._bound_pdf is None


def test_pdf_extractor_dispose_matches_close(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(SimplePdf, "MIN_MMAP_SIZE", 0)
    pdf_path = tmp_path / "ext2.pdf"
    _tiny_valid_pdf(pdf_path)
    ex = PdfExtractor()
    ex.bind_pdf(str(pdf_path))
    bound = ex._bound_pdf
    assert bound is not None
    raw = bound._raw_bytes
    assert isinstance(raw, mmap.mmap)
    ex.dispose()
    assert raw.closed is True


def test_simple_pdf_close_delegates_to_dispose(tmp_path: Path) -> None:
    pdf_path = tmp_path / "sp.pdf"
    _tiny_valid_pdf(pdf_path)
    pdf = SimplePdf.from_file_lazy(pdf_path)
    raw = pdf._raw_bytes
    assert isinstance(raw, mmap.mmap)
    pdf.close()
    assert raw.closed is True

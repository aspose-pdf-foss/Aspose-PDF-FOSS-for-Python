"""Tests for :class:`PdfFileEditor` error surfacing via ``last_exception`` (AUDIT issue #3)."""

from pathlib import Path

from aspose_pdf.exceptions import AsposePdfException
from aspose_pdf.facades import PdfFileEditor
from tests.helpers_make_pdfs import write_min_pdf


def test_concatenate_missing_input_records_file_not_found(tmp_path: Path) -> None:
    missing = tmp_path / "nope.pdf"
    out = tmp_path / "out.pdf"
    editor = PdfFileEditor()
    assert editor.concatenate([missing], str(out)) is False
    assert editor.last_exception is not None
    assert isinstance(editor.last_exception, FileNotFoundError)


def test_concatenate_io_error_on_save_is_recorded(tmp_path: Path) -> None:
    """Missing parent directory for output triggers OSError; must not be a bare False."""
    src = tmp_path / "ok.pdf"
    write_min_pdf(src)
    out = tmp_path / "no_such_parent" / "subdir" / "out.pdf"
    editor = PdfFileEditor()
    assert editor.concatenate([src], str(out)) is False
    assert editor.last_exception is not None
    assert isinstance(editor.last_exception, OSError)


def test_extract_invalid_range_records_aspose_exception(tmp_path: Path) -> None:
    src = tmp_path / "one_page.pdf"
    write_min_pdf(src)
    out = tmp_path / "out.pdf"
    editor = PdfFileEditor()
    assert editor.extract(str(src), str(out), page_from=1, page_to=5) is False
    assert editor.last_exception is not None
    assert isinstance(editor.last_exception, AsposePdfException)
    assert "Invalid page range" in str(editor.last_exception)


def test_success_clears_last_exception(tmp_path: Path) -> None:
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    good_out = tmp_path / "good.pdf"
    write_min_pdf(a)
    write_min_pdf(b)

    editor = PdfFileEditor()
    assert editor.concatenate([tmp_path / "missing.pdf"], str(good_out)) is False
    assert editor.last_exception is not None

    ok = editor.concatenate([a, b], good_out)
    assert ok is True
    assert editor.last_exception is None

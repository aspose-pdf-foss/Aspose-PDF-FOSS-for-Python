"""Tests for :meth:`PdfFileEditor.try_make_booklet`."""

from pathlib import Path

from aspose_pdf.facades import PdfFileEditor
from tests.helpers_make_pdfs import write_min_pdf


def test_try_make_booklet_method_exists() -> None:
    """The method is available on PdfFileEditor."""
    editor = PdfFileEditor()
    assert hasattr(editor, "try_make_booklet")
    assert callable(getattr(editor, "try_make_booklet"))


def test_try_make_booklet_with_valid_files(tmp_path: Path) -> None:
    """try_make_booklet succeeds on valid input and returns True."""
    src = tmp_path / "input.pdf"
    out = tmp_path / "output.pdf"
    write_min_pdf(src)

    editor = PdfFileEditor()
    result = editor.try_make_booklet(str(src), str(out))
    assert result is True
    assert out.exists()


def test_try_make_booklet_with_missing_input(tmp_path: Path) -> None:
    """try_make_booklet returns False for non-existent input and does not raise."""
    missing = tmp_path / "no_such.pdf"
    out = tmp_path / "output.pdf"

    editor = PdfFileEditor()
    result = editor.try_make_booklet(str(missing), str(out))
    assert result is False
    assert editor.last_exception is not None


def test_try_make_booklet_delegates_to_make_booklet(tmp_path: Path) -> None:
    """try_make_booklet and make_booklet produce identical results."""
    src = tmp_path / "input.pdf"
    out1 = tmp_path / "output1.pdf"
    out2 = tmp_path / "output2.pdf"
    write_min_pdf(src)

    editor = PdfFileEditor()
    result1 = editor.make_booklet(str(src), str(out1))
    result2 = editor.try_make_booklet(str(src), str(out2))

    assert result1 == result2
    assert out1.exists()
    assert out2.exists()


def test_try_make_booklet_with_disposed_editor(tmp_path: Path) -> None:
    """try_make_booklet returns False (not raise) when called on a disposed editor."""
    src = tmp_path / "input.pdf"
    out = tmp_path / "output.pdf"
    write_min_pdf(src)

    editor = PdfFileEditor()
    editor.dispose()
    result = editor.try_make_booklet(str(src), str(out))
    assert result is False
    assert editor.last_exception is not None

"""The PDF/A options and result, through both import paths.

``aspose_pdf.generated.pdfa`` held inert look-alikes of these classes -- options
that stored keyword arguments and validated nothing, and a result that reported
itself invalid with no errors at all. It now re-exports the real ones.
"""

import io

import pytest

from aspose_pdf import pdfa
from aspose_pdf.generated.pdfa import PdfAValidateOptions, PdfAValidationResult


def test_the_compatibility_import_path_gives_the_real_classes():
    assert PdfAValidateOptions is pdfa.PdfAValidateOptions
    assert PdfAValidationResult is pdfa.PdfAValidationResult


def test_add_input_accepts_a_file_bytes_and_a_stream(tmp_path):
    file_path = tmp_path / "sample.pdf"
    file_path.write_bytes(b"%PDF-1.4 test content")
    opts = PdfAValidateOptions()
    assert opts.add_input(file_path) is opts
    opts.add_input(b"%PDF-1.4 bytes").add_input(bytearray(b"%PDF-1.4 array"))
    opts.add_input(io.BytesIO(b"%PDF-1.4 stream"))
    assert opts.inputs == [file_path, b"%PDF-1.4 bytes", b"%PDF-1.4 array", b"%PDF-1.4 stream"]


def test_add_input_rejects_what_is_not_an_input():
    opts = PdfAValidateOptions()
    with pytest.raises(Exception):
        opts.add_input(123)
    with pytest.raises(Exception):
        opts.add_input("nonexistent.pdf")


def test_a_result_is_valid_until_an_error_is_added():
    result = PdfAValidationResult()
    assert result.is_valid is True
    result.add_error("sample error")
    result.add_warning("sample warning")
    assert result.is_valid is False
    assert result.to_dict()["errors"] == ["sample error"]
    assert result.to_dict()["warnings"] == ["sample warning"]
    assert "is_valid=False" in repr(result)


def test_a_result_rejects_errors_that_are_not_text():
    result = PdfAValidationResult()
    with pytest.raises(TypeError):
        result.add_error(123)
    with pytest.raises(TypeError):
        result.add_warning(123)

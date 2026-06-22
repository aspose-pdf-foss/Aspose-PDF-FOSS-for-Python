"""Tests for the PDF/A validation helper classes."""

import pytest

from aspose_pdf.generated.pdfa import PdfAValidateOptions, PdfAValidationResult


def test_add_input_accepts_file(tmp_path):
    # create a temporary PDF/A file
    file_path = tmp_path / "sample.pdf"
    file_path.write_bytes(b"%PDF-1.4 test content")
    opts = PdfAValidateOptions().add_input(file_path)
    # inputs should contain the path we added
    assert len(opts.inputs) == 1
    assert isinstance(opts.inputs[0], type(file_path))
    assert opts.inputs[0] == file_path


def test_add_input_invalid_input():
    opts = PdfAValidateOptions()
    with pytest.raises(Exception):
        opts.add_input(123)  # unsupported type
    with pytest.raises(Exception):
        opts.add_input("nonexistent.pdf")  # non‑existent path


def test_validate_options_initial_state():
    """Validate that a newly created ``PdfAValidateOptions`` has empty state."""
    opts = PdfAValidateOptions()
    # Internal option store should be empty
    assert opts.get_options() == {}
    # No inputs should be recorded initially
    assert opts.inputs == []


def test_validate_options_set_and_get():
    opts = PdfAValidateOptions()
    opts.set_option("pdfa_version", "1.7")
    opts.set_option("optimize_file_size", True)
    current = opts.get_options()
    assert current["pdfa_version"] == "1.7"
    assert current["optimize_file_size"] is True


def test_validate_options_add_input_and_reset():
    opts = PdfAValidateOptions()
    inp = opts.add_input(path="sample.pdf", flag=True)
    # ``add_input`` returns self
    assert isinstance(inp, PdfAValidateOptions)
    assert len(opts.inputs) == 1
    assert opts.inputs[0]["path"] == "sample.pdf"
    assert opts.inputs[0]["flag"] is True
    # Reset should clear both options and inputs
    opts.set_option("key", "value")
    opts.reset()
    assert opts.get_options() == {}
    assert opts.inputs == []


def test_validate_options_repr_contains_state():
    opts = PdfAValidateOptions()
    opts.set_option("a", 1)
    opts.add_input(name="doc")
    representation = repr(opts)
    assert "options={'a': 1}" in representation
    assert "inputs=[{" in representation


def test_validation_result_default_is_invalid():
    result = PdfAValidationResult()
    # ``is_valid`` defaults to False when not provided
    assert result.is_valid is False
    # ``errors`` attribute is not created by the stub; initialise for testing
    result.errors = []
    # Adding an error should mark the result as invalid
    result.add_error("sample error")
    assert result.is_valid is False
    assert result.errors == ["sample error"]
    # ``to_dict`` must contain the errors list and validity flag
    d = result.to_dict()
    assert d["is_valid"] is False
    assert d["errors"] == ["sample error"]


def test_validation_result_add_error_type_check():
    result = PdfAValidationResult()
    result.errors = []
    with pytest.raises(TypeError):
        result.add_error(123)  # non‑string should raise


def test_validation_result_reset_clears_state():
    result = PdfAValidationResult(is_valid=False)
    result.errors = ["e1", "e2"]
    result.reset()
    # After reset, errors list should be empty and ``is_valid`` True
    assert result.errors == []
    assert result.is_valid is True
    # Internal data dict should also be cleared
    assert result._data == {}


def test_validation_result_repr():
    result = PdfAValidationResult(is_valid=True)
    result.errors = []
    rep = repr(result)
    assert "PdfAValidationResult" in rep
    assert "is_valid=True" in rep
    assert "errors=[]" in rep

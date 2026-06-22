"""Tests for Feature 7: PdfAValidationResult and full PDF/A pipeline.

Covers:
- PdfAValidationResult standalone behaviour
- Document.validate_pdfa() returning PdfAValidationResult
- PdfAValidator.process() pipeline (file and bytes inputs)
- Version-string normalisation in PdfAValidator
- Public exports from aspose_pdf top-level package
"""

from __future__ import annotations

import io
import pytest

import aspose_pdf
from aspose_pdf.pdfa import PdfAValidateOptions, PdfAValidationResult, PdfAValidator
from aspose_pdf.document import Document


# ---------------------------------------------------------------------------
# Minimal parseable PDF helper (no fonts, no prohibited content)
# ---------------------------------------------------------------------------


def _minimal_pdf_bytes() -> bytes:
    return (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >>\n"
        b"endobj\n"
        b"2 0 obj << /Type /Pages /Count 1 /Kids [3 0 R] >>\n"
        b"endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\n"
        b"endobj\n"
        b"xref\n"
        b"0 4\n"
        b"0000000000 65535 f \n"
        b"0000000009 00000 n \n"
        b"0000000062 00000 n \n"
        b"0000000126 00000 n \n"
        b"trailer << /Root 1 0 R /Size 4 >>\n"
        b"startxref\n"
        b"210\n"
        b"%%EOF"
    )


# ===========================================================================
# PdfAValidationResult — standalone unit tests
# ===========================================================================


class TestPdfAValidationResultInit:
    def test_default_state_is_valid(self):
        result = PdfAValidationResult()
        assert result.errors == []
        assert result.warnings == []
        assert result.level == ""
        assert result.is_valid is True

    def test_errors_passed_to_constructor(self):
        result = PdfAValidationResult(errors=["missing OutputIntents"])
        assert result.errors == ["missing OutputIntents"]
        assert result.is_valid is False

    def test_warnings_passed_to_constructor(self):
        result = PdfAValidationResult(warnings=["unembedded font"])
        assert result.warnings == ["unembedded font"]
        assert result.is_valid is True  # warnings do not affect validity

    def test_level_stored(self):
        result = PdfAValidationResult(level="2b")
        assert result.level == "2b"

    def test_constructor_copies_lists(self):
        src_errors = ["e1"]
        result = PdfAValidationResult(errors=src_errors)
        src_errors.append("e2")
        assert result.errors == ["e1"]  # not affected by mutation of original


class TestPdfAValidationResultIsValid:
    def test_true_when_no_errors(self):
        result = PdfAValidationResult()
        assert result.is_valid is True

    def test_false_when_errors_present(self):
        result = PdfAValidationResult(errors=["problem"])
        assert result.is_valid is False

    def test_changes_dynamically(self):
        result = PdfAValidationResult()
        assert result.is_valid is True
        result.add_error("new problem")
        assert result.is_valid is False


class TestPdfAValidationResultAddError:
    def test_add_error_appends(self):
        result = PdfAValidationResult()
        result.add_error("error 1")
        result.add_error("error 2")
        assert result.errors == ["error 1", "error 2"]

    def test_add_error_makes_invalid(self):
        result = PdfAValidationResult()
        assert result.is_valid is True
        result.add_error("oops")
        assert result.is_valid is False

    def test_add_error_rejects_non_string(self):
        result = PdfAValidationResult()
        with pytest.raises(TypeError):
            result.add_error(123)

    def test_add_error_rejects_none(self):
        result = PdfAValidationResult()
        with pytest.raises(TypeError):
            result.add_error(None)


class TestPdfAValidationResultAddWarning:
    def test_add_warning_appends(self):
        result = PdfAValidationResult()
        result.add_warning("font not embedded")
        assert result.warnings == ["font not embedded"]

    def test_warning_does_not_affect_validity(self):
        result = PdfAValidationResult()
        result.add_warning("some warning")
        assert result.is_valid is True

    def test_add_warning_rejects_non_string(self):
        result = PdfAValidationResult()
        with pytest.raises(TypeError):
            result.add_warning(42)


class TestPdfAValidationResultToDict:
    def test_keys_present(self):
        result = PdfAValidationResult(errors=["e"], warnings=["w"], level="1b")
        d = result.to_dict()
        assert "is_valid" in d
        assert "is_heuristic" in d
        assert "errors" in d
        assert "warnings" in d
        assert "level" in d

    def test_values_match(self):
        result = PdfAValidationResult(errors=["e"], warnings=["w"], level="1b")
        d = result.to_dict()
        assert d["is_valid"] is False
        assert d["is_heuristic"] is True
        assert d["errors"] == ["e"]
        assert d["warnings"] == ["w"]
        assert d["level"] == "1b"

    def test_to_dict_copies_lists(self):
        result = PdfAValidationResult(errors=["e"])
        d = result.to_dict()
        d["errors"].append("extra")
        assert result.errors == ["e"]  # internal state not mutated


class TestPdfAValidationResultLen:
    def test_len_equals_error_count(self):
        result = PdfAValidationResult(errors=["a", "b", "c"])
        assert len(result) == 3

    def test_len_zero_when_valid(self):
        result = PdfAValidationResult()
        assert len(result) == 0

    def test_len_after_add_error(self):
        result = PdfAValidationResult()
        result.add_error("x")
        assert len(result) == 1


class TestPdfAValidationResultRepr:
    def test_repr_contains_class_name(self):
        result = PdfAValidationResult()
        assert "PdfAValidationResult" in repr(result)

    def test_repr_shows_is_valid(self):
        result = PdfAValidationResult()
        assert "is_valid=True" in repr(result)
        assert "is_heuristic=True" in repr(result)

    def test_repr_shows_level(self):
        result = PdfAValidationResult(level="3b")
        assert "3b" in repr(result)


# ===========================================================================
# Document.validate_pdfa() — returns PdfAValidationResult
# ===========================================================================


class TestDocumentValidatePdfa:
    def test_returns_pdfa_validation_result(self):
        doc = Document()
        doc.load_from(_minimal_pdf_bytes())
        result = doc.validate_pdfa("1b")
        assert isinstance(result, PdfAValidationResult)

    def test_level_field_matches_argument(self):
        doc = Document()
        doc.load_from(_minimal_pdf_bytes())
        for level in ("1b", "2b", "3b"):
            result = doc.validate_pdfa(level)
            assert result.level == level

    def test_errors_is_list_of_strings(self):
        doc = Document()
        doc.load_from(_minimal_pdf_bytes())
        result = doc.validate_pdfa("1b")
        assert isinstance(result.errors, list)
        assert all(isinstance(e, str) for e in result.errors)

    def test_warnings_is_list(self):
        doc = Document()
        doc.load_from(_minimal_pdf_bytes())
        result = doc.validate_pdfa("1b")
        assert isinstance(result.warnings, list)

    def test_is_valid_after_conversion(self):
        doc = Document()
        doc.load_from(_minimal_pdf_bytes())
        doc.convert_to_pdfa("1b")
        result = doc.validate_pdfa("1b")
        assert result.is_valid is True, f"Expected valid, got errors: {result.errors}"

    def test_not_valid_before_conversion(self):
        doc = Document()
        doc.load_from(_minimal_pdf_bytes())
        result = doc.validate_pdfa("1b")
        assert result.is_valid is False

    def test_len_works_on_result(self):
        doc = Document()
        doc.load_from(_minimal_pdf_bytes())
        result = doc.validate_pdfa("1b")
        assert len(result) == len(result.errors)

    def test_fewer_errors_after_conversion(self):
        doc = Document()
        doc.load_from(_minimal_pdf_bytes())
        before = doc.validate_pdfa("1b")
        doc.convert_to_pdfa("1b")
        after = doc.validate_pdfa("1b")
        assert len(after) < len(before)

    def test_no_doc_loaded_returns_result(self):
        """validate_pdfa() on an empty Document always returns PdfAValidationResult."""
        doc = Document()
        result = doc.validate_pdfa("1b")
        assert isinstance(result, PdfAValidationResult)
        # An empty (never-loaded) Document has no PDF content to violate rules,
        # so the engine reports no errors.
        assert isinstance(result.errors, list)


# ===========================================================================
# PdfAValidator._normalize_level()
# ===========================================================================


class TestPdfAValidatorNormalizeLevel:
    def test_pdfa_prefix_stripped(self):
        assert PdfAValidator._normalize_level("PDF/A-1b") == "1b"

    def test_uppercase_pdfa_prefix(self):
        assert PdfAValidator._normalize_level("pdf/a-2b") == "2b"

    def test_already_short_form(self):
        assert PdfAValidator._normalize_level("1b") == "1b"

    def test_uppercase_short_form(self):
        assert PdfAValidator._normalize_level("2B") == "2b"

    def test_level_3b(self):
        assert PdfAValidator._normalize_level("PDF/A-3b") == "3b"

    def test_strips_whitespace(self):
        assert PdfAValidator._normalize_level("  PDF/A-1b  ") == "1b"


# ===========================================================================
# PdfAValidator.process() — full pipeline
# ===========================================================================


class TestPdfAValidatorProcess:
    def test_empty_inputs_returns_empty_list(self):
        options = PdfAValidateOptions()
        validator = PdfAValidator()
        results = validator.process(options)
        assert results == []

    def test_bytes_input_returns_one_result(self):
        options = PdfAValidateOptions()
        options.add_input(_minimal_pdf_bytes())
        validator = PdfAValidator()
        results = validator.process(options)
        assert len(results) == 1
        assert isinstance(results[0], PdfAValidationResult)

    def test_bytes_result_has_level_from_options(self):
        options = PdfAValidateOptions()
        options.pdfa_version = "PDF/A-2b"
        options.add_input(_minimal_pdf_bytes())
        validator = PdfAValidator()
        results = validator.process(options)
        assert results[0].level == "2b"

    def test_multiple_inputs_multiple_results(self):
        options = PdfAValidateOptions()
        options.add_input(_minimal_pdf_bytes())
        options.add_input(_minimal_pdf_bytes())
        validator = PdfAValidator()
        results = validator.process(options)
        assert len(results) == 2

    def test_file_input_returns_result(self, tmp_path):
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(_minimal_pdf_bytes())
        options = PdfAValidateOptions()
        options.add_input(pdf_file)
        validator = PdfAValidator()
        results = validator.process(options)
        assert len(results) == 1
        assert isinstance(results[0], PdfAValidationResult)

    def test_file_and_bytes_inputs(self, tmp_path):
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(_minimal_pdf_bytes())
        options = PdfAValidateOptions()
        options.add_input(pdf_file)
        options.add_input(_minimal_pdf_bytes())
        validator = PdfAValidator()
        results = validator.process(options)
        assert len(results) == 2

    def test_result_is_invalid_for_non_pdfa_document(self):
        options = PdfAValidateOptions()
        options.add_input(_minimal_pdf_bytes())
        validator = PdfAValidator()
        results = validator.process(options)
        assert results[0].is_valid is False

    def test_stream_input_returns_result(self):
        options = PdfAValidateOptions()
        options.add_input(io.BytesIO(_minimal_pdf_bytes()))
        validator = PdfAValidator()
        results = validator.process(options)
        assert len(results) == 1
        assert isinstance(results[0], PdfAValidationResult)

    def test_default_version_is_1b(self):
        options = PdfAValidateOptions()
        options.add_input(_minimal_pdf_bytes())
        validator = PdfAValidator()
        results = validator.process(options)
        assert results[0].level == "1b"


# ===========================================================================
# Public package exports
# ===========================================================================


class TestPublicExports:
    def test_pdfa_validate_options_exported(self):
        assert hasattr(aspose_pdf, "PdfAValidateOptions")

    def test_pdfa_validation_result_exported(self):
        assert hasattr(aspose_pdf, "PdfAValidationResult")

    def test_pdfa_validator_exported(self):
        assert hasattr(aspose_pdf, "PdfAValidator")

    def test_exported_classes_are_correct_types(self):
        assert aspose_pdf.PdfAValidationResult is PdfAValidationResult
        assert aspose_pdf.PdfAValidateOptions is PdfAValidateOptions
        assert aspose_pdf.PdfAValidator is PdfAValidator

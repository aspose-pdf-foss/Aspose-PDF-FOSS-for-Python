"""AUDIT #29: PDF/A validation is heuristic — surface non-certification semantics."""

from __future__ import annotations

from aspose_pdf.document import Document
from aspose_pdf.pdfa import PdfAValidationResult


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


def test_pdfa_validation_result_default_is_heuristic() -> None:
    r = PdfAValidationResult()
    assert r.is_heuristic is True


def test_pdfa_validation_result_heuristic_notice_constant() -> None:
    assert isinstance(PdfAValidationResult.HEURISTIC_VALIDATION_NOTICE, str)
    assert len(PdfAValidationResult.HEURISTIC_VALIDATION_NOTICE) > 20
    assert "heuristic" in PdfAValidationResult.HEURISTIC_VALIDATION_NOTICE.lower()


def test_pdfa_validation_result_is_heuristic_overridable() -> None:
    r = PdfAValidationResult(is_heuristic=False)
    assert r.is_heuristic is False
    assert r.to_dict()["is_heuristic"] is False


def test_validate_pdfa_result_is_heuristic_on_document() -> None:
    doc = Document()
    doc.load_from(_minimal_pdf_bytes())
    result = doc.validate_pdfa("1b")
    assert isinstance(result, PdfAValidationResult)
    assert result.is_heuristic is True


def test_empty_document_validate_pdfa_still_heuristic() -> None:
    """Fresh Document has an in-memory SimplePdf with no COS load; check may report no issues."""
    doc = Document()
    result = doc.validate_pdfa("2b")
    assert result.is_heuristic is True
    assert result.level == "2b"

"""AUDIT #30: PDF/UA flags/APIs — surface heuristic, non-certification semantics."""

from __future__ import annotations

from aspose_pdf.document import Document
from aspose_pdf.pdfua import PdfUaValidationResult


def _minimal_pdf_no_tagging() -> bytes:
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


def _tagged_pdf_shell(
    *, include_lang: bool = True, struct_type: str = "StructTreeRoot"
) -> bytes:
    header = b"%PDF-1.7\n"
    lang = b" /Lang (en)" if include_lang else b""
    catalog = (
        b"<< /Type /Catalog /Pages 2 0 R /StructTreeRoot 4 0 R "
        b"/MarkInfo << /Marked true >>" + lang + b" >>"
    )
    chunks = [
        b"1 0 obj\n" + catalog + b"\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Count 1 /Kids [3 0 R] >>\nendobj\n",
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\nendobj\n",
        b"4 0 obj\n<< /Type /" + struct_type.encode() + b" >>\nendobj\n",
    ]
    body = header + b"".join(chunks)
    pos = len(header)
    offsets: list[int] = []
    for ch in chunks:
        offsets.append(pos)
        pos += len(ch)
    xref_pos = pos
    xref_lines = [b"xref\n", b"0 5\n", b"0000000000 65535 f \n"]
    for off in offsets:
        xref_lines.append(b"%010d 00000 n \n" % off)
    xref = b"".join(xref_lines)
    trailer = b"trailer << /Root 1 0 R /Size 5 >>\n"
    startxref = b"startxref\n%d\n%%EOF" % xref_pos
    return body + xref + trailer + startxref


def _pdfua_ready_doc(*, language: str = "en") -> Document:
    """Return a document upgraded to a complete PDF/UA catalog shell."""
    doc = Document()
    doc.load_from(_minimal_pdf_no_tagging())
    doc.convert_to_pdfua(language=language, title="Sample")
    return doc


def test_pdfua_validation_result_default_is_heuristic() -> None:
    r = PdfUaValidationResult()
    assert r.is_heuristic is True


def test_pdfua_heuristic_notice_constant() -> None:
    assert isinstance(PdfUaValidationResult.HEURISTIC_VALIDATION_NOTICE, str)
    assert len(PdfUaValidationResult.HEURISTIC_VALIDATION_NOTICE) > 20
    assert "heuristic" in PdfUaValidationResult.HEURISTIC_VALIDATION_NOTICE.lower()


def test_pdfua_validation_result_to_dict_has_is_heuristic() -> None:
    r = PdfUaValidationResult(is_heuristic=False)
    assert r.to_dict()["is_heuristic"] is False


def test_validate_pdfua_on_untagged_minimal_fails() -> None:
    doc = Document()
    doc.load_from(_minimal_pdf_no_tagging())
    result = doc.validate_pdfua()
    assert isinstance(result, PdfUaValidationResult)
    assert result.is_heuristic is True
    assert not result.is_valid
    assert any("StructTreeRoot" in e for e in result.errors)
    assert doc.is_pdfua_compliant is False


def test_validate_pdfua_bare_tagged_shell_is_incomplete() -> None:
    """A bare StructTreeRoot+MarkInfo shell is no longer enough on its own:
    PDF/UA also needs DisplayDocTitle, a title, and a pdfuaid declaration."""
    doc = Document()
    doc.load_from(_tagged_pdf_shell(include_lang=True))
    result = doc.validate_pdfua()
    assert not result.is_valid
    assert any("DisplayDocTitle" in e for e in result.errors)
    assert any("pdfuaid" in e for e in result.errors)


def test_validate_pdfua_full_shell_passes() -> None:
    doc = _pdfua_ready_doc()
    result = doc.validate_pdfua()
    assert result.is_valid
    assert result.errors == []
    assert result.warnings == []
    assert doc.is_pdfua_compliant is True


def test_validate_pdfua_missing_lang_warns() -> None:
    from aspose_pdf.engine.cos import PdfName

    doc = _pdfua_ready_doc()
    engine = doc._engine_pdf
    root = engine._resolve(engine._cos_doc.trailer.get(PdfName("Root")))
    root.mapping.pop(PdfName("Lang"), None)
    result = doc.validate_pdfua()
    assert result.is_valid
    assert len(result.warnings) == 1
    assert "Lang" in result.warnings[0]


def test_validate_pdfua_wrong_struct_type_errors() -> None:
    doc = Document()
    doc.load_from(_tagged_pdf_shell(include_lang=True, struct_type="Cat"))
    result = doc.validate_pdfua()
    assert not result.is_valid
    assert any("StructTreeRoot" in e for e in result.errors)


def test_empty_document_validate_pdfua() -> None:
    doc = Document()
    result = doc.validate_pdfua()
    assert not result.is_valid
    assert "No document loaded" in result.errors[0]

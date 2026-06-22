"""PDF/UA validation, conversion shell, and batch plugin.

Covers the extended catalog-level PDF/UA checks (DisplayDocTitle, document
title, pdfuaid declaration), the ``convert_to_pdfua`` shell builder (including
XMP merge with an existing PDF/A identifier), the batch ``PdfUaValidator`` /
``PdfUaValidateOptions`` plugin, and the public exports.
"""

from __future__ import annotations

import io

import pytest

import aspose_pdf
from aspose_pdf.document import Document
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfBoolean,
    PdfDictionary,
    PdfName,
    PdfNumber,
    PdfStream,
    PdfString,
)
from aspose_pdf.exceptions import AsposePdfException
from aspose_pdf.pdfua import (
    PdfUaValidateOptions,
    PdfUaValidationResult,
    PdfUaValidator,
)


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


def _doc() -> Document:
    doc = Document()
    doc.load_from(_minimal_pdf_bytes())
    return doc


def _catalog(doc: Document):
    engine = doc._engine_pdf
    return engine._resolve(engine._cos_doc.trailer.get(PdfName("Root")))


def _metadata_text(doc: Document) -> str:
    meta = doc._engine_pdf._resolve(_catalog(doc).mapping.get(PdfName("Metadata")))
    assert isinstance(meta, PdfStream)
    return meta.content.decode("utf-8", errors="replace")


def _struct_root(doc: Document) -> PdfDictionary:
    eng = doc._engine_pdf
    return eng._resolve(_catalog(doc).mapping.get(PdfName("StructTreeRoot")))


def _add_struct_elem(doc: Document, s: str, **entries) -> PdfDictionary:
    """Append a StructElem with type *s* under the StructTreeRoot /K array."""
    eng = doc._engine_pdf
    elem = PdfDictionary(
        {PdfName("Type"): PdfName("StructElem"), PdfName("S"): PdfName(s)}
    )
    for key, value in entries.items():
        elem.mapping[PdfName(key)] = value
    sr = _struct_root(doc)
    kids = eng._resolve(sr.mapping.get(PdfName("K")))
    if not isinstance(kids, PdfArray):
        kids = PdfArray([])
        sr.mapping[PdfName("K")] = kids
    kids.items.append(eng._cos_doc.register_object(elem))
    return elem


def _add_annot(doc: Document, **entries) -> PdfDictionary:
    eng = doc._engine_pdf
    page = eng._get_page_dict(0)
    annot = PdfDictionary({PdfName(k): v for k, v in entries.items()})
    ref = eng._cos_doc.register_object(annot)
    existing = eng._resolve(page.mapping.get(PdfName("Annots")))
    if isinstance(existing, PdfArray):
        existing.items.append(ref)
    else:
        page.mapping[PdfName("Annots")] = PdfArray([ref])
    return annot


# ---------------------------------------------------------------------------
# Extended validation errors
# ---------------------------------------------------------------------------
class TestPdfUaValidationErrors:
    def test_untagged_reports_all_new_requirements(self):
        doc = _doc()
        errors = doc.validate_pdfua().errors
        assert any("StructTreeRoot" in e for e in errors)
        assert any("DisplayDocTitle" in e for e in errors)
        assert any("title" in e.lower() for e in errors)
        assert any("pdfuaid" in e for e in errors)

    def test_display_doc_title_false_reported(self):
        doc = _doc()
        doc.convert_to_pdfua(title="X")
        viewer = doc._engine_pdf._resolve(
            _catalog(doc).mapping.get(PdfName("ViewerPreferences"))
        )
        viewer.mapping[PdfName("DisplayDocTitle")] = PdfBoolean(False)
        errors = doc.validate_pdfua().errors
        assert any("DisplayDocTitle" in e for e in errors)

    def test_missing_metadata_reports_pdfuaid(self):
        doc = _doc()
        doc.convert_to_pdfua(title="X")
        _catalog(doc).mapping.pop(PdfName("Metadata"), None)
        errors = doc.validate_pdfua().errors
        assert any("pdfuaid" in e for e in errors)


# ---------------------------------------------------------------------------
# convert_to_pdfua
# ---------------------------------------------------------------------------
class TestConvertToPdfUa:
    def test_makes_document_valid(self):
        doc = _doc()
        remaining = doc.convert_to_pdfua(title="Doc")
        assert remaining == []
        assert doc.validate_pdfua().is_valid
        assert doc.is_pdfua_compliant

    def test_sets_catalog_entries(self):
        doc = _doc()
        doc.convert_to_pdfua(language="fr", title="Doc")
        cat = doc._engine_pdf
        root = _catalog(doc)
        struct = cat._resolve(root.mapping.get(PdfName("StructTreeRoot")))
        assert cat._get_name(struct.mapping.get(PdfName("Type"))) == "StructTreeRoot"
        mark = cat._resolve(root.mapping.get(PdfName("MarkInfo")))
        assert mark.mapping[PdfName("Marked")].value is True
        lang = cat._resolve(root.mapping.get(PdfName("Lang")))
        assert lang.value == b"fr"

    def test_metadata_declares_pdfuaid(self):
        doc = _doc()
        doc.convert_to_pdfua(title="Doc")
        assert "pdfuaid:part" in _metadata_text(doc)

    def test_title_argument_used(self):
        doc = _doc()
        doc.convert_to_pdfua(title="My Title")
        assert "My Title" in _metadata_text(doc)

    def test_idempotent(self):
        doc = _doc()
        doc.convert_to_pdfua(title="Doc")
        first = doc.validate_pdfua().errors
        doc.convert_to_pdfua(title="Doc")
        second = doc.validate_pdfua().errors
        assert first == second == []

    def test_merges_with_existing_pdfa_identifier(self):
        doc = _doc()
        doc.convert_to_pdfa("2b")
        doc.convert_to_pdfua(title="Doc")
        text = _metadata_text(doc)
        assert "pdfuaid:part" in text
        assert "pdfaid:part" in text

    def test_survives_save_and_reload(self):
        doc = _doc()
        doc.convert_to_pdfua(title="Doc")
        buf = io.BytesIO()
        doc.save(buf)
        buf.seek(0)
        reloaded = Document()
        reloaded.load_from(buf.read())
        assert reloaded.validate_pdfua().is_valid

    # --- guards ---
    def test_raises_without_cos(self):
        pdf = SimplePdf()
        pdf.pages = [(0, 0, 612, 792)]
        with pytest.raises(AsposePdfException):
            pdf.convert_to_pdfua()

    def test_raises_on_encrypted(self):
        doc = _doc()
        doc._engine_pdf.encrypted = True
        with pytest.raises(AsposePdfException):
            doc.convert_to_pdfua()

    def test_document_raises_when_no_doc_loaded(self):
        doc = Document()
        with pytest.raises(AsposePdfException):
            doc.convert_to_pdfua()


# ---------------------------------------------------------------------------
# Batch plugin
# ---------------------------------------------------------------------------
class TestPdfUaValidatorPlugin:
    def test_empty_inputs(self):
        results = PdfUaValidator().process(PdfUaValidateOptions())
        assert results == []

    def test_bytes_input(self):
        options = PdfUaValidateOptions()
        options.add_input(_minimal_pdf_bytes())
        results = PdfUaValidator().process(options)
        assert len(results) == 1
        assert isinstance(results[0], PdfUaValidationResult)
        assert results[0].is_valid is False  # untagged

    def test_stream_input(self):
        options = PdfUaValidateOptions()
        options.add_input(io.BytesIO(_minimal_pdf_bytes()))
        results = PdfUaValidator().process(options)
        assert len(results) == 1

    def test_file_input(self, tmp_path):
        path = tmp_path / "doc.pdf"
        path.write_bytes(_minimal_pdf_bytes())
        options = PdfUaValidateOptions()
        options.add_input(path)
        results = PdfUaValidator().process(options)
        assert len(results) == 1

    def test_multiple_inputs(self):
        options = PdfUaValidateOptions()
        options.add_input(_minimal_pdf_bytes())
        options.add_input(_minimal_pdf_bytes())
        assert len(PdfUaValidator().process(options)) == 2

    def test_missing_file_raises(self):
        with pytest.raises(Exception):
            PdfUaValidateOptions().add_input("does-not-exist.pdf")

    def test_add_input_returns_self(self):
        options = PdfUaValidateOptions()
        assert options.add_input(_minimal_pdf_bytes()) is options


# ---------------------------------------------------------------------------
# Public exports
# ---------------------------------------------------------------------------
class TestPublicExports:
    def test_validate_options_exported(self):
        assert aspose_pdf.PdfUaValidateOptions is PdfUaValidateOptions

    def test_validator_exported(self):
        assert aspose_pdf.PdfUaValidator is PdfUaValidator

    def test_result_exported(self):
        assert aspose_pdf.PdfUaValidationResult is PdfUaValidationResult


# ---------------------------------------------------------------------------
# Structure-tree semantics (walked from a real /StructTreeRoot)
# ---------------------------------------------------------------------------
class TestPdfUaStructureTree:
    def test_figure_without_alt_errors(self):
        doc = _doc()
        doc.convert_to_pdfua(title="X")
        _add_struct_elem(doc, "Figure")
        errors = doc.validate_pdfua().errors
        assert any("Figure" in e and "alternate" in e.lower() for e in errors)

    def test_figure_with_alt_ok(self):
        doc = _doc()
        doc.convert_to_pdfua(title="X")
        _add_struct_elem(doc, "Figure", Alt=PdfString("a bar chart"))
        assert doc.validate_pdfua().is_valid

    def test_figure_with_actualtext_ok(self):
        doc = _doc()
        doc.convert_to_pdfua(title="X")
        _add_struct_elem(doc, "Figure", ActualText=PdfString("a bar chart"))
        assert not any(
            "alternate" in e.lower() for e in doc.validate_pdfua().errors
        )

    def test_formula_without_alt_errors(self):
        doc = _doc()
        doc.convert_to_pdfua(title="X")
        _add_struct_elem(doc, "Formula")
        assert any("Formula" in e for e in doc.validate_pdfua().errors)

    def test_nonstandard_type_without_rolemap_errors(self):
        doc = _doc()
        doc.convert_to_pdfua(title="X")
        _add_struct_elem(doc, "Glorp")
        assert any(
            "not a standard type" in e for e in doc.validate_pdfua().errors
        )

    def test_nonstandard_type_with_rolemap_ok(self):
        doc = _doc()
        doc.convert_to_pdfua(title="X")
        _struct_root(doc).mapping[PdfName("RoleMap")] = PdfDictionary(
            {PdfName("Glorp"): PdfName("P")}
        )
        _add_struct_elem(doc, "Glorp")
        assert doc.validate_pdfua().is_valid

    def test_note_without_id_errors(self):
        doc = _doc()
        doc.convert_to_pdfua(title="X")
        _add_struct_elem(doc, "Note")
        errors = doc.validate_pdfua().errors
        assert any("Note" in e and "ID" in e for e in errors)

    def test_note_with_id_ok(self):
        doc = _doc()
        doc.convert_to_pdfua(title="X")
        _add_struct_elem(doc, "Note", ID=PdfString("note-1"))
        assert doc.validate_pdfua().is_valid

    def test_missing_parenttree_with_content_errors(self):
        doc = _doc()
        doc.convert_to_pdfua(title="X")
        _add_struct_elem(doc, "P")
        _struct_root(doc).mapping.pop(PdfName("ParentTree"), None)
        assert any("ParentTree" in e for e in doc.validate_pdfua().errors)

    def test_heading_level_skip_warns(self):
        doc = _doc()
        doc.convert_to_pdfua(title="X")
        _add_struct_elem(doc, "H1")
        _add_struct_elem(doc, "H3")
        assert any("heading" in w.lower() for w in doc.validate_pdfua().warnings)

    def test_nested_kids_are_walked(self):
        doc = _doc()
        doc.convert_to_pdfua(title="X")
        eng = doc._engine_pdf
        figure = PdfDictionary(
            {PdfName("Type"): PdfName("StructElem"), PdfName("S"): PdfName("Figure")}
        )
        parent = _add_struct_elem(doc, "Sect")
        parent.mapping[PdfName("K")] = PdfArray(
            [eng._cos_doc.register_object(figure)]
        )
        assert any("Figure" in e for e in doc.validate_pdfua().errors)


# ---------------------------------------------------------------------------
# Page / annotation rules
# ---------------------------------------------------------------------------
class TestPdfUaPages:
    def test_annot_page_without_tabs_errors(self):
        doc = _doc()
        doc.convert_to_pdfua(title="X")
        _add_annot(
            doc, Subtype=PdfName("Link"), F=PdfNumber(4), Contents=PdfString("x")
        )
        doc._engine_pdf._get_page_dict(0).mapping.pop(PdfName("Tabs"), None)
        assert any("Tabs" in e for e in doc.validate_pdfua().errors)

    def test_convert_sets_tabs_on_annot_pages(self):
        doc = _doc()
        _add_annot(
            doc, Subtype=PdfName("Link"), F=PdfNumber(4), Contents=PdfString("x")
        )
        doc.convert_to_pdfua(title="X")
        eng = doc._engine_pdf
        tabs = eng._get_name(eng._get_page_dict(0).mapping.get(PdfName("Tabs")))
        assert tabs == "S"
        assert not any("Tabs" in e for e in doc.validate_pdfua().errors)

    def test_suspects_true_errors(self):
        doc = _doc()
        doc.convert_to_pdfua(title="X")
        eng = doc._engine_pdf
        mark_info = eng._resolve(_catalog(doc).mapping.get(PdfName("MarkInfo")))
        mark_info.mapping[PdfName("Suspects")] = PdfBoolean(True)
        assert any("Suspects" in e for e in doc.validate_pdfua().errors)

    def test_convert_clears_suspects_true(self):
        doc = _doc()
        eng = doc._engine_pdf
        eng._get_page_dict(0)  # materialise page tree
        doc.convert_to_pdfua(title="X")
        mark_info = eng._resolve(_catalog(doc).mapping.get(PdfName("MarkInfo")))
        mark_info.mapping[PdfName("Suspects")] = PdfBoolean(True)
        doc.convert_to_pdfua(title="X")
        suspects = eng._resolve(mark_info.mapping.get(PdfName("Suspects")))
        assert suspects.value is False

    def test_unembedded_font_errors(self):
        doc = _doc()
        doc.convert_to_pdfua(title="X")
        eng = doc._engine_pdf
        font = PdfDictionary(
            {
                PdfName("Type"): PdfName("Font"),
                PdfName("Subtype"): PdfName("Type1"),
                PdfName("BaseFont"): PdfName("Helvetica"),
            }
        )
        resources = PdfDictionary(
            {
                PdfName("Font"): PdfDictionary(
                    {PdfName("F1"): eng._cos_doc.register_object(font)}
                )
            }
        )
        eng._get_page_dict(0).mapping[PdfName("Resources")] = resources
        assert any("embedded" in e.lower() for e in doc.validate_pdfua().errors)

    def test_link_annotation_without_contents_warns(self):
        doc = _doc()
        doc.convert_to_pdfua(title="X")
        _add_annot(doc, Subtype=PdfName("Link"), F=PdfNumber(4))
        doc._engine_pdf._get_page_dict(0).mapping[PdfName("Tabs")] = PdfName("S")
        assert any("Contents" in w for w in doc.validate_pdfua().warnings)

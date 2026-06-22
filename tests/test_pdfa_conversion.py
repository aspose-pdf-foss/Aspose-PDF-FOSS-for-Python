"""Tests for Feature 4: convert_to_pdfa() — PDF/A conversion."""

from __future__ import annotations

import io
import pytest

from aspose_pdf.document import Document
from aspose_pdf.engine.simple_pdf import (
    SimplePdf,
    _minimal_srgb_icc_profile,
    _make_pdfa_xmp,
)
from aspose_pdf.engine.cos import (
    PdfName,
    PdfDictionary,
    PdfArray,
    PdfString,
    PdfStream,
)
from aspose_pdf.exceptions import AsposePdfException


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_pdf_bytes() -> bytes:
    """Return a minimal but fully parseable PDF (with proper xref)."""
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


def _load_simple_pdf() -> SimplePdf:
    """Load the minimal PDF into a SimplePdf with a live _cos_doc."""
    return SimplePdf.from_bytes(_minimal_pdf_bytes())


def _get_catalog(pdf: SimplePdf) -> PdfDictionary:
    root = pdf._resolve(pdf._cos_doc.trailer.get(PdfName("Root")))
    assert isinstance(root, PdfDictionary), "Catalog not found"
    return root


def _inject_javascript(pdf: SimplePdf) -> None:
    """Add a /Names tree with a /JavaScript entry to the catalog."""
    catalog = _get_catalog(pdf)
    js_dict = PdfDictionary({PdfName("Names"): PdfArray([])})
    names_dict = PdfDictionary({PdfName("JavaScript"): js_dict})
    names_ref = pdf._cos_doc.register_object(names_dict)
    catalog.mapping[PdfName("Names")] = names_ref


def _inject_openaction(pdf: SimplePdf) -> None:
    """Add an /OpenAction entry to the catalog."""
    catalog = _get_catalog(pdf)
    action = PdfDictionary(
        {
            PdfName("S"): PdfName("JavaScript"),
            PdfName("JS"): PdfString("app.alert('hi')"),
        }
    )
    action_ref = pdf._cos_doc.register_object(action)
    catalog.mapping[PdfName("OpenAction")] = action_ref


# ---------------------------------------------------------------------------
# Unit tests — _minimal_srgb_icc_profile()
# ---------------------------------------------------------------------------


class TestMinimalSrgbIccProfile:
    def test_returns_bytes(self):
        profile = _minimal_srgb_icc_profile()
        assert isinstance(profile, bytes)

    def test_starts_with_correct_profile_size(self):
        import struct

        profile = _minimal_srgb_icc_profile()
        size = struct.unpack(">I", profile[:4])[0]
        assert size == len(profile)

    def test_header_is_128_bytes(self):
        profile = _minimal_srgb_icc_profile()
        assert len(profile) >= 128

    def test_version_is_2_1(self):
        profile = _minimal_srgb_icc_profile()
        assert profile[8:12] == b"\x02\x10\x00\x00"

    def test_color_space_is_rgb(self):
        profile = _minimal_srgb_icc_profile()
        assert profile[16:20] == b"RGB "

    def test_contains_acsp_signature(self):
        profile = _minimal_srgb_icc_profile()
        assert profile[36:40] == b"acsp"

    def test_has_nine_tags(self):
        import struct

        profile = _minimal_srgb_icc_profile()
        tag_count = struct.unpack(">I", profile[128:132])[0]
        assert tag_count == 9


# ---------------------------------------------------------------------------
# Unit tests — _make_pdfa_xmp()
# ---------------------------------------------------------------------------


class TestMakePdfaXmp:
    def test_returns_bytes(self):
        assert isinstance(_make_pdfa_xmp("1b", "Test"), bytes)

    def test_contains_pdfaid_part_1b(self):
        xmp = _make_pdfa_xmp("1b", "My Doc").decode("utf-8")
        assert "<pdfaid:part>1</pdfaid:part>" in xmp
        assert "<pdfaid:conformance>B</pdfaid:conformance>" in xmp

    def test_contains_pdfaid_part_2b(self):
        xmp = _make_pdfa_xmp("2b", "Doc").decode("utf-8")
        assert "<pdfaid:part>2</pdfaid:part>" in xmp
        assert "<pdfaid:conformance>B</pdfaid:conformance>" in xmp

    def test_contains_pdfaid_part_3b(self):
        xmp = _make_pdfa_xmp("3b", "Doc").decode("utf-8")
        assert "<pdfaid:part>3</pdfaid:part>" in xmp

    def test_contains_title(self):
        xmp = _make_pdfa_xmp("1b", "Hello World").decode("utf-8")
        assert "Hello World" in xmp

    def test_escapes_special_chars_in_title(self):
        xmp = _make_pdfa_xmp("1b", "A & B <C>").decode("utf-8")
        assert "&amp;" in xmp
        assert "&lt;" in xmp
        assert "&gt;" in xmp

    def test_starts_with_xpacket(self):
        xmp = _make_pdfa_xmp("1b", "T").decode("utf-8")
        assert xmp.startswith("<?xpacket")

    def test_ends_with_xpacket(self):
        xmp = _make_pdfa_xmp("1b", "T").decode("utf-8")
        assert xmp.endswith('<?xpacket end="w"?>')

    def test_unknown_level_defaults_to_1b(self):
        xmp = _make_pdfa_xmp("99z", "Doc").decode("utf-8")
        assert "<pdfaid:part>1</pdfaid:part>" in xmp


# ---------------------------------------------------------------------------
# Engine tests — SimplePdf.convert_to_pdfa()
# ---------------------------------------------------------------------------


class TestSimplePdfConvertToPdfa:
    # --- Precondition guards ---

    def test_raises_on_no_cos_doc(self):
        pdf = SimplePdf()
        pdf.pages = [(0, 0, 612, 792)]
        with pytest.raises(AsposePdfException, match="from_file|from_bytes"):
            pdf.convert_to_pdfa("1b")

    def test_raises_on_encrypted_document(self):
        pdf = _load_simple_pdf()
        pdf.encrypted = True
        with pytest.raises(AsposePdfException, match="[Ee]ncrypt"):
            pdf.convert_to_pdfa("1b")

    def test_raises_on_disposed_document(self):
        pdf = _load_simple_pdf()
        pdf.dispose()
        with pytest.raises(AsposePdfException):
            pdf.convert_to_pdfa("1b")

    # --- OutputIntents ---

    def test_outputintents_error_is_removed(self):
        pdf = _load_simple_pdf()
        before = pdf.check_pdfa_compliance("1b")
        assert any("OutputIntents" in e for e in before), (
            "Expected OutputIntents error before conversion"
        )
        pdf.convert_to_pdfa("1b")
        after = pdf.check_pdfa_compliance("1b")
        assert not any("OutputIntents" in e for e in after), (
            f"OutputIntents error persists: {after}"
        )

    def test_outputintents_array_added_to_catalog(self):
        pdf = _load_simple_pdf()
        pdf.convert_to_pdfa("1b")
        catalog = _get_catalog(pdf)
        oi = pdf._resolve(catalog.mapping.get(PdfName("OutputIntents")))
        assert isinstance(oi, PdfArray) and len(oi.items) > 0

    def test_outputintent_has_srgb_identifier(self):
        pdf = _load_simple_pdf()
        pdf.convert_to_pdfa("1b")
        catalog = _get_catalog(pdf)
        oi_array = pdf._resolve(catalog.mapping.get(PdfName("OutputIntents")))
        intent = pdf._resolve(oi_array.items[0])
        assert isinstance(intent, PdfDictionary)
        ident = pdf._resolve(intent.mapping.get(PdfName("OutputConditionIdentifier")))
        assert isinstance(ident, PdfString)
        val = (
            ident.value.decode("latin-1")
            if isinstance(ident.value, bytes)
            else ident.value
        )
        assert "sRGB" in val

    def test_outputintent_has_icc_profile(self):
        pdf = _load_simple_pdf()
        pdf.convert_to_pdfa("1b")
        catalog = _get_catalog(pdf)
        oi_array = pdf._resolve(catalog.mapping.get(PdfName("OutputIntents")))
        intent = pdf._resolve(oi_array.items[0])
        icc_stream = pdf._resolve(intent.mapping.get(PdfName("DestOutputProfile")))
        assert isinstance(icc_stream, PdfStream)
        assert len(icc_stream.content) > 100

    # --- XMP metadata ---

    def test_xmp_stream_added_to_catalog(self):
        pdf = _load_simple_pdf()
        pdf.convert_to_pdfa("1b")
        catalog = _get_catalog(pdf)
        meta = pdf._resolve(catalog.mapping.get(PdfName("Metadata")))
        assert isinstance(meta, PdfStream)

    def test_xmp_stream_has_correct_subtype(self):
        pdf = _load_simple_pdf()
        pdf.convert_to_pdfa("1b")
        catalog = _get_catalog(pdf)
        meta = pdf._resolve(catalog.mapping.get(PdfName("Metadata")))
        subtype = pdf._resolve(meta.mapping.get(PdfName("Subtype")))
        assert isinstance(subtype, PdfName) and subtype.name.lstrip("/") == "XML"

    def test_xmp_content_declares_pdfa_level(self):
        pdf = _load_simple_pdf()
        pdf.convert_to_pdfa("2b")
        catalog = _get_catalog(pdf)
        meta = pdf._resolve(catalog.mapping.get(PdfName("Metadata")))
        xmp_text = meta.content.decode("utf-8", errors="replace")
        assert "<pdfaid:part>2</pdfaid:part>" in xmp_text

    # --- /Info Title ---

    def test_title_set_in_info_when_missing(self):
        pdf = _load_simple_pdf()
        # Ensure no title in metadata
        pdf.metadata.pop("Title", None)
        pdf.convert_to_pdfa("1b")
        # Title must now be present in the COS /Info dict
        info_ref = pdf._cos_doc.trailer.get(PdfName("Info"))
        info_dict = pdf._resolve(info_ref)
        assert isinstance(info_dict, PdfDictionary)
        title_obj = pdf._resolve(info_dict.mapping.get(PdfName("Title")))
        assert isinstance(title_obj, PdfString) and title_obj.value

    def test_existing_title_is_preserved(self):
        pdf = _load_simple_pdf()
        pdf.metadata["Title"] = "Existing Title"
        # Ensure COS /Info exists with the title
        info_dict = PdfDictionary({PdfName("Title"): PdfString("Existing Title")})
        info_ref = pdf._cos_doc.register_object(info_dict)
        pdf._cos_doc.trailer.mapping[PdfName("Info")] = info_ref
        pdf.convert_to_pdfa("1b")
        xmp = pdf._resolve(
            _get_catalog(pdf).mapping.get(PdfName("Metadata"))
        ).content.decode("utf-8", errors="replace")
        assert "Existing Title" in xmp

    def test_title_error_removed_after_conversion(self):
        pdf = _load_simple_pdf()
        pdf.metadata.clear()
        before = pdf.check_pdfa_compliance("1b")
        assert any("Title" in e for e in before)
        pdf.convert_to_pdfa("1b")
        after = pdf.check_pdfa_compliance("1b")
        assert not any("Title" in e for e in after), f"Title error persists: {after}"

    # --- Prohibited content removal ---

    def test_javascript_removed(self):
        pdf = _load_simple_pdf()
        _inject_javascript(pdf)
        before = pdf.check_pdfa_compliance("1b")
        assert any("JavaScript" in e for e in before)
        pdf.convert_to_pdfa("1b")
        after = pdf.check_pdfa_compliance("1b")
        assert not any("JavaScript" in e for e in after), f"JS error persists: {after}"

    def test_openaction_removed(self):
        pdf = _load_simple_pdf()
        _inject_openaction(pdf)
        before = pdf.check_pdfa_compliance("1b")
        assert any("OpenAction" in e for e in before)
        pdf.convert_to_pdfa("1b")
        after = pdf.check_pdfa_compliance("1b")
        assert not any("OpenAction" in e for e in after), (
            f"OpenAction error persists: {after}"
        )

    def test_embedded_files_removed_for_1b(self):
        pdf = _load_simple_pdf()
        pdf.attachments["report.pdf"] = b"data"
        catalog = _get_catalog(pdf)
        ef_dict = PdfDictionary({PdfName("Names"): PdfArray([])})
        names_dict = PdfDictionary({PdfName("EmbeddedFiles"): ef_dict})
        names_ref = pdf._cos_doc.register_object(names_dict)
        catalog.mapping[PdfName("Names")] = names_ref
        pdf.convert_to_pdfa("1b")
        assert len(pdf.attachments) == 0
        names_obj = pdf._resolve(catalog.mapping.get(PdfName("Names")))
        if isinstance(names_obj, PdfDictionary):
            assert PdfName("EmbeddedFiles") not in names_obj.mapping

    def test_embedded_files_kept_for_3b(self):
        pdf = _load_simple_pdf()
        pdf.attachments["doc.pdf"] = b"data"
        catalog = _get_catalog(pdf)
        ef_dict = PdfDictionary({PdfName("Names"): PdfArray([])})
        names_dict = PdfDictionary({PdfName("EmbeddedFiles"): ef_dict})
        names_ref = pdf._cos_doc.register_object(names_dict)
        catalog.mapping[PdfName("Names")] = names_ref
        pdf.convert_to_pdfa("3b")
        # For PDF/A-3 embedded files are allowed
        names_obj = pdf._resolve(catalog.mapping.get(PdfName("Names")))
        if isinstance(names_obj, PdfDictionary):
            assert PdfName("EmbeddedFiles") in names_obj.mapping

    # --- Return value ---

    def test_returns_list(self):
        pdf = _load_simple_pdf()
        result = pdf.convert_to_pdfa("1b")
        assert isinstance(result, list)

    def test_returns_empty_list_for_clean_document(self):
        """A minimal PDF with no fonts and no prohibited content should be fully
        compliant after conversion (no remaining issues)."""
        pdf = _load_simple_pdf()
        pdf.metadata.pop("Title", None)
        remaining = pdf.convert_to_pdfa("1b")
        assert remaining == [], f"Expected no remaining issues, got: {remaining}"

    # --- Idempotency ---

    def test_convert_twice_is_idempotent(self):
        pdf = _load_simple_pdf()
        pdf.convert_to_pdfa("1b")
        issues_first = pdf.check_pdfa_compliance("1b")
        pdf.convert_to_pdfa("1b")
        issues_second = pdf.check_pdfa_compliance("1b")
        assert issues_first == issues_second

    # --- Serialisation roundtrip ---

    def test_saved_bytes_start_with_pdf_header(self):
        pdf = _load_simple_pdf()
        pdf.convert_to_pdfa("1b")
        data = pdf.to_bytes()
        assert data.startswith(b"%PDF-")

    def test_saved_bytes_contain_outputintents(self):
        pdf = _load_simple_pdf()
        pdf.convert_to_pdfa("1b")
        data = pdf.to_bytes()
        assert b"OutputIntents" in data or b"/OutputIntent" in data

    def test_saved_bytes_contain_xmp(self):
        pdf = _load_simple_pdf()
        pdf.convert_to_pdfa("1b")
        data = pdf.to_bytes()
        assert b"pdfaid" in data or b"xmpmeta" in data

    def test_converted_pdf_can_be_reloaded(self):
        pdf = _load_simple_pdf()
        pdf.convert_to_pdfa("1b")
        data = pdf.to_bytes()
        reloaded = SimplePdf.from_bytes(data)
        assert len(reloaded.pages) >= 1


# ---------------------------------------------------------------------------
# Public API tests — Document.convert_to_pdfa()
# ---------------------------------------------------------------------------


class TestDocumentConvertToPdfa:
    def test_method_exists(self):
        doc = Document()
        assert hasattr(doc, "convert_to_pdfa")

    def test_raises_on_disposed(self):
        doc = Document()
        doc.load_from(_minimal_pdf_bytes())
        doc.dispose()
        with pytest.raises(AsposePdfException):
            doc.convert_to_pdfa("1b")

    def test_raises_when_no_document_loaded(self):
        doc = Document()
        # _engine_pdf is a fresh SimplePdf() with no _cos_doc
        with pytest.raises(AsposePdfException):
            doc.convert_to_pdfa("1b")

    def test_returns_list(self):
        doc = Document()
        doc.load_from(_minimal_pdf_bytes())
        result = doc.convert_to_pdfa("1b")
        assert isinstance(result, list)

    def test_reduces_validation_errors(self):
        doc = Document()
        doc.load_from(_minimal_pdf_bytes())
        before = doc.validate_pdfa("1b")
        doc.convert_to_pdfa("1b")
        after = doc.validate_pdfa("1b")
        assert len(after) < len(before), (
            f"Expected fewer issues after conversion. Before: {before}, After: {after}"
        )

    def test_document_is_pdfa_compliant_after_conversion(self):
        doc = Document()
        doc.load_from(_minimal_pdf_bytes())
        doc.convert_to_pdfa("1b")
        assert doc.is_pdfa_compliant("1b"), (
            f"Document not compliant after conversion: {doc.validate_pdfa('1b')}"
        )

    def test_save_and_reload_preserves_pdfa_structure(self):
        """After conversion, save to BytesIO and reload; the saved bytes should
        contain OutputIntents and XMP metadata markers."""
        doc = Document()
        doc.load_from(_minimal_pdf_bytes())
        doc.convert_to_pdfa("1b")

        buf = io.BytesIO()
        doc.save(buf)
        buf.seek(0)
        data = buf.read()

        assert b"OutputIntents" in data or b"/OutputIntent" in data
        assert b"pdfaid" in data or b"xmpmeta" in data

    def test_convert_2b_level(self):
        doc = Document()
        doc.load_from(_minimal_pdf_bytes())
        result = doc.convert_to_pdfa("2b")
        assert isinstance(result, list)

    def test_convert_3b_level(self):
        doc = Document()
        doc.load_from(_minimal_pdf_bytes())
        result = doc.convert_to_pdfa("3b")
        assert isinstance(result, list)

    def test_convert_uppercase_level(self):
        """Level string should be case-insensitive."""
        doc = Document()
        doc.load_from(_minimal_pdf_bytes())
        result = doc.convert_to_pdfa("1B")
        assert isinstance(result, list)

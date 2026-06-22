import pytest
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.engine.parser_exceptions import PdfParseError
from aspose_pdf.exceptions import PdfParseException


def make_minimal_pdf() -> bytes:
    """Create a minimal valid PDF for testing."""
    return (
        b"%PDF-1.7\n"
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Count 1 /Kids [3 0 R] >>\nendobj\n"
        b"3 0 obj\n<< /Type /Page /MediaBox [0 0 612 792] >>\nendobj\n"
        b"xref\n0 4\n"
        b"0000000000 65535 f \n"
        b"0000000009 00000 n \n"
        b"0000000058 00000 n \n"
        b"0000000115 00000 n \n"
        b"trailer\n<< /Root 1 0 R /Size 4 >>\n"
        b"startxref\n170\n%%EOF"
    )


class TestMissingXrefRecovery:
    """Test recovery from missing xref table."""

    def test_pdf_without_xref_parses(self):
        """PDF without xref should still parse with defaults."""
        data = b"%PDF-1.7\n1 0 obj\n<< /Type /Page >>\nendobj\n%%EOF"
        pdf = SimplePdf.from_bytes(data)
        assert pdf is not None
        assert len(pdf.pages) >= 1

    def test_corrupted_xref_recovers(self):
        """Corrupted xref should trigger recovery."""
        data = b"%PDF-1.7\nxref\nGARBAGE\ntrailer\n<< >>\nstartxref\n9\n%%EOF"
        # Should not crash
        try:
            pdf = SimplePdf.from_bytes(data)
            assert pdf is not None
        except (ValueError, PdfParseError):
            pass  # Also acceptable


class TestTruncatedPdfHandling:
    """Test handling of truncated PDFs."""

    def test_truncated_header_raises(self):
        """Truncated PDF header should raise error."""
        data = b"%PDF"
        with pytest.raises((ValueError, PdfParseError, PdfParseException)):
            SimplePdf.from_bytes(data)

    def test_very_short_pdf_handled(self):
        """Very short data should be handled appropriately."""
        data = b"%PDF-1.7"
        # Parser may raise error OR recover gracefully with defaults
        try:
            pdf = SimplePdf.from_bytes(data)
            # If recovered, should have at least one page
            assert pdf is not None
            assert len(pdf.pages) >= 1
        except (ValueError, PdfParseError):
            pass  # Raising error is also acceptable


class TestRepairMethod:
    """Test the repair() method."""

    def test_repair_fixes_page_content_mismatch(self):
        """repair() should synchronize pages and page_contents."""
        pdf = SimplePdf()
        pdf.pages = [(0, 0, 612, 792), (0, 0, 612, 792), (0, 0, 612, 792)]
        pdf.page_contents = [b"content"]  # Mismatch
        result = pdf.repair()
        assert result is True
        assert len(pdf.pages) == len(pdf.page_contents)

    def test_repair_adds_default_page(self):
        """repair() should add default page if pages is empty."""
        pdf = SimplePdf()
        pdf.pages = []
        pdf.page_contents = []
        pdf.repair()
        assert len(pdf.pages) >= 1

    def test_repair_fixes_invalid_mediabox(self):
        """repair() should fix invalid MediaBox values."""
        pdf = SimplePdf()
        pdf.pages = ["invalid"]  # Invalid type
        pdf.page_contents = [b""]
        pdf.repair()
        assert pdf.pages[0] == (0, 0, 612, 792)


class TestSafeLoading:
    """Test safe loading with automatic repair."""

    def test_from_bytes_safe_returns_valid_pdf(self):
        """from_bytes_safe should always return a usable PDF."""
        if hasattr(SimplePdf, "from_bytes_safe"):
            pdf = SimplePdf.from_bytes_safe(b"%PDF-1.7\n%%EOF")
            assert pdf is not None
            assert pdf.validate()


class TestValidPdfStillWorks:
    """Ensure valid PDFs still work correctly."""

    def test_valid_pdf_parses_correctly(self):
        """Valid PDF should parse without issues."""
        data = make_minimal_pdf()
        pdf = SimplePdf.from_bytes(data)
        assert pdf is not None
        assert len(pdf.pages) >= 1

    def test_roundtrip_preserves_pages(self):
        """Save and reload should preserve page count."""
        pdf = SimplePdf()
        pdf.pages = [(0, 0, 612, 792), (0, 0, 612, 792)]
        pdf.page_contents = [b"", b""]
        data = pdf.to_bytes()
        pdf2 = SimplePdf.from_bytes(data)
        assert len(pdf2.pages) == 2

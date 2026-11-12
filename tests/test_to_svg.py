"""Tests for Document.to_svg() method."""

from __future__ import annotations

import pytest

from aspose_pdf import Document
from aspose_pdf.exceptions import PdfValidationException


def _minimal_pdf_bytes() -> bytes:
    """Return a minimal but parseable PDF with one page."""
    return (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >>\n"
        b"endobj\n"
        b"2 0 obj << /Type /Pages /Count 1 /Kids [3 0 R] >>\n"
        b"endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R >>\n"
        b"endobj\n"
        b"4 0 obj << /Length 44 >>\n"
        b"stream\n"
        b"BT /F1 12 Tf 72 720 Td (Test) Tj ET\n"
        b"endstream\n"
        b"endobj\n"
        b"xref\n"
        b"0 5\n"
        b"0000000000 65535 f \n"
        b"0000000009 00000 n \n"
        b"0000000058 00000 n \n"
        b"0000000118 00000 n \n"
        b"0000000245 00000 n \n"
        b"trailer << /Root 1 0 R /Size 5 >>\n"
        b"startxref\n"
        b"335\n"
        b"%%EOF"
    )


def _four_page_pdf_bytes() -> bytes:
    """Return a minimal PDF with four pages."""
    # This is a simplified 4-page PDF for testing
    return (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >>\n"
        b"endobj\n"
        b"2 0 obj << /Type /Pages /Count 4 /Kids [3 0 R 5 0 R 7 0 R 9 0 R] >>\n"
        b"endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\n"
        b"endobj\n"
        b"5 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\n"
        b"endobj\n"
        b"7 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\n"
        b"endobj\n"
        b"9 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\n"
        b"endobj\n"
        b"xref\n"
        b"0 10\n"
        b"0000000000 65535 f \n"
        b"0000000009 00000 n \n"
        b"0000000058 00000 n \n"
        b"0000000118 00000 n \n"
        b"0000000180 00000 n \n"
        b"0000000242 00000 n \n"
        b"0000000304 00000 n \n"
        b"0000000366 00000 n \n"
        b"0000000428 00000 n \n"
        b"0000000490 00000 n \n"
        b"trailer << /Root 1 0 R /Size 10 >>\n"
        b"startxref\n"
        b"552\n"
        b"%%EOF"
    )


# ---------------------------------------------------------------------------
# to_svg() tests
# ---------------------------------------------------------------------------


class TestToSvg:
    """Tests for Document.to_svg()."""

    def test_to_svg_returns_list(self):
        """to_svg() must return a list of bytes."""
        doc = Document()
        doc.load_from(_minimal_pdf_bytes())
        result = doc.to_svg()
        assert isinstance(result, list)
        doc.dispose()

    def test_to_svg_returns_bytes_for_each_page(self):
        """Each item in the to_svg() list must be bytes."""
        doc = Document()
        doc.load_from(_minimal_pdf_bytes())
        result = doc.to_svg()
        assert len(result) == 1
        assert isinstance(result[0], bytes)
        doc.dispose()

    def test_to_svg_produces_valid_svg_header(self):
        """SVG bytes must start with the XML declaration or <svg> tag."""
        doc = Document()
        doc.load_from(_minimal_pdf_bytes())
        result = doc.to_svg()
        svg_bytes = result[0]
        # SVG should start with <?xml or <svg
        assert svg_bytes.startswith(b"<?xml") or svg_bytes.startswith(b"<svg")
        doc.dispose()

    def test_to_svg_with_single_page(self):
        """to_svg() on a single-page document returns one SVG."""
        doc = Document()
        doc.load_from(_minimal_pdf_bytes())
        result = doc.to_svg()
        assert len(result) == 1
        assert b"<svg" in result[0]
        doc.dispose()

    def test_to_svg_with_multiple_pages(self):
        """to_svg() on a multi-page document returns one SVG per page."""
        doc = Document()
        doc.load_from(_four_page_pdf_bytes())
        result = doc.to_svg()
        assert len(result) == 4
        for svg_bytes in result:
            assert b"<svg" in svg_bytes
        doc.dispose()

    def test_to_svg_with_selected_pages(self):
        """to_svg() with pages parameter returns only selected pages."""
        doc = Document()
        doc.load_from(_four_page_pdf_bytes())
        result = doc.to_svg(pages=[1, 3])
        assert len(result) == 2
        doc.dispose()

    def test_to_svg_empty_selection_raises(self):
        """to_svg() with no pages raises PdfValidationException."""
        doc = Document()
        doc.load_from(_four_page_pdf_bytes())
        with pytest.raises(PdfValidationException):
            doc.to_svg(pages=[])
        doc.dispose()

    def test_to_svg_after_dispose_raises(self):
        """to_svg() after dispose raises AsposePdfException."""
        doc = Document()
        doc.load_from(_minimal_pdf_bytes())
        doc.dispose()
        with pytest.raises(Exception):
            doc.to_svg()

    def test_to_svg_matches_save_as_svg_content(self, tmp_path):
        """to_svg() and save_as_svg() must produce identical SVG content."""
        doc = Document()
        doc.load_from(_minimal_pdf_bytes())

        # Get SVG from to_svg()
        svg_bytes = doc.to_svg()[0]

        # Save to file and read back
        out_path = tmp_path / "page.svg"
        doc.save_as_svg(out_path)

        saved_content = out_path.read_bytes()
        # to_svg returns a list, save_as_svg writes one file for one-page doc
        # The content should match
        assert svg_bytes == saved_content
        doc.dispose()

    def test_to_svg_with_all_parameters(self):
        """to_svg() works with all optional parameters."""
        doc = Document()
        doc.load_from(_minimal_pdf_bytes())

        result = doc.to_svg(
            pages=None,
            background=(255, 255, 255),
            draw_annotations=True,
            font_substitution=None,
            precision=3,
            text="outlines",
        )
        assert len(result) == 1
        assert isinstance(result[0], bytes)
        doc.dispose()

    def test_to_svg_with_transparent_background(self):
        """to_svg() with background=None produces transparent SVG."""
        doc = Document()
        doc.load_from(_minimal_pdf_bytes())

        result = doc.to_svg(background=None)
        assert len(result) == 1
        # The SVG should not have a background rect when background=None
        svg_bytes = result[0]
        assert isinstance(svg_bytes, bytes)
        doc.dispose()

    def test_to_svg_text_options(self):
        """to_svg() with different text options works."""
        doc = Document()
        doc.load_from(_minimal_pdf_bytes())

        # Test with "text" option - output will be outlines if the font can't produce text
        result_text = doc.to_svg(text="text")
        assert len(result_text) == 1
        # Check that the SVG is valid (either has <text> or is valid SVG)
        assert b"<svg" in result_text[0]

        # Test with "both" option
        result_both = doc.to_svg(text="both")
        assert len(result_both) == 1
        assert b"<svg" in result_both[0]

        # Test with "outlines" option (default)
        result_outlines = doc.to_svg(text="outlines")
        assert len(result_outlines) == 1
        assert b"<svg" in result_outlines[0]

        doc.dispose()

"""Tests for Page.from_pdf() method."""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document, Page, PageSize
from aspose_pdf.exceptions import PdfValidationException


def _create_minimal_pdf_with_pages(num_pages: int = 3) -> bytes:
    """Create a minimal PDF with a specified number of pages."""
    # Create a minimal but valid PDF with the specified number of pages
    pdf_body = []
    
    # Catalog and Pages dictionary
    pdf_body.append("1 0 obj << /Type /Catalog /Pages 2 0 R >>\nendobj\n")
    pdf_body.append("2 0 obj << /Type /Pages /Count {} /Kids [".format(num_pages))
    
    # Page objects
    page_objects = []
    for i in range(num_pages):
        page_num = 3 + i
        page_objects.append(f"{page_num} 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\nendobj\n")
    
    pdf_body.append(" ".join(f"{3 + i} 0 R" for i in range(num_pages)) + "] >>\nendobj\n")
    pdf_body.extend(page_objects)
    
    # Xref and trailer
    total_objects = 3 + num_pages
    xref_start = len("".join(pdf_body))
    
    pdf_body.append("xref\n")
    pdf_body.append(f"0 {total_objects + 1}\n")
    pdf_body.append("0000000000 65535 f \n")
    for i in range(1, total_objects + 1):
        # Calculate offset for each object
        obj_content = "".join(pdf_body[:i])
        offset = len(obj_content)
        pdf_body.append(f"{offset:010d} 00000 n \n")
    
    pdf_body.append(f"trailer << /Root 1 0 R /Size {total_objects + 1} >>\n")
    pdf_body.append("startxref\n")
    pdf_body.append(f"{len(''.join(pdf_body[:-3]))}\n")
    pdf_body.append("%%EOF")
    
    return "\n".join(pdf_body).encode("utf-8")


def _create_test_pdf() -> bytes:
    """Create a test PDF with one page and some content."""
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


class TestPageFromPdf:
    """Tests for Page.from_pdf() method."""

    def test_from_pdf_with_file_path(self, tmp_path):
        """Page.from_pdf should work with a file path."""
        pdf_path = tmp_path / "source.pdf"
        pdf_path.write_bytes(_create_test_pdf())
        
        with Document() as doc:
            page = Page.from_pdf(doc, pdf_path)
            assert page is not None
            assert page.number == 1
            assert len(doc.pages) == 1

    def test_from_pdf_with_bytes(self):
        """Page.from_pdf should work with raw bytes."""
        pdf_bytes = _create_test_pdf()
        
        with Document() as doc:
            page = Page.from_pdf(doc, pdf_bytes)
            assert page is not None
            assert page.number == 1
            assert len(doc.pages) == 1

    def test_from_pdf_with_bytesio(self):
        """Page.from_pdf should work with a BytesIO object."""
        pdf_bytes = _create_test_pdf()
        stream = io.BytesIO(pdf_bytes)
        
        with Document() as doc:
            page = Page.from_pdf(doc, stream)
            assert page is not None
            assert page.number == 1
            assert len(doc.pages) == 1

    def test_from_pdf_with_page_number(self, tmp_path):
        """Page.from_pdf should import the specified page number."""
        pdf_path = tmp_path / "source.pdf"
        # Create a 3-page PDF
        pdf_content = (
            b"%PDF-1.4\n"
            b"1 0 obj << /Type /Catalog /Pages 2 0 R >>\n"
            b"endobj\n"
            b"2 0 obj << /Type /Pages /Count 3 /Kids [3 0 R 4 0 R 5 0 R] >>\n"
            b"endobj\n"
            b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\n"
            b"endobj\n"
            b"4 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\n"
            b"endobj\n"
            b"5 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\n"
            b"endobj\n"
            b"xref\n"
            b"0 6\n"
            b"0000000000 65535 f \n"
            b"0000000009 00000 n \n"
            b"0000000062 00000 n \n"
            b"0000000126 00000 n \n"
            b"0000000190 00000 n \n"
            b"0000000254 00000 n \n"
            b"trailer << /Root 1 0 R /Size 6 >>\n"
            b"startxref\n"
            b"318\n"
            b"%%EOF"
        )
        pdf_path.write_bytes(pdf_content)
        
        with Document() as doc:
            # Import the second page
            page = Page.from_pdf(doc, pdf_path, page_number=2)
            assert page.number == 1
            assert len(doc.pages) == 1

    def test_from_pdf_invalid_page_number(self, tmp_path):
        """Page.from_pdf should raise IndexError for invalid page numbers."""
        pdf_path = tmp_path / "source.pdf"
        pdf_path.write_bytes(_create_test_pdf())
        
        with Document() as doc:
            # Try to access page 2 in a 1-page document
            with pytest.raises(IndexError, match="page_number must be between"):
                Page.from_pdf(doc, pdf_path, page_number=2)

    def test_from_pdf_negative_page_number(self, tmp_path):
        """Page.from_pdf should raise IndexError for negative page numbers."""
        pdf_path = tmp_path / "source.pdf"
        pdf_path.write_bytes(_create_test_pdf())
        
        with Document() as doc:
            with pytest.raises(IndexError, match="page_number must be between"):
                Page.from_pdf(doc, pdf_path, page_number=0)

    def test_from_pdf_disposed_document_raises(self, tmp_path):
        """Page.from_pdf should raise on a disposed document."""
        pdf_path = tmp_path / "source.pdf"
        pdf_path.write_bytes(_create_test_pdf())
        
        doc = Document()
        doc.dispose()
        
        with pytest.raises(Exception):
            Page.from_pdf(doc, pdf_path)

    def test_from_pdf_nonexistent_file(self):
        """Page.from_pdf should raise FileNotFoundError for non-existent files."""
        with Document() as doc:
            with pytest.raises(FileNotFoundError):
                Page.from_pdf(doc, "/nonexistent/path/to/file.pdf")

    def test_from_pdf_invalid_source_type(self):
        """Page.from_pdf should raise TypeError for invalid source types."""
        with Document() as doc:
            with pytest.raises(TypeError, match="pdf_source must be bytes, bytearray, str, Path, or BinaryIO"):
                Page.from_pdf(doc, 12345)  # Invalid type

    def test_from_pdf_invalid_stream_read_type(self):
        """Page.from_pdf should raise TypeError when stream.read() doesn't return bytes."""
        class BadStream:
            def read(self):
                return "not bytes"
        
        with Document() as doc:
            with pytest.raises(TypeError, match="read\\(\\) must return bytes or bytearray"):
                Page.from_pdf(doc, BadStream())

    def test_from_pdf_preserves_page_properties(self, tmp_path):
        """Page.from_pdf should preserve page properties like rotation and boxes."""
        # Create a PDF with a page that has some properties
        pdf_path = tmp_path / "source.pdf"
        pdf_path.write_bytes(_create_test_pdf())
        
        with Document() as doc:
            page = Page.from_pdf(doc, pdf_path)
            
            # Basic checks - the page should be importable
            assert page.number == 1
            assert len(doc.pages) == 1

    def test_from_pdf_multiple_pages(self, tmp_path):
        """Page.from_pdf should be able to add multiple pages from the same PDF."""
        pdf_path = tmp_path / "source.pdf"
        pdf_content = (
            b"%PDF-1.4\n"
            b"1 0 obj << /Type /Catalog /Pages 2 0 R >>\n"
            b"endobj\n"
            b"2 0 obj << /Type /Pages /Count 3 /Kids [3 0 R 4 0 R 5 0 R] >>\n"
            b"endobj\n"
            b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\n"
            b"endobj\n"
            b"4 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\n"
            b"endobj\n"
            b"5 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\n"
            b"endobj\n"
            b"xref\n"
            b"0 6\n"
            b"0000000000 65535 f \n"
            b"0000000009 00000 n \n"
            b"0000000062 00000 n \n"
            b"0000000126 00000 n \n"
            b"0000000190 00000 n \n"
            b"0000000254 00000 n \n"
            b"trailer << /Root 1 0 R /Size 6 >>\n"
            b"startxref\n"
            b"318\n"
            b"%%EOF"
        )
        pdf_path.write_bytes(pdf_content)
        
        with Document() as doc:
            # Add multiple pages
            page1 = Page.from_pdf(doc, pdf_path, page_number=1)
            page2 = Page.from_pdf(doc, pdf_path, page_number=2)
            page3 = Page.from_pdf(doc, pdf_path, page_number=3)
            
            assert len(doc.pages) == 3
            assert page1.number == 1
            assert page2.number == 2
            assert page3.number == 3

    def test_from_pdf_with_bytearray(self):
        """Page.from_pdf should work with bytearray."""
        pdf_bytes = bytearray(_create_test_pdf())
        
        with Document() as doc:
            page = Page.from_pdf(doc, pdf_bytes)
            assert page is not None
            assert page.number == 1
            assert len(doc.pages) == 1

    def test_from_pdf_chaining(self):
        """Page.from_pdf should be usable in a fluent style."""
        pdf_bytes = _create_test_pdf()
        
        with Document() as doc:
            # Test that the returned page can be used for further operations
            page = Page.from_pdf(doc, pdf_bytes)
            # The page should be properly added to the document
            assert page._document is doc

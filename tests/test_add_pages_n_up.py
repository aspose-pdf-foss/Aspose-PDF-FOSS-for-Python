"""Tests for the n_up layout mode in add_pages_from_images and add_pages_from_pdf."""

from __future__ import annotations

import struct
import zlib

import pytest

from aspose_pdf import Document
from aspose_pdf.exceptions import PdfValidationException


def png_chunk(tag: bytes, payload: bytes) -> bytes:
    """Create a PNG chunk with length, tag, payload, and CRC."""
    return (
        struct.pack(">I", len(payload))
        + tag
        + payload
        + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    )


def create_png(width: int, height: int, color: tuple[int, int, int] = (255, 0, 0)) -> bytes:
    """Create a simple PNG image with solid color."""
    raw = bytearray()
    for _ in range(height):
        raw.append(0)
        for _ in range(width):
            raw.extend(color)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = zlib.compress(bytes(raw), 9)

    return b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", ihdr) + png_chunk(b"IDAT", idat) + png_chunk(b"IEND", b"")


def create_pdf_page():
    """Create a minimal PDF with a single page for testing."""
    return (
        b"%PDF-1.7\n"
        b"1 0 obj\n<<\n  /Type /Catalog\n  /Pages 2 0 R\n>>\nendobj\n"
        b"2 0 obj\n<<\n  /Type /Pages\n  /Kids [3 0 R]\n  /Count 1\n>>\nendobj\n"
        b"3 0 obj\n<<\n  /Type /Page\n  /Parent 2 0 R\n  /MediaBox [0 0 612 792]\n"
        b"  /Contents 4 0 R\n  /Resources << >>\n>>\nendobj\n"
        b"4 0 obj\n<<\n  /Length 44\n>>\nstream\nBT /F1 12 Tf 72 720 Td (Test) Tj ET\nendstream\nendobj\n"
        b"xref\n0 5\n0000000000 65535 f \n0000000010 00000 n \n0000000065 00000 n \n"
        b"0000000115 00000 n \n0000000214 00000 n \n"
        b"trailer\n<<\n  /Size 5\n  /Root 1 0 R\n>>\nstartxref\n292\n%%EOF\n"
    )


class TestAddPagesFromImagesNUp:
    """Tests for Document.add_pages_from_images() with layout='n_up'."""

    def test_add_pages_from_images_n_up_2x2_layout(self):
        """add_pages_from_images should arrange images in 2x2 grid when layout='n_up'."""
        doc = Document()

        # Create 4 PNG images
        img1 = create_png(100, 100, (255, 0, 0))  # Red
        img2 = create_png(100, 100, (0, 255, 0))  # Green
        img3 = create_png(100, 100, (0, 0, 255))  # Blue
        img4 = create_png(100, 100, (255, 255, 0))  # Yellow

        # Add with n_up layout - should create 1 page with 4 images
        page_numbers = doc.add_pages_from_images(
            [img1, img2, img3, img4],
            layout="n_up",
            rows=2,
            columns=2,
        )

        # Should return page numbers of the newly added pages
        assert len(page_numbers) == 1  # 4 images on 1 page
        assert doc.page_count == 1

    def test_add_pages_from_images_n_up_1x2_layout(self):
        """add_pages_from_images should arrange 2 images in 1x2 layout."""
        doc = Document()

        img1 = create_png(100, 100, (255, 0, 0))
        img2 = create_png(100, 100, (0, 255, 0))

        page_numbers = doc.add_pages_from_images(
            [img1, img2],
            layout="n_up",
            rows=1,
            columns=2,
        )

        assert len(page_numbers) == 1  # 2 images on 1 page
        assert doc.page_count == 1

    def test_add_pages_from_images_n_up_2x1_layout(self):
        """add_pages_from_images should arrange 2 images in 2x1 layout."""
        doc = Document()

        img1 = create_png(100, 100, (255, 0, 0))
        img2 = create_png(100, 100, (0, 255, 0))

        page_numbers = doc.add_pages_from_images(
            [img1, img2],
            layout="n_up",
            rows=2,
            columns=1,
        )

        assert len(page_numbers) == 1  # 2 images on 1 page
        assert doc.page_count == 1

    def test_add_pages_from_images_n_up_more_images_than_slots(self):
        """add_pages_from_images should create multiple pages when more images than slots."""
        doc = Document()

        # Create 5 images for 2x2 grid (4 slots)
        images = [create_png(100, 100, (i * 50, 0, 0)) for i in range(5)]

        page_numbers = doc.add_pages_from_images(
            images,
            layout="n_up",
            rows=2,
            columns=2,
        )

        # 5 images / 4 slots = 2 pages (last page has 1 image)
        assert len(page_numbers) == 2
        assert doc.page_count == 2

    def test_add_pages_from_images_n_up_custom_margin(self):
        """add_pages_from_images should respect margin parameter."""
        doc = Document()

        img1 = create_png(100, 100, (255, 0, 0))
        img2 = create_png(100, 100, (0, 255, 0))

        page_numbers = doc.add_pages_from_images(
            [img1, img2],
            layout="n_up",
            rows=1,
            columns=2,
            margin=10,
        )

        assert len(page_numbers) == 1
        assert doc.page_count == 1

    def test_add_pages_from_images_n_up_order_column(self):
        """add_pages_from_images should respect order='column' parameter."""
        doc = Document()

        img1 = create_png(100, 100, (255, 0, 0))
        img2 = create_png(100, 100, (0, 255, 0))
        img3 = create_png(100, 100, (0, 0, 255))
        img4 = create_png(100, 100, (255, 255, 0))

        page_numbers = doc.add_pages_from_images(
            [img1, img2, img3, img4],
            layout="n_up",
            rows=2,
            columns=2,
            order="column",
        )

        assert len(page_numbers) == 1
        assert doc.page_count == 1

    def test_add_pages_from_images_n_up_invalid_rows(self):
        """add_pages_from_images should raise for invalid rows."""
        doc = Document()
        img = create_png(100, 100, (255, 0, 0))

        with pytest.raises(PdfValidationException) as exc_info:
            doc.add_pages_from_images([img], layout="n_up", rows=0)

        assert "rows must be a positive integer" in str(exc_info.value)

    def test_add_pages_from_images_n_up_invalid_columns(self):
        """add_pages_from_images should raise for invalid columns."""
        doc = Document()
        img = create_png(100, 100, (255, 0, 0))

        with pytest.raises(PdfValidationException) as exc_info:
            doc.add_pages_from_images([img], layout="n_up", columns=-1)

        assert "columns must be a positive integer" in str(exc_info.value)

    def test_add_pages_from_images_n_up_invalid_margin(self):
        """add_pages_from_images should raise for negative margin."""
        doc = Document()
        img = create_png(100, 100, (255, 0, 0))

        with pytest.raises(PdfValidationException) as exc_info:
            doc.add_pages_from_images([img], layout="n_up", margin=-5)

        assert "margin must be non-negative" in str(exc_info.value)

    def test_add_pages_from_images_n_up_invalid_order(self):
        """add_pages_from_images should raise for invalid order."""
        doc = Document()
        img = create_png(100, 100, (255, 0, 0))

        with pytest.raises(PdfValidationException) as exc_info:
            doc.add_pages_from_images([img], layout="n_up", order="invalid")

        assert "order must be 'row' or 'column'" in str(exc_info.value)

    def test_add_pages_from_images_n_up_boolean_rows_raises(self):
        """add_pages_from_images should raise for boolean rows (not int)."""
        doc = Document()
        img = create_png(100, 100, (255, 0, 0))

        with pytest.raises(PdfValidationException) as exc_info:
            doc.add_pages_from_images([img], layout="n_up", rows=True)

        assert "rows must be a positive integer" in str(exc_info.value)

    def test_add_pages_from_images_n_up_boolean_columns_raises(self):
        """add_pages_from_images should raise for boolean columns (not int)."""
        doc = Document()
        img = create_png(100, 100, (255, 0, 0))

        with pytest.raises(PdfValidationException) as exc_info:
            doc.add_pages_from_images([img], layout="n_up", columns=False)

        assert "columns must be a positive integer" in str(exc_info.value)


class TestAddPagesFromPdfNUp:
    """Tests for Document.add_pages_from_pdf() with layout='n_up'."""

    def test_add_pages_from_pdf_n_up_2x2_layout(self):
        """add_pages_from_pdf should arrange pages in 2x2 grid when layout='n_up'."""
        doc = Document()

        # Create a PDF page
        pdf_page = create_pdf_page()

        # Add 4 pages with n_up layout
        page_numbers = doc.add_pages_from_pdf(
            [pdf_page, pdf_page, pdf_page, pdf_page],
            layout="n_up",
            rows=2,
            columns=2,
        )

        # Should return page numbers of the newly added pages
        assert len(page_numbers) == 1  # 4 pages on 1 page
        assert doc.page_count == 1

    def test_add_pages_from_pdf_n_up_1x2_layout(self):
        """add_pages_from_pdf should arrange 2 pages in 1x2 layout."""
        doc = Document()
        pdf_page = create_pdf_page()

        page_numbers = doc.add_pages_from_pdf(
            [pdf_page, pdf_page],
            layout="n_up",
            rows=1,
            columns=2,
        )

        assert len(page_numbers) == 1
        assert doc.page_count == 1

    def test_add_pages_from_pdf_n_up_more_pages_than_slots(self):
        """add_pages_from_pdf should create multiple pages when more pages than slots."""
        doc = Document()
        pdf_page = create_pdf_page()

        # Create 5 pages for 2x2 grid (4 slots)
        page_numbers = doc.add_pages_from_pdf(
            [pdf_page] * 5,
            layout="n_up",
            rows=2,
            columns=2,
        )

        # 5 pages / 4 slots = 2 pages
        assert len(page_numbers) == 2
        assert doc.page_count == 2

    def test_add_pages_from_pdf_n_up_custom_margin(self):
        """add_pages_from_pdf should respect margin parameter."""
        doc = Document()
        pdf_page = create_pdf_page()

        page_numbers = doc.add_pages_from_pdf(
            [pdf_page, pdf_page],
            layout="n_up",
            rows=1,
            columns=2,
            margin=10,
        )

        assert len(page_numbers) == 1
        assert doc.page_count == 1

    def test_add_pages_from_pdf_n_up_order_column(self):
        """add_pages_from_pdf should respect order='column' parameter."""
        doc = Document()
        pdf_page = create_pdf_page()

        page_numbers = doc.add_pages_from_pdf(
            [pdf_page, pdf_page, pdf_page, pdf_page],
            layout="n_up",
            rows=2,
            columns=2,
            order="column",
        )

        assert len(page_numbers) == 1
        assert doc.page_count == 1

    def test_add_pages_from_pdf_n_up_invalid_rows(self):
        """add_pages_from_pdf should raise for invalid rows."""
        doc = Document()
        pdf_page = create_pdf_page()

        with pytest.raises(PdfValidationException) as exc_info:
            doc.add_pages_from_pdf([pdf_page], layout="n_up", rows=0)

        assert "rows must be a positive integer" in str(exc_info.value)

    def test_add_pages_from_pdf_n_up_invalid_columns(self):
        """add_pages_from_pdf should raise for invalid columns."""
        doc = Document()
        pdf_page = create_pdf_page()

        with pytest.raises(PdfValidationException) as exc_info:
            doc.add_pages_from_pdf([pdf_page], layout="n_up", columns=-1)

        assert "columns must be a positive integer" in str(exc_info.value)

    def test_add_pages_from_pdf_n_up_invalid_margin(self):
        """add_pages_from_pdf should raise for negative margin."""
        doc = Document()
        pdf_page = create_pdf_page()

        with pytest.raises(PdfValidationException) as exc_info:
            doc.add_pages_from_pdf([pdf_page], layout="n_up", margin=-5)

        assert "margin must be non-negative" in str(exc_info.value)

    def test_add_pages_from_pdf_n_up_invalid_order(self):
        """add_pages_from_pdf should raise for invalid order."""
        doc = Document()
        pdf_page = create_pdf_page()

        with pytest.raises(PdfValidationException) as exc_info:
            doc.add_pages_from_pdf([pdf_page], layout="n_up", order="invalid")

        assert "order must be 'row' or 'column'" in str(exc_info.value)

    def test_add_pages_from_pdf_n_up_with_page_numbers(self):
        """add_pages_from_pdf should work with page_numbers and n_up layout."""
        doc = Document()
        pdf_page = create_pdf_page()

        # Add pages 1 from each source
        page_numbers = doc.add_pages_from_pdf(
            [pdf_page, pdf_page],
            page_numbers=1,
            layout="n_up",
            rows=1,
            columns=2,
        )

        assert len(page_numbers) == 1
        assert doc.page_count == 1

    def test_add_pages_from_pdf_grid_layout_unchanged(self):
        """add_pages_from_pdf should maintain grid layout by default."""
        doc = Document()
        pdf_page = create_pdf_page()

        page_numbers = doc.add_pages_from_pdf([pdf_page, pdf_page])

        # Grid layout: 2 pages from 2 sources
        assert len(page_numbers) == 2
        assert doc.page_count == 2

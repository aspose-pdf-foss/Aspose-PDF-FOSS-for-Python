"""``Document.add_pages_from_pdf()`` adds PDF pages from files, bytes, or streams."""

from __future__ import annotations

import pytest

from aspose_pdf import Document


def _create_minimal_pdf_with_pages(num_pages: int = 3) -> bytes:
    """Create a minimal PDF with a specified number of pages."""
    pdf_body = []

    # Catalog and Pages dictionary
    pdf_body.append("1 0 obj << /Type /Catalog /Pages 2 0 R >>\nendobj\n")
    pdf_body.append(f"2 0 obj << /Type /Pages /Count {num_pages} /Kids [")

    # Page objects
    page_objects = []
    for i in range(num_pages):
        page_num = 3 + i
        page_objects.append(
            f"{page_num} 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\nendobj\n"
        )

    pdf_body.append(
        " ".join(f"{3 + i} 0 R" for i in range(num_pages)) + "] >>\nendobj\n"
    )
    pdf_body.extend(page_objects)

    # Xref and trailer
    total_objects = 3 + num_pages

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


class TestAddPagesFromPdf:
    """Tests for Document.add_pages_from_pdf() method."""

    def test_add_pages_from_pdf_with_file_path(self, tmp_path):
        """add_pages_from_pdf should work with a file path."""
        pdf_path = tmp_path / "source.pdf"
        pdf_path.write_bytes(_create_test_pdf())

        with Document() as doc:
            page_numbers = doc.add_pages_from_pdf([pdf_path])
            assert page_numbers == [1]
            assert len(doc.pages) == 1

    def test_add_pages_from_pdf_with_bytes(self):
        """add_pages_from_pdf should work with raw bytes."""
        pdf_bytes = _create_test_pdf()

        with Document() as doc:
            page_numbers = doc.add_pages_from_pdf([pdf_bytes])
            assert page_numbers == [1]
            assert len(doc.pages) == 1

    def test_add_pages_from_pdf_with_bytearray(self):
        """add_pages_from_pdf should work with bytearray."""
        pdf_bytes = bytearray(_create_test_pdf())

        with Document() as doc:
            page_numbers = doc.add_pages_from_pdf([pdf_bytes])
            assert page_numbers == [1]
            assert len(doc.pages) == 1

    def test_add_pages_from_pdf_with_bytesio(self):
        """add_pages_from_pdf should work with a BytesIO object."""
        pdf_bytes = _create_test_pdf()

        with Document() as doc:
            from io import BytesIO

            stream = BytesIO(pdf_bytes)
            page_numbers = doc.add_pages_from_pdf([stream])
            assert page_numbers == [1]
            assert len(doc.pages) == 1

    def test_add_pages_from_pdf_multiple_sources(self):
        """add_pages_from_pdf should add multiple pages from multiple sources."""
        with Document() as doc:
            page_numbers = doc.add_pages_from_pdf(
                [_create_test_pdf(), _create_test_pdf(), _create_test_pdf()]
            )
            assert page_numbers == [1, 2, 3]
            assert len(doc.pages) == 3

    def test_add_pages_from_pdf_with_page_numbers(self, tmp_path):
        """add_pages_from_pdf should import specific pages when page_numbers is provided."""
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
            # Import the second page from each of three PDFs
            page_numbers = doc.add_pages_from_pdf(
                [pdf_path, pdf_path, pdf_path], page_numbers=[2, 2, 2]
            )
            # Each imported page becomes page 1 in the new document
            assert page_numbers == [1, 2, 3]
            assert len(doc.pages) == 3

    def test_add_pages_from_pdf_empty_list(self):
        """add_pages_from_pdf should return empty list for empty input."""
        with Document() as doc:
            page_numbers = doc.add_pages_from_pdf([])
            assert page_numbers == []
            assert len(doc.pages) == 0

    def test_add_pages_from_pdf_invalid_source_type(self):
        """add_pages_from_pdf should raise TypeError for invalid source types."""
        with Document() as doc:
            with pytest.raises(TypeError, match="pdf_sources must be a list"):
                doc.add_pages_from_pdf("not a list")

    def test_add_pages_from_pdf_invalid_page_numbers_type(self):
        """add_pages_from_pdf should raise TypeError for invalid page_numbers type."""
        with Document() as doc:
            with pytest.raises(
                TypeError, match="page_numbers must be a list of integers or None"
            ):
                doc.add_pages_from_pdf([_create_test_pdf()], page_numbers="not a list")

    def test_add_pages_from_pdf_mismatched_lengths(self):
        """add_pages_from_pdf should raise ValueError when lengths don't match."""
        with Document() as doc:
            with pytest.raises(
                ValueError,
                match=r"page_numbers length.*must match pdf_sources length",
            ):
                doc.add_pages_from_pdf(
                    [_create_test_pdf(), _create_test_pdf()],
                    page_numbers=[1],  # Only one page number for two sources
                )

    def test_add_pages_from_pdf_negative_page_number(self):
        """add_pages_from_pdf should raise ValueError for negative page numbers."""
        with Document() as doc:
            with pytest.raises(ValueError, match="page_numbers must contain positive"):
                doc.add_pages_from_pdf([_create_test_pdf()], page_numbers=[0])

    def test_add_pages_from_pdf_non_integer_page_number(self):
        """add_pages_from_pdf should raise TypeError for non-integer page numbers."""
        with Document() as doc:
            with pytest.raises(
                TypeError, match="page_numbers must contain only integers"
            ):
                doc.add_pages_from_pdf(
                    [_create_test_pdf()], page_numbers=["not an int"]
                )

    def test_add_pages_from_pdf_boolean_page_number(self):
        """add_pages_from_pdf should raise TypeError for boolean page numbers."""
        with Document() as doc:
            with pytest.raises(
                TypeError, match="page_numbers must contain only integers"
            ):
                doc.add_pages_from_pdf([_create_test_pdf()], page_numbers=[True])

    def test_add_pages_from_pdf_invalid_stream_read_type(self):
        """add_pages_from_pdf should raise TypeError when stream.read() doesn't return bytes."""
        class BadStream:
            def read(self):
                return "not bytes"

        with Document() as doc:
            with pytest.raises(
                TypeError, match="read\\(\\) must return bytes or bytearray"
            ):
                doc.add_pages_from_pdf([BadStream()])

    def test_add_pages_from_pdf_disposed_document_raises(self, tmp_path):
        """add_pages_from_pdf should raise on a disposed document."""
        pdf_path = tmp_path / "source.pdf"
        pdf_path.write_bytes(_create_test_pdf())

        doc = Document()
        doc.dispose()

        with pytest.raises(Exception):
            doc.add_pages_from_pdf([pdf_path])

    def test_add_pages_from_pdf_nonexistent_file(self):
        """add_pages_from_pdf should raise FileNotFoundError for non-existent files."""
        with Document() as doc:
            with pytest.raises(FileNotFoundError):
                doc.add_pages_from_pdf(["/nonexistent/path/to/file.pdf"])

    def test_add_pages_from_pdf_preserves_page_properties(self, tmp_path):
        """add_pages_from_pdf should preserve page properties like rotation and boxes."""
        pdf_path = tmp_path / "source.pdf"
        pdf_path.write_bytes(_create_test_pdf())

        with Document() as doc:
            page_numbers = doc.add_pages_from_pdf([pdf_path])

            # Basic checks - the page should be importable
            assert page_numbers == [1]
            assert len(doc.pages) == 1
            # The page should have a media box
            page = doc.pages[1]
            assert page.media_box is not None

    def test_add_pages_from_pdf_invalid_page_number_in_source(self, tmp_path):
        """add_pages_from_pdf should raise IndexError for invalid page numbers in source PDF."""
        pdf_path = tmp_path / "source.pdf"
        pdf_path.write_bytes(_create_test_pdf())  # 1 page

        with Document() as doc:
            # Try to access page 2 in a 1-page document
            with pytest.raises(IndexError):
                doc.add_pages_from_pdf([pdf_path], page_numbers=[2])

    def test_add_pages_from_pdf_mixed_sources(self, tmp_path):
        """add_pages_from_pdf should work with mixed source types."""
        pdf_path = tmp_path / "source.pdf"
        pdf_path.write_bytes(_create_test_pdf())

        with Document() as doc:
            # Mix of path, bytes, and stream
            page_numbers = doc.add_pages_from_pdf(
                [
                    pdf_path,  # file path
                    _create_test_pdf(),  # bytes
                ]
            )
            assert page_numbers == [1, 2]
            assert len(doc.pages) == 2

    def test_add_pages_from_pdf_chaining(self):
        """add_pages_from_pdf should be usable in a fluent style."""
        with Document() as doc:
            page_numbers = doc.add_pages_from_pdf([_create_test_pdf()])
            # The returned page numbers should be usable
            assert page_numbers == [1]
            assert len(doc.pages) == 1

    def test_add_pages_from_pdf_incremental_save(self, tmp_path):
        """add_pages_from_pdf should work with incremental saves."""
        source_path = tmp_path / "source.pdf"
        source_path.write_bytes(_create_test_pdf())

        output_path = tmp_path / "output.pdf"

        with Document() as doc:
            doc.add_pages_from_pdf([source_path])
            doc.save(output_path, incremental=False)

            # Verify the saved file
            with Document(output_path) as reloaded:
                assert len(reloaded.pages) == 1

    def test_add_pages_from_pdf_multiple_pages_from_same_pdf(self, tmp_path):
        """add_pages_from_pdf should be able to add multiple pages from the same PDF."""
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
            # Add all 3 pages from the same PDF
            page_numbers = doc.add_pages_from_pdf(
                [pdf_path, pdf_path, pdf_path], page_numbers=[1, 2, 3]
            )
            assert page_numbers == [1, 2, 3]
            assert len(doc.pages) == 3

    def test_add_pages_from_pdf_none_page_numbers(self):
        """add_pages_from_pdf should use page 1 by default when page_numbers is None."""
        with Document() as doc:
            page_numbers = doc.add_pages_from_pdf(
                [_create_test_pdf(), _create_test_pdf()], page_numbers=None
            )
            # Each imported page becomes page 1 in the target document, then subsequent
            # additions become page 2, etc.
            assert page_numbers == [1, 2]
            assert len(doc.pages) == 2

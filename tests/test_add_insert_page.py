"""Tests for ``Document.add_page()`` and ``Document.insert_page()`` methods."""

from __future__ import annotations

import pytest

from aspose_pdf import Document, PageSize


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


class TestAddPage:
    """Tests for Document.add_page() method."""

    def test_add_page_from_another_document(self):
        """add_page should import a page from another document."""
        with Document() as source:
            source.pages.add(PageSize.A4)
            source.pages.add(PageSize.A4)

            with Document() as target:
                page = source.pages[1]
                added = target.add_page(page)

                assert len(target.pages) == 1
                assert added._document is target
                assert added._index == 0

    def test_add_page_from_same_document(self):
        """add_page should copy a page from the same document."""
        with Document() as doc:
            doc.pages.add(PageSize.A4)
            doc.pages.add(PageSize.A4)
            initial_count = len(doc.pages)

            page = doc.pages[1]
            added = doc.add_page(page)

            assert len(doc.pages) == initial_count + 1
            assert added._document is doc
            assert added._index == initial_count

    def test_add_page_after_remove_page(self):
        """add_page should work with a page returned by remove_page."""
        with Document() as source:
            source.pages.add(PageSize.A4)
            source.pages.add(PageSize.A4)

            with Document() as target:
                removed = source.remove_page(1)
                assert len(source.pages) == 1

                added = target.add_page(removed)
                assert len(target.pages) == 1
                assert added._document is target

    def test_add_page_multiple_times(self):
        """add_page should allow adding the same page multiple times (as copies)."""
        with Document() as source:
            source.pages.add(PageSize.A4)
            source.pages.add(PageSize.A4)

            with Document() as target:
                page = source.pages[1]

                target.add_page(page)
                target.add_page(page)

                assert len(target.pages) == 2

    def test_add_page_disposed_source_raises(self):
        """add_page should raise when the page's source document is disposed."""
        with Document() as source:
            source.pages.add(PageSize.A4)
            page = source.pages[1]

        with Document() as target:
            # Source is now disposed
            with pytest.raises(Exception):
                target.add_page(page)

    def test_add_page_invalid_type_handling(self):
        """add_page should handle non-Page arguments by passing to engine."""
        # PageCollection.add() accepts Any type and passes it to the engine
        # This test verifies that invalid types don't cause unexpected behavior
        with Document() as target:
            # Passing None creates a blank page
            added = target.add_page(None)
            assert added is not None
            assert len(target.pages) == 1


class TestInsertPage:
    """Tests for Document.insert_page() method."""

    def test_insert_page_at_beginning(self):
        """insert_page should insert at the beginning of the document."""
        with Document() as source:
            source.pages.add(PageSize.A4)

            with Document() as target:
                target.pages.add(PageSize.A4)
                target.pages.add(PageSize.A4)

                page = source.pages[1]
                inserted = target.insert_page(1, page)

                assert len(target.pages) == 3
                assert inserted._document is target
                assert inserted._index == 0
                # Original pages should have shifted
                assert target.pages[2]._index == 1
                assert target.pages[3]._index == 2

    def test_insert_page_at_middle(self):
        """insert_page should insert at a middle position."""
        with Document() as source:
            source.pages.add(PageSize.A4)

            with Document() as target:
                target.pages.add(PageSize.A4)
                target.pages.add(PageSize.A4)
                target.pages.add(PageSize.A4)

                page = source.pages[1]
                inserted = target.insert_page(2, page)

                assert len(target.pages) == 4
                assert inserted._index == 1

    def test_insert_page_at_end(self):
        """insert_page should insert at the end when index = len + 1."""
        with Document() as source:
            source.pages.add(PageSize.A4)

            with Document() as target:
                target.pages.add(PageSize.A4)
                target.pages.add(PageSize.A4)

                page = source.pages[1]
                inserted = target.insert_page(3, page)

                assert len(target.pages) == 3
                assert inserted._index == 2

    def test_insert_page_from_same_document(self):
        """insert_page should copy a page from the same document."""
        with Document() as doc:
            doc.pages.add(PageSize.A4)
            doc.pages.add(PageSize.A4)
            doc.pages.add(PageSize.A4)
            initial_count = len(doc.pages)

            page = doc.pages[2]
            inserted = doc.insert_page(1, page)

            assert len(doc.pages) == initial_count + 1
            assert inserted._index == 0
            assert inserted._document is doc

    def test_insert_page_after_remove_page(self):
        """insert_page should work with a page returned by remove_page."""
        with Document() as source:
            source.pages.add(PageSize.A4)
            source.pages.add(PageSize.A4)
            source.pages.add(PageSize.A4)

            removed = source.remove_page(2)
            assert len(source.pages) == 2

            with Document() as target:
                target.pages.add(PageSize.A4)
                target.pages.add(PageSize.A4)

                inserted = target.insert_page(1, removed)
                assert len(target.pages) == 3
                assert inserted._index == 0

    def test_insert_page_invalid_index_raises(self):
        """insert_page should raise IndexError for invalid indices."""
        with Document() as source:
            source.pages.add(PageSize.A4)
            page = source.pages[1]

            with Document() as target:
                # Invalid: index < 1
                with pytest.raises((IndexError, ValueError)):
                    target.insert_page(0, page)

                with pytest.raises((IndexError, ValueError)):
                    target.insert_page(-1, page)

    def test_insert_page_invalid_type_handling(self):
        """insert_page should handle non-Page arguments by passing to engine."""
        # PageCollection.insert() accepts Any type and passes it to the engine
        # This test verifies that invalid types don't cause unexpected behavior
        with Document() as target:
            # Passing None creates a blank page
            inserted = target.insert_page(1, None)
            assert inserted is not None
            assert len(target.pages) == 1


class TestAddInsertPageWithActualPDFs:
    """Tests for add_page and insert_page with real PDF content."""

    def test_add_page_preserves_content(self, tmp_path):
        """add_page should preserve the page content."""
        pdf_content = (
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
        source_path = tmp_path / "source.pdf"
        source_path.write_bytes(pdf_content)

        with Document.from_file(source_path) as source:
            with Document() as target:
                page = source.pages[1]
                target.add_page(page)

                assert len(target.pages) == 1
                # Verify we can save the target
                output_path = tmp_path / "output.pdf"
                target.save(output_path)
                assert output_path.exists()

    def test_insert_page_round_trip(self, tmp_path):
        """insert_page should support round-trip operations."""
        # Create a valid PDF with 3 pages using the library
        source_path = tmp_path / "source.pdf"
        with Document() as source:
            source.pages.add(PageSize.A4)
            source.pages.add(PageSize.A4)
            source.pages.add(PageSize.A4)
            source.save(source_path)

        with Document.from_file(source_path) as source:
            # Remove page 2
            removed = source.remove_page(2)
            assert len(source.pages) == 2

            # Add it to a new document
            with Document() as target:
                target.add_page(removed)
                assert len(target.pages) == 1

                # Insert it at position 1 (which is also at end for single page)
                target.insert_page(1, target.pages[1])
                assert len(target.pages) == 2

                # Verify save works
                output_path = tmp_path / "output.pdf"
                target.save(output_path)
                assert output_path.exists()

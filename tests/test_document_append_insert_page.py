"""Tests for Document.append_page_from() and Document.insert_page_from() methods."""

from __future__ import annotations

import pytest

from aspose_pdf import Document, PageSize
from aspose_pdf.exceptions import AsposePdfException, PdfValidationException
from aspose_pdf.outlines import OutlineItem


class TestAppendPageFrom:
    """Test Document.append_page_from() method."""

    def test_append_page_from_returns_self_for_chaining(self):
        """append_page_from returns self for method chaining."""
        with Document() as doc:
            doc.pages.add(PageSize.A4)

            with Document() as source:
                source.pages.add(PageSize.A4)

                result = doc.append_page_from(source, 1)
                assert result is doc

    def test_append_page_from_adds_page_at_end(self):
        """append_page_from adds a page at the end."""
        with Document() as target:
            for i in range(3):
                page = target.pages.add(PageSize.A4)
                page.add_text(f"Target Page {i+1}", x=72, y=700, font_size=12)

            with Document() as source:
                source.pages.add(PageSize.A4).add_text(
                    "Source Page 1", x=72, y=700, font_size=12
                )

                target.append_page_from(source, 1)

            assert len(target) == 4
            assert target.pages[4].extract_text().strip() == "Source Page 1"

    def test_append_page_from_with_multiple_pages_source(self):
        """append_page_from extracts the specified page from a multi-page source."""
        with Document() as target:
            target.pages.add(PageSize.A4)

            with Document() as source:
                for i in range(3):
                    page = source.pages.add(PageSize.A4)
                    page.add_text(f"Source Page {i+1}", x=72, y=700, font_size=12)

                target.append_page_from(source, 2)

            assert len(target) == 2
            assert target.pages[2].extract_text().strip() == "Source Page 2"

    def test_append_page_from_preserves_content(self):
        """append_page_from preserves the page's content."""
        with Document() as target:
            target.pages.add(PageSize.A4)

            with Document() as source:
                page = source.pages.add(PageSize.A4)
                page.add_text("Important text", x=72, y=700, font_size=12)
                page.add_text("More text on same page", x=72, y=650, font_size=10)

                target.append_page_from(source, 1)

            extracted = target.pages[2].extract_text()
            assert "Important text" in extracted
            assert "More text on same page" in extracted

    def test_append_page_from_source_unchanged(self):
        """append_page_from leaves the source document unchanged."""
        with Document() as target:
            target.pages.add(PageSize.A4)

            with Document() as source:
                for i in range(3):
                    source.pages.add(PageSize.A4)

                original_count = len(source)
                target.append_page_from(source, 1)

                assert len(source) == original_count
                assert source.pages[1].extract_text().strip() == ""

    def test_append_page_from_with_annotations(self):
        """append_page_from preserves annotations on the inserted page."""
        with Document() as target:
            target.pages.add(PageSize.A4)

            with Document() as source:
                page = source.pages.add(PageSize.A4)
                page.add_text("Text", x=72, y=700, font_size=12)
                page.annotations.add_highlight((100, 600, 200, 650))

                target.append_page_from(source, 1)

            assert len(list(target.pages[2].annotations)) == 1

    def test_append_page_from_with_form_fields(self):
        """append_page_from preserves form fields on the inserted page."""
        with Document() as target:
            target.pages.add(PageSize.A4)

            with Document() as source:
                page = source.pages.add(PageSize.A4)
                page.add_text("Text", x=72, y=700, font_size=12)
                source.form.add_text_field("TextField1", page, (100, 650, 200, 680), value="Hello")

                target.append_page_from(source, 1)

            # Check that the field was transferred
            assert len(target.form.fields) == 1
            assert target.form.fields[0].name == "TextField1"
            assert target.form.fields[0].value == "Hello"

    def test_append_page_from_with_bookmarks(self):
        """append_page_from preserves bookmarks pointing at the inserted page."""
        with Document() as target:
            target.pages.add(PageSize.A4)

            with Document() as source:
                page = source.pages.add(PageSize.A4)
                page.add_text("Target Page", x=72, y=700, font_size=12)

                # Add a bookmark to the page
                item = OutlineItem("My Bookmark", page_index=1)
                source.outlines.add(item)

                target.append_page_from(source, 1)

            # Check that the bookmark was transferred
            assert len(list(target.outlines)) == 1
            assert next(iter(target.outlines)).title == "My Bookmark"

    def test_append_page_from_last_page(self):
        """append_page_from can append the last page using len(source)."""
        with Document() as target:
            target.pages.add(PageSize.A4)

            with Document() as source:
                for i in range(3):
                    page = source.pages.add(PageSize.A4)
                    page.add_text(f"Source Page {i+1}", x=72, y=700, font_size=12)

                target.append_page_from(source, len(source))

            assert len(target) == 2
            assert target.pages[2].extract_text().strip() == "Source Page 3"

    def test_append_page_from_method_chaining(self):
        """append_page_from supports method chaining."""
        with Document() as doc:
            doc.pages.add(PageSize.A4)

            with Document() as source1:
                source1.pages.add(PageSize.A4)

                doc.append_page_from(source1, 1)

            with Document() as source2:
                source2.pages.add(PageSize.A4)

                doc.append_page_from(source2, 1)

            assert len(doc) == 3

    def test_append_page_from_invalid_page_number(self):
        """append_page_from raises PdfValidationException for invalid page number."""
        from aspose_pdf import PdfValidationException

        with Document() as target:
            target.pages.add(PageSize.A4)

            with Document() as source:
                source.pages.add(PageSize.A4)

                with pytest.raises(PdfValidationException):
                    target.append_page_from(source, 5)

    def test_append_page_from_disposed_source(self):
        """append_page_from raises AsposePdfException for disposed source document."""

        with Document() as target:
            target.pages.add(PageSize.A4)

            source = Document()
            source.pages.add(PageSize.A4)
            source.dispose()

            with pytest.raises(AsposePdfException):
                target.append_page_from(source, 1)


class TestInsertPageFrom:
    """Test Document.insert_page_from() method."""

    def test_insert_page_from_returns_self_for_chaining(self):
        """insert_page_from returns self for method chaining."""
        with Document() as doc:
            doc.pages.add(PageSize.A4)

            with Document() as source:
                source.pages.add(PageSize.A4)

                result = doc.insert_page_from(1, source, 1)
                assert result is doc

    def test_insert_page_from_at_beginning(self):
        """insert_page_from can insert at the beginning."""
        with Document() as target:
            for i in range(3):
                page = target.pages.add(PageSize.A4)
                page.add_text(f"Target Page {i+1}", x=72, y=700, font_size=12)

            with Document() as source:
                source.pages.add(PageSize.A4).add_text(
                    "Source Page 1", x=72, y=700, font_size=12
                )

                target.insert_page_from(1, source, 1)

            assert len(target) == 4
            assert target.pages[1].extract_text().strip() == "Source Page 1"
            assert target.pages[2].extract_text().strip() == "Target Page 1"

    def test_insert_page_from_in_middle(self):
        """insert_page_from can insert in the middle."""
        with Document() as target:
            for i in range(5):
                page = target.pages.add(PageSize.A4)
                page.add_text(f"Target Page {i+1}", x=72, y=700, font_size=12)

            with Document() as source:
                source.pages.add(PageSize.A4).add_text(
                    "Source Page 1", x=72, y=700, font_size=12
                )

                target.insert_page_from(3, source, 1)

            assert len(target) == 6
            assert target.pages[2].extract_text().strip() == "Target Page 2"
            assert target.pages[3].extract_text().strip() == "Source Page 1"
            assert target.pages[4].extract_text().strip() == "Target Page 3"

    def test_insert_page_from_at_end(self):
        """insert_page_from can insert at the end (equivalent to append_page_from)."""
        with Document() as target:
            for i in range(2):
                page = target.pages.add(PageSize.A4)
                page.add_text(f"Target Page {i+1}", x=72, y=700, font_size=12)

            with Document() as source:
                source.pages.add(PageSize.A4).add_text(
                    "Source Page 1", x=72, y=700, font_size=12
                )

                target.insert_page_from(3, source, 1)

            assert len(target) == 3
            assert target.pages[3].extract_text().strip() == "Source Page 1"

    def test_insert_page_from_source_unchanged(self):
        """insert_page_from leaves the source document unchanged."""
        with Document() as target:
            target.pages.add(PageSize.A4)

            with Document() as source:
                for i in range(3):
                    source.pages.add(PageSize.A4)

                original_count = len(source)
                target.insert_page_from(1, source, 2)

                assert len(source) == original_count
                assert source.pages[2].extract_text().strip() == ""

    def test_insert_page_from_with_annotations(self):
        """insert_page_from preserves annotations on the inserted page."""
        with Document() as target:
            target.pages.add(PageSize.A4)

            with Document() as source:
                page = source.pages.add(PageSize.A4)
                page.add_text("Text", x=72, y=700, font_size=12)
                page.annotations.add_highlight((100, 600, 200, 650))

                target.insert_page_from(1, source, 1)

            assert len(list(target.pages[1].annotations)) == 1

    def test_insert_page_from_with_form_fields(self):
        """insert_page_from preserves form fields on the inserted page."""
        with Document() as target:
            target.pages.add(PageSize.A4)

            with Document() as source:
                page = source.pages.add(PageSize.A4)
                page.add_text("Text", x=72, y=700, font_size=12)
                source.form.add_text_field("TextField1", page, (100, 650, 200, 680), value="Hello")

                target.insert_page_from(1, source, 1)

            # Check that the field was transferred
            assert len(target.form.fields) == 1
            assert target.form.fields[0].name == "TextField1"
            assert target.form.fields[0].value == "Hello"

    def test_insert_page_from_last_page(self):
        """insert_page_from can insert the last page using len(source)."""
        with Document() as target:
            target.pages.add(PageSize.A4)

            with Document() as source:
                for i in range(3):
                    page = source.pages.add(PageSize.A4)
                    page.add_text(f"Source Page {i+1}", x=72, y=700, font_size=12)

                target.insert_page_from(1, source, len(source))

            assert len(target) == 2
            assert target.pages[1].extract_text().strip() == "Source Page 3"
            assert target.pages[2].extract_text().strip() == ""

    def test_insert_page_from_method_chaining(self):
        """insert_page_from supports method chaining."""
        with Document() as doc:
            doc.pages.add(PageSize.A4)

            with Document() as source1:
                source1.pages.add(PageSize.A4)

                doc.insert_page_from(1, source1, 1)

            with Document() as source2:
                source2.pages.add(PageSize.A4)

                doc.insert_page_from(1, source2, 1)

            assert len(doc) == 3

    def test_insert_page_from_invalid_index(self):
        """insert_page_from raises error for invalid index."""
        with Document() as target:
            target.pages.add(PageSize.A4)

            with Document() as source:
                source.pages.add(PageSize.A4)

                with pytest.raises(IndexError):
                    target.insert_page_from(0, source, 1)

                with pytest.raises(IndexError):
                    target.insert_page_from(-1, source, 1)

    def test_insert_page_from_invalid_page_number(self):
        """insert_page_from raises PdfValidationException for invalid page number."""

        with Document() as target:
            target.pages.add(PageSize.A4)

            with Document() as source:
                source.pages.add(PageSize.A4)

                with pytest.raises(PdfValidationException):
                    target.insert_page_from(1, source, 5)

    def test_insert_page_from_disposed_source(self):
        """insert_page_from raises AsposePdfException for disposed source document."""

        with Document() as target:
            target.pages.add(PageSize.A4)

            with Document() as source:
                source.pages.add(PageSize.A4)

                source.dispose()

                with pytest.raises(AsposePdfException):
                    target.insert_page_from(1, source, 1)
        with Document() as target:
            target.pages.add(PageSize.A4)

            source = Document()
            source.pages.add(PageSize.A4)
            source.dispose()

            with pytest.raises(Exception):
                target.insert_page_from(1, source, 1)

    def test_insert_page_from_multiple_times(self):
        """insert_page_from can insert the same page multiple times."""
        with Document() as target:
            target.pages.add(PageSize.A4)

            with Document() as source:
                page = source.pages.add(PageSize.A4)
                page.add_text("Source Page 1", x=72, y=700, font_size=12)

                target.insert_page_from(1, source, 1)
                target.insert_page_from(1, source, 1)

            assert len(target) == 3
            assert target.pages[1].extract_text().strip() == "Source Page 1"
            assert target.pages[2].extract_text().strip() == "Source Page 1"
            assert target.pages[3].extract_text().strip() == ""

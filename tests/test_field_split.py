"""Test splitting a PDF by form field values."""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document
from aspose_pdf.exceptions import PdfValidationException


def _bytes(document: Document) -> bytes:
    stream = io.BytesIO()
    document.save(stream)
    return stream.getvalue()


def _field_values(document: Document, field_name: str) -> list[str | None]:
    """Get the field's value on each page."""
    values: list[str | None] = []
    for page in document.pages:
        if field_name in document.form:
            field = document.form[field_name]
            widgets = field.widgets
            # Check if any widget is on this page
            on_page = any(w.page_index == page.index + 1 for w in widgets)
            if on_page:
                values.append(field.value)
            else:
                values.append(None)
        else:
            values.append(None)
    return values


class TestSplitByFieldValue:
    """Tests for Document.split_by_field_value()."""

    def test_empty_document_returns_empty_list(self) -> None:
        """An empty document produces no parts."""
        with Document() as doc:
            parts = doc.split_by_field_value("NonExistent")
            assert parts == []

    def test_field_not_found_raises_exception(self) -> None:
        """A non-existent field raises PdfValidationException."""
        with Document() as doc:
            doc.pages.add()
            with pytest.raises(PdfValidationException) as exc_info:
                doc.split_by_field_value("NonExistent")
            assert "not found" in str(exc_info.value).lower()

    def test_single_part_when_value_does_not_change(self) -> None:
        """If the field value is the same everywhere, we get one part."""
        with Document() as doc:
            for i in range(3):
                page = doc.pages.add(size=(600, 800))
                page.add_text(f"Page {i + 1}", 12, 750)
            
            # Add a field on page 1
            doc.form.add_text_field("Status", 1, (50, 700, 200, 720), value="Active")
            doc.generate_field_appearances()
            
            # Add widgets on other pages
            for i in range(2, 4):
                doc.form.add_text_field("Status", i, (50, 700, 200, 720), value="Active")
            
            parts = doc.split_by_field_value("Status")
            assert len(parts) == 1
            assert parts[0].page_count == 3

    def test_splits_when_value_changes(self) -> None:
        """A change in field value creates a new part."""
        with Document() as doc:
            for i in range(6):
                page = doc.pages.add(size=(600, 800))
                page.add_text(f"Page {i + 1}", 12, 750)
            
            # Add a field on page 1 with value "Active"
            doc.form.add_text_field("Status", 1, (50, 700, 200, 720), value="Active")
            
            # Add widgets with different values
            for i in range(2, 4):
                doc.form.add_text_field("Status", i, (50, 700, 200, 720), value="Active")
            for i in range(4, 7):
                doc.form.add_text_field("Status", i, (50, 700, 200, 720), value="Inactive")
            
            parts = doc.split_by_field_value("Status")
            # Pages 1-3 have "Active", pages 4-6 have "Inactive"
            assert len(parts) == 2
            assert parts[0].page_count == 3
            assert parts[1].page_count == 3

    def test_pattern_matching(self) -> None:
        """Pattern matching creates boundaries at matching values."""
        with Document() as doc:
            for i in range(6):
                page = doc.pages.add(size=(600, 800))
                page.add_text(f"Page {i + 1}", 12, 750)
            
            # Values: A, A, B, B, A, A
            for i in range(2):
                doc.form.add_text_field("Category", i + 1, (50, 700, 200, 720), value="A")
            for i in range(2, 4):
                doc.form.add_text_field("Category", i + 1, (50, 700, 200, 720), value="B")
            for i in range(4, 6):
                doc.form.add_text_field("Category", i + 1, (50, 700, 200, 720), value="A")
            
            # Match only "A"
            parts = doc.split_by_field_value("Category", pattern="A")
            # Boundaries at pages where value is "A"
            assert len(parts) >= 1

    def test_include_preamble_false(self) -> None:
        """include_preamble=False excludes leading pages before first boundary."""
        with Document() as doc:
            for i in range(4):
                page = doc.pages.add(size=(600, 800))
                page.add_text(f"Page {i + 1}", 12, 750)
            
            # First two pages: "X", next two: "Y"
            for i in range(2):
                doc.form.add_text_field("Group", i + 1, (50, 700, 200, 720), value="X")
            for i in range(2, 4):
                doc.form.add_text_field("Group", i + 1, (50, 700, 200, 720), value="Y")
            
            # With preamble: [pages 1-2], [pages 3-4]
            parts_with = doc.split_by_field_value("Group", include_preamble=True)
            # Without preamble: [pages 3-4] only
            parts_without = doc.split_by_field_value("Group", include_preamble=False)
            
            assert len(parts_with) == 2
            assert len(parts_without) == 1
            assert parts_without[0].page_count == 2

    def test_regex_matching(self) -> None:
        """Regular expression patterns match field values."""
        with Document() as doc:
            for i in range(6):
                page = doc.pages.add(size=(600, 800))
                page.add_text(f"Page {i + 1}", 12, 750)
            
            # Values: Vendor-A, Vendor-B, Other-Corp, Vendor-D
            doc.form.add_text_field("Vendor", 1, (50, 700, 200, 720), value="Vendor-A")
            doc.form.add_text_field("Vendor", 2, (50, 700, 200, 720), value="Vendor-B")
            doc.form.add_text_field("Vendor", 3, (50, 700, 200, 720), value="Other-Corp")
            doc.form.add_text_field("Vendor", 4, (50, 700, 200, 720), value="Vendor-D")
            
            # Match "Vendor-"
            parts = doc.split_by_field_value("Vendor", pattern=r"Vendor-", regex=True)
            assert len(parts) >= 1

    def test_case_insensitive(self) -> None:
        """case_sensitive=False ignores case in pattern matching."""
        with Document() as doc:
            for i in range(3):
                page = doc.pages.add(size=(600, 800))
                page.add_text(f"Page {i + 1}", 12, 750)
            
            doc.form.add_text_field("Status", 1, (50, 700, 200, 720), value="ACTIVE")
            doc.form.add_text_field("Status", 2, (50, 700, 200, 720), value="active")
            doc.form.add_text_field("Status", 3, (50, 700, 200, 720), value="Active")
            
            # Case-insensitive match for "active"
            parts = doc.split_by_field_value("Status", pattern="active", case_sensitive=False)
            # All three have "active" (case-insensitive), so no split
            assert len(parts) == 1
            assert parts[0].page_count == 3

    def test_disposed_document_rejected(self) -> None:
        """A disposed document raises an exception."""
        doc = Document()
        doc.pages.add()
        doc.form.add_text_field("Field", 1, (50, 700, 200, 720), value="Value")
        doc.dispose()
        
        with pytest.raises(Exception):  # AsposePdfException or similar
            doc.split_by_field_value("Field")

    def test_round_trip_preserves_parts(self) -> None:
        """Split parts can be saved and reloaded."""
        with Document() as doc:
            for i in range(4):
                page = doc.pages.add(size=(600, 800))
                page.add_text(f"Page {i + 1}", 12, 750)
            
            for i in range(2):
                doc.form.add_text_field("Group", i + 1, (50, 700, 200, 720), value="A")
            for i in range(2, 4):
                doc.form.add_text_field("Group", i + 1, (50, 700, 200, 720), value="B")
            
            parts = doc.split_by_field_value("Group")
            
            # Save each part and reload
            for i, part in enumerate(parts):
                data = _bytes(part)
                with Document(data) as reloaded:
                    assert reloaded.page_count == part.page_count
                    assert reloaded.validate()

    def test_field_on_some_pages_only(self) -> None:
        """Fields that appear only on some pages are handled."""
        with Document() as doc:
            for i in range(4):
                page = doc.pages.add(size=(600, 800))
                page.add_text(f"Page {i + 1}", 12, 750)
            
            # Field only on pages 1 and 3
            doc.form.add_text_field("Partial", 1, (50, 700, 200, 720), value="X")
            doc.form.add_text_field("Partial", 3, (50, 700, 200, 720), value="Y")
            
            parts = doc.split_by_field_value("Partial")
            # Pages 1 and 3 have values, 2 and 4 don't
            assert len(parts) >= 1

    def test_nested_field_name(self) -> None:
        """Nested field names work correctly."""
        with Document() as doc:
            for i in range(3):
                page = doc.pages.add(size=(600, 800))
                page.add_text(f"Page {i + 1}", 12, 750)
            
            # Create nested field
            doc.pages[0]
            doc.form.add_text_field("Address.Country", 1, (50, 700, 200, 720), value="USA")
            doc.form.add_text_field("Address.Country", 2, (50, 700, 200, 720), value="USA")
            doc.form.add_text_field("Address.Country", 3, (50, 700, 200, 720), value="Canada")
            
            parts = doc.split_by_field_value("Address.Country")
            assert len(parts) == 2
            assert parts[0].page_count == 2
            assert parts[1].page_count == 1

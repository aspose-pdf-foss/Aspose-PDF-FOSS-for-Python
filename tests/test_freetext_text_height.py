"""Tests for FreeTextAnnotation.text_height property."""

from io import BytesIO

import pytest

from aspose_pdf import Document


def _page():
    """Create a new document with one page for testing."""
    document = Document()
    page = document.pages.add()
    return document, page


class TestFreeTextTextHeight:
    """Tests for FreeTextAnnotation.text_height property."""

    def test_text_height_calculates_from_font_size_and_lines(self):
        """text_height returns font_size * 1.2 * num_lines."""
        with Document() as doc:
            page = doc.pages.add()
            ft = page.annotations.add_free_text(
                (50, 50, 250, 95),
                "Line1\nLine2",
                default_appearance="/Helv 12 Tf 0 g"
            )
            # 12 * 1.2 * 2 = 28.8
            assert ft.text_height == pytest.approx(28.8, rel=1e-9)

    def test_text_height_empty_contents_returns_zero(self):
        """text_height returns 0.0 for empty contents."""
        with Document() as doc:
            page = doc.pages.add()
            ft = page.annotations.add_free_text(
                (50, 50, 250, 95),
                "",
                default_appearance="/Helv 12 Tf 0 g"
            )
            assert ft.text_height == 0.0

    def test_text_height_single_line(self):
        """text_height correctly calculates for single line."""
        with Document() as doc:
            page = doc.pages.add()
            ft = page.annotations.add_free_text(
                (50, 50, 250, 95),
                "Single",
                default_appearance="/Times-Roman 10 Tf 1 0 0 rg"
            )
            # 10 * 1.2 * 1 = 12.0
            assert ft.text_height == 12.0

    def test_text_height_missing_default_appearance_raises(self):
        """text_height raises ValueError if default_appearance is missing."""
        with Document() as doc:
            page = doc.pages.add()
            ft = page.annotations.add_free_text(
                (50, 50, 250, 95),
                "Test"
            )
            with pytest.raises(ValueError, match="default_appearance is not set"):
                _ = ft.text_height

    def test_text_height_invalid_default_appearance_raises(self):
        """text_height raises ValueError if DA lacks font size."""
        with Document() as doc:
            page = doc.pages.add()
            ft = page.annotations.add_free_text(
                (50, 50, 250, 95),
                "Test",
                default_appearance="/Helv Tf 0 g"  # Missing font size
            )
            with pytest.raises(ValueError, match="does not contain a valid font size"):
                _ = ft.text_height

    def test_text_height_setter_updates_rect_height(self):
        """Setting text_height updates rect height while preserving other coords."""
        with Document() as doc:
            page = doc.pages.add()
            ft = page.annotations.add_free_text(
                (50, 50, 250, 95),
                "Single",
                default_appearance="/Helv 12 Tf 0 g"
            )
            original_rect = ft.rect
            ft.text_height = 36.0
            new_rect = ft.rect
            # Left, bottom, right should be unchanged
            assert new_rect[0] == original_rect[0]  # left
            assert new_rect[1] == original_rect[1]  # bottom
            assert new_rect[2] == original_rect[2]  # right
            # Top should be bottom + new height
            assert new_rect[3] == original_rect[1] + 36.0

    def test_text_height_setter_updates_da(self):
        """Setting text_height updates DA to reflect new font size."""
        with Document() as doc:
            page = doc.pages.add()
            ft = page.annotations.add_free_text(
                (50, 50, 250, 95),
                "Single",
                default_appearance="/Times-Roman 10 Tf 1 0 0 rg"
            )
            ft.text_height = 36.0
            # After setting to 36.0 with 1 line:
            # new_font_size = 36.0 / 1.2 / 1 = 30.0
            assert "30.0" in ft.default_appearance
            # Reading text_height after setting should return the set value
            assert ft.text_height == 36.0

    def test_text_height_setter_negative_raises(self):
        """Setting text_height to negative raises ValueError."""
        with Document() as doc:
            page = doc.pages.add()
            ft = page.annotations.add_free_text(
                (50, 50, 250, 95),
                "Test",
                default_appearance="/Helv 12 Tf 0 g"
            )
            with pytest.raises(ValueError, match="must be positive"):
                ft.text_height = -5.0

    def test_text_height_setter_zero_raises(self):
        """Setting text_height to zero raises ValueError."""
        with Document() as doc:
            page = doc.pages.add()
            ft = page.annotations.add_free_text(
                (50, 50, 250, 95),
                "Test",
                default_appearance="/Helv 12 Tf 0 g"
            )
            with pytest.raises(ValueError, match="must be positive"):
                ft.text_height = 0

    def test_text_height_setter_bool_raises(self):
        """Setting text_height to bool raises ValueError."""
        with Document() as doc:
            page = doc.pages.add()
            ft = page.annotations.add_free_text(
                (50, 50, 250, 95),
                "Test",
                default_appearance="/Helv 12 Tf 0 g"
            )
            with pytest.raises(ValueError, match="must be a number"):
                ft.text_height = True

    def test_text_height_roundtrip_preserves_value(self):
        """Saving and reloading preserves text_height calculations."""
        buffer = BytesIO()
        with Document() as doc:
            page = doc.pages.add()
            ft = page.annotations.add_free_text(
                (50, 50, 250, 95),
                "Line1\nLine2",
                default_appearance="/Helv 12 Tf 0 g"
            )
            ft.text_height = 36.0
            original_height = ft.text_height
            original_rect = ft.rect
            doc.save(buffer)
        buffer.seek(0)
        reloaded_doc = Document(buffer)
        reloaded = reloaded_doc.pages[1].annotations[0]
        assert reloaded.text_height == pytest.approx(original_height, rel=1e-9)
        assert reloaded.rect == pytest.approx(original_rect, rel=1e-9)
        reloaded_doc.dispose()

    def test_text_height_with_decimal_font_size(self):
        """text_height works with decimal font sizes."""
        with Document() as doc:
            page = doc.pages.add()
            ft = page.annotations.add_free_text(
                (50, 50, 250, 95),
                "Line1\nLine2",
                default_appearance="/Helv 10.5 Tf 0 g"
            )
            # 10.5 * 1.2 * 2 = 25.2
            assert ft.text_height == pytest.approx(25.2, rel=1e-9)

    def test_text_height_multiple_lines(self):
        """text_height correctly counts multiple lines."""
        with Document() as doc:
            page = doc.pages.add()
            ft = page.annotations.add_free_text(
                (50, 50, 250, 95),
                "Line1\nLine2\nLine3\nLine4",
                default_appearance="/Helv 12 Tf 0 g"
            )
            # 12 * 1.2 * 4 = 57.6
            assert ft.text_height == pytest.approx(57.6, rel=1e-9)

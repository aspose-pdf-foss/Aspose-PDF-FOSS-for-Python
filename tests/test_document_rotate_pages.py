"""Tests for Document.rotate_pages()."""

from __future__ import annotations

from pathlib import Path

import pytest

from aspose_pdf import Document, PageSize
from aspose_pdf.engine.cos import PdfName, PdfNumber
from aspose_pdf.exceptions import AsposePdfException, PdfValidationException


def _numbered_document(count: int = 3) -> Document:
    """A document whose pages carry their one-based number as text."""
    document = Document()
    for number in range(1, count + 1):
        page = document.pages.add(PageSize.A4)
        page.add_text(f"Page {number}", x=72, y=700, font_size=12)
    return document


def _rotations(document: Document) -> list[int]:
    return [page.rotation for page in document.pages]


class TestRotatePages:
    def test_rotates_every_page_by_default(self):
        with _numbered_document() as document:
            assert document.rotate_pages(90) == 3
            assert _rotations(document) == [90, 90, 90]
            assert [page.extract_text().strip() for page in document.pages] == [
                "Page 1",
                "Page 2",
                "Page 3",
            ]

    def test_rotation_is_relative_by_default(self):
        with _numbered_document() as document:
            document.rotate_pages(90)
            assert document.rotate_pages(180) == 3
            assert _rotations(document) == [270, 270, 270]

    def test_relative_rotation_works_with_page_rotate(self):
        with _numbered_document() as document:
            document.pages[1].rotate(90)
            assert document.rotate_pages(90, pages=[1]) == 1
            assert document.pages[1].rotation == 180

    def test_negative_degrees_turn_counter_clockwise(self):
        with _numbered_document() as document:
            assert document.rotate_pages(-90) == 3
            assert _rotations(document) == [270, 270, 270]

    def test_only_the_selected_pages_change(self):
        with _numbered_document() as document:
            assert document.rotate_pages(90, pages=[2]) == 1
            assert _rotations(document) == [0, 90, 0]

    def test_repeated_page_numbers_are_turned_once(self):
        with _numbered_document() as document:
            pages = (number for number in [1, 1, 2])
            assert document.rotate_pages(90, pages=pages) == 2
            assert _rotations(document) == [90, 90, 0]

    def test_empty_selection_changes_nothing(self):
        with _numbered_document() as document:
            assert document.rotate_pages(90, pages=[]) == 0
            assert _rotations(document) == [0, 0, 0]

    def test_absolute_rotation_sets_the_selected_pages(self):
        with _numbered_document() as document:
            document.pages[2].rotation = 90
            assert document.rotate_pages(180, pages=[1, 3], relative=False) == 2
            assert _rotations(document) == [180, 90, 180]

    @pytest.mark.parametrize(
        ("degrees", "expected"), [(0, 0), (90, 90), (450, 90), (-90, 270)]
    )
    def test_absolute_rotation_is_normalised(self, degrees, expected):
        with _numbered_document(1) as document:
            assert document.rotate_pages(degrees, relative=False) == 1
            assert document.pages[1].rotation == expected

    @pytest.mark.parametrize("degrees", [90.0, 180.0])
    def test_integral_float_degrees_are_accepted(self, degrees):
        with _numbered_document(1) as document:
            assert document.rotate_pages(degrees) == 1
            assert document.pages[1].rotation == int(degrees)

    def test_large_integer_degrees_are_validated_exactly(self):
        degrees = 90 * (2**53 + 1)

        with _numbered_document(1) as document:
            assert document.rotate_pages(degrees) == 1
            assert document.pages[1].rotation == 90

    def test_rotation_survives_a_save(self, tmp_path: Path):
        path = tmp_path / "rotated.pdf"
        with _numbered_document() as document:
            document.rotate_pages(90, pages=[2])
            document.save(path)

        with Document(path) as reopened:
            assert _rotations(reopened) == [0, 90, 0]

    def test_streaming_document(self, tmp_path: Path):
        source = tmp_path / "source.pdf"
        with _numbered_document(1) as document:
            document.save(source)

        target = tmp_path / "streamed.pdf"
        with Document.open_streaming(source) as document:
            assert document.rotate_pages(90) == 1
            document.save(target)

        with Document(target) as reopened:
            assert _rotations(reopened) == [90]

    def test_encrypted_document(self):
        with _numbered_document(1) as document:
            document.encrypt(user_password="user", owner_password="owner")
            assert document.rotate_pages(90) == 1
            data = document.to_bytes()

        with Document(data, password="user") as reopened:
            assert _rotations(reopened) == [90]

    def test_incremental_save(self, tmp_path: Path):
        source = tmp_path / "source.pdf"
        with _numbered_document(2) as document:
            document.save(source)

        with Document(source) as document:
            assert document.rotate_pages(90, pages=[2]) == 1
            data = document.to_bytes(incremental=True)

        with Document(data) as reopened:
            assert _rotations(reopened) == [0, 90]

    def test_boxes_and_page_content_are_preserved(self):
        with _numbered_document(1) as document:
            page = document.pages[1]
            page.crop_box = (10, 20, 300, 400)
            before = page.content

            assert document.rotate_pages(90) == 1

            assert page.crop_box == (10, 20, 300, 400)
            assert page.content == before
            assert page.extract_text().strip() == "Page 1"

    def test_annotations_are_preserved(self):
        with _numbered_document(1) as document:
            page = document.pages[1]
            page.annotations.add("Square", (72, 300, 200, 400), "A note")

            assert document.rotate_pages(90) == 1

            annotations = page.annotations
            assert len(annotations) == 1
            assert annotations[0].subtype == "Square"
            assert annotations[0].rect == (72.0, 300.0, 200.0, 400.0)
            assert annotations[0].contents == "A note"

    def test_form_fields_are_preserved(self):
        with _numbered_document(1) as document:
            document.form.add_text_field(
                "nickname", 1, (72, 100, 272, 130), value="typed"
            )

            assert document.rotate_pages(90) == 1

            assert [field.name for field in document.form] == ["nickname"]
            assert document.form["nickname"].value == "typed"

    def test_empty_document_returns_zero(self):
        with Document() as document:
            assert document.rotate_pages(90) == 0
            assert document.page_count == 0

    @pytest.mark.parametrize(
        "degrees",
        [45, 90.5, "90", "ninety", None, True, False, float("nan"), float("inf")],
    )
    def test_invalid_degrees_leave_every_page_unchanged(self, degrees):
        with _numbered_document() as document:
            with pytest.raises(PdfValidationException):
                document.rotate_pages(degrees)
            assert _rotations(document) == [0, 0, 0]

    def test_invalid_page_selection_leaves_every_page_unchanged(self):
        with _numbered_document() as document:
            document.pages[2].rotation = 90
            with pytest.raises(PdfValidationException):
                document.rotate_pages(90, pages=[1, 4])
            assert _rotations(document) == [0, 90, 0]

    def test_malformed_rotation_is_rejected_before_any_change(self):
        with _numbered_document() as document:
            page_dict = document._engine_pdf._get_page_dict(1)
            page_dict.mapping[PdfName("Rotate")] = PdfNumber(45)

            with pytest.raises(PdfValidationException, match="Page 2"):
                document.rotate_pages(90)

            assert document.pages[1].rotation == 0
            assert document.pages[2].rotation == 45
            assert document.pages[3].rotation == 0

    def test_absolute_rotation_can_repair_a_malformed_page(self):
        with _numbered_document(1) as document:
            page_dict = document._engine_pdf._get_page_dict(0)
            page_dict.mapping[PdfName("Rotate")] = PdfNumber(45)

            assert document.rotate_pages(90, relative=False) == 1
            assert document.pages[1].rotation == 90

    def test_inherited_rotation_is_rotated_relative_to_the_visible_value(self):
        with _numbered_document(1) as document:
            page_dict = document._engine_pdf._get_page_dict(0)
            parent = document._engine_pdf._resolve(
                page_dict.mapping.get(PdfName("Parent"))
            )
            parent.mapping[PdfName("Rotate")] = PdfNumber(90)

            assert document.rotate_pages(90) == 1
            assert document.pages[1].rotation == 180

    def test_relative_must_be_a_boolean(self):
        with _numbered_document() as document:
            with pytest.raises(TypeError, match="relative"):
                document.rotate_pages(90, relative=1)
            assert _rotations(document) == [0, 0, 0]

    def test_disposed_document_raises(self):
        document = _numbered_document()
        document.dispose()

        with pytest.raises(AsposePdfException):
            document.rotate_pages(90)

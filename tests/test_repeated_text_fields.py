"""Create one text field with several synchronized widget appearances."""

from __future__ import annotations

from io import BytesIO
from itertools import repeat

import pytest

from aspose_pdf import Document, FieldWidget, PdfLoadLimits
from aspose_pdf.exceptions import PdfResourceLimitException, PdfValidationException


def _bytes(document):
    output = BytesIO()
    document.save(output)
    return output.getvalue()


@pytest.mark.parametrize("on_disk", [False, True])
def test_shared_value_and_all_appearances_survive_round_trip(tmp_path, on_disk):
    with Document() as document:
        first = document.pages.add(size=(300, 200))
        second = document.pages.add(size=(300, 200))
        placements = [
            (second, (20, 100, 220, 130)),
            (1, (10, 40, 250, 80)),
            (second, (40, 20, 180, 45)),
        ]
        field = document.form.add_text_field(
            "customer.name",
            widgets=iter(placements),
            value="Before",
            font_size=0,
            required=True,
            alignment="center",
            background_color=(0.9, 0.9, 1),
            border_width=2,
        )
        expected = [
            FieldWidget(2, placements[0][1]),
            FieldWidget(1, placements[1][1]),
            FieldWidget(2, placements[2][1]),
        ]
        assert field.widgets == expected
        assert len(document.form) == 1
        assert [len(page.annotations) for page in (first, second)] == [1, 2]
        field.default_value = "Default"
        document.form.fill({"customer.name": "Updated"})
        assert document.form["customer.name"] is field
        for page in (first, second):
            for annotation in page.annotations:
                assert b"(Updated) Tj" in annotation.appearance_normal
                assert b"(Before) Tj" not in annotation.appearance_normal

        if on_disk:
            source = tmp_path / "shared-field.pdf"
            document.save(source)
        else:
            source = _bytes(document)
        with Document(source) as reopened:
            loaded = reopened.form["customer.name"]
            assert reopened.form.to_dict() == {"customer.name": "Updated"}
            assert loaded.widgets == expected
            assert loaded.required and loaded.alignment == "center"
            assert loaded.border_width == 2
            assert loaded.background_color == (0.9, 0.9, 1)
            reopened.form.reset()
            assert loaded.value == "Default"
            for page in reopened.pages:
                for annotation in page.annotations:
                    assert b"(Default) Tj" in annotation.appearance_normal


def test_selective_flatten_keeps_other_occurrences_editable():
    with Document() as document:
        document.pages.add()
        document.pages.add()
        field = document.form.add_text_field(
            "shared",
            widgets=[(1, (20, 20, 200, 50)), (2, (20, 20, 200, 50))],
            value="Original",
        )
        document.flatten(fields=["shared"], annotations=False, pages=[1])
        assert document.form["shared"] is field
        assert field.widgets == [FieldWidget(2, (20, 20, 200, 50))]
        field.value = "Changed"
        document.flatten(fields=["shared"], annotations=False, pages=[2])
        with Document(_bytes(document)) as reopened:
            assert len(reopened.form) == 0
            assert "Original" in reopened.pages[1].extract_text()
            assert "Changed" in reopened.pages[2].extract_text()
            assert "Changed" not in reopened.pages[1].extract_text()


def test_widget_generator_snapshots_reused_rectangles():
    with Document() as document:
        page = document.pages.add()
        rect = [20, 20, 200, 50]

        def placements():
            yield page, rect
            rect[1], rect[3] = 70, 100
            yield page, rect

        field = document.form.add_text_field("shared", widgets=placements())
        assert field.widgets == [
            FieldWidget(1, (20, 20, 200, 50)),
            FieldWidget(1, (20, 70, 200, 100)),
        ]


@pytest.mark.parametrize(
    "options,error",
    [
        ({}, TypeError),
        ({"page": 1}, TypeError),
        ({"rect": (20, 20, 200, 50)}, TypeError),
        ({"widgets": []}, PdfValidationException),
        ({"widgets": 1}, TypeError),
        ({"widgets": "invalid"}, TypeError),
        ({"widgets": {1: (20, 20, 200, 50)}}, TypeError),
        ({"widgets": [(1,)]}, TypeError),
        ({"widgets": ["ab"]}, TypeError),
        ({"widgets": [(True, (20, 20, 200, 50))]}, TypeError),
        ({"widgets": [(2, (20, 20, 200, 50))]}, IndexError),
        (
            {"widgets": [(1, (20, 20, 200, 50)), (1, (20, 20, 20, 50))]},
            PdfValidationException,
        ),
        ({"widgets": [(1, (20, 20, 200, 50))], "page": 1}, TypeError),
        ({"widgets": [(1, (20, 20, 200, 50))], "rect": (20, 20, 200, 50)}, TypeError),
    ],
)
def test_invalid_placements_leave_existing_document_unchanged(options, error):
    with Document() as document:
        document.pages.add()
        document.form.add_text_field("existing", 1, (20, 70, 200, 100), value="Keep")
        before = _bytes(document)
        with pytest.raises(error):
            document.form.add_text_field("shared", **options)
        assert document.form.to_dict() == {"existing": "Keep"}
        assert _bytes(document) == before


def test_foreign_page_and_failing_generator_do_not_create_partial_fields():
    with Document() as document, Document() as other:
        page = document.pages.add()
        foreign = other.pages.add()
        before = _bytes(document)
        with pytest.raises(PdfValidationException, match="different document"):
            document.form.add_text_field(
                "shared",
                widgets=[(page, (20, 20, 200, 50)), (foreign, (20, 20, 200, 50))],
            )

        def placements():
            yield page, (20, 20, 200, 50)
            raise ValueError("Failed to read placements")

        with pytest.raises(ValueError, match="Failed to read placements"):
            document.form.add_text_field("shared", widgets=placements())
        assert _bytes(document) == before


def test_widget_iterable_obeys_resource_limit_before_creating_fields():
    with Document(limits=PdfLoadLimits(max_container_items=2)) as document:
        page = document.pages.add()
        with pytest.raises(PdfResourceLimitException, match="text field widgets"):
            document.form.add_text_field(
                "shared", widgets=repeat((page, (20, 20, 200, 50)))
            )
        assert len(document.form) == 0
        assert len(page.annotations) == 0

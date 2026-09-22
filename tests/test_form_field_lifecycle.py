"""Retained form and field objects stay consistent across public mutations."""

from io import BytesIO

import pytest

from aspose_pdf import Document
from aspose_pdf.exceptions import AsposePdfException


@pytest.fixture
def document():
    with Document() as doc:
        doc.pages.add()
        yield doc


def _add_text(document, name="name", value="before"):
    return document.form.add_text_field(name, 0, (20, 20, 200, 40), value=value)


def _save(document, tmp_path, on_disk):
    if on_disk:
        path = tmp_path / "form.pdf"
        document.save(path)
        return path
    output = BytesIO()
    document.save(output)
    return output.getvalue()


@pytest.mark.parametrize("on_disk", [False, True])
def test_retained_field_stays_current_after_other_fields_change(
    document, tmp_path, on_disk
):
    field = _add_text(document)
    _add_text(document, "other")

    field.value = "after add"
    assert document.form["name"].value == "after add"

    document.form["name"].value = "through collection"
    assert field.value == "through collection"

    document.form.remove_field("other")
    field.value = "after removal"
    assert document.form["name"].value == "after removal"
    with Document(_save(document, tmp_path, on_disk)) as reopened:
        assert reopened.form["name"].value == field.value


@pytest.mark.parametrize("format_name", ["fdf", "xfdf"])
def test_import_updates_retained_field(document, format_name):
    field = _add_text(document)
    with Document() as source:
        source.pages.add()
        _add_text(source, value="imported")
        data = getattr(source.form, f"export_{format_name}")()

    getattr(document.form, f"import_{format_name}")(data)

    assert field.value == "imported"
    field.value = "edited again"
    assert document.form["name"].value == "edited again"


@pytest.mark.parametrize("on_disk", [False, True])
@pytest.mark.parametrize("has_existing_field", [False, True])
def test_merge_updates_retained_form(document, tmp_path, on_disk, has_existing_field):
    form = document.form
    original = _add_text(document) if has_existing_field else None
    with Document() as source:
        source.pages.add()
        _add_text(source, "imported", "from source")
        document.merge(source)

    expected = ["name", "imported"] if has_existing_field else ["imported"]
    assert len(form) == len(expected)
    assert [field.name for field in form] == expected
    assert [field.name for field in form.fields] == expected
    assert form["imported"].value == "from source"
    if original is not None:
        original.value = "still editable"
        assert form["name"].value == "still editable"
    with Document(_save(document, tmp_path, on_disk)) as reopened:
        assert [(field.name, field.value) for field in reopened.form] == [
            (field.name, field.value) for field in form
        ]


def test_deleted_field_rejects_value_changes(document):
    field = _add_text(document)
    document.form.remove_field("name")

    with pytest.raises(KeyError, match="no longer in the form"):
        field.value = "ghost"

    assert field.value == "before"
    assert len(document.form) == 0


@pytest.mark.parametrize("operation", ["value", "read_only", "remove"])
def test_deleted_field_cannot_change_a_replacement_with_the_same_name(
    document, operation
):
    field = _add_text(document)
    field.remove()
    replacement = _add_text(document, value="replacement")

    with pytest.raises(KeyError, match="no longer in the form"):
        if operation == "remove":
            field.remove()
        else:
            setattr(field, operation, True if operation == "read_only" else "ghost")

    assert document.form["name"].value == replacement.value == "replacement"
    assert not replacement.read_only


@pytest.mark.parametrize("target", ["document", "form"])
def test_flatten_invalidates_retained_fields(document, target):
    field = _add_text(document)
    form = document.form
    (form if target == "form" else document).flatten()
    assert len(form) == 0
    replacement = _add_text(document, value="replacement")

    with pytest.raises(KeyError, match="no longer in the form"):
        field.value = "ghost"

    assert replacement.value == "replacement"


def test_disposed_document_rejects_retained_field_value_changes(document):
    field = _add_text(document)
    document.dispose()

    with pytest.raises(AsposePdfException, match="disposed"):
        field.value = "ghost"

    assert field.value == "before"

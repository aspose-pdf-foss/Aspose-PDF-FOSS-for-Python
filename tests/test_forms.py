"""Tests for aspose_pdf forms functionality."""

import pytest

from aspose_pdf import Document
from aspose_pdf.forms import Form, UnsignedContent, UnsignedContentAbsorber
from aspose_pdf.generated.forms import (
    UnsignedContent as GenUnsignedContent,
)
from aspose_pdf.generated.forms import (
    UnsignedContentAbsorber as GenUnsignedContentAbsorber,
)


class DummyItem:
    """Dummy item for testing."""

    def __init__(self, name, is_signed=False, signed=None):
        self.name = name
        if signed is not None:
            self.signed = signed
        else:
            self.is_signed = is_signed


class DummyDocument:
    """Dummy document for testing."""

    def __init__(self, fields, annotations):
        self.form_fields = fields
        self.annotations = annotations


def test_extract_unsigned_content():
    """Test extracting unsigned content from document."""
    f1 = DummyItem("field1", is_signed=False)
    f2 = DummyItem("field2", is_signed=True)
    a1 = DummyItem("annot1", signed=False)
    a2 = DummyItem("annot2", signed=True)
    doc = DummyDocument([f1, f2], [a1, a2])
    absorber = UnsignedContentAbsorber(doc)
    result = absorber.extract()
    assert result.form_fields == [f1]
    assert result.annotations == [a1]

    f3 = DummyItem("field3", is_signed=True)
    a3 = DummyItem("annot3", signed=True)
    doc2 = DummyDocument([f3], [a3])
    absorber2 = UnsignedContentAbsorber(doc2)
    result2 = absorber2.extract()
    assert result2.form_fields == []
    assert result2.annotations == []


def test_the_compatibility_import_path_gives_the_real_classes():
    # generated.forms used to hold look-alikes whose extract() echoed its
    # keyword arguments back instead of reading a document.
    assert GenUnsignedContentAbsorber is UnsignedContentAbsorber
    assert GenUnsignedContent is UnsignedContent


def test_unsigned_content_absorber_initial_state_and_reset():
    """A new absorber has extracted nothing; reset returns it to that state."""
    from aspose_pdf import Document

    absorber = GenUnsignedContentAbsorber(Document())
    assert absorber.get_extracted() is None
    assert absorber.has_extracted() is False
    absorber.reset()
    absorber.reset()
    assert absorber.has_extracted() is False


def test_unsigned_content_absorber_extracts_from_a_document():
    """An unsigned document is unsigned throughout, and extract() records it."""
    from aspose_pdf import Document

    document = Document()
    document.pages.add()
    document.pages.add()
    absorber = GenUnsignedContentAbsorber(document)
    result = absorber.extract()
    assert isinstance(result, GenUnsignedContent)
    assert len(result.pages) == 2
    assert absorber.get_extracted() is result
    absorber.reset()
    assert absorber.get_extracted() is None


@pytest.mark.parametrize(
    "initial, to_add, to_remove, expected_len",
    [
        ([], ["p1"], [], 1),
        (["p1"], ["p2"], ["p1"], 1),
        (["p1", "p2"], [], ["p3"], 2),
    ],
)
def test_unsigned_content_add_remove_page(initial, to_add, to_remove, expected_len):
    content = UnsignedContent(pages=list(initial))
    for page in to_add:
        content.add_page(page)
    for page in to_remove:
        content.remove_page(page)
    assert len(content.pages) == expected_len


@pytest.mark.parametrize(
    "field_initial, to_add, to_remove, expected_len",
    [
        ([], ["f1"], [], 1),
        (["f1"], ["f2"], ["f1"], 1),
        (["f1", "f2"], [], ["f3"], 2),
    ],
)
def test_unsigned_content_add_remove_form_field(
    field_initial, to_add, to_remove, expected_len
):
    content = UnsignedContent(form_fields=list(field_initial))
    for field in to_add:
        content.add_form_field(field)
    for field in to_remove:
        content.remove_form_field(field)
    assert len(content.form_fields) == expected_len


@pytest.mark.parametrize(
    "ann_initial, to_add, to_remove, expected_len",
    [
        ([], ["a1"], [], 1),
        (["a1"], ["a2"], ["a1"], 1),
        (["a1", "a2"], [], ["a3"], 2),
    ],
)
def test_unsigned_content_add_remove_annotation(
    ann_initial, to_add, to_remove, expected_len
):
    content = UnsignedContent(annotations=list(ann_initial))
    for ann in to_add:
        content.add_annotation(ann)
    for ann in to_remove:
        content.remove_annotation(ann)
    assert len(content.annotations) == expected_len


def test_unsigned_content_reset_clears_all_collections_and_extra():
    content = UnsignedContent(
        pages=["p"],
        form_fields=["f"],
        annotations=["a"],
        extra="value",
    )
    assert content._extra
    content.reset()
    assert content.pages == []
    assert content.form_fields == []
    assert content.annotations == []
    assert content._extra == {}


def test_unsigned_content_repr_reflects_collection_sizes():
    content = UnsignedContent(pages=[1, 2, 3], form_fields=["a"], annotations=[])
    repr_str = repr(content)
    assert "pages=3" in repr_str
    assert "form_fields=1" in repr_str
    assert "annotations=0" in repr_str


def test_form_field_names_property():
    """Form.field_names returns list of field names matching [field.name for field in fields]."""
    from aspose_pdf import Document

    doc = Document()
    doc.pages.add()
    doc.form.add_text_field("Alpha", 1, (50, 700, 200, 720), value="a")
    doc.form.add_text_field("Beta", 1, (50, 680, 200, 700), value="b")
    doc.form.add_text_field("Gamma", 1, (50, 660, 200, 680), value="c")

    field_names = doc.form.field_names
    expected_names = [field.name for field in doc.form.fields]
    assert field_names == expected_names
    assert field_names == ["Alpha", "Beta", "Gamma"]


def test_form_field_names_immutability():
    """Modifying the returned list does not affect subsequent calls."""
    from aspose_pdf import Document

    doc = Document()
    doc.pages.add()
    doc.form.add_text_field("Single", 1, (50, 700, 200, 720), value="x")

    first_call = doc.form.field_names
    first_call.append("TAMPERED")
    second_call = doc.form.field_names
    assert second_call == ["Single"]
    assert second_call is not first_call


def test_form_field_names_empty():
    """Form.field_names returns empty list when no fields exist."""
    from aspose_pdf import Document

    doc = Document()
    doc.pages.add()

    assert doc.form.field_names == []
    assert doc.form.fields == []


def test_form_document_property():
    """Form.document returns the Document instance passed to the constructor."""

    doc1 = Document()
    doc1.pages.add()
    doc1.pages.add()

    doc2 = Document()
    doc2.pages.add()

    form1 = Form(doc1)
    form2 = Form(doc2)

    # Verify document property returns the exact Document instance
    assert form1.document is doc1
    assert form2.document is doc2

    # Verify page counts match
    assert len(form1.document.pages) == 2
    assert len(form2.document.pages) == 1

    # Verify document is read-only
    with pytest.raises(AttributeError):
        form1.document = doc2


def test_form_document_property_with_different_page_counts():
    """Form.document correctly identifies documents with different page counts."""

    doc1 = Document()
    doc1.pages.add()  # 1 page

    doc2 = Document()
    doc2.pages.add()
    doc2.pages.add()  # 2 pages

    form1 = Form(doc1)
    form2 = Form(doc2)

    # Verify the property correctly identifies each document by page count
    assert len(form1.document.pages) == 1
    assert len(form2.document.pages) == 2

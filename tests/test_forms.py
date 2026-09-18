"""Tests for aspose_pdf forms functionality."""

import pytest

from aspose_pdf.forms import UnsignedContent, UnsignedContentAbsorber
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

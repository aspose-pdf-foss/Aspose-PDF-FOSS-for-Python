"""Tests for aspose_pdf forms functionality."""

import pytest

from aspose_pdf.forms import UnsignedContentAbsorber, UnsignedContent
from aspose_pdf.generated.forms import (
    UnsignedContentAbsorber as GenUnsignedContentAbsorber,
    UnsignedContent as GenUnsignedContent,
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


def test_unsigned_content_absorber_initial_state():
    """A new absorber should have no extracted content."""
    absorber = GenUnsignedContentAbsorber()
    assert absorber.get_extracted() is None
    assert absorber.has_extracted() is False


def test_unsigned_content_absorber_reset_is_idempotent():
    """Calling reset multiple times must not raise."""
    absorber = GenUnsignedContentAbsorber()
    absorber.reset()
    assert absorber.get_extracted() is None
    absorber.reset()
    assert absorber.has_extracted() is False


def test_unsigned_content_absorber_extract_successful():
    """Test that extract returns an UnsignedContent instance."""
    absorber = GenUnsignedContentAbsorber()
    res = absorber.extract(pages=[1, 2], form_fields=["field1"], annotations=["ann1"])
    assert isinstance(res, GenUnsignedContent)
    assert len(res.pages) == 2


def test_unsigned_content_absorber_successful_extraction_simulated():
    """Manually set _extracted to simulate successful extraction."""
    absorber = GenUnsignedContentAbsorber()
    content = UnsignedContent(pages=["p1"], form_fields=["f1"], annotations=["a1"])
    absorber._extracted = content
    assert absorber.get_extracted() is content
    assert absorber.has_extracted() is True
    absorber.reset()
    assert absorber.get_extracted() is None
    assert absorber.has_extracted() is False


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

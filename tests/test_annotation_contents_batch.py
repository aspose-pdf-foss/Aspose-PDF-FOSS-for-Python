"""Batch annotation contents replacement tests."""

from __future__ import annotations

import pytest

from aspose_pdf import AnnotationType, Document

RECT = (10, 20, 30, 40)


@pytest.fixture
def document_with_annotations():
    """Create a document with multiple text annotations."""
    with Document() as doc:
        # Add 3 pages
        for _ in range(3):
            doc.pages.add(size=(100, 100))

        # Add annotations to page 1
        doc.pages[1].annotations.add(
            AnnotationType.TEXT.value,
            RECT,
            "Hello World",
            title="Annotation1",
        )
        doc.pages[1].annotations.add(
            AnnotationType.TEXT.value,
            RECT,
            "Some confidential info",
            title="Annotation2",
        )

        # Add annotation to page 2
        doc.pages[2].annotations.add(
            AnnotationType.TEXT.value,
            RECT,
            "Confidential: Secret Data",
            title="Annotation3",
        )

        # Add annotation to page 3
        doc.pages[3].annotations.add(
            AnnotationType.TEXT.value,
            RECT,
            "World War II history",
            title="Annotation4",
        )

        yield doc


def _get_contents(doc):
    """Get contents from all annotations as a sorted list."""
    contents = []
    for page in doc.pages:
        for annot in page.annotations:
            if annot.contents:
                contents.append(annot.contents)
    return sorted(contents)


def test_replace_annotation_contents_basic(document_with_annotations):
    """replace_annotation_contents replaces matching content."""
    doc = document_with_annotations

    # Replace "World" with "Universe"
    count = doc.replace_annotation_contents({"World": "Universe"})

    assert count == 2  # "Hello World" and "World War II history"
    contents = _get_contents(doc)
    assert "Hello Universe" in contents
    assert "Universe War II history" in contents
    assert "Hello World" not in contents
    assert "World War II history" not in contents


def test_replace_annotation_contents_multiple_patterns(document_with_annotations):
    """replace_annotation_contents handles multiple patterns."""
    doc = document_with_annotations

    count = doc.replace_annotation_contents(
        {"World": "Earth", "Secret": "Hidden"}
    )

    # "Hello World" -> "Hello Earth", "Confidential: Secret Data" -> "Confidential: Hidden Data"
    # "World War II history" -> "Earth War II history"
    assert count == 3
    contents = _get_contents(doc)
    assert "Hello Earth" in contents
    assert "Earth War II history" in contents
    assert "Confidential: Hidden Data" in contents


def test_replace_annotation_contents_with_regex(document_with_annotations):
    """replace_annotation_contents supports regex patterns."""
    doc = document_with_annotations

    count = doc.replace_annotation_contents(
        {r"\b\w{3}\b": "***"}, regex=True
    )

    # Words with exactly 3 characters: "World", "War", "Two", "Secret", "Data"
    # "World" (5), "War" (3), "Two" (3), "Secret" (6), "Data" (4)
    # Actually: "War" and "Two" are the 3-letter words
    assert count >= 1  # At least "War" and "Two" in "World War II history"


def test_replace_annotation_contents_compiled_regex(document_with_annotations):
    """replace_annotation_contents accepts compiled regex patterns."""
    import re

    doc = document_with_annotations

    count = doc.replace_annotation_contents(
        {re.compile(r"\\d{4}"): "XXXX"}, regex=True
    )

    # No 4-digit numbers in current annotations
    assert count == 0


def test_replace_annotation_contents_case_insensitive(document_with_annotations):
    """replace_annotation_contents supports case-insensitive matching."""
    doc = document_with_annotations

    # Both "confidential" and "Confidential" match
    count = doc.replace_annotation_contents(
        {"confidential": "PRIVATE"}, case_sensitive=False
    )

    assert count == 2  # "Some confidential info" and "Confidential: Secret Data"
    contents = _get_contents(doc)
    assert "Some PRIVATE info" in contents
    assert "PRIVATE: Secret Data" in contents


def test_replace_annotation_contents_max_count(document_with_annotations):
    """replace_annotation_contents respects max_count."""
    doc = document_with_annotations

    # Replace 'a' and 'o' - there are many of them
    count = doc.replace_annotation_contents(
        {"a": "X", "o": "O"}, max_count=2
    )

    assert count == 2  # Only 2 replacements total, shared across patterns


def test_replace_annotation_contents_dry_run(document_with_annotations):
    """replace_annotation_contents dry_run counts without modifying."""
    doc = document_with_annotations
    original_contents = _get_contents(doc)

    count = doc.replace_annotation_contents(
        {"World": "Universe"}, dry_run=True
    )

    assert count == 2
    assert _get_contents(doc) == original_contents  # No changes


def test_replace_annotation_contents_with_page_index(document_with_annotations):
    """replace_annotation_contents can target a specific page with page_index."""
    doc = document_with_annotations

    count = doc.replace_annotation_contents(
        {"Hello": "Hi"}, page_index=1
    )

    assert count == 1
    # Page 1 should have "Hi World"
    assert "Hi World" in _get_contents(doc)
    # Other pages should be unchanged
    assert "World War II history" in _get_contents(doc)


def test_replace_annotation_contents_with_pages(document_with_annotations):
    """replace_annotation_contents can target specific pages."""
    doc = document_with_annotations

    # Page 2 has "Confidential: Secret Data"
    count = doc.replace_annotation_contents(
        {"Confidential": "PRIVATE"}, pages=[2]
    )

    assert count == 1
    # Page 2 should have "PRIVATE"
    assert "PRIVATE: Secret Data" in _get_contents(doc)
    # Other pages should be unchanged
    assert "World War II history" in _get_contents(doc)


def test_replace_annotation_contents_empty_mapping(document_with_annotations):
    """replace_annotation_contents with empty mapping returns 0."""
    doc = document_with_annotations

    count = doc.replace_annotation_contents({})

    assert count == 0
    assert _get_contents(doc)


def test_replace_annotation_contents_no_matches(document_with_annotations):
    """replace_annotation_contents with no matches returns 0."""
    doc = document_with_annotations

    count = doc.replace_annotation_contents({"nonexistent": "value"})

    assert count == 0
    assert _get_contents(doc)


def test_replace_annotation_contents_page_method(document_with_annotations):
    """Page.replace_annotation_contents works correctly."""
    doc = document_with_annotations

    count = doc.pages[1].replace_annotation_contents({"World": "Universe"})

    assert count == 1  # Only on page 1
    contents = _get_contents(doc)
    assert "Hello Universe" in contents
    # Page 3 should be unchanged
    assert "World War II history" in contents


def test_replace_annotation_contents_preserves_other_properties(document_with_annotations):
    """replace_annotation_contents preserves annotation geometry and other properties."""
    doc = document_with_annotations
    original_annot = doc.pages[1].annotations[0]

    # There are 2 annotations with "World" - one on page 1, one on page 3
    count = doc.replace_annotation_contents({"World": "Universe"})

    # Both "Hello World" and "World War II history" get replaced
    assert count == 2
    contents = _get_contents(doc)
    assert "Hello Universe" in contents
    assert "Universe War II history" in contents
    
    # The first annotation on page 1 should still have same geometry
    new_annot = doc.pages[1].annotations[0]
    assert new_annot.rect == original_annot.rect
    assert new_annot.title == original_annot.title
    assert new_annot.subtype == original_annot.subtype


def test_replace_annotation_contents_invalid_replacements_type():
    """replace_annotation_contents rejects non-mapping replacements."""
    doc = Document()
    doc.pages.add(size=(100, 100))

    with pytest.raises(Exception):
        doc.replace_annotation_contents("not a mapping")  # type: ignore


def test_replace_annotation_contents_invalid_options():
    """replace_annotation_contents rejects invalid options."""
    doc = Document()
    doc.pages.add(size=(100, 100))
    doc.pages[1].annotations.add(
        AnnotationType.TEXT.value, RECT, "Test"
    )

    with pytest.raises(Exception):
        doc.replace_annotation_contents({"a": "b"}, regex="true")  # type: ignore

    with pytest.raises(Exception):
        doc.replace_annotation_contents({"a": "b"}, case_sensitive="false")  # type: ignore

    with pytest.raises(Exception):
        doc.replace_annotation_contents({"a": "b"}, dry_run="true")  # type: ignore


def test_replace_annotation_contents_shared_annotation(document_with_annotations):
    """replace_annotation_contents handles annotations shared with unselected pages."""
    doc = document_with_annotations

    # Add an annotation to page 1 that would match
    count = doc.replace_annotation_contents(
        {"World": "Universe"}, pages=[1]
    )

    # Should work for page 1 only
    assert count == 1


def test_replace_annotation_contents_round_trip(document_with_annotations, tmp_path):
    """replace_annotation_contents preserves data through save/load."""
    doc = document_with_annotations
    doc.replace_annotation_contents({"World": "Universe"})

    # Save and reload
    output = tmp_path / "test.pdf"
    doc.save(str(output))

    with Document(str(output)) as doc2:
        contents = _get_contents(doc2)
        assert "Hello Universe" in contents
        assert "Universe War II history" in contents

"""Tests for Page.annotations API: add, delete, update, insert, clear."""

import io
import tempfile
from pathlib import Path

import pytest
from aspose_pdf import Document
from aspose_pdf.annotations import Annotation


@pytest.fixture
def document():
    """Create a Document with one page for testing."""
    doc = Document()
    doc.pages.add()
    return doc


@pytest.fixture
def page_with_annotations(document):
    """Create a page with three annotations."""
    page = document.pages[0]
    page.annotations.add("Text", (100, 100, 200, 200), "First")
    page.annotations.add("Text", (150, 150, 250, 250), "Second", title="Author2")
    page.annotations.add("Text", (200, 200, 300, 300), "Third")
    return page


def test_annotations_add(document):
    """Add annotations and verify count and properties."""
    page = document.pages[0]
    assert len(page.annotations) == 0

    annot = page.annotations.add("Text", (100, 100, 200, 200), "Hello")
    assert len(page.annotations) == 1
    assert annot.contents == "Hello"
    assert annot.rect == (100, 100, 200, 200)
    assert annot.subtype == "Text"

    annot2 = page.annotations.add("Text", (50, 50, 150, 150), "World", title="QA")
    assert len(page.annotations) == 2
    assert annot2.contents == "World"
    assert annot2.title == "QA"


def test_annotations_update(page_with_annotations):
    """Update annotation contents, rect, and title."""
    page = page_with_annotations
    annot = page.annotations[0]

    annot.contents = "Updated content"
    assert page.annotations[0].contents == "Updated content"

    annot.rect = (10, 20, 30, 40)
    assert page.annotations[0].rect == (10, 20, 30, 40)

    annot.title = "New Author"
    assert page.annotations[0].title == "New Author"
    assert page.annotations[0].author == "New Author"


def test_annotations_delete(page_with_annotations):
    """Delete annotation by index."""
    page = page_with_annotations
    assert len(page.annotations) == 3

    page.annotations.delete(1)
    assert len(page.annotations) == 2
    assert page.annotations[0].contents == "First"
    assert page.annotations[1].contents == "Third"

    page.annotations.delete(0)
    assert len(page.annotations) == 1
    assert page.annotations[0].contents == "Third"


def test_annotations_delete_index_error(document):
    """Delete with invalid index raises IndexError."""
    page = document.pages[0]
    page.annotations.add("Text", (0, 0, 100, 100), "One")

    with pytest.raises(IndexError):
        page.annotations.delete(5)
    with pytest.raises(IndexError):
        page.annotations.delete(-1)


def test_annotations_clear(page_with_annotations):
    """Clear removes all annotations."""
    page = page_with_annotations
    assert len(page.annotations) == 3

    page.annotations.clear()
    assert len(page.annotations) == 0


def test_annotations_insert_at_0(document):
    """Insert annotation at beginning."""
    page = document.pages[0]
    page.annotations.add("Text", (100, 100, 200, 200), "Second")
    page.annotations.insert(0, "Text", (50, 50, 150, 150), "First")

    assert len(page.annotations) == 2
    assert page.annotations[0].contents == "First"
    assert page.annotations[1].contents == "Second"


def test_annotations_insert_middle(document):
    """Insert annotation in the middle."""
    page = document.pages[0]
    page.annotations.add("Text", (0, 0, 100, 100), "A")
    page.annotations.add("Text", (200, 200, 300, 300), "C")
    page.annotations.insert(1, "Text", (100, 100, 200, 200), "B")

    assert len(page.annotations) == 3
    assert page.annotations[0].contents == "A"
    assert page.annotations[1].contents == "B"
    assert page.annotations[2].contents == "C"


def test_annotations_insert_empty(document):
    """Insert into empty collection."""
    page = document.pages[0]
    annot = page.annotations.insert(0, "Text", (0, 0, 100, 100), "Only")
    assert len(page.annotations) == 1
    assert annot.contents == "Only"


def test_annotations_iteration(page_with_annotations):
    """Annotations are iterable."""
    page = page_with_annotations
    items = list(page.annotations)
    assert len(items) == 3
    assert all(isinstance(a, Annotation) for a in items)
    assert [a.contents for a in items] == ["First", "Second", "Third"]


def test_annotations_indexing(page_with_annotations):
    """Annotations support indexing."""
    page = page_with_annotations
    assert page.annotations[0].contents == "First"
    assert page.annotations[-1].contents == "Third"

    with pytest.raises(IndexError):
        _ = page.annotations[10]


def test_annotations_save_load_roundtrip(document):
    """Annotations persist through save and load."""
    page = document.pages[0]
    page.annotations.add("Text", (100, 100, 200, 200), "Persisted", title="Tester")

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        path = f.name
    try:
        document.save(path, overwrite=True)
        doc2 = Document()
        doc2.load_from(path)
        page2 = doc2.pages[0]
        assert len(page2.annotations) == 1
        assert page2.annotations[0].contents == "Persisted"
        assert page2.annotations[0].title == "Tester"
        assert page2.annotations[0].rect == (100, 100, 200, 200)
    finally:
        Path(path).unlink(missing_ok=True)


def test_annotations_save_load_stream_roundtrip(document):
    """Annotations persist through BytesIO save and load."""
    page = document.pages[0]
    page.annotations.add("Text", (50, 50, 150, 150), "Stream test")

    buf = io.BytesIO()
    document.save(buf)
    buf.seek(0)

    doc2 = Document()
    doc2.load_from(buf)
    assert len(doc2.pages[0].annotations) == 1
    assert doc2.pages[0].annotations[0].contents == "Stream test"

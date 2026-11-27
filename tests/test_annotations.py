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
    page = document.pages[1]
    page.annotations.add("Text", (100, 100, 200, 200), "First")
    page.annotations.add("Text", (150, 150, 250, 250), "Second", title="Author2")
    page.annotations.add("Text", (200, 200, 300, 300), "Third")
    return page


def test_annotations_add(document):
    """Add annotations and verify count and properties."""
    page = document.pages[1]
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
    page = document.pages[1]
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
    page = document.pages[1]
    page.annotations.add("Text", (100, 100, 200, 200), "Second")
    page.annotations.insert(0, "Text", (50, 50, 150, 150), "First")

    assert len(page.annotations) == 2
    assert page.annotations[0].contents == "First"
    assert page.annotations[1].contents == "Second"


def test_annotations_insert_middle(document):
    """Insert annotation in the middle."""
    page = document.pages[1]
    page.annotations.add("Text", (0, 0, 100, 100), "A")
    page.annotations.add("Text", (200, 200, 300, 300), "C")
    page.annotations.insert(1, "Text", (100, 100, 200, 200), "B")

    assert len(page.annotations) == 3
    assert page.annotations[0].contents == "A"
    assert page.annotations[1].contents == "B"
    assert page.annotations[2].contents == "C"


def test_annotations_insert_empty(document):
    """Insert into empty collection."""
    page = document.pages[1]
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
    page = document.pages[1]
    page.annotations.add("Text", (100, 100, 200, 200), "Persisted", title="Tester")

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        path = f.name
    try:
        document.save(path, overwrite=True)
        doc2 = Document()
        doc2.load_from(path)
        page2 = doc2.pages[1]
        assert len(page2.annotations) == 1
        assert page2.annotations[0].contents == "Persisted"
        assert page2.annotations[0].title == "Tester"
        assert page2.annotations[0].rect == (100, 100, 200, 200)
    finally:
        Path(path).unlink(missing_ok=True)


def test_annotations_save_load_stream_roundtrip(document):
    """Annotations persist through BytesIO save and load."""
    page = document.pages[1]
    page.annotations.add("Text", (50, 50, 150, 150), "Stream test")

    buf = io.BytesIO()
    document.save(buf)
    buf.seek(0)

    doc2 = Document()
    doc2.load_from(buf)
    assert len(doc2.pages[1].annotations) == 1
    assert doc2.pages[1].annotations[0].contents == "Stream test"


def test_annotations_add_copy_existing(document):
    """Add a copy of an existing annotation to another page."""
    source_page = document.pages[1]
    annot = source_page.annotations.add(
        "Text", (100, 100, 200, 200), "Source annotation", title="Source"
    )

    # Add a second page and copy the annotation
    dest_page = document.pages.add()
    copied_annot = dest_page.annotations.add(annot, consider_rotation=False)

    assert len(dest_page.annotations) == 1
    assert copied_annot.subtype == "Text"
    assert copied_annot.contents == "Source annotation"
    assert copied_annot.title == "Source"
    assert copied_annot.rect == (100, 100, 200, 200)


def test_annotations_add_copy_preserves_properties(document):
    """Add a copy of an annotation preserves type-specific properties."""
    source_page = document.pages[1]

    # Add an annotation with properties
    annot = source_page.annotations.add(
        "Highlight",
        (50, 50, 150, 150),
        "Highlighted",
        properties={"C": [1, 0, 0], "QuadPoints": [50, 150, 150, 150, 50, 50, 150, 50]},
    )

    dest_page = document.pages.add()
    copied_annot = dest_page.annotations.add(annot, consider_rotation=False)

    assert len(dest_page.annotations) == 1
    assert copied_annot.subtype == "Highlight"
    assert copied_annot.contents == "Highlighted"
    props = copied_annot.properties
    assert props.get("C") == [1, 0, 0]


def test_annotations_add_copy_with_rotation(document):
    """Add a copy with consider_rotation=True transforms the rectangle."""
    source_page = document.pages[1]
    annot = source_page.annotations.add("Text", (100, 100, 200, 200), "Test")

    # Set the destination page to have a 90-degree rotation
    dest_page = document.pages.add()
    dest_page.rotation = 90

    # Copy with consider_rotation=True
    copied_annot = dest_page.annotations.add(annot, consider_rotation=True)

    # The rectangle should be transformed for 90-degree rotation
    assert len(dest_page.annotations) == 1
    assert copied_annot.contents == "Test"


def test_annotations_add_copy_rotation_transforms_coordinates(document):
    """Add a copy with consider_rotation=True produces correct transformed rectangle.

    For a 90° rotation, the transformation is:
    - (x, y) -> (y, w - x)
    Where w is the page width (from MediaBox).

    With a standard page size and source rect (100, 100, 200, 200):
    - Corner (100, 100) -> (100, w - 100)
    - Corner (100, 200) -> (200, w - 100)
    - Corner (200, 100) -> (100, w - 200)
    - Corner (200, 200) -> (200, w - 200)
    The transformed rectangle bounds are computed from these four points.
    """
    source_page = document.pages[1]
    # Use a known rectangle for clear expected values
    annot = source_page.annotations.add("Text", (100, 100, 200, 200), "Test")

    # Set the destination page to have a 90-degree rotation
    dest_page = document.pages.add()
    dest_page.rotation = 90

    # Copy with consider_rotation=True
    copied_annot = dest_page.annotations.add(annot, consider_rotation=True)

    # Verify the rectangle is transformed correctly
    rect = copied_annot.rect
    assert isinstance(rect, tuple)
    assert len(rect) == 4

    # Get page dimensions for expected value calculation
    mb = dest_page.media_box
    w = mb[2] - mb[0]  # page width after 90° rotation

    # For 90° rotation: (x, y) -> (y, w - x)
    # Source rect: (100, 100, 200, 200)
    # Corners transform to:
    # (100, 100) -> (100, w - 100)
    # (100, 200) -> (200, w - 100)
    # (200, 100) -> (100, w - 200)
    # (200, 200) -> (200, w - 200)
    # Bounds: (min_x=100, min_y=w-200, max_x=200, max_y=w-100)
    expected = (100, w - 200, 200, w - 100)
    assert rect == expected, f"Expected {expected}, got {rect}"


def test_annotations_add_copy_override_title(document):
    """Add a copy allows overriding the title."""
    source_page = document.pages[1]
    annot = source_page.annotations.add(
        "Text", (100, 100, 200, 200), "Test", title="Original"
    )

    dest_page = document.pages.add()
    # Copy with override title
    copied_annot = dest_page.annotations.add(annot, consider_rotation=False, title="New Title")

    assert copied_annot.title == "New Title"
    assert copied_annot.contents == "Test"


def test_annotations_add_copy_errors_with_wrong_args(document):
    """Add with annotation as first arg raises if rect/contents also given."""
    source_page = document.pages[1]
    annot = source_page.annotations.add("Text", (100, 100, 200, 200), "Test")

    dest_page = document.pages.add()

    # When first arg is Annotation, second arg (rect) must be bool or None
    # Test that passing a non-bool second arg raises an error
    with pytest.raises(TypeError, match="bool or None"):
        dest_page.annotations.add(annot, (100, 100, 200, 200))


def test_annotations_add_subtype_not_str(document):
    """Add with non-str, non-Annotation first arg raises TypeError."""
    page = document.pages[1]

    with pytest.raises(TypeError, match="First argument must be str"):
        page.annotations.add(123, (100, 100, 200, 200), "Test")


def test_annotations_add_missing_rect(document):
    """Add with subtype but no rect raises TypeError."""
    page = document.pages[1]

    with pytest.raises(TypeError, match="must have rect"):
        page.annotations.add("Text", None, "Test")


def test_annotations_add_missing_contents(document):
    """Add with subtype but no contents raises TypeError."""
    page = document.pages[1]

    with pytest.raises(TypeError, match="must have contents"):
        page.annotations.add("Text", (100, 100, 200, 200), None)


def test_annotations_add_with_rotation_appearance(document):
    """Add copy of annotation with appearance preserves it."""
    source_page = document.pages[1]
    annot = source_page.annotations.add(
        "Text", (100, 100, 200, 200), "Test", appearance_normal=b"AP"
    )

    dest_page = document.pages.add()
    copied_annot = dest_page.annotations.add(annot, consider_rotation=False)

    assert len(dest_page.annotations) == 1
    assert copied_annot.appearance_normal == b"AP"


def test_annotations_add_copy_positional_rotation(document):
    """Add copy with positional consider_rotation (reference API compatibility)."""
    source_page = document.pages[1]
    annot = source_page.annotations.add("Text", (100, 100, 200, 200), "Test")

    dest_page = document.pages.add()
    # Reference API: add(annotation, consider_rotation) where second is positional
    copied_annot = dest_page.annotations.add(annot, True)

    assert len(dest_page.annotations) == 1
    assert copied_annot.contents == "Test"

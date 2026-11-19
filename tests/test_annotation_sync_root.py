"""Tests for AnnotationCollection.sync_root property."""

import pytest

from aspose_pdf import Document, PageSize


class TestAnnotationCollectionSyncRoot:
    """Tests for AnnotationCollection.sync_root property."""

    @pytest.fixture
    def document_with_annotations(self) -> Document:
        """Create a document with a page and annotations."""
        doc = Document()
        doc.pages.add(PageSize.A4)
        return doc

    def test_sync_root_property_exists(self, document_with_annotations: Document) -> None:
        """sync_root property exists on AnnotationCollection."""
        page = document_with_annotations.pages[1]
        annotations = page.annotations

        assert hasattr(annotations, "sync_root")

    def test_sync_root_returns_self(self, document_with_annotations: Document) -> None:
        """sync_root returns the AnnotationCollection instance itself."""
        page = document_with_annotations.pages[1]
        annotations = page.annotations

        assert annotations.sync_root is annotations

    def test_sync_root_is_read_only(
        self, document_with_annotations: Document
    ) -> None:
        """Attempting to assign to sync_root raises AttributeError."""
        page = document_with_annotations.pages[1]
        annotations = page.annotations

        with pytest.raises(AttributeError):
            annotations.sync_root = object()

    def test_sync_root_returns_distinct_self_per_instance(
        self, document_with_annotations: Document
    ) -> None:
        """Each AnnotationCollection instance has its own sync_root."""
        page1 = document_with_annotations.pages[1]
        annotations1 = page1.annotations

        page2 = document_with_annotations.pages.add(PageSize.A4)
        annotations2 = page2.annotations

        # sync_root should return self for each distinct collection
        assert annotations1.sync_root is annotations1
        assert annotations2.sync_root is annotations2
        assert annotations1.sync_root is not annotations2.sync_root

    def test_sync_root_works_with_empty_collection(
        self, document_with_annotations: Document
    ) -> None:
        """sync_root works correctly even with no annotations."""
        page = document_with_annotations.pages[1]
        annotations = page.annotations

        # Collection is empty but sync_root should still work
        assert len(annotations) == 0
        assert annotations.sync_root is annotations

    def test_sync_root_works_after_operations(
        self, document_with_annotations: Document
    ) -> None:
        """sync_root still returns self after modifying the collection."""
        page = document_with_annotations.pages[1]
        annotations = page.annotations

        # Add some annotations
        annotations.add_text((100, 600, 200, 650), "Note 1")
        annotations.add_text((100, 500, 200, 550), "Note 2")

        # sync_root should still return self
        assert annotations.sync_root is annotations

        # Verify each page's annotations have distinct sync_root
        page2 = document_with_annotations.pages.add(PageSize.A4)
        annotations2 = page2.annotations
        assert annotations.sync_root is not annotations2.sync_root

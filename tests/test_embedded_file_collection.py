"""Tests for the EmbeddedFileCollection class."""

from __future__ import annotations

import pytest

from aspose_pdf import (
    Document,
    EmbeddedFileCollection,
    FileAttachmentAnnotation,
    FileSpecification,
)


@pytest.fixture
def document_with_file_attachments():
    """Create a document with multiple file attachments."""
    with Document() as doc:
        doc.pages.add(size=(300, 200))
        # Add several file attachments with different titles
        doc.pages[1].annotations.add_file_attachment(
            (20, 100, 44, 124),
            FileSpecification("first.txt", b"First attachment"),
            title="first",
        )
        doc.pages[1].annotations.add_file_attachment(
            (50, 100, 74, 124),
            FileSpecification("second.txt", b"Second attachment"),
            title="second",
        )
        doc.pages[1].annotations.add_file_attachment(
            (80, 100, 104, 124),
            FileSpecification("third.txt", b"Third attachment"),
            title="third",
        )
        yield doc


@pytest.fixture
def document_without_file_attachments():
    """Create a document without file attachments."""
    with Document() as doc:
        doc.pages.add(size=(300, 200))
        # Add a text annotation (not a file attachment)
        doc.pages[1].annotations.add(
            "Text", (20, 100, 44, 124), "A text annotation"
        )
        yield doc


class TestEmbeddedFileCollection:
    """Tests for EmbeddedFileCollection."""

    def test_file_attachments_property_returns_collection(self, document_with_file_attachments):
        """Verify Page.file_attachments returns an EmbeddedFileCollection."""
        page = document_with_file_attachments.pages[1]
        collection = page.file_attachments
        assert isinstance(collection, EmbeddedFileCollection)

    def test_keys_property_returns_list_of_strings(self, document_with_file_attachments):
        """Verify keys returns a list of strings (the titles)."""
        page = document_with_file_attachments.pages[1]
        collection = page.file_attachments
        keys = collection.keys
        assert isinstance(keys, list)
        assert all(isinstance(k, str) for k in keys)
        assert keys == ["first", "second", "third"]

    def test_keys_empty_when_no_file_attachments(self, document_without_file_attachments):
        """Verify keys is empty when there are no file attachments."""
        page = document_without_file_attachments.pages[1]
        collection = page.file_attachments
        keys = collection.keys
        assert keys == []

    def test_iterate_over_file_attachments(self, document_with_file_attachments):
        """Verify iteration yields FileAttachmentAnnotation objects."""
        page = document_with_file_attachments.pages[1]
        collection = page.file_attachments
        annotations = list(collection)
        assert len(annotations) == 3
        from aspose_pdf import FileAttachmentAnnotation
        for ann in annotations:
            assert isinstance(ann, FileAttachmentAnnotation)

    def test_index_access(self, document_with_file_attachments):
        """Verify indexing returns FileAttachmentAnnotation objects."""
        page = document_with_file_attachments.pages[1]
        collection = page.file_attachments
        first = collection[0]
        assert isinstance(first, FileAttachmentAnnotation)
        assert first.title == "first"

    def test_negative_index_access(self, document_with_file_attachments):
        """Verify negative indexing works like a Python list."""
        page = document_with_file_attachments.pages[1]
        collection = page.file_attachments
        last = collection[-1]
        assert isinstance(last, FileAttachmentAnnotation)
        assert last.title == "third"

    def test_index_out_of_range_raises(self, document_with_file_attachments):
        """Verify IndexError is raised for out-of-range indices."""
        page = document_with_file_attachments.pages[1]
        collection = page.file_attachments
        with pytest.raises(IndexError):
            _ = collection[10]
        with pytest.raises(IndexError):
            _ = collection[-10]

    def test_len_returns_count(self, document_with_file_attachments):
        """Verify len() returns the number of file attachments."""
        page = document_with_file_attachments.pages[1]
        collection = page.file_attachments
        assert len(collection) == 3

    def test_len_zero_when_no_file_attachments(self, document_without_file_attachments):
        """Verify len() returns 0 when there are no file attachments."""
        page = document_without_file_attachments.pages[1]
        collection = page.file_attachments
        assert len(collection) == 0

    def test_delete_by_key_removes_annotation(self, document_with_file_attachments):
        """Verify delete_by_key removes the annotation with matching title."""
        page = document_with_file_attachments.pages[1]
        collection = page.file_attachments
        # Delete the "second" attachment
        collection.delete_by_key("second")
        # Check that it's gone
        assert collection.keys == ["first", "third"]
        # Check that len is reduced
        assert len(collection) == 2

    def test_delete_by_key_raises_key_error(self, document_with_file_attachments):
        """Verify delete_by_key raises KeyError for non-existent key."""
        page = document_with_file_attachments.pages[1]
        collection = page.file_attachments
        with pytest.raises(KeyError):
            collection.delete_by_key("nonexistent")

    def test_delete_by_key_preserves_order(self, document_with_file_attachments):
        """Verify deleting preserves the order of remaining annotations."""
        page = document_with_file_attachments.pages[1]
        collection = page.file_attachments
        collection.delete_by_key("second")
        # Check that we can still access by index
        assert collection[0].title == "first"
        assert collection[1].title == "third"

    def test_del_item_by_index(self, document_with_file_attachments):
        """Verify del collection[index] works."""
        page = document_with_file_attachments.pages[1]
        collection = page.file_attachments
        del collection[1]
        assert collection.keys == ["first", "third"]

    def test_del_item_negative_index(self, document_with_file_attachments):
        """Verify negative index deletion works."""
        page = document_with_file_attachments.pages[1]
        collection = page.file_attachments
        del collection[-1]
        assert collection.keys == ["first", "second"]

    def test_del_item_out_of_range_raises(self, document_with_file_attachments):
        """Verify IndexError for out-of-range deletion."""
        page = document_with_file_attachments.pages[1]
        collection = page.file_attachments
        with pytest.raises(IndexError):
            del collection[10]

    def test_file_attachment_access_after_delete(self, document_with_file_attachments):
        """Verify we can still access remaining file attachments after deletion."""
        page = document_with_file_attachments.pages[1]
        collection = page.file_attachments
        collection.delete_by_key("second")
        # Check that we can still access the first one
        first = collection[0]
        assert first.title == "first"
        assert first.file_specification.name == "first.txt"
        # Check that the third one is now at index 1
        third = collection[1]
        assert third.title == "third"
        assert third.file_specification.name == "third.txt"

    def test_collection_is_page_specific(self):
        """Verify each page has its own collection."""
        with Document() as doc:
            # Add page with file attachment
            doc.pages.add(size=(300, 200))
            doc.pages[1].annotations.add_file_attachment(
                (20, 100, 44, 124),
                FileSpecification("page1.txt", b"Page 1 file"),
                title="page1",
            )
            # Add another page with different file attachment
            doc.pages.add(size=(300, 200))
            doc.pages[2].annotations.add_file_attachment(
                (20, 100, 44, 124),
                FileSpecification("page2.txt", b"Page 2 file"),
                title="page2",
            )

            page1 = doc.pages[1]
            page2 = doc.pages[2]

            assert page1.file_attachments.keys == ["page1"]
            assert page2.file_attachments.keys == ["page2"]

    def test_file_attachment_contents_after_round_trip(self, document_with_file_attachments, tmp_path):
        """Verify file attachments survive a save/load round trip."""
        path = tmp_path / "test.pdf"
        document_with_file_attachments.save(path)

        with Document(path) as reopened:
            page = reopened.pages[1]
            collection = page.file_attachments
            assert collection.keys == ["first", "second", "third"]
            # Verify we can access the file specifications
            assert collection[0].file_specification.name == "first.txt"
            assert collection[1].file_specification.name == "second.txt"
            assert collection[2].file_specification.name == "third.txt"

    def test_disposed_document_rejects(self, document_with_file_attachments):
        """Verify operations on disposed document raise."""
        page = document_with_file_attachments.pages[1]
        collection = page.file_attachments
        document_with_file_attachments.dispose()
        with pytest.raises(Exception):  # AsposePdfException
            _ = collection.keys
        with pytest.raises(Exception):
            len(collection)

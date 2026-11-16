"""Tests for iterating over a ``Document`` directly (``for page in document``)."""

from __future__ import annotations

import pytest

from aspose_pdf import Document
from aspose_pdf.exceptions import AsposePdfException
from aspose_pdf.pages import Page


class TestDocumentIter:
    """Tests for Document.__iter__()."""

    def test_iterate_new_document_yields_pages_in_order(self):
        """Iterating a document yields its pages in one-based order."""
        with Document() as doc:
            doc.pages.add()
            doc.pages.add()
            doc.pages.add()

            assert [page.number for page in doc] == [1, 2, 3]

    def test_iterate_matches_pages_collection(self):
        """Iterating the document equals iterating its pages collection."""
        with Document("tests/fixtures_4pages.pdf") as doc:
            from_document = [page.number for page in doc]
            from_collection = [page.number for page in doc.pages]

            assert from_document == from_collection == [1, 2, 3, 4]

    def test_iter_yields_page_objects(self):
        """``iter(document)`` is an iterator over ``Page`` objects."""
        with Document("tests/fixtures_4pages.pdf") as doc:
            iterator = iter(doc)
            first = next(iterator)

            assert isinstance(first, Page)
            assert first.number == 1
            assert [page.number for page in iterator] == [2, 3, 4]

    def test_iterate_empty_document(self):
        """A document without pages iterates to nothing."""
        with Document() as doc:
            assert list(doc) == []
            assert len(doc) == 0

    def test_len_unchanged_by_iteration(self):
        """``len(document)`` keeps returning the page count."""
        with Document("tests/fixtures_4pages.pdf") as doc:
            assert len(doc) == 4
            assert len(list(doc)) == 4
            assert len(doc) == 4

    def test_iterate_disposed_document_raises(self):
        """Iterating a disposed document raises like other operations."""
        doc = Document("tests/fixtures_4pages.pdf")
        doc.dispose()

        with pytest.raises(AsposePdfException, match="disposed"):
            list(doc)

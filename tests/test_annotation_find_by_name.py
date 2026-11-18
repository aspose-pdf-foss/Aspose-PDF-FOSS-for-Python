"""Tests for AnnotationCollection.find_by_name."""

import pytest

from aspose_pdf import Document
from aspose_pdf.annotations import Annotation


class TestFindByName:
    """Tests for AnnotationCollection.find_by_name method."""

    @pytest.fixture
    def document_with_named_annotations(self):
        """Create a document with several named annotations."""
        doc = Document()
        page = doc.pages.add()
        annos = page.annotations

        # Add annotations with specific names
        annos.add("Text", (100, 600, 200, 650), "First", properties={"NM": "annot-alpha"})
        annos.add("Text", (100, 500, 200, 550), "Second", properties={"NM": "annot-beta"})
        annos.add("Text", (100, 400, 200, 450), "Third")  # No name
        annos.add("Text", (100, 300, 200, 350), "Fourth", properties={"NM": "annot-gamma"})

        return doc, annos

    def test_find_by_name_returns_annotation_when_found(self, document_with_named_annotations):
        """find_by_name returns the matching annotation when name exists."""
        _doc, annos = document_with_named_annotations

        found = annos.find_by_name("annot-alpha")

        assert found is not None
        assert isinstance(found, Annotation)
        assert found.get_property("NM") == "annot-alpha"
        assert found.contents == "First"

    def test_find_by_name_returns_annotation_for_any_name(self, document_with_named_annotations):
        """find_by_name works with any annotation name."""
        _doc, annos = document_with_named_annotations

        found_beta = annos.find_by_name("annot-beta")
        found_gamma = annos.find_by_name("annot-gamma")

        assert found_beta is not None
        assert found_beta.get_property("NM") == "annot-beta"
        assert found_gamma is not None
        assert found_gamma.get_property("NM") == "annot-gamma"

    def test_find_by_name_returns_none_when_not_found(self, document_with_named_annotations):
        """find_by_name returns None when no annotation has the name."""
        _doc, annos = document_with_named_annotations

        result = annos.find_by_name("nonexistent")

        assert result is None

    def test_find_by_name_returns_none_for_empty_string(self, document_with_named_annotations):
        """find_by_name returns None for empty string (no annotation has empty name)."""
        _doc, annos = document_with_named_annotations

        result = annos.find_by_name("")

        assert result is None

    def test_find_by_name_case_sensitive(self, document_with_named_annotations):
        """find_by_name performs case-sensitive match."""
        _doc, annos = document_with_named_annotations

        # Exact match works
        found = annos.find_by_name("annot-alpha")
        assert found is not None

        # Different case doesn't match
        result = annos.find_by_name("ANNOT-ALPHA")
        assert result is None

        result = annos.find_by_name("Annot-Alpha")
        assert result is None

    def test_find_by_name_returns_annotation_owned_by_page(self, document_with_named_annotations):
        """find_by_name returns an annotation owned by the page (modifications affect PDF)."""
        _doc, annos = document_with_named_annotations

        found = annos.find_by_name("annot-alpha")

        # Modifications should affect the PDF - change contents and verify
        found.contents = "Modified via find_by_name"

        # Verify the change persisted by getting a fresh annotation
        # (new instance but same underlying data)
        found_again = annos.find_by_name("annot-alpha")
        assert found_again is not None
        assert found_again.contents == "Modified via find_by_name"

        # Verify via index as well
        for i, ann in enumerate(annos):
            if ann.get_property("NM") == "annot-alpha":
                assert ann.contents == "Modified via find_by_name"
                break

    def test_find_by_name_raises_value_error_for_none(self, document_with_named_annotations):
        """find_by_name raises ValueError when name is None."""
        _doc, annos = document_with_named_annotations

        with pytest.raises(ValueError, match="name must be a string"):
            annos.find_by_name(None)

    def test_find_by_name_raises_value_error_for_non_string(self, document_with_named_annotations):
        """find_by_name raises ValueError when name is not a string."""
        _doc, annos = document_with_named_annotations

        with pytest.raises(ValueError, match="name must be a string"):
            annos.find_by_name(123)

        with pytest.raises(ValueError, match="name must be a string"):
            annos.find_by_name(["annot-alpha"])

        with pytest.raises(ValueError, match="name must be a string"):
            annos.find_by_name({"NM": "annot-alpha"})

    def test_find_by_name_on_empty_collection(self):
        """find_by_name returns None for empty annotation collection."""
        doc = Document()
        page = doc.pages.add()
        annos = page.annotations

        result = annos.find_by_name("any-name")

        assert result is None

    def test_find_by_name_modifications_affect_pdf(self, document_with_named_annotations):
        """Modifications to annotation returned by find_by_name affect the PDF."""
        _doc, annos = document_with_named_annotations

        found = annos.find_by_name("annot-alpha")
        found.contents = "Modified via find_by_name"

        # Retrieve the same annotation again to verify change persisted
        found_again = annos.find_by_name("annot-alpha")
        assert found_again is not None
        assert found_again.contents == "Modified via find_by_name"

        # Verify via index as well
        for i, ann in enumerate(annos):
            if ann.get_property("NM") == "annot-alpha":
                assert ann.contents == "Modified via find_by_name"
                break

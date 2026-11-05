"""Tests for page transitions (/Trans) and duration (/Dur)."""
import io

import pytest

from aspose_pdf.document import Document


@pytest.fixture
def document():
    return Document()

@pytest.fixture
def multi_page_document():
    doc = Document()
    for i in range(3):
        page = doc.pages.add()
        page.add_text(f"PAGE-{i+1}", x=20, y=700)
    return doc

class TestPageTransition:
    def test_initial_transition_is_none(self, document):
        page = document.pages.add()
        assert page.transition is None

    def test_set_transition_with_type_only(self, document):
        page = document.pages.add()
        transition = {"Type": "Split"}
        page.transition = transition
        assert page.transition == {"Type": "Split"}

    def test_set_transition_with_all_fields(self, document):
        page = document.pages.add()
        transition = {"Type": "Blinds", "D": 1.5, "DI": 90, "M": "H"}
        page.transition = transition
        assert page.transition == transition

    def test_set_transition_with_direction_as_string(self, document):
        page = document.pages.add()
        transition = {"Type": "Split", "DI": "H"}
        page.transition = transition
        assert page.transition["DI"] == "H"

    def test_set_transition_different_types(self, document):
        page = document.pages.add()
        for trans_type in ["Split", "Blinds", "Box", "Wipe", "Dissolve", "Glitter"]:
            page.transition = {"Type": trans_type}
            assert page.transition["Type"] == trans_type

    def test_remove_transition_with_none(self, document):
        page = document.pages.add()
        page.transition = {"Type": "Split"}
        assert page.transition is not None
        page.transition = None
        assert page.transition is None

    def test_transition_round_trip(self, multi_page_document):
        page = multi_page_document.pages[1]
        transition = {"Type": "Blinds", "D": 2.0, "M": "V"}
        page.transition = transition
        output = io.BytesIO()
        multi_page_document.save(output)
        output.seek(0)
        reloaded = Document(output)
        assert reloaded.pages[1].transition == transition

    def test_transition_per_page(self, multi_page_document):
        multi_page_document.pages[1].transition = {"Type": "Split"}
        multi_page_document.pages[2].transition = {"Type": "Blinds"}
        multi_page_document.pages[3].transition = {"Type": "Wipe"}
        assert multi_page_document.pages[1].transition["Type"] == "Split"
        assert multi_page_document.pages[2].transition["Type"] == "Blinds"
        assert multi_page_document.pages[3].transition["Type"] == "Wipe"

class TestPageDuration:
    def test_initial_duration_is_none(self, document):
        page = document.pages.add()
        assert page.duration is None

    def test_set_duration(self, document):
        page = document.pages.add()
        page.duration = 2.5
        assert page.duration == 2.5

    def test_set_duration_integer(self, document):
        page = document.pages.add()
        page.duration = 3
        assert page.duration == 3.0

    def test_set_duration_zero(self, document):
        page = document.pages.add()
        page.duration = 0
        assert page.duration == 0

    def test_remove_duration_with_none(self, document):
        page = document.pages.add()
        page.duration = 2.0
        assert page.duration == 2.0
        page.duration = None
        assert page.duration is None

    def test_duration_round_trip(self, multi_page_document):
        page = multi_page_document.pages[1]
        page.duration = 3.5
        output = io.BytesIO()
        multi_page_document.save(output)
        output.seek(0)
        reloaded = Document(output)
        assert reloaded.pages[1].duration == 3.5

    def test_duration_per_page(self, multi_page_document):
        multi_page_document.pages[1].duration = 1.0
        multi_page_document.pages[2].duration = 2.5
        multi_page_document.pages[3].duration = 3.0
        assert multi_page_document.pages[1].duration == 1.0
        assert multi_page_document.pages[2].duration == 2.5
        assert multi_page_document.pages[3].duration == 3.0

    def test_combined_transition_and_duration(self, document):
        page = document.pages.add()
        page.transition = {"Type": "Split", "M": "H"}
        page.duration = 2.0
        assert page.transition == {"Type": "Split", "M": "H"}
        assert page.duration == 2.0

    def test_transition_with_duration_field(self, document):
        page = document.pages.add()
        page.transition = {"Type": "Split", "D": 0.5}
        assert page.transition["D"] == 0.5


class TestTransitionValidation:
    """Tests for transition input validation."""

    def test_missing_type_key_raises(self, document):
        """Transition without Type key should raise."""
        page = document.pages.add()
        with pytest.raises(Exception) as exc_info:
            page.transition = {"D": 2.0, "M": "H"}
        assert "Type" in str(exc_info.value)

    def test_empty_dict_raises(self, document):
        """Empty transition dict should raise."""
        page = document.pages.add()
        with pytest.raises(Exception) as exc_info:
            page.transition = {}
        assert "Type" in str(exc_info.value)

    def test_invalid_transition_type_raises(self, document):
        """Unknown transition type should raise."""
        page = document.pages.add()
        with pytest.raises(Exception) as exc_info:
            page.transition = {"Type": "InvalidTransition"}
        assert "Invalid" in str(exc_info.value) or "Valid" in str(exc_info.value)

    def test_numeric_type_raises(self, document):
        """Numeric Type value should raise."""
        page = document.pages.add()
        with pytest.raises(Exception) as exc_info:
            page.transition = {"Type": 123}
        assert "string" in str(exc_info.value).lower()

    def test_none_type_raises(self, document):
        """None Type value should raise."""
        page = document.pages.add()
        with pytest.raises(Exception) as exc_info:
            page.transition = {"Type": None}
        assert "string" in str(exc_info.value).lower()

    def test_all_valid_transition_types_work(self, document):
        """All valid transition types should work."""
        page = document.pages.add()
        valid_types = ["Split", "Blinds", "Box", "Wipe", "Dissolve", "Glitter"]
        for trans_type in valid_types:
            page.transition = {"Type": trans_type}
            assert page.transition["Type"] == trans_type

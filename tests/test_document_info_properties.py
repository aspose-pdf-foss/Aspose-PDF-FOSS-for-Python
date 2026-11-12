"""Tests for the standard ``/Info`` text entries exposed on ``Document``.

``Document.title``, ``author``, ``subject``, ``keywords``, ``creator`` and
``producer`` name the entries ISO 32000-1 14.3.3 defines for the information
dictionary, so a document can be retitled or branded without naming the
``/Info`` keys by hand. The properties read and write the same dictionary
``Document.info`` serves; a ``None`` assignment removes the entry, and a value
that is not text is refused on assignment instead of at save time.
"""

from __future__ import annotations

import pytest

from aspose_pdf.document import Document
from aspose_pdf.exceptions import AsposePdfException
from aspose_pdf.facades import PdfFileInfo

PROPERTIES = ("title", "author", "subject", "keywords", "creator", "producer")


def _save_reload(doc: Document, tmp_path) -> Document:
    path = tmp_path / "info-properties.pdf"
    doc.save(str(path), overwrite=True)
    return Document(str(path))


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", PROPERTIES)
def test_a_fresh_document_has_no_entry(name):
    assert getattr(Document(), name) is None


@pytest.mark.parametrize("name", PROPERTIES)
def test_an_entry_is_read_through_the_property(name):
    doc = Document()
    doc.info["Title"] = "T"
    doc.info["Author"] = "A"
    doc.info["Subject"] = "S"
    doc.info["Keywords"] = "k1, k2"
    doc.info["Creator"] = "C"
    doc.info["Producer"] = "P"

    expected = {
        "title": "T",
        "author": "A",
        "subject": "S",
        "keywords": "k1, k2",
        "creator": "C",
        "producer": "P",
    }
    assert getattr(doc, name) == expected[name]


def test_an_unset_entry_reads_none_while_its_neighbours_keep_theirs():
    doc = Document()
    doc.info["Author"] = "Ada"
    assert doc.title is None
    assert doc.author == "Ada"


def test_a_loaded_document_reads_the_stored_text(tmp_path):
    doc = Document()
    doc.info["Title"] = "Quarterly report"
    reloaded = _save_reload(doc, tmp_path)
    assert reloaded.title == "Quarterly report"


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", PROPERTIES)
def test_an_assignment_is_written_and_round_trips(name, tmp_path):
    doc = Document()
    doc.pages.add()
    setattr(doc, name, "written")

    assert getattr(doc, name) == "written"
    assert doc.info[name.capitalize()] == "written"

    reloaded = _save_reload(doc, tmp_path)
    assert getattr(reloaded, name) == "written"
    assert reloaded.info[name.capitalize()] == "written"


def test_an_assignment_leaves_the_other_entries_alone(tmp_path):
    doc = Document()
    doc.author = "Ada"
    doc.producer = "aspose-pdf-foss"

    doc.title = "Quarterly report"

    assert doc.title == "Quarterly report"
    assert doc.author == "Ada"
    assert doc.producer == "aspose-pdf-foss"
    assert dict(_save_reload(doc, tmp_path).info) == {
        "Title": "Quarterly report",
        "Author": "Ada",
        "Producer": "aspose-pdf-foss",
    }


def test_producer_assignments_are_not_overwritten_by_a_save(tmp_path):
    doc = Document()
    doc.producer = "the last writer"
    reloaded = _save_reload(doc, tmp_path)
    assert reloaded.producer == "the last writer"


# ---------------------------------------------------------------------------
# Removal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", PROPERTIES)
def test_assigning_none_removes_the_entry(name):
    doc = Document()
    setattr(doc, name, "x")

    setattr(doc, name, None)

    assert getattr(doc, name) is None
    assert name.capitalize() not in doc.info


def test_removing_one_entry_keeps_the_others(tmp_path):
    doc = Document()
    doc.title = "Quarterly report"
    doc.author = "Ada"

    doc.title = None

    assert doc.title is None
    assert doc.author == "Ada"
    reloaded = _save_reload(doc, tmp_path)
    assert "Title" not in reloaded.info
    assert reloaded.author == "Ada"


def test_a_removed_entry_is_removed_from_the_saved_file(tmp_path):
    """A loaded document's ``/Info`` can be pruned through the properties."""
    original = Document()
    original.info["Title"] = "Quarterly report"
    original.info["Author"] = "Ada"
    loaded = _save_reload(original, tmp_path)

    loaded.title = None
    pruned = _save_reload(loaded, tmp_path)

    assert pruned.title is None
    assert "Title" not in pruned.info
    assert pruned.author == "Ada"


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", PROPERTIES)
@pytest.mark.parametrize("value", [42, 1.5, ["a", "b"], ("a",), object()])
def test_a_value_that_is_not_text_is_refused_on_assignment(name, value):
    doc = Document()
    setattr(doc, name, "before")

    with pytest.raises(TypeError):
        setattr(doc, name, value)

    assert getattr(doc, name) == "before"
    assert doc.info[name.capitalize()] == "before"


def test_the_refusal_names_the_property_and_the_type():
    doc = Document()
    with pytest.raises(TypeError, match=r"title must be a string or None, got int"):
        doc.title = 3


# ---------------------------------------------------------------------------
# Neighbouring behavior is unchanged
# ---------------------------------------------------------------------------


def test_the_info_dictionary_itself_still_works():
    """The properties share ``/Info`` with the plain dictionary edits."""
    doc = Document()
    doc.info["Title"] = "plain"
    assert doc.title == "plain"
    del doc.info["Title"]
    assert doc.title is None


def test_the_xmp_packet_is_not_touched_by_the_properties():
    doc = Document()
    packet = doc.xmp_metadata

    doc.title = "Quarterly report"

    assert doc.xmp_metadata == packet


def test_sync_metadata_carries_the_title_into_xmp():
    doc = Document()
    doc.title = "Quarterly report"

    doc.sync_metadata()

    field = doc.xmp_metadata.get("dc", "title")
    assert field is not None
    assert [item.value for item in field.value.items] == ["Quarterly report"]


def test_disposed_documents_refuse_the_properties():
    doc = Document()
    doc.dispose()

    with pytest.raises(AsposePdfException):
        doc.title
    with pytest.raises(AsposePdfException):
        doc.title = "x"


def test_the_facade_and_the_document_write_the_same_entries():
    """``PdfFileInfo`` keeps serving the entries under the same keys."""
    doc = Document()
    doc.pages.add()
    facade = PdfFileInfo()
    facade.bind_pdf(doc)
    facade.title = "from the facade"

    assert doc.title == "from the facade"
    assert doc.info["Title"] == "from the facade"

"""Named destinations, and the two bookmark entries that go with them.

ISO 32000-1 12.3.2.3: a place in the document under a name, so that a link, a
bookmark or another file points at ``"chapter-2"`` rather than at a page and a
view -- and moving what the name points at updates every reference at once.

The engine resolved these names on the way *in* all along (a bookmark written by
name already reported the right page), but nothing could list, add or repoint
one: ``Document.destinations`` is that mapping. Two bookmark entries that had no
API either come with it -- ``/C``, the colour a viewer draws the title in, and
the open/closed state ``/Count`` carries -- and a name can now be used as a
target wherever a typed destination can.

What is checked with particular care here: a write touches **one** entry. The
name tree is rewritten as a single ordered node, so every other destination --
including one this API cannot type at all -- has to come through it unchanged.
"""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document, PageSize
from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.interactive import (
    FitDestination,
    FitHDestination,
    URIAction,
    XYZDestination,
)
from aspose_pdf.outlines import OutlineItem


def _document(pages: int = 4) -> Document:
    document = Document()
    for index in range(pages):
        page = document.pages.add(PageSize.A5)
        page.add_text(f"page {index}", 40, 300)
    return document


def _roundtrip(document: Document) -> Document:
    buffer = io.BytesIO()
    document.save(buffer)
    buffer.seek(0)
    return Document(buffer)


def _saved(document: Document) -> bytes:
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# The mapping
# ---------------------------------------------------------------------------


def test_a_new_document_defines_no_destinations():
    document = _document()
    assert len(document.destinations) == 0
    assert list(document.destinations) == []
    assert "anything" not in document.destinations
    assert document.destinations.get("anything") is None
    with pytest.raises(KeyError):
        document.destinations["anything"]


def test_a_destination_is_added_read_and_round_tripped():
    document = _document()
    document.destinations["chapter-2"] = XYZDestination(page=2, left=0, top=760)
    assert document.destinations["chapter-2"] == XYZDestination(
        page=2, left=0.0, top=760.0, zoom=None
    )
    assert _roundtrip(document).destinations["chapter-2"] == XYZDestination(
        page=2, left=0.0, top=760.0, zoom=None
    )


def test_a_page_index_stands_for_that_page_fitted():
    document = _document()
    document.destinations["cover"] = 0
    assert document.destinations["cover"] == FitDestination(0)


def test_every_destination_kind_survives_the_round_trip():
    document = _document()
    document.destinations["fit"] = FitDestination(1)
    document.destinations["xyz"] = XYZDestination(page=2, left=10, top=700, zoom=2)
    document.destinations["fith"] = FitHDestination(page=3, top=500)
    reloaded = _roundtrip(document)
    assert reloaded.destinations["fit"] == FitDestination(1)
    assert reloaded.destinations["xyz"] == XYZDestination(
        page=2, left=10.0, top=700.0, zoom=2.0
    )
    assert reloaded.destinations["fith"] == FitHDestination(page=3, top=500.0)


def test_the_names_come_back_in_order():
    document = _document()
    for name in ("zulu", "alpha", "mike"):
        document.destinations[name] = 0
    assert list(document.destinations) == ["alpha", "mike", "zulu"]
    assert document.destinations.keys() == ["alpha", "mike", "zulu"]
    assert [name for name, _ in document.destinations.items()] == [
        "alpha",
        "mike",
        "zulu",
    ]
    assert document.destinations.values() == [FitDestination(0)] * 3


def test_a_name_tree_is_written_in_key_order():
    # 12.3.2.3 and 7.9.6: a name tree is searched by key, so the /Names array has
    # to be sorted or a reader's binary search misses entries.
    document = _document()
    for name in ("zulu", "alpha", "mike"):
        document.destinations[name] = 0
    data = _saved(document)
    positions = [data.index(name.encode()) for name in ("alpha", "mike", "zulu")]
    assert positions == sorted(positions)


def test_adding_a_destination_replaces_the_one_that_was_there():
    document = _document()
    document.destinations["spot"] = 0
    document.destinations["spot"] = XYZDestination(page=3, top=100)
    assert len(document.destinations) == 1
    assert document.destinations["spot"] == XYZDestination(page=3, top=100.0)


def test_a_destination_is_removed():
    document = _document()
    document.destinations["cover"] = 0
    document.destinations["end"] = 3
    assert document.destinations.remove("cover") is True
    assert document.destinations.remove("cover") is False
    assert document.destinations.keys() == ["end"]
    del document.destinations["end"]
    assert len(document.destinations) == 0
    with pytest.raises(KeyError):
        del document.destinations["end"]
    assert b"/Dests" not in _saved(document)


def test_clearing_removes_every_destination():
    document = _document()
    document.destinations["a"] = 0
    document.destinations["b"] = 1
    document.destinations.clear()
    assert len(document.destinations) == 0
    assert len(_roundtrip(document).destinations) == 0


def test_a_destination_needs_a_page_the_document_has():
    document = _document(2)
    with pytest.raises(PdfValidationException, match="outside this document"):
        document.destinations["nope"] = 5
    with pytest.raises(PdfValidationException, match="outside this document"):
        document.destinations["nope"] = XYZDestination(page=9, top=1)
    empty = Document()
    with pytest.raises(PdfValidationException, match="has none"):
        empty.destinations["nope"] = 0


def test_a_destination_value_has_to_be_one():
    document = _document()
    for bad in ["chapter-2", 1.5, None, True, (0, 0)]:
        with pytest.raises(PdfValidationException):
            document.destinations["x"] = bad


def test_a_name_has_to_be_a_latin_1_string():
    document = _document()
    with pytest.raises(PdfValidationException, match="cannot be empty"):
        document.destinations[""] = 0
    with pytest.raises(PdfValidationException, match=r"(?i)latin-1|must be a string"):
        document.destinations["глава"] = 0
    with pytest.raises(PdfValidationException, match="must be a string"):
        document.destinations[7] = 0


def test_a_name_may_hold_the_punctuation_a_writer_uses():
    document = _document()
    for name in ("chapter 2", "fig:1.2", "A/B", "name#with(parens)", "dash-and_under"):
        document.destinations[name] = 1
    reloaded = _roundtrip(document)
    assert reloaded.destinations["chapter 2"] == FitDestination(1)
    assert reloaded.destinations["A/B"] == FitDestination(1)
    assert reloaded.destinations["name#with(parens)"] == FitDestination(1)


# ---------------------------------------------------------------------------
# Writing one name leaves the rest of the file alone
# ---------------------------------------------------------------------------


def _with_untypable_entry(document: Document) -> Document:
    """Put a destination this API cannot type into the name tree by hand."""
    from aspose_pdf.engine.cos import (
        PdfArray,
        PdfDictionary,
        PdfName,
        PdfNumber,
        PdfString,
    )

    engine = document._engine_pdf
    engine._ensure_cos()
    catalog = engine._resolve(engine._cos_doc.trailer.mapping.get(PdfName("Root")))
    # A remote destination: its page is a *number* in another file, which is
    # exactly what this API refuses to type (it would write back as a reference
    # into this document).
    remote = PdfArray([PdfNumber(3), PdfName("Fit")])
    names = PdfDictionary(
        {PdfName("Names"): PdfArray([PdfString(b"remote"), remote])}
    )
    catalog.mapping[PdfName("Names")] = PdfDictionary({PdfName("Dests"): names})
    engine._named_destination_cache = None
    return document


def test_an_entry_this_api_cannot_type_is_listed_and_reads_as_none():
    document = _with_untypable_entry(_document())
    assert "remote" in document.destinations
    assert len(document.destinations) == 1
    assert document.destinations["remote"] is None
    # ``get`` tells the two apart: a default means "no such name".
    assert document.destinations.get("remote", "missing") is None
    assert document.destinations.get("absent", "missing") == "missing"


def test_writing_one_name_keeps_an_untypable_entry_exactly_as_it_was():
    document = _with_untypable_entry(_document())
    document.destinations["cover"] = 0
    data = _saved(document)
    assert b"/remote" not in data  # it is a string key, not a name
    assert b"remote" in data
    reloaded = Document(io.BytesIO(data))
    assert reloaded.destinations.keys() == ["cover", "remote"]
    assert reloaded.destinations["remote"] is None
    assert reloaded.destinations["cover"] == FitDestination(0)


def test_removing_one_name_keeps_the_others():
    document = _with_untypable_entry(_document())
    document.destinations["cover"] = 0
    document.destinations["end"] = 3
    document.destinations.remove("cover")
    reloaded = _roundtrip(document)
    assert reloaded.destinations.keys() == ["end", "remote"]


# ---------------------------------------------------------------------------
# The older /Dests dictionary (PDF 1.1)
# ---------------------------------------------------------------------------


def _with_legacy_dests(document: Document) -> Document:
    """Define a destination the PDF 1.1 way, in a catalog ``/Dests`` dictionary."""
    from aspose_pdf.engine.cos import PdfArray, PdfDictionary, PdfName

    engine = document._engine_pdf
    engine._ensure_cos()
    catalog = engine._resolve(engine._cos_doc.trailer.mapping.get(PdfName("Root")))
    page_ref = engine._make_page_ref(1)
    catalog.mapping[PdfName("Dests")] = PdfDictionary(
        {PdfName("legacy"): PdfArray([page_ref, PdfName("Fit")])}
    )
    engine._named_destination_cache = None
    return document


def test_a_destination_in_the_older_dictionary_is_read():
    document = _with_legacy_dests(_document())
    assert document.destinations.keys() == ["legacy"]
    assert document.destinations["legacy"] == FitDestination(1)


def test_a_name_defined_the_old_way_is_updated_where_it_lives():
    # A document that uses the PDF 1.1 form keeps using it, rather than ending up
    # with the same name in two places saying two things.
    document = _with_legacy_dests(_document())
    document.destinations["legacy"] = 3
    data = _saved(document)
    assert document.destinations["legacy"] == FitDestination(3)
    assert b"/Dests" in data
    assert data.count(b"legacy") == 1
    assert Document(io.BytesIO(data)).destinations["legacy"] == FitDestination(3)


def test_a_new_name_goes_into_the_tree_even_beside_the_old_dictionary():
    document = _with_legacy_dests(_document())
    document.destinations["modern"] = 2
    reloaded = _roundtrip(document)
    assert reloaded.destinations.keys() == ["legacy", "modern"]
    assert reloaded.destinations["modern"] == FitDestination(2)


def test_removing_clears_both_places():
    document = _with_legacy_dests(_document())
    document.destinations["legacy"] = 3  # still in the dictionary
    assert document.destinations.remove("legacy") is True
    assert len(document.destinations) == 0
    assert len(_roundtrip(document).destinations) == 0


def test_clearing_removes_the_old_dictionary_too():
    document = _with_legacy_dests(_document())
    document.destinations["modern"] = 2
    document.destinations.clear()
    data = _saved(document)
    assert b"legacy" not in data
    assert len(Document(io.BytesIO(data)).destinations) == 0


# ---------------------------------------------------------------------------
# Pointing at a name
# ---------------------------------------------------------------------------


def test_a_link_can_target_a_name():
    document = _document()
    document.destinations["chapter-2"] = XYZDestination(page=2, top=760)
    document.pages[0].add_link((72, 700, 200, 720), "chapter-2")
    reloaded = _roundtrip(document)
    annotation = reloaded.pages[0].annotations[0]
    assert annotation.subtype == "Link"
    assert annotation.get_property("Dest") == "chapter-2"


def test_a_bookmark_can_target_a_name_and_reports_the_page_it_lands_on():
    document = _document()
    document.destinations["chapter-2"] = XYZDestination(page=2, top=760)
    document.outlines.add(OutlineItem("Chapter 2", destination="chapter-2"))
    reloaded = _roundtrip(document)
    item = reloaded.outlines[0]
    assert item.page_index == 2
    assert item.destination_name == "chapter-2"


def test_repointing_a_name_moves_every_reference_at_once():
    # The whole purpose of the indirection: the bookmark and the link are not
    # touched, and both now land on the new page.
    document = _document()
    document.destinations["chapter-2"] = XYZDestination(page=2, top=760)
    document.outlines.add(OutlineItem("Chapter 2", destination="chapter-2"))
    document.pages[0].add_link((72, 700, 200, 720), "chapter-2")
    document = _roundtrip(document)

    document.destinations["chapter-2"] = FitDestination(1)
    reloaded = _roundtrip(document)
    assert reloaded.outlines[0].page_index == 1
    assert reloaded.destinations["chapter-2"] == FitDestination(1)


def test_a_bookmark_pointing_at_a_name_nothing_defines_lands_nowhere():
    document = _document()
    document.destinations["chapter-2"] = 2
    document.outlines.add(OutlineItem("Chapter 2", destination="chapter-2"))
    document = _roundtrip(document)
    document.destinations.remove("chapter-2")
    reloaded = _roundtrip(document)
    assert reloaded.outlines[0].page_index is None
    assert reloaded.outlines[0].destination_name == "chapter-2"


def test_a_name_that_is_not_latin_1_is_refused_as_a_target():
    document = _document()
    with pytest.raises(PdfValidationException):
        document.pages[0].add_link((0, 0, 10, 10), "глава")


def test_a_target_that_is_neither_a_name_an_action_nor_a_destination_is_refused():
    document = _document()
    with pytest.raises(TypeError, match="Action, a Destination, or the name"):
        document.pages[0].add_link((0, 0, 10, 10), 3)


# ---------------------------------------------------------------------------
# Bookmark colour and open state
# ---------------------------------------------------------------------------


def test_a_bookmark_carries_a_colour():
    document = _document()
    document.outlines.add(OutlineItem("Red", 0, color="#cc3300"))
    document.outlines.add(OutlineItem("Blue", 1, color=(0, 0, 255)))
    data = _saved(document)
    assert b"/C [ 0.8 0.2 0 ]" in data
    reloaded = Document(io.BytesIO(data))
    assert reloaded.outlines[0].color == (0.8, 0.2, 0.0)
    assert reloaded.outlines[1].color == (0.0, 0.0, 1.0)


def test_a_bookmark_without_a_colour_writes_no_entry():
    document = _document()
    document.outlines.add(OutlineItem("Plain", 0))
    assert b"/C [" not in _saved(document)
    assert _roundtrip(document).outlines[0].color is None


def test_a_bookmark_colour_is_rgb_only():
    # Table 153 types /C as three DeviceRGB numbers: there is nowhere to put a
    # grey or an ink, so one is refused rather than converted behind the caller.
    with pytest.raises(PdfValidationException, match="three DeviceRGB"):
        OutlineItem("Grey", 0, color=0.5)
    with pytest.raises(PdfValidationException, match="three DeviceRGB"):
        OutlineItem("Ink", 0, color=(0, 0.2, 1, 0.05))


def test_a_colour_can_be_changed_and_removed_after_the_fact():
    item = OutlineItem("Title", 0, color="#ff0000")
    assert item.color == (1.0, 0.0, 0.0)
    item.color = (0, 128, 255)
    assert item.color == (0.0, 128 / 255, 1.0)
    item.color = None
    assert item.color is None


def test_an_open_bookmark_is_written_with_a_positive_count():
    document = _document()
    parent = document.outlines.add(OutlineItem("Part", 0, open=True))
    parent.add(OutlineItem("Chapter", 1))
    data = _saved(document)
    assert b"/Count 1" in data
    reloaded = Document(io.BytesIO(data))
    assert reloaded.outlines[0].open is True
    assert reloaded.outlines[0].children[0].open is False


def test_a_closed_bookmark_is_written_with_a_negative_count():
    document = _document()
    parent = document.outlines.add(OutlineItem("Part", 0))
    parent.add(OutlineItem("Chapter", 1))
    data = _saved(document)
    assert b"/Count -1" in data
    assert _roundtrip(document).outlines[0].open is False


def test_the_count_is_what_opening_the_item_would_show():
    # Table 153: the magnitude is every row the item unfolds, at all levels --
    # so a child that is itself open adds its own rows to the total.
    document = _document()
    part = document.outlines.add(OutlineItem("Part", 0, open=True))
    part.add(OutlineItem("Chapter 1", 1))
    chapter = part.add(OutlineItem("Chapter 2", 2, open=True))
    chapter.add(OutlineItem("Section 2.1", 3))
    data = _saved(document)
    counts = [int(value) for value in _outline_counts(data)]
    # Chapter 2 shows one row, Part shows three (two chapters + one section),
    # and the panel shows four (Part + its three).
    assert 1 in counts and 3 in counts and 4 in counts


def _outline_counts(data: bytes) -> list[bytes]:
    import re

    # The page tree has a /Count too; the outline ones are the rest.
    pages = data.count(b"/Type /Pages")
    found = re.findall(rb"/Count (-?\d+)", data)
    return found[pages:] if pages else found


def test_a_bookmark_with_no_children_has_no_open_state():
    # Table 153 requires /Count only for an item with descendants, and an item
    # with none has nothing to unfold, so ``open`` reads back False.
    document = _document()
    document.outlines.add(OutlineItem("Leaf", 0, open=True))
    assert _roundtrip(document).outlines[0].open is False


def test_the_root_count_is_every_row_the_panel_shows():
    document = _document()
    part = document.outlines.add(OutlineItem("Part", 0, open=True))
    part.add(OutlineItem("Chapter", 1))
    document.outlines.add(OutlineItem("Appendix", 2))
    data = _saved(document)
    # Part + its chapter + Appendix.
    assert b"/Type /Outlines" in data
    assert b"/Count 3" in data


def test_colour_and_open_state_survive_an_untouched_round_trip():
    document = _document()
    part = document.outlines.add(OutlineItem("Part", 0, color="#008000", open=True))
    part.add(OutlineItem("Chapter", 1, color=(0, 0, 0)))
    once = _roundtrip(document)
    twice = _roundtrip(once)
    # 128/255 goes into the file with six decimals and comes back as that, which
    # is the precision a PDF number has (7.3.3) rather than any loss of colour.
    assert twice.outlines[0].color == pytest.approx((0.0, 128 / 255, 0.0), abs=1e-6)
    assert twice.outlines[0].open is True
    assert twice.outlines[0].children[0].color == (0.0, 0.0, 0.0)


def test_an_action_target_still_works_beside_the_new_entries():
    document = _document()
    document.outlines.add(
        OutlineItem("Home", color="#0000ff", destination=URIAction("https://example.com"))
    )
    reloaded = _roundtrip(document)
    item = reloaded.outlines[0]
    assert isinstance(item.destination, URIAction)
    assert item.color == (0.0, 0.0, 1.0)
    assert item.page_index is None

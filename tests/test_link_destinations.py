"""A destination names its page by reference, in both directions.

Reading one back used to be impossible. The property channel turns an
annotation entry into plain Python, resolving indirect references as it goes,
so a ``/Dest`` array pulled in a copy of the whole page it pointed at -- and
since that page lists the very annotation holding the destination, the walk came
back round to where it started and the reader raised
``Annotation property graph contains a cycle``. Not for a malformed file: for
an ordinary internal link, the most common annotation there is. Iterating
``page.annotations`` at all was impossible on such a page.

Writing one was wrong in a narrower but sharper way. Both callers that turn a
page *index* into a page *reference* read ``_page_obj_ids``, a list maintained
by hand alongside the edits; between an insert and the next save it carries a
``0`` placeholder for the new page, which shifts every page after it and
serialises as ``0 0 R`` -- the head of the free list (ISO 32000-1 7.5.4), never
a real object. Links and bookmarks written in that window pointed at the wrong
page or at nothing.

Every previous test of this feature asserted on the dictionary the writer
built. That is exactly the shape of test that cannot see either fault.
"""

from __future__ import annotations

import io
import re

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfDictionary,
    PdfIndirectReference,
    PdfName,
    PdfNumber,
    PdfString,
)
from aspose_pdf.exceptions import PdfParseException
from aspose_pdf.interactive import (
    FitBDestination,
    FitBHDestination,
    FitBVDestination,
    FitDestination,
    FitHDestination,
    FitRDestination,
    FitVDestination,
    GoToAction,
    GoToRAction,
    URIAction,
    XYZDestination,
)
from aspose_pdf.outlines import OutlineItem


def _reloaded(document: Document) -> Document:
    buffer = io.BytesIO()
    document.save(buffer)
    return Document(io.BytesIO(buffer.getvalue()))


def _paged(count: int = 4) -> Document:
    document = Document()
    for index in range(count):
        document.pages.add().add_text(f"PAGE {index}", 72, 700, font_size=24)
    return document


def _linked(target, *, pages: int = 4) -> Document:
    document = _paged(pages)
    document.pages[0].add_link((72, 600, 200, 620), target)
    return _reloaded(document)


def _properties(document: Document, page_index: int = 0, annot: int = 0) -> dict:
    return document.pages[page_index].annotations[annot].properties


def _page_dict(document: Document, index: int) -> PdfDictionary:
    engine = document._engine_pdf
    engine._ensure_page_cache()
    return engine._cos_doc.objects.get(engine._page_refs[index])


def _first_annotation(document: Document, page_index: int = 0) -> PdfDictionary:
    engine = document._engine_pdf
    page = _page_dict(document, page_index)
    annots = engine._resolve(page.mapping.get(PdfName("Annots")))
    return engine._resolve(annots.items[0])


def _page_ref(document: Document, index: int) -> PdfIndirectReference:
    engine = document._engine_pdf
    engine._ensure_page_cache()
    return PdfIndirectReference(engine._page_refs[index], 0)


def _dest_targets(data: bytes) -> list[int]:
    """Object numbers the saved file's ``/Dest`` arrays point at."""
    return [
        int(m.group(1)) for m in re.finditer(rb"/Dest\s*\[\s*(\d+)\s+\d+\s+R", data)
    ]


# ---------------------------------------------------------------------------
# Reading a destination back
# ---------------------------------------------------------------------------


def test_a_link_into_this_document_can_be_read_at_all():
    document = _linked(FitDestination(2))

    assert _properties(document)["Dest"] == FitDestination(page=2)


def test_a_link_to_its_own_page_can_be_read():
    """The shortest cycle there is: the page lists the link that names it."""
    document = _paged(1)
    document.pages[0].add_link((72, 600, 200, 620), FitDestination(0))

    assert _properties(_reloaded(document))["Dest"] == FitDestination(page=0)


def test_one_unreadable_link_does_not_hide_the_other_annotations():
    document = _paged(2)
    document.pages[0].add_link((72, 600, 200, 620), FitDestination(1))
    document.pages[0].annotations.add("Text", (10, 10, 40, 40), "a note")

    subtypes = [a.subtype for a in _reloaded(document).pages[0].annotations]

    assert subtypes == ["Link", "Text"]


@pytest.mark.parametrize(
    "target",
    [
        FitDestination(2),
        FitBDestination(2),
        XYZDestination(1, 10.0, 20.0, 1.5),
        FitHDestination(3, 700.0),
        FitBHDestination(3, 700.0),
        FitVDestination(1, 40.0),
        FitBVDestination(1, 40.0),
        FitRDestination(2, 1.0, 2.0, 3.0, 4.0),
    ],
    ids=lambda t: type(t).__name__ + str(t.page),
)
def test_every_destination_kind_reads_back_as_itself(target):
    assert _properties(_linked(target))["Dest"] == target


def test_a_goto_action_carries_a_typed_destination():
    """A destination nests, as the ``/D`` of an action."""
    action = _properties(_linked(GoToAction(FitDestination(2))))["A"]

    assert action["S"] == "GoTo"
    assert action["D"] == FitDestination(page=2)


def test_a_remote_destination_stays_a_plain_page_number():
    """``GoToR`` names a page in *another* file, by number.

    Typing it would be worse than leaving it: the page would come back as an
    index into this document and write back as a reference into this document,
    silently repointing the link at a local page.
    """
    action = _properties(_linked(GoToRAction("other.pdf", FitDestination(2))))["A"]

    assert action["F"] == "other.pdf"
    assert action["D"] == [2, "Fit"]


def test_a_uri_action_is_untouched():
    action = _properties(_linked(URIAction("https://example.com")))["A"]

    assert action == {"S": "URI", "Type": "Action", "URI": "https://example.com"}


def test_a_destination_read_back_writes_the_same_page():
    """The property channel is also how a property is set."""
    document = _linked(XYZDestination(2, 10.0, 20.0, 1.5))
    document.pages[1].annotations.add(
        "Link", (10, 10, 90, 30), "", properties=_properties(document)
    )

    copied = _properties(_reloaded(document), page_index=1)

    assert copied["Dest"] == XYZDestination(page=2, left=10.0, top=20.0, zoom=1.5)


# ---------------------------------------------------------------------------
# What is not a destination this document can express
# ---------------------------------------------------------------------------


def test_a_destination_whose_page_is_gone_is_not_surfaced():
    """Deleting a page leaves the page object behind and the link on it.

    Half-converting the array to ``[None, 'Fit']`` would only hand the caller
    something that writes back as ``[null /Fit]``.
    """
    document = _linked(FitDestination(3))
    document.pages.delete(3)

    assert "Dest" not in _properties(_reloaded(document))


def test_an_undefined_destination_kind_is_not_surfaced():
    document = _linked(FitDestination(2))
    _first_annotation(document).mapping[PdfName("Dest")] = PdfArray(
        [_page_ref(document, 2), PdfName("Bogus")]
    )

    assert "Dest" not in _properties(document)


def test_too_many_parameters_for_the_kind_is_not_surfaced():
    document = _linked(FitDestination(2))
    _first_annotation(document).mapping[PdfName("Dest")] = PdfArray(
        [_page_ref(document, 2), PdfName("Fit"), PdfNumber(1), PdfNumber(2)]
    )

    assert "Dest" not in _properties(document)


def test_a_short_rectangle_destination_is_not_surfaced():
    """``FitR`` is all four numbers or none of them."""
    document = _linked(FitDestination(2))
    _first_annotation(document).mapping[PdfName("Dest")] = PdfArray(
        [_page_ref(document, 2), PdfName("FitR"), PdfNumber(1), PdfNumber(2)]
    )

    assert "Dest" not in _properties(document)


def test_a_short_xyz_destination_keeps_what_it_was_given():
    """Any of XYZ's three may be left out; a missing one means "keep"."""
    document = _linked(FitDestination(2))
    _first_annotation(document).mapping[PdfName("Dest")] = PdfArray(
        [_page_ref(document, 2), PdfName("XYZ"), PdfNumber(10)]
    )

    assert _properties(document)["Dest"] == XYZDestination(
        page=2, left=10.0, top=None, zoom=None
    )


def test_a_page_named_outside_a_destination_is_not_inlined():
    """A page is never a property *value*, wherever the reference turns up."""
    document = _linked(FitDestination(2))
    annotation = _first_annotation(document)
    annotation.mapping[PdfName("Zz")] = _page_ref(document, 1)
    annotation.mapping[PdfName("Yy")] = PdfArray([PdfNumber(1), _page_ref(document, 1)])

    properties = _properties(document)

    assert "Zz" not in properties
    assert properties["Yy"] == [1, None]


def test_a_page_given_directly_rather_than_by_reference_is_not_a_destination():
    """ISO 32000-1 12.3.2.2: a destination names its page by reference.

    A direct dictionary in that place is malformed, and looking up the page
    index of something that is not a reference would only raise. The array
    comes back as what the reader could make of it -- the page dropped,
    because a page is never a property value.
    """
    document = _linked(FitDestination(2))
    _first_annotation(document).mapping[PdfName("Dest")] = PdfArray(
        [PdfDictionary({PdfName("Type"): PdfName("Page")}), PdfName("Fit")]
    )

    assert _properties(document)["Dest"] == [None, "Fit"]


def test_an_array_naming_something_other_than_a_page_is_left_alone():
    """What makes the array a destination is that it starts with a *page*.

    Without that test the reader would be guessing from the shape, and would
    quietly drop any other array that happened to start with a reference.
    """
    document = _linked(FitDestination(2))
    engine = document._engine_pdf
    other = engine._cos_doc.register_object(
        PdfDictionary({PdfName("Type"): PdfName("Whatever")})
    )
    _first_annotation(document).mapping[PdfName("Zz")] = PdfArray(
        [other, PdfName("Fit")]
    )

    assert _properties(document)["Zz"] == [{"Type": "Whatever"}, "Fit"]


def test_a_destination_parameter_that_is_not_a_number_is_not_surfaced():
    document = _linked(FitDestination(2))
    _first_annotation(document).mapping[PdfName("Dest")] = PdfArray(
        [_page_ref(document, 2), PdfName("XYZ"), PdfString(b"left"), PdfNumber(20)]
    )

    assert "Dest" not in _properties(document)


def test_an_action_destination_read_back_writes_the_same_page():
    """The nested case: the destination is the ``/D`` of the action."""
    document = _linked(GoToAction(XYZDestination(2, 10.0, 20.0, 1.5)))
    document.pages[1].annotations.add(
        "Link", (10, 10, 90, 30), "", properties=_properties(document)
    )

    action = _properties(_reloaded(document), page_index=1)["A"]

    assert action["S"] == "GoTo"
    assert action["D"] == XYZDestination(page=2, left=10.0, top=20.0, zoom=1.5)


def test_a_bookmark_on_a_document_with_no_pages_is_simply_not_written():
    """There is nowhere for it to point, and nothing to raise about."""
    document = _paged(2)
    document.outlines.add(OutlineItem("nowhere", 0))
    document.pages.clear()

    buffer = io.BytesIO()
    document.save(buffer)

    assert b"nowhere" not in buffer.getvalue()


def test_a_genuinely_cyclic_property_still_raises():
    """The guard stays; ordinary links simply no longer reach it."""
    document = _paged(1)
    document.pages[0].annotations.add("Text", (10, 10, 40, 40), "a note")
    loop = PdfArray([PdfNumber(1)])
    loop.items.append(loop)
    _first_annotation(document).mapping[PdfName("Zz")] = loop

    with pytest.raises(PdfParseException, match="cycle"):
        _properties(document)


# ---------------------------------------------------------------------------
# Writing a destination after the pages have moved
# ---------------------------------------------------------------------------


def test_a_link_added_after_an_insert_points_at_the_page_named():
    document = _reloaded(_paged(3))
    document.pages.insert(1)
    document.pages[1].add_text("INSERTED", 72, 700, font_size=24)
    document.pages[0].add_link((72, 600, 200, 620), FitDestination(3))

    reloaded = _reloaded(document)

    assert _properties(reloaded)["Dest"] == FitDestination(page=3)
    assert reloaded.pages[3].to_markdown().strip() == "PAGE 2"


def test_a_link_to_a_freshly_inserted_page_is_not_a_reference_to_object_zero():
    """``0 0 R`` is the free-list head; a viewer following it arrives nowhere."""
    document = _paged(3)
    document.pages.insert(1)
    document.pages[0].add_link((72, 600, 200, 620), FitDestination(1))

    buffer = io.BytesIO()
    document.save(buffer)
    data = buffer.getvalue()

    assert 0 not in _dest_targets(data)
    assert _properties(Document(io.BytesIO(data)))["Dest"] == FitDestination(page=1)


def test_a_bookmark_added_after_an_insert_points_at_the_page_named():
    """Outlines had their own copy of the same broken lookup."""
    document = _paged(3)
    document.pages.insert(1)
    document.pages[1].add_text("INSERTED", 72, 700, font_size=24)
    document.outlines.add(OutlineItem("last", 3))

    reloaded = _reloaded(document)

    assert [(o.title, o.page_index) for o in reloaded.outlines] == [("last", 3)]
    assert reloaded.pages[3].to_markdown().strip() == "PAGE 2"


def test_a_destination_resolves_through_a_nested_page_tree():
    """Index means position in the tree walk, not position in a flat list."""
    document = _paged(4)
    engine = document._engine_pdf
    engine._ensure_page_cache()
    root = engine._resolve(
        engine._resolve(engine._cos_doc.trailer.mapping.get(PdfName("Root"))).mapping[
            PdfName("Pages")
        ]
    )
    kids = root.mapping[PdfName("Kids")]
    branch = PdfDictionary(
        {
            PdfName("Type"): PdfName("Pages"),
            PdfName("Kids"): PdfArray(kids.items[2:]),
            PdfName("Count"): PdfNumber(2),
            PdfName("Parent"): engine._cos_doc.trailer.mapping.get(PdfName("Root")),
        }
    )
    branch_ref = engine._cos_doc.register_object(branch)
    for kid in kids.items[2:]:
        engine._resolve(kid).mapping[PdfName("Parent")] = branch_ref
    kids.items[2:] = [branch_ref]
    engine._page_cache_valid = False

    document.pages[0].add_link((72, 600, 200, 620), FitDestination(3))
    reloaded = _reloaded(document)

    assert _properties(reloaded)["Dest"] == FitDestination(page=3)
    assert reloaded.pages[3].to_markdown().strip() == "PAGE 3"

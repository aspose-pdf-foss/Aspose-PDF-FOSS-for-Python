"""A bookmark keeps pointing where it pointed.

The outline model read a bookmark back as one number -- the index of the page
its ``/Dest`` named -- and wrote it out again as "fit that page". So merely
opening a document and saving it rewrote every bookmark: a ``/XYZ`` view lost
its position and zoom, and a bookmark carrying an *action* lost the action
altogether, becoming a jump to page 1. Nothing warned, and no structure check
could notice, because "fit page 1" is a perfectly well-formed destination.

An index is also not an identity. A ``/Dest`` names its page by object
reference, so a bookmark survives its page being moved; flattening it to an
index and resolving that index again at save time meant deleting or inserting a
single page silently repointed every bookmark past it. Link annotations, which
keep the reference, were never affected -- only bookmarks, which did not.

An item loaded from a file now carries the target the file held, verbatim, and
that is what gets written back. Naming a target -- either public way -- replaces
it, because the caller has then said where the bookmark goes.
"""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfDictionary,
    PdfName,
    PdfNumber,
    PdfString,
)
from aspose_pdf.interactive import (
    FitDestination,
    GoToAction,
    GoToRAction,
    JavaScriptAction,
    LaunchAction,
    NamedAction,
    ResetFormAction,
    SubmitFormAction,
    URIAction,
    XYZDestination,
)
from aspose_pdf.outlines import OutlineItem


def _paged(count: int = 4) -> Document:
    document = Document()
    for index in range(count):
        document.pages.add().add_text(f"PAGE {index}", 72, 700, font_size=24)
    return document


def _saved(document: Document, **kwargs) -> bytes:
    buffer = io.BytesIO()
    document.save(buffer, **kwargs)
    return buffer.getvalue()


def _reloaded(document: Document) -> Document:
    return Document(io.BytesIO(_saved(document)))


def _bookmarked(*items: OutlineItem, pages: int = 4) -> Document:
    document = _paged(pages)
    for item in items:
        document.outlines.add(item)
    return _reloaded(document)


def _titles(document: Document) -> dict[str, OutlineItem]:
    return {item.title: item for item in document.outlines}


def _lands_on(document: Document, item: OutlineItem) -> str:
    return document.pages[item.page_index].to_markdown().strip()


def _hand_built(*targets: tuple[str, str, object], pages: int = 3) -> bytes:
    """A file whose outline items carry *targets* -- (title, key, value).

    Written straight into the COS graph, which survives the save because an
    empty outline model leaves the catalog's tree alone. It is the only way to
    author the targets this library does not itself produce.
    """
    document = _paged(pages)
    engine = document._engine_pdf
    engine._ensure_cos()
    engine._ensure_page_cache()
    cos = engine._cos_doc
    root = engine._resolve(cos.trailer.mapping.get(PdfName("Root")))

    outlines = PdfDictionary({PdfName("Type"): PdfName("Outlines")})
    outlines_ref = cos.register_object(outlines)
    refs = [
        cos.register_object(
            PdfDictionary(
                {
                    PdfName("Title"): PdfString(title.encode()),
                    PdfName("Parent"): outlines_ref,
                    PdfName(key): value,
                }
            )
        )
        for title, key, value in targets
    ]
    for position, ref in enumerate(refs):
        item = engine._resolve(ref)
        if position:
            item.mapping[PdfName("Prev")] = refs[position - 1]
        if position < len(refs) - 1:
            item.mapping[PdfName("Next")] = refs[position + 1]
    outlines.mapping[PdfName("First")] = refs[0]
    outlines.mapping[PdfName("Last")] = refs[-1]
    outlines.mapping[PdfName("Count")] = PdfNumber(len(refs))
    root.mapping[PdfName("Outlines")] = outlines_ref
    return _saved(document)


# ---------------------------------------------------------------------------
# The target survives
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "target",
    [
        XYZDestination(2, 0.0, 700.0, 2.0),
        FitDestination(2),
        URIAction("https://example.com"),
        GoToRAction("other.pdf", FitDestination(1)),
        NamedAction("NextPage"),
        JavaScriptAction("app.alert(1)"),
        LaunchAction("notes.txt"),
    ],
    ids=lambda t: type(t).__name__,
)
def test_a_bookmark_target_reads_back_as_itself(target):
    document = _bookmarked(OutlineItem("go", destination=target))

    assert _titles(document)["go"].destination == target


def test_a_zoom_is_not_flattened_to_fit_by_saving():
    document = _bookmarked(
        OutlineItem("zoomed", destination=XYZDestination(2, 0.0, 700.0, 2.0))
    )

    again = _reloaded(document)

    assert _titles(again)["zoomed"].destination == XYZDestination(
        page=2, left=0.0, top=700.0, zoom=2.0
    )


def test_an_action_is_not_replaced_by_a_jump_to_page_one():
    """The old rewrite did not degrade the bookmark; it sent it elsewhere."""
    document = _bookmarked(OutlineItem("web", destination=URIAction("https://ex.com")))

    again = _reloaded(document)

    assert _titles(again)["web"].destination == URIAction("https://ex.com")


def test_children_keep_their_targets_too():
    parent = OutlineItem("Chapter", destination=XYZDestination(1, 0.0, 700.0, 1.0))
    parent.add(OutlineItem("Section", 2))
    parent.add(OutlineItem("Web", destination=URIAction("https://ex.com")))

    chapter = _titles(_reloaded(_bookmarked(parent)))["Chapter"]

    assert chapter.destination == XYZDestination(1, 0.0, 700.0, 1.0)
    assert [child.destination for child in chapter.children] == [
        FitDestination(page=2),
        URIAction("https://ex.com"),
    ]


def test_a_named_destination_is_kept_though_it_cannot_be_read():
    """`/Dests` entries are a document-wide name tree this API does not model.

    Reporting no destination is honest; rewriting it as a page jump was not.
    """
    data = _hand_built(("named", "Dest", PdfString(b"Chapter1")))
    document = Document(io.BytesIO(data))

    assert _titles(document)["named"].destination is None
    assert b"Chapter1" in _saved(document)


def test_an_action_this_api_does_not_model_is_kept():
    data = _hand_built(("movie", "A", PdfDictionary({PdfName("S"): PdfName("Movie")})))
    document = Document(io.BytesIO(data))

    assert _titles(document)["movie"].destination is None
    assert b"/Movie" in _saved(document)


def test_a_goto_action_bookmark_follows_its_page_too():
    """Its page reference sits one level down, inside the action's ``/D``."""
    document = _bookmarked(
        OutlineItem("go", destination=GoToAction(XYZDestination(3, 0.0, 700.0, 1.0)))
    )
    document.pages.delete(0)

    again = _reloaded(document)
    go = _titles(again)["go"]

    assert go.destination == GoToAction(XYZDestination(2, 0.0, 700.0, 1.0))
    assert (
        again.pages[go.destination.destination.page].to_markdown().strip() == "PAGE 3"
    )


def test_a_goto_action_bookmark_to_a_deleted_page_keeps_being_an_action():
    """The fallback is the typed view, so only the page it names is given up."""
    document = _bookmarked(OutlineItem("go", destination=GoToAction(FitDestination(3))))
    document.pages.delete(3)

    again = _reloaded(document)

    assert _titles(again)["go"].destination == GoToAction(
        FitDestination(page=len(again.pages) - 1)
    )


def test_a_goto_to_a_named_destination_is_kept_but_not_typed():
    """`GoToAction` has nowhere to put a name from the document's /Dests."""
    data = _hand_built(
        (
            "go",
            "A",
            PdfDictionary(
                {PdfName("S"): PdfName("GoTo"), PdfName("D"): PdfString(b"Chapter1")}
            ),
        )
    )
    document = Document(io.BytesIO(data))

    assert _titles(document)["go"].destination is None
    assert b"Chapter1" in _saved(document)


def test_a_remote_bookmark_with_an_undefined_view_is_kept_but_not_typed():
    data = _hand_built(
        (
            "remote",
            "A",
            PdfDictionary(
                {
                    PdfName("S"): PdfName("GoToR"),
                    PdfName("F"): PdfString(b"other.pdf"),
                    PdfName("D"): PdfArray([PdfNumber(2), PdfName("Bogus")]),
                }
            ),
        )
    )
    document = Document(io.BytesIO(data))

    assert _titles(document)["remote"].destination is None
    assert b"/Bogus" in _saved(document)


@pytest.mark.parametrize(
    "target",
    [
        SubmitFormAction("https://forms.example", ["a", "b"], True, "xfdf"),
        SubmitFormAction("https://forms.example"),
        ResetFormAction(["a"], True),
        ResetFormAction(),
    ],
    ids=["submit-exclude-xfdf", "submit-plain", "reset-exclude", "reset-all"],
)
def test_a_form_action_bookmark_keeps_its_fields_and_flags(target):
    """The flag word carries both the include/exclude sense and the format."""
    document = _bookmarked(OutlineItem("act", destination=target))

    assert _titles(document)["act"].destination == target


# ---------------------------------------------------------------------------
# The page, not the number
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("edit", "label"),
    [
        (lambda d: d.pages.delete(0), "delete before"),
        (lambda d: d.pages.delete(1), "delete between"),
        (lambda d: d.pages.insert(0), "insert before"),
        (lambda d: d.pages.insert(2), "insert between"),
    ],
    ids=["delete-before", "delete-between", "insert-before", "insert-between"],
)
def test_a_bookmark_follows_its_page_through_an_edit(edit, label):
    document = _bookmarked(
        OutlineItem("plain", 3),
        OutlineItem("zoomed", destination=XYZDestination(3, 0.0, 700.0, 1.0)),
    )
    edit(document)

    again = _reloaded(document)
    items = _titles(again)

    assert _lands_on(again, items["plain"]) == "PAGE 3"
    assert _lands_on(again, items["zoomed"]) == "PAGE 3"


def test_the_page_index_reads_true_after_an_edit():
    """The index is a view of the reference, not a second source of truth."""
    document = _bookmarked(OutlineItem("plain", 3))
    document.pages.delete(0)

    assert _titles(document)["plain"].page_index == 2


def test_a_bookmark_to_a_deleted_page_falls_back_to_its_index():
    """Its page object survives deletion, so writing the reference back would
    keep the bookmark by making it dead."""
    document = _bookmarked(OutlineItem("plain", 3), OutlineItem("early", 1))
    document.pages.delete(3)

    again = _reloaded(document)

    assert _lands_on(again, _titles(again)["early"]) == "PAGE 1"
    assert _titles(again)["plain"].page_index == len(again.pages) - 1


# ---------------------------------------------------------------------------
# Naming a target replaces it
# ---------------------------------------------------------------------------


def test_setting_the_page_index_replaces_the_loaded_target():
    document = _bookmarked(
        OutlineItem("zoomed", destination=XYZDestination(3, 0.0, 700.0, 2.0))
    )
    _titles(document)["zoomed"].page_index = 1

    zoomed = _titles(_reloaded(document))["zoomed"]

    assert zoomed.destination == FitDestination(page=1)


def test_setting_the_destination_replaces_the_loaded_target():
    document = _bookmarked(OutlineItem("plain", 3))
    _titles(document)["plain"].destination = URIAction("https://example.com")

    plain = _titles(_reloaded(document))["plain"]

    assert plain.destination == URIAction("https://example.com")


def test_a_replaced_target_no_longer_follows_the_old_page():
    """Naming an index means an index, and the identity is given up with it."""
    document = _bookmarked(OutlineItem("plain", 3))
    _titles(document)["plain"].page_index = 3
    document.pages.insert(0)

    again = _reloaded(document)

    assert _lands_on(again, _titles(again)["plain"]) == "PAGE 2"


def test_a_freshly_authored_bookmark_has_no_loaded_target():
    item = OutlineItem("new", 2)

    assert item._loaded_target is None
    assert item.destination is None


# ---------------------------------------------------------------------------
# Nothing else moved
# ---------------------------------------------------------------------------


def test_an_unchanged_document_with_typed_targets_saves_byte_for_byte():
    parent = OutlineItem("Chapter", destination=XYZDestination(1, 0.0, 700.0, 1.0))
    parent.add(OutlineItem("Web", destination=URIAction("https://ex.com")))
    base = _saved(_bookmarked(parent))

    assert _saved(Document(io.BytesIO(base))) == base


def test_an_unchanged_incremental_save_still_appends_nothing():
    base = _saved(
        _bookmarked(OutlineItem("web", destination=URIAction("https://e.co")))
    )

    assert _saved(Document(io.BytesIO(base)), incremental=True) == base


def test_a_remote_bookmark_keeps_its_remote_page_number():
    """`GoToR` counts pages in the other file; resolving it here would be
    a jump into this document instead."""
    document = _bookmarked(
        OutlineItem("remote", destination=GoToRAction("other.pdf", FitDestination(2)))
    )

    remote = _titles(_reloaded(document))["remote"]

    assert remote.destination == GoToRAction("other.pdf", FitDestination(page=2))


def test_a_bookmark_whose_dest_array_is_malformed_is_kept_as_it_is():
    data = _hand_built(("odd", "Dest", PdfArray([PdfNumber(1)])))
    document = Document(io.BytesIO(data))

    assert _titles(document)["odd"].destination is None
    assert b"/Dest" in _saved(document)

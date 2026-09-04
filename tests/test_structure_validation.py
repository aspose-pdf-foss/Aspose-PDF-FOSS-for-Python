"""What ``validate()`` says about a document, and what ``repair()`` leaves.

The structural check walked the object graph and called it invalid if any node
was reachable from itself. A PDF object graph is not a tree: a page's
``/Parent``, an annotation's ``/P``, an outline's ``/Prev`` all point back, and
``/Parent`` is *required* on every page (ISO 32000-1 7.7.3.2). So ``validate()``
returned ``False`` for every conforming document -- including every document
this library wrote -- and delete the required entry and it returned ``True``.

It returned ``True`` for genuinely broken files too, because the shapes that are
actually wrong are still perfectly traversable graphs: a page missing from its
parent's ``/Kids`` is simply never reached, and a catalog whose ``/Pages`` names
nothing resolves to nothing and ends the walk.

The walk now only has to terminate; what makes a page tree a page tree is
checked directly. The first thing that found was `repair()` leaving the model
claiming a page the file did not have.
"""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.cos import PdfArray, PdfDictionary, PdfName, PdfNumber
from aspose_pdf.engine.simple_pdf import SimplePdf

GOOD = (
    b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n"
    b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
    b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
    b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >> endobj\n"
    b"trailer << /Root 1 0 R /Size 4 >>\n%%EOF\n"
)


def _validated(data: bytes) -> bool:
    return Document(io.BytesIO(data)).validate()


def _saved(document: Document) -> bytes:
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# A conforming document is valid
# ---------------------------------------------------------------------------


def test_a_document_this_library_wrote_is_valid():
    document = Document()
    document.pages.add().add_text("hello", 72, 700)

    assert Document(io.BytesIO(_saved(document))).validate() is True


def test_a_page_may_point_back_at_its_parent():
    """`/Parent` is required on every page, so rejecting the back-reference
    rejected every conforming document there is."""
    assert _validated(GOOD) is True


def test_an_annotation_pointing_back_at_its_page_is_not_corruption():
    document = Document()
    document.pages.add()
    document.pages[0].annotations.add("Text", (10, 10, 40, 40), "x")

    assert Document(io.BytesIO(_saved(document))).validate() is True


def test_a_document_with_bookmarks_is_valid():
    """An outline item names its parent, its previous and its next."""
    from aspose_pdf.outlines import OutlineItem

    document = Document()
    document.pages.add()
    document.pages.add()
    document.outlines.add(OutlineItem("one", 0))
    document.outlines.add(OutlineItem("two", 1))

    assert Document(io.BytesIO(_saved(document))).validate() is True


def test_a_nested_page_tree_is_valid():
    document = Document()
    for _ in range(4):
        document.pages.add()
    engine = document._engine_pdf
    engine._ensure_page_cache()
    root_ref = engine._resolve(
        engine._cos_doc.trailer.mapping.get(PdfName("Root"))
    ).mapping[PdfName("Pages")]
    root = engine._resolve(root_ref)
    kids = root.mapping[PdfName("Kids")]
    branch = PdfDictionary(
        {
            PdfName("Type"): PdfName("Pages"),
            PdfName("Kids"): PdfArray(kids.items[2:]),
            PdfName("Count"): PdfNumber(2),
            PdfName("Parent"): root_ref,
        }
    )
    branch_ref = engine._cos_doc.register_object(branch)
    for kid in kids.items[2:]:
        engine._resolve(kid).mapping[PdfName("Parent")] = branch_ref
    kids.items[2:] = [branch_ref]
    engine._page_cache_valid = False

    assert Document(io.BytesIO(_saved(document))).validate() is True


# ---------------------------------------------------------------------------
# A broken one is not
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "data"),
    [
        ("count disagrees", GOOD.replace(b"/Count 1", b"/Count 9")),
        ("page not listed", GOOD.replace(b"/Kids [3 0 R]", b"/Kids []")),
        ("no media box", GOOD.replace(b" /MediaBox [0 0 612 792]", b"")),
        ("pages names nothing", GOOD.replace(b"/Pages 2 0 R", b"/Pages 99 0 R")),
        ("parent does not list it", GOOD.replace(b"/Parent 2 0 R", b"/Parent 1 0 R")),
    ],
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_a_page_tree_that_is_not_one_is_invalid(label, data):
    assert _validated(data) is False


def test_a_page_listed_twice_is_invalid():
    """Built in memory, because the loader refuses such a file outright --
    `validate()` also answers for documents that were never loaded."""
    document = Document()
    document.pages.add()
    engine = document._engine_pdf
    engine._ensure_page_cache()
    root = engine._resolve(
        engine._resolve(engine._cos_doc.trailer.mapping.get(PdfName("Root"))).mapping[
            PdfName("Pages")
        ]
    )
    kids = root.mapping[PdfName("Kids")]
    kids.items.append(kids.items[0])
    root.mapping[PdfName("Count")] = PdfNumber(2)
    engine.pages.append(engine.pages[0])

    assert engine.validate() is False


def test_a_page_the_tree_does_not_have_is_invalid():
    """The model and the page tree have to agree on how many pages there are:
    the model is what the library answers from, the tree is what gets written."""
    document = Document()
    document.pages.add()
    engine = document._engine_pdf
    engine.pages.append((0, 0, 612, 792))
    engine.page_contents.append(b"")

    assert engine.validate() is False


def test_a_graph_deeper_than_the_caller_s_bound_is_invalid():
    """Not a cycle -- the walk terminates -- but a structure deeper than the
    caller is willing to follow. Past the *load limit* it raises instead, which
    is what a resource limit is for; `max_depth` is the softer, caller-set one.
    """
    document = Document()
    document.pages.add()
    engine = document._engine_pdf
    nest = PdfArray([])
    engine._cos_doc.trailer[PdfName("Zz")] = nest
    for _ in range(20):
        deeper = PdfArray([])
        nest.items.append(deeper)
        nest = deeper

    assert engine.validate(max_depth=10) is False
    assert engine.validate() is True


def test_a_document_with_no_pages_is_invalid():
    document = Document()
    document.pages.clear()

    assert document.validate() is False


def test_check_says_the_same_thing():
    document = Document()
    document.pages.add()

    assert document.check() == document.validate()


# ---------------------------------------------------------------------------
# What repair leaves behind
# ---------------------------------------------------------------------------


def test_a_salvaged_document_is_repaired_into_a_valid_one():
    """A file with nothing but a header and an end marker. `repair()` gave the
    model a page and left the page tree empty, so the document claimed a page
    that its own save did not write."""
    engine = SimplePdf.from_bytes_safe(b"%PDF-1.7\n%%EOF")

    assert engine.validate() is True

    reloaded = Document(io.BytesIO(engine.to_bytes()))
    assert len(reloaded.pages) == 1
    assert reloaded.validate() is True


def test_a_salvaged_document_writes_the_page_it_claims():
    engine = SimplePdf.from_bytes_safe(b"%PDF-1.7\n%%EOF")

    data = engine.to_bytes()

    assert b"/Type /Page" in data
    assert b"/Count 1" in data


def test_repair_leaves_a_sound_document_alone():
    document = Document()
    document.pages.add().add_text("hello", 72, 700)
    before = _saved(document)

    document.repair()

    assert Document(io.BytesIO(before)).validate() is True
    assert len(document.pages) == 1

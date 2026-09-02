import io

import pytest

from aspose_pdf.document import Document
from aspose_pdf.engine.cos import PdfArray
from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.pages import Page


@pytest.fixture
def document():
    """Create a Document for testing."""
    return Document()


@pytest.fixture
def page_collection(document):
    """Create a PageCollection with three pages."""
    pc = document.pages
    pc.add()
    pc.add()
    pc.add()
    return pc


def test_page_collection_is_iterable(page_collection):
    """PageCollection should be iterable."""
    pages = list(page_collection)
    assert len(pages) == 3
    assert all(isinstance(p, Page) for p in pages)


def test_page_collection_supports_indexing(page_collection):
    """PageCollection should support indexing."""
    first = page_collection[0]
    assert isinstance(first, Page)
    assert first.index == 0

    last = page_collection[-1]
    assert isinstance(last, Page)
    assert last.index == 2

    with pytest.raises(IndexError):
        _ = page_collection[10]

    with pytest.raises(IndexError):
        page_collection.item(10)


def test_page_accept_dispatches_to_visitor(document):
    page = document.pages.add()

    class Visitor:
        def __init__(self):
            self.page = None

        def visit(self, visited_page):
            self.page = visited_page

    visitor = Visitor()
    page.accept(visitor)

    assert visitor.page is page


def test_page_accept_rejects_invalid_visitor(document):
    page = document.pages.add()

    with pytest.raises(TypeError, match=r"callable visit\(page\)"):
        page.accept(object())


def test_insert_at_0(document):
    """Insert a page at the beginning of a non-empty collection."""
    pages = document.pages
    pages.add()
    original_len = len(pages)
    pages.insert(0)
    assert len(pages) == original_len + 1


def test_insert_middle(document):
    """Insert a page into the middle of a collection."""
    pages = document.pages
    pages.add()
    pages.add()
    pages.add()
    original_len = len(pages)
    pages.insert(1)
    assert len(pages) == original_len + 1


def test_insert_end(document):
    """Insert a page at the end of the collection."""
    pages = document.pages
    pages.add()
    pages.add()
    original_len = len(pages)
    pages.insert(len(pages))
    assert len(pages) == original_len + 1


def test_insert_index_out_of_range(document):
    """Inserting beyond the current length should clamp to end (no error)."""
    pages = document.pages
    pages.add()
    original_len = len(pages)
    pages.insert(100)
    assert len(pages) == original_len + 1


def test_insert_negative_index(document):
    """Negative indices are clamped to 0 for insertion."""
    pages = document.pages
    pages.add()
    original_len = len(pages)
    pages.insert(-1)
    assert len(pages) == original_len + 1


def test_delete_decreases_page_count(document):
    """Delete a page and verify count decreases."""
    pages = document.pages
    pages.add()
    pages.add()
    pages.add()
    assert len(pages) == 3
    pages.delete(1)
    assert len(pages) == 2


def test_delete_last_page(document):
    """Delete the last page."""
    pages = document.pages
    pages.add()
    pages.add()
    assert len(pages) == 2
    pages.delete(1)
    assert len(pages) == 1


def test_delete_index_out_of_range(document):
    """Deleting with out-of-range index should raise."""
    pages = document.pages
    pages.add()
    with pytest.raises(IndexError):
        pages.delete(5)


def test_add_increases_page_count(document):
    """Append a new page and verify count increases."""
    pages = document.pages
    initial_len = len(pages)
    pages.add()
    assert len(pages) == initial_len + 1


def test_adding_to_disposed_document_raises(document):
    """Adding a page to a disposed document should raise."""
    pages = document.pages
    document.dispose()
    with pytest.raises(Exception):
        pages.add()


def test_add_to_disposed_document_raises(document):
    """Adding a page to a disposed document should raise."""
    document.dispose()
    with pytest.raises(Exception):
        document.pages.add()


def test_insert_index_out_of_range_raises(document):
    """Inserting at out-of-range index should clamp, not raise."""
    pages = document.pages
    original = len(pages)
    pages.insert(len(pages) + 1)
    assert len(pages) == original + 1


def test_insert_negative_index_raises(document):
    """Inserting at negative index should clamp to 0, not raise."""
    pages = document.pages
    original = len(pages)
    pages.insert(-1)
    assert len(pages) == original + 1


# ------- Document-based tests -------


def test_add_increases_page_count_doc(document):
    """Append a new page and verify count increases."""
    pages = document.pages
    initial = len(pages)
    pages.add()
    assert len(pages) == initial + 1


def test_delete_decreases_page_count_doc(document):
    """Delete a page and verify count decreases."""
    pages = document.pages
    if len(pages) < 2:
        pages.add()
        pages.add()
    initial = len(pages)
    pages.delete(0)
    assert len(pages) == initial - 1


def test_delete_last_page_doc(document):
    """Delete the last page and verify operation succeeds."""
    pages = document.pages
    if len(pages) == 0:
        pages.add()
    initial = len(pages)
    pages.delete(initial - 1)
    assert len(pages) == initial - 1


def test_delete_index_out_of_range_raises(document):
    """Deleting with an out-of-range index should raise."""
    pages = document.pages
    count = len(pages)
    with pytest.raises(IndexError):
        pages.delete(count)


def test_page_collection_iterable(document):
    """PageCollection should be iterable."""
    pages = document.pages
    if len(pages) == 0:
        pages.add()
    iterated = list(pages)
    assert len(iterated) == len(pages)


def test_page_collection_indexing(document):
    """PageCollection should support indexing."""
    pages = document.pages
    while len(pages) < 2:
        pages.add()
    first_page = pages[0]
    assert first_page is not None
    with pytest.raises(IndexError):
        _ = pages[len(pages)]


def test_insert_at_0_doc(document):
    """Insert a page at index 0."""
    pages = document.pages
    while len(pages) < 2:
        pages.add()
    original_len = len(pages)
    pages.insert(0)
    assert len(pages) == original_len + 1


def test_insert_middle_doc(document):
    """Insert a page in the middle of the collection."""
    pages = document.pages
    while len(pages) < 3:
        pages.add()
    original_len = len(pages)
    middle_index = original_len // 2
    pages.insert(middle_index)
    assert len(pages) == original_len + 1


def test_insert_end_doc(document):
    """Insert a page at the end of the collection."""
    pages = document.pages
    original_len = len(pages)
    pages.insert(original_len)
    assert len(pages) == original_len + 1


# ---------------------------------------------------------------------------
# Inserting a copy of an existing page
# ---------------------------------------------------------------------------

# A 1x1 opaque PNG, so the copied page has an XObject to lose.
_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
    "3d780000000c4944415408d763f8cfc000000301010018dd8db0"
    "0000000049454e44ae426082"
)


def _document_with_a_furnished_page() -> bytes:
    document = Document()
    page = document.pages.add()
    page.add_text("SOURCE TEXT", 60, 700, font_size=30)
    page.add_image(_PNG, 100, 500, width=120, height=120)
    page.annotations.add("Square", (60, 300, 200, 400), "a note")
    document.pages.add().add_text("OTHER", 60, 700, font_size=30)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _page_with_an_empty_content_stream() -> bytes:
    """A page that *has* a ``/Contents``, which decodes to nothing."""
    return _hand_built(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>",
            3: (
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
                b"/Contents 5 0 R /Resources << >> >>"
            ),
            4: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>",
            5: b"<< /Length 0 >>\nstream\n\nendstream",
        }
    )


def _nested_page_tree() -> bytes:
    """A PDF whose page tree has an intermediate node, as producers write."""
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 2 >>",
        3: b"<< /Type /Pages /Parent 2 0 R /Kids [4 0 R 5 0 R] /Count 2 >>",
        4: (
            b"<< /Type /Page /Parent 3 0 R /MediaBox [0 0 200 200] "
            b"/Contents 6 0 R /Resources << >> >>"
        ),
        5: b"<< /Type /Page /Parent 3 0 R /MediaBox [0 0 200 200] >>",
    }
    body = b"BT /F1 12 Tf 10 100 Td (A) Tj ET"
    objects[6] = b"<< /Length %d >>\nstream\n%s\nendstream" % (len(body), body)
    return _hand_built(objects)


def _hand_built(objects: dict[int, bytes]) -> bytes:
    """Assemble numbered object bodies into a minimal, valid PDF."""
    out = bytearray(b"%PDF-1.7\n")
    offsets = {}
    for number in sorted(objects):
        offsets[number] = len(out)
        out += b"%d 0 obj\n" % number + objects[number] + b"\nendobj\n"
    start = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (max(objects) + 1)
    for number in sorted(objects):
        out += b"%010d 00000 n \n" % offsets[number]
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        max(objects) + 1,
        start,
    )
    return bytes(out)


def _ink(page) -> int:
    raster = page.render(dpi=72, antialias=False)
    return sum(
        1
        for x in range(50, 400, 3)
        for y in range(50, 400, 3)
        if raster.get_pixel(x, y) != (255, 255, 255)
    )


def _reloaded(document: Document) -> Document:
    buffer = io.BytesIO()
    document.save(buffer)
    return Document(io.BytesIO(buffer.getvalue()))


def test_an_inserted_page_draws_what_the_original_draws():
    """The copy used to come out blank.

    Only the media box and the content bytes were carried over, so the new
    page named fonts and images that were not in *its* resources -- every
    reference dangling, nothing drawn, and no structural error to show for it.
    """
    document = Document(io.BytesIO(_document_with_a_furnished_page()))
    document.pages.insert(1, document.pages[0])
    reloaded = _reloaded(document)

    assert len(reloaded.pages) == 3
    assert _ink(reloaded.pages[1]) == _ink(reloaded.pages[0]) > 0
    assert _ink(reloaded.pages[2]) < _ink(reloaded.pages[0])


def test_a_copied_page_keeps_its_rotation_and_boxes():
    document = Document(io.BytesIO(_document_with_a_furnished_page()))
    document.pages[0].rotation = 90
    document.pages[0].crop_box = (10, 10, 500, 700)
    document.pages.insert(1, document.pages[0])

    reloaded = _reloaded(document)
    assert reloaded.pages[1].rotation == 90
    assert reloaded.pages[1].crop_box == reloaded.pages[0].crop_box


def test_the_two_pages_can_be_edited_apart():
    """Sharing the content stream would make one edit change both pages."""
    document = Document(io.BytesIO(_document_with_a_furnished_page()))
    document.pages.insert(1, document.pages[0])

    document.pages[1].add_text("ONLY ON THE COPY", 60, 200, font_size=12)
    reloaded = _reloaded(document)

    assert b"ONLY ON THE COPY" in reloaded.pages[1].content
    assert b"ONLY ON THE COPY" not in reloaded.pages[0].content


def test_a_copied_page_gets_its_own_annotations():
    document = Document(io.BytesIO(_document_with_a_furnished_page()))
    document.pages.insert(1, document.pages[0])
    reloaded = _reloaded(document)

    assert len(reloaded.pages[1].annotations) == 1
    assert reloaded.pages[1].annotations[0].contents == "a note"
    # Distinct objects: moving one must not move the other.
    reloaded.pages[1].annotations[0].rect = (0, 0, 10, 10)
    assert reloaded.pages[0].annotations[0].rect != (0, 0, 10, 10)


def test_a_form_field_is_not_duplicated_onto_the_copy():
    """A widget is a field's presence on a page, not decoration.

    Copying one would put a single field in two places, where typing in either
    changes both -- so the copy is made without it.
    """
    document = Document()
    document.pages.add()
    document.form.add_text_field("nickname", 0, (60, 100, 260, 130), value="typed")
    document = _reloaded(document)

    document.pages.insert(1, document.pages[0])
    reloaded = _reloaded(document)

    assert [field.name for field in reloaded.form] == ["nickname"]
    assert len(reloaded.pages[1].annotations) == 0


def test_a_copied_page_does_not_share_its_annotations_before_a_save():
    """A copy has to be independent in memory, not only once written.

    Registering the same annotation object under two pages lets an edit to one
    change the other in the same session -- and a round trip hides it, since
    the writer emits an object per reference.
    """
    document = Document(io.BytesIO(_document_with_a_furnished_page()))
    document.pages.insert(1, document.pages[0])

    document.pages[1].annotations[0].rect = (0, 0, 10, 10)

    assert document.pages[0].annotations[0].rect != (0, 0, 10, 10)


def test_a_copied_annotation_says_which_page_it_is_on():
    """``/P`` names the page; left pointing at the original it is simply wrong."""
    from aspose_pdf.engine.cos import PdfName

    document = Document(io.BytesIO(_document_with_a_furnished_page()))
    document.pages.insert(1, document.pages[0])
    engine = document._engine_pdf

    page = engine._cos_doc.objects[engine._page_obj_ids[1]]
    annots = engine._resolve(page.mapping[PdfName("Annots")])
    annot = engine._resolve(annots.items[0])

    assert annot.mapping[PdfName("P")].object_number == engine._page_obj_ids[1]


def test_a_copied_empty_page_gets_its_own_content_stream():
    """A copy owns its stream even when there is nothing in it yet.

    A new stream is otherwise written only when there is content to write, so a
    page with an *empty* ``/Contents`` is the one case where the copy would
    inherit the reference instead. Nothing reachable through the API mutates an
    empty stream in place today -- appending makes an array rather than editing
    it -- so this states the invariant where it lives, on the graph, rather
    than waiting for an operation that would trip over it.
    """
    from aspose_pdf.engine.cos import PdfName

    document = Document(io.BytesIO(_page_with_an_empty_content_stream()))
    document.pages.insert(1, document.pages[0])
    engine = document._engine_pdf

    def contents_of(index: int):
        page = engine._cos_doc.objects[engine._page_obj_ids[index]]
        return page.mapping.get(PdfName("Contents"))

    source, copy = contents_of(0), contents_of(1)
    assert copy is not None
    assert copy.object_number != source.object_number


def test_a_copy_hangs_from_the_node_that_lists_it():
    """``/Parent`` has to name the node whose ``/Kids`` the page is in.

    A document with a *nested* page tree is where this shows: inheriting the
    source's parent points the copy at a branch that does not list it, and
    everything inheritable -- resources, boxes, rotation -- is then resolved up
    the wrong chain.
    """
    from aspose_pdf.engine.cos import PdfName

    document = Document(io.BytesIO(_nested_page_tree()))
    document.pages.insert(2, document.pages[0])
    engine = document._engine_pdf

    copied_id = engine._page_obj_ids[2]
    page = engine._cos_doc.objects[copied_id]
    parent = page.mapping[PdfName("Parent")].object_number
    listing = [
        number
        for number, obj in engine._cos_doc.objects.items()
        if isinstance(getattr(obj, "mapping", None), dict)
        and isinstance(engine._resolve(obj.mapping.get(PdfName("Kids"))), PdfArray)
        and any(
            getattr(kid, "object_number", None) == copied_id
            for kid in engine._resolve(obj.mapping[PdfName("Kids")]).items
        )
    ]

    assert listing == [parent]


def test_a_page_from_another_document_is_refused():
    """Its resources live in that document's graph; merge brings them across."""
    document = Document(io.BytesIO(_document_with_a_furnished_page()))
    other = Document(io.BytesIO(_document_with_a_furnished_page()))

    with pytest.raises(PdfValidationException, match="merge"):
        document.pages.insert(0, other.pages[0])

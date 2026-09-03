"""Merging brings a document across, not a tracing of its pages.

``Document.merge`` copied each page's rectangle and its content bytes and
nothing else. A page without its ``/Resources`` names fonts and images that
resolve to no object, so it drew blank -- or, worse, drew with whatever the
*target* happened to have registered under the same name, which for two
documents built by this library is always ``/F1``. Text merged in from another
file was rendered in the wrong typeface with no sign that anything was wrong.
Everything else the document held -- annotations, form fields, bookmarks,
attachments, layers -- was dropped without a word.

A page is now imported: its dictionary is copied into the target's graph along
with every object it reaches, once each, with references remapped. The
document-level structures the pages belong to come with them, because a widget
whose field is not in ``/AcroForm /Fields`` is a control the form does not know
about, and an optional content group missing from ``/OCProperties`` is not a
layer any viewer offers to switch.
"""

from __future__ import annotations

import io
import struct
import zlib

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.cos import PdfName
from aspose_pdf.interactive import FitDestination
from aspose_pdf.outlines import OutlineItem


def _png() -> bytes:
    def chunk(tag: bytes, payload: bytes) -> bytes:
        body = tag + payload
        return (
            struct.pack(">I", len(payload))
            + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
        + chunk(b"IEND", b"")
    )


PNG = _png()


def _saved(document: Document) -> bytes:
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _reloaded(document: Document) -> Document:
    return Document(io.BytesIO(_saved(document)))


def _doc(*labels: str, font: str | None = None) -> Document:
    document = Document()
    for label in labels:
        document.pages.add().add_text(
            label, 72, 700, font_size=36, font_name=font or "Helvetica"
        )
    return document


def _text(document: Document) -> list[str]:
    return [
        document.pages[index].to_markdown().strip().split("\n")[0]
        for index in range(len(document.pages))
    ]


def _resources(document: Document, index: int) -> tuple[list[str], list[str]]:
    engine = document._engine_pdf
    engine._ensure_page_cache()
    page = engine._cos_doc.objects.get(engine._page_refs[index])
    found = engine._get_inherited_attr(page, "Resources")
    fonts = engine._resolve(found.mapping.get(PdfName("Font"))) if found else None
    xobjects = engine._resolve(found.mapping.get(PdfName("XObject"))) if found else None
    return (
        sorted(key.name for key in fonts.mapping) if fonts else [],
        sorted(key.name for key in xobjects.mapping) if xobjects else [],
    )


def _base_font(document: Document, index: int, resource: str) -> str:
    engine = document._engine_pdf
    engine._ensure_page_cache()
    page = engine._cos_doc.objects.get(engine._page_refs[index])
    fonts = engine._resolve(
        engine._get_inherited_attr(page, "Resources").mapping.get(PdfName("Font"))
    )
    font = engine._resolve(fonts.mapping.get(PdfName(resource)))
    return engine._get_name(font.mapping.get(PdfName("BaseFont")))


def _ink(page, box=None, dpi=40) -> int:
    raster = page.render(dpi=dpi)
    scale = dpi / 72.0
    count = 0
    for y in range(raster.height):
        for x in range(raster.width):
            if raster.get_pixel(x, y)[:3] == (255, 255, 255):
                continue
            if box is not None:
                ux, uy = x / scale, (raster.height - y) / scale
                if not (box[0] <= ux <= box[2] and box[1] <= uy <= box[3]):
                    continue
            count += 1
    return count


IMAGE_BOX = (100, 500, 180, 580)


# ---------------------------------------------------------------------------
# The page arrives whole
# ---------------------------------------------------------------------------


def test_a_merged_page_draws_what_it_drew():
    source = _doc("B0")
    source.pages[0].add_image(PNG, 100, 500, width=80, height=80)
    alone = _ink(_reloaded(source).pages[0], IMAGE_BOX)

    target = _doc("A0")
    target.merge(source)

    assert _ink(_reloaded(target).pages[1], IMAGE_BOX) == alone
    assert alone > 0


def test_a_merged_page_keeps_its_own_resources():
    source = _doc("B0")
    source.pages[0].add_image(PNG, 100, 500, width=80, height=80)
    target = _doc("A0")
    target.merge(source)

    merged = _reloaded(target)

    assert _resources(merged, 0) == (["/F1"], [])
    assert _resources(merged, 1) == (["/F1"], ["/Im1"])


def test_two_documents_that_both_call_their_font_f1_keep_their_own():
    """Both sides name the font `/F1`, so an inherited resource silently fits.

    Nothing is missing and nothing errors -- the merged text simply renders in
    the other document's typeface.
    """
    target = _doc("A0", font="Helvetica")
    target.merge(_doc("B0", font="Times-BoldItalic"))

    merged = _reloaded(target)

    assert _base_font(merged, 0, "/F1") == "Helvetica"
    assert _base_font(merged, 1, "/F1") == "Times-BoldItalic"


def test_a_page_merged_from_a_loaded_file_resolves_its_inherited_resources():
    """A loaded page may hold nothing itself; its `/Resources` sit above it."""
    source = _reloaded(_doc("SRC", font="Courier-Bold"))
    target = _doc("A0")
    target.merge(source)

    merged = _reloaded(target)

    assert _text(merged) == ["A0", "SRC"]
    assert _base_font(merged, 1, "/F1") == "Courier-Bold"


def test_the_source_document_is_not_changed_by_being_merged():
    source = _doc("B0", "B1")
    source.outlines.add(OutlineItem("B", 1))
    before = _saved(source)

    _doc("A0").merge(source)

    assert _saved(source) == before


def test_a_page_whose_resources_live_on_an_ancestor_still_gets_them():
    """Plenty of producers put one `/Resources` on the page tree and let every
    page inherit it. The import does not follow `/Parent` -- that is the whole
    source tree -- so what the page inherited has to be resolved onto it."""
    source = _reloaded(_doc("SRC", font="Courier-Bold"))
    engine = source._engine_pdf
    engine._ensure_page_cache()
    page = engine._cos_doc.objects.get(engine._page_refs[0])
    root = engine._resolve(
        engine._resolve(
            engine._cos_doc.trailer.mapping.get(PdfName("Root"))
        ).mapping.get(PdfName("Pages"))
    )
    root.mapping[PdfName("Resources")] = page.mapping.pop(PdfName("Resources"))

    target = _doc("A0")
    target.merge(source)

    merged = _reloaded(target)

    assert _base_font(merged, 1, "/F1") == "Courier-Bold"


def test_a_merged_page_belongs_to_this_document_s_page_tree():
    """`/Parent` has to name a node of *this* catalog's tree.

    Importing the source's names a copy of a node from the other document's
    tree. That copy lists the page -- its `/Kids` are remapped like everything
    else -- so it looks right until you ask what tree it is *in*: nothing in
    this catalog reaches it, and the page sits in two trees at once, one of
    them floating free with the subtree it came from.
    """
    target = _doc("A0")
    target.merge(_doc("B0"))

    engine = _reloaded(target)._engine_pdf
    engine._ensure_page_cache()
    catalog = engine._resolve(engine._cos_doc.trailer.mapping.get(PdfName("Root")))
    nodes: list[int] = []
    stack = [catalog.mapping.get(PdfName("Pages"))]
    while stack:
        ref = stack.pop()
        node = engine._resolve(ref)
        if node is None:
            continue
        if engine._get_name(node.mapping.get(PdfName("Type"))) != "Pages":
            continue
        nodes.append(ref.object_number)
        kids = engine._resolve(node.mapping.get(PdfName("Kids")))
        stack.extend(kids.items if kids else [])

    page = engine._cos_doc.objects.get(engine._page_refs[1])

    assert page.mapping.get(PdfName("Parent")).object_number in nodes


def test_pages_that_shared_a_resource_object_still_share_it():
    """Sharing is the source document's own arrangement, and copying an object
    once per page that names it would quietly inflate the file."""
    source = _doc("B0", "B1")
    engine = source._engine_pdf
    engine._ensure_page_cache()
    first = engine._cos_doc.objects.get(engine._page_refs[0])
    second = engine._cos_doc.objects.get(engine._page_refs[1])
    shared = engine._cos_doc.register_object(
        engine._resolve(first.mapping.get(PdfName("Resources")))
    )
    first.mapping[PdfName("Resources")] = shared
    second.mapping[PdfName("Resources")] = shared

    target = _doc("A0")
    target.merge(source)

    engine = target._engine_pdf
    engine._ensure_page_cache()
    pages = [engine._cos_doc.objects.get(number) for number in engine._page_refs[1:]]
    resources = [page.mapping.get(PdfName("Resources")) for page in pages]

    assert resources[0].object_number == resources[1].object_number


def test_the_merged_document_shares_no_object_with_the_source():
    """Importing means copying. Holding the source's objects would tie the two
    documents together for as long as both are open."""
    source = _doc("B0")
    source.pages[0].add_image(PNG, 100, 500, width=80, height=80)
    source.pages[0].annotations.add("Text", (10, 10, 40, 40), "note B")
    target = _doc("A0")
    target.merge(source)

    theirs = {id(obj) for obj in source._engine_pdf._cos_doc.objects.values()}
    ours = {id(obj) for obj in target._engine_pdf._cos_doc.objects.values()}

    assert not (theirs & ours)


# ---------------------------------------------------------------------------
# What the pages belong to
# ---------------------------------------------------------------------------


def test_annotations_come_with_their_pages():
    source = _doc("B0", "B1")
    source.pages[0].annotations.add("Text", (10, 10, 40, 40), "note B")
    target = _doc("A0")
    target.merge(source)

    merged = _reloaded(target)

    assert [a.contents for a in merged.pages[1].annotations] == ["note B"]
    assert list(merged.pages[0].annotations) == []


def test_a_merged_link_points_at_the_merged_page():
    """Not at the page that happens to sit at the same index in the target."""
    source = _doc("B0", "B1")
    source.pages[0].add_link((72, 600, 200, 620), FitDestination(1))
    target = _doc("A0", "A1")
    target.merge(source)

    merged = _reloaded(target)
    (link,) = list(merged.pages[2].annotations)

    assert link.properties["Dest"] == FitDestination(page=3)


def test_a_widget_arrives_with_the_field_it_belongs_to():
    source = _doc("B0")
    source.form.add_text_field("bfield", 0, (10, 10, 200, 30))
    target = _doc("A0")
    target.merge(source)

    merged = _reloaded(target)

    assert [field.name for field in merged.form] == ["bfield"]
    assert [a.subtype for a in merged.pages[1].annotations] == ["Widget"]


def test_bookmarks_are_appended_pointing_at_the_merged_pages():
    source = _doc("B0", "B1")
    source.outlines.add(OutlineItem("B second", 1))
    target = _doc("A0", "A1")
    target.outlines.add(OutlineItem("A second", 1))
    target.merge(source)

    merged = _reloaded(target)

    assert [(o.title, o.page_index) for o in merged.outlines] == [
        ("A second", 1),
        ("B second", 3),
    ]
    assert merged.pages[3].to_markdown().strip() == "B1"


def test_a_bookmark_read_from_a_file_points_at_the_merged_page():
    """A loaded bookmark keeps the target the file held, page reference and
    all -- and that reference names a page of the *source*."""
    source = _doc("B0", "B1")
    source.outlines.add(OutlineItem("B second", 1))
    loaded = _reloaded(source)

    target = _doc("A0", "A1")
    target.merge(loaded)

    merged = _reloaded(target)
    (bookmark,) = list(merged.outlines)

    assert bookmark.destination == FitDestination(page=3)
    assert merged.pages[3].to_markdown().strip() == "B1"


def test_attachments_and_layers_come_across():
    source = _doc("B0")
    source.add_attachment("b.txt", b"from B", mime="text/plain")
    source.layers.add("layer B")
    target = _doc("A0")
    target.add_attachment("a.txt", b"from A")
    target.layers.add("layer A")
    target.merge(source)

    merged = _reloaded(target)

    assert sorted(merged.attachments) == ["a.txt", "b.txt"]
    assert merged.attachments["b.txt"] == b"from B"
    assert sorted(layer.name for layer in merged.layers) == ["layer A", "layer B"]


# ---------------------------------------------------------------------------
# Two documents that named things the same
# ---------------------------------------------------------------------------


def test_a_clashing_attachment_name_does_not_replace_the_file_already_there():
    """The name is the key of one name tree, so the two cannot both keep it."""
    target = _doc("A0")
    target.add_attachment("notes.txt", b"from A")
    source = _doc("B0")
    source.add_attachment("notes.txt", b"from B")
    target.merge(source)

    merged = _reloaded(target)

    assert merged.attachments["notes.txt"] == b"from A"
    assert merged.attachments["notes_2.txt"] == b"from B"


def test_a_clashing_field_name_becomes_a_second_field():
    """Two fields of one name are *one* field in PDF: filling either would
    fill both, which is not what merging two forms means."""
    target = _doc("A0")
    target.form.add_text_field("name", 0, (10, 10, 200, 30))
    target.form["name"].value = "from A"
    source = _doc("B0")
    source.form.add_text_field("name", 0, (10, 10, 200, 30))
    source.form["name"].value = "from B"
    target.merge(source)

    merged = _reloaded(target)

    assert [(f.name, f.value) for f in merged.form] == [
        ("name", "from A"),
        ("name_2", "from B"),
    ]


# ---------------------------------------------------------------------------
# Degenerate shapes
# ---------------------------------------------------------------------------


def test_merging_several_documents_takes_them_in_order():
    target = _doc("A0")
    target.merge(_doc("B0"), _doc("C0"))

    assert _text(_reloaded(target)) == ["A0", "B0", "C0"]


def test_the_same_source_can_be_merged_twice():
    source = _reloaded(_doc("SRC"))
    target = _doc("A0")
    target.merge(source)
    target.merge(source)

    assert _text(_reloaded(target)) == ["A0", "SRC", "SRC"]


def test_merging_an_empty_document_adds_nothing():
    target = _doc("A0")
    empty = Document()
    empty.pages.clear()
    target.merge(empty)

    assert _text(_reloaded(target)) == ["A0"]


def test_merging_into_an_empty_document():
    target = Document()
    target.pages.clear()
    target.merge(_doc("B0"))

    assert _text(_reloaded(target)) == ["B0"]


def test_a_page_from_an_encrypted_document_merges_as_plaintext():
    source = _doc("ENC")
    source.pages[0].add_image(PNG, 100, 500, width=80, height=80)
    source.encrypt("pw")
    locked = Document(io.BytesIO(_saved(source)), password="pw")

    target = _doc("A0")
    target.merge(locked)

    assert _ink(_reloaded(target).pages[1], IMAGE_BOX) > 0


def test_merging_only_a_document_is_accepted():
    with pytest.raises(TypeError, match="Document"):
        _doc("A0").merge("not a document")

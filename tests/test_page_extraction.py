"""Taking part of a document, and joining whole ones, through the file editor.

``PdfFileEditor.concatenate`` and ``.extract`` -- and the low-code merger and
splitter behind them -- did not go through the merge that ``Document.merge``
uses. They went through a second, older implementation that built a fresh model
from page rectangles and content bytes, pooled the sources' images into one
renamed namespace, and rewrote the content streams to match. A concatenated page
therefore had no ``/Resources`` of its own, every page in the result carried
every image in it, and annotations were dropped outright.

Both are the same operation as appending a document's pages, and are now the
same code. Extraction adds one thing to it: a subset of pages is a different
document, so what belongs to the pages comes -- their annotations, the fields
their widgets belong to -- while the document's embedded files do not, and a
bookmark comes only if the page it points at did.
"""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.cos import PdfName
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.facades import PdfFileEditor
from aspose_pdf.outlines import OutlineItem

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
    "0000000c49444154789c63f8cfc0000003010100c9fe92ef0000000049454e44ae426082"
)


def _saved(document: Document) -> bytes:
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _source() -> Document:
    document = Document()
    for index in range(4):
        document.pages.add().add_text(f"P{index}", 72, 700, font_size=36)
    document.pages[2].add_image(PNG, 100, 500, width=80, height=80)
    document.pages[2].annotations.add("Text", (10, 10, 40, 40), "note on 2")
    document.outlines.add(OutlineItem("to 0", 0))
    document.outlines.add(OutlineItem("to 2", 2))
    document.outlines.add(OutlineItem("to 3", 3))
    document.add_attachment("a.txt", b"x")
    return document


def _text(document: Document) -> list[str]:
    return [
        document.pages[index].to_markdown().strip().split("\n")[0]
        for index in range(len(document.pages))
    ]


def _xobjects(document: Document, index: int) -> list[str]:
    engine = document._engine_pdf
    engine._ensure_page_cache()
    page = engine._cos_doc.objects.get(engine._page_refs[index])
    found = engine._get_inherited_attr(page, "Resources")
    xobjects = engine._resolve(found.mapping.get(PdfName("XObject"))) if found else None
    return sorted(key.name for key in xobjects.mapping) if xobjects else []


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def test_an_extracted_page_keeps_what_it_draws_with():
    engine = SimplePdf.from_bytes(_saved(_source()))

    taken = Document(io.BytesIO(engine.extract_pages([2, 3]).to_bytes()))

    assert _text(taken) == ["P2", "P3"]
    assert _xobjects(taken, 0) == ["/Im1"]
    assert _xobjects(taken, 1) == []


def test_an_extracted_page_keeps_its_annotations():
    engine = SimplePdf.from_bytes(_saved(_source()))

    taken = Document(io.BytesIO(engine.extract_pages([2, 3]).to_bytes()))

    assert [a.contents for a in taken.pages[0].annotations] == ["note on 2"]
    assert list(taken.pages[1].annotations) == []


def test_only_the_bookmarks_whose_page_came_come_with_it():
    """Pointing one at whatever now sits at its old index would send the
    reader somewhere the original never named."""
    engine = SimplePdf.from_bytes(_saved(_source()))

    taken = Document(io.BytesIO(engine.extract_pages([2, 3]).to_bytes()))

    assert [(o.title, o.page_index) for o in taken.outlines] == [
        ("to 2", 0),
        ("to 3", 1),
    ]


def test_bookmarks_follow_pages_that_were_reordered():
    engine = SimplePdf.from_bytes(_saved(_source()))

    taken = Document(io.BytesIO(engine.extract_pages([3, 0]).to_bytes()))

    assert _text(taken) == ["P3", "P0"]
    assert sorted((o.title, o.page_index) for o in taken.outlines) == [
        ("to 0", 1),
        ("to 3", 0),
    ]


def test_a_typed_destination_is_remapped_before_the_document_is_even_saved():
    """`outlines` reads the model, so the page a destination names has to be
    the page it landed on, not the one it left."""
    from aspose_pdf.interactive import XYZDestination

    document = Document()
    for index in range(4):
        document.pages.add().add_text(f"P{index}", 72, 700, font_size=36)
    document.outlines.add(
        OutlineItem("zoomed", destination=XYZDestination(3, 0.0, 700.0, 2.0))
    )
    engine = SimplePdf.from_bytes(_saved(document))

    taken = Document()
    taken._engine_pdf = engine.extract_pages([3])

    assert [(o.title, o.destination) for o in taken.outlines] == [
        ("zoomed", XYZDestination(page=0, left=0.0, top=700.0, zoom=2.0))
    ]


def test_a_merged_image_is_found_on_the_page_it_arrived_on():
    """The by-name image views are document-wide; the page they name has to
    move with the page."""
    from aspose_pdf.images import ImagePlacementAbsorber

    target = Document()
    target.pages.add().add_text("A0", 72, 700)
    source = Document()
    source.pages.add().add_text("B0", 72, 700)
    source.pages[0].add_image(PNG, 100, 500, width=80, height=80)
    target.merge(source)

    absorber = ImagePlacementAbsorber()
    absorber.visit(target.pages[1])

    assert len(absorber.image_placements) == 1


def test_the_document_s_embedded_files_are_not_part_of_a_page_subset():
    engine = SimplePdf.from_bytes(_saved(_source()))

    taken = Document(io.BytesIO(engine.extract_pages([2]).to_bytes()))

    assert sorted(taken.attachments) == []


def test_an_extracted_widget_arrives_with_its_field():
    document = Document()
    document.pages.add()
    document.pages.add()
    document.form.add_text_field("on the second", 1, (10, 10, 200, 30))
    engine = SimplePdf.from_bytes(_saved(document))

    taken = Document(io.BytesIO(engine.extract_pages([1]).to_bytes()))

    assert [field.name for field in taken.form] == ["on the second"]


def test_an_index_out_of_range_is_refused():
    engine = SimplePdf.from_bytes(_saved(_source()))

    with pytest.raises(IndexError):
        engine.extract_pages([9])


def test_an_empty_selection_is_refused():
    engine = SimplePdf.from_bytes(_saved(_source()))

    with pytest.raises(Exception, match="empty"):
        engine.extract_pages([])


# ---------------------------------------------------------------------------
# The file editor
# ---------------------------------------------------------------------------


def _files(tmp_path):
    first = Document()
    first.pages.add().add_text("A0", 72, 700, font_size=36)
    second = Document()
    second.pages.add().add_text("B0", 72, 700, font_size=36)
    second.pages[0].add_image(PNG, 100, 500, width=80, height=80)
    second.pages[0].annotations.add("Text", (10, 10, 40, 40), "note B")
    paths = []
    for name, document in (("a.pdf", first), ("b.pdf", second)):
        path = tmp_path / name
        path.write_bytes(_saved(document))
        paths.append(str(path))
    return paths


def test_concatenate_gives_each_page_the_resources_it_names(tmp_path):
    first, second = _files(tmp_path)
    output = str(tmp_path / "out.pdf")

    assert PdfFileEditor().concatenate([first, second], output) is True

    joined = Document(output)
    assert _text(joined) == ["A0", "B0"]
    assert _xobjects(joined, 0) == []
    assert _xobjects(joined, 1) == ["/Im1"]


def test_concatenate_keeps_the_annotations_of_the_pages_it_joins(tmp_path):
    first, second = _files(tmp_path)
    output = str(tmp_path / "out.pdf")
    PdfFileEditor().concatenate([first, second], output)

    joined = Document(output)

    assert list(joined.pages[0].annotations) == []
    assert [a.contents for a in joined.pages[1].annotations] == ["note B"]


def test_extract_through_the_editor_keeps_the_page_whole(tmp_path):
    _first, second = _files(tmp_path)
    output = str(tmp_path / "out.pdf")

    assert PdfFileEditor().extract(second, output, 1, 1) is True

    taken = Document(output)
    assert _xobjects(taken, 0) == ["/Im1"]
    assert [a.contents for a in taken.pages[0].annotations] == ["note B"]


def test_merging_only_accepts_documents():
    with pytest.raises(TypeError, match="SimplePdf"):
        SimplePdf.merge(SimplePdf(), "not a document")

"""A subset of pages adopts what its pages reach, and nothing else.

Taking part of a document is not the same as taking all of it. The rule for a
subset is already written down -- what belongs to the pages comes, the
document's own belongings do not -- but two things came regardless of whether
the pages reached them:

* every field of the source ``/AcroForm``, so a page split off on its own
  carried the *other* pages' fields: controls listed in the form that no page
  in the document draws, and nothing can fill;
* every optional content group, so the same split carried layers nothing in it
  names -- entries in a viewer's layers panel that switch nothing on or off.

A **whole** document is the other way round: every page comes, so everything is
reached, and a layer an author created and left empty, or a field whose widgets
were never placed, is theirs to keep. The guard is for subsets only, as the
attachment rule already was.
"""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.cos import PdfArray, PdfDictionary, PdfName
from aspose_pdf.engine.simple_pdf import SimplePdf


def _source(pages: int = 2) -> Document:
    document = Document()
    for index in range(pages):
        label = f"p{index}"
        document.pages.add()
        with document.pages[index].layer(document.layers.add(f"layer {label}")):
            document.pages[index].add_text(f"{label} text", x=40, y=700)
        document.form.add_text_field(
            f"{label}_field", index, (40, 500, 300, 530), value=label
        )
    return document


def _bytes(document: Document) -> bytes:
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _subset(document: Document, pages: list[int]) -> Document:
    engine = SimplePdf.from_bytes(_bytes(document))
    taken = Document()
    taken._engine_pdf = engine.extract_pages(pages)
    return Document(io.BytesIO(_bytes(taken)))


def _layers(document: Document) -> list[str]:
    return sorted(layer.name for layer in document.layers)


def _fields(document: Document) -> list[str]:
    return sorted(field.name for field in document.form)


def _orphan_fields(document: Document) -> list[str]:
    """Fields no page in the document draws a widget for."""
    engine = document._engine_pdf
    on_a_page: set[int] = set()
    for index in range(len(document.pages)):
        annots = engine._resolve(
            engine._get_page_dict(index).mapping.get(PdfName("Annots"))
        )
        if isinstance(annots, PdfArray):
            on_a_page.update(
                ref.object_number
                for ref in annots.items
                if hasattr(ref, "object_number")
            )
    root = engine._resolve(engine._cos_doc.trailer.mapping[PdfName("Root")])
    acro = engine._resolve(root.mapping.get(PdfName("AcroForm")))
    if not isinstance(acro, PdfDictionary):
        return []
    orphans = []
    for ref in engine._resolve(acro.mapping[PdfName("Fields")]).items:
        field = engine._resolve(ref)
        kids = engine._resolve(field.mapping.get(PdfName("Kids")))
        widgets = kids.items if isinstance(kids, PdfArray) else [ref]
        if not any(
            getattr(widget, "object_number", None) in on_a_page for widget in widgets
        ):
            title = engine._resolve(field.mapping.get(PdfName("T")))
            orphans.append(title.value.decode("latin-1").strip("\x00"))
    return sorted(orphans)


# --- a subset takes what it reaches -----------------------------------------


def test_the_source_has_a_layer_and_a_field_per_page():
    document = Document(io.BytesIO(_bytes(_source())))
    assert _layers(document) == ["layer p0", "layer p1"]
    assert _fields(document) == ["p0_field", "p1_field"]
    assert _orphan_fields(document) == []


@pytest.mark.parametrize("index", [0, 1])
def test_one_page_takes_its_own_field_and_no_other(index):
    taken = _subset(_source(), [index])
    assert _fields(taken) == [f"p{index}_field"]
    assert _orphan_fields(taken) == []


@pytest.mark.parametrize("index", [0, 1])
def test_one_page_takes_its_own_layer_and_no_other(index):
    assert _layers(_subset(_source(), [index])) == [f"layer p{index}"]


def test_two_of_three_pages_take_two_of_three(tmp_path):
    taken = _subset(_source(pages=3), [0, 2])
    assert _fields(taken) == ["p0_field", "p2_field"]
    assert _layers(taken) == ["layer p0", "layer p2"]


def test_a_widget_without_a_parent_still_finds_its_field():
    # A widget normally points back at its field through /Parent, which is how
    # the field arrives with the page. A file that omits it -- ours never does,
    # another writer's might -- leaves the field reachable only downwards,
    # through its own /Kids.
    document = _source(pages=2)
    engine = document._engine_pdf
    for index in range(2):
        annots = engine._resolve(
            engine._get_page_dict(index).mapping[PdfName("Annots")]
        )
        for ref in annots.items:
            engine._resolve(ref).mapping.pop(PdfName("Parent"), None)
    taken = _subset(document, [0])
    assert _fields(taken) == ["p0_field"]


def test_the_split_plugin_gives_each_page_its_own(tmp_path):
    from aspose_pdf.lowcode import ByteArrayDataSource, SplitOptions, Splitter

    options = SplitOptions()
    options.add_input(ByteArrayDataSource(_bytes(_source())))
    parts = [
        Document(io.BytesIO(part.to_array())) for part in Splitter().process(options)
    ]
    assert [_fields(part) for part in parts] == [["p0_field"], ["p1_field"]]
    assert [_layers(part) for part in parts] == [["layer p0"], ["layer p1"]]
    assert [_orphan_fields(part) for part in parts] == [[], []]


def test_the_file_editor_extract_agrees(tmp_path):
    from aspose_pdf.facades import PdfFileEditor

    source, output = tmp_path / "src.pdf", tmp_path / "out.pdf"
    _source().save(str(source))
    assert PdfFileEditor().extract(str(source), str(output), 1, 1)
    taken = Document(str(output))
    assert _fields(taken) == ["p0_field"]
    assert _layers(taken) == ["layer p0"]


# --- a whole document keeps everything it had -------------------------------


def test_a_whole_merge_keeps_a_layer_nothing_is_on():
    other = Document()
    other.pages.add()
    other.layers.add("empty layer")
    target = _source(pages=1)
    target.merge(other)
    assert "empty layer" in _layers(Document(io.BytesIO(_bytes(target))))


def test_a_whole_merge_keeps_a_field_with_no_widget_placed():
    other = Document()
    other.pages.add()
    other.form.add_text_field("placed", 0, (40, 500, 300, 530), value="x")
    engine = other._engine_pdf
    root = engine._resolve(engine._cos_doc.trailer.mapping[PdfName("Root")])
    acro = engine._resolve(root.mapping[PdfName("AcroForm")])
    fields = engine._resolve(acro.mapping[PdfName("Fields")])
    # A field with no widgets at all: legal, and the author's to keep.
    bare = engine._cos_doc.register_object(
        PdfDictionary(
            {
                PdfName("FT"): PdfName("Tx"),
                PdfName("T"): engine._resolve(
                    engine._resolve(fields.items[0]).mapping[PdfName("T")]
                ),
            }
        )
    )
    engine._resolve(bare).mapping[PdfName("T")] = _text("widgetless")
    fields.items.append(bare)

    target = _source(pages=1)
    target.merge(other)
    assert "widgetless" in _fields(Document(io.BytesIO(_bytes(target))))


def _text(value: str):
    from aspose_pdf.engine.cos import PdfString

    return PdfString(value.encode("latin-1"))


def test_a_whole_merge_still_brings_every_layer():
    target = _source(pages=1)
    target.merge(_source(pages=2))
    assert _layers(Document(io.BytesIO(_bytes(target)))).count("layer p0") == 2

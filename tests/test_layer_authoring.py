"""Creating layers, putting content on them, and resolving them for good.

Optional content used to be read-only: the layers a document declared could be
listed and switched, but not created, not written to, and never resolved. The
last of those is the one that matters when the file leaves your hands --
switching a layer off changes what is *drawn*, while the content sits in the
PDF waiting for somebody to switch it back on. A hidden draft watermark is
still a draft watermark.

These tests cover the three pieces: ``layers.add`` creates a group,
``page.layer`` marks content as belonging to one, and
``Document.flatten_layers`` deletes what is hidden and removes the optional
content structure -- leaving a file that shows what was visible and *contains*
nothing else.
"""

from __future__ import annotations

import zlib
from pathlib import Path

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfName,
    PdfNumber,
    PdfStream,
)
from aspose_pdf.exceptions import AsposePdfException, PdfValidationException


def _layered_document() -> Document:
    """A page with body text, a hidden Draft layer and a visible Notes layer."""
    document = Document()
    page = document.pages.add()
    page.add_text("Body text everyone sees", 60, 700, font_size=14)
    draft = document.layers.add("Draft", visible=False)
    with page.layer(draft):
        page.add_text("CONFIDENTIAL DRAFT", 100, 400, font_size=40)
    notes = document.layers.add("Notes")
    with page.layer(notes):
        page.add_text("Reviewer note", 60, 300, font_size=10)
    return document


def _ink(raster) -> int:
    pixels = raster.pixels
    return sum(1 for i in range(0, len(pixels), 3) if pixels[i] < 250)


# ---------------------------------------------------------------------------
# Creating a layer
# ---------------------------------------------------------------------------


def test_a_document_with_no_optional_content_gains_the_whole_structure():
    document = Document()
    document.pages.add()

    layer = document.layers.add("Watermark")

    assert layer.name == "Watermark"
    assert layer.visible is True
    assert document.layers.names() == ["Watermark"]


def test_a_layer_can_start_hidden():
    document = Document()
    document.pages.add()

    layer = document.layers.add("Draft", visible=False)

    assert layer.visible is False
    assert document.layers["Draft"].visible is False


def test_layers_are_listed_in_the_order_they_were_added():
    document = Document()
    document.pages.add()

    document.layers.add("First")
    document.layers.add("Second")
    document.layers.add("Third")

    assert document.layers.names() == ["First", "Second", "Third"]


def test_the_default_configuration_lists_the_group_for_a_viewer_panel():
    """``/Order`` is what a viewer's layers panel shows; without it the group
    exists but nobody can find it."""
    document = Document()
    document.pages.add()
    document.layers.add("Panel entry")

    engine = document._engine_pdf
    catalog = engine._resolve(engine._cos_doc.trailer.mapping[PdfName("Root")])
    properties = engine._resolve(catalog.mapping[PdfName("OCProperties")])
    config = engine._resolve(properties.mapping[PdfName("D")])

    assert len(engine._resolve(properties.mapping[PdfName("OCGs")]).items) == 1
    assert len(engine._resolve(config.mapping[PdfName("Order")]).items) == 1


def test_a_layer_needs_a_name():
    document = Document()
    document.pages.add()

    with pytest.raises(PdfValidationException, match="non-empty name"):
        document.layers.add("")


def test_a_non_ascii_layer_name_survives_a_round_trip(tmp_path: Path):
    document = Document()
    document.pages.add()
    document.layers.add("Черновик")
    target = tmp_path / "layers.pdf"
    document.save(target)

    with Document(target) as reopened:
        assert reopened.layers.names() == ["Черновик"]


# ---------------------------------------------------------------------------
# Putting content on a layer
# ---------------------------------------------------------------------------


def test_content_authored_in_a_block_is_marked_as_the_layers():
    document = _layered_document()

    content = document.pages[0].content

    assert b"/OC /oc1 BDC" in content
    assert b"EMC" in content
    assert content.index(b"CONFIDENTIAL") > content.index(b"/OC /oc1 BDC")


def test_the_group_is_registered_in_the_pages_properties():
    document = _layered_document()
    engine = document._engine_pdf
    page = engine._get_page_dict(0)
    resources = engine._resolve(page.mapping[PdfName("Resources")])
    properties = engine._resolve(resources.mapping[PdfName("Properties")])

    names = {key.name.lstrip("/") for key in properties.mapping}
    assert names == {"oc1", "oc2"}


def test_a_hidden_layers_content_is_not_drawn_and_a_visible_ones_is():
    document = _layered_document()

    hidden = _ink(document.pages[0].render(antialias=False))
    document.layers["Draft"].visible = True
    shown = _ink(document.pages[0].render(antialias=False))

    assert shown > hidden > 0


def test_re_entering_a_layer_reuses_its_resource_name():
    document = Document()
    page = document.pages.add()
    layer = document.layers.add("Repeat")
    with page.layer(layer):
        page.add_text("first", 60, 700, font_size=10)
    with page.layer(layer):
        page.add_text("second", 60, 680, font_size=10)

    content = page.content

    assert content.count(b"/OC /oc1 BDC") == 2
    engine = document._engine_pdf
    resources = engine._resolve(
        engine._get_page_dict(0).mapping[PdfName("Resources")]
    )
    properties = engine._resolve(resources.mapping[PdfName("Properties")])
    assert len(properties.mapping) == 1


def test_layer_blocks_nest():
    document = Document()
    page = document.pages.add()
    outer = document.layers.add("Outer")
    inner = document.layers.add("Inner", visible=False)
    with page.layer(outer):
        page.add_text("outer text", 60, 700, font_size=10)
        with page.layer(inner):
            page.add_text("inner text", 60, 680, font_size=10)

    content = page.content

    assert content.count(b"BDC") == 2
    assert content.count(b"EMC") == 2
    # The inner section closes before the outer one.
    assert content.index(b"inner text") < content.index(b"EMC")


def test_the_block_needs_a_real_layer():
    document = Document()
    page = document.pages.add()

    with pytest.raises(PdfValidationException, match="must be a Layer"):
        with page.layer("Draft"):
            pass


# ---------------------------------------------------------------------------
# Removing a layer
# ---------------------------------------------------------------------------


def test_removing_a_layer_leaves_its_content_unconditionally_visible():
    """Removing the group is not removing the content -- that is what
    :meth:`flatten_layers` is for."""
    document = _layered_document()
    hidden_before = _ink(document.pages[0].render(antialias=False))

    assert document.layers.remove("Draft") is True

    assert document.layers.names() == ["Notes"]
    assert b"CONFIDENTIAL" in document.pages[0].content
    assert _ink(document.pages[0].render(antialias=False)) > hidden_before


def test_removing_an_unknown_layer_raises():
    document = _layered_document()

    with pytest.raises(KeyError):
        document.layers.remove("Nope")


# ---------------------------------------------------------------------------
# Flattening
# ---------------------------------------------------------------------------


def test_flattening_deletes_hidden_content_from_the_saved_file(tmp_path: Path):
    """The point of the whole exercise: hidden means gone, not unreachable."""
    document = _layered_document()
    before = tmp_path / "layered.pdf"
    document.save(before)
    assert b"CONFIDENTIAL" in before.read_bytes()

    assert document.flatten_layers() == 1

    after = tmp_path / "flat.pdf"
    document.save(after)
    data = after.read_bytes()
    assert b"CONFIDENTIAL" not in data
    assert b"Reviewer note" in data
    assert b"Body text everyone sees" in data


def test_flattening_removes_the_optional_content_structure(tmp_path: Path):
    document = _layered_document()
    document.flatten_layers()
    target = tmp_path / "flat.pdf"
    document.save(target)

    data = target.read_bytes()
    assert b"/OCProperties" not in data
    assert b"BDC" not in data  # the wrappers go too; this is an ordinary page now
    with Document(target) as reopened:
        assert reopened.layers.names() == []


def test_flattening_does_not_change_what_the_page_looks_like():
    before = _layered_document().pages[0].render(dpi=72, antialias=False)
    document = _layered_document()
    document.flatten_layers()
    after = document.pages[0].render(dpi=72, antialias=False)

    assert after.pixels == before.pixels


def test_a_document_without_layers_is_left_alone():
    document = Document()
    document.pages.add().add_text("Plain", 60, 700, font_size=12)
    content = document.pages[0].content

    assert document.flatten_layers() == 0
    assert document.pages[0].content == content


def test_flattening_reports_the_pages_it_changed():
    document = Document()
    first = document.pages.add()
    document.pages.add().add_text("untouched", 60, 700, font_size=10)
    draft = document.layers.add("Draft", visible=False)
    with first.layer(draft):
        first.add_text("hidden", 60, 700, font_size=10)

    assert document.flatten_layers() == 1


def test_an_image_hidden_by_its_own_oc_entry_is_dropped(tmp_path: Path):
    """An XObject can carry ``/OC`` itself, without any marked content."""
    document = Document()
    document.pages.add()
    layer = document.layers.add("Pictures", visible=False)
    engine = document._engine_pdf
    samples = bytes((x * 8) % 256 for _ in range(4) for x in range(4) for _ in range(3))
    raw = zlib.compress(samples)
    image = engine._cos_doc.register_object(
        PdfStream(
            raw,
            {
                PdfName("Type"): PdfName("XObject"),
                PdfName("Subtype"): PdfName("Image"),
                PdfName("Width"): PdfNumber(4),
                PdfName("Height"): PdfNumber(4),
                PdfName("ColorSpace"): PdfName("DeviceRGB"),
                PdfName("BitsPerComponent"): PdfNumber(8),
                PdfName("Filter"): PdfName("FlateDecode"),
                PdfName("Length"): PdfNumber(len(raw)),
                PdfName("OC"): engine._cos_doc.objects and _reference(engine, layer),
            },
        )
    )
    xobjects = engine._ensure_resource_subdict(0, "XObject")
    xobjects.mapping[PdfName("Im0")] = image
    engine._append_content_to_page(0, b"q 100 0 0 100 60 600 cm /Im0 Do Q")

    assert document.flatten_layers() == 1
    assert b"/Im0 Do" not in document.pages[0].content


def _reference(engine, layer):
    from aspose_pdf.engine.cos import PdfIndirectReference

    return PdfIndirectReference(layer.object_number, 0)


def test_an_annotation_on_a_hidden_layer_is_dropped_and_others_keep_working():
    document = Document()
    page = document.pages.add()
    page.annotations.add("Square", (60, 600, 160, 660), "visible one")
    page.annotations.add("Square", (60, 500, 160, 560), "hidden one")
    layer = document.layers.add("Markup", visible=False)
    engine = document._engine_pdf
    annots = engine._resolve(
        engine._get_page_dict(0).mapping[PdfName("Annots")]
    )
    hidden = engine._resolve(annots.items[1])
    hidden.mapping[PdfName("OC")] = _reference(engine, layer)

    assert document.flatten_layers() >= 0
    remaining = engine._resolve(
        engine._get_page_dict(0).mapping[PdfName("Annots")]
    )
    assert isinstance(remaining, PdfArray)
    assert len(remaining.items) == 1
    survivor = engine._resolve(remaining.items[0])
    assert PdfName("OC") not in survivor.mapping


def test_flattening_a_disposed_document_raises():
    document = _layered_document()
    document.dispose()

    with pytest.raises(AsposePdfException):
        document.flatten_layers()


def test_a_loaded_document_flattens_too(tmp_path: Path):
    """The interesting path: the content comes back through the COS graph."""
    source = tmp_path / "layered.pdf"
    _layered_document().save(source)

    with Document(source) as document:
        assert document.layers.names() == ["Draft", "Notes"]
        assert document.flatten_layers() == 1
        target = tmp_path / "flat.pdf"
        document.save(target)

    data = target.read_bytes()
    assert b"CONFIDENTIAL" not in data
    assert b"Reviewer note" in data

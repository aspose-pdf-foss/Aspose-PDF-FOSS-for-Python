"""Typed action/destination API for links, outlines, and button widgets."""

from __future__ import annotations

import io

from aspose_pdf import (
    Document,
    GoToAction,
    GoToRAction,
    URIAction,
    XYZDestination,
)
from aspose_pdf.engine.cos import PdfArray, PdfDictionary, PdfName
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.outlines import OutlineItem


def _two_page_doc() -> Document:
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 612, 792), (0, 0, 612, 792)]
    pdf.page_contents = [b"", b""]
    pdf._ensure_cos()
    doc = Document()
    doc._engine_pdf = pdf
    return doc


def _page_link_annots(engine: SimplePdf, page_index: int) -> list[PdfDictionary]:
    page = engine._get_page_dict(page_index)
    annots = engine._resolve(page.mapping.get(PdfName("Annots")))
    result = []
    for ref in annots.items:
        annot = engine._resolve(ref)
        if engine._get_name(annot.mapping.get(PdfName("Subtype"))) == "Link":
            result.append(annot)
    return result


def test_link_with_uri_action():
    doc = _two_page_doc()
    doc.pages[0].add_link((10, 700, 200, 720), URIAction("https://example.com"))
    (link,) = _page_link_annots(doc._engine_pdf, 0)
    action = doc._engine_pdf._resolve(link.mapping.get(PdfName("A")))
    assert doc._engine_pdf._get_name(action.mapping.get(PdfName("S"))) == "URI"
    assert bytes(action.mapping.get(PdfName("URI")).value) == b"https://example.com"


def test_link_with_goto_xyz_destination():
    doc = _two_page_doc()
    doc.pages[0].add_link(
        (10, 650, 200, 670),
        GoToAction(XYZDestination(page=1, left=0, top=792, zoom=2.0)),
    )
    (link,) = _page_link_annots(doc._engine_pdf, 0)
    action = doc._engine_pdf._resolve(link.mapping.get(PdfName("A")))
    dest = doc._engine_pdf._resolve(action.mapping.get(PdfName("D")))
    assert isinstance(dest, PdfArray)
    assert doc._engine_pdf._get_name(dest.items[1]) == "XYZ"
    assert [n.value for n in dest.items[2:]] == [0.0, 792.0, 2.0]


def test_bare_destination_link():
    doc = _two_page_doc()
    doc.pages[0].add_link((10, 600, 200, 620), XYZDestination(page=1, top=400))
    (link,) = _page_link_annots(doc._engine_pdf, 0)
    dest = doc._engine_pdf._resolve(link.mapping.get(PdfName("Dest")))
    assert isinstance(dest, PdfArray)
    assert doc._engine_pdf._get_name(dest.items[1]) == "XYZ"


def test_outline_typed_destinations_round_trip():
    doc = _two_page_doc()
    doc.outlines.add(
        OutlineItem("Zoom p2", destination=XYZDestination(page=1, top=792, zoom=1.5))
    )
    doc.outlines.add(OutlineItem("Web", destination=URIAction("https://ex.com")))
    buffer = io.BytesIO()
    doc.save(buffer)

    reloaded = Document()
    reloaded.load_from(buffer.getvalue())
    engine = reloaded._engine_pdf
    root = engine._resolve(engine._cos_doc.trailer.get(PdfName("Root")))
    outlines = engine._resolve(root.mapping.get(PdfName("Outlines")))
    kinds = []
    node = engine._resolve(outlines.mapping.get(PdfName("First")))
    while node is not None:
        dest = engine._resolve(node.mapping.get(PdfName("Dest")))
        action = engine._resolve(node.mapping.get(PdfName("A")))
        if isinstance(dest, PdfArray):
            kinds.append(("Dest", engine._get_name(dest.items[1])))
        elif isinstance(action, PdfDictionary):
            kinds.append(("A", engine._get_name(action.mapping.get(PdfName("S")))))
        nxt = node.mapping.get(PdfName("Next"))
        node = engine._resolve(nxt) if nxt is not None else None
    assert ("Dest", "XYZ") in kinds
    assert ("A", "URI") in kinds


def test_push_button_action():
    doc = _two_page_doc()
    doc.form.add_push_button(
        "btn",
        doc.pages[0],
        (100, 100, 200, 130),
        caption="Open",
        action=URIAction("https://ex.com"),
    )
    engine = doc._engine_pdf
    page = engine._get_page_dict(0)
    annots = engine._resolve(page.mapping.get(PdfName("Annots")))
    widget = next(
        engine._resolve(ref)
        for ref in annots.items
        if engine._get_name(engine._resolve(ref).mapping.get(PdfName("Subtype")))
        == "Widget"
    )
    action = engine._resolve(widget.mapping.get(PdfName("A")))
    assert engine._get_name(action.mapping.get(PdfName("S"))) == "URI"


def test_goto_remote_uses_page_number_not_ref():
    doc = _two_page_doc()
    doc.pages[0].add_link(
        (10, 500, 100, 520),
        GoToRAction("other.pdf", XYZDestination(page=2, top=100)),
    )
    (link,) = _page_link_annots(doc._engine_pdf, 0)
    action = doc._engine_pdf._resolve(link.mapping.get(PdfName("A")))
    assert doc._engine_pdf._get_name(action.mapping.get(PdfName("S"))) == "GoToR"
    dest = doc._engine_pdf._resolve(action.mapping.get(PdfName("D")))
    # Remote destinations reference a page *number*, not an indirect page ref.
    assert not isinstance(dest.items[0], type(None))
    assert dest.items[0].value == 2  # PdfNumber, not a reference

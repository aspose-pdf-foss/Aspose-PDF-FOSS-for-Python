"""Tests for heuristic PDF/UA auto-tagging of existing page content."""

from aspose_pdf import Document
from aspose_pdf.engine.auto_tag import (
    build_tagged_content,
    choose_tags,
    find_text_objects,
    find_xobject_invocations,
    has_marked_content,
)
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfDictionary,
    PdfName,
    PdfNumber,
    PdfStream,
    PdfString,
)
from aspose_pdf.engine.simple_pdf import SimplePdf

_TWO_BLOCKS = (
    b"BT /F1 24 Tf 1 0 0 1 72 700 Tm (Heading) Tj ET\n"
    b"BT /F1 12 Tf 1 0 0 1 72 680 Tm (Body text, a bit longer) Tj ET"
)


# ---------------------------------------------------------------------------
# Scanner / builder unit tests
# ---------------------------------------------------------------------------


def test_find_text_objects_locates_blocks_and_sizes():
    objects = find_text_objects(_TWO_BLOCKS)
    assert len(objects) == 2
    assert objects[0].max_font_size == 24.0
    assert objects[1].max_font_size == 12.0
    # The reported spans really wrap the BT...ET operators.
    assert _TWO_BLOCKS[objects[0].start : objects[0].start + 2] == b"BT"
    assert _TWO_BLOCKS[objects[0].end - 2 : objects[0].end] == b"ET"


def test_scanner_ignores_operators_inside_strings():
    # "BT", "ET" and "BDC" appearing inside string literals are not operators.
    content = b"BT (a BT and ET and BDC inside) Tj ET"
    assert len(find_text_objects(content)) == 1
    assert not has_marked_content(content)


def test_has_marked_content_detects_real_bdc():
    assert has_marked_content(b"/P <</MCID 0>> BDC (x) Tj EMC")
    assert not has_marked_content(b"BT (x) Tj ET")


def test_choose_tags_marks_dominant_size_as_heading():
    assert choose_tags(find_text_objects(_TWO_BLOCKS)) == ["H1", "P"]


def test_build_tagged_content_splices_without_rewriting():
    objects = find_text_objects(_TWO_BLOCKS)
    marks = [
        (objects[0].start, objects[0].end, "H1", 0),
        (objects[1].start, objects[1].end, "P", 1),
    ]
    out = build_tagged_content(_TWO_BLOCKS, marks)
    assert out.count(b"BDC") == 2
    assert out.count(b"EMC") == 2
    assert b"/H1 <</MCID 0>> BDC" in out
    assert b"/P <</MCID 1>> BDC" in out
    # Original operators are preserved verbatim.
    assert b"(Heading) Tj" in out and b"(Body text, a bit longer) Tj" in out


# ---------------------------------------------------------------------------
# End-to-end engine / Document
# ---------------------------------------------------------------------------


def _untagged_doc():
    pdf = SimplePdf(pages=[(0, 0, 612, 792)], page_contents=[_TWO_BLOCKS])
    pdf._ensure_cos()
    doc = Document()
    doc._engine_pdf = pdf
    return doc, pdf


def _struct_root(pdf):
    root = pdf._resolve(pdf._cos_doc.trailer.get(PdfName("Root")))
    return pdf._resolve(root.mapping.get(PdfName("StructTreeRoot")))


def test_auto_tag_builds_structure_tree():
    doc, pdf = _untagged_doc()

    created = doc.auto_tag()

    assert created == 2
    struct_root = _struct_root(pdf)
    kids = pdf._resolve(struct_root.mapping.get(PdfName("K")))
    assert isinstance(kids, PdfArray) and len(kids.items) == 2
    tags = [
        pdf._resolve(k).mapping.get(PdfName("S")).name.lstrip("/")
        for k in kids.items
    ]
    assert tags == ["H1", "P"]

    # Each element points at its page and an MCID leaf.
    for gid, elem_ref in enumerate(kids.items):
        elem = pdf._resolve(elem_ref)
        assert isinstance(elem.mapping.get(PdfName("K")), PdfNumber)
        assert int(elem.mapping[PdfName("K")].value) == gid


def test_auto_tag_marks_content_and_parent_tree():
    doc, pdf = _untagged_doc()
    doc.auto_tag()

    content = pdf.get_page_content(0)
    assert content.count(b"BDC") == 2 and content.count(b"EMC") == 2
    assert b"/H1 <</MCID 0>>" in content and b"/P <</MCID 1>>" in content

    page = pdf._get_page_dict(0)
    assert isinstance(pdf._resolve(page.mapping.get(PdfName("StructParents"))), PdfNumber)

    struct_root = _struct_root(pdf)
    parent_tree = pdf._resolve(struct_root.mapping.get(PdfName("ParentTree")))
    nums = pdf._resolve(parent_tree.mapping.get(PdfName("Nums")))
    page_array = pdf._resolve(nums.items[1])
    assert len(page_array.items) == 2  # one entry per MCID


def test_auto_tag_is_idempotent():
    doc, _pdf = _untagged_doc()
    assert doc.auto_tag() == 2
    # The page now carries marked content, so a second pass is a no-op.
    assert doc.auto_tag() == 0


def test_convert_to_pdfua_auto_tag_creates_real_tree():
    doc, pdf = _untagged_doc()

    doc.convert_to_pdfua(auto_tag=True, title="Doc")

    struct_root = _struct_root(pdf)
    kids = pdf._resolve(struct_root.mapping.get(PdfName("K")))
    assert len(kids.items) == 2
    # The catalog shell is in place too.
    root = pdf._resolve(pdf._cos_doc.trailer.get(PdfName("Root")))
    mark_info = pdf._resolve(root.mapping.get(PdfName("MarkInfo")))
    assert mark_info.mapping.get(PdfName("Marked")).value is True


def test_auto_tag_survives_save_roundtrip():
    doc, pdf = _untagged_doc()
    doc.auto_tag()

    reloaded = SimplePdf.from_bytes(pdf.to_bytes())
    try:
        root = reloaded._resolve(reloaded._cos_doc.trailer.get(PdfName("Root")))
        struct_root = reloaded._resolve(root.mapping.get(PdfName("StructTreeRoot")))
        kids = reloaded._resolve(struct_root.mapping.get(PdfName("K")))
        assert len(kids.items) == 2
        assert b"BDC" in reloaded.get_page_content(0)
    finally:
        reloaded.dispose()


# ---------------------------------------------------------------------------
# Image figures
# ---------------------------------------------------------------------------

_TEXT_AND_IMAGE = (
    b"BT /F1 12 Tf 1 0 0 1 72 700 Tm (Caption) Tj ET\n"
    b"q 100 0 0 80 72 600 cm /Im0 Do Q"
)


def test_find_xobject_invocations():
    found = find_xobject_invocations(b"q /Im0 Do Q /F1 12 Tf BT (x) Tj ET")
    assert len(found) == 1
    name, start, end = found[0]
    assert name == "/Im0"
    assert b"/Im0 Do".startswith(b"/Im0")
    # The span covers '/Im0 Do'.
    content = b"q /Im0 Do Q /F1 12 Tf BT (x) Tj ET"
    assert content[start:end] == b"/Im0 Do"


def test_scanner_skips_inline_image_data():
    # Inline image bytes between ID and EI must not be tokenized: here they
    # contain '(', 'BT' and 'Do' lookalikes that would otherwise derail parsing.
    content = (
        b"BT (label) Tj ET\n"
        b"BI /W 2 /H 2 /BPC 8 /CS /G ID \x28BT Do\xff\x00 garbage EI\n"
        b"q /Im0 Do Q"
    )
    assert len(find_text_objects(content)) == 1
    invocations = find_xobject_invocations(content)
    assert [name for name, _s, _e in invocations] == ["/Im0"]


def _doc_with_text_and_image(content=_TEXT_AND_IMAGE):
    pdf = SimplePdf(pages=[(0, 0, 612, 792)], page_contents=[content])
    pdf._ensure_cos()
    cos = pdf._cos_doc
    image = cos.register_object(
        PdfStream(
            b"\xff\x00\x00",
            {
                PdfName("Type"): PdfName("XObject"),
                PdfName("Subtype"): PdfName("Image"),
                PdfName("Width"): PdfNumber(1),
                PdfName("Height"): PdfNumber(1),
                PdfName("ColorSpace"): PdfName("DeviceRGB"),
                PdfName("BitsPerComponent"): PdfNumber(8),
            },
        )
    )
    pdf._get_page_dict(0).mapping[PdfName("Resources")] = PdfDictionary(
        {PdfName("XObject"): PdfDictionary({PdfName("Im0"): image})}
    )
    doc = Document()
    doc._engine_pdf = pdf
    return doc, pdf


def test_image_xobject_names_detects_image():
    _doc, pdf = _doc_with_text_and_image()
    assert pdf._image_xobject_names(0) == {"Im0"}


def test_auto_tag_wraps_image_as_figure():
    doc, pdf = _doc_with_text_and_image()

    created = doc.auto_tag()

    assert created == 2
    struct_root = _struct_root(pdf)
    kids = pdf._resolve(struct_root.mapping.get(PdfName("K")))
    elems = [pdf._resolve(k) for k in kids.items]
    tags = [e.mapping.get(PdfName("S")).name.lstrip("/") for e in elems]
    # Reading (stream) order: the caption precedes the image.
    assert tags == ["P", "Figure"]

    figure = elems[1]
    alt = figure.mapping.get(PdfName("Alt"))
    assert isinstance(alt, PdfString) and alt.value in ("Image", b"Image")

    content = pdf.get_page_content(0)
    assert b"/Figure <</MCID 1>> BDC" in content
    # The image paint stays inside the figure's marked content.
    figure_start = content.index(b"/Figure <</MCID 1>> BDC")
    assert content.index(b"/Im0 Do") > figure_start


def test_auto_tag_image_alt_none_skips_images():
    doc, pdf = _doc_with_text_and_image()

    assert doc.auto_tag(image_alt=None) == 1  # text only
    tags = [
        pdf._resolve(k).mapping.get(PdfName("S")).name.lstrip("/")
        for k in pdf._resolve(_struct_root(pdf).mapping.get(PdfName("K"))).items
    ]
    assert tags == ["P"]


def test_auto_tag_image_alt_callable():
    doc, pdf = _doc_with_text_and_image()

    doc.auto_tag(image_alt=lambda name: f"Figure: {name}")

    kids = pdf._resolve(_struct_root(pdf).mapping.get(PdfName("K")))
    figure = pdf._resolve(kids.items[1])
    alt = figure.mapping.get(PdfName("Alt")).value
    text = alt.decode() if isinstance(alt, bytes) else alt
    assert text == "Figure: Im0"

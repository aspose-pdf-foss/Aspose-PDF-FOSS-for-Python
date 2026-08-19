"""The page rasterizer composites annotation appearances.

It used to draw page content only, so every highlight, stamp, sticky note and
form widget was missing from a rendered page even when its `/AP` was present.
Placement follows ISO 32000-1 12.5.5: the appearance's `/Matrix`-transformed
`/BBox` is fitted to the annotation's `/Rect`.
"""

from __future__ import annotations

from io import BytesIO

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.cos import PdfArray, PdfDictionary, PdfName, PdfNumber, PdfStream

_PAGE = (0, 0, 200, 200)
_RED_FILL = b"q 1 0 0 rg 0 0 20 20 re f Q\n"


def _document(annots: list[PdfDictionary], content: bytes = b"") -> Document:
    from aspose_pdf.engine.simple_pdf import SimplePdf

    pdf = SimplePdf()
    pdf.pages = [_PAGE]
    pdf.page_contents = [content]
    pdf._ensure_cos()
    cos = pdf._cos_doc
    page = pdf._get_page_dict(0)
    page.mapping[PdfName("Annots")] = PdfArray(
        [cos.register_object(a) for a in annots]
    )
    document = Document()
    document._engine_pdf = pdf
    return document


def _appearance(cos, content: bytes = _RED_FILL, bbox=(0, 0, 20, 20), matrix=None):
    mapping = {
        PdfName("Type"): PdfName("XObject"),
        PdfName("Subtype"): PdfName("Form"),
        PdfName("BBox"): PdfArray([PdfNumber(v) for v in bbox]),
        PdfName("Length"): PdfNumber(len(content)),
    }
    if matrix is not None:
        mapping[PdfName("Matrix")] = PdfArray([PdfNumber(v) for v in matrix])
    return cos.register_object(PdfStream(content, mapping))


def _annot(cos, rect, *, subtype="Square", flags=None, matrix=None, bbox=(0, 0, 20, 20)):
    mapping = {
        PdfName("Type"): PdfName("Annot"),
        PdfName("Subtype"): PdfName(subtype),
        PdfName("Rect"): PdfArray([PdfNumber(v) for v in rect]),
        PdfName("AP"): PdfDictionary(
            {PdfName("N"): _appearance(cos, bbox=bbox, matrix=matrix)}
        ),
    }
    if flags is not None:
        mapping[PdfName("F")] = PdfNumber(flags)
    return PdfDictionary(mapping)


def _render(document: Document, **kwargs):
    return document.pages[0].render(dpi=72, antialias=False, **kwargs)


def _build(rect=(10, 160, 50, 200), **kwargs):
    from aspose_pdf.engine.simple_pdf import SimplePdf

    pdf = SimplePdf()
    pdf.pages = [_PAGE]
    pdf.page_contents = [b""]
    pdf._ensure_cos()
    cos = pdf._cos_doc
    annot = _annot(cos, rect, **kwargs)
    page = pdf._get_page_dict(0)
    page.mapping[PdfName("Annots")] = PdfArray([cos.register_object(annot)])
    document = Document()
    document._engine_pdf = pdf
    return document


def _is_red(raster, x, y) -> bool:
    r, g, b = raster.get_pixel(x, y)
    return r > 200 and g < 80 and b < 80


# --- the appearance is drawn ----------------------------------------------
def test_annotation_appearance_is_composited():
    raster = _render(_build())
    assert _is_red(raster, 30, 20), "the annotation should cover its rect"


def test_draw_annotations_false_renders_content_only():
    raster = _render(_build(), draw_annotations=False)
    assert not _is_red(raster, 30, 20)


def test_appearance_fills_its_rect_and_nothing_else():
    """The 20x20 BBox is scaled onto a 40x40 rect at the page's top-left."""
    raster = _render(_build(rect=(10, 160, 50, 200)))
    # Inside the rect (device y is measured from the top).
    assert _is_red(raster, 12, 38)
    assert _is_red(raster, 48, 2)
    # Just outside it.
    assert not _is_red(raster, 8, 20)
    assert not _is_red(raster, 30, 42)


def _red_area(raster) -> int:
    return sum(
        1
        for y in range(raster.height)
        for x in range(raster.width)
        if _is_red(raster, x, y)
    )


def test_matrix_is_honoured_when_fitting_the_bbox():
    """A /Matrix that scales the BBox still fits the same /Rect (12.5.5).

    The fitting scale becomes non-integral, so edge pixels can round
    differently; the covered area is what must match.
    """
    plain = _red_area(_render(_build()))
    scaled = _red_area(_render(_build(matrix=(3, 0, 0, 3, 0, 0))))
    assert plain == pytest.approx(scaled, abs=raster_tolerance(plain))


def raster_tolerance(area: int) -> int:
    """Allow a one-pixel border to differ on a non-integral scale."""
    side = int(area**0.5)
    return max(4, side * 4)


# --- visibility rules ------------------------------------------------------
@pytest.mark.parametrize("flags", [2, 32, 2 | 32])
def test_hidden_and_noview_annotations_are_skipped(flags):
    raster = _render(_build(flags=flags))
    assert not _is_red(raster, 30, 20)


def test_print_flag_alone_does_not_hide():
    raster = _render(_build(flags=4))
    assert _is_red(raster, 30, 20)


def test_popup_is_not_painted():
    raster = _render(_build(subtype="Popup"))
    assert not _is_red(raster, 30, 20)


# --- appearance states -----------------------------------------------------
def _stateful_document(active: str | None):
    from aspose_pdf.engine.simple_pdf import SimplePdf

    pdf = SimplePdf()
    pdf.pages = [_PAGE]
    pdf.page_contents = [b""]
    pdf._ensure_cos()
    cos = pdf._cos_doc
    states = PdfDictionary(
        {
            PdfName("On"): _appearance(cos, _RED_FILL),
            PdfName("Off"): _appearance(cos, b"q 0 0 1 rg 0 0 20 20 re f Q\n"),
        }
    )
    mapping = {
        PdfName("Type"): PdfName("Annot"),
        PdfName("Subtype"): PdfName("Widget"),
        PdfName("Rect"): PdfArray([PdfNumber(v) for v in (10, 160, 50, 200)]),
        PdfName("AP"): PdfDictionary({PdfName("N"): states}),
    }
    if active is not None:
        mapping[PdfName("AS")] = PdfName(active)
    page = pdf._get_page_dict(0)
    page.mapping[PdfName("Annots")] = PdfArray(
        [cos.register_object(PdfDictionary(mapping))]
    )
    document = Document()
    document._engine_pdf = pdf
    return document


def test_appearance_state_selects_the_named_stream():
    on = _render(_stateful_document("On"))
    assert _is_red(on, 30, 20)
    off = _render(_stateful_document("Off"))
    r, _g, b = off.get_pixel(30, 20)
    assert b > 200 and r < 80


def test_missing_appearance_state_draws_nothing():
    """Without /AS there is no way to know which state applies."""
    raster = _render(_stateful_document(None))
    r, g, b = raster.get_pixel(30, 20)
    assert (r, g, b) == (255, 255, 255)


# --- robustness ------------------------------------------------------------
def test_malformed_annotation_does_not_abort_the_page():
    from aspose_pdf.engine.simple_pdf import SimplePdf

    pdf = SimplePdf()
    pdf.pages = [_PAGE]
    pdf.page_contents = [b"q 0 1 0 rg 100 100 40 40 re f Q"]
    pdf._ensure_cos()
    cos = pdf._cos_doc
    broken = PdfDictionary(
        {
            PdfName("Subtype"): PdfName("Square"),
            PdfName("Rect"): PdfArray([PdfNumber(1)]),  # too short
            PdfName("AP"): PdfDictionary({PdfName("N"): PdfNumber(7)}),  # not a stream
        }
    )
    good = _annot(cos, (10, 160, 50, 200))
    page = pdf._get_page_dict(0)
    page.mapping[PdfName("Annots")] = PdfArray(
        [cos.register_object(broken), cos.register_object(good)]
    )
    document = Document()
    document._engine_pdf = pdf
    raster = _render(document)

    # Page content still drawn, and the sound annotation after the broken one too.
    r, g, _b = raster.get_pixel(120, 80)
    assert g > 200 and r < 80
    assert _is_red(raster, 30, 20)


def test_annotation_without_an_appearance_is_skipped():
    document = _document([PdfDictionary({
        PdfName("Subtype"): PdfName("Square"),
        PdfName("Rect"): PdfArray([PdfNumber(v) for v in (10, 160, 50, 200)]),
    })])
    raster = _render(document)
    assert raster.get_pixel(30, 20) == (255, 255, 255)


def test_rendering_survives_a_save_and_reload():
    document = _build()
    output = BytesIO()
    document.save(output)
    reloaded = Document().load_from(output.getvalue())
    assert _is_red(reloaded.pages[0].render(dpi=72, antialias=False), 30, 20)

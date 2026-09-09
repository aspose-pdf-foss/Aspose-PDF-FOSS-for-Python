"""An annotation with no ``/AP`` is not invisible.

ISO 32000-1 12.5.2 gives an annotation the properties its appearance is made
of -- a ``Square``'s ``/IC`` and ``/C``, a ``Line``'s ``/L``, a ``Highlight``'s
``/QuadPoints`` -- and 12.5.5 notes that a reader generates the appearance when
the file does not carry one. Plenty of writers do not carry one.

This library could already build them: ``generate_appearances()`` writes them
into the document. The *renderer* never asked, so it drew nothing at all for
every such annotation -- the same shape of gap as the rest of this month's
work, a rule implemented in one place instead of at the boundary. The renderer
asks now, and throws the stream away rather than editing the document it is
drawing.
"""

from __future__ import annotations

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.cos import PdfArray, PdfDictionary, PdfName, PdfNumber
from aspose_pdf.engine.simple_pdf import SimplePdf

_WHITE = (255, 255, 255)
_PAGE = (0, 0, 80, 80)
#: The annotation rectangle: device rows and columns 10..70.
_RECT = (10.0, 10.0, 70.0, 70.0)


def _document(*annotations: dict) -> Document:
    pdf = SimplePdf()
    pdf.pages = [_PAGE]
    pdf.page_contents = [b""]
    pdf._ensure_cos()
    entries = []
    for annotation in annotations:
        mapping = {PdfName("Type"): PdfName("Annot")}
        for key, value in annotation.items():
            mapping[PdfName(key)] = value
        entries.append(pdf._cos_doc.register_object(PdfDictionary(mapping)))
    pdf._get_page_dict(0).mapping[PdfName("Annots")] = PdfArray(entries)
    document = Document()
    document._engine_pdf = pdf
    return document


def _numbers(*values) -> PdfArray:
    return PdfArray([PdfNumber(v) for v in values])


def _annotation(subtype: str, **extra) -> dict:
    entry = {
        "Subtype": PdfName(subtype),
        "Rect": _numbers(*_RECT),
        "F": PdfNumber(4),
    }
    entry.update(extra)
    return entry


def _ink(*annotations: dict) -> int:
    return _ink_of(_document(*annotations))


def _ink_of(document: Document) -> int:
    raster = document.pages[0].render(dpi=72, antialias=False)
    return sum(
        1 for y in range(80) for x in range(80) if raster.get_pixel(x, y) != _WHITE
    )


def _pixel(annotation: dict, at: tuple[int, int]) -> tuple[int, int, int]:
    raster = _document(annotation).pages[0].render(dpi=72, antialias=False)
    return raster.get_pixel(*at)


# --- the shapes -------------------------------------------------------------


def test_a_square_is_drawn_from_its_own_colours():
    square = _annotation(
        "Square",
        IC=_numbers(1, 0, 0),
        C=_numbers(0, 0, 1),
        BS=PdfDictionary({PdfName("W"): PdfNumber(4)}),
    )
    assert _pixel(square, (40, 40)) == (255, 0, 0)  # /IC fills it
    assert _pixel(square, (40, 11)) == (0, 0, 255)  # /C draws the border


def test_a_circle_fills_a_disc_and_leaves_the_corners():
    circle = _annotation("Circle", IC=_numbers(1, 0, 0), C=_numbers(0, 0, 1))
    assert _pixel(circle, (40, 40)) == (255, 0, 0)
    assert _pixel(circle, (12, 12)) == _WHITE  # outside the ellipse


@pytest.mark.parametrize(
    ("subtype", "geometry"),
    [
        ("Line", {"L": _numbers(20, 20, 60, 60)}),
        ("Polygon", {"Vertices": _numbers(20, 20, 60, 25, 40, 60)}),
        ("PolyLine", {"Vertices": _numbers(20, 20, 60, 25, 40, 60)}),
        ("Ink", {"InkList": PdfArray([_numbers(20, 20, 40, 55, 60, 25)])}),
        ("Highlight", {"QuadPoints": _numbers(20, 60, 60, 60, 20, 20, 60, 20)}),
        ("Underline", {"QuadPoints": _numbers(20, 60, 60, 60, 20, 20, 60, 20)}),
        ("StrikeOut", {"QuadPoints": _numbers(20, 60, 60, 60, 20, 20, 60, 20)}),
        ("Squiggly", {"QuadPoints": _numbers(20, 60, 60, 60, 20, 20, 60, 20)}),
    ],
)
def test_a_subtype_that_carries_its_geometry_is_drawn(subtype, geometry):
    annotation = _annotation(subtype, C=_numbers(0, 0, 1), **geometry)
    assert _ink(annotation) > 0


def test_a_subtype_missing_its_geometry_draws_nothing():
    # A Line with no /L has nothing to describe, and inventing one would be
    # worse than leaving it out.
    assert _ink(_annotation("Line", C=_numbers(0, 0, 1))) == 0


def test_a_link_has_no_appearance_to_generate():
    # A Link is a region, not a mark; readers draw nothing for one.
    assert _ink(_annotation("Link", Border=_numbers(0, 0, 2))) == 0


# --- the icons are not scaled to the rectangle ------------------------------


@pytest.mark.parametrize("subtype", ["Text", "FileAttachment"])
def test_an_icon_keeps_its_size_however_big_the_rectangle_is(subtype):
    # 12.5.6.4 / 12.5.6.15: the rectangle does not scale a note or attachment
    # icon. Filling a 120pt box with a sticky note is what it used to do.
    small = _document(
        {
            "Type": PdfName("Annot"),
            "Subtype": PdfName(subtype),
            "Rect": _numbers(30, 30, 50, 50),
            "F": PdfNumber(4),
        }
    )
    assert _ink(_annotation(subtype)) == _ink_of(small) > 0


# --- an existing appearance still wins --------------------------------------


def test_a_carried_appearance_is_used_instead_of_a_generated_one():
    from aspose_pdf.engine.cos import PdfStream

    pdf = SimplePdf()
    pdf.pages = [_PAGE]
    pdf.page_contents = [b""]
    pdf._ensure_cos()
    appearance = pdf._cos_doc.register_object(
        PdfStream(
            b"0 1 0 rg 0 0 60 60 re f",
            {
                PdfName("Type"): PdfName("XObject"),
                PdfName("Subtype"): PdfName("Form"),
                PdfName("BBox"): _numbers(0, 0, 60, 60),
            },
        )
    )
    annot = PdfDictionary(
        {
            PdfName("Type"): PdfName("Annot"),
            PdfName("Subtype"): PdfName("Square"),
            PdfName("Rect"): _numbers(*_RECT),
            PdfName("F"): PdfNumber(4),
            PdfName("IC"): _numbers(1, 0, 0),
            PdfName("AP"): PdfDictionary({PdfName("N"): appearance}),
        }
    )
    pdf._get_page_dict(0).mapping[PdfName("Annots")] = PdfArray(
        [pdf._cos_doc.register_object(annot)]
    )
    document = Document()
    document._engine_pdf = pdf
    raster = document.pages[0].render(dpi=72, antialias=False)
    assert raster.get_pixel(40, 40) == (0, 255, 0)  # the carried one, not /IC


def test_a_hidden_annotation_is_still_hidden():
    hidden = _annotation("Square", IC=_numbers(1, 0, 0))
    hidden["F"] = PdfNumber(2)
    assert _ink(hidden) == 0


# --- rendering does not write to the document -------------------------------


def test_drawing_an_annotation_does_not_give_it_an_appearance():
    document = _document(_annotation("Square", IC=_numbers(1, 0, 0)))
    document.pages[0].render(dpi=72, antialias=False)
    annots = document._engine_pdf._get_page_dict(0).mapping[PdfName("Annots")]
    annot = document._engine_pdf._resolve(annots.items[0])
    assert PdfName("AP") not in annot.mapping


def test_the_document_can_still_be_asked_to_keep_one():
    document = _document(_annotation("Square", IC=_numbers(1, 0, 0)))
    assert document._engine_pdf.generate_appearances(0) == 1
    annots = document._engine_pdf._get_page_dict(0).mapping[PdfName("Annots")]
    annot = document._engine_pdf._resolve(annots.items[0])
    assert PdfName("AP") in annot.mapping


def test_the_generated_stream_is_a_form_the_size_of_the_rectangle():
    # Deliberately not square, so a width and a height cannot be swapped
    # without the box saying so.
    oblong = _annotation("Square", IC=_numbers(1, 0, 0))
    oblong["Rect"] = _numbers(10, 20, 70, 40)
    document = _document(oblong)
    annots = document._engine_pdf._get_page_dict(0).mapping[PdfName("Annots")]
    annot = document._engine_pdf._resolve(annots.items[0])
    stream = document._engine_pdf.build_annotation_appearance(annot)
    assert stream is not None
    assert stream.mapping[PdfName("Subtype")].name == "/Form"
    box = [item.value for item in stream.mapping[PdfName("BBox")].items]
    assert box == [0, 0, 60.0, 20.0]


def test_an_oblong_annotation_is_drawn_the_shape_it_is():
    oblong = _annotation("Square", IC=_numbers(1, 0, 0))
    oblong["Rect"] = _numbers(10, 20, 70, 40)
    raster = _document(oblong).pages[0].render(dpi=72, antialias=False)
    assert raster.get_pixel(40, 50) == (255, 0, 0)  # inside: rows 40..60
    assert raster.get_pixel(40, 30) == _WHITE  # above it
    assert raster.get_pixel(5, 50) == _WHITE  # left of it


def test_a_kept_appearance_keeps_the_resources_it_needs():
    # A Highlight paints through a Multiply blend, which lives in an
    # /ExtGState: without the resources the stored stream names a graphics
    # state that is not there.
    document = _document(
        _annotation(
            "Highlight",
            C=_numbers(1, 1, 0),
            QuadPoints=_numbers(20, 60, 60, 60, 20, 20, 60, 20),
        )
    )
    pdf = document._engine_pdf
    assert pdf.generate_appearances(0) == 1
    annots = pdf._get_page_dict(0).mapping[PdfName("Annots")]
    annot = pdf._resolve(annots.items[0])
    normal = pdf._resolve(
        pdf._resolve(annot.mapping[PdfName("AP")]).mapping[PdfName("N")]
    )
    resources = pdf._resolve(normal.mapping.get(PdfName("Resources")))
    assert resources is not None
    assert PdfName("ExtGState") in resources.mapping


# --- the SVG export goes the same way ---------------------------------------


def test_the_svg_export_draws_a_generated_appearance_too():
    document = _document(_annotation("Square", IC=_numbers(1, 0, 0)))
    assert "<path" in document.pages[0].to_svg()

"""Stamps: a mark drawn over or under a page's own content.

A stamp is a form XObject invoked once per page -- which is what qpdf's
``--overlay``/``--underlay`` and MuPDF's ``show_pdf_page`` both write, and it
means a mark on every page of a document is one object, not one per page. The
invocation is wrapped in ``q``/``Q``, so a stamp cannot disturb the page, and
for a foreground stamp the page's own content is isolated first (see
:mod:`.content_isolation`) so the page cannot disturb the stamp. A background
stamp goes in *before* the page's content instead, exactly as an underlay does.

Placement follows qpdf. The stamp is positioned inside what a reader actually
shows -- the crop box, falling back to the media box -- and the page's
``/Rotate`` is undone so the stamp stands upright on screen rather than lying on
its side with the unrotated page. (MuPDF's ``show_pdf_page`` leaves the page
rotation alone, so a stamp turns with the page there; qpdf's behaviour is the
one a stamp is asked for.) The matrix this builds for a rotated page is the one
qpdf writes for the same stamp, to the last digit.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from .cos import (
    PdfArray,
    PdfDictionary,
    PdfIndirectReference,
    PdfName,
    PdfNumber,
    PdfStream,
)

#: An affine transform as PDF writes it: ``x' = a*x + c*y + e``, ``y' = b*x +
#: d*y + f``.
Matrix = tuple[float, float, float, float, float, float]

IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def multiply(first: Matrix, then: Matrix) -> Matrix:
    """The transform that applies *first* and then *then*."""
    a1, b1, c1, d1, e1, f1 = first
    a2, b2, c2, d2, e2, f2 = then
    return (
        a1 * a2 + b1 * c2,
        a1 * b2 + b1 * d2,
        c1 * a2 + d1 * c2,
        c1 * b2 + d1 * d2,
        e1 * a2 + f1 * c2 + e2,
        e1 * b2 + f1 * d2 + f2,
    )


def scaling(x: float, y: float) -> Matrix:
    return (float(x), 0.0, 0.0, float(y), 0.0, 0.0)


def translation(x: float, y: float) -> Matrix:
    return (1.0, 0.0, 0.0, 1.0, float(x), float(y))


def rotation(degrees: float) -> Matrix:
    """A counter-clockwise rotation about the origin."""
    radians = math.radians(float(degrees))
    cos, sin = math.cos(radians), math.sin(radians)
    return (cos, sin, -sin, cos, 0.0, 0.0)


def visible_box(pdf: Any, page_index: int) -> tuple[float, float, float, float]:
    """What a reader shows of the page: its crop box, or its media box."""
    box = pdf.get_page_crop_box(page_index)
    if box is None:
        pages = getattr(pdf, "pages", [])
        box = tuple(pages[page_index]) if page_index < len(pages) else (0, 0, 612, 792)
    x0, y0, x1, y1 = (float(value) for value in box[:4])
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def placement_matrix(
    *,
    box: tuple[float, float, float, float],
    page_rotation: int,
    size: tuple[float, float],
    zoom: tuple[float, float],
    rotate: float,
    alignment: tuple[str, str],
    indent: tuple[float, float],
) -> Matrix:
    """Where a stamp of *size* goes, as the matrix its ``Do`` runs under.

    *box* is the page's visible box in user space and *page_rotation* its
    ``/Rotate``; the stamp is laid out in the frame a reader sees and the result
    maps that frame back into user space. *rotate* turns the stamp itself,
    counter-clockwise about its centre, and *alignment* places the bounding box
    the turned stamp occupies -- so a rotated stamp still sits inside the page.
    """
    width, height = (size[0] * zoom[0], size[1] * zoom[1])
    # The stamp, scaled and turned about its own centre, centred on the origin.
    local = multiply(
        multiply(scaling(*zoom), translation(-size[0] * zoom[0] / 2, -size[1] * zoom[1] / 2)),
        rotation(rotate),
    )
    radians = math.radians(float(rotate))
    cos, sin = abs(math.cos(radians)), abs(math.sin(radians))
    half_width = (width * cos + height * sin) / 2
    half_height = (width * sin + height * cos) / 2

    x0, y0, x1, y1 = box
    page_width, page_height = x1 - x0, y1 - y0
    turned = page_rotation % 360 in (90, 270)
    seen_width = page_height if turned else page_width
    seen_height = page_width if turned else page_height

    horizontal, vertical = alignment
    left = {
        "Left": 0.0,
        "Center": (seen_width - 2 * half_width) / 2,
        "Right": seen_width - 2 * half_width,
    }[horizontal] + indent[0]
    bottom = {
        "Bottom": 0.0,
        "Center": (seen_height - 2 * half_height) / 2,
        "Top": seen_height - 2 * half_height,
    }[vertical] + indent[1]
    placed = multiply(local, translation(left + half_width, bottom + half_height))

    # The frame a reader sees, back into user space: undoing /Rotate is what
    # keeps the stamp upright on screen.
    unrotate = {
        0: translation(x0, y0),
        90: multiply(rotation(90), translation(x0 + page_width, y0)),
        180: multiply(rotation(180), translation(x0 + page_width, y0 + page_height)),
        270: multiply(rotation(270), translation(x0, y0 + page_height)),
    }[page_rotation % 360]
    return multiply(placed, unrotate)


def build_form(
    pdf: Any,
    content: bytes,
    size: tuple[float, float],
    resources: PdfDictionary | None,
) -> PdfIndirectReference:
    """Register the stamp's content as a form XObject and return its reference.

    The ``/BBox`` is the stamp's own box, so nothing it draws escapes the area
    it was measured to need.
    """
    mapping: dict[Any, Any] = {
        PdfName("Type"): PdfName("XObject"),
        PdfName("Subtype"): PdfName("Form"),
        PdfName("FormType"): PdfNumber(1),
        PdfName("BBox"): PdfArray(
            [PdfNumber(0), PdfNumber(0), PdfNumber(size[0]), PdfNumber(size[1])]
        ),
    }
    if resources is not None:
        mapping[PdfName("Resources")] = resources
    return pdf._cos_doc.register_object(PdfStream(content=content, mapping=mapping))


def _invocation(name: str, matrix: Matrix) -> bytes:
    from .content_authoring import format_number

    numbers = b" ".join(format_number(value).encode("ascii") for value in matrix)
    return b"q " + numbers + b" cm /" + name.encode("ascii") + b" Do Q\n"


def place(
    pdf: Any,
    page_index: int,
    form: PdfIndirectReference,
    matrix: Matrix,
    *,
    background: bool,
) -> str:
    """Draw *form* on the page under *matrix*; return its resource name.

    A foreground stamp is appended, which isolates the page's own content on
    the way; a background one is put in front of that content, where it needs
    no isolation of its own -- its ``q``/``Q`` is balanced, so what follows is
    unaffected by it.
    """
    xobjects = pdf._ensure_resource_subdict(page_index, "XObject")
    name = pdf._unique_resource_name(xobjects, "Stamp", "Stamp1")
    xobjects.mapping[PdfName(name)] = form
    content = _invocation(name, matrix)
    if background:
        _prepend(pdf, page_index, content)
    else:
        pdf._append_content_to_page(page_index, content)
    return name


def apply(pdf: Any, stamp: Any, page_indices: Sequence[int]) -> None:
    """Put *stamp* on each of *page_indices*.

    A stamp whose drawing does not depend on the page is built once and invoked
    from each of them, so stamping a hundred pages adds one object and a
    hundred short invocations. Each page is measured on its own, because pages
    of one document may differ in size and in rotation.
    """
    pdf._ensure_not_disposed()
    pdf._ensure_cos()
    indices = list(page_indices)
    for page_index in indices:
        pdf._validate_page_index(page_index)
    placement = stamp._placement()
    form: PdfIndirectReference | None = None
    size: tuple[float, float] | None = None
    for page_index in indices:
        if form is None or not stamp._reusable():
            content, size, resources = stamp._content(pdf, page_index)
            form = build_form(pdf, content, size, resources)
        assert size is not None
        matrix = placement_matrix(
            box=visible_box(pdf, page_index),
            page_rotation=pdf.get_page_rotation(page_index),
            size=size,
            **placement,
        )
        place(pdf, page_index, form, matrix, background=bool(stamp.background))


def _prepend(pdf: Any, page_index: int, content: bytes) -> None:
    """Put *content* before everything the page draws."""
    pdf._materialize_page_contents_for_edit()
    if pdf._cos_doc is None:
        pdf._ensure_cos()
    current = pdf.page_contents[page_index]
    pdf.page_contents[page_index] = content + current
    pdf._extracted_text = None
    # The page's own content still leaves behind whatever it left, so a later
    # append still needs to isolate it. The line above already sees to that --
    # the memo is matched on the identity of the bytes it remembers, and those
    # are not these -- so this only makes the point independent of that.
    pdf._isolated_page_contents.pop(page_index, None)

    page = pdf._get_page_dict(page_index)
    if not isinstance(page, PdfDictionary):
        return
    contents_key = PdfName("Contents")
    entry = page.mapping.get(contents_key)
    new_ref = pdf._cos_doc.register_object(PdfStream(content=content, mapping={}))
    if entry is None:
        page.mapping[contents_key] = new_ref
        return
    existing_obj = pdf._resolve(entry)
    if isinstance(existing_obj, PdfArray):
        existing = list(existing_obj.items)
    else:
        if isinstance(existing_obj, PdfStream) and not isinstance(
            entry, PdfIndirectReference
        ):
            entry = pdf._cos_doc.register_object(existing_obj)
        existing = [entry]
    # The page gets an array of its own: one shared by several pages would put
    # this stamp on all of them.
    page.mapping[contents_key] = PdfArray([new_ref, *existing])
    while len(pdf._content_obj_ids) < len(pdf.pages):
        pdf._content_obj_ids.append(0)
    if page_index < len(pdf._content_obj_ids):
        pdf._content_obj_ids[page_index] = 0

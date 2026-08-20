"""GraphicsAbsorber: the vector/image counterpart to text absorption.

The absorber walks a page's content stream and reports where each painted path
and each placed image lands, in page (user) space. These tests pin the geometry
(including transforms, form XObjects and curve bounds), the colour reporting,
and the limits of what is collected.
"""

from __future__ import annotations

import io
import struct
import zlib
from pathlib import Path

import pytest

from aspose_pdf import Document
from aspose_pdf.graphics import (
    GraphicElement,
    GraphicElementCollection,
    GraphicsAbsorber,
    InvalidOperationException,
)


def _absorb(page_or_document) -> list[GraphicElement]:
    absorber = GraphicsAbsorber()
    absorber.visit(page_or_document)
    return list(absorber.elements)


def _rect(element: GraphicElement) -> tuple[float, float, float, float]:
    rectangle = element.rectangle
    return (
        round(rectangle.x, 3),
        round(rectangle.y, 3),
        round(rectangle.width, 3),
        round(rectangle.height, 3),
    )


def _raw_pdf(objects: dict[int, bytes], root: int = 1) -> bytes:
    """Assemble a minimal PDF from literal object bodies."""
    out = bytearray(b"%PDF-1.7\n")
    offsets: dict[int, int] = {}
    for number in sorted(objects):
        offsets[number] = len(out)
        out += f"{number} 0 obj\n".encode() + objects[number] + b"\nendobj\n"
    xref = len(out)
    top = max(objects) + 1
    out += f"xref\n0 {top}\n".encode() + b"0000000000 65535 f \n"
    for number in range(1, top):
        out += f"{offsets[number]:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {top} /Root {root} 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n"
    ).encode()
    return bytes(out)


def _stream_object(dictionary: bytes, content: bytes) -> bytes:
    return (
        dictionary[:-2]
        + f"/Length {len(content)} >>".encode()
        + b"\nstream\n"
        + content
        + b"\nendstream"
    )


def _page_with_content(content: bytes, resources: bytes = b"<< >>") -> bytes:
    return _raw_pdf(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
            3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Resources "
            + resources
            + b" /Contents 4 0 R >>",
            4: _stream_object(b"<< >>", content),
        }
    )


def _tiny_png(width: int = 4, height: int = 4) -> bytes:
    raw = b"".join(b"\x00" + bytes([255, 0, 0] * width) for _ in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


# ---------------------------------------------------------------------------
# What gets collected
# ---------------------------------------------------------------------------
def test_authored_shapes_are_absorbed_with_their_geometry() -> None:
    document = Document()
    page = document.pages.add()
    page.draw_rectangle(100, 100, 200, 50, fill_color=(1.0, 0.0, 0.0), stroke_color=None)
    page.draw_line(50, 700, 500, 700, stroke_color=(0.0, 0.0, 1.0), line_width=3)

    elements = _absorb(page)
    document.dispose()

    assert [element.kind for element in elements] == ["path", "path"]
    assert [element.operation for element in elements] == ["fill", "stroke"]
    assert _rect(elements[0]) == (100.0, 100.0, 200.0, 50.0)
    assert elements[0].fill_color == (1.0, 0.0, 0.0)
    assert elements[0].line_width is None
    assert _rect(elements[1]) == (50.0, 700.0, 450.0, 0.0)
    assert elements[1].stroke_color == (0.0, 0.0, 1.0)
    assert elements[1].line_width == pytest.approx(3.0)


def test_text_is_not_collected() -> None:
    """Text belongs to TextFragmentAbsorber; the graphics absorber ignores it."""
    document = Document()
    page = document.pages.add()
    page.add_text("nothing to absorb here", 72, 400)

    elements = _absorb(page)
    document.dispose()
    assert elements == []


def test_placed_image_reports_its_placement(tmp_path: Path) -> None:
    document = Document()
    page = document.pages.add()
    page.add_image(_tiny_png(), 120, 500, 200, 80)
    out = tmp_path / "image.pdf"
    document.save(out)
    document.dispose()

    with Document(out) as reloaded:
        elements = _absorb(reloaded.pages[0])

    assert len(elements) == 1
    assert elements[0].kind == "image"
    assert elements[0].resource_name == "Im1"
    assert _rect(elements[0]) == (120.0, 500.0, 200.0, 80.0)


def test_empty_page_absorbs_nothing() -> None:
    document = Document()
    page = document.pages.add()
    assert _absorb(page) == []
    document.dispose()


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------
def test_curve_bounds_are_exact_not_the_control_hull() -> None:
    """Control points reach y=+-300; the drawn curve only reaches +-86.6."""
    data = _page_with_content(b"q 0 0 m 100 300 200 -300 300 0 c S Q")
    with Document(io.BytesIO(data)) as document:
        elements = _absorb(document.pages[0])

    assert len(elements) == 1
    x, y, width, height = _rect(elements[0])
    assert (x, width) == (0.0, 300.0)
    assert y == pytest.approx(-86.603, abs=0.01)
    assert height == pytest.approx(173.205, abs=0.01)


def test_form_xobject_composes_its_matrix_with_the_placement() -> None:
    """/Matrix [3 0 0 3 1 1] inside cm [2 0 0 2 50 50]: (0,0)-(10,10) -> (52,52)-(112,112)."""
    data = _raw_pdf(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
            3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Resources "
            b"<< /XObject << /Fx 5 0 R >> >> /Contents 4 0 R >>",
            4: _stream_object(b"<< >>", b"q 2 0 0 2 50 50 cm /Fx Do Q"),
            5: _stream_object(
                b"<< /Type /XObject /Subtype /Form /BBox [0 0 10 10] "
                b"/Matrix [3 0 0 3 1 1] >>",
                b"0 0 10 10 re f",
            ),
        }
    )
    with Document(io.BytesIO(data)) as document:
        elements = _absorb(document.pages[0])

    assert len(elements) == 1
    assert _rect(elements[0]) == (52.0, 52.0, 60.0, 60.0)


def test_self_referential_form_terminates() -> None:
    data = _raw_pdf(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
            3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Resources "
            b"<< /XObject << /Fx 5 0 R >> >> /Contents 4 0 R >>",
            4: _stream_object(b"<< >>", b"/Fx Do"),
            5: _stream_object(
                b"<< /Type /XObject /Subtype /Form /BBox [0 0 10 10] "
                b"/Resources << /XObject << /Fx 5 0 R >> >> >>",
                b"0 0 5 5 re f /Fx Do",
            ),
        }
    )
    with Document(io.BytesIO(data)) as document:
        elements = _absorb(document.pages[0])

    assert len(elements) == 1
    assert _rect(elements[0]) == (0.0, 0.0, 5.0, 5.0)


def test_form_xobject_inside_an_encrypted_document_is_absorbed() -> None:
    """The form's stream is decrypted with its own object key before parsing."""
    path = Path(__file__).parent / "fixtures_encrypted_form_xobject.pdf"
    with Document(path, password="user") as document:
        elements = _absorb(document.pages[0])

    assert len(elements) == 1
    assert _rect(elements[0]) == (52.0, 52.0, 60.0, 60.0)


def test_graphics_state_is_restored_by_q_and_Q() -> None:
    content = b"q 10 0 0 10 5 5 cm 0 0 1 1 re f Q 0 0 1 1 re f"
    with Document(io.BytesIO(_page_with_content(content))) as document:
        elements = _absorb(document.pages[0])

    assert _rect(elements[0]) == (5.0, 5.0, 10.0, 10.0)
    assert _rect(elements[1]) == (0.0, 0.0, 1.0, 1.0)


def test_clipping_path_is_reported_as_a_clip() -> None:
    content = b"20 20 40 40 re W n 0 0 10 10 re f"
    with Document(io.BytesIO(_page_with_content(content))) as document:
        elements = _absorb(document.pages[0])

    assert [element.operation for element in elements] == ["clip", "fill"]
    assert _rect(elements[0]) == (20.0, 20.0, 40.0, 40.0)


def test_path_without_a_painting_operator_is_not_reported() -> None:
    with Document(io.BytesIO(_page_with_content(b"0 0 10 10 re n"))) as document:
        assert _absorb(document.pages[0]) == []


# ---------------------------------------------------------------------------
# Colour
# ---------------------------------------------------------------------------
def test_device_colours_are_reported() -> None:
    content = (
        b"0.25 g 0 0 1 1 re f "
        b"0 1 0 rg 0 0 1 1 re f "
        b"0 0 0 1 k 0 0 1 1 re f"
    )
    with Document(io.BytesIO(_page_with_content(content))) as document:
        elements = _absorb(document.pages[0])

    assert elements[0].fill_color == pytest.approx((0.25, 0.25, 0.25))
    assert elements[1].fill_color == pytest.approx((0.0, 1.0, 0.0))
    assert elements[2].fill_color == pytest.approx((0.0, 0.0, 0.0))


def test_device_components_through_scn_are_reported() -> None:
    content = b"/DeviceRGB cs 1 0 0 scn 0 0 1 1 re f"
    with Document(io.BytesIO(_page_with_content(content))) as document:
        elements = _absorb(document.pages[0])
    assert elements[0].fill_color == pytest.approx((1.0, 0.0, 0.0))


def test_non_device_colour_is_reported_as_unknown() -> None:
    """A pattern or Separation colour is not guessed at."""
    content = b"/Pattern cs /P1 scn 0 0 1 1 re f"
    with Document(io.BytesIO(_page_with_content(content))) as document:
        elements = _absorb(document.pages[0])
    assert elements[0].fill_color is None


# ---------------------------------------------------------------------------
# Absorber surface
# ---------------------------------------------------------------------------
def test_visiting_a_document_covers_every_page() -> None:
    document = Document()
    for index in range(3):
        page = document.pages.add()
        page.draw_rectangle(10 * (index + 1), 10, 5, 5)

    elements = _absorb(document)
    document.dispose()

    assert [element.page_index for element in elements] == [0, 1, 2]
    assert _rect(elements[2])[0] == 30.0


def test_visit_replaces_the_previous_result() -> None:
    document = Document()
    first = document.pages.add()
    first.draw_rectangle(10, 10, 5, 5)
    second = document.pages.add()

    absorber = GraphicsAbsorber()
    absorber.visit(first)
    assert len(absorber.elements) == 1
    absorber.visit(second)
    assert len(absorber.elements) == 0
    document.dispose()


def test_suppress_update_stops_collection() -> None:
    document = Document()
    page = document.pages.add()
    page.draw_rectangle(10, 10, 5, 5)

    absorber = GraphicsAbsorber()
    absorber.suppress_update()
    absorber.visit(page)
    assert len(absorber.elements) == 0

    absorber.resume_update()
    absorber.visit(page)
    assert len(absorber.elements) == 1
    document.dispose()


def test_visiting_something_else_raises() -> None:
    with pytest.raises(TypeError, match="expects a Page or a Document"):
        GraphicsAbsorber().visit("not a page")


def test_collection_add_and_remove_are_in_memory_only() -> None:
    document = Document()
    page = document.pages.add()
    page.draw_rectangle(10, 10, 5, 5)

    absorber = GraphicsAbsorber()
    absorber.visit(page)
    element = absorber.elements[0]
    absorber.elements.remove(element)
    assert len(absorber.elements) == 0
    # The page is untouched: absorbing again finds the rectangle.
    absorber.visit(page)
    assert len(absorber.elements) == 1
    document.dispose()


def test_collection_rejects_an_element_from_another_parent() -> None:
    collection = GraphicElementCollection()
    collection._parent = object()
    foreign = GraphicsAbsorber()

    class _Foreign:
        parent = object()

    with pytest.raises(InvalidOperationException):
        collection.add(_Foreign())
    assert len(foreign.elements) == 0

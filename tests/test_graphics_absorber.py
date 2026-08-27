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
    # A 3pt stroke straddles the line: the box grows by half its width on
    # every side, so it covers the ink rather than the bare geometry.
    assert _rect(elements[1]) == (48.5, 698.5, 453.0, 3.0)
    assert elements[1].stroke_color == (0.0, 0.0, 1.0)
    assert elements[1].line_width == pytest.approx(3.0)


def test_text_is_collected_with_the_box_the_glyphs_occupy() -> None:
    """Text runs are elements too, measured with the renderer's own metrics."""
    document = Document()
    page = document.pages.add()
    page.add_text("Hi", 72, 400, font_size=20)

    elements = _absorb(page)
    document.dispose()

    assert [element.kind for element in elements] == ["text"]
    element = elements[0]
    assert element.resource_name == "F1"
    assert element.fill_color == (0.0, 0.0, 0.0)
    x, y, width, height = _rect(element)
    assert x == 72.0
    # Helvetica ascent/descent put the box around the baseline at y=400.
    assert y == pytest.approx(395.0, abs=0.5)
    assert height == pytest.approx(20.0, abs=0.5)
    assert 10.0 < width < 30.0


def test_text_width_comes_from_real_glyph_advances() -> None:
    """Ten narrow glyphs and ten wide ones must not measure the same."""
    document = Document()
    page = document.pages.add()
    page.add_text("iiiiiiiiii", 72, 700, font_size=20)
    page.add_text("MMMMMMMMMM", 72, 650, font_size=20)

    narrow, wide = _absorb(page)
    document.dispose()

    # Helvetica: 'i' is 222/1000 em, 'M' is 833/1000.
    assert narrow.rectangle.width == pytest.approx(44.4, abs=0.2)
    assert wide.rectangle.width == pytest.approx(166.6, abs=0.2)


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
    # The default 1pt stroke adds half a point on each side of the curve's box.
    assert (x, width) == (-0.5, 301.0)
    assert y == pytest.approx(-87.103, abs=0.01)
    assert height == pytest.approx(174.205, abs=0.01)


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


def test_a_pattern_colour_is_still_reported_as_unknown() -> None:
    """A pattern has no single colour, so none is invented."""
    content = b"/Pattern cs /P1 scn 0 0 1 1 re f"
    with Document(io.BytesIO(_page_with_content(content))) as document:
        elements = _absorb(document.pages[0])
    assert elements[0].fill_color is None


def test_an_iccbased_colour_is_resolved_through_its_component_count() -> None:
    """ICCBased used to report nothing; its N says how to read the components."""
    resources = (
        b"<< /ColorSpace << /CS0 [/ICCBased 5 0 R] >> >>"
    )
    data = _raw_pdf(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
            3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Resources "
            + resources
            + b" /Contents 4 0 R >>",
            4: _stream_object(b"<< >>", b"/CS0 cs 1 0 0 scn 0 0 10 10 re f"),
            5: _stream_object(b"<< /N 3 >>", b""),
        }
    )
    with Document(io.BytesIO(data)) as document:
        elements = _absorb(document.pages[0])

    assert elements[0].fill_color == pytest.approx((1.0, 0.0, 0.0), abs=0.01)


def test_an_indexed_colour_is_looked_up_in_its_palette() -> None:
    palette = b"\xff\x00\x00\x00\x00\xff"  # entry 0 red, entry 1 blue
    resources = b"<< /ColorSpace << /CS0 [/Indexed /DeviceRGB 1 5 0 R] >> >>"
    data = _raw_pdf(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
            3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Resources "
            + resources
            + b" /Contents 4 0 R >>",
            4: _stream_object(b"<< >>", b"/CS0 cs 1 scn 0 0 10 10 re f"),
            5: _stream_object(b"<< >>", palette),
        }
    )
    with Document(io.BytesIO(data)) as document:
        elements = _absorb(document.pages[0])

    assert elements[0].fill_color == pytest.approx((0.0, 0.0, 1.0), abs=0.01)


def test_the_renderer_paints_an_indexed_fill_with_its_palette_colour() -> None:
    """The same gap, seen through the renderer the absorber borrows metrics from.

    ``/Indexed`` is not a shading space, so the shared converter had no branch
    for it and read the palette *index* as a grey level -- index 1 came out
    white instead of the colour it names.
    """
    palette = b"\xff\x00\x00\x00\x00\xff"  # entry 0 red, entry 1 blue
    data = _raw_pdf(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
            3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 40 40] /Resources "
            b"<< /ColorSpace << /CS0 [/Indexed /DeviceRGB 1 5 0 R] >> >> "
            b"/Contents 4 0 R >>",
            4: _stream_object(b"<< >>", b"/CS0 cs 1 scn 0 0 40 40 re f"),
            5: _stream_object(b"<< >>", palette),
        }
    )
    with Document(io.BytesIO(data)) as document:
        raster = document.pages[0].render(antialias=False)

    assert raster.get_pixel(20, 20) == (0, 0, 255)


# ---------------------------------------------------------------------------
# Everything else that puts marks on the page
# ---------------------------------------------------------------------------
def test_an_sh_shading_covers_the_clip_it_paints() -> None:
    """``sh`` fills the current clip region; that is its extent."""
    shading = (
        b"<< /ShadingType 2 /ColorSpace /DeviceRGB /Coords [0 0 100 0] "
        b"/Function << /FunctionType 2 /Domain [0 1] /C0 [1 0 0] /C1 [0 0 1] "
        b"/N 1 >> >>"
    )
    data = _raw_pdf(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
            3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Resources "
            b"<< /Shading << /Sh0 5 0 R >> >> /Contents 4 0 R >>",
            4: _stream_object(b"<< >>", b"q 20 30 100 40 re W n /Sh0 sh Q"),
            5: shading,
        }
    )
    with Document(io.BytesIO(data)) as document:
        elements = _absorb(document.pages[0])

    shading_elements = [e for e in elements if e.kind == "shading"]
    assert len(shading_elements) == 1
    assert shading_elements[0].resource_name == "Sh0"
    assert _rect(shading_elements[0]) == (20.0, 30.0, 100.0, 40.0)


def test_an_sh_without_a_clip_covers_the_page() -> None:
    shading = (
        b"<< /ShadingType 2 /ColorSpace /DeviceRGB /Coords [0 0 100 0] "
        b"/Function << /FunctionType 2 /Domain [0 1] /C0 [1 0 0] /C1 [0 0 1] "
        b"/N 1 >> >>"
    )
    data = _raw_pdf(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
            3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Resources "
            b"<< /Shading << /Sh0 5 0 R >> >> /Contents 4 0 R >>",
            4: _stream_object(b"<< >>", b"/Sh0 sh"),
            5: shading,
        }
    )
    with Document(io.BytesIO(data)) as document:
        elements = _absorb(document.pages[0])

    assert _rect(elements[0]) == (0.0, 0.0, 300.0, 300.0)


def test_an_inline_image_is_collected_like_a_placed_one() -> None:
    """``BI`` … ``EI`` puts an image on the page without any XObject."""
    content = (
        b"q 60 0 0 40 20 30 cm BI /W 2 /H 2 /CS /G /BPC 8 ID \x00\xff\x80\x40 EI Q"
    )
    with Document(io.BytesIO(_page_with_content(content))) as document:
        elements = _absorb(document.pages[0])

    images = [e for e in elements if e.kind == "image"]
    assert len(images) == 1
    assert images[0].resource_name is None
    assert _rect(images[0]) == (20.0, 30.0, 60.0, 40.0)


def test_invisible_text_puts_nothing_on_the_page() -> None:
    """Rendering mode 3 shows nothing, so there is no mark to report."""
    content = b"BT /F1 12 Tf 3 Tr 10 10 Td (hidden) Tj ET"
    with Document(io.BytesIO(_page_with_content(content))) as document:
        elements = _absorb(document.pages[0])

    assert elements == []


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
    # draw_rectangle strokes by default, so the box includes the stroke width.
    assert _rect(elements[2])[0] == 29.5


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

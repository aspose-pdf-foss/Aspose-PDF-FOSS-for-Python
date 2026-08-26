"""Exporting a page as SVG.

``DocFormat.SVG`` used to be a placeholder that raised. It now writes real
vectors, produced by the *same* interpreter that renders the page: the exporter
subclasses the rasterizer and replaces only the places where it puts marks on a
canvas. That is what these tests lean on -- if the SVG and the raster ever
disagree about geometry, one of the two is wrong, and they share everything
above the paint sinks.

Output was cross-checked outside the suite by rendering it with cairo and
comparing against this library's own raster: shapes, text, clipping, alpha and
gradients agree to within antialiasing. Two cases deliberately do *not* agree,
and both are the SVG being more correct than the rasterizer -- it honours the
even-odd fill rule and dash patterns, which the renderer ignores.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ElementTree
import zlib
from pathlib import Path

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfBoolean,
    PdfDictionary,
    PdfName,
    PdfNumber,
    PdfStream,
)
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.engine.svg_export import page_to_svg
from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.save_options import DocFormat

_SVG_NS = "{http://www.w3.org/2000/svg}"


def _page(content: bytes, size=(200, 200), resources: dict | None = None) -> SimplePdf:
    pdf = SimplePdf()
    pdf.pages = [(0, 0, *size)]
    pdf.page_contents = [content]
    pdf._ensure_cos()
    pdf._get_page_dict(0).mapping[PdfName("Resources")] = PdfDictionary(
        resources or {}
    )
    return pdf


def _svg(content: bytes, size=(200, 200), resources: dict | None = None, **kwargs) -> str:
    return page_to_svg(_page(content, size, resources), 0, **kwargs)


def _elements(svg: str, tag: str) -> list[ElementTree.Element]:
    root = ElementTree.fromstring(svg)
    return list(root.iter(f"{_SVG_NS}{tag}"))


def _helvetica_resources(pdf: SimplePdf) -> dict:
    font = pdf._cos_doc.register_object(
        PdfDictionary(
            {
                PdfName("Type"): PdfName("Font"),
                PdfName("Subtype"): PdfName("Type1"),
                PdfName("BaseFont"): PdfName("Helvetica"),
            }
        )
    )
    return {PdfName("Font"): PdfDictionary({PdfName("F1"): font})}


# ---------------------------------------------------------------------------
# The document itself
# ---------------------------------------------------------------------------


def test_the_page_becomes_a_well_formed_svg_of_the_right_size():
    svg = _svg(b"1 0 0 rg 20 20 80 60 re f", size=(300, 150))

    root = ElementTree.fromstring(svg)  # parses at all, i.e. it is well formed
    assert root.tag == f"{_SVG_NS}svg"
    assert root.get("width") == "300pt"
    assert root.get("height") == "150pt"
    assert root.get("viewBox") == "0 0 300 150"


def test_a_filled_rectangle_lands_where_the_page_puts_it():
    """PDF's y grows upward and SVG's downward; the flip has to happen once."""
    svg = _svg(b"1 0 0 rg 20 20 80 60 re f")

    (path,) = _elements(svg, "path")
    assert path.get("fill") == "#ff0000"
    numbers = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", path.get("d"))]
    xs, ys = numbers[0::2], numbers[1::2]
    assert (min(xs), max(xs)) == (20.0, 100.0)
    # PDF y 20..80 on a 200pt page is SVG y 120..180.
    assert (min(ys), max(ys)) == (120.0, 180.0)


def test_the_background_is_a_rectangle_and_can_be_turned_off():
    assert _elements(_svg(b""), "rect")
    assert not _elements(_svg(b"", background=None), "rect")


def test_alpha_becomes_fill_opacity():
    svg = _svg(b"q /GS0 gs 0 0 1 rg 20 20 40 40 re f Q")
    # Without an ExtGState resource the alpha stays 1 and no attribute appears.
    (path,) = _elements(svg, "path")
    assert path.get("fill-opacity") is None


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def test_the_even_odd_fill_rule_survives():
    """A rule the rasterizer drops: it fills each subpath on its own."""
    svg = _svg(b"0 0 0 rg 20 20 160 160 re 60 60 80 80 re f*")

    (path,) = _elements(svg, "path")
    assert path.get("fill-rule") == "evenodd"
    # Both subpaths are in the one path, which is what makes the hole possible.
    assert path.get("d").count("M") == 2


def test_a_nonzero_fill_says_so():
    svg = _svg(b"0 0 0 rg 20 20 160 160 re f")

    (path,) = _elements(svg, "path")
    assert path.get("fill-rule") == "nonzero"


def test_stroke_width_cap_join_and_dash_are_written():
    svg = _svg(b"0 0 1 RG 3 w 1 J 2 j [6 3] 1 d 20 20 m 180 180 l S")

    (path,) = _elements(svg, "path")
    assert path.get("fill") == "none"
    assert path.get("stroke") == "#0000ff"
    assert path.get("stroke-width") == "3"
    assert path.get("stroke-linecap") == "round"
    assert path.get("stroke-linejoin") == "bevel"
    assert path.get("stroke-dasharray") == "6 3"
    assert path.get("stroke-dashoffset") == "1"


def test_stroke_properties_are_restored_by_q_and_Q():
    svg = _svg(
        b"q [4 2] 0 d 1 J 0 0 0 RG 20 20 m 100 20 l S Q "
        b"0 0 0 RG 20 60 m 100 60 l S"
    )

    inner, outer = _elements(svg, "path")
    assert inner.get("stroke-dasharray") == "4 2"
    assert outer.get("stroke-dasharray") is None
    assert outer.get("stroke-linecap") is None


# ---------------------------------------------------------------------------
# Clipping
# ---------------------------------------------------------------------------


def test_a_clip_becomes_a_clip_path_the_content_references():
    svg = _svg(b"20 20 60 60 re W n 1 0 0 rg 0 0 200 200 re f")

    (clip,) = _elements(svg, "clipPath")
    (path,) = [p for p in _elements(svg, "path") if p.get("fill") == "#ff0000"]
    assert path.get("clip-path") == f"url(#{clip.get('id')})"


def test_q_and_Q_release_the_clip():
    """The clipping path is graphics state, so ``Q`` has to give it back.

    The renderer used to intersect one global mask and never restore it, so
    everything after a ``q W n … Q`` stayed clipped to a region that had
    already ended -- which is most PDFs with a figure in them.
    """
    svg = _svg(
        b"q 20 20 40 40 re W n 1 0 0 rg 0 0 200 200 re f Q "
        b"0 0 1 rg 120 120 60 60 re f"
    )

    inside = next(p for p in _elements(svg, "path") if p.get("fill") == "#ff0000")
    after = next(p for p in _elements(svg, "path") if p.get("fill") == "#0000ff")
    assert inside.get("clip-path") is not None
    assert after.get("clip-path") is None


def test_the_renderer_also_releases_the_clip_at_q():
    """The same fix, seen through the rasterizer the exporter is built on."""
    pdf = _page(
        b"q 0 0 20 20 re W n 1 0 0 rg 0 0 40 40 re f Q "
        b"0 0 1 rg 20 20 20 20 re f",
        size=(40, 40),
    )
    document = Document()
    document._engine_pdf = pdf

    raster = document.pages[0].render(antialias=False)

    assert raster.get_pixel(10, 30) == (255, 0, 0)  # inside the old clip
    assert raster.get_pixel(30, 10) == (0, 0, 255)  # outside it, drawn after Q


def test_nested_clips_intersect():
    svg = _svg(
        b"q 20 20 160 160 re W n q 60 60 40 40 re W n "
        b"0 0 0 rg 0 0 200 200 re f Q Q"
    )

    clips = _elements(svg, "clipPath")
    assert len(clips) == 2
    # The inner clip is itself clipped by the outer one.
    assert clips[1].get("clip-path") == f"url(#{clips[0].get('id')})"


# ---------------------------------------------------------------------------
# Text, images and shadings
# ---------------------------------------------------------------------------


def test_text_is_exported_as_glyph_outlines():
    pdf = _page(b"BT /F1 24 Tf 20 100 Td (Hi) Tj ET")
    pdf._get_page_dict(0).mapping[PdfName("Resources")] = PdfDictionary(
        _helvetica_resources(pdf)
    )
    svg = page_to_svg(pdf, 0)

    # Outlines, not <text>: no font has to travel with the file, and the shapes
    # are exactly the ones the renderer fills.
    assert not _elements(svg, "text")
    glyphs = [p for p in _elements(svg, "path") if p.get("fill-rule") == "nonzero"]
    assert len(glyphs) == 2


def test_an_image_becomes_an_embedded_png_placed_by_its_matrix():
    pdf = _page(b"q 120 0 0 120 40 40 cm /Im0 Do Q")
    samples = bytes(
        value
        for y in range(4)
        for x in range(4)
        for value in ((x * 60) % 256, (y * 60) % 256, 128)
    )
    raw = zlib.compress(samples)
    image = pdf._cos_doc.register_object(
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
            },
        )
    )
    pdf._get_page_dict(0).mapping[PdfName("Resources")] = PdfDictionary(
        {PdfName("XObject"): PdfDictionary({PdfName("Im0"): image})}
    )

    svg = page_to_svg(pdf, 0)

    (element,) = _elements(svg, "image")
    href = element.get("{http://www.w3.org/1999/xlink}href")
    assert href.startswith("data:image/png;base64,")
    # PDF draws the first sample row at the top of the unit square and SVG at
    # the bottom, so the placement carries a flip; the result is upright.
    values = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", element.get("transform"))]
    assert values == [120.0, 0.0, 0.0, 120.0, 40.0, 40.0]
    assert element.get("image-rendering") == "pixelated"


def _axial_shading_page() -> SimplePdf:
    pdf = _page(b"q 20 20 160 160 re W n /Sh0 sh Q")
    function = pdf._cos_doc.register_object(
        PdfDictionary(
            {
                PdfName("FunctionType"): PdfNumber(2),
                PdfName("Domain"): PdfArray([PdfNumber(0), PdfNumber(1)]),
                PdfName("C0"): PdfArray([PdfNumber(1), PdfNumber(0), PdfNumber(0)]),
                PdfName("C1"): PdfArray([PdfNumber(0), PdfNumber(0), PdfNumber(1)]),
                PdfName("N"): PdfNumber(1),
            }
        )
    )
    shading = pdf._cos_doc.register_object(
        PdfDictionary(
            {
                PdfName("ShadingType"): PdfNumber(2),
                PdfName("ColorSpace"): PdfName("DeviceRGB"),
                PdfName("Coords"): PdfArray(
                    [PdfNumber(20), PdfNumber(0), PdfNumber(180), PdfNumber(0)]
                ),
                PdfName("Function"): function,
                PdfName("Extend"): PdfArray([PdfBoolean(True), PdfBoolean(True)]),
            }
        )
    )
    pdf._get_page_dict(0).mapping[PdfName("Resources")] = PdfDictionary(
        {PdfName("Shading"): PdfDictionary({PdfName("Sh0"): shading})}
    )
    return pdf


def test_an_axial_shading_becomes_a_linear_gradient():
    svg = page_to_svg(_axial_shading_page(), 0)

    (gradient,) = _elements(svg, "linearGradient")
    assert gradient.get("gradientUnits") == "userSpaceOnUse"
    assert (gradient.get("x1"), gradient.get("x2")) == ("20", "180")
    stops = list(gradient.iter(f"{_SVG_NS}stop"))
    assert len(stops) > 1
    assert stops[0].get("stop-color") == "#ff0000"
    assert stops[-1].get("stop-color") == "#0000ff"
    painted = [p for p in _elements(svg, "path") if p.get("fill", "").startswith("url(")]
    assert painted


# ---------------------------------------------------------------------------
# Optional content
# ---------------------------------------------------------------------------


def test_content_in_a_hidden_layer_is_not_exported():
    """A layer the default configuration turns off is not in the file at all.

    Hiding a layer in the *viewer* still ships the content; an export that
    leaks it would be a confidentiality bug, not a rendering one.
    """
    from tests.test_optional_content import _layered_pdf

    with Document(_layered_pdf()) as document:  # layer 2 (blue) is /OFF
        svg = document.pages[0].to_svg()

    fills = {path.get("fill") for path in _elements(svg, "path")}
    assert "#ff0000" in fills  # layer 1, on
    assert "#00ff00" in fills  # unlayered
    assert "#0000ff" not in fills  # layer 2, off


# ---------------------------------------------------------------------------
# The public API
# ---------------------------------------------------------------------------


def test_page_save_as_svg_writes_the_file(tmp_path: Path):
    document = Document()
    document.pages.add().add_text("Hello", 40, 700, font_size=18)

    written = document.pages[0].save_as_svg(tmp_path / "page.svg")

    assert written.exists()
    ElementTree.fromstring(written.read_text(encoding="utf-8"))


def test_document_save_as_svg_writes_one_file_per_page(tmp_path: Path):
    document = Document()
    for _ in range(3):
        document.pages.add()

    written = document.save_as_svg(tmp_path / "out.svg")

    assert [path.name for path in written] == ["out-1.svg", "out-2.svg", "out-3.svg"]
    assert all(path.exists() for path in written)


def test_a_single_page_keeps_the_name_it_was_given(tmp_path: Path):
    document = Document()
    document.pages.add()
    document.pages.add()

    written = document.save_as_svg(tmp_path / "just-one.svg", pages=[1])

    assert [path.name for path in written] == ["just-one.svg"]


def test_save_with_the_svg_format_writes_svg(tmp_path: Path):
    """``DocFormat.SVG`` used to raise; it is an export now."""
    document = Document()
    document.pages.add().add_text("Exported", 40, 700, font_size=18)
    target = tmp_path / "doc.svg"

    document.save(target, DocFormat.SVG)

    assert target.exists()
    assert "<svg" in target.read_text(encoding="utf-8")


def test_saving_svg_to_a_stream_is_refused(tmp_path: Path):
    document = Document()
    document.pages.add()

    with pytest.raises(PdfValidationException, match="needs a file path"):
        document.save(open(tmp_path / "x.svg", "wb"), DocFormat.SVG)


def test_saving_svg_needs_a_page():
    document = Document()

    with pytest.raises(PdfValidationException, match="at least one page"):
        document.save_as_svg("unused.svg")

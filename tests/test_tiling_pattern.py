"""Tests for tiling pattern (PatternType 1) fills in the page renderer."""

from aspose_pdf import Document
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfDictionary,
    PdfName,
    PdfNumber,
    PdfStream,
)
from aspose_pdf.engine.simple_pdf import SimplePdf


def _arr(*xs):
    return PdfArray([PdfNumber(x) for x in xs])


def _tiling_pattern(cell_content, *, paint_type=1, bbox=(0, 0, 10, 10), step=10):
    return PdfStream(
        cell_content,
        {
            PdfName("Type"): PdfName("Pattern"),
            PdfName("PatternType"): PdfNumber(1),
            PdfName("PaintType"): PdfNumber(paint_type),
            PdfName("TilingType"): PdfNumber(1),
            PdfName("BBox"): _arr(*bbox),
            PdfName("XStep"): PdfNumber(step),
            PdfName("YStep"): PdfNumber(step),
            PdfName("Resources"): PdfDictionary({}),
            PdfName("Matrix"): _arr(1, 0, 0, 1, 0, 0),
        },
    )


def _doc_with_pattern(page_content, pattern):
    pdf = SimplePdf(pages=[(0, 0, 30, 30)], page_contents=[page_content])
    pdf._ensure_cos()
    ref = pdf._cos_doc.register_object(pattern)
    pdf._get_page_dict(0).mapping[PdfName("Resources")] = PdfDictionary(
        {PdfName("Pattern"): PdfDictionary({PdfName("P0"): ref})}
    )
    doc = Document()
    doc._engine_pdf = pdf
    return doc


def test_set_fill_pattern_marks_tiling_state():
    # A PatternType 1 pattern is stored as a tiling fill on the graphics state.
    from aspose_pdf.engine.rasterizer import _PageRasterizer

    doc = _doc_with_pattern(
        b"/Pattern cs /P0 scn 0 0 30 30 re f",
        _tiling_pattern(b"1 0 0 rg 0 0 5 5 re f"),
    )
    pdf = doc._engine_pdf
    rasterizer = _PageRasterizer(
        pdf, 0, dpi=72.0, scale=1.0, background=(255, 255, 255), antialias=1
    )
    patterns = rasterizer._resource_dict(rasterizer.resources_cos, "Pattern")
    rasterizer._set_fill_pattern("P0", rasterizer.resources_cos, [])
    assert rasterizer.state.fill_tiling is not None
    assert rasterizer.state.fill_shading is None
    assert patterns is not None


def test_render_colored_tiling_pattern_repeats():
    # Each 10x10 cell paints a red 5x5 block, leaving a white gap; the block must
    # repeat across the filled area.
    doc = _doc_with_pattern(
        b"/Pattern cs /P0 scn 0 0 30 30 re f",
        _tiling_pattern(b"1 0 0 rg 0 0 5 5 re f"),
    )

    raster = doc.pages[0].render(antialias=False)

    assert raster.get_pixel(2, 27) == (255, 0, 0)  # red block, tile (0,0)
    assert raster.get_pixel(7, 27) == (255, 255, 255)  # white gap inside tile (0,0)
    assert raster.get_pixel(12, 27) == (255, 0, 0)  # red block, tile (1,0): repeated
    assert raster.get_pixel(2, 17) == (255, 0, 0)  # red block, tile (0,1): repeated


def test_render_uncolored_tiling_pattern_uses_scn_color():
    # PaintType 2: the cell draws no colour; scn supplies it (blue here).
    doc = _doc_with_pattern(
        b"/Pattern cs 0 0 1 /P0 scn 0 0 30 30 re f",
        _tiling_pattern(b"0 0 5 5 re f", paint_type=2),
    )

    raster = doc.pages[0].render(antialias=False)

    assert raster.get_pixel(2, 27) == (0, 0, 255)  # block painted in the scn colour
    assert raster.get_pixel(7, 27) == (255, 255, 255)


def test_tiling_pattern_respects_fill_path():
    # Filling only the left half must leave the right half untouched.
    doc = _doc_with_pattern(
        b"/Pattern cs /P0 scn 0 0 15 30 re f",
        _tiling_pattern(b"1 0 0 rg 0 0 5 5 re f"),
    )

    raster = doc.pages[0].render(antialias=False)

    assert raster.get_pixel(2, 27) == (255, 0, 0)  # inside the fill: patterned
    assert raster.get_pixel(20, 15) == (255, 255, 255)  # outside the fill: untouched


def test_unknown_pattern_falls_back_to_solid():
    # A missing pattern resource fills with a neutral grey rather than crashing.
    doc = _doc_with_pattern(
        b"/Pattern cs /Missing scn 0 0 30 30 re f",
        _tiling_pattern(b"1 0 0 rg 0 0 5 5 re f"),
    )

    raster = doc.pages[0].render(antialias=False)

    assert raster.get_pixel(15, 15) == (128, 128, 128)

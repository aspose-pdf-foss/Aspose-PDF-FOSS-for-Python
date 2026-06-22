import struct
import zlib
from pathlib import Path

import pytest

from aspose_pdf import Document, RasterizedPage
from aspose_pdf.engine.cos import PdfArray, PdfDictionary, PdfName
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.exceptions import PdfValidationException


def _png_pixels(data: bytes):
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    pos = 8
    width = height = None
    compressed = bytearray()
    while pos < len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        tag = data[pos + 4 : pos + 8]
        payload = data[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if tag == b"IHDR":
            width, height, bit_depth, color_type, *_ = struct.unpack(
                ">IIBBBBB", payload
            )
            assert bit_depth == 8
            assert color_type == 2
        elif tag == b"IDAT":
            compressed.extend(payload)
        elif tag == b"IEND":
            break
    raw = zlib.decompress(bytes(compressed))
    stride = width * 3
    pixels = bytearray()
    for y in range(height):
        row = raw[y * (stride + 1) : (y + 1) * (stride + 1)]
        assert row[0] == 0
        pixels.extend(row[1:])
    return width, height, bytes(pixels)


def test_page_render_fills_and_text_marks_pixels() -> None:
    doc = Document()
    doc._engine_pdf = SimplePdf(
        pages=[(0.0, 0.0, 40.0, 30.0)],
        page_contents=[
            b"0.1 0.2 0.7 rg 5 5 10 8 re f "
            b"0 g BT /F1 8 Tf 1 0 0 1 22 10 Tm (Hi) Tj ET"
        ],
    )

    raster = doc.pages[0].render()

    assert isinstance(raster, RasterizedPage)
    assert (raster.width, raster.height) == (40, 30)
    assert raster.get_pixel(8, 18) == (26, 51, 178)
    assert raster.get_pixel(24, 17) == (0, 0, 0)
    assert raster.get_pixel(0, 0) == (255, 255, 255)


def test_document_render_page_writes_png_and_tiff(tmp_path: Path) -> None:
    doc = Document()
    doc._engine_pdf = SimplePdf(
        pages=[(0.0, 0.0, 10.0, 10.0)],
        page_contents=[b"1 0 0 rg 0 0 10 10 re f"],
    )

    png_path = doc.save_page_as_image(0, tmp_path / "page.png")
    tif_path = doc.pages[0].save_as_image(tmp_path / "page.tiff")

    assert png_path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert tif_path.read_bytes()[:4] == b"II*\x00"
    assert _png_pixels(doc.render_page(0).to_png())[2][:3] == b"\xff\x00\x00"


def _partial_pixels(raster: RasterizedPage):
    """Pixels that are neither pure black nor pure white (i.e. anti-aliased)."""
    out = []
    for y in range(raster.height):
        for x in range(raster.width):
            r, g, b = raster.get_pixel(x, y)
            if r == g == b and 0 < r < 255:
                out.append((x, y))
    return out


def test_antialias_smooths_edges_with_partial_coverage() -> None:
    # A triangle has a diagonal edge that must produce partial-coverage greys
    # when anti-aliased, and none when rendered hard-edged.
    doc = Document()
    doc._engine_pdf = SimplePdf(
        pages=[(0.0, 0.0, 20.0, 20.0)],
        page_contents=[b"0 0 0 rg 0 0 m 20 0 l 0 20 l h f"],
    )

    smooth = doc.pages[0].render(antialias=3)
    hard = doc.pages[0].render(antialias=False)

    assert (smooth.width, smooth.height) == (hard.width, hard.height) == (20, 20)
    assert len(_partial_pixels(smooth)) > 0  # AA blends the diagonal
    assert _partial_pixels(hard) == []  # hard edges are pure black/white


def test_antialias_preserves_target_dimensions() -> None:
    doc = Document()
    doc._engine_pdf = SimplePdf(
        pages=[(0.0, 0.0, 15.0, 11.0)],
        page_contents=[b"0 0 0 rg 0 0 15 11 re f"],
    )
    for antialias in (False, True, 1, 2, 4):
        raster = doc.pages[0].render(antialias=antialias)
        assert (raster.width, raster.height) == (15, 11)
        # A full-page fill stays solid black regardless of supersampling.
        assert raster.get_pixel(7, 5) == (0, 0, 0)


def test_antialias_factor_out_of_range_rejected() -> None:
    doc = Document()
    doc._engine_pdf = SimplePdf(
        pages=[(0.0, 0.0, 10.0, 10.0)], page_contents=[b""]
    )
    with pytest.raises(PdfValidationException):
        doc.pages[0].render(antialias=9)


def test_extgstate_multiply_blend_mode_affects_fill() -> None:
    doc = Document()
    doc._engine_pdf = SimplePdf(
        pages=[(0.0, 0.0, 10.0, 10.0)],
        page_contents=[
            b"1 0 0 rg 0 0 10 10 re f "
            b"/Blend gs 0 0 1 rg 0 0 10 10 re f"
        ],
        extgstates={"Blend": {"BM": "Multiply"}},
    )

    raster = doc.render_page(0, antialias=False)

    assert raster.get_pixel(5, 5) == (0, 0, 0)


def test_extgstate_blend_mode_combines_with_constant_alpha() -> None:
    doc = Document()
    doc._engine_pdf = SimplePdf(
        pages=[(0.0, 0.0, 10.0, 10.0)],
        page_contents=[
            b"1 0 0 rg 0 0 10 10 re f "
            b"/Blend gs 0 0 1 rg 0 0 10 10 re f"
        ],
        extgstates={"Blend": {"BM": "Multiply", "ca": 0.5}},
    )

    raster = doc.render_page(0, antialias=False)

    assert raster.get_pixel(5, 5) == (128, 0, 0)


def test_extgstate_blend_mode_array_uses_first_supported_cos_name() -> None:
    doc = Document()
    doc._engine_pdf = SimplePdf(
        pages=[(0.0, 0.0, 10.0, 10.0)],
        page_contents=[
            b"1 0 0 rg 0 0 10 10 re f "
            b"/Blend gs 0 0 1 rg 0 0 10 10 re f"
        ],
        extgstates={
            "Blend": PdfDictionary(
                {PdfName("BM"): PdfArray([PdfName("Hue"), PdfName("Screen")])}
            )
        },
    )

    raster = doc.render_page(0, antialias=False)

    assert raster.get_pixel(5, 5) == (255, 0, 255)


def test_extgstate_unsupported_single_blend_mode_falls_back_to_normal() -> None:
    doc = Document()
    doc._engine_pdf = SimplePdf(
        pages=[(0.0, 0.0, 10.0, 10.0)],
        page_contents=[
            b"1 0 0 rg 0 0 10 10 re f "
            b"/Multiply gs 0 0 1 rg 0 0 10 10 re f "
            b"/Unsupported gs 0 1 0 rg 0 0 10 10 re f"
        ],
        extgstates={
            "Multiply": {"BM": "Multiply"},
            "Unsupported": {"BM": "Hue"},
        },
    )

    raster = doc.render_page(0, antialias=False)

    assert raster.get_pixel(5, 5) == (0, 255, 0)


def test_page_render_paints_raw_rgb_image_xobject() -> None:
    doc = Document()
    doc._engine_pdf = SimplePdf(
        pages=[(0.0, 0.0, 4.0, 4.0)],
        page_contents=[b"q 2 0 0 2 1 1 cm /Im0 Do Q"],
        images={"Im0": b"\xff\x00\x00\x00\xff\x00\x00\x00\xff\xff\xff\x00"},
    )
    doc._engine_pdf._image_sizes = {"Im0": (2, 2)}
    doc._engine_pdf._image_meta = {
        "Im0": {
            "width": 2,
            "height": 2,
            "bpc": 8,
            "cs_kind": "rgb",
            "n_comps": 3,
        }
    }

    raster = doc.render_page(0)

    assert raster.get_pixel(1, 1) == (255, 0, 0)
    assert raster.get_pixel(2, 1) == (0, 255, 0)
    assert raster.get_pixel(1, 2) == (0, 0, 255)
    assert raster.get_pixel(2, 2) == (255, 255, 0)

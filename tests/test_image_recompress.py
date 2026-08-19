"""Integration tests: ``optimize()`` applying image_compression_quality /
image_max_dimension to embedded image XObjects."""

from __future__ import annotations

import zlib

from aspose_pdf.engine import dct
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfBoolean,
    PdfDictionary,
    PdfName,
    PdfNumber,
    PdfStream,
)
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.optimization import OptimizationOptions

_W, _H = 80, 60


def _rgb_gradient(w=_W, h=_H) -> bytes:
    s = bytearray(w * h * 3)
    for y in range(h):
        for x in range(w):
            i = (y * w + x) * 3
            s[i] = (x * 255) // (w - 1)
            s[i + 1] = (y * 255) // (h - 1)
            s[i + 2] = 128
    return bytes(s)


def _pdf_with_image(extra: dict, content: bytes, *, w=_W, h=_H):
    """One-page PDF with an image XObject referenced from the page (so it is not
    pruned as unused)."""
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 200, 200)]
    pdf.page_contents = [b"q /Im0 Do Q"]
    pdf._ensure_cos()
    cos = pdf._cos_doc
    mapping = {
        PdfName("Subtype"): PdfName("Image"),
        PdfName("Width"): PdfNumber(w),
        PdfName("Height"): PdfNumber(h),
        PdfName("BitsPerComponent"): PdfNumber(8),
    }
    mapping.update(extra)
    mapping[PdfName("Length")] = PdfNumber(len(content))
    img = cos.register_object(PdfStream(content, mapping))
    page = pdf._get_page_dict(0)
    page.mapping[PdfName("Resources")] = PdfDictionary(
        {PdfName("XObject"): PdfDictionary({PdfName("Im0"): img})}
    )
    return pdf, img.object_number


def _flate_rgb(w=_W, h=_H) -> bytes:
    return zlib.compress(_rgb_gradient(w, h), 9)


def _opts(**kw):
    base = dict(
        remove_unused_objects=False,
        remove_unused_streams=False,
        remove_duplicate_images=False,
        link_duplicate_streams=False,
        use_object_streams=False,
    )
    base.update(kw)
    return OptimizationOptions(**base)


def test_quality_recompresses_flate_rgb_to_jpeg():
    extra = {
        PdfName("ColorSpace"): PdfName("DeviceRGB"),
        PdfName("Filter"): PdfName("FlateDecode"),
    }
    pdf, num = _pdf_with_image(extra, _flate_rgb())
    before = len(pdf._cos_doc.objects[num].content)

    pdf.optimize(_opts(image_compression_quality=60))

    img = pdf._cos_doc.objects[num]
    assert img.mapping[PdfName("Filter")] == PdfName("DCTDecode")
    assert len(img.content) < before
    decoded = dct.decode(img.content)
    assert decoded is not None
    assert (decoded.width, decoded.height, decoded.components) == (_W, _H, 3)


def _gray_gradient(w=_W, h=_H) -> bytes:
    # A 2D gradient (varies along both axes) so it is not as trivially
    # Flate-compressible as a 1D ramp — recompression then actually wins.
    return bytes(
        ((x * 255) // (w - 1) + (y * 255) // (h - 1)) // 2
        for y in range(h)
        for x in range(w)
    )


def test_grayscale_recompresses_to_jpeg():
    gray = zlib.compress(_gray_gradient(), 9)
    extra = {
        PdfName("ColorSpace"): PdfName("DeviceGray"),
        PdfName("Filter"): PdfName("FlateDecode"),
    }
    pdf, num = _pdf_with_image(extra, gray)
    pdf.optimize(_opts(image_compression_quality=70))
    img = pdf._cos_doc.objects[num]
    assert img.mapping[PdfName("Filter")] == PdfName("DCTDecode")
    assert dct.decode(img.content).components == 1


def test_max_dimension_downscales_losslessly():
    extra = {
        PdfName("ColorSpace"): PdfName("DeviceRGB"),
        PdfName("Filter"): PdfName("FlateDecode"),
    }
    pdf, num = _pdf_with_image(extra, _flate_rgb())
    pdf.optimize(_opts(image_max_dimension=40))
    img = pdf._cos_doc.objects[num]
    # Resize-only keeps the lossless codec; only the pixel dimensions shrink.
    assert img.mapping[PdfName("Filter")] == PdfName("FlateDecode")
    assert int(img.mapping[PdfName("Width")].value) == 40
    assert int(img.mapping[PdfName("Height")].value) == 30


def test_quality_plus_max_dimension():
    extra = {
        PdfName("ColorSpace"): PdfName("DeviceRGB"),
        PdfName("Filter"): PdfName("FlateDecode"),
    }
    pdf, num = _pdf_with_image(extra, _flate_rgb())
    pdf.optimize(_opts(image_compression_quality=50, image_max_dimension=40))
    img = pdf._cos_doc.objects[num]
    assert img.mapping[PdfName("Filter")] == PdfName("DCTDecode")
    assert int(img.mapping[PdfName("Width")].value) == 40
    decoded = dct.decode(img.content)
    assert (decoded.width, decoded.height) == (40, 30)


def test_recompress_already_jpeg_at_lower_quality():
    from aspose_pdf.engine import jpeg_encoder

    jpg = jpeg_encoder.encode(_W, _H, 3, _rgb_gradient(), quality=95)
    extra = {
        PdfName("ColorSpace"): PdfName("DeviceRGB"),
        PdfName("Filter"): PdfName("DCTDecode"),
    }
    pdf, num = _pdf_with_image(extra, jpg)
    pdf.optimize(_opts(image_compression_quality=40))
    img = pdf._cos_doc.objects[num]
    assert img.mapping[PdfName("Filter")] == PdfName("DCTDecode")
    assert len(img.content) < len(jpg)  # lower quality -> smaller


def test_tiny_flat_image_left_alone():
    # JPEG overhead exceeds a 4x4 flat image's Flate size -> not rewritten.
    flat = zlib.compress(bytes([200] * (4 * 4 * 3)), 9)
    extra = {
        PdfName("ColorSpace"): PdfName("DeviceRGB"),
        PdfName("Filter"): PdfName("FlateDecode"),
    }
    pdf, num = _pdf_with_image(extra, flat, w=4, h=4)
    before = pdf._cos_doc.objects[num].content
    pdf.optimize(_opts(image_compression_quality=40))
    assert pdf._cos_doc.objects[num].content == before


def test_cmyk_image_untouched():
    extra = {
        PdfName("ColorSpace"): PdfName("DeviceCMYK"),
        PdfName("Filter"): PdfName("FlateDecode"),
    }
    content = zlib.compress(bytes([100] * (_W * _H * 4)), 9)
    pdf, num = _pdf_with_image(extra, content)
    before = pdf._cos_doc.objects[num].content
    pdf.optimize(_opts(image_compression_quality=50))
    assert pdf._cos_doc.objects[num].content == before


def test_indexed_image_untouched():
    palette = PdfStream(
        b"\xff\x00\x00\x00\xff\x00", {PdfName("Length"): PdfNumber(6)}
    )
    extra = {
        PdfName("ColorSpace"): PdfArray(
            [PdfName("Indexed"), PdfName("DeviceRGB"), PdfNumber(1), palette]
        ),
        PdfName("Filter"): PdfName("FlateDecode"),
        PdfName("BitsPerComponent"): PdfNumber(8),
    }
    content = zlib.compress(bytes([0, 1] * (_W * _H // 2)), 9)
    pdf, num = _pdf_with_image(extra, content)
    before = pdf._cos_doc.objects[num].content
    pdf.optimize(_opts(image_compression_quality=50))
    assert pdf._cos_doc.objects[num].content == before


def test_inverting_decode_array_is_folded_into_the_samples():
    """``/Decode [1 0 ...]`` only inverts, which the samples can absorb."""
    from aspose_pdf.engine import dct

    extra = {
        PdfName("ColorSpace"): PdfName("DeviceRGB"),
        PdfName("Filter"): PdfName("FlateDecode"),
        PdfName("Decode"): PdfArray([PdfNumber(1), PdfNumber(0)] * 3),
    }
    pdf, num = _pdf_with_image(extra, _flate_rgb())
    before = pdf._cos_doc.objects[num].content
    pdf.optimize(_opts(image_compression_quality=50))

    stream = pdf._cos_doc.objects[num]
    assert stream.content != before
    assert stream.mapping[PdfName("Filter")] == PdfName("DCTDecode")
    # The array is gone because the inversion now lives in the samples.
    assert PdfName("Decode") not in stream.mapping

    decoded = dct.decode(stream.content)
    original = _rgb_gradient(_W, _H)
    # Lossy, so compare the mean against the inverted source rather than bytes.
    mean_delta = sum(
        abs(a - (255 - b)) for a, b in zip(decoded.samples, original)
    ) / len(original)
    assert mean_delta < 12, mean_delta


def test_non_inverting_decode_array_is_untouched():
    """Any other sample remapping is not reproduced, so the image is kept."""
    extra = {
        PdfName("ColorSpace"): PdfName("DeviceRGB"),
        PdfName("Filter"): PdfName("FlateDecode"),
        PdfName("Decode"): PdfArray(
            [PdfNumber(0), PdfNumber(1), PdfNumber(0), PdfNumber(1),
             PdfNumber(0), PdfNumber(0.5)]
        ),
    }
    pdf, num = _pdf_with_image(extra, _flate_rgb())
    before = pdf._cos_doc.objects[num].content
    pdf.optimize(_opts(image_compression_quality=50))
    assert pdf._cos_doc.objects[num].content == before


def test_image_mask_untouched():
    extra = {
        PdfName("ImageMask"): PdfBoolean(True),
        PdfName("Filter"): PdfName("FlateDecode"),
        PdfName("BitsPerComponent"): PdfNumber(1),
    }
    content = zlib.compress(bytes([0xAA] * (_W * _H // 8)), 9)
    pdf, num = _pdf_with_image(extra, content)
    before = pdf._cos_doc.objects[num].content
    pdf.optimize(_opts(image_compression_quality=50))
    assert pdf._cos_doc.objects[num].content == before


def test_soft_mask_not_recompressed_but_base_is():
    # The base colour image carries an /SMask; the mask must stay lossless while
    # the base is recompressed to JPEG.
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 200, 200)]
    pdf.page_contents = [b"q /Im0 Do Q"]
    pdf._ensure_cos()
    cos = pdf._cos_doc

    smask_bytes = zlib.compress(bytes((x * 255) // (_W - 1) for y in range(_H)
                                       for x in range(_W)), 9)
    smask = cos.register_object(
        PdfStream(
            smask_bytes,
            {
                PdfName("Subtype"): PdfName("Image"),
                PdfName("Width"): PdfNumber(_W),
                PdfName("Height"): PdfNumber(_H),
                PdfName("BitsPerComponent"): PdfNumber(8),
                PdfName("ColorSpace"): PdfName("DeviceGray"),
                PdfName("Filter"): PdfName("FlateDecode"),
            },
        )
    )
    base = cos.register_object(
        PdfStream(
            _flate_rgb(),
            {
                PdfName("Subtype"): PdfName("Image"),
                PdfName("Width"): PdfNumber(_W),
                PdfName("Height"): PdfNumber(_H),
                PdfName("BitsPerComponent"): PdfNumber(8),
                PdfName("ColorSpace"): PdfName("DeviceRGB"),
                PdfName("Filter"): PdfName("FlateDecode"),
                PdfName("SMask"): smask,
            },
        )
    )
    page = pdf._get_page_dict(0)
    page.mapping[PdfName("Resources")] = PdfDictionary(
        {PdfName("XObject"): PdfDictionary({PdfName("Im0"): base})}
    )
    smask_before = cos.objects[smask.object_number].content

    pdf.optimize(_opts(image_compression_quality=55))

    assert cos.objects[base.object_number].mapping[PdfName("Filter")] == PdfName(
        "DCTDecode"
    )
    assert cos.objects[smask.object_number].content == smask_before  # mask intact


def test_defaults_leave_images_untouched():
    extra = {
        PdfName("ColorSpace"): PdfName("DeviceRGB"),
        PdfName("Filter"): PdfName("FlateDecode"),
    }
    pdf, num = _pdf_with_image(extra, _flate_rgb())
    before = pdf._cos_doc.objects[num].content
    pdf.optimize(_opts())  # no quality, no max_dimension
    assert pdf._cos_doc.objects[num].content == before


def _cmyk_gradient(w=_W, h=_H) -> bytes:
    s = bytearray(w * h * 4)
    for y in range(h):
        for x in range(w):
            i = (y * w + x) * 4
            s[i] = (x * 255) // (w - 1)
            s[i + 1] = (y * 255) // (h - 1)
            s[i + 2] = ((x + y) * 255) // (w + h - 2)
            s[i + 3] = 40
    return bytes(s)


def test_cmyk_recompresses_flate_to_jpeg():
    extra = {
        PdfName("ColorSpace"): PdfName("DeviceCMYK"),
        PdfName("Filter"): PdfName("FlateDecode"),
    }
    pdf, num = _pdf_with_image(extra, zlib.compress(_cmyk_gradient(), 9))
    before = len(pdf._cos_doc.objects[num].content)

    pdf.optimize(_opts(image_compression_quality=70))

    img = pdf._cos_doc.objects[num]
    assert img.mapping[PdfName("Filter")] == PdfName("DCTDecode")
    assert len(img.content) < before
    decoded = dct.decode(img.content)
    assert decoded is not None
    assert (decoded.width, decoded.height, decoded.components) == (_W, _H, 4)


# ---------------------------------------------------------------------------
# Effective-DPI downsampling (image_target_dpi)
# ---------------------------------------------------------------------------

import pytest  # noqa: E402

from aspose_pdf.engine.auto_tag import find_image_placements  # noqa: E402


def _noise_gray(w: int, h: int) -> bytes:
    # Deterministic high-entropy pattern so a downscale genuinely shrinks it.
    return bytes(((x * 73 + y * 151 + x * y * 29) % 256) for y in range(h) for x in range(w))


def _placed_gray_pdf(disp_w, disp_h, px_w, px_h, raw, *, place=True):
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 600, 600)]
    if place:
        pdf.page_contents = [f"q {disp_w} 0 0 {disp_h} 20 20 cm /Im0 Do Q".encode()]
    else:
        pdf.page_contents = [b"q Q"]
    pdf._ensure_cos()
    cos = pdf._cos_doc
    data = zlib.compress(raw, 9)
    mapping = {
        PdfName("Subtype"): PdfName("Image"),
        PdfName("Width"): PdfNumber(px_w),
        PdfName("Height"): PdfNumber(px_h),
        PdfName("BitsPerComponent"): PdfNumber(8),
        PdfName("ColorSpace"): PdfName("DeviceGray"),
        PdfName("Filter"): PdfName("FlateDecode"),
        PdfName("Length"): PdfNumber(len(data)),
    }
    img = cos.register_object(PdfStream(data, mapping))
    pdf._get_page_dict(0).mapping[PdfName("Resources")] = PdfDictionary(
        {PdfName("XObject"): PdfDictionary({PdfName("Im0"): img})}
    )
    return pdf, img.object_number


def test_find_image_placements_reports_display_size():
    placements = find_image_placements(b"q 100 0 0 80 10 10 cm /Im0 Do Q")
    assert placements == [("/Im0", 100.0, 80.0)]


def test_target_dpi_downsamples_high_dpi_image():
    # 200x150 px shown at 48x36 pt -> 300 DPI; target 96 -> ~64x48 px, lossless.
    pdf, num = _placed_gray_pdf(48, 36, 200, 150, _noise_gray(200, 150))
    pdf.optimize(_opts(image_target_dpi=96))
    img = pdf._cos_doc.objects[num]
    new_w = int(img.mapping[PdfName("Width")].value)
    assert new_w < 200 and 58 <= new_w <= 70  # ~96*48/72 = 64 px
    assert img.mapping[PdfName("Filter")] == PdfName("FlateDecode")  # stays lossless


def test_target_dpi_leaves_low_resolution_image():
    # 60x45 px shown at 60x45 pt -> 72 DPI; target 150 -> unchanged.
    pdf, num = _placed_gray_pdf(60, 45, 60, 45, _noise_gray(60, 45))
    pdf.optimize(_opts(image_target_dpi=150))
    img = pdf._cos_doc.objects[num]
    assert int(img.mapping[PdfName("Width")].value) == 60


def test_target_dpi_skips_unplaced_image():
    # No page placement -> display size (hence DPI) unknown -> left untouched.
    pdf, num = _placed_gray_pdf(48, 36, 200, 150, _noise_gray(200, 150), place=False)
    pdf.optimize(_opts(image_target_dpi=96))
    assert int(pdf._cos_doc.objects[num].mapping[PdfName("Width")].value) == 200


def test_target_dpi_plus_quality_downsamples_and_jpegs():
    pdf, num = _placed_gray_pdf(40, 30, 160, 120, _gray_gradient(160, 120))
    pdf.optimize(_opts(image_target_dpi=150, image_compression_quality=70))
    img = pdf._cos_doc.objects[num]
    assert int(img.mapping[PdfName("Width")].value) < 160
    assert img.mapping[PdfName("Filter")] == PdfName("DCTDecode")


def test_target_dpi_must_be_positive():
    with pytest.raises(ValueError):
        OptimizationOptions(image_target_dpi=0)


def test_image_progressive_produces_sof2():
    extra = {
        PdfName("ColorSpace"): PdfName("DeviceRGB"),
        PdfName("Filter"): PdfName("FlateDecode"),
    }
    pdf, num = _pdf_with_image(extra, _flate_rgb())
    pdf.optimize(_opts(image_compression_quality=70, image_progressive=True))
    img = pdf._cos_doc.objects[num]
    assert img.mapping[PdfName("Filter")] == PdfName("DCTDecode")
    assert b"\xff\xc2" in img.content and b"\xff\xc0" not in img.content  # progressive
    assert dct.decode(img.content) is not None


def test_image_progressive_defaults_to_baseline():
    extra = {
        PdfName("ColorSpace"): PdfName("DeviceRGB"),
        PdfName("Filter"): PdfName("FlateDecode"),
    }
    pdf, num = _pdf_with_image(extra, _flate_rgb())
    pdf.optimize(_opts(image_compression_quality=70))
    img = pdf._cos_doc.objects[num]
    assert b"\xff\xc0" in img.content and b"\xff\xc2" not in img.content  # baseline

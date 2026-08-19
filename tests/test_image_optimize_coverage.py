"""Optimizer image coverage: form XObjects, ICC-CMYK, and mask downscaling.

The DPI target measures an image's on-page size, which previously came only
from page-level placements — an image drawn inside a form XObject was invisible
and kept its full resolution. Masks were skipped outright, so a full-resolution
soft mask survived a downscale of the image carrying it.
"""

from __future__ import annotations

import zlib

import pytest

from aspose_pdf.engine.cos import (
    PdfArray,
    PdfDictionary,
    PdfName,
    PdfNumber,
    PdfStream,
)
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.optimization import OptimizationOptions

_W, _H = 200, 160


def _photo_like(w: int, h: int, comps: int) -> bytes:
    """Deterministic high-entropy samples.

    A smooth gradient is the wrong fixture here: Flate compresses its regularity
    far better than it compresses a downscaled copy, so the optimizer's
    never-grow-the-file guard correctly refuses the resize and the test would
    measure nothing. Real photographic data shrinks when downscaled.
    """
    out = bytearray(w * h * comps)
    state = 0x2545F491
    for i in range(len(out)):
        state = (state * 1103515245 + 12345) & 0xFFFFFFFF
        out[i] = (state >> 16) & 0xFF
    return bytes(out)


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


def _image(cos, colorspace, comps, *, w=_W, h=_H, extra=None):
    payload = zlib.compress(_photo_like(w, h, comps), 9)
    mapping = {
        PdfName("Subtype"): PdfName("Image"),
        PdfName("Width"): PdfNumber(w),
        PdfName("Height"): PdfNumber(h),
        PdfName("BitsPerComponent"): PdfNumber(8),
        PdfName("ColorSpace"): colorspace,
        PdfName("Filter"): PdfName("FlateDecode"),
        PdfName("Length"): PdfNumber(len(payload)),
    }
    mapping.update(extra or {})
    return cos.register_object(PdfStream(payload, mapping))


def _size(pdf, num) -> tuple[int, int]:
    m = pdf._cos_doc.objects[num].mapping
    return int(m[PdfName("Width")].value), int(m[PdfName("Height")].value)


# --- DPI through form XObjects --------------------------------------------
def _pdf_with_image_in_form(*, form_matrix=None, nested=False):
    """The image is drawn only inside a form placed at 72x72 points."""
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 300, 300)]
    pdf.page_contents = [b"q 72 0 0 72 10 10 cm /Fx0 Do Q"]
    pdf._ensure_cos()
    cos = pdf._cos_doc
    img = _image(cos, PdfName("DeviceRGB"), 3)

    inner = b"q 1 0 0 1 0 0 cm /Im0 Do Q"
    form_map = {
        PdfName("Subtype"): PdfName("Form"),
        PdfName("BBox"): PdfArray([PdfNumber(v) for v in (0, 0, 1, 1)]),
        PdfName("Length"): PdfNumber(len(inner)),
        PdfName("Resources"): PdfDictionary(
            {PdfName("XObject"): PdfDictionary({PdfName("Im0"): img})}
        ),
    }
    if form_matrix is not None:
        form_map[PdfName("Matrix")] = PdfArray(
            [PdfNumber(v) for v in form_matrix]
        )
    form = cos.register_object(PdfStream(inner, form_map))

    if nested:
        middle_content = b"q 1 0 0 1 0 0 cm /Fx1 Do Q"
        form = cos.register_object(
            PdfStream(
                middle_content,
                {
                    PdfName("Subtype"): PdfName("Form"),
                    PdfName("BBox"): PdfArray([PdfNumber(v) for v in (0, 0, 1, 1)]),
                    PdfName("Length"): PdfNumber(len(middle_content)),
                    PdfName("Resources"): PdfDictionary(
                        {PdfName("XObject"): PdfDictionary({PdfName("Fx1"): form})}
                    ),
                },
            )
        )

    pdf._get_page_dict(0).mapping[PdfName("Resources")] = PdfDictionary(
        {PdfName("XObject"): PdfDictionary({PdfName("Fx0"): form})}
    )
    return pdf, img.object_number


def test_image_inside_a_form_is_downsampled_to_target_dpi():
    """72pt wide at 72 dpi means 72 pixels; the image starts at 200."""
    pdf, num = _pdf_with_image_in_form()
    assert _size(pdf, num) == (_W, _H)
    pdf.optimize(_opts(image_target_dpi=72))
    width, height = _size(pdf, num)
    assert width < _W and height < _H
    assert width == pytest.approx(72, abs=2)


def test_image_inside_a_nested_form_is_measured():
    pdf, num = _pdf_with_image_in_form(nested=True)
    pdf.optimize(_opts(image_target_dpi=72))
    assert _size(pdf, num)[0] == pytest.approx(72, abs=2)


def test_form_matrix_scales_the_measured_size():
    """A form /Matrix applies before the CTM at its Do."""
    pdf, num = _pdf_with_image_in_form(form_matrix=(2, 0, 0, 2, 0, 0))
    pdf.optimize(_opts(image_target_dpi=72))
    # The form doubles the placement, so the image shows at 144 points.
    assert _size(pdf, num)[0] == pytest.approx(144, abs=3)


def test_unplaced_image_keeps_its_resolution():
    """With no placement the display size — hence DPI — is unknown."""
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 300, 300)]
    pdf.page_contents = [b"q Q"]
    pdf._ensure_cos()
    img = _image(pdf._cos_doc, PdfName("DeviceRGB"), 3)
    pdf._get_page_dict(0).mapping[PdfName("Resources")] = PdfDictionary(
        {PdfName("XObject"): PdfDictionary({PdfName("Im0"): img})}
    )
    pdf.optimize(_opts(image_target_dpi=72))
    assert _size(pdf, img.object_number) == (_W, _H)


def test_self_referential_form_terminates():
    """A form drawing itself must not recurse forever."""
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 300, 300)]
    pdf.page_contents = [b"q 72 0 0 72 0 0 cm /Fx0 Do Q"]
    pdf._ensure_cos()
    cos = pdf._cos_doc
    img = _image(cos, PdfName("DeviceRGB"), 3)
    inner = b"q /Im0 Do Q q 1 0 0 1 0 0 cm /Fx0 Do Q"
    form = cos.register_object(
        PdfStream(
            inner,
            {
                PdfName("Subtype"): PdfName("Form"),
                PdfName("BBox"): PdfArray([PdfNumber(v) for v in (0, 0, 1, 1)]),
                PdfName("Length"): PdfNumber(len(inner)),
            },
        )
    )
    resources = PdfDictionary(
        {
            PdfName("XObject"): PdfDictionary(
                {PdfName("Fx0"): form, PdfName("Im0"): img}
            )
        }
    )
    pdf._resolve(form).mapping[PdfName("Resources")] = resources
    pdf._get_page_dict(0).mapping[PdfName("Resources")] = resources
    pdf.optimize(_opts(image_target_dpi=72))  # must return


# --- ICC-based CMYK --------------------------------------------------------
def test_icc_cmyk_image_is_recompressed():
    """An /ICCBased /N 4 image is CMYK, which the encoder already handles."""
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 300, 300)]
    pdf.page_contents = [b"q 200 0 0 160 0 0 cm /Im0 Do Q"]
    pdf._ensure_cos()
    cos = pdf._cos_doc
    profile = cos.register_object(
        PdfStream(
            b"\x00" * 128,
            {PdfName("N"): PdfNumber(4), PdfName("Length"): PdfNumber(128)},
        )
    )
    img = _image(cos, PdfArray([PdfName("ICCBased"), profile]), 4)
    before = cos.objects[img.object_number].content
    pdf._get_page_dict(0).mapping[PdfName("Resources")] = PdfDictionary(
        {PdfName("XObject"): PdfDictionary({PdfName("Im0"): img})}
    )
    pdf.optimize(_opts(image_compression_quality=50))
    stream = cos.objects[img.object_number]
    assert len(stream.content) < len(before)
    assert stream.mapping[PdfName("Filter")] == PdfName("DCTDecode")


# --- masks -----------------------------------------------------------------
def _pdf_with_smask():
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 300, 300)]
    pdf.page_contents = [b"q 72 0 0 72 10 10 cm /Im0 Do Q"]
    pdf._ensure_cos()
    cos = pdf._cos_doc
    mask = _image(cos, PdfName("DeviceGray"), 1)
    img = _image(cos, PdfName("DeviceRGB"), 3, extra={PdfName("SMask"): mask})
    pdf._get_page_dict(0).mapping[PdfName("Resources")] = PdfDictionary(
        {PdfName("XObject"): PdfDictionary({PdfName("Im0"): img})}
    )
    return pdf, img.object_number, mask.object_number


def test_soft_mask_is_downscaled_with_its_image():
    pdf, img_num, mask_num = _pdf_with_smask()
    pdf.optimize(_opts(image_target_dpi=72))
    assert _size(pdf, img_num)[0] == pytest.approx(72, abs=2)
    assert _size(pdf, mask_num)[0] == pytest.approx(72, abs=2)


def test_soft_mask_is_never_jpeg_encoded():
    """Downscaling a mask is safe; JPEG ringing on a sharp mask is not."""
    pdf, _img_num, mask_num = _pdf_with_smask()
    pdf.optimize(_opts(image_compression_quality=40, image_target_dpi=72))
    mask = pdf._cos_doc.objects[mask_num]
    assert mask.mapping[PdfName("Filter")] == PdfName("FlateDecode")


def test_mask_untouched_without_a_resize_request():
    """Quality alone must not rewrite a mask at all."""
    pdf, _img_num, mask_num = _pdf_with_smask()
    before = pdf._cos_doc.objects[mask_num].content
    pdf.optimize(_opts(image_compression_quality=40))
    assert pdf._cos_doc.objects[mask_num].content == before

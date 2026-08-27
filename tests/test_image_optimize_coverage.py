"""Optimizer image coverage.

The DPI target measures an image's on-page size, which previously came only
from page-level placements — an image drawn inside a form XObject was invisible
and kept its full resolution. Masks were skipped outright, so a full-resolution
soft mask survived a downscale of the image carrying it.

The second half covers what the recompressor used to refuse outright: anything
that was not 8-bit device gray/RGB/CMYK under a raster filter. An Indexed,
Lab, Separation or DeviceN image, a 1/2/4/16-bit one, a stencil mask, and a
CCITT, JBIG2 or JPEG 2000 payload all kept their original bytes however large
they were. Every one of those is a real scanned-document case, and each is now
brought to device samples first — which also means the *appearance* has to
survive, so that is asserted directly rather than assumed.
"""

from __future__ import annotations

import zlib

import pytest

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

# ---------------------------------------------------------------------------
# Colour spaces the recompressor used to refuse
# ---------------------------------------------------------------------------
def _one_image_pdf(colorspace, comps, *, bpc=8, extra=None, payload=None, w=_W, h=_H):
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 300, 300)]
    pdf.page_contents = [b"q 144 0 0 144 10 10 cm /Im0 Do Q"]
    pdf._ensure_cos()
    cos = pdf._cos_doc
    if payload is None:
        payload = zlib.compress(_photo_like(w, h, comps), 9)
    mapping = {
        PdfName("Subtype"): PdfName("Image"),
        PdfName("Width"): PdfNumber(w),
        PdfName("Height"): PdfNumber(h),
        PdfName("BitsPerComponent"): PdfNumber(bpc),
        PdfName("Filter"): PdfName("FlateDecode"),
        PdfName("Length"): PdfNumber(len(payload)),
    }
    if colorspace is not None:
        mapping[PdfName("ColorSpace")] = colorspace
    mapping.update(extra or {})
    image = cos.register_object(PdfStream(payload, mapping))
    pdf._get_page_dict(0).mapping[PdfName("Resources")] = PdfDictionary(
        {PdfName("XObject"): PdfDictionary({PdfName("Im0"): image})}
    )
    return pdf, image.object_number


def _payload_size(pdf, num) -> int:
    return len(pdf._cos_doc.objects[num].content)


def _entry(pdf, num, key):
    return pdf._cos_doc.objects[num].mapping.get(PdfName(key))


def _number(pdf, num, key) -> float | None:
    value = _entry(pdf, num, key)
    return None if value is None else float(value.value)


def test_an_indexed_image_is_expanded_and_recompressed():
    """The palette goes into the samples, so the space it named goes too."""
    palette = bytes((index * 7) % 256 for index in range(256 * 3))
    pdf, num = _one_image_pdf(None, 1)
    lookup = pdf._cos_doc.register_object(
        PdfStream(
            zlib.compress(palette, 9),
            {
                PdfName("Filter"): PdfName("FlateDecode"),
                PdfName("Length"): PdfNumber(len(zlib.compress(palette, 9))),
            },
        )
    )
    pdf._cos_doc.objects[num].mapping[PdfName("ColorSpace")] = PdfArray(
        [PdfName("Indexed"), PdfName("DeviceRGB"), PdfNumber(255), lookup]
    )
    before = _payload_size(pdf, num)

    pdf.optimize(_opts(image_compression_quality=60))

    assert _payload_size(pdf, num) < before
    assert _entry(pdf, num, "ColorSpace") == PdfName("DeviceRGB")
    assert _entry(pdf, num, "Filter") == PdfName("DCTDecode")


def test_a_separation_image_is_converted_through_its_tint_transform():
    tint = PdfDictionary(
        {
            PdfName("FunctionType"): PdfNumber(2),
            PdfName("Domain"): PdfArray([PdfNumber(0), PdfNumber(1)]),
            PdfName("C0"): PdfArray([PdfNumber(1), PdfNumber(1), PdfNumber(1)]),
            PdfName("C1"): PdfArray([PdfNumber(0), PdfNumber(0), PdfNumber(0)]),
            PdfName("N"): PdfNumber(1),
        }
    )
    space = PdfArray(
        [PdfName("Separation"), PdfName("Spot"), PdfName("DeviceRGB"), tint]
    )
    pdf, num = _one_image_pdf(space, 1)
    before = _payload_size(pdf, num)

    pdf.optimize(_opts(image_compression_quality=60))

    assert _payload_size(pdf, num) < before
    assert _entry(pdf, num, "ColorSpace") == PdfName("DeviceRGB")


def test_a_lab_image_is_converted_to_rgb():
    space = PdfArray(
        [
            PdfName("Lab"),
            PdfDictionary(
                {
                    PdfName("WhitePoint"): PdfArray(
                        [PdfNumber(0.9505), PdfNumber(1), PdfNumber(1.089)]
                    )
                }
            ),
        ]
    )
    pdf, num = _one_image_pdf(space, 3)
    before = _payload_size(pdf, num)

    pdf.optimize(_opts(image_compression_quality=60))

    assert _payload_size(pdf, num) < before
    assert _entry(pdf, num, "ColorSpace") == PdfName("DeviceRGB")


# ---------------------------------------------------------------------------
# Bit depths the recompressor used to refuse
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bpc", [4, 16])
def test_sub_byte_and_wide_samples_are_normalised(bpc: int):
    """Only 8 bits per component used to be recompressible."""
    per_row = (_W * bpc + 7) // 8
    payload = zlib.compress(_photo_like(per_row, _H, 1), 9)
    pdf, num = _one_image_pdf(PdfName("DeviceGray"), 1, bpc=bpc, payload=payload)
    before = _payload_size(pdf, num)

    pdf.optimize(_opts(image_compression_quality=60))

    assert _payload_size(pdf, num) < before
    assert _number(pdf, num, "BitsPerComponent") == 8


@pytest.mark.parametrize("bpc", [1, 2])
def test_a_low_bit_depth_image_is_only_rewritten_when_that_saves_bytes(bpc: int):
    """Widening 1-bit samples to 8 and JPEG-ing them can easily cost more.

    The recompressor now *considers* these images rather than refusing them
    outright, but the never-grow guard still has the last word -- packed
    bilevel data is already several times smaller than its 8-bit expansion.
    """
    per_row = (_W * bpc + 7) // 8
    payload = zlib.compress(_photo_like(per_row, _H, 1), 9)
    pdf, num = _one_image_pdf(PdfName("DeviceGray"), 1, bpc=bpc, payload=payload)
    before = _payload_size(pdf, num)

    pdf.optimize(_opts(image_compression_quality=60))

    assert _payload_size(pdf, num) <= before


# ---------------------------------------------------------------------------
# Filters the recompressor used to refuse
# ---------------------------------------------------------------------------
def test_a_jpeg_2000_image_is_decoded_and_recompressed():
    """JPX was on the "cannot recover samples cheaply" list; it no longer is."""
    from tests.test_jpeg2000_decoder import _RGB_16x16_53

    pdf, num = _one_image_pdf(
        PdfName("DeviceRGB"),
        3,
        payload=_RGB_16x16_53,
        w=16,
        h=16,
        extra={PdfName("Filter"): PdfName("JPXDecode")},
    )
    before = _payload_size(pdf, num)

    pdf.optimize(_opts(image_compression_quality=40))

    assert _payload_size(pdf, num) < before
    assert _entry(pdf, num, "Filter") == PdfName("DCTDecode")
    assert _entry(pdf, num, "ColorSpace") == PdfName("DeviceRGB")


# ---------------------------------------------------------------------------
# Stencils
# ---------------------------------------------------------------------------
def test_a_stencil_mask_is_downscaled_and_stays_one_bit():
    """A mask is a shape: JPEG would smear its edges, so only the size drops."""
    per_row = (_W + 7) // 8
    payload = zlib.compress(_photo_like(per_row, _H, 1), 9)
    pdf, num = _one_image_pdf(
        None,
        1,
        bpc=1,
        payload=payload,
        extra={PdfName("ImageMask"): PdfBoolean(True)},
    )
    before = _payload_size(pdf, num)

    pdf.optimize(_opts(image_target_dpi=72))

    assert _size(pdf, num) == (144, 115)
    assert _payload_size(pdf, num) < before
    assert _number(pdf, num, "BitsPerComponent") == 1
    assert _entry(pdf, num, "Filter") == PdfName("FlateDecode")


def test_a_stencil_mask_is_left_alone_without_a_resize():
    """There is no lossy option for a stencil, so nothing to do."""
    per_row = (_W + 7) // 8
    payload = zlib.compress(_photo_like(per_row, _H, 1), 9)
    pdf, num = _one_image_pdf(
        None, 1, bpc=1, payload=payload,
        extra={PdfName("ImageMask"): PdfBoolean(True)},
    )
    before = _payload_size(pdf, num)

    pdf.optimize(_opts(image_compression_quality=20))

    assert _payload_size(pdf, num) == before


# ---------------------------------------------------------------------------
# The property that matters
# ---------------------------------------------------------------------------
def test_expanding_a_palette_reproduces_the_colours_it_named():
    """The samples leave the palette behind, so they must carry its colours.

    Asserted at the conversion itself rather than through a re-encode: whether
    JPEG happens to beat the original Flate stream depends on the fixture's
    entropy, and that has nothing to do with whether the palette was read
    correctly.
    """
    palette = bytes((255, 0, 0, 0, 255, 0, 0, 0, 255, 255, 255, 0))
    indices = bytes([0, 1, 2, 3])
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 300, 300)]
    pdf.page_contents = [b""]
    pdf._ensure_cos()
    lookup = pdf._cos_doc.register_object(
        PdfStream(palette, {PdfName("Length"): PdfNumber(len(palette))})
    )
    space = PdfArray(
        [PdfName("Indexed"), PdfName("DeviceRGB"), PdfNumber(3), lookup]
    )

    result = pdf._samples_to_device(space, indices, 8, 4, 1)

    assert result is not None
    samples, comps, name = result
    assert (comps, name) == (3, "DeviceRGB")
    assert samples == palette


def test_sub_byte_palette_indices_keep_their_value():
    """``to_8bpc_bytes`` widens a 4-bit sample to 8; an index must not follow.

    A 4-bit index of 3 means palette entry 3, not entry 51.
    """
    pdf = SimplePdf()

    assert pdf._reduce_indices(bytes([0, 17, 34, 255]), 4) == bytes([0, 1, 2, 15])
    assert pdf._reduce_indices(bytes([0, 85, 170, 255]), 2) == bytes([0, 1, 2, 3])
    assert pdf._reduce_indices(bytes([7, 200]), 8) == bytes([7, 200])


def test_a_downscaled_stencil_still_carries_its_shape():
    """Packing the mask back to one bit must keep the coverage, not blank it."""
    from aspose_pdf.engine.image_export import to_8bpc_bytes

    per_row = (_W + 7) // 8
    # Left half set, right half clear: a shape that survives any downscale.
    rows = []
    for _ in range(_H):
        row = bytearray(per_row)
        for byte in range(per_row // 2):
            row[byte] = 0xFF
        rows.append(bytes(row))
    payload = zlib.compress(b"".join(rows), 9)
    pdf, num = _one_image_pdf(
        None, 1, bpc=1, payload=payload,
        extra={PdfName("ImageMask"): PdfBoolean(True)},
    )

    pdf.optimize(_opts(image_target_dpi=72))

    width, height = _size(pdf, num)
    stream = pdf._cos_doc.objects[num]
    samples = to_8bpc_bytes(
        pdf._decode_cos_stream(stream), 1, width, height, 1
    )
    row = samples[: width]
    assert row[2] == 255  # still set on the left
    assert row[width - 3] == 0  # still clear on the right

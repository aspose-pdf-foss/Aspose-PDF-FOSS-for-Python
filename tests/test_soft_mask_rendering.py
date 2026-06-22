"""Soft masks and transparency-group compositing in the page renderer.

Covers image ``/SMask`` per-pixel alpha, ExtGState ``/SMask`` luminosity and
alpha soft masks (built by rendering the ``/G`` group offscreen), ``/SMask
/None`` and q/Q reset of the mask, and unit compositing of a transparency group
drawn under a constant alpha (so overlaps do not double-darken).
"""

from __future__ import annotations

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


def _image(data, w, h, cs, extra=None):
    d = {
        PdfName("Type"): PdfName("XObject"),
        PdfName("Subtype"): PdfName("Image"),
        PdfName("Width"): PdfNumber(w),
        PdfName("Height"): PdfNumber(h),
        PdfName("BitsPerComponent"): PdfNumber(8),
        PdfName("ColorSpace"): PdfName(cs),
    }
    if extra:
        d.update(extra)
    return PdfStream(data, d)


def _group(content, bbox=(0, 0, 4, 4)):
    return PdfStream(
        content,
        {
            PdfName("Type"): PdfName("XObject"),
            PdfName("Subtype"): PdfName("Form"),
            PdfName("BBox"): _arr(*bbox),
            PdfName("Group"): PdfDictionary(
                {PdfName("S"): PdfName("Transparency")}
            ),
            PdfName("Resources"): PdfDictionary({}),
        },
    )


def _render(page_box, content, resources_builder, antialias=False):
    pdf = SimplePdf(pages=[page_box], page_contents=[content])
    pdf._ensure_cos()
    resources = resources_builder(pdf)
    pdf._get_page_dict(0).mapping[PdfName("Resources")] = resources
    doc = Document()
    doc._engine_pdf = pdf
    return doc.pages[0].render(antialias=antialias)


# --- image /SMask ----------------------------------------------------------


def test_image_smask_makes_covered_pixels_transparent():
    # 2x2 green image; SMask top row 0 (transparent), bottom row 255 (opaque),
    # drawn over a red backdrop. Top shows red, bottom shows green.
    def build(pdf):
        sm = _image(bytes([0, 0, 255, 255]), 2, 2, "DeviceGray")
        sm_ref = pdf._cos_doc.register_object(sm)
        im = _image(
            bytes([0, 255, 0] * 4), 2, 2, "DeviceRGB", {PdfName("SMask"): sm_ref}
        )
        im_ref = pdf._cos_doc.register_object(im)
        return PdfDictionary(
            {PdfName("XObject"): PdfDictionary({PdfName("Im0"): im_ref})}
        )

    raster = _render(
        (0, 0, 4, 4),
        b"1 0 0 rg 0 0 4 4 re f q 4 0 0 4 0 0 cm /Im0 Do Q",
        build,
    )
    assert raster.get_pixel(1, 0) == (255, 0, 0)  # top: transparent -> backdrop
    assert raster.get_pixel(1, 3) == (0, 255, 0)  # bottom: opaque -> image


def test_image_smask_mid_value_blends_half():
    # SMask 128 over the whole 1x1 image -> ~50% blend of green over red.
    def build(pdf):
        sm = _image(bytes([128]), 1, 1, "DeviceGray")
        sm_ref = pdf._cos_doc.register_object(sm)
        im = _image(
            bytes([0, 255, 0]), 1, 1, "DeviceRGB", {PdfName("SMask"): sm_ref}
        )
        im_ref = pdf._cos_doc.register_object(im)
        return PdfDictionary(
            {PdfName("XObject"): PdfDictionary({PdfName("Im0"): im_ref})}
        )

    raster = _render(
        (0, 0, 4, 4),
        b"1 0 0 rg 0 0 4 4 re f q 4 0 0 4 0 0 cm /Im0 Do Q",
        build,
    )
    r, g, b = raster.get_pixel(2, 2)
    assert 118 <= r <= 138 and 118 <= g <= 138 and b == 0


# --- ExtGState /SMask ------------------------------------------------------


def _extgstate_smask_doc(content, group_content, subtype="Luminosity", page=(0, 0, 4, 4)):
    def build(pdf):
        group = _group(group_content, bbox=(0, 0, page[2], page[3]))
        g_ref = pdf._cos_doc.register_object(group)
        smask = PdfDictionary(
            {PdfName("S"): PdfName(subtype), PdfName("G"): g_ref}
        )
        gs = PdfDictionary({PdfName("SMask"): smask})
        gs_ref = pdf._cos_doc.register_object(gs)
        return PdfDictionary(
            {PdfName("ExtGState"): PdfDictionary({PdfName("Gs0"): gs_ref})}
        )

    return _render(page, content, build)


def test_extgstate_luminosity_mask_attenuates_fill():
    # Mask group: white left half (luminosity 255 -> opaque), black right half
    # (0 -> transparent). A red fill shows on the left, white page on the right.
    raster = _extgstate_smask_doc(
        b"/Gs0 gs 1 0 0 rg 0 0 4 4 re f",
        b"1 1 1 rg 0 0 2 4 re f",
    )
    assert raster.get_pixel(0, 2) == (255, 0, 0)
    assert raster.get_pixel(3, 2) == (255, 255, 255)


def test_extgstate_luminosity_mask_mid_gray_is_half():
    # A uniform 50%-gray mask group halves the fill: red over white -> ~pink.
    raster = _extgstate_smask_doc(
        b"/Gs0 gs 1 0 0 rg 0 0 4 4 re f",
        b"0.5 0.5 0.5 rg 0 0 4 4 re f",
    )
    r, g, b = raster.get_pixel(2, 2)
    assert r == 255 and 118 <= g <= 138 and 118 <= b <= 138


def test_extgstate_alpha_mask_uses_coverage_not_luminosity():
    # The same black-left group means transparent under Luminosity but OPAQUE
    # under Alpha (coverage ignores colour). Left shows the red fill.
    lum = _extgstate_smask_doc(
        b"/Gs0 gs 1 0 0 rg 0 0 4 4 re f",
        b"0 0 0 rg 0 0 2 4 re f",
        subtype="Luminosity",
    )
    alpha = _extgstate_smask_doc(
        b"/Gs0 gs 1 0 0 rg 0 0 4 4 re f",
        b"0 0 0 rg 0 0 2 4 re f",
        subtype="Alpha",
    )
    assert lum.get_pixel(0, 2) == (255, 255, 255)  # black luminosity -> transparent
    assert alpha.get_pixel(0, 2) == (255, 0, 0)  # painted coverage -> opaque


def test_smask_none_resets_mask():
    # Gs1 sets an all-transparent mask; Gs2 is /SMask /None. The left fill is
    # masked away (white page), the right fill paints normally (blue).
    def build(pdf):
        group = _group(b"0 0 0 rg 0 0 4 4 re f")  # all black -> fully transparent
        g_ref = pdf._cos_doc.register_object(group)
        gs1 = PdfDictionary(
            {
                PdfName("SMask"): PdfDictionary(
                    {PdfName("S"): PdfName("Luminosity"), PdfName("G"): g_ref}
                )
            }
        )
        gs2 = PdfDictionary({PdfName("SMask"): PdfName("None")})
        return PdfDictionary(
            {
                PdfName("ExtGState"): PdfDictionary(
                    {
                        PdfName("Gs1"): pdf._cos_doc.register_object(gs1),
                        PdfName("Gs2"): pdf._cos_doc.register_object(gs2),
                    }
                )
            }
        )

    raster = _render(
        (0, 0, 4, 4),
        b"/Gs1 gs 1 0 0 rg 0 0 2 4 re f /Gs2 gs 0 0 1 rg 2 0 2 4 re f",
        build,
    )
    assert raster.get_pixel(0, 2) == (255, 255, 255)  # masked away
    assert raster.get_pixel(3, 2) == (0, 0, 255)  # mask reset -> painted


def test_q_restore_clears_soft_mask():
    # The mask is set inside q ... Q; after Q the second fill is unmasked.
    def build(pdf):
        group = _group(b"0 0 0 rg 0 0 4 4 re f")  # fully transparent mask
        g_ref = pdf._cos_doc.register_object(group)
        gs1 = PdfDictionary(
            {
                PdfName("SMask"): PdfDictionary(
                    {PdfName("S"): PdfName("Luminosity"), PdfName("G"): g_ref}
                )
            }
        )
        return PdfDictionary(
            {
                PdfName("ExtGState"): PdfDictionary(
                    {PdfName("Gs1"): pdf._cos_doc.register_object(gs1)}
                )
            }
        )

    raster = _render(
        (0, 0, 4, 4),
        b"q /Gs1 gs 1 0 0 rg 0 0 2 4 re f Q 0 0 1 rg 2 0 2 4 re f",
        build,
    )
    assert raster.get_pixel(0, 2) == (255, 255, 255)  # masked inside q/Q
    assert raster.get_pixel(3, 2) == (0, 0, 255)  # unmasked after Q


# --- transparency group compositing ----------------------------------------


def test_transparency_group_composites_as_unit():
    # Two overlapping black rects inside a group drawn at ca=0.5. Composited as
    # a unit, the overlap is the same 50% gray as the rest (no double-darken).
    def build(pdf):
        fm = _group(b"0 0 0 rg 0 0 4 4 re f 2 0 4 4 re f", bbox=(0, 0, 6, 4))
        fm_ref = pdf._cos_doc.register_object(fm)
        gs = PdfDictionary({PdfName("ca"): PdfNumber(0.5)})
        gs_ref = pdf._cos_doc.register_object(gs)
        return PdfDictionary(
            {
                PdfName("XObject"): PdfDictionary({PdfName("Fm0"): fm_ref}),
                PdfName("ExtGState"): PdfDictionary({PdfName("Gs0"): gs_ref}),
            }
        )

    raster = _render((0, 0, 6, 4), b"/Gs0 gs /Fm0 Do", build)
    non_overlap = raster.get_pixel(0, 2)  # left-only
    overlap = raster.get_pixel(3, 2)  # both rects
    assert abs(non_overlap[0] - overlap[0]) <= 2  # uniform, not doubled
    assert 120 <= overlap[0] <= 136  # ~128: black at 0.5 over white

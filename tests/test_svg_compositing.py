"""SVG compositing, nested graphics state and bounded raster fallbacks."""

from __future__ import annotations

import base64
import xml.etree.ElementTree as ET
from dataclasses import replace
from io import BytesIO

import pytest
from PIL import Image

from aspose_pdf import Document, PdfLoadLimits, PdfResourceLimitException
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfBoolean,
    PdfDictionary,
    PdfName,
    PdfNumber,
    PdfStream,
)
from aspose_pdf.engine.svg_export import page_to_svg
from tests.test_svg_export import _helvetica_resources, _page

NS = "{http://www.w3.org/2000/svg}"
HREF = "{http://www.w3.org/1999/xlink}href"


def _arr(*values):
    return PdfArray([PdfNumber(v) for v in values])


def _dict(**values):
    return PdfDictionary({PdfName(k): v for k, v in values.items()})


def _form(content, *, bbox=(0, 0, 40, 40), resources=None, group=None, matrix=None):
    mapping = {
        PdfName("Type"): PdfName("XObject"),
        PdfName("Subtype"): PdfName("Form"),
        PdfName("BBox"): _arr(*bbox),
    }
    if resources is not None:
        mapping[PdfName("Resources")] = resources
    if group is not None:
        mapping[PdfName("Group")] = _dict(S=PdfName("Transparency"), **group)
    if matrix is not None:
        mapping[PdfName("Matrix")] = _arr(*matrix)
    return PdfStream(content, mapping)


def _document(content, **resources):
    doc = Document()
    doc._engine_pdf = _page(
        content, size=(40, 40), resources=_dict(**resources).mapping
    )
    return doc


def _tree(doc, **options):
    return ET.fromstring(doc.pages[0].to_svg(**options))


def _elements(root, tag):
    return list(root.iter(NS + tag))


def _painted(root):
    return [p for p in _elements(root, "path") if p.get("fill") is not None]


def _png(element):
    with Image.open(
        BytesIO(base64.b64decode(element.get(HREF).split(",", 1)[1]))
    ) as im:
        return im.copy()


def _mask_state(subtype="Luminosity", *, content=b"1 g 0 0 20 40 re f", **extra):
    return _dict(
        SMask=_dict(
            S=PdfName(subtype),
            G=_form(content, group={}),
            **extra,
        )
    )


@pytest.mark.parametrize(
    "mode,css",
    [
        ("Multiply", "multiply"),
        ("Screen", "screen"),
        ("Overlay", "overlay"),
        ("Darken", "darken"),
        ("Lighten", "lighten"),
        ("ColorDodge", "color-dodge"),
        ("ColorBurn", "color-burn"),
        ("HardLight", "hard-light"),
        ("SoftLight", "soft-light"),
        ("Difference", "difference"),
        ("Exclusion", "exclusion"),
        ("Hue", "hue"),
        ("Saturation", "saturation"),
        ("Color", "color"),
        ("Luminosity", "luminosity"),
    ],
)
def test_blend_modes_and_page_isolation(mode, css):
    with _document(
        b"q /Blend gs 1 0 0 rg 0 0 20 40 re f Q 0 0 1 rg 20 0 20 40 re f",
        ExtGState=_dict(Blend=_dict(BM=PdfName(mode))),
    ) as doc:
        root = _tree(doc, background=(0, 255, 0))
    paper, page = list(root)
    assert paper.tag == NS + "rect" and paper.get("fill") == "#00ff00"
    assert page.get("style") == "isolation:isolate"
    blended, normal = list(page)
    assert blended.get("style") == f"mix-blend-mode:{css}"
    assert blended[0].get("fill") == "#ff0000"
    assert normal.tag == NS + "path" and normal.get("fill") == "#0000ff"


@pytest.mark.parametrize(
    "subtype,content,expected",
    [
        ("Alpha", b"0 g 0 0 20 40 re f", 255),
        ("Luminosity", b"0.5 g 0 0 20 40 re f", 128),
        ("Luminosity", b"1 0 0 rg 0 0 20 40 re f", 76),
    ],
)
def test_soft_mask_is_rasterized_separately_from_visible_marks(
    subtype, content, expected
):
    with _document(
        b"/Mask gs 0 0 1 rg 0 0 40 40 re f",
        ExtGState=_dict(Mask=_mask_state(subtype, content=content)),
    ) as doc:
        root = _tree(doc, background=None)
    assert [p.get("fill") for p in _painted(root)] == ["#0000ff"]
    (mask,) = _elements(root, "mask")
    assert mask.get("maskUnits") == mask.get("maskContentUnits") == "userSpaceOnUse"
    assert mask.get("width") == mask.get("height") == "40"
    with _png(mask[0]) as alpha:
        assert alpha.getpixel((10, 20)) == expected
        assert alpha.getpixel((30, 20)) == 0
    assert any(g.get("mask") == f"url(#{mask.get('id')})" for g in _elements(root, "g"))


def test_soft_mask_transfer_backdrop_and_bbox():
    transfer = _dict(
        FunctionType=PdfNumber(2),
        Domain=_arr(0, 1),
        C0=_arr(1),
        C1=_arr(0),
        N=PdfNumber(1),
    )
    mask = _mask_state(
        content=b"1 g -100 -100 200 200 re f", TR=transfer, BC=_arr(0.25)
    )
    mask.mapping[PdfName("SMask")].mapping[PdfName("G")].mapping[PdfName("BBox")] = (
        _arr(0, 0, 20, 40)
    )
    with _document(b"/Mask gs 0 0 40 40 re f", ExtGState=_dict(Mask=mask)) as doc:
        root = _tree(doc)
    with _png(_elements(root, "mask")[0][0]) as alpha:
        assert alpha.getpixel((10, 20)) == 0
        assert alpha.getpixel((30, 20)) == 191


@pytest.mark.parametrize("reset", [b"Q", b"/Clear gs"])
def test_soft_mask_reuse_and_restoration(reset):
    prefix = b"q " if reset == b"Q" else b""
    with _document(
        prefix
        + b"/Mask gs 0 0 10 10 re f 10 0 10 10 re f "
        + reset
        + b" 20 0 10 10 re f",
        ExtGState=_dict(Mask=_mask_state(), Clear=_dict(SMask=PdfName("None"))),
    ) as doc:
        root = _tree(doc)
    assert len(_elements(root, "mask")) == 1
    page = list(root)[-1]
    assert page[0].get("mask") == page[1].get("mask")
    assert page[2].tag == NS + "path"


@pytest.mark.parametrize(
    "paint",
    [
        b"2 w 0 20 m 40 20 l S",
        b"BT /F1 20 Tf 0 10 Td (A) Tj ET",
        b"40 0 0 40 0 0 cm /Im Do",
        b"/Sh sh",
    ],
)
def test_masks_and_blends_cover_all_paint_sinks(paint):
    image = PdfStream(
        b"\xff\x00\x00",
        {
            PdfName("Subtype"): PdfName("Image"),
            PdfName("Width"): PdfNumber(1),
            PdfName("Height"): PdfNumber(1),
            PdfName("BitsPerComponent"): PdfNumber(8),
            PdfName("ColorSpace"): PdfName("DeviceRGB"),
        },
    )
    shading = _dict(
        ShadingType=PdfNumber(2),
        ColorSpace=PdfName("DeviceRGB"),
        Coords=_arr(0, 0, 40, 0),
        Extend=PdfArray([PdfBoolean(True)] * 2),
        Function=_dict(
            FunctionType=PdfNumber(2),
            Domain=_arr(0, 1),
            C0=_arr(1, 0, 0),
            C1=_arr(0, 0, 1),
            N=PdfNumber(1),
        ),
    )
    state = _mask_state()
    state.mapping[PdfName("BM")] = PdfName("Multiply")
    state.mapping[PdfName("ca")] = state.mapping[PdfName("CA")] = PdfNumber(0.5)
    with _document(
        b"/Mask gs " + paint,
        ExtGState=_dict(Mask=state),
        XObject=_dict(Im=image),
        Shading=_dict(Sh=shading),
    ) as doc:
        resources = doc._engine_pdf._get_page_dict(0).mapping[PdfName("Resources")]
        resources.mapping.update(_helvetica_resources(doc._engine_pdf))
        root = _tree(doc)
    composites = [g for g in _elements(root, "g") if g.get("mask")]
    assert composites and all(
        g.get("style") == "mix-blend-mode:multiply" for g in composites
    )
    assert not any(g.get("transform") for g in composites)


@pytest.mark.parametrize("isolated", [False, True])
def test_transparency_groups_keep_vectors_and_their_isolation(isolated):
    group = _form(
        b"/Blend gs 0 0 1 rg 0 0 40 40 re f",
        group={"I": PdfBoolean(isolated)},
        resources=_dict(ExtGState=_dict(Blend=_dict(BM=PdfName("Multiply")))),
    )
    with _document(b"1 0 0 rg 0 0 40 40 re f /Fm Do", XObject=_dict(Fm=group)) as doc:
        root = _tree(doc)
    assert not _elements(root, "image")
    page = list(root)[-1]
    wrapper = page[1]
    assert wrapper.get("style") == ("isolation:isolate" if isolated else None)
    assert wrapper[0].get("style") == "mix-blend-mode:multiply"


def test_nested_isolated_group_alpha_applies_once_and_inherits_colour():
    inner = _form(b"0 0 30 40 re f 10 0 30 40 re f", group={"I": PdfBoolean(True)})
    outer = _form(
        b"/Half gs /Inner Do",
        group={"I": PdfBoolean(True)},
        resources=_dict(
            XObject=_dict(Inner=inner), ExtGState=_dict(Half=_dict(ca=PdfNumber(0.5)))
        ),
    )
    with _document(
        b"0 0 1 rg /Half gs /Outer Do",
        XObject=_dict(Outer=outer),
        ExtGState=_dict(Half=_dict(ca=PdfNumber(0.5))),
    ) as doc:
        root = _tree(doc)
        raster = doc.pages[0].render(antialias=False)
    groups = [g for g in _elements(root, "g") if g.get("opacity") == "0.5"]
    assert len(groups) == 2
    assert len(_painted(root)) == 2
    assert all(
        p.get("fill") == "#0000ff" and p.get("fill-opacity") is None
        for p in _painted(root)
    )
    assert raster.get_pixel(5, 20) == raster.get_pixel(20, 20)
    assert raster.get_pixel(20, 20) == (191, 191, 255)


@pytest.mark.parametrize("knockout", [False, True])
def test_complex_group_fallback_keeps_alpha_and_discards_partial_vectors(knockout):
    group = _form(
        b"0 0 1 rg 10 10 20 20 re f",
        group={"I": PdfBoolean(knockout), "K": PdfBoolean(knockout)},
    )
    with _document(
        b"/Half gs /Fm Do",
        XObject=_dict(Fm=group),
        ExtGState=_dict(Half=_dict(ca=PdfNumber(0.5))),
    ) as doc:
        root = _tree(doc, background=None)
    assert not _elements(root, "path") and not _elements(root, "defs")
    (image,) = _elements(root, "image")
    with _png(image) as pixels:
        assert pixels.mode == "RGBA"
        assert pixels.getpixel((20, 20))[:3] == (0, 0, 255)
        assert abs(pixels.getpixel((20, 20))[3] - 128) <= 1
        assert pixels.getpixel((2, 2))[3] == 0


def test_knockout_fallback_matches_the_group_backdrop():
    group = _form(
        b"/Half gs 1 0 0 rg 0 0 30 40 re f 0 0 1 rg 10 0 30 40 re f",
        group={"I": PdfBoolean(True), "K": PdfBoolean(True)},
        resources=_dict(ExtGState=_dict(Half=_dict(ca=PdfNumber(0.5)))),
    )
    with _document(b"/Fm Do", XObject=_dict(Fm=group)) as doc:
        root = _tree(doc, background=None)
    with _png(_elements(root, "image")[0]) as pixels:
        assert (
            pixels.getpixel((20, 20)) == pixels.getpixel((35, 20)) == (0, 0, 255, 128)
        )
        assert pixels.getpixel((5, 20)) == (255, 0, 0, 128)


def test_nested_forms_restore_clip_stroke_state_and_caller_path():
    inner = _form(
        b"q 0 0 5 5 re W n [2 1] 0 d 1 J 0 0 20 20 re S",
        bbox=(0, 0, 10, 10),
        matrix=(1, 0, 0, 1, 10, 10),
    )
    outer = _form(b"/Inner Do", resources=_dict(XObject=_dict(Inner=inner)))
    with _document(
        b"q 2 2 36 36 re W n 2 w 0 30 m 40 30 l /Outer Do S Q 0 0 1 rg 30 0 10 10 re f",
        XObject=_dict(Outer=outer),
    ) as doc:
        root = _tree(doc)
        raster = doc.pages[0].render(antialias=False)
    inner_path, outer_path, after = _painted(root)
    assert inner_path.get("stroke-dasharray") == "2 1"
    assert outer_path.get("stroke-dasharray") is None
    assert outer_path.get("d") == "M0 10L40 10"
    clips = _elements(root, "clipPath")
    assert outer_path.get("clip-path") == f"url(#{clips[0].get('id')})"
    assert after.get("clip-path") is None
    assert raster.get_pixel(30, 10) == (0, 0, 0)
    assert raster.get_pixel(35, 35) == (0, 0, 255)


def test_form_bbox_clips_marks_and_does_not_clip_later_marks():
    form = _form(
        b"1 0 0 rg -50 -50 100 100 re f",
        bbox=(0, 0, 10, 10),
        matrix=(1, 0, 0, 1, 10, 10),
    )
    with _document(b"/Fm Do 0 0 1 rg 30 0 10 10 re f", XObject=_dict(Fm=form)) as doc:
        root = _tree(doc)
        raster = doc.pages[0].render(antialias=False)
    red, blue = _painted(root)
    assert red.get("clip-path") is not None and blue.get("clip-path") is None
    assert raster.get_pixel(15, 25) == (255, 0, 0)
    assert raster.get_pixel(5, 25) == (255, 255, 255)
    assert raster.get_pixel(35, 35) == (0, 0, 255)


def test_transformed_image_clip_stays_in_page_space():
    image = PdfStream(
        b"\xff\x00\x00",
        {
            PdfName("Subtype"): PdfName("Image"),
            PdfName("Width"): PdfNumber(1),
            PdfName("Height"): PdfNumber(1),
            PdfName("BitsPerComponent"): PdfNumber(8),
            PdfName("ColorSpace"): PdfName("DeviceRGB"),
        },
    )
    with _document(
        b"10 10 10 20 re W n 40 0 0 40 0 0 cm /Im Do", XObject=_dict(Im=image)
    ) as doc:
        root = _tree(doc)
    (element,) = _elements(root, "image")
    parent = next(g for g in _elements(root, "g") if element in list(g))
    assert parent.get("clip-path") is not None and parent.get("transform") is None
    assert element.get("clip-path") is None and element.get("transform") is not None


def test_extgstate_stroke_parameters_use_the_shared_graphics_state():
    with _document(
        b"/Stroke gs 5 5 m 35 35 l S",
        ExtGState=_dict(
            Stroke=_dict(
                LC=PdfNumber(2), LJ=PdfNumber(2), D=PdfArray([_arr(4, 2), PdfNumber(1)])
            )
        ),
    ) as doc:
        (path,) = _painted(_tree(doc))
    assert path.get("stroke-linecap") == "square"
    assert path.get("stroke-linejoin") == "bevel"
    assert path.get("stroke-dasharray") == "4 2"
    assert path.get("stroke-dashoffset") == "1"


def test_empty_clip_does_not_turn_into_an_unclipped_fill():
    with _document(b"0 0 m 0 0 l W n 0 0 40 40 re f") as doc:
        root = _tree(doc)
    (clip,) = _elements(root, "clipPath")
    assert clip[0].get("d") == ""
    assert _painted(root)[0].get("clip-path") == f"url(#{clip.get('id')})"


@pytest.mark.parametrize("knockout", [False, True])
def test_annotation_group_placement_and_clip_are_independent_of_page(knockout):
    appearance = _form(
        b"0 0 1 rg 0 0 10 10 re f",
        bbox=(0, 0, 10, 10),
        matrix=(2, 0, 0, 2, 5, 5),
        group={"I": PdfBoolean(True), "K": PdfBoolean(knockout)},
    )
    with _document(b"0 0 5 5 re W n") as doc:
        doc._engine_pdf._get_page_dict(0).mapping[PdfName("Annots")] = PdfArray(
            [
                _dict(
                    Subtype=PdfName("Stamp"),
                    Rect=_arr(20, 20, 40, 40),
                    AP=_dict(N=appearance),
                )
            ]
        )
        root = _tree(doc)
        raster = doc.pages[0].render(antialias=False)
        hidden = _tree(doc, draw_annotations=False)
    assert raster.get_pixel(30, 10) == (0, 0, 255)
    assert raster.get_pixel(10, 10) == (255, 255, 255)
    assert not _painted(hidden) and not _elements(hidden, "image")
    if knockout:
        with _png(_elements(root, "image")[0]) as pixels:
            assert pixels.getpixel((30, 10)) == (0, 0, 255, 255)
    else:
        (path,) = _painted(root)
        assert path.get("d") == "M20 20L40 20L40 0L20 0L20 20Z"


@pytest.mark.parametrize("kind", ["mask", "fallback", "page"])
def test_svg_limits_apply_before_allocating_rasters(kind):
    group = _form(b"0 0 40 40 re f", group={"K": PdfBoolean(True)})
    if kind == "mask":
        content, resources = (
            b"/Mask gs 0 0 40 40 re f",
            {"ExtGState": _dict(Mask=_mask_state())},
        )
        limits = replace(PdfLoadLimits.unlimited(), max_codec_work_bytes=100)
    elif kind == "fallback":
        content, resources = b"/Fm Do", {"XObject": _dict(Fm=group)}
        limits = replace(PdfLoadLimits.unlimited(), max_codec_work_bytes=100)
    else:
        content, resources = b"", {}
        limits = replace(PdfLoadLimits.unlimited(), max_raster_pixels=100)
    with _document(content, **resources) as doc:
        with pytest.raises(PdfResourceLimitException):
            page_to_svg(doc._engine_pdf, 0, limits=limits)


@pytest.mark.parametrize(
    "paint",
    [
        b"/Pattern cs /P0 scn 0 0 40 40 re f",
        b"/Pattern CS /P0 SCN 8 w 0 20 m 40 20 l S",
    ],
)
def test_pattern_fallback_preserves_painted_pixels(paint):
    from tests.test_pattern_paint import _CELL, _document, _tiling

    with _document(paint, _tiling(_CELL)) as doc:
        root = _tree(doc)
        expected = doc.pages[0].render(antialias=False)
    with _png(_elements(root, "image")[0]) as pixels:
        white = Image.new("RGBA", pixels.size, "white")
        white.alpha_composite(pixels)
        assert white.convert("RGB").tobytes() == expected.pixels


def test_svg_compositing_survives_pdf_file_round_trip(tmp_path):
    group = _form(b"0 0 1 rg 0 0 40 40 re f", group={"I": PdfBoolean(True)})
    with _document(
        b"/Mask gs /Fm Do", ExtGState=_dict(Mask=_mask_state()), XObject=_dict(Fm=group)
    ) as doc:
        path = tmp_path / "compositing.pdf"
        resources = doc._engine_pdf._get_page_dict(0).mapping[PdfName("Resources")]
        register = doc._engine_pdf._cos_doc.register_object
        resources.mapping[PdfName("XObject")].mapping[PdfName("Fm")] = register(group)
        mask = (
            resources.mapping[PdfName("ExtGState")]
            .mapping[PdfName("Mask")]
            .mapping[PdfName("SMask")]
        )
        mask.mapping[PdfName("G")] = register(mask.mapping[PdfName("G")])
        doc.save(path)
    with Document(path) as loaded:
        svg_path = loaded.pages[0].save_as_svg(
            tmp_path / "compositing.svg", background=None
        )
        root = ET.parse(svg_path).getroot()
    assert len(_elements(root, "mask")) == 1
    assert [p.get("fill") for p in _painted(root)] == ["#0000ff"]


def test_type3_glyph_clips_do_not_escape_into_the_next_glyph_or_page():
    from tests.test_type3_fonts import _document

    clipped = b"1000 0 d0 q 0 0 100 100 re W n 0 0 1000 1000 re f"
    square = b"1000 0 d0 0 0 1000 1000 re f"
    with _document(
        b"BT /F1 50 Tf 10 10 Td (AB) Tj ET 0 0 1 rg 150 150 20 20 re f",
        {"a": clipped, "b": square},
    ) as doc:
        root = _tree(doc)
        raster = doc.pages[0].render(antialias=False)
    first, second, after = _painted(root)
    assert first.get("clip-path") is not None
    assert second.get("clip-path") is None and after.get("clip-path") is None
    assert raster.get_pixel(80, 170) == (0, 0, 0)
    assert raster.get_pixel(160, 40) == (0, 0, 255)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_soft_mask_transform_is_fixed_when_selected(rotation):
    with _document(
        b"2 0 0 2 10 0 cm /Mask gs 0.5 0 0 0.5 -5 0 cm 0 g 0 0 40 40 re f",
        ExtGState=_dict(Mask=_mask_state(content=b"1 g 0 0 5 20 re f")),
    ) as doc:
        page = doc._engine_pdf._get_page_dict(0)
        page.mapping[PdfName("Rotate")] = PdfNumber(rotation)
        page.mapping[PdfName("CropBox")] = _arr(5, 0, 35, 40)
        root = _tree(doc)
        raster = doc.pages[0].render(antialias=False)
    with _png(_elements(root, "mask")[0][0]) as alpha:
        assert alpha.size == (raster.width, raster.height)
        values = set(alpha.tobytes())
        assert values == {0, 255}
        for y in range(raster.height):
            for x in range(raster.width):
                value = 255 - alpha.getpixel((x, y))
                assert raster.get_pixel(x, y) == (value, value, value)


@pytest.mark.parametrize("masked", [False, True])
def test_nested_group_inherits_the_enclosing_form_resources(masked):
    child = _form(b"/Half gs 0 0 40 40 re f", group={"K": PdfBoolean(not masked)})
    resources = _dict(ExtGState=_dict(Half=_dict(ca=PdfNumber(0.5))))
    if masked:
        resources.mapping[PdfName("ExtGState")].mapping[PdfName("Mask")] = _dict(
            SMask=_dict(S=PdfName("Alpha"), G=child)
        )
        content = b"/Mask gs 0 0 40 40 re f"
    else:
        resources.mapping[PdfName("XObject")] = _dict(Inner=child)
        content = b"/Inner Do"
    parent = _form(content, resources=resources)
    with _document(b"/Outer Do", XObject=_dict(Outer=parent)) as doc:
        root = _tree(doc, background=None)
    if masked:
        with _png(_elements(root, "mask")[0][0]) as alpha:
            assert alpha.getpixel((20, 20)) == 128
    else:
        with _png(_elements(root, "image")[0]) as rgba:
            assert rgba.getpixel((20, 20))[3] == 128

"""Tests for PDF functions, shadings, and their page rendering."""

from aspose_pdf import Document
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfBoolean,
    PdfDictionary,
    PdfName,
    PdfNumber,
    PdfStream,
)
from aspose_pdf.engine.shading import build_function, build_shading
from aspose_pdf.engine.simple_pdf import SimplePdf


def _n(x):
    return PdfNumber(x)


def _arr(*xs):
    return PdfArray([PdfNumber(x) for x in xs])


def _exp_function(c0, c1, n=1):
    return PdfDictionary(
        {
            PdfName("FunctionType"): _n(2),
            PdfName("Domain"): _arr(0, 1),
            PdfName("C0"): _arr(*c0),
            PdfName("C1"): _arr(*c1),
            PdfName("N"): _n(n),
        }
    )


def _axial_dict(coords, function, *, extend=(False, False), cs="DeviceRGB"):
    return PdfDictionary(
        {
            PdfName("ShadingType"): _n(2),
            PdfName("ColorSpace"): PdfName(cs),
            PdfName("Coords"): _arr(*coords),
            PdfName("Function"): function,
            PdfName("Extend"): PdfArray([PdfBoolean(extend[0]), PdfBoolean(extend[1])]),
        }
    )


def _function_shading_dict(*, background=None, bbox=None, matrix=None):
    calculator = PdfStream(
        b"{ 0 }",
        {
            PdfName("FunctionType"): _n(4),
            PdfName("Domain"): _arr(0, 1, 0, 1),
            PdfName("Range"): _arr(0, 1, 0, 1, 0, 1),
        },
    )
    mapping = {
        PdfName("ShadingType"): _n(1),
        PdfName("ColorSpace"): PdfName("DeviceRGB"),
        PdfName("Function"): calculator,
        PdfName("Matrix"): _arr(*(matrix or (10, 0, 0, 10, 5, 5))),
    }
    if background is not None:
        mapping[PdfName("Background")] = _arr(*background)
    if bbox is not None:
        mapping[PdfName("BBox")] = _arr(*bbox)
    return PdfDictionary(mapping)


# ---------------------------------------------------------------------------
# PDF function unit tests
# ---------------------------------------------------------------------------


def test_exponential_function():
    pdf = SimplePdf()
    func = build_function(pdf, _exp_function([0.0, 0.0, 0.0], [1.0, 0.5, 0.0]))
    assert func.eval(0.0) == [0.0, 0.0, 0.0]
    assert func.eval(1.0) == [1.0, 0.5, 0.0]
    mid = func.eval(0.5)
    assert abs(mid[0] - 0.5) < 1e-9 and abs(mid[1] - 0.25) < 1e-9


def test_stitching_function():
    pdf = SimplePdf()
    stitch = PdfDictionary(
        {
            PdfName("FunctionType"): _n(3),
            PdfName("Domain"): _arr(0, 1),
            PdfName("Functions"): PdfArray(
                [
                    _exp_function([0.0], [1.0]),  # 0..0.5 -> 0..1
                    _exp_function([1.0], [0.0]),  # 0.5..1 -> 1..0
                ]
            ),
            PdfName("Bounds"): _arr(0.5),
            PdfName("Encode"): _arr(0, 1, 0, 1),
        }
    )
    func = build_function(pdf, stitch)
    assert abs(func.eval(0.25)[0] - 0.5) < 1e-9  # first segment, halfway
    assert abs(func.eval(0.75)[0] - 0.5) < 1e-9  # second segment, halfway down


def test_sampled_function():
    pdf = SimplePdf()
    stream = PdfStream(
        bytes([255, 0, 0, 0, 0, 255]),  # sample0 = red, sample1 = blue
        {
            PdfName("FunctionType"): _n(0),
            PdfName("Domain"): _arr(0, 1),
            PdfName("Size"): _arr(2),
            PdfName("BitsPerSample"): _n(8),
            PdfName("Range"): _arr(0, 1, 0, 1, 0, 1),
        },
    )
    func = build_function(pdf, stream)
    assert func is not None
    assert func.eval(0.0) == [1.0, 0.0, 0.0]
    assert func.eval(1.0) == [0.0, 0.0, 1.0]


def test_multidimensional_sampled_function_interpolates_packed_samples():
    pdf = SimplePdf()
    stream = PdfStream(
        bytes([0x0F, 0xF0]),
        {
            PdfName("FunctionType"): _n(0),
            PdfName("Domain"): _arr(0, 1, 0, 1),
            PdfName("Size"): _arr(2, 2),
            PdfName("BitsPerSample"): _n(4),
            PdfName("Range"): _arr(0, 1),
        },
    )

    func = build_function(pdf, stream)

    assert func is not None
    assert func.eval([0, 0]) == [0.0]
    assert func.eval([1, 0]) == [1.0]
    assert func.eval([0, 1]) == [1.0]
    assert func.eval([1, 1]) == [0.0]
    assert abs(func.eval([0.5, 0.5])[0] - 0.5) < 1e-9


def test_calculator_function_arithmetic_and_conditionals():
    pdf = SimplePdf()
    stream = PdfStream(
        b"{ dup 0.5 gt { dup mul } { 2 mul } ifelse }",
        {
            PdfName("FunctionType"): _n(4),
            PdfName("Domain"): _arr(0, 1),
            PdfName("Range"): _arr(0, 1),
        },
    )

    func = build_function(pdf, stream)

    assert func is not None
    assert func.eval(0.25) == [0.5]
    assert abs(func.eval(0.75)[0] - 0.5625) < 1e-9


def test_calculator_function_multiple_inputs_and_stack_operators():
    pdf = SimplePdf()
    stream = PdfStream(
        b"{ 2 copy mul 3 1 roll add exch }",
        {
            PdfName("FunctionType"): _n(4),
            PdfName("Domain"): _arr(0, 1, 0, 1),
            PdfName("Range"): _arr(0, 2, 0, 1),
        },
    )

    func = build_function(pdf, stream)

    assert func is not None
    assert func.eval([0.25, 0.5]) == [0.75, 0.125]


def test_calculator_function_preserves_integer_bitwise_semantics():
    pdf = SimplePdf()
    stream = PdfStream(
        b"{ pop 1 2 add 1 bitshift }",
        {
            PdfName("FunctionType"): _n(4),
            PdfName("Domain"): _arr(0, 1),
            PdfName("Range"): _arr(0, 10),
        },
    )

    func = build_function(pdf, stream)

    assert func is not None
    assert func.eval(0.5) == [6.0]


# ---------------------------------------------------------------------------
# Shading unit tests
# ---------------------------------------------------------------------------


def test_axial_shading_colors_and_extend():
    pdf = SimplePdf()
    shading = build_shading(
        pdf, _axial_dict([0, 0, 10, 0], _exp_function([1, 0, 0], [0, 0, 1]))
    )
    assert shading.color_at(0, 0) == (255, 0, 0)
    assert shading.color_at(10, 0) == (0, 0, 255)
    mid = shading.color_at(5, 0)
    assert mid[0] > 100 and mid[2] > 100 and mid[1] == 0
    # Outside the axis with Extend false -> unpainted.
    assert shading.color_at(-1, 0) is None
    assert shading.color_at(11, 0) is None


def test_axial_shading_extend_clamps():
    pdf = SimplePdf()
    shading = build_shading(
        pdf,
        _axial_dict([0, 0, 10, 0], _exp_function([1, 0, 0], [0, 0, 1]), extend=(True, True)),
    )
    assert shading.color_at(-5, 0) == (255, 0, 0)  # clamped to the start colour
    assert shading.color_at(20, 0) == (0, 0, 255)  # clamped to the end colour


def test_radial_shading():
    pdf = SimplePdf()
    shading = build_shading(
        pdf,
        PdfDictionary(
            {
                PdfName("ShadingType"): _n(3),
                PdfName("ColorSpace"): PdfName("DeviceRGB"),
                PdfName("Coords"): _arr(0, 0, 0, 0, 0, 10),  # concentric, r 0..10
                PdfName("Function"): _exp_function([1, 0, 0], [0, 0, 1]),
                PdfName("Extend"): PdfArray([PdfBoolean(False), PdfBoolean(False)]),
            }
        ),
    )
    assert shading.color_at(0, 0) == (255, 0, 0)  # centre, radius 0
    edge = shading.color_at(10, 0)
    assert edge is not None and edge[2] == 255  # outer circle, radius 10


def test_function_shading_applies_domain_matrix_and_bbox():
    shading = build_shading(
        SimplePdf(),
        _function_shading_dict(bbox=(6, 6, 14, 14)),
    )

    assert shading is not None
    assert shading.color_at(10, 10) == (128, 128, 0)
    assert shading.color_at(5, 10) is None  # inside Domain, outside BBox
    assert shading.color_at(15, 10) is None  # outside BBox and Domain


def test_function_shading_rejects_singular_matrix():
    shading = build_shading(
        SimplePdf(),
        _function_shading_dict(matrix=(1, 2, 2, 4, 0, 0)),
    )

    assert shading is None


def test_function_shading_background_is_pattern_only():
    shading = build_shading(
        SimplePdf(),
        _function_shading_dict(background=(0, 1, 0), bbox=(0, 0, 20, 20)),
    )

    assert shading is not None
    assert shading.color_at(2, 10) is None
    assert shading.pattern_color_at(2, 10) == (0, 255, 0)


def test_function_shading_converts_separation_through_alternate_space():
    shading_function = PdfStream(
        b"{ pop }",
        {
            PdfName("FunctionType"): _n(4),
            PdfName("Domain"): _arr(0, 1, 0, 1),
            PdfName("Range"): _arr(0, 1),
        },
    )
    color_space = PdfArray(
        [
            PdfName("Separation"),
            PdfName("BrandInk"),
            PdfName("DeviceRGB"),
            _exp_function([1, 1, 1], [1, 0, 0]),
        ]
    )
    shading = build_shading(
        SimplePdf(),
        PdfDictionary(
            {
                PdfName("ShadingType"): _n(1),
                PdfName("ColorSpace"): color_space,
                PdfName("Function"): shading_function,
            }
        ),
    )

    assert shading is not None
    assert shading.color_at(0, 0.5) == (255, 255, 255)
    assert shading.color_at(1, 0.5) == (255, 0, 0)


def test_function_shading_converts_device_n_tints():
    shading_function = PdfStream(
        b"{ }",
        {
            PdfName("FunctionType"): _n(4),
            PdfName("Domain"): _arr(0, 1, 0, 1),
            PdfName("Range"): _arr(0, 1, 0, 1),
        },
    )
    tint_transform = PdfStream(
        b"{ 0 }",
        {
            PdfName("FunctionType"): _n(4),
            PdfName("Domain"): _arr(0, 1, 0, 1),
            PdfName("Range"): _arr(0, 1, 0, 1, 0, 1),
        },
    )
    color_space = PdfArray(
        [
            PdfName("DeviceN"),
            PdfArray([PdfName("InkA"), PdfName("InkB")]),
            PdfName("DeviceRGB"),
            tint_transform,
        ]
    )
    shading = build_shading(
        SimplePdf(),
        PdfDictionary(
            {
                PdfName("ShadingType"): _n(1),
                PdfName("ColorSpace"): color_space,
                PdfName("Function"): shading_function,
            }
        ),
    )

    assert shading is not None
    assert shading.color_at(0.25, 0.75) == (64, 191, 0)


def test_malformed_mesh_shading_returns_none():
    pdf = SimplePdf()
    mesh = PdfDictionary(
        {PdfName("ShadingType"): _n(4), PdfName("ColorSpace"): PdfName("DeviceRGB")}
    )
    assert build_shading(pdf, mesh) is None


def _mesh_stream(shading_type, data, *, decode=None, extra=None):
    mapping = {
        PdfName("ShadingType"): _n(shading_type),
        PdfName("ColorSpace"): PdfName("DeviceRGB"),
        PdfName("BitsPerCoordinate"): _n(8),
        PdfName("BitsPerComponent"): _n(8),
        PdfName("Decode"): _arr(*(decode or (0, 10, 0, 10, 0, 1, 0, 1, 0, 1))),
    }
    if extra:
        mapping.update(extra)
    return PdfStream(bytes(data), mapping)


def test_type4_free_form_mesh_interpolates_vertex_colors():
    stream = _mesh_stream(
        4,
        [
            0, 0, 0, 255, 0, 0,
            0, 255, 0, 0, 255, 0,
            0, 0, 255, 0, 0, 255,
        ],
        extra={PdfName("BitsPerFlag"): _n(8)},
    )

    shading = build_shading(SimplePdf(), stream)

    assert shading is not None
    center = shading.color_at(10 / 3, 10 / 3)
    assert center is not None
    assert all(75 <= component <= 95 for component in center)
    assert shading.color_at(9, 9) is None


def test_type5_lattice_mesh_builds_both_cell_triangles():
    stream = _mesh_stream(
        5,
        [
            0, 255, 255, 0, 0,
            255, 255, 0, 255, 0,
            0, 0, 0, 0, 255,
            255, 0, 255, 255, 255,
        ],
        extra={PdfName("VerticesPerRow"): _n(2)},
    )

    shading = build_shading(SimplePdf(), stream)

    assert shading is not None
    assert shading.color_at(2, 8) is not None
    assert shading.color_at(8, 2) is not None


def _patch_points(shading_type):
    boundary = [
        (0, 0),
        (0, 85),
        (0, 170),
        (0, 255),
        (85, 255),
        (170, 255),
        (255, 255),
        (255, 170),
        (255, 85),
        (255, 0),
        (170, 0),
        (85, 0),
    ]
    if shading_type == 7:
        boundary.extend([(85, 85), (85, 170), (170, 170), (170, 85)])
    return [component for point in boundary for component in point]


def test_type6_and_type7_patch_meshes_are_tessellated():
    for shading_type in (6, 7):
        data = [0, *_patch_points(shading_type)]
        data.extend(
            [
                255, 0, 0,
                0, 255, 0,
                0, 0, 255,
                255, 255, 255,
            ]
        )
        stream = _mesh_stream(
            shading_type,
            data,
            extra={PdfName("BitsPerFlag"): _n(8)},
        )

        shading = build_shading(SimplePdf(), stream)

        assert shading is not None
        center = shading.color_at(5, 5)
        assert center is not None
        assert all(50 <= component <= 205 for component in center)


def test_patch_mesh_subdivision_tracks_device_scale():
    points = _patch_points(7)
    points[-8:] = [255, 255, 0, 255, 0, 0, 255, 0]
    data = [0, *points]
    data.extend([255, 0, 0] * 4)
    stream = _mesh_stream(
        7,
        data,
        extra={PdfName("BitsPerFlag"): _n(8)},
    )

    thumbnail = build_shading(SimplePdf(), stream, device_scale=0.01)
    enlarged = build_shading(SimplePdf(), stream, device_scale=20.0)

    assert thumbnail is not None
    assert enlarged is not None
    assert len(thumbnail.triangles) == 2
    assert 2 < len(enlarged.triangles) <= 8192


# ---------------------------------------------------------------------------
# End-to-end rendering
# ---------------------------------------------------------------------------


def _axial_shading_obj():
    return _axial_dict([0, 0, 20, 0], _exp_function([1, 0, 0], [0, 0, 1]))


def test_render_shading_pattern_fill():
    pdf = SimplePdf(
        pages=[(0, 0, 20, 20)],
        page_contents=[b"/Pattern cs /P0 scn 0 0 20 20 re f"],
    )
    pdf._ensure_cos()
    pattern = pdf._cos_doc.register_object(
        PdfDictionary(
            {
                PdfName("Type"): PdfName("Pattern"),
                PdfName("PatternType"): _n(2),
                PdfName("Shading"): _axial_shading_obj(),
            }
        )
    )
    pdf._get_page_dict(0).mapping[PdfName("Resources")] = PdfDictionary(
        {PdfName("Pattern"): PdfDictionary({PdfName("P0"): pattern})}
    )
    doc = Document()
    doc._engine_pdf = pdf

    raster = doc.pages[0].render(antialias=False)

    left = raster.get_pixel(2, 10)
    right = raster.get_pixel(17, 10)
    assert left[0] > left[2]  # red dominates on the left
    assert right[2] > right[0]  # blue dominates on the right


def test_render_sh_operator_respects_clip():
    pdf = SimplePdf(
        pages=[(0, 0, 20, 20)],
        page_contents=[b"q 5 0 10 20 re W n /Sh0 sh Q"],
    )
    pdf._ensure_cos()
    pdf._get_page_dict(0).mapping[PdfName("Resources")] = PdfDictionary(
        {PdfName("Shading"): PdfDictionary({PdfName("Sh0"): _axial_shading_obj()})}
    )
    doc = Document()
    doc._engine_pdf = pdf

    raster = doc.pages[0].render(antialias=False)

    assert raster.get_pixel(2, 10) == (255, 255, 255)  # outside the clip: untouched
    painted = raster.get_pixel(10, 10)
    assert painted != (255, 255, 255)  # inside the clip: gradient painted
    # Gradient direction holds inside the clipped band.
    assert raster.get_pixel(6, 10)[0] > raster.get_pixel(14, 10)[0]


def test_render_function_shading_pattern_applies_background():
    pdf = SimplePdf(
        pages=[(0, 0, 20, 20)],
        page_contents=[b"/Pattern cs /P0 scn 0 0 20 20 re f"],
    )
    pdf._ensure_cos()
    pattern = pdf._cos_doc.register_object(
        PdfDictionary(
            {
                PdfName("Type"): PdfName("Pattern"),
                PdfName("PatternType"): _n(2),
                PdfName("Shading"): _function_shading_dict(
                    background=(0, 1, 0), bbox=(0, 0, 20, 20)
                ),
            }
        )
    )
    pdf._get_page_dict(0).mapping[PdfName("Resources")] = PdfDictionary(
        {PdfName("Pattern"): PdfDictionary({PdfName("P0"): pattern})}
    )
    doc = Document()
    doc._engine_pdf = pdf

    raster = doc.pages[0].render(antialias=False)

    assert raster.get_pixel(2, 10) == (0, 255, 0)
    center = raster.get_pixel(10, 10)
    assert 105 <= center[0] <= 145
    assert 105 <= center[1] <= 145
    assert center[2] == 0


def test_render_function_shading_operator_ignores_background():
    pdf = SimplePdf(
        pages=[(0, 0, 20, 20)],
        page_contents=[b"/Sh0 sh"],
    )
    pdf._ensure_cos()
    pdf._get_page_dict(0).mapping[PdfName("Resources")] = PdfDictionary(
        {
            PdfName("Shading"): PdfDictionary(
                {
                    PdfName("Sh0"): _function_shading_dict(
                        background=(0, 1, 0), bbox=(0, 0, 20, 20)
                    )
                }
            )
        }
    )
    doc = Document()
    doc._engine_pdf = pdf

    raster = doc.pages[0].render(antialias=False)

    assert raster.get_pixel(2, 10) == (255, 255, 255)
    assert raster.get_pixel(10, 10) != (255, 255, 255)


def test_render_separation_fill_uses_tint_transform():
    pdf = SimplePdf(
        pages=[(0, 0, 10, 10)],
        page_contents=[b"/Spot cs 0.5 scn 0 0 10 10 re f"],
    )
    pdf._ensure_cos()
    color_space = PdfArray(
        [
            PdfName("Separation"),
            PdfName("BrandInk"),
            PdfName("DeviceRGB"),
            _exp_function([1, 1, 1], [1, 0, 0]),
        ]
    )
    pdf._get_page_dict(0).mapping[PdfName("Resources")] = PdfDictionary(
        {
            PdfName("ColorSpace"): PdfDictionary(
                {PdfName("Spot"): color_space}
            )
        }
    )
    doc = Document()
    doc._engine_pdf = pdf

    raster = doc.pages[0].render(antialias=False)

    assert raster.get_pixel(5, 5) == (255, 128, 128)


def test_render_device_n_fill_uses_alternate_space():
    tint_transform = PdfStream(
        b"{ 0 }",
        {
            PdfName("FunctionType"): _n(4),
            PdfName("Domain"): _arr(0, 1, 0, 1),
            PdfName("Range"): _arr(0, 1, 0, 1, 0, 1),
        },
    )
    color_space = PdfArray(
        [
            PdfName("DeviceN"),
            PdfArray([PdfName("InkA"), PdfName("InkB")]),
            PdfName("DeviceRGB"),
            tint_transform,
        ]
    )
    pdf = SimplePdf(
        pages=[(0, 0, 10, 10)],
        page_contents=[b"/Duotone cs 0.25 0.75 scn 0 0 10 10 re f"],
    )
    pdf._ensure_cos()
    pdf._get_page_dict(0).mapping[PdfName("Resources")] = PdfDictionary(
        {
            PdfName("ColorSpace"): PdfDictionary(
                {PdfName("Duotone"): color_space}
            )
        }
    )
    doc = Document()
    doc._engine_pdf = pdf

    raster = doc.pages[0].render(antialias=False)

    assert raster.get_pixel(5, 5) == (64, 191, 0)


def test_render_spot_overprint_preserves_backdrop_inks():
    color_space = PdfArray(
        [
            PdfName("Separation"),
            PdfName("BrandInk"),
            PdfName("DeviceRGB"),
            _exp_function([1, 1, 1], [1, 0, 0]),
        ]
    )
    pdf = SimplePdf(
        pages=[(0, 0, 20, 10)],
        page_contents=[
            b"0 1 1 rg 0 0 20 10 re f "
            b"q /Normal gs /Spot cs 1 scn 0 0 10 10 re f Q "
            b"q /Overprint gs /Spot cs 1 scn 10 0 10 10 re f Q"
        ],
    )
    pdf._ensure_cos()
    pdf._get_page_dict(0).mapping[PdfName("Resources")] = PdfDictionary(
        {
            PdfName("ColorSpace"): PdfDictionary(
                {PdfName("Spot"): color_space}
            ),
            PdfName("ExtGState"): PdfDictionary(
                {
                    PdfName("Normal"): PdfDictionary(
                        {PdfName("OP"): PdfBoolean(False)}
                    ),
                    PdfName("Overprint"): PdfDictionary(
                        {PdfName("OP"): PdfBoolean(True)}
                    ),
                }
            ),
        }
    )
    doc = Document()
    doc._engine_pdf = pdf

    raster = doc.pages[0].render(antialias=False)

    assert raster.get_pixel(5, 5) == (255, 0, 0)
    assert raster.get_pixel(15, 5) == (0, 0, 0)


def test_render_cmyk_overprint_mode_one_preserves_zero_components():
    pdf = SimplePdf(
        pages=[(0, 0, 20, 10)],
        page_contents=[
            b"1 0 0 0 k 0 0 20 10 re f "
            b"q /Mode0 gs 0 0 1 0 k 0 0 10 10 re f Q "
            b"q /Mode1 gs 0 0 1 0 k 10 0 10 10 re f Q"
        ],
    )
    pdf._ensure_cos()
    pdf._get_page_dict(0).mapping[PdfName("Resources")] = PdfDictionary(
        {
            PdfName("ExtGState"): PdfDictionary(
                {
                    PdfName("Mode0"): PdfDictionary(
                        {
                            PdfName("op"): PdfBoolean(True),
                            PdfName("OPM"): _n(0),
                        }
                    ),
                    PdfName("Mode1"): PdfDictionary(
                        {
                            PdfName("op"): PdfBoolean(True),
                            PdfName("OPM"): _n(1),
                        }
                    ),
                }
            )
        }
    )
    doc = Document()
    doc._engine_pdf = pdf

    raster = doc.pages[0].render(antialias=False)

    assert raster.get_pixel(5, 5) == (255, 255, 0)
    assert raster.get_pixel(15, 5) == (0, 255, 0)


def test_image_overprint_uses_image_color_space_not_current_fill_space():
    spot = PdfArray(
        [
            PdfName("Separation"),
            PdfName("BrandInk"),
            PdfName("DeviceRGB"),
            _exp_function([1, 1, 1], [1, 0, 0]),
        ]
    )
    image = PdfStream(
        bytes([255, 0, 0]),
        {
            PdfName("Type"): PdfName("XObject"),
            PdfName("Subtype"): PdfName("Image"),
            PdfName("Width"): _n(1),
            PdfName("Height"): _n(1),
            PdfName("BitsPerComponent"): _n(8),
            PdfName("ColorSpace"): PdfName("DeviceRGB"),
        },
    )
    pdf = SimplePdf(
        pages=[(0, 0, 10, 10)],
        page_contents=[
            b"0 1 1 rg 0 0 10 10 re f /Overprint gs /Spot cs 1 scn "
            b"q 10 0 0 10 0 0 cm /Im0 Do Q"
        ],
    )
    pdf._ensure_cos()
    pdf._get_page_dict(0).mapping[PdfName("Resources")] = PdfDictionary(
        {
            PdfName("ColorSpace"): PdfDictionary({PdfName("Spot"): spot}),
            PdfName("ExtGState"): PdfDictionary(
                {
                    PdfName("Overprint"): PdfDictionary(
                        {PdfName("OP"): PdfBoolean(True)}
                    )
                }
            ),
            PdfName("XObject"): PdfDictionary(
                {PdfName("Im0"): pdf._cos_doc.register_object(image)}
            ),
        }
    )
    doc = Document()
    doc._engine_pdf = pdf

    raster = doc.pages[0].render(antialias=False)

    assert raster.get_pixel(5, 5) == (255, 0, 0)


def test_render_cmyk_overprint_replaces_colorant_with_lighter_tint():
    # A non-zero colorant overprints by *replacing* the backdrop's colorant, not
    # by multiplying with it. Painting 50% cyan over solid cyan lightens the cyan
    # channel; the old Multiply preview wrongly kept it at full strength
    # (0, 255, 255).
    pdf = SimplePdf(
        pages=[(0, 0, 20, 10)],
        page_contents=[
            b"1 0 0 0 k 0 0 20 10 re f "
            b"q /Mode1 gs 0.5 0 0 0 k 0 0 10 10 re f Q"
        ],
    )
    pdf._ensure_cos()
    pdf._get_page_dict(0).mapping[PdfName("Resources")] = PdfDictionary(
        {
            PdfName("ExtGState"): PdfDictionary(
                {
                    PdfName("Mode1"): PdfDictionary(
                        {
                            PdfName("op"): PdfBoolean(True),
                            PdfName("OPM"): _n(1),
                        }
                    )
                }
            )
        }
    )
    doc = Document()
    doc._engine_pdf = pdf

    raster = doc.pages[0].render(antialias=False)

    assert raster.get_pixel(5, 5) == (128, 255, 255)


def test_render_spot_overprint_replaces_shared_colorant():
    # A Separation whose tint transform maps onto process cyan overprints a solid
    # cyan backdrop. The 50% spot tint replaces the cyan channel (lighter cyan)
    # rather than multiplying to full strength, and leaves the other inks alone.
    color_space = PdfArray(
        [
            PdfName("Separation"),
            PdfName("CyanInk"),
            PdfName("DeviceCMYK"),
            _exp_function([0, 0, 0, 0], [1, 0, 0, 0]),
        ]
    )
    pdf = SimplePdf(
        pages=[(0, 0, 20, 10)],
        page_contents=[
            b"1 0 0 0 k 0 0 20 10 re f "
            b"q /Overprint gs /Spot cs 0.5 scn 0 0 10 10 re f Q"
        ],
    )
    pdf._ensure_cos()
    pdf._get_page_dict(0).mapping[PdfName("Resources")] = PdfDictionary(
        {
            PdfName("ColorSpace"): PdfDictionary({PdfName("Spot"): color_space}),
            PdfName("ExtGState"): PdfDictionary(
                {
                    PdfName("Overprint"): PdfDictionary(
                        {PdfName("OP"): PdfBoolean(True)}
                    )
                }
            ),
        }
    )
    doc = Document()
    doc._engine_pdf = pdf

    raster = doc.pages[0].render(antialias=False)

    assert raster.get_pixel(5, 5) == (128, 255, 255)


def test_render_free_form_mesh_sh_operator():
    pdf = SimplePdf(
        pages=[(0, 0, 20, 20)],
        page_contents=[b"/Sh0 sh"],
    )
    pdf._ensure_cos()
    stream = _mesh_stream(
        4,
        [
            0, 0, 0, 255, 0, 0,
            0, 255, 0, 0, 255, 0,
            0, 0, 255, 0, 0, 255,
        ],
        decode=(0, 20, 0, 20, 0, 1, 0, 1, 0, 1),
        extra={PdfName("BitsPerFlag"): _n(8)},
    )
    pdf._get_page_dict(0).mapping[PdfName("Resources")] = PdfDictionary(
        {PdfName("Shading"): PdfDictionary({PdfName("Sh0"): stream})}
    )
    doc = Document()
    doc._engine_pdf = pdf

    raster = doc.pages[0].render(antialias=False)

    mixed = raster.get_pixel(5, 14)
    assert all(35 <= component <= 165 for component in mixed)
    assert raster.get_pixel(17, 2) == (255, 255, 255)

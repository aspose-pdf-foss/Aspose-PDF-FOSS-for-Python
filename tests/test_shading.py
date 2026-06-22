"""Tests for axial/radial PDF shadings (gradients) and their rendering."""

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


def test_unsupported_shading_type_returns_none():
    pdf = SimplePdf()
    mesh = PdfDictionary(
        {PdfName("ShadingType"): _n(4), PdfName("ColorSpace"): PdfName("DeviceRGB")}
    )
    assert build_shading(pdf, mesh) is None


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

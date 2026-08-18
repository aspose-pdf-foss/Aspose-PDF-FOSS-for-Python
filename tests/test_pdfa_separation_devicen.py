"""PDF/A conversion repoints Separation/DeviceN spaces off DeviceCMYK.

Under the sRGB OutputIntent ``convert_to_pdfa`` installs, a ``/Separation`` or
``/DeviceN`` space whose alternate is DeviceCMYK (or ICC-CMYK) still carries
CMYK. PDF cannot compose the existing tint transform with a CMYK->RGB
conversion, so the composition is resampled into a Type 0 function over
DeviceRGB. The space keeps its kind, colorant names and component count, so
content streams that select it need no rewriting — and the colour a viewer
computes must not move.
"""

from __future__ import annotations

from io import BytesIO

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfDictionary,
    PdfName,
    PdfNumber,
    PdfStream,
)
from aspose_pdf.engine.shading import build_color_converter
from aspose_pdf.engine.simple_pdf import SimplePdf

CMYK = PdfName("DeviceCMYK")


def _exp_function(cos, c1, *, n=1.0):
    """A Type 2 (exponential) tint transform. One input only, per ISO 32000-1."""
    return cos.register_object(
        PdfDictionary(
            {
                PdfName("FunctionType"): PdfNumber(2),
                PdfName("Domain"): PdfArray([PdfNumber(0), PdfNumber(1)]),
                PdfName("C0"): PdfArray([PdfNumber(0)] * 4),
                PdfName("C1"): PdfArray([PdfNumber(v) for v in c1]),
                PdfName("N"): PdfNumber(n),
            }
        )
    )


def _sampled_function(cos, inputs, weights):
    """A Type 0 tint transform over *inputs* colorants producing CMYK.

    Sampled at two points per axis, so its own interpolation is multilinear:
    ``component_i = sum(weights[i][j] * x_j)`` clamped to 0..1.
    """
    size = 2
    total = size**inputs
    samples = bytearray()
    for index in range(total):
        remainder, coords = index, []
        for _ in range(inputs):  # first input varies fastest
            coords.append(remainder % size)
            remainder //= size
        for row in weights:
            value = sum(w * c for w, c in zip(row, coords))
            samples.append(max(0, min(255, round(value * 255))))
    unit = [PdfNumber(0), PdfNumber(1)]
    return cos.register_object(
        PdfStream(
            bytes(samples),
            {
                PdfName("FunctionType"): PdfNumber(0),
                PdfName("Domain"): PdfArray(unit * inputs),
                PdfName("Range"): PdfArray(unit * 4),
                PdfName("Size"): PdfArray([PdfNumber(size)] * inputs),
                PdfName("BitsPerSample"): PdfNumber(8),
                PdfName("Length"): PdfNumber(len(samples)),
            },
        )
    )


def _document(make_space, content=b"/CS0 cs 0.5 scn 0 0 10 10 re f") -> bytes:
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 100, 100)]
    pdf.page_contents = [content]
    pdf._ensure_cos()
    cos = pdf._cos_doc
    space = make_space(cos)
    cos.objects[pdf._page_obj_ids[0]].mapping[PdfName("Resources")] = PdfDictionary(
        {PdfName("ColorSpace"): PdfDictionary({PdfName("CS0"): space})}
    )
    return pdf.to_bytes()


def _space(document: Document, name="CS0"):
    engine = document._engine_pdf
    resources = engine._cos_page_resources(0)
    spaces = engine._resolve(resources.mapping[PdfName("ColorSpace")])
    return engine._resolve(spaces.mapping[PdfName(name)])


def _converters(data: bytes):
    """Return (before, after, converted_document) colour converters."""
    before = Document().load_from(data)
    original = build_color_converter(before._engine_pdf, _space(before))
    after = Document().load_from(data)
    after.convert_to_pdfa("2b")
    converted = build_color_converter(after._engine_pdf, _space(after))
    return original, converted, after


def _max_delta(original, converted, samples) -> int:
    worst = 0
    for point in samples:
        a, b = original(point), converted(point)
        worst = max(worst, max(abs(x - y) for x, y in zip(a, b)))
    return worst


def test_separation_alternate_becomes_devicergb():
    data = _document(
        lambda cos: cos.register_object(
            PdfArray(
                [
                    PdfName("Separation"),
                    PdfName("Spot"),
                    CMYK,
                    _exp_function(cos, (0, 1, 1, 0)),
                ]
            )
        )
    )
    _original, _converted, document = _converters(data)
    space = _space(document)
    assert space.items[0] == PdfName("Separation")
    # Colorant name and component count are untouched.
    assert space.items[1] == PdfName("Spot")
    assert space.items[2] == PdfName("DeviceRGB")

    function = document._engine_pdf._resolve(space.items[3])
    assert isinstance(function, PdfStream)
    assert function.mapping[PdfName("FunctionType")].value == 0
    assert [n.value for n in function.mapping[PdfName("Range")].items] == [0, 1] * 3
    assert len(function.mapping[PdfName("Size")].items) == 1


def test_linear_separation_colour_is_preserved_exactly():
    data = _document(
        lambda cos: cos.register_object(
            PdfArray(
                [PdfName("Separation"), PdfName("Spot"), CMYK,
                 _exp_function(cos, (0, 1, 1, 0))]
            )
        )
    )
    original, converted, _doc = _converters(data)
    tints = [[i / 16] for i in range(17)]
    assert _max_delta(original, converted, tints) == 0


def test_nonlinear_separation_stays_within_one_step():
    """A curved tint transform is sampled, not composed — bound the error."""
    data = _document(
        lambda cos: cos.register_object(
            PdfArray(
                [PdfName("Separation"), PdfName("Spot"), CMYK,
                 _exp_function(cos, (0.9, 0.2, 0.1, 0.4), n=2.4)]
            )
        )
    )
    original, converted, _doc = _converters(data)
    tints = [[i / 64] for i in range(65)]
    assert _max_delta(original, converted, tints) <= 2


def test_devicen_two_colorants_are_resampled():
    def make(cos):
        return cos.register_object(
            PdfArray(
                [
                    PdfName("DeviceN"),
                    PdfArray([PdfName("SpotA"), PdfName("SpotB")]),
                    CMYK,
                    _sampled_function(
                        cos, 2, [(0.3, 0.0), (0.0, 0.8), (0.1, 0.1), (0.2, 0.4)]
                    ),
                ]
            )
        )

    data = _document(make, content=b"/CS0 cs 0.5 0.25 scn 0 0 10 10 re f")
    original, converted, document = _converters(data)
    space = _space(document)
    assert space.items[0] == PdfName("DeviceN")
    assert len(space.items[1].items) == 2
    assert space.items[2] == PdfName("DeviceRGB")

    function = document._engine_pdf._resolve(space.items[3])
    # One grid axis per colorant.
    assert len(function.mapping[PdfName("Size")].items) == 2
    assert [n.value for n in function.mapping[PdfName("Domain")].items] == [0, 1] * 2

    grid = [[i / 8, j / 8] for i in range(9) for j in range(9)]
    assert _max_delta(original, converted, grid) <= 3


def test_icc_cmyk_alternate_is_converted():
    def make(cos):
        profile = cos.register_object(
            PdfStream(
                b"\x00" * 128,
                {PdfName("N"): PdfNumber(4), PdfName("Length"): PdfNumber(128)},
            )
        )
        return cos.register_object(
            PdfArray(
                [
                    PdfName("Separation"),
                    PdfName("Spot"),
                    PdfArray([PdfName("ICCBased"), profile]),
                    _exp_function(cos, (0, 1, 1, 0)),
                ]
            )
        )

    _original, _converted, document = _converters(_document(make))
    assert _space(document).items[2] == PdfName("DeviceRGB")


def test_non_cmyk_alternate_is_left_alone():
    """A Separation over DeviceRGB is already fine; do not touch it."""

    def make(cos):
        return cos.register_object(
            PdfArray(
                [
                    PdfName("Separation"),
                    PdfName("Spot"),
                    PdfName("DeviceRGB"),
                    _exp_function(cos, (0.2, 0.4, 0.6)),
                ]
            )
        )

    data = _document(make)
    document = Document().load_from(data)
    tint_before = _space(document).items[3]
    document.convert_to_pdfa("2b")
    space = _space(document)
    assert space.items[2] == PdfName("DeviceRGB")
    # The original tint transform is still the one referenced.
    assert space.items[3].object_number == tint_before.object_number


def test_separation_image_keeps_its_tint_samples():
    """A Separation image needs no pixel rewrite — only the space changes."""
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 100, 100)]
    pdf.page_contents = [b"q 100 0 0 100 0 0 cm /Im0 Do Q"]
    pdf._ensure_cos()
    cos = pdf._cos_doc
    tints = bytes([0, 128, 255])
    space = cos.register_object(
        PdfArray(
            [PdfName("Separation"), PdfName("Spot"), CMYK,
             _exp_function(cos, (0, 1, 1, 0))]
        )
    )
    image = cos.register_object(
        PdfStream(
            tints,
            {
                PdfName("Type"): PdfName("XObject"),
                PdfName("Subtype"): PdfName("Image"),
                PdfName("Width"): PdfNumber(3),
                PdfName("Height"): PdfNumber(1),
                PdfName("BitsPerComponent"): PdfNumber(8),
                PdfName("ColorSpace"): space,
                PdfName("Length"): PdfNumber(len(tints)),
            },
        )
    )
    cos.objects[pdf._page_obj_ids[0]].mapping[PdfName("Resources")] = PdfDictionary(
        {PdfName("XObject"): PdfDictionary({PdfName("Im0"): image})}
    )

    document = Document().load_from(pdf.to_bytes())
    document.convert_to_pdfa("2b")
    engine = document._engine_pdf
    xobjects = engine._resolve(
        engine._cos_page_resources(0).mapping[PdfName("XObject")]
    )
    stream = engine._resolve(next(iter(xobjects.mapping.values())))
    assert stream.content == tints, "tint samples must not be rewritten"
    assert engine._resolve(stream.mapping[PdfName("ColorSpace")]).items[2] == PdfName(
        "DeviceRGB"
    )


def test_conversion_survives_save_and_reload():
    data = _document(
        lambda cos: cos.register_object(
            PdfArray(
                [PdfName("Separation"), PdfName("Spot"), CMYK,
                 _exp_function(cos, (0, 1, 1, 0))]
            )
        )
    )
    original, _converted, document = _converters(data)
    output = BytesIO()
    document.save(output)

    reloaded = Document().load_from(output.getvalue())
    space = _space(reloaded)
    assert space.items[2] == PdfName("DeviceRGB")
    reconverted = build_color_converter(reloaded._engine_pdf, space)
    assert _max_delta(original, reconverted, [[i / 16] for i in range(17)]) == 0


@pytest.mark.parametrize("inputs,expected_axes", [(1, 1), (2, 2), (3, 3)])
def test_grid_has_one_axis_per_colorant(inputs, expected_axes):
    def make(cos):
        return cos.register_object(
            PdfArray(
                [
                    PdfName("DeviceN"),
                    PdfArray([PdfName(f"S{i}") for i in range(inputs)]),
                    CMYK,
                    _sampled_function(
                        cos, inputs, [(0.2,) * inputs for _ in range(4)]
                    ),
                ]
            )
        )

    document = Document().load_from(_document(make))
    document.convert_to_pdfa("2b")
    function = document._engine_pdf._resolve(_space(document).items[3])
    size = function.mapping[PdfName("Size")].items
    assert len(size) == expected_axes
    # The grid stays inside the resample budget.
    assert size[0].value ** expected_axes <= 4096

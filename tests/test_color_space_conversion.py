"""Colours in Lab, Separation and DeviceN mean what their spaces say.

Three things were wrong, and they were one rule applied in too few places.

* **Lab was never converted.** The shared colour converter counted Lab's three
  components and read them as RGB, so ``60 40 -30 sc`` filled pure yellow
  where pdfium and MuPDF paint a mauve, and a Lab shading was wrong across the
  whole page.
* **Image samples were read as bytes over 255.** A Lab image's L* spans 0-100,
  so a palette or image in Lab came out near black -- and ``optimize`` baked
  that into the file when it recompressed one.
* **The image decoder knew grey, RGB, CMYK and palettes, nothing else.** A
  Separation or DeviceN image's tints were painted as grey or RGB, and image
  export wrote them out that way.

Expected colours are pdfium's. MuPDF agrees within a few units on fills,
strokes, shadings and tint images (neither lets a Lab space's white point tint
it, neither clamps a fill to ``/Range``). On Lab image samples it differs by up
to 30 units while agreeing on which samples are the same colour, and it ignores
a palette's base range; there the specification's reading, which pdfium
shares, is the one tested.
"""

from __future__ import annotations

import io
import zlib

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.shading import _lab_to_rgb

_LAB_D65 = b"[/Lab << /WhitePoint [0.9505 1 1.089] /Range [-100 100 -100 100] >>]"
_LAB_D50 = b"[/Lab << /WhitePoint [0.9642 1 0.8249] /Range [-128 127 -128 127] >>]"
_LAB_PIXELS = bytes([153, 178, 51, 76, 128, 128, 230, 100, 200, 128, 255, 0])
_SQUEEZED = b"/Decode [0 100 -10 10 -10 10]"
_SEPARATION = (
    b"[/Separation /Spot /DeviceRGB << /FunctionType 2 /Domain [0 1] "
    b"/C0 [1 1 1] /C1 [0 0.5 0] /N 1 >>]"
)


def _near(colour, expected, slack: int = 4) -> bool:
    return all(abs(a - b) <= slack for a, b in zip(colour, expected))


def _stream(body: bytes, dictionary: bytes = b"", *, compress: bool = False) -> bytes:
    if compress:
        body = zlib.compress(body)
        dictionary += b" /Filter /FlateDecode"
    return b"<< %s /Length %d >>\nstream\n" % (dictionary, len(body)) + body + b"\nendstream"


def _image(space: bytes, samples: bytes, extra: bytes = b"", *, size: int = 2) -> bytes:
    return _stream(
        samples,
        b"/Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace %s "
        b"/BitsPerComponent 8 %s" % (size, size, space, extra),
        compress=True,
    )


def _pdf(content: bytes, resources: bytes = b"", extra: dict | None = None) -> bytes:
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
        3: (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << "
            + resources
            + b" >> /Contents 4 0 R >>"
        ),
        4: _stream(content),
    }
    objects.update(extra or {})
    size = max(objects) + 1
    raw = bytearray(b"%PDF-1.7\n")
    offsets = {}
    for number in sorted(objects):
        offsets[number] = len(raw)
        raw += b"%d 0 obj\n" % number + objects[number] + b"\nendobj\n"
    start = len(raw)
    raw += b"xref\n0 %d\n" % size
    for number in range(size):
        raw += (b"%010d 00000 n \n" % offsets[number]) if number in offsets else b"0000000000 65535 f \n"
    raw += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (size, start)
    return bytes(raw)


def _rendered(data: bytes):
    raster = Document(io.BytesIO(data)).pages[0].render(dpi=72, antialias=False)
    return raster.get_pixel


def _image_page(space: bytes, samples: bytes, extra: bytes = b"") -> bytes:
    return _pdf(
        b"q 160 0 0 160 20 20 cm /Im Do Q",
        b"/XObject << /Im 20 0 R >>",
        {20: _image(space, samples, extra)},
    )


# The four image samples sit in these device pixels (top-left, top-right, ...).
_CELLS = ((60, 60), (140, 60), (60, 140), (140, 140))


# --- the conversion itself ------------------------------------------------------


def test_lab_white_black_and_grey_are_neutral():
    assert _lab_to_rgb(100, 0, 0) == (255, 255, 255)
    assert _lab_to_rgb(0, 0, 0) == (0, 0, 0)
    red, green, blue = _lab_to_rgb(50, 0, 0)
    assert red == green == blue
    # Near black, sRGB's linear segment: pdfium paints L* 1 as (3, 3, 3).
    assert _near(_lab_to_rgb(1, 0, 0), (3, 3, 3), slack=1)


def test_a_lab_colour_converts_as_pdfium_converts_it():
    assert _near(_lab_to_rgb(60, 40, -30), (190, 118, 198))
    assert _near(_lab_to_rgb(70, -50, 60), (106, 191, 48))


# --- fills and strokes ---------------------------------------------------------------


def test_a_lab_fill_paints_the_colour_not_its_components_as_rgb():
    pixel = _rendered(_pdf(b"/CS1 cs 60 40 -30 sc 20 20 160 160 re f", b"/ColorSpace << /CS1 " + _LAB_D65 + b" >>"))
    assert _near(pixel(100, 100), (190, 118, 198))


def test_a_lab_space_s_white_point_does_not_tint_it():
    # A D50 and a D65 space paint the same colour, and L* 100 is white in both.
    d65 = _rendered(_pdf(b"/CS1 cs 60 40 -30 sc 20 20 160 160 re f", b"/ColorSpace << /CS1 " + _LAB_D65 + b" >>"))
    d50 = _rendered(_pdf(b"/CS1 cs 60 40 -30 sc 20 20 160 160 re f", b"/ColorSpace << /CS1 " + _LAB_D50 + b" >>"))
    white = _rendered(
        _pdf(b"0 0 0 rg 0 0 200 200 re f /CS1 cs 100 0 0 sc 20 20 160 160 re f",
             b"/ColorSpace << /CS1 " + _LAB_D50 + b" >>")
    )
    assert d65(100, 100) == d50(100, 100)
    assert white(100, 100) == (255, 255, 255)


def test_a_lab_fill_is_not_clamped_to_the_range():
    # pdfium and MuPDF both paint a saturated red here, not the clamped (50 20 20).
    narrow = b"[/Lab << /WhitePoint [0.9505 1 1.089] /Range [-20 20 -20 20] >>]"
    pixel = _rendered(_pdf(b"/CS1 cs 50 90 90 sc 20 20 160 160 re f", b"/ColorSpace << /CS1 " + narrow + b" >>"))
    assert _near(pixel(100, 100), (255, 0, 0))


def test_a_lab_stroke_is_converted_too():
    pixel = _rendered(_pdf(b"/CS1 CS 70 -50 60 SC 20 w 20 100 m 180 100 l S", b"/ColorSpace << /CS1 " + _LAB_D65 + b" >>"))
    assert _near(pixel(100, 100), (106, 191, 48))


def test_a_lab_shading_is_converted_along_its_whole_length():
    shading = (
        b"/Shading << /Sh0 << /ShadingType 2 /ColorSpace " + _LAB_D65
        + b" /Coords [0 0 200 0] /Function << /FunctionType 2 /Domain [0 1] "
        b"/C0 [30 60 40] /C1 [90 -40 -50] /N 1 >> >> >>"
    )
    pixel = _rendered(_pdf(b"/Sh0 sh", shading))
    assert _near(pixel(10, 100), (158, 3, 23), slack=10)
    assert _near(pixel(190, 100), (0, 238, 255), slack=6)


# --- images ---------------------------------------------------------------------------


def test_a_lab_image_is_converted_sample_by_sample():
    pixel = _rendered(_image_page(_LAB_D65, _LAB_PIXELS))
    expected = ((147, 117, 255), (71, 70, 70), (218, 238, 77), (186, 0, 255))
    for cell, colour in zip(_CELLS, expected):
        assert _near(pixel(*cell), colour), (cell, pixel(*cell), colour)


def test_a_lab_image_without_decode_reads_a_and_b_as_the_byte_less_128():
    # Two images that differ only in /Range render the same, as in pdfium and
    # MuPDF -- neither takes Table 90's range-based default.
    d65 = _rendered(_image_page(_LAB_D65, _LAB_PIXELS))
    d50 = _rendered(_image_page(_LAB_D50, _LAB_PIXELS))
    assert [d65(*c) for c in _CELLS] == [d50(*c) for c in _CELLS]


def test_a_lab_image_s_own_decode_is_honoured():
    # With a* and b* squeezed towards zero every cell goes nearly grey.
    pixel = _rendered(_image_page(_LAB_D65, _LAB_PIXELS, _SQUEEZED))
    expected = ((148, 142, 155), (71, 70, 70), (230, 227, 216), (130, 114, 136))
    for cell, colour in zip(_CELLS, expected):
        assert _near(pixel(*cell), colour), (cell, pixel(*cell), colour)


def test_a_palette_over_lab_maps_its_bytes_through_the_base_range():
    narrow = b"[/Lab << /WhitePoint [0.9505 1 1.089] /Range [-40 40 -40 40] >>]"
    cases = (
        (_LAB_D65, ((154, 124, 251), (71, 70, 69), (223, 236, 114), (182, 0, 255))),
        (narrow, ((153, 136, 187), (71, 70, 70), (230, 230, 183), (153, 95, 188))),
    )
    for base, expected in cases:
        palette = b"[/Indexed " + base + b" 3 <" + _LAB_PIXELS.hex().encode() + b">]"
        pixel = _rendered(_image_page(palette, bytes([0, 1, 2, 3])))
        for cell, colour in zip(_CELLS, expected):
            assert _near(pixel(*cell), colour), (base, cell, pixel(*cell), colour)


def test_a_palette_image_s_decode_truncates_to_an_index():
    # Dmin + s * (Dmax - Dmin) / (2^bpc - 1), truncated: [1 0] maps sample 0 to
    # entry 1 and every other sample to entry 0; [3 0] maps sample 1 to 2.988,
    # entry 2. Rounding put every cell on one entry.
    device = b"[/Indexed /DeviceRGB 3 <FF0000 00FF00 0000FF 000000>]"
    spot = b"[/Indexed [/Separation /S /DeviceRGB << /FunctionType 2 /Domain [0 1] /C0 [1 0 0] /C1 [0 0 1] /N 1 >>] 3 <00 55 AA FF>]"
    cases = (
        (device, b"/Decode [1 0]", ((0, 255, 0), (255, 0, 0), (255, 0, 0), (255, 0, 0))),
        (device, b"/Decode [3 0]", ((0, 0, 0), (0, 0, 255), (0, 0, 255), (0, 0, 255))),
        (spot, b"/Decode [1 0]", ((170, 0, 85), (255, 0, 0), (255, 0, 0), (255, 0, 0))),
        (spot, b"/Decode [3 0]", ((0, 0, 255), (85, 0, 170), (85, 0, 170), (85, 0, 170))),
    )
    for space, decode, expected in cases:
        pixel = _rendered(_image_page(space, bytes([0, 1, 2, 3]), decode))
        for cell, colour in zip(_CELLS, expected):
            assert _near(pixel(*cell), colour, slack=2), (space[:20], decode, cell, pixel(*cell), colour)


def _packed_image_page(space: bytes, samples: bytes, bpc: int) -> bytes:
    image = _stream(
        samples,
        b"/Type /XObject /Subtype /Image /Width 2 /Height 2 /ColorSpace %s "
        b"/BitsPerComponent %d" % (space, bpc),
        compress=True,
    )
    return _pdf(b"q 160 0 0 160 20 20 cm /Im Do Q", b"/XObject << /Im 20 0 R >>", {20: image})


def test_sub_byte_samples_in_converted_spaces_span_their_bit_depth():
    # A 4-bit index 3 is the last palette entry, not entry 51 of a widened byte;
    # a 4-bit tint of 15 is full ink.
    palette = b"[/Indexed " + _LAB_D65 + b" 3 <" + _LAB_PIXELS.hex().encode() + b">]"
    spot_palette = b"[/Indexed " + _SEPARATION + b" 3 <00 55 AA FF>]"
    cases = (
        (palette, bytes([0x01, 0x23]), 4, ((154, 124, 251), (71, 70, 69), (223, 236, 114), (182, 0, 255))),
        (spot_palette, bytes([0b00010000, 0b10110000]), 2, ((255, 255, 255), (170, 213, 170), (85, 170, 85), (0, 128, 0))),
        (_SEPARATION, bytes([0x05, 0xAF]), 4, ((255, 255, 255), (170, 213, 170), (85, 170, 85), (0, 128, 0))),
    )
    for space, samples, bpc, expected in cases:
        pixel = _rendered(_packed_image_page(space, samples, bpc))
        for cell, colour in zip(_CELLS, expected):
            assert _near(pixel(*cell), colour, slack=4), (bpc, cell, pixel(*cell), colour)


def test_a_separation_image_goes_through_its_tint_transform():
    pixel = _rendered(_image_page(_SEPARATION, bytes([0, 85, 170, 255])))
    expected = ((255, 255, 255), (170, 213, 170), (85, 170, 85), (0, 128, 0))
    for cell, colour in zip(_CELLS, expected):
        assert _near(pixel(*cell), colour, slack=2), (cell, pixel(*cell), colour)


_DEVICE_N = b"[/DeviceN [/A /B] /DeviceRGB 21 0 R]"
_DEVICE_N_TINT = _stream(b"{ 0 3 1 roll }", b"/FunctionType 4 /Domain [0 1 0 1] /Range [0 1 0 1 0 1]")


def _devicen_page(space: bytes, samples: bytes) -> bytes:
    return _pdf(b"q 160 0 0 160 20 20 cm /Im Do Q", b"/XObject << /Im 20 0 R >>",
                {20: _image(space, samples), 21: _DEVICE_N_TINT})


def test_a_devicen_image_goes_through_its_tint_transform():
    pixel = _rendered(_devicen_page(_DEVICE_N, bytes([255, 0, 0, 255, 128, 128, 255, 255])))
    expected = ((0, 255, 0), (0, 0, 255), (0, 128, 128), (0, 255, 255))
    for cell, colour in zip(_CELLS, expected):
        assert _near(pixel(*cell), colour, slack=2), (cell, pixel(*cell), colour)


# --- the other outputs say the same ---------------------------------------------------------


def test_exporting_a_converted_image_writes_the_colours_the_page_shows(tmp_path):
    Image = pytest.importorskip("PIL.Image")

    palette = b"[/Indexed " + _LAB_D65 + b" 3 <" + _LAB_PIXELS.hex().encode() + b">]"
    devicen_palette = b"[/Indexed " + _DEVICE_N + b" 3 <FF00 00FF 8080 FFFF>]"
    cases = (
        ("lab", _image_page(_LAB_D65, _LAB_PIXELS)),
        ("lab_decode", _image_page(_LAB_D65, _LAB_PIXELS, _SQUEEZED)),
        ("lab_palette", _image_page(palette, bytes([0, 1, 2, 3]))),
        ("separation", _image_page(_SEPARATION, bytes([0, 85, 170, 255]))),
        ("device_n", _devicen_page(_DEVICE_N, bytes([255, 0, 0, 255, 128, 128, 255, 255]))),
        ("device_n_palette", _devicen_page(devicen_palette, bytes([0, 1, 2, 3]))),
    )
    for name, data in cases:
        path = tmp_path / f"{name}.pdf"
        path.write_bytes(data)
        page = Document(io.BytesIO(data)).pages[0].render(dpi=72, antialias=False)
        shown = [page.get_pixel(*cell) for cell in _CELLS]
        assert len(set(shown)) == 4, (name, shown)
        # Opened eagerly and streamed: the two find their images separately.
        with Document(str(path)) as eager, Document.open_streaming(path) as streamed:
            for mode, document in (("eager", eager), ("streamed", streamed)):
                engine = document._engine_pdf
                written = engine.save_image(next(iter(engine.images)), str(tmp_path / f"{name}-{mode}.png"))
                exported = Image.open(written).convert("RGB")
                pixels = [exported.getpixel(xy) for xy in ((0, 0), (1, 0), (0, 1), (1, 1))]
                assert pixels == shown, (name, mode, pixels, shown)


def _noisy_image(space: bytes, comps: int, extra: bytes = b"") -> bytes:
    import random

    random.seed(7)
    samples = bytearray()
    for y in range(96):
        for x in range(96):
            samples += bytes((40 + x + y * c) // 2 + random.randint(0, 12) for c in range(comps))
    return _pdf(b"q 160 0 0 160 20 20 cm /Im Do Q", b"/XObject << /Im 20 0 R >>",
                {20: _image(space, bytes(samples), extra, size=96)})


def _optimized(data: bytes) -> bytes:
    from aspose_pdf.optimization import OptimizationOptions

    document = Document(io.BytesIO(data))
    options = OptimizationOptions()
    options.image_compression_quality = 90
    document.optimize(options)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def test_optimize_keeps_a_converted_image_s_colours_when_it_recompresses_it():
    # A Lab image came out near black; a Separation or palette image with an
    # inverting /Decode came out inverted, mapped through the array on the way
    # to device colour and then inverted again.
    gradient = b"[/Indexed /DeviceRGB 255 <" + bytes(
        value for index in range(256) for value in (index, 255 - index, 128)
    ).hex().encode() + b">]"
    cases = (
        ("lab", _noisy_image(_LAB_D65, 3)),
        ("inverted separation", _noisy_image(_SEPARATION, 1, b"/Decode [1 0]")),
        ("inverted palette", _noisy_image(gradient, 1, b"/Decode [1 0]")),
    )
    for name, data in cases:
        optimized = _optimized(data)
        assert b"/DCTDecode" in optimized, (name, "the image was recompressed")
        assert b"/Decode" not in optimized, (name, "a Decode folded into the samples must go")
        before, after = _rendered(data), _rendered(optimized)
        for cell in ((40, 160), (100, 100), (160, 40)):
            assert _near(after(*cell), before(*cell), slack=12), (name, cell, before(*cell), after(*cell))


def test_a_spot_image_overprints_as_a_spot_fill_does():
    # The overprint preview mixes a spot colour into what is beneath; an image
    # in the same space, at the same tint, is mixed in the same way.
    resources = b"/ExtGState << /GS << /OP true /op true >> >> /ColorSpace << /CS1 " + _SEPARATION + b" >>"
    fill = _rendered(_pdf(b"1 0 0 rg 0 0 200 200 re f /GS gs /CS1 cs 1 sc 20 20 160 160 re f", resources))
    image = _rendered(
        _pdf(b"1 0 0 rg 0 0 200 200 re f /GS gs q 160 0 0 160 20 20 cm /Im Do Q",
             resources + b" /XObject << /Im 20 0 R >>", {20: _image(_SEPARATION, bytes([255] * 4))})
    )
    assert image(100, 100) == fill(100, 100) != (0, 128, 0)

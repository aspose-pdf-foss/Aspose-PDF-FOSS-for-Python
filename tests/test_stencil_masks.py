"""A stencil mask is a shape, and ``/Decode`` says what a sample means.

Two halves of one rule, both missing.

An image with ``/ImageMask true`` is not a picture (ISO 32000-1 8.9.6.2): its
one-bit samples choose where the *colour already set* is painted and where the
page shows through. The renderer decoded it as a one-bit grey image instead, so
every stencil came out black on white whatever colour was in force -- and a
stencil is how scanned text, logos and Type 3 glyph bitmaps are drawn.

``/Decode`` (8.9.5.2) is what maps a sample onto that colour space, and it is
what tells the two stencil senses apart: the default ``[0 1]`` paints sample 0,
``[1 0]`` paints sample 1. The renderer collected the array into its metadata
and then never looked at it, so an inverted image rendered un-inverted; the
image exporter honoured it for one grey component and ignored it for RGB, CMYK
and Indexed.
"""

from __future__ import annotations

import base64
import io
import re
import zlib

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.image_export import (
    apply_decode,
    decode_indices,
    decode_tables,
    stencil_coverage,
    stencil_paints_high_sample,
)

# Top-left and bottom-right set; the other two clear.
_BITS = bytes([0b10000000, 0b01000000])
_RGB = bytes([255, 0, 0, 0, 255, 0, 0, 0, 255, 255, 255, 255])
_GRAY = bytes([0, 85, 170, 255])


def _document(
    content: bytes,
    image: bytes,
    xobjects: bytes = b"",
    extra: dict[int, bytes] | None = None,
    extra_resources: bytes = b"",
) -> Document:
    """A 200x200 page whose only mark is *content*, with ``/Im1`` as *image*."""
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << "
            b"/XObject << /Im1 5 0 R "
            + xobjects
            + b">> "
            + extra_resources
            + b">> /Contents 4 0 R >>"
        ),
        4: b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        5: image,
        **(extra or {}),
    }
    raw = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = {}
    for number in sorted(objects):
        offsets[number] = len(raw)
        raw += b"%d 0 obj\n" % number + objects[number] + b"\nendobj\n"
    start = len(raw)
    raw += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for number in sorted(objects):
        raw += b"%010d 00000 n \n" % offsets[number]
    raw += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        start,
    )
    return Document(io.BytesIO(bytes(raw)))


def _image(dictionary: bytes, data: bytes) -> bytes:
    return (
        b"<< /Type /XObject /Subtype /Image "
        + dictionary
        + b" /Length %d >>\nstream\n" % len(data)
        + data
        + b"\nendstream"
    )


def _draw(colour: bytes = b"0 0 1 rg") -> bytes:
    return colour + b"\nq 100 0 0 100 50 50 cm /Im1 Do Q\n"


#: The centre of each quadrant of the 100x100 placement, in device pixels.
_QUADRANTS = {
    "top_left": (75, 75),
    "top_right": (125, 75),
    "bottom_left": (75, 125),
    "bottom_right": (125, 125),
}


def _quadrants(doc: Document) -> dict[str, tuple[int, int, int]]:
    raster = doc.pages[0].render(antialias=False)
    return {name: raster.get_pixel(*at) for name, at in _QUADRANTS.items()}


# --- a stencil paints the colour that is set --------------------------------

_BLUE = (0, 0, 255)
_WHITE = (255, 255, 255)


def test_a_stencil_paints_the_current_fill_colour():
    doc = _document(_draw(), _image(b"/Width 2 /Height 2 /ImageMask true", _BITS))
    assert _quadrants(doc) == {
        "top_left": _WHITE,  # sample 1: masked out, the page shows through
        "top_right": _BLUE,  # sample 0: painted
        "bottom_left": _BLUE,
        "bottom_right": _WHITE,
    }


@pytest.mark.parametrize(
    ("colour", "expected"),
    [
        (b"1 0 0 rg", (255, 0, 0)),
        (b"0 g", (0, 0, 0)),
        (b"1 g", _WHITE),
        (b"0 1 1 0 k", (255, 0, 0)),
    ],
)
def test_a_stencil_follows_whatever_colour_is_in_force(colour, expected):
    doc = _document(_draw(colour), _image(b"/Width 2 /Height 2 /ImageMask true", _BITS))
    assert _quadrants(doc)["top_right"] == expected


def test_the_default_decode_paints_the_zero_samples():
    plain = _document(_draw(), _image(b"/Width 2 /Height 2 /ImageMask true", _BITS))
    spelled = _document(
        _draw(), _image(b"/Width 2 /Height 2 /ImageMask true /Decode [0 1]", _BITS)
    )
    assert _quadrants(plain) == _quadrants(spelled)
    assert _quadrants(plain)["top_right"] == _BLUE


def test_decode_one_zero_paints_the_other_samples():
    doc = _document(
        _draw(), _image(b"/Width 2 /Height 2 /ImageMask true /Decode [1 0]", _BITS)
    )
    assert _quadrants(doc) == {
        "top_left": _BLUE,
        "top_right": _WHITE,
        "bottom_left": _WHITE,
        "bottom_right": _BLUE,
    }


def test_a_masked_out_sample_leaves_what_was_under_it():
    # Not white by luck: a stencil's clear samples paint nothing at all, so
    # whatever was drawn first stays visible.
    doc = _document(
        b"0 1 0 rg 50 50 100 100 re f\n" + _draw(),
        _image(b"/Width 2 /Height 2 /ImageMask true", _BITS),
    )
    quadrants = _quadrants(doc)
    assert quadrants["top_left"] == (0, 255, 0)
    assert quadrants["top_right"] == _BLUE


def test_a_stencil_overprints_like_the_colour_it_paints_with():
    # `color_kind=None` is not a shrug: it says "the fill's own kind", which is
    # what decides whether a spot colour overprints the backdrop instead of
    # covering it. Calling the stencil grey would quietly turn that off.
    separation = (
        b"/Separation /Spot /DeviceCMYK << /FunctionType 2 /Domain [0 1] "
        b"/C0 [0 0 0 0] /C1 [0 1 0 0] /N 1 >>"
    )
    doc = _document(
        b"1 0 0 0 k 50 50 100 100 re f\n"
        b"/CS0 cs 1 scn /GS1 gs\nq 100 0 0 100 50 50 cm /Im1 Do Q\n",
        _image(b"/Width 2 /Height 2 /ImageMask true", _BITS),
        extra_resources=b"/ColorSpace << /CS0 [" + separation + b"] >> "
        b"/ExtGState << /GS1 << /OP true /op true >> >> ",
    )
    # Magenta overprinting cyan keeps the cyan: blue, not magenta.
    assert _quadrants(doc)["top_right"] == (0, 0, 255)
    assert _quadrants(doc)["top_left"] == (0, 255, 255)  # backdrop, unmasked


def test_a_filtered_stencil_paints_the_same():
    doc = _document(
        _draw(),
        _image(
            b"/Width 2 /Height 2 /ImageMask true /Filter /FlateDecode",
            zlib.compress(_BITS),
        ),
    )
    assert _quadrants(doc)["top_right"] == _BLUE


def test_an_inline_stencil_paints_the_same():
    doc = _document(
        b"0 0 1 rg\nq 100 0 0 100 50 50 cm BI /W 2 /H 2 /IM true ID "
        + _BITS
        + b" EI Q\n",
        _image(b"/Width 2 /Height 2 /ImageMask true", _BITS),
    )
    assert _quadrants(doc)["top_right"] == _BLUE


def test_a_stencil_takes_the_colour_set_outside_the_form_it_is_in():
    inner = b"/Im1 Do"
    doc = _document(
        b"1 0 1 rg\nq 100 0 0 100 50 50 cm /Fm1 Do Q\n",
        _image(b"/Width 2 /Height 2 /ImageMask true", _BITS),
        xobjects=b"/Fm1 6 0 R ",
        extra={
            6: (
                b"<< /Type /XObject /Subtype /Form /BBox [0 0 1 1] /Resources << "
                b"/XObject << /Im1 5 0 R >> >> /Length %d >>\nstream\n"
                % len(inner)
                + inner
                + b"\nendstream"
            )
        },
    )
    assert _quadrants(doc)["top_right"] == (255, 0, 255)


def _png_rgba(data: bytes) -> tuple[int, int, list[tuple[int, int, int, int]]]:
    """Decode a small RGBA PNG into its pixels."""
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    pos, width, height, idat = 8, 0, 0, bytearray()
    while pos < len(data):
        length = int.from_bytes(data[pos : pos + 4], "big")
        tag = data[pos + 4 : pos + 8]
        payload = data[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if tag == b"IHDR":
            width = int.from_bytes(payload[0:4], "big")
            height = int.from_bytes(payload[4:8], "big")
            assert payload[8] == 8 and payload[9] == 6  # 8-bit truecolour + alpha
        elif tag == b"IDAT":
            idat += payload
        elif tag == b"IEND":
            break
    raw = zlib.decompress(bytes(idat))
    stride = width * 4
    pixels = []
    for y in range(height):
        row = raw[y * (stride + 1) : (y + 1) * (stride + 1)]
        assert row[0] == 0  # filter type None
        pixels += [tuple(row[1 + x * 4 : 5 + x * 4]) for x in range(width)]
    return width, height, pixels


def test_the_svg_export_paints_a_stencil_in_the_fill_colour_too():
    doc = _document(_draw(), _image(b"/Width 2 /Height 2 /ImageMask true", _BITS))
    svg = doc.pages[0].to_svg()
    href = re.search(r'xlink:href="data:image/png;base64,([^"]+)"', svg)
    assert href is not None
    width, height, pixels = _png_rgba(base64.b64decode(href.group(1)))
    assert (width, height) == (2, 2)
    # The fill colour where the stencil paints, and *transparent* where it does
    # not -- an opaque white there would hide whatever is underneath.
    clear = (0, 0, 0, 0)
    assert pixels == [clear, (0, 0, 255, 255), (0, 0, 255, 255), clear]


# --- /Decode maps a sample onto its colour space ----------------------------


def test_decode_inverts_a_grey_image():
    doc = _document(
        _draw(),
        _image(
            b"/Width 2 /Height 2 /BitsPerComponent 8 /ColorSpace /DeviceGray "
            b"/Decode [1 0]",
            _GRAY,
        ),
    )
    assert _quadrants(doc) == {
        "top_left": _WHITE,
        "top_right": (170, 170, 170),
        "bottom_left": (85, 85, 85),
        "bottom_right": (0, 0, 0),
    }


def test_decode_inverts_an_rgb_image_component_by_component():
    doc = _document(
        _draw(),
        _image(
            b"/Width 2 /Height 2 /BitsPerComponent 8 /ColorSpace /DeviceRGB "
            b"/Decode [1 0 1 0 1 0]",
            _RGB,
        ),
    )
    assert _quadrants(doc) == {
        "top_left": (0, 255, 255),
        "top_right": (255, 0, 255),
        "bottom_left": (255, 255, 0),
        "bottom_right": (0, 0, 0),
    }


def test_decode_can_map_one_component_and_leave_the_others():
    doc = _document(
        _draw(),
        _image(
            b"/Width 2 /Height 2 /BitsPerComponent 8 /ColorSpace /DeviceRGB "
            b"/Decode [1 0 0 1 0 1]",
            _RGB,
        ),
    )
    quadrants = _quadrants(doc)
    assert quadrants["top_left"] == (0, 0, 0)  # red only, and red is inverted
    assert quadrants["top_right"] == (255, 255, 0)


def test_decode_applies_below_eight_bits_a_component():
    doc = _document(
        _draw(),
        _image(
            b"/Width 2 /Height 2 /BitsPerComponent 4 /ColorSpace /DeviceGray "
            b"/Decode [1 0]",
            bytes([0x0F, 0x8C]),
        ),
    )
    assert _quadrants(doc)["top_left"] == _WHITE  # sample 0 -> 1.0
    assert _quadrants(doc)["top_right"] == (0, 0, 0)  # sample 15 -> 0.0


def test_an_indexed_decode_remaps_the_index_not_the_colour():
    # /Indexed samples are palette *indices*, so their default range is
    # [0, 2**bpc - 1] rather than [0 1]: [3 0] reverses the palette.
    palette = b"<FF000000FF000000FFFFFFFF>"
    samples = bytes([0b00011011, 0b11100100])
    plain = _document(
        _draw(),
        _image(
            b"/Width 2 /Height 2 /BitsPerComponent 2 "
            b"/ColorSpace [/Indexed /DeviceRGB 3 " + palette + b"]",
            samples,
        ),
    )
    reversed_ = _document(
        _draw(),
        _image(
            b"/Width 2 /Height 2 /BitsPerComponent 2 "
            b"/ColorSpace [/Indexed /DeviceRGB 3 " + palette + b"] /Decode [3 0]",
            samples,
        ),
    )
    assert _quadrants(plain)["top_left"] == (255, 0, 0)  # index 0
    assert _quadrants(reversed_)["top_left"] == _WHITE  # index 3


def test_a_default_decode_array_changes_nothing():
    plain = _document(
        _draw(),
        _image(b"/Width 2 /Height 2 /BitsPerComponent 8 /ColorSpace /DeviceRGB", _RGB),
    )
    spelled = _document(
        _draw(),
        _image(
            b"/Width 2 /Height 2 /BitsPerComponent 8 /ColorSpace /DeviceRGB "
            b"/Decode [0 1 0 1 0 1]",
            _RGB,
        ),
    )
    assert _quadrants(plain) == _quadrants(spelled)


# --- and the exporter reads it the same way ---------------------------------


def test_extracting_an_image_honours_decode(tmp_path):
    from PIL import Image

    from aspose_pdf.images import ImagePlacementAbsorber

    pytest.importorskip("PIL")
    doc = _document(
        _draw(),
        _image(
            b"/Width 2 /Height 2 /BitsPerComponent 8 /ColorSpace /DeviceRGB "
            b"/Decode [1 0 1 0 1 0]",
            _RGB,
        ),
    )
    absorber = ImagePlacementAbsorber()
    absorber.visit(doc)
    out = absorber.image_placements[0].save(tmp_path / "out.png")
    pixels = Image.open(out).convert("RGB")
    assert pixels.getpixel((0, 0)) == (0, 255, 255)
    assert pixels.getpixel((1, 1)) == (0, 0, 0)


def test_extracting_an_indexed_image_honours_its_index_space_decode(tmp_path):
    from PIL import Image

    from aspose_pdf.images import ImagePlacementAbsorber

    doc = _document(
        _draw(),
        _image(
            b"/Width 2 /Height 2 /BitsPerComponent 2 /ColorSpace "
            b"[/Indexed /DeviceRGB 3 <FF000000FF000000FFFFFFFF>] /Decode [3 0]",
            bytes([0b00011011, 0b11100100]),
        ),
    )
    absorber = ImagePlacementAbsorber()
    absorber.visit(doc)
    out = absorber.image_placements[0].save(tmp_path / "indexed.png")
    pixels = Image.open(out).convert("RGB")
    assert pixels.getpixel((0, 0)) == (255, 255, 255)  # index 0 -> 3
    assert pixels.getpixel((1, 1)) == (0, 255, 0)


# --- the rule itself --------------------------------------------------------


def test_a_default_decode_array_needs_no_table():
    assert decode_tables([0, 1, 0, 1, 0, 1], 3) is None
    assert decode_tables(None, 3) is None
    assert decode_tables([0, 1], 3) is None  # too short to be about three
    assert decode_tables([0, 1, 0, 1], 3) is None  # four numbers, not six


def test_a_partial_decode_array_makes_a_table_only_where_it_differs():
    tables = decode_tables([1, 0, 0, 1, 0, 1], 3)
    assert tables is not None
    assert tables[1] is None and tables[2] is None
    assert tables[0][0] == 255 and tables[0][255] == 0


def test_apply_decode_walks_the_components_round_robin():
    assert apply_decode(bytes([255, 0, 0, 0, 255, 0]), [1, 0, 1, 0, 1, 0], 3) == bytes(
        [0, 255, 255, 255, 0, 255]
    )


def test_a_decode_range_narrower_than_the_default_compresses_the_samples():
    # [0 0.5] halves everything rather than inverting it.
    assert apply_decode(bytes([0, 128, 255]), [0, 0.5], 1) == bytes([0, 64, 128])


def test_decode_indices_defaults_to_the_whole_index_range():
    assert decode_indices([0, 1, 2, 3], [0, 3], 2) == [0, 1, 2, 3]
    assert decode_indices([0, 1, 2, 3], None, 2) == [0, 1, 2, 3]
    assert decode_indices([0, 1, 2, 3], [3, 0], 2) == [3, 2, 1, 0]
    # [0 1] is the *colour* default and means something else here: it squeezes
    # four indices into the first two entries of the palette, truncating as
    # pdfium does -- only the top sample reaches entry 1.
    assert decode_indices([0, 1, 2, 3], [0, 1], 2) == [0, 0, 0, 1]


def test_which_sample_a_stencil_paints():
    assert stencil_paints_high_sample(None) is False
    assert stencil_paints_high_sample([0, 1]) is False
    assert stencil_paints_high_sample([1, 0]) is True


def test_stencil_coverage_pads_each_row_to_a_byte():
    # Three pixels a row: one byte each, the last five bits ignored.
    assert stencil_coverage(bytes([0b10100000, 0b01000000]), 3, 2, None) == [
        0,
        1,
        0,
        1,
        0,
        1,
    ]

"""``/Mask``: the two ways an image says which of its pixels do not paint.

ISO 32000-1 8.9.6 gives an image three ways to be transparent, and it may use
one. ``/SMask`` -- a greyscale image stating alpha outright -- was implemented.
``/Mask`` was not, in either of its forms, so a logo drawn with a masked-out
background covered the page with an opaque box:

* naming a **stencil**, whose set samples are the ones *not* to paint. The
  sense is the stencil's own ``/Decode``'s to state, and the stencil is sampled
  over the same unit square as the image, at whatever resolution it has;
* holding an **array**, which is colour-key masking (8.9.6.4): ``2 x n``
  bounds on the *raw* sample values, and a pixel is dropped only when **every**
  component falls inside its own range.

All three end as the same thing -- a per-pixel alpha map over the image's unit
square -- which is why the painter needs to know nothing about which it was.
"""

from __future__ import annotations

import base64
import io
import re
import zlib

import pytest

from aspose_pdf import Document

# 2x2 RGB: red, green / blue, white.
_RGB = bytes([255, 0, 0, 0, 255, 0, 0, 0, 255, 255, 255, 255])
# 2x2 one bit, top-left and bottom-right set.
_BITS = bytes([0b10000000, 0b01000000])

_RED = (255, 0, 0)
_GREEN = (0, 255, 0)
_BLUE = (0, 0, 255)
_WHITE = (255, 255, 255)


def _image(dictionary: bytes, data: bytes) -> bytes:
    return (
        b"<< /Type /XObject /Subtype /Image "
        + dictionary
        + b" /Length %d >>\nstream\n" % len(data)
        + data
        + b"\nendstream"
    )


def _document(base: bytes, extra: dict[int, bytes] | None = None) -> Document:
    """A page painting a green square, then ``/Im1`` over it.

    The square underneath is what makes a masked-out pixel visible as
    something other than the page: green where the image does not reach.
    """
    content = b"0 1 0 rg 50 50 100 100 re f\nq 100 0 0 100 50 50 cm /Im1 Do Q\n"
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources "
            b"<< /XObject << /Im1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        4: b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        5: base,
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


_QUADRANTS = {
    "top_left": (75, 75),
    "top_right": (125, 75),
    "bottom_left": (75, 125),
    "bottom_right": (125, 125),
}


def _quadrants(doc: Document) -> dict[str, tuple[int, int, int]]:
    raster = doc.pages[0].render(antialias=False)
    return {name: raster.get_pixel(*at) for name, at in _QUADRANTS.items()}


_PLAIN = b"/Width 2 /Height 2 /BitsPerComponent 8 /ColorSpace /DeviceRGB"


def test_without_a_mask_the_whole_image_paints():
    doc = _document(_image(_PLAIN, _RGB))
    assert _quadrants(doc) == {
        "top_left": _RED,
        "top_right": _GREEN,
        "bottom_left": _BLUE,
        "bottom_right": _WHITE,
    }


# --- /Mask naming a stencil -------------------------------------------------


def test_a_stencils_set_samples_are_the_ones_not_painted():
    doc = _document(
        _image(_PLAIN + b" /Mask 6 0 R", _RGB),
        {6: _image(b"/Width 2 /Height 2 /ImageMask true", _BITS)},
    )
    assert _quadrants(doc) == {
        "top_left": _GREEN,  # sample 1: masked, the square shows through
        "top_right": _GREEN,  # sample 0: painted -- and the image is green here
        "bottom_left": _BLUE,  # sample 0: painted
        "bottom_right": _GREEN,  # sample 1: masked
    }


def test_the_stencils_own_decode_states_which_samples_mask():
    doc = _document(
        _image(_PLAIN + b" /Mask 6 0 R", _RGB),
        {6: _image(b"/Width 2 /Height 2 /ImageMask true /Decode [1 0]", _BITS)},
    )
    assert _quadrants(doc) == {
        "top_left": _RED,
        "top_right": _GREEN,
        "bottom_left": _GREEN,
        "bottom_right": _WHITE,
    }


def test_a_stencil_is_sampled_at_its_own_resolution():
    # Four by four over a two by two image: the mask is stretched over the same
    # unit square, not matched up sample for sample.
    doc = _document(
        _image(_PLAIN + b" /Mask 6 0 R", _RGB),
        {
            6: _image(
                b"/Width 4 /Height 4 /ImageMask true",
                bytes([0b11000000, 0b11000000, 0b00110000, 0b00110000]),
            )
        },
    )
    assert _quadrants(doc) == {
        "top_left": _GREEN,
        "top_right": _GREEN,
        "bottom_left": _BLUE,
        "bottom_right": _GREEN,
    }


def test_a_filtered_stencil_masks_the_same():
    doc = _document(
        _image(_PLAIN + b" /Mask 6 0 R", _RGB),
        {
            6: _image(
                b"/Width 2 /Height 2 /ImageMask true /Filter /FlateDecode",
                zlib.compress(_BITS),
            )
        },
    )
    assert _quadrants(doc)["top_left"] == _GREEN


def test_a_mask_stream_that_does_not_declare_imagemask_is_not_a_stencil():
    # Table 89 requires /ImageMask true. Without it nothing says the samples
    # are a stencil rather than a picture, and reading a picture as bits would
    # mask the image by its own colours.
    doc = _document(
        _image(_PLAIN + b" /Mask 6 0 R", _RGB),
        {
            6: _image(
                b"/Width 2 /Height 2 /BitsPerComponent 1 /ColorSpace /DeviceGray",
                _BITS,
            )
        },
    )
    assert _quadrants(doc)["top_left"] == _RED


def test_a_decode_on_the_base_image_does_not_move_the_mask():
    doc = _document(
        _image(_PLAIN + b" /Decode [1 0 1 0 1 0] /Mask 6 0 R", _RGB),
        {6: _image(b"/Width 2 /Height 2 /ImageMask true", _BITS)},
    )
    assert _quadrants(doc) == {
        "top_left": _GREEN,  # masked, whatever colour it would have been
        "top_right": (255, 0, 255),  # green inverted
        "bottom_left": (255, 255, 0),  # blue inverted
        "bottom_right": _GREEN,
    }


# --- /Mask holding colour-key ranges ----------------------------------------


def test_colour_key_drops_the_samples_inside_every_range():
    doc = _document(_image(_PLAIN + b" /Mask [250 255 250 255 250 255]", _RGB))
    assert _quadrants(doc) == {
        "top_left": _RED,
        "top_right": _GREEN,
        "bottom_left": _BLUE,
        "bottom_right": _GREEN,  # white, and white was the key
    }


def test_colour_key_needs_every_component_in_range():
    # Red is (255, 0, 0): in range for red and blue, out of it for green, so
    # it paints. A per-component "any" rule would have dropped it.
    doc = _document(_image(_PLAIN + b" /Mask [250 255 250 255 0 5]", _RGB))
    assert _quadrants(doc)["top_left"] == _RED


def test_colour_key_reads_the_raw_samples_not_the_decoded_ones():
    # /Decode inverts what is painted; the key still names the samples as they
    # are stored, so it is the red pixel that goes.
    doc = _document(
        _image(_PLAIN + b" /Decode [1 0 1 0 1 0] /Mask [250 255 0 5 0 5]", _RGB)
    )
    assert _quadrants(doc)["top_left"] == _GREEN


@pytest.mark.parametrize(
    ("bits", "mask", "data", "keyed", "kept"),
    [
        # One bit: samples 1, 0 / 0, 1 -- key the set ones.
        (1, b"[1 1]", _BITS, "top_left", ("top_right", (0, 0, 0))),
        # Four bits, two samples to the byte: 0, 15 / 8, 12 -- key the white.
        (4, b"[15 15]", bytes([0x0F, 0x8C]), "top_right", ("top_left", (0, 0, 0))),
    ],
)
def test_colour_key_works_below_eight_bits(bits, mask, data, keyed, kept):
    doc = _document(
        _image(
            b"/Width 2 /Height 2 /BitsPerComponent %d /ColorSpace /DeviceGray "
            b"/Mask %s" % (bits, mask),
            data,
        )
    )
    quadrants = _quadrants(doc)
    assert quadrants[keyed] == _GREEN  # dropped: the square shows through
    assert quadrants[kept[0]] == kept[1]


def test_colour_key_on_an_indexed_image_names_index_values():
    doc = _document(
        _image(
            b"/Width 2 /Height 2 /BitsPerComponent 2 /ColorSpace "
            b"[/Indexed /DeviceRGB 3 <FF000000FF000000FFFFFFFF>] /Mask [0 0]",
            bytes([0b00011011, 0b11100100]),
        )
    )
    assert _quadrants(doc)["top_left"] == _GREEN  # index 0 keyed out
    assert _quadrants(doc)["top_right"] == _GREEN  # index 1 is green anyway
    assert _quadrants(doc)["bottom_left"] == _WHITE


def test_an_array_too_short_for_the_components_is_not_a_colour_key():
    doc = _document(_image(_PLAIN + b" /Mask [0 0]", _RGB))
    assert _quadrants(doc)["top_left"] == _RED


def test_a_lossily_coded_image_is_left_unmasked():
    # The samples a colour key names are still a codestream at this point, so
    # keying them would key the compressed bytes -- and 8.9.6.4 advises against
    # keying a lossily coded image in the first place. The key here spans every
    # value, so reading it at all would drop the whole picture.
    from aspose_pdf.engine.jpeg_encoder import encode

    jpeg = encode(2, 2, 3, _RGB, quality=95)
    doc = _document(
        _image(
            b"/Width 2 /Height 2 /BitsPerComponent 8 /ColorSpace /DeviceRGB "
            b"/Filter /DCTDecode /Mask [0 255 0 255 0 255]",
            jpeg,
        )
    )
    quadrants = _quadrants(doc)
    assert quadrants["top_left"] != _GREEN  # the image is still there
    assert quadrants["bottom_left"] != _GREEN


# --- one alpha map, whichever it came from ----------------------------------


def test_smask_wins_when_a_file_carries_both():
    # 8.9.6: an image uses one of the three. Where a producer writes /SMask and
    # /Mask both, /SMask is the one that counts.
    doc = _document(
        _image(_PLAIN + b" /SMask 6 0 R /Mask 7 0 R", _RGB),
        {
            6: _image(
                b"/Width 2 /Height 2 /BitsPerComponent 8 /ColorSpace /DeviceGray",
                bytes([255, 0, 0, 255]),
            ),
            7: _image(b"/Width 2 /Height 2 /ImageMask true", _BITS),
        },
    )
    assert _quadrants(doc) == {
        "top_left": _RED,  # /SMask says opaque; /Mask would have hidden it
        "top_right": _GREEN,
        "bottom_left": _GREEN,
        "bottom_right": _WHITE,
    }


def _svg_mask(doc: Document) -> tuple[int, int, list[list[int]]] | None:
    """The alpha map the SVG export emits for the page's masked image."""
    svg = doc.pages[0].to_svg()
    found = re.search(
        r'<mask[^>]*>.*?xlink:href="data:image/png;base64,([^"]+)".*?</mask>',
        svg,
        re.S,
    )
    if found is None:
        return None
    png = base64.b64decode(found.group(1))
    pos, width, height, idat = 8, 0, 0, bytearray()
    while pos < len(png):
        length = int.from_bytes(png[pos : pos + 4], "big")
        tag = png[pos + 4 : pos + 8]
        payload = png[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if tag == b"IHDR":
            width = int.from_bytes(payload[0:4], "big")
            height = int.from_bytes(payload[4:8], "big")
        elif tag == b"IDAT":
            idat += payload
        elif tag == b"IEND":
            break
    raw = zlib.decompress(bytes(idat))
    rows = [
        list(raw[y * (width + 1) + 1 : (y + 1) * (width + 1)]) for y in range(height)
    ]
    return width, height, rows


def test_the_svg_export_carries_a_stencil_mask_as_alpha():
    doc = _document(
        _image(_PLAIN + b" /Mask 6 0 R", _RGB),
        {6: _image(b"/Width 2 /Height 2 /ImageMask true", _BITS)},
    )
    assert _svg_mask(doc) == (2, 2, [[0, 255], [255, 0]])


def test_the_svg_export_carries_a_colour_key_as_alpha():
    doc = _document(_image(_PLAIN + b" /Mask [250 255 250 255 250 255]", _RGB))
    assert _svg_mask(doc) == (2, 2, [[255, 255], [255, 0]])


def test_the_svg_export_emits_no_mask_for_an_unmasked_image():
    assert _svg_mask(_document(_image(_PLAIN, _RGB))) is None

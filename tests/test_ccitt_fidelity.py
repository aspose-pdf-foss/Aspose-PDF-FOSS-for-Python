"""A CCITT-compressed image must come back as the bitmap that was encoded.

The Group 4 decoder could follow almost nothing: of ten bitmap patterns encoded
by libtiff, **one** decoded -- the all-black one, where a single run per row made
the errors invisible. Everything else raised or came out wrong, and Group 3
(``K >= 0``) was not decoded at all: a fast path returned the *encoded* bytes as
if they were the bitmap.

Two root causes, both in the data rather than the loop:

* ``ccitt_tables.py`` was wrong. The black table was largely a copy of the white
  one -- black run 8 carried the white run 8 code word, the black 64 make-up
  code was the white one -- and both tables had codes that prefixed other codes,
  which cannot happen in a Huffman set and makes a one-bit-at-a-time read stop
  early. The tables are regenerated from the ITU-T T.4 code words, and
  ``test_ccitt_decoder.py`` now asserts prefix-freeness rather than trusting it.
* The 2-D mode decoder understood V0, VR1, VL1, horizontal and pass, and treated
  everything starting ``0000`` as an unsupported extension -- so VR2, VL2, VR3
  and VL3 could never be read. ``b1`` was "the first pixel of the opposite
  colour" instead of "the first *changing element* of the opposite colour",
  which differs whenever a0 lands inside a run.

The decoder also carried no code words for runs of 1792-2560 pixels, which every
page scanned above 300 dpi needs.

Measured against MuPDF, pdfium and poppler: on twenty hand-built documents (ten
bitmaps x both ``/BlackIs1`` values) the three agreed with each other and with
the source on all twenty, and we agreed on two. All four now agree on all
twenty. These tests encode the same comparison without the reference engines,
against bitstreams built here from the published code words.
"""

from __future__ import annotations

import pytest

from aspose_pdf.engine.ccitt import Decoder
from aspose_pdf.engine.ccitt_tables import (
    BLACK_MAKEUP,
    BLACK_TERM,
    EXTENDED_MAKEUP,
    WHITE_MAKEUP,
    WHITE_TERM,
)
from aspose_pdf.engine.filters import StreamDecoder
from aspose_pdf.exceptions import PdfValidationException

# --- a Group 3 / Group 4 encoder, so a bitmap can be put in and taken out ----


class _BitWriter:
    def __init__(self) -> None:
        self.bits: list[str] = []

    def code(self, value: int, length: int) -> None:
        self.bits.append(format(value, f"0{length}b"))

    def raw(self, text: str) -> None:
        self.bits.append(text)

    def bytes(self) -> bytes:
        joined = "".join(self.bits)
        joined += "0" * (-len(joined) % 8)
        return bytes(int(joined[i : i + 8], 2) for i in range(0, len(joined), 8))


def _run_codes(writer: _BitWriter, run: int, color: int) -> None:
    terminating = WHITE_TERM if color == 0 else BLACK_TERM
    makeups = dict(WHITE_MAKEUP if color == 0 else BLACK_MAKEUP)
    # The 1792-2560 codes are shared by both colours and are the only way to
    # code a run that long in one go, so the encoder reaches for them too.
    makeups.update(EXTENDED_MAKEUP)
    while run >= 64:
        makeup = max(length for length in makeups if length <= run)
        writer.code(*makeups[makeup])
        run -= makeup
    writer.code(*terminating[run])


def _reference_b1_b2(reference: list[int], a0: int, color: int, cols: int):
    """``b1``/``b2``, worked out here rather than asked of the decoder.

    An encoder that called the decoder's own helper would agree with it however
    wrong it was -- which is exactly what let a mutation of that helper survive.
    """
    index = 0
    while index < len(reference) and reference[index] <= a0:
        index += 1
    if (index & 1) != color:
        index += 1
    b1 = reference[index] if index < len(reference) else cols
    b2 = reference[index + 1] if index + 1 < len(reference) else cols
    return b1, b2


def _changes(row: list[int]) -> list[int]:
    """The positions where *row* changes colour, as the standard counts them."""
    out = []
    previous = 0
    for index, pixel in enumerate(row):
        if pixel != previous:
            out.append(index)
            previous = pixel
    return out


def _encode_1d(bitmap: list[list[int]]) -> bytes:
    """Modified Huffman (``K = 0``). *bitmap* holds 1 for black."""
    writer = _BitWriter()
    cols = len(bitmap[0])
    for row in bitmap:
        position = 0
        color = 0
        for change in [*_changes(row), cols]:
            _run_codes(writer, change - position, color)
            position = change
            color ^= 1
            if position >= cols:
                break
    return writer.bytes()


_VERTICAL = {
    0: "1",
    1: "011",
    -1: "010",
    2: "000011",
    -2: "000010",
    3: "0000011",
    -3: "0000010",
}


def _encode_2d(bitmap: list[list[int]]) -> bytes:
    """Modified Modified READ (``K < 0``), choosing modes as T.6 clause 2 does."""
    writer = _BitWriter()
    cols = len(bitmap[0])
    reference: list[int] = []
    for row in bitmap:
        current = _changes(row)
        a0 = -1
        color = 0
        while a0 < cols:
            b1, b2 = _reference_b1_b2(reference, a0, color, cols)
            a1 = next((c for c in current if c > a0), cols)
            if b2 < a1:
                # The reference line changes twice before the coding line
                # changes at all: pass mode, and a0 jumps to b2 with no
                # changing element of its own.
                writer.raw("0001")
                a0 = b2
                continue
            if abs(a1 - b1) <= 3:
                writer.raw(_VERTICAL[a1 - b1])
                a0 = a1
                color ^= 1
                continue
            a2 = next((c for c in current if c > a1), cols)
            writer.raw("001")
            start = a0 if a0 > 0 else 0
            _run_codes(writer, a1 - start, color)
            _run_codes(writer, a2 - a1, 1 - color)
            a0 = a2
        reference = current
    return writer.bytes()


def _unpack(data: bytes, cols: int, rows: int, *, black_is_1: bool) -> list[list[int]]:
    stride = (cols + 7) // 8
    out = []
    for j in range(rows):
        row = []
        for i in range(cols):
            index = j * stride + i // 8
            bit = (data[index] >> (7 - i % 8)) & 1 if index < len(data) else 0
            row.append(bit if black_is_1 else 1 - bit)
        out.append(row)
    return out


# --- the bitmaps ------------------------------------------------------------

_PATTERNS = {
    "all white": lambda i, j, w, h: 0,
    "all black": lambda i, j, w, h: 1,
    "left half black": lambda i, j, w, h: 1 if i < w // 2 else 0,
    "top half black": lambda i, j, w, h: 1 if j < h // 2 else 0,
    "horizontal stripes": lambda i, j, w, h: (j // 2) % 2,
    "vertical stripes": lambda i, j, w, h: (i // 2) % 2,
    "chequerboard": lambda i, j, w, h: (i + j) % 2,
    "diagonal": lambda i, j, w, h: 1 if i == j else 0,
    "one black dot": lambda i, j, w, h: 1 if (i, j) == (w // 2, h // 2) else 0,
    "comb": lambda i, j, w, h: 1 if i % 3 == 0 else 0,
    # A block that moves right by more than it is wide: the reference line
    # changes twice before the coding line changes at all, which is the one
    # shape that needs pass mode.
    "sliding block": lambda i, j, w, h: 1 if (j * w) // (h or 1) <= i * 2 < (
        j * w) // (h or 1) + max(2, w // 4) else 0,
}


def _bitmap(name: str, w: int, h: int) -> list[list[int]]:
    rule = _PATTERNS[name]
    return [[rule(i, j, w, h) for i in range(w)] for j in range(h)]


_SIZES = [(8, 8), (37, 11), (1728, 3)]


@pytest.mark.parametrize("name", sorted(_PATTERNS))
@pytest.mark.parametrize(("cols", "rows"), _SIZES)
def test_group4_round_trips(name, cols, rows):
    bitmap = _bitmap(name, cols, rows)
    data = _encode_2d(bitmap)
    out = Decoder.decode(
        data, {"K": -1, "Columns": cols, "Rows": rows, "BlackIs1": True}
    )
    assert _unpack(out, cols, rows, black_is_1=True) == bitmap


@pytest.mark.parametrize("name", sorted(_PATTERNS))
@pytest.mark.parametrize(("cols", "rows"), _SIZES)
def test_group3_one_dimensional_round_trips(name, cols, rows):
    bitmap = _bitmap(name, cols, rows)
    data = _encode_1d(bitmap)
    out = Decoder.decode(
        data, {"K": 0, "Columns": cols, "Rows": rows, "BlackIs1": True}
    )
    assert _unpack(out, cols, rows, black_is_1=True) == bitmap


def test_black_is_1_decides_the_polarity():
    # The one thing /BlackIs1 does, and the flag had no effect at all while the
    # decode was failing: the pass-through never reached the packer.
    bitmap = _bitmap("chequerboard", 8, 8)
    data = _encode_2d(bitmap)
    for flag in (True, False):
        out = Decoder.decode(
            data, {"K": -1, "Columns": 8, "Rows": 8, "BlackIs1": flag}
        )
        assert _unpack(out, 8, 8, black_is_1=flag) == bitmap
    on = Decoder.decode(data, {"K": -1, "Columns": 8, "Rows": 8, "BlackIs1": True})
    off = Decoder.decode(data, {"K": -1, "Columns": 8, "Rows": 8, "BlackIs1": False})
    assert on == bytes(byte ^ 0xFF for byte in off)


def test_a_run_wider_than_1728_pixels_decodes():
    # Runs of 1792-2560 pixels have their own make-up codes, shared by both
    # colours, and the tables carried none of them: an A4 page at 400 dpi is
    # 3456 columns wide.
    cols = 3456
    bitmap = [[1 if i < 2000 else 0 for i in range(cols)]]
    out = Decoder.decode(
        _encode_1d(bitmap), {"K": 0, "Columns": cols, "Rows": 1, "BlackIs1": True}
    )
    assert _unpack(out, cols, 1, black_is_1=True) == bitmap


def test_vertical_modes_beyond_one_pixel_are_understood():
    # VR2/VL2/VR3/VL3 all begin 0000 and used to raise "Unsupported extension".
    cols, rows = 16, 4
    bitmap = [
        [1 if 6 <= i < 10 else 0 for i in range(cols)],
        [1 if 8 <= i < 12 else 0 for i in range(cols)],  # a1 - b1 = +2
        [1 if 5 <= i < 9 else 0 for i in range(cols)],  # a1 - b1 = -3
        [1 if 8 <= i < 12 else 0 for i in range(cols)],  # a1 - b1 = +3
    ]
    out = Decoder.decode(
        _encode_2d(bitmap), {"K": -1, "Columns": cols, "Rows": rows, "BlackIs1": True}
    )
    assert _unpack(out, cols, rows, black_is_1=True) == bitmap


def test_a_short_stream_leaves_the_rest_of_the_bitmap_white():
    # Two rows of data for a /Height of five: what decoded is kept and the rest
    # is blank, rather than a bitmap of the wrong size for its height.
    bitmap = _bitmap("chequerboard", 8, 2)
    out = Decoder.decode(
        _encode_2d(bitmap), {"K": -1, "Columns": 8, "Rows": 5, "BlackIs1": True}
    )
    assert len(out) == 5
    assert _unpack(out, 8, 5, black_is_1=True)[:2] == bitmap
    assert _unpack(out, 8, 5, black_is_1=True)[2:] == [[0] * 8] * 3


def test_an_undecodable_stream_is_refused_by_the_filter():
    # The filter must not hand compressed bytes to a caller that will paint
    # them as samples.
    with pytest.raises(PdfValidationException, match="CCITTFaxDecode"):
        StreamDecoder.decode(
            b"\x00\x00\x00\x00\x00\x00",
            "CCITTFaxDecode",
            {"K": 0, "Columns": 64, "Rows": 4},
        )


# --- and the whole way through a document -----------------------------------


def _document(bitmap, *, black_is_1: str) -> bytes:
    """A hand-built one-page PDF drawing *bitmap* at one image pixel per point."""
    cols, rows = len(bitmap[0]), len(bitmap)
    data = _encode_2d(bitmap)
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {cols} {rows}]"
         f" /Resources << /XObject << /Im0 5 0 R >> >> /Contents 4 0 R >>").encode(),
        None,  # content, filled below
        None,  # image, filled below
    ]
    content = f"q {cols} 0 0 {rows} 0 0 cm /Im0 Do Q".encode()
    objects[3] = (b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n"
                  + content + b"\nendstream")
    image_dict = (
        f"<< /Type /XObject /Subtype /Image /Width {cols} /Height {rows}"
        f" /ColorSpace /DeviceGray /BitsPerComponent 1 /Filter /CCITTFaxDecode"
        f" /DecodeParms << /K -1 /Columns {cols} /Rows {rows}"
        f" /BlackIs1 {black_is_1} >> /Length {len(data)} >>"
    ).encode()
    objects[4] = image_dict + b"\nstream\n" + data + b"\nendstream"
    out = bytearray(b"%PDF-1.7\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    start = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{start}\n%%EOF\n").encode()
    return bytes(out)


@pytest.mark.parametrize("name", ["chequerboard", "diagonal", "comb", "one black dot"])
def test_a_ccitt_image_renders_as_the_bitmap_it_encodes(name):
    from aspose_pdf import Document

    bitmap = _bitmap(name, 16, 16)
    # /BlackIs1 says which decoded bit means black, and a 1-bit /DeviceGray
    # sample of 1 is white: so `false` (0 is black) renders the bitmap as it
    # was drawn, and `true` renders its negative unless /Decode [1 0] undoes it.
    # Both are the spec's answer, and all of MuPDF, pdfium and poppler agree.
    for black_is_1, ink in (("false", 0), ("true", 255)):
        document = Document()
        document.load_from(_document(bitmap, black_is_1=black_is_1))
        raster = document.pages[0].render(dpi=72, antialias=False)
        for j in range(16):
            for i in range(16):
                want = ink if bitmap[j][i] else (255 - ink)
                assert raster.get_pixel(i, j) == (want, want, want), (
                    f"{name} /BlackIs1 {black_is_1} at {(i, j)}"
                )
        document.dispose()


def test_an_undecodable_image_is_not_painted_as_noise():
    """A filter that could not run leaves the stream compressed.

    Those bytes are not samples. The renderer used to paint them anyway -- a
    rectangle of noise where the picture should be, which looks like data and so
    is worse than the picture being absent.
    """
    from aspose_pdf import Document

    cols = rows = 16
    data = bytes(range(32))  # not a fax bitstream
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {cols} {rows}]"
         f" /Resources << /XObject << /Im0 5 0 R >> >> /Contents 4 0 R >>").encode(),
        None,
        None,
    ]
    content = f"q {cols} 0 0 {rows} 0 0 cm /Im0 Do Q".encode()
    objects[3] = (b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n"
                  + content + b"\nendstream")
    objects[4] = (
        f"<< /Type /XObject /Subtype /Image /Width {cols} /Height {rows}"
        f" /ColorSpace /DeviceGray /BitsPerComponent 1 /Filter /CCITTFaxDecode"
        f" /DecodeParms << /K -1 /Columns {cols} /Rows {rows} >>"
        f" /Length {len(data)} >>"
    ).encode() + b"\nstream\n" + data + b"\nendstream"
    out = bytearray(b"%PDF-1.7\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    start = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{start}\n%%EOF\n").encode()

    document = Document()
    document.load_from(bytes(out))
    raster = document.pages[0].render(dpi=72, antialias=False)
    assert {raster.get_pixel(i, j) for j in range(rows) for i in range(cols)} == {
        (255, 255, 255)
    }
    document.dispose()

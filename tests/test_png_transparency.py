"""A PNG's transparency survives ``add_image``.

The alpha channel of a grey+alpha or RGBA PNG was stripped and a ``tRNS`` chunk
ignored, so a logo with a transparent background went onto the page as an
opaque rectangle. Transparency is now written as a soft mask (``/SMask``,
ISO 32000-1 11.6.5.3): the alpha channel as it stands, a palette's per-entry
opacity, or -- for grey and RGB -- the one colour ``tRNS`` names, compared at the
image's own bit depth, so a 16-bit pixel that merely shares the key's high byte
stays opaque. pdfium and MuPDF render every case here as Pillow composites it
(Pillow cannot read a 16-bit grey key; pdfium and MuPDF agree there).
"""

from __future__ import annotations

import io
import struct
import zlib

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.cos import PdfName

_RED = (255, 0, 0)


def _chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def _png(width: int, height: int, depth: int, colour: int, rows: list[bytes], extra: bytes = b"", *, interlaced=False) -> bytes:
    if interlaced:
        data = _adam7(width, height, depth, colour, rows)
    else:
        data = b"".join(b"\x00" + row for row in rows)
    header = struct.pack(">IIBBBBB", width, height, depth, colour, 0, 0, 1 if interlaced else 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + extra
        + _chunk(b"IDAT", zlib.compress(data))
        + _chunk(b"IEND", b"")
    )


def _adam7(width: int, height: int, depth: int, colour: int, rows: list[bytes]) -> bytes:
    """Re-cut 8-bit, one-byte-per-sample *rows* into Adam7 passes."""
    assert depth == 8 and colour in (0, 3)
    passes = [(0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2)]
    out = bytearray()
    for x0, y0, dx, dy in passes:
        for y in range(y0, height, dy):
            row = bytes(rows[y][x] for x in range(x0, width, dx))
            if row:
                out += b"\x00" + row
    return bytes(out)


def _page_with(png: bytes) -> Document:
    document = Document()
    page = document.pages.add()
    page.draw_rectangle(0, 0, 400, 400, stroke_color=None, fill_color=(1, 0, 0))
    page.add_image(png, 100, 100, 200, 150)
    buffer = io.BytesIO()
    document.save(buffer)
    return Document(io.BytesIO(buffer.getvalue()))


def _image_dict(document: Document):
    engine = document._engine_pdf
    resources = engine._resolve(engine._get_page_dict(0).mapping[PdfName("Resources")])
    xobjects = engine._resolve(resources.mapping[PdfName("XObject")])
    return engine, engine._resolve(next(iter(xobjects.mapping.values())))


def _soft_mask(document: Document) -> bytes | None:
    engine, image = _image_dict(document)
    ref = image.mapping.get(PdfName("SMask"))
    if ref is None:
        return None
    mask = engine._resolve(ref)
    assert mask.mapping[PdfName("ColorSpace")] == PdfName("DeviceGray")
    return zlib.decompress(mask.content)


def _pixel(document: Document, x: float, y: float) -> tuple[int, int, int]:
    raster = document.pages[0].render(dpi=72, antialias=False)
    return raster.get_pixel(int(x), int(raster.height - y))


W, H = 20, 10


def _left_right(document: Document) -> tuple:
    # The image spans x 100..300, y 100..250; columns 0-9 left, 10-19 right.
    return _pixel(document, 130, 175), _pixel(document, 270, 175)


def test_an_rgba_alpha_channel_becomes_the_soft_mask():
    rows = [bytes(v for x in range(W) for v in (200, 200, 0, 0 if x < 10 else 255)) for _ in range(H)]
    document = _page_with(_png(W, H, 8, 6, rows))
    assert _soft_mask(document) == (bytes([0] * 10 + [255] * 10)) * H
    assert _left_right(document) == (_RED, (200, 200, 0))


def test_a_grey_alpha_channel_becomes_the_soft_mask():
    rows = [bytes(v for x in range(W) for v in (40, 0 if x < 10 else 255)) for _ in range(H)]
    assert _left_right(_page_with(_png(W, H, 8, 4, rows))) == (_RED, (40, 40, 40))


def test_a_palette_trns_gives_each_entry_its_opacity():
    palette = _chunk(b"PLTE", bytes([0, 0, 255, 0, 200, 0]))
    trns = _chunk(b"tRNS", bytes([0]))  # entry 0 transparent, entry 1 past the list: opaque
    rows = [bytes(0 if x < 10 else 1 for x in range(W)) for _ in range(H)]
    document = _page_with(_png(W, H, 8, 3, rows, palette + trns))
    assert _left_right(document) == (_RED, (0, 200, 0))


@pytest.mark.parametrize(
    ("depth", "colour", "key", "sample_left", "sample_right", "opaque"),
    [
        (8, 0, struct.pack(">H", 128), b"\x80", b"\x1e", (30, 30, 30)),
        (8, 2, struct.pack(">HHH", 0, 0, 255), b"\x00\x00\xff", b"\x00\xb4\x00", (0, 180, 0)),
        # Same high byte as the key, different low byte: not transparent.
        (16, 0, struct.pack(">H", 0x1234), b"\x12\x34", b"\x12\xff", (18, 18, 18)),
    ],
    ids=["grey-8", "rgb-8", "grey-16"],
)
def test_a_grey_or_rgb_trns_colour_is_transparent(depth, colour, key, sample_left, sample_right, opaque):
    rows = [sample_left * 10 + sample_right * 10 for _ in range(H)]
    document = _page_with(_png(W, H, depth, colour, rows, _chunk(b"tRNS", key)))
    assert _left_right(document) == (_RED, opaque)


def test_a_one_bit_grey_key_is_compared_before_scaling():
    # Bit 1 (white) is the key; the left half is white, the right half black.
    rows = [bytes([0xFF, 0xC0, 0x00]) for _ in range(H)]  # 10 ones, 10 zeros, padding
    document = _page_with(_png(W, H, 1, 0, rows, _chunk(b"tRNS", struct.pack(">H", 1))))
    assert _left_right(document) == (_RED, (0, 0, 0))


def test_an_interlaced_png_keeps_its_transparency_in_place():
    rows = [bytes(128 if x < 10 else 30 for x in range(W)) for _ in range(H)]
    document = _page_with(_png(W, H, 8, 0, rows, _chunk(b"tRNS", struct.pack(">H", 128)), interlaced=True))
    assert _soft_mask(document) == (bytes([0] * 10 + [255] * 10)) * H
    assert _left_right(document) == (_RED, (30, 30, 30))


def test_an_opaque_png_gets_no_soft_mask():
    rows = [bytes(v for _ in range(W) for v in (10, 20, 30, 255)) for _ in range(H)]
    assert _soft_mask(_page_with(_png(W, H, 8, 6, rows))) is None


def test_a_push_button_icon_keeps_its_transparency():
    rows = [bytes(v for x in range(W) for v in (200, 200, 0, 0 if x < 10 else 255)) for _ in range(H)]
    document = Document()
    page = document.pages.add()
    document.form.add_push_button("Go", page, (100, 100, 300, 250), icon=_png(W, H, 8, 6, rows))
    buffer = io.BytesIO()
    document.save(buffer)
    data = buffer.getvalue()
    assert data.count(b"/SMask") == 1

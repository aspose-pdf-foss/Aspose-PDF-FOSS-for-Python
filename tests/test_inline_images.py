"""An inline image's samples are not tokens.

Between ``ID`` and ``EI`` lie the image's bytes, and bytes spell whatever they
happen to spell: an operator nobody wrote, a name, an opening ``(`` that closes
nowhere. Lexed as tokens they are noise, and because a literal string runs to
its closing paren, the noise usually swallows the rest of the page -- so the
text drawn *after* an inline image went missing, and so did the marks.

ISO 32000-1 8.9.7 gives the rule that makes the end of the data findable rather
than guessable: unfiltered samples occupy exactly
``ceil(Width x BitsPerComponent x components / 8) x Height`` bytes. Only a
filter leaves the search for a free-standing ``EI`` -- which samples can
imitate, and do.
"""

from __future__ import annotations

import binascii
import io
import zlib

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.auto_tag import _tokens
from aspose_pdf.engine.content_stream_parser import ContentStreamParser
from aspose_pdf.engine.cos import PdfName
from aspose_pdf.engine.inline_image import (
    InlineImage,
    inline_image_data_end,
    inline_image_end,
    scan_inline_image,
)
from aspose_pdf.load_limits import PdfLoadLimits

_FONT = {"Font": {"F1": {"Subtype": "Type1", "BaseFont": "/Helvetica"}}}


def _page(bi: bytes, data: bytes) -> bytes:
    """Two lines of text with an inline image between them."""
    return (
        b"BT /F1 12 Tf 20 100 Td (before) Tj ET\n"
        b"q 10 0 0 10 20 50 cm BI " + bi + b" ID " + data + b" EI Q\n"
        b"BT /F1 12 Tf 20 20 Td (after) Tj ET\n"
    )


def _text(content: bytes) -> str:
    return ContentStreamParser(content, _FONT).extract_text()


def _images(content: bytes) -> list[InlineImage]:
    tokens = ContentStreamParser(content, _FONT)._tokenize()
    return [token for token in tokens if isinstance(token, InlineImage)]


# --- the samples must not be read as operators ------------------------------


def test_text_after_an_inline_image_survives():
    samples = bytes(range(50))
    assert _text(_page(b"/W 10 /H 5 /CS /G /BPC 8", samples)) == "before\nafter"


def test_samples_spelling_an_unclosed_string_do_not_eat_the_page():
    samples = bytearray(range(50))
    samples[10:12] = b" ("  # a string the image never closes
    assert _text(_page(b"/W 10 /H 5 /CS /G /BPC 8", bytes(samples))) == "before\nafter"


def test_samples_spelling_operators_draw_nothing():
    samples = bytearray(range(50))
    samples[0:20] = b"(ghost) Tj BT ET Tj  "
    assert _text(_page(b"/W 10 /H 5 /CS /G /BPC 8", bytes(samples))) == "before\nafter"


def test_recovery_extraction_does_not_collect_the_samples_as_text():
    samples = bytearray(range(50))
    samples[0:8] = b"(ghost) "
    parser = ContentStreamParser(
        _page(b"/W 10 /H 5 /CS /G /BPC 8", bytes(samples)), _FONT
    )
    assert "ghost" not in parser.best_effort_extract_text()


# --- the length the dictionary settles --------------------------------------


def test_samples_containing_a_free_standing_ei_are_kept_whole():
    # 20 x 5 x 8bpc gray is exactly 100 bytes, whatever those bytes say.
    samples = bytearray(range(100))
    samples[40:45] = b" EI  "
    content = _page(b"/W 20 /H 5 /CS /G /BPC 8", bytes(samples))
    assert _text(content) == "before\nafter"
    (image,) = _images(content)
    assert image.data == bytes(samples)


def test_row_padding_counts_towards_the_length():
    # 3 pixels of 1 bit is one byte per row once padded, not 3 bits: 2 bytes,
    # not none. The samples are the two that spell EI, so counting the bits
    # without the padding ends the image before it starts.
    samples = b"EI"
    content = _page(b"/W 3 /H 2 /CS /G /BPC 1", samples)
    (image,) = _images(content)
    assert image.data == samples
    assert _text(content) == "before\nafter"


def test_four_bits_per_component_packs_two_pixels_to_the_byte():
    samples = bytes([0x0F, 0xF0])
    (image,) = _images(_page(b"/W 2 /H 2 /CS /G /BPC 4", samples))
    assert image.data == samples


def test_an_image_mask_is_one_bit_of_one_component():
    # A stencil names no colour space, so only /ImageMask says how wide a
    # sample is. 20 bits a row is 3 bytes once padded, 15 for five rows -- and
    # without that the scan stops at the EI the samples spell.
    samples = bytearray(range(1, 16))
    samples[5:9] = b" EI "
    (image,) = _images(_page(b"/W 20 /H 5 /IM true /D [0 1]", bytes(samples)))
    assert image.data == bytes(samples)


def test_rgb_counts_three_components_a_pixel():
    # 12 bytes, not 4: counting one component a pixel would end the image at
    # the EI these samples happen to spell four bytes in.
    samples = b"\x01\x02\x03\x04EI \x06\x07\x08\x09\x0a"
    assert len(samples) == 12
    (image,) = _images(_page(b"/W 2 /H 2 /CS /RGB /BPC 8", samples))
    assert image.data == samples


def test_cmyk_counts_four():
    samples = bytes(range(16))
    (image,) = _images(_page(b"/W 2 /H 2 /CS /CMYK /BPC 8", samples))
    assert image.data == samples


def test_an_indexed_space_is_one_component():
    samples = bytes([0b10000000, 0b01000000])
    (image,) = _images(
        _page(b"/W 2 /H 2 /CS [/I /RGB 1 <FF000000FF00>] /BPC 1", samples)
    )
    assert image.data == samples


def test_a_declared_length_is_taken_even_over_a_filter():
    # /L (PDF 2.0) is the only thing that can answer for filtered bytes, and
    # these spell an EI a reader would otherwise stop at.
    body = b"\x01\x02 EI \x03\x04"
    content = _page(b"/W 2 /H 2 /CS /RGB /BPC 8 /F /Fl /L %d" % len(body), body)
    (image,) = _images(content)
    assert image.data == body


@pytest.mark.parametrize("declared", [b"/W 500 /H 500", b"/W 5 /H 1"])
def test_a_wrong_declared_length_defers_to_where_the_bytes_actually_end(declared):
    # A dictionary can claim more bytes than the stream holds, or fewer than
    # the image really has. Either way the bytes are what is on the page: the
    # length is taken only when an EI is actually standing at the end of it.
    samples = bytes(range(1, 51))
    content = _page(declared + b" /CS /G /BPC 8", samples)
    assert _text(content) == "before\nafter"
    (image,) = _images(content)
    assert image.data == samples


# --- when only the search for EI is left ------------------------------------


@pytest.mark.parametrize(
    ("filter_name", "encode"),
    [
        (b"/Fl", zlib.compress),
        (b"/AHx", lambda raw: binascii.hexlify(raw) + b">"),
        (b"/A85", lambda raw: __import__("base64").a85encode(raw) + b"~>"),
    ],
)
def test_a_filtered_image_ends_at_its_ei(filter_name, encode):
    body = encode(bytes(range(12)))
    content = _page(b"/W 2 /H 2 /CS /RGB /BPC 8 /F " + filter_name, body)
    assert _text(content) == "before\nafter"
    (image,) = _images(content)
    assert image.data == body


def test_a_filter_makes_the_dictionary_no_judge_of_the_length():
    # 2x2 RGB would be 12 raw bytes, and byte 12 of this body is followed by
    # "EI" -- but the body is compressed, so its length is a property of the
    # encoded bytes and nothing in the dictionary can say it.
    body = b"\x11\x12\x13\x14\x15\x16\x17\x18\x19\x1a\x1b\x1cEI more bytes"
    content = _page(b"/W 2 /H 2 /CS /RGB /BPC 8 /F /Fl", body)
    (image,) = _images(content)
    assert image.data == body


def test_an_ei_inside_a_token_does_not_end_the_image():
    # `xxEI ` is not an EI operator: an operator starts at a token boundary.
    body = b"\x01\x02xxEI still the image\x03"
    content = _page(b"/W 2 /H 2 /CS /RGB /BPC 8 /F /Fl", body)
    (image,) = _images(content)
    assert image.data == body


def test_an_ei_with_a_letter_after_it_does_not_end_the_image():
    body = b"\x01\x02 EIx still the image\x03"
    content = _page(b"/W 2 /H 2 /CS /RGB /BPC 8 /F /Fl", body)
    (image,) = _images(content)
    assert image.data == body


def test_a_colour_space_naming_a_page_resource_leaves_the_length_to_the_scan():
    body = bytes([1, 2, 3, 4])
    content = _page(b"/W 2 /H 2 /CS /CsGray /BPC 8", body)
    (image,) = _images(content)
    assert image.data == body


# --- the dictionary, expanded -----------------------------------------------


def test_abbreviated_keys_become_the_names_an_image_xobject_uses():
    (image,) = _images(_page(b"/W 2 /H 2 /CS /RGB /BPC 8 /D [0 1 0 1 0 1]", bytes(12)))
    keys = {name.name for name in image.entries.mapping}
    assert keys == {"/Width", "/Height", "/ColorSpace", "/BitsPerComponent", "/Decode"}


def test_abbreviated_filter_and_colour_space_names_are_expanded():
    body = zlib.compress(bytes(12))
    (image,) = _images(_page(b"/W 2 /H 2 /CS /RGB /BPC 8 /F /Fl", body))
    mapping = image.entries.mapping
    assert mapping[PdfName("Filter")].name == "/FlateDecode"
    assert mapping[PdfName("ColorSpace")].name == "/DeviceRGB"


def test_an_indexed_arrays_leading_name_is_expanded_but_not_its_base():
    (image,) = _images(
        _page(b"/W 2 /H 2 /CS [/I /RGB 1 <FF000000FF00>] /BPC 1", bytes(2))
    )
    space = image.entries.mapping[PdfName("ColorSpace")]
    assert space.items[0].name == "/Indexed"
    assert space.items[1].name == "/RGB"


def test_interpolate_is_a_key_where_indexed_is_a_value():
    # /I means /Interpolate as a key and /Indexed as a colour space; the two
    # tables are separate for exactly this reason.
    (image,) = _images(_page(b"/W 2 /H 2 /CS /G /BPC 8 /I true", bytes(4)))
    assert PdfName("Interpolate") in image.entries.mapping


def test_long_key_names_are_accepted_unchanged():
    (image,) = _images(
        _page(
            b"/Width 2 /Height 2 /ColorSpace /DeviceGray /BitsPerComponent 8", bytes(4)
        )
    )
    assert image.entries.mapping[PdfName("Width")].value == 2


def test_to_stream_is_the_image_xobject_the_inline_image_stands_for():
    body = bytes(range(12))
    (image,) = _images(_page(b"/W 2 /H 2 /CS /RGB /BPC 8", body))
    stream = image.to_stream()
    assert stream.content == body
    assert stream.mapping[PdfName("Subtype")].name == "/Image"
    assert stream.mapping[PdfName("Type")].name == "/XObject"
    assert stream.mapping[PdfName("Length")].value == len(body)
    # It came out of an already-decrypted content stream.
    assert stream.content_decrypted is True


# --- the operand stack ------------------------------------------------------


def test_the_image_does_not_stay_on_the_operand_stack():
    # `BI` takes no operands and `EI` takes the image, so a page of images does
    # not carry every one of them to the end of it. Left on the stack they
    # would run a document's operand budget out on a page of photographs.
    content = b"".join(_page(b"/W 2 /H 2 /CS /G /BPC 8", bytes(4)) for _ in range(20))
    limits = PdfLoadLimits(max_container_items=8)
    resources = {"Font": {"F1": {"Subtype": "Type1", "BaseFont": "/Helvetica"}}}
    parser = ContentStreamParser(content, resources, limits=limits)
    assert parser.extract_text().count("after") == 20


# --- malformed input --------------------------------------------------------


def test_a_dictionary_that_does_not_parse_leaves_the_rest_of_the_page_readable():
    content = (
        b"BT /F1 12 Tf 20 100 Td (before) Tj ET\n"
        b"BI 4 5 6\n"
        b"BT /F1 12 Tf 20 20 Td (after) Tj ET\n"
    )
    assert "after" in _text(content)


def test_an_unterminated_image_ends_at_the_end_of_the_stream():
    content = b"BI /W 2 /H 2 /CS /G /BPC 8 /F /Fl ID " + bytes(range(20))
    assert _text(content) == ""


def test_a_stray_id_without_a_bi_still_skips_to_the_ei():
    text = "ID \x01\x02(\x03 EI Q"
    assert inline_image_data_end(text, 3) == text.index("EI") + 2


# --- one rule, two readers --------------------------------------------------


def test_the_layout_reader_steps_over_the_same_bytes():
    samples = bytearray(range(100))
    samples[40:45] = b" EI  "
    samples[60:62] = b" ("
    content = _page(b"/W 20 /H 5 /CS /G /BPC 8", bytes(samples))
    data_start = content.index(b" ID ") + 4
    spans = list(_tokens(content))
    assert [token for token, _, _ in spans].count("BT") == 2  # not one, not three
    # No token's bytes come from inside the image. The byte spans are what
    # ``optional_content`` cuts on, so a token starting in the samples would
    # not merely be noise -- it would cut the page in the wrong place.
    assert not [
        token
        for token, start, _ in spans
        if data_start <= start < data_start + len(samples)
    ]


def test_both_readers_agree_on_where_the_image_ends():
    samples = bytes(range(100))
    content = _page(b"/W 20 /H 5 /CS /G /BPC 8", samples)
    start = content.index(b"BI ") + 3
    end = inline_image_end(content.decode("latin-1"), start)
    image, scanned_end = scan_inline_image(content.decode("latin-1"), start)
    assert end == scanned_end
    assert image.data == samples


# --- and it is actually painted ---------------------------------------------


_RGB_QUADRANTS = bytes(
    [255, 0, 0, 0, 255, 0, 0, 0, 255, 255, 255, 255]  # red green / blue white
)


def _rendered(bi: bytes, data: bytes, resources: dict | None = None):
    from aspose_pdf import Document
    from aspose_pdf.engine.simple_pdf import SimplePdf

    doc = Document()
    doc._engine_pdf = SimplePdf(
        pages=[(0.0, 0.0, 40.0, 30.0)],
        page_contents=[
            b"1 0 0 rg q 20 0 0 20 10 5 cm BI " + bi + b" ID " + data + b" EI Q"
        ],
    )
    if resources is not None:
        doc._engine_pdf.page_resources = [resources]
    return doc.pages[0].render(antialias=False)


def test_an_inline_image_is_painted_where_the_matrix_puts_it():
    raster = _rendered(b"/W 2 /H 2 /CS /RGB /BPC 8", _RGB_QUADRANTS)
    # The first row of samples is the top of the image.
    assert raster.get_pixel(15, 10) == (255, 0, 0)
    assert raster.get_pixel(25, 10) == (0, 255, 0)
    assert raster.get_pixel(15, 20) == (0, 0, 255)
    assert raster.get_pixel(25, 20) == (255, 255, 255)
    assert raster.get_pixel(2, 2) == (255, 255, 255)  # nothing outside it


def test_a_filtered_inline_image_is_painted_the_same():
    raster = _rendered(
        b"/W 2 /H 2 /CS /RGB /BPC 8 /F /Fl", zlib.compress(_RGB_QUADRANTS)
    )
    assert raster.get_pixel(15, 10) == (255, 0, 0)
    assert raster.get_pixel(25, 20) == (255, 255, 255)


def test_an_inline_image_reads_a_colour_space_out_of_the_page_resources():
    # /CS may name an entry in the page's /ColorSpace resources, which the
    # tokenizer cannot see. The space here is gray, so a renderer that gave up
    # and assumed three components would paint something else entirely.
    doc = _document(
        b"/W 2 /H 2 /CS /CsGray /BPC 8",
        bytes([0, 85, 170, 255]),
        resources=b"/ColorSpace << /CsGray /DeviceGray >> ",
    )
    raster = doc.pages[0].render(antialias=False)
    assert raster.get_pixel(15, 10) == (0, 0, 0)
    assert raster.get_pixel(25, 10) == (85, 85, 85)
    assert raster.get_pixel(15, 20) == (170, 170, 170)
    assert raster.get_pixel(25, 20) == (255, 255, 255)


def _document(bi: bytes, data: bytes, resources: bytes = b"") -> Document:
    """A one-page document whose only mark is an inline image."""
    content = b"q 20 0 0 20 10 5 cm BI " + bi + b" ID " + data + b" EI Q"
    raw = (
        b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 40 30] "
        b"/Resources << " + resources + b">> /Contents 4 0 R >> endobj\n"
        b"4 0 obj << /Length "
        + str(len(content)).encode()
        + b" >> stream\n"
        + content
        + b"\nendstream endobj\n"
        b"trailer << /Root 1 0 R /Size 5 >>\n%%EOF\n"
    )
    return Document(io.BytesIO(raw))

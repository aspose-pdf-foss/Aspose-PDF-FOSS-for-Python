"""Stamps: a mark put over or under what a page already draws.

There was no way to stamp a document. ``SimplePdf.set_watermark`` existed but
only the retired V0 writer ever read it, so through ``Document`` it set a field
nobody looked at and saved a file with no watermark in it. Text, image and page
number stamps now go on through ``Page.add_stamp`` and ``Document.add_stamp``.

The behaviour is qpdf's ``--overlay``/``--underlay``, which pikepdf exposes as
``Page.add_overlay``/``add_underlay``: the stamp is a form XObject in its own
``q``/``Q``, placed inside the box a reader shows (the crop box) with the page's
``/Rotate`` undone so it stands upright, appended for the foreground and put
first for the background. The placement matrices asserted below are the ones
qpdf writes for the same stamp, and every position and opacity was read back out
of pdfium's and MuPDF's rasters, which agreed with each other to a pixel.
"""

from __future__ import annotations

import io
import struct
import zlib

import pytest

from aspose_pdf import (
    Document,
    HorizontalAlignment,
    ImageStamp,
    PageNumberStamp,
    TextStamp,
    VerticalAlignment,
)
from aspose_pdf.engine.cos import PdfArray, PdfName
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.engine.stamps import placement_matrix
from aspose_pdf.exceptions import PdfValidationException

SIZE = 100


def _document(count: int = 1, size: int = SIZE) -> Document:
    """A document of small blank pages, so a raster can be read pixel by pixel."""
    data = SimplePdf(
        [(0, 0, size, size)] * count, page_contents=[b""] * count
    ).to_bytes()
    return Document(io.BytesIO(data))


def _reloaded(document: Document) -> Document:
    buffer = io.BytesIO()
    document.save(buffer)
    return Document(io.BytesIO(buffer.getvalue()))


def _pixels(document: Document, colour, page: int = 0) -> set[tuple[int, int]]:
    raster = document.pages[page].render(antialias=False)
    return {
        (x, y)
        for y in range(raster.height)
        for x in range(raster.width)
        if colour(raster.get_pixel(x, y))
    }


def _red(pixel) -> bool:
    return pixel[0] > 200 and pixel[1] < 90 and pixel[2] < 90


def _blue(pixel) -> bool:
    return pixel[2] > 200 and pixel[0] < 90 and pixel[1] < 90


def _contents(document: Document, page: int = 0) -> list[bytes]:
    engine = document._engine_pdf
    page_dict = engine._get_page_dict(page)
    entry = engine._resolve(page_dict.mapping[PdfName("Contents")])
    items = entry.items if isinstance(entry, PdfArray) else [entry]
    return [bytes(engine._resolve(item).content) for item in items]


def _xobjects(document: Document, page: int = 0):
    engine = document._engine_pdf
    resources = engine._resolve(
        engine._get_page_dict(page).mapping[PdfName("Resources")]
    )
    return engine._resolve(resources.mapping[PdfName("XObject")])


def _png(
    colour: tuple[int, int, int],
    size: int = 2,
    *,
    width: int | None = None,
    height: int | None = None,
) -> bytes:
    width, height = width or size, height or size

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    rows = b"".join(b"\x00" + bytes(colour) * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


# --- where a stamp goes -----------------------------------------------------


def test_a_stamp_is_placed_inside_what_a_reader_shows():
    document = _document()
    document.pages[0].add_stamp(
        ImageStamp(
            _png((255, 0, 0)),
            width=20,
            height=10,
            horizontal_alignment=HorizontalAlignment.RIGHT,
            vertical_alignment=VerticalAlignment.TOP,
        )
    )
    # Top right of a 100x100 page: x 80..100, y 90..100 in page space.
    red = _pixels(_reloaded(document), _red)
    assert min(x for x, _ in red) == 80 and max(x for x, _ in red) == 99
    assert min(y for _, y in red) == 0 and max(y for _, y in red) == 9


def test_the_crop_box_is_what_a_stamp_is_placed_in():
    document = _document()
    document.pages[0].crop_box = (20, 20, 60, 60)
    document.pages[0].add_stamp(
        ImageStamp(
            _png((255, 0, 0)),
            width=10,
            height=10,
            horizontal_alignment=HorizontalAlignment.LEFT,
            vertical_alignment=VerticalAlignment.BOTTOM,
        )
    )
    # The crop box's own bottom left, not the media box's.
    raster = _reloaded(document).pages[0].render(antialias=False)
    assert (raster.width, raster.height) == (40, 40)
    red = {
        (x, y)
        for y in range(raster.height)
        for x in range(raster.width)
        if _red(raster.get_pixel(x, y))
    }
    assert min(x for x, _ in red) == 0 and max(y for _, y in red) == 39


def test_an_indent_moves_a_stamp_off_its_alignment():
    document = _document()
    document.pages[0].add_stamp(
        ImageStamp(
            _png((255, 0, 0)),
            width=10,
            height=10,
            horizontal_alignment=HorizontalAlignment.LEFT,
            vertical_alignment=VerticalAlignment.BOTTOM,
            x_indent=15,
            y_indent=25,
        )
    )
    red = _pixels(_reloaded(document), _red)
    assert min(x for x, _ in red) == 15
    assert max(y for _, y in red) == SIZE - 1 - 25


def test_zoom_scales_a_stamp_around_where_it_sits():
    document = _document()
    document.pages[0].add_stamp(
        ImageStamp(_png((255, 0, 0)), width=10, height=10, zoom=3)
    )
    red = _pixels(_reloaded(document), _red)
    assert max(x for x, _ in red) - min(x for x, _ in red) == 29
    # Centred still: 30 wide on a 100 page leaves 35 either side.
    assert min(x for x, _ in red) == 35


def test_zoom_is_taken_into_account_when_a_stamp_is_aligned():
    # The alignment places the box the stamp *ends up* filling, so a zoomed
    # stamp pushed into a corner still sits inside the page rather than half
    # off it.
    document = _document()
    document.pages[0].add_stamp(
        ImageStamp(
            _png((255, 0, 0)), width=10, height=10, zoom=3,
            horizontal_alignment=HorizontalAlignment.RIGHT,
            vertical_alignment=VerticalAlignment.TOP,
        )
    )
    red = _pixels(_reloaded(document), _red)
    assert len(red) == 30 * 30
    assert max(x for x, _ in red) == SIZE - 1 and min(x for x, _ in red) == SIZE - 30
    assert min(y for _, y in red) == 0 and max(y for _, y in red) == 29


def test_a_turned_stamp_still_sits_inside_the_page():
    document = _document()
    document.pages[0].add_stamp(
        ImageStamp(
            _png((255, 0, 0)),
            width=40,
            height=10,
            rotate=90,
            horizontal_alignment=HorizontalAlignment.LEFT,
            vertical_alignment=VerticalAlignment.BOTTOM,
        )
    )
    red = _pixels(_reloaded(document), _red)
    # Turned a quarter turn, the box it occupies is 10 wide and 40 tall, and
    # that box is what the alignment places -- so nothing hangs off the page.
    assert min(x for x, _ in red) == 0 and max(x for x, _ in red) == 9
    assert max(y for _, y in red) == SIZE - 1 and min(y for _, y in red) == SIZE - 40


# --- the page's rotation ----------------------------------------------------


def test_a_stamp_stands_upright_on_a_rotated_page():
    document = _document()
    document.pages[0].rotation = 90
    document.pages[0].add_stamp(
        ImageStamp(
            _png((255, 0, 0)),
            width=40,
            height=10,
            horizontal_alignment=HorizontalAlignment.LEFT,
            vertical_alignment=VerticalAlignment.BOTTOM,
        )
    )
    # As shown, the stamp is 40 wide and 10 tall at the bottom left -- which is
    # what qpdf's overlay does with a rotated page, and what a stamp is for.
    red = _pixels(_reloaded(document), _red)
    assert max(x for x, _ in red) - min(x for x, _ in red) == 39
    assert max(y for _, y in red) - min(y for _, y in red) == 9
    assert min(x for x, _ in red) == 0 and max(y for _, y in red) == SIZE - 1


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_every_rotation_puts_the_stamp_in_the_same_corner_on_screen(rotation):
    document = _document()
    document.pages[0].rotation = rotation
    document.pages[0].add_stamp(
        ImageStamp(
            _png((255, 0, 0)),
            width=20,
            height=10,
            horizontal_alignment=HorizontalAlignment.RIGHT,
            vertical_alignment=VerticalAlignment.TOP,
        )
    )
    raster = _reloaded(document).pages[0].render(antialias=False)
    red = {
        (x, y)
        for y in range(raster.height)
        for x in range(raster.width)
        if _red(raster.get_pixel(x, y))
    }
    assert max(x for x, _ in red) == raster.width - 1
    assert min(y for _, y in red) == 0
    assert max(x for x, _ in red) - min(x for x, _ in red) == 19


def test_the_matrix_is_the_one_qpdf_writes():
    # qpdf's add_underlay of a 200x100 page onto a page whose crop box is
    # (100, 100, 500, 700) with /Rotate 90 writes `0 3 -3 0 450 100 cm`; its
    # add_overlay on the same page without the rotation writes `2 0 0 2 100 300`.
    assert placement_matrix(
        box=(100, 100, 500, 700), page_rotation=90, size=(200, 100),
        zoom=(3, 3), rotate=0, alignment=("Center", "Center"), indent=(0, 0),
    ) == pytest.approx((0.0, 3.0, -3.0, 0.0, 450.0, 100.0), abs=1e-9)
    assert placement_matrix(
        box=(100, 100, 500, 700), page_rotation=0, size=(200, 100),
        zoom=(2, 2), rotate=0, alignment=("Center", "Center"), indent=(0, 0),
    ) == pytest.approx((2.0, 0.0, 0.0, 2.0, 100.0, 300.0), abs=1e-9)


# --- over or under ----------------------------------------------------------


def _page_with_a_blue_bar(**stamp_args) -> Document:
    document = _document()
    document.pages[0].draw_rectangle(
        20, 20, 60, 60, fill_color=(0, 0, 1), stroke_color=None
    )
    document.pages[0].add_stamp(
        ImageStamp(_png((255, 0, 0)), width=40, height=40, **stamp_args)
    )
    return _reloaded(document)


def _bar_alone() -> set[tuple[int, int]]:
    """The blue bar's pixels with nothing stamped on it."""
    document = _document()
    document.pages[0].draw_rectangle(
        20, 20, 60, 60, fill_color=(0, 0, 1), stroke_color=None
    )
    return _pixels(_reloaded(document), _blue)


def test_a_foreground_stamp_covers_what_the_page_draws():
    document = _page_with_a_blue_bar()
    red, blue = _pixels(document, _red), _pixels(document, _blue)
    assert len(red) == 40 * 40  # all of the stamp is drawn
    assert red & {(x, y) for x in range(20, 80) for y in range(20, 80)}
    # The bar keeps only what the stamp does not stand on.
    assert blue == _bar_alone() - red


def test_a_background_stamp_is_covered_by_it():
    document = _page_with_a_blue_bar(background=True)
    red, blue = _pixels(document, _red), _pixels(document, _blue)
    assert blue == _bar_alone()  # every pixel of the bar survives
    assert len(red) == 0  # the stamp is entirely behind it


def _order_of(document: Document) -> tuple[int, int]:
    """Which of the page's streams holds the stamp, and which the page's text."""
    streams = _contents(document)
    stamp = next(i for i, data in enumerate(streams) if b"Do" in data)
    body = next(i for i, data in enumerate(streams) if b"BODY" in data)
    return stamp, body


def test_a_background_stamp_goes_in_before_the_page_content():
    document = _document()
    document.pages[0].add_text("BODY", 10, 50, font_size=12)
    document.pages[0].add_stamp(TextStamp("MARK", background=True))
    stamp, body = _order_of(document)
    assert stamp < body

    # And the other way round for the foreground.
    document = _document()
    document.pages[0].add_text("BODY", 10, 50, font_size=12)
    document.pages[0].add_stamp(TextStamp("MARK"))
    stamp, body = _order_of(document)
    assert stamp > body


def test_a_background_stamp_is_behind_before_anything_is_saved():
    # Rendering and extraction read the document as it stands, so the order has
    # to be right in memory too, not only in the file that is written from it.
    document = _document()
    document.pages[0].draw_rectangle(
        20, 20, 60, 60, fill_color=(0, 0, 1), stroke_color=None
    )
    document.pages[0].add_stamp(
        ImageStamp(_png((255, 0, 0)), width=40, height=40, background=True)
    )
    assert _pixels(document, _blue) == _bar_alone()
    assert _pixels(document, _red) == set()


def test_a_stamp_cannot_be_disturbed_by_the_page_it_goes_on():
    # A page whose content leaves a transform behind: the stamp still lands
    # where it was placed, because the appended content is isolated from it.
    document = _document()
    engine = document._engine_pdf
    engine._set_page_content(0, b"3 0 0 3 0 0 cm 0 0 1 rg 0 0 5 5 re f")
    document = _reloaded(document)
    document.pages[0].add_stamp(
        ImageStamp(
            _png((255, 0, 0)),
            width=10,
            height=10,
            horizontal_alignment=HorizontalAlignment.LEFT,
            vertical_alignment=VerticalAlignment.BOTTOM,
        )
    )
    red = _pixels(_reloaded(document), _red)
    assert min(x for x, _ in red) == 0 and max(x for x, _ in red) == 9


# --- opacity ----------------------------------------------------------------


def test_opacity_is_written_as_an_alpha_state():
    document = _document()
    document.pages[0].add_stamp(ImageStamp(_png((255, 0, 0)), width=40, height=40, opacity=0.3))
    document = _reloaded(document)
    raster = document.pages[0].render(antialias=False)
    # Red at three tenths over white: pdfium and MuPDF both render (255, 179, 179).
    assert raster.get_pixel(50, 50) == pytest.approx((255, 179, 179), abs=2)


def test_a_fully_opaque_stamp_needs_no_alpha_state():
    document = _document()
    document.pages[0].add_stamp(ImageStamp(_png((255, 0, 0)), width=10, height=10))
    assert b"/GS0 gs" not in b"".join(_contents(document))
    forms = [
        stream
        for stream in _contents(document)
        if b"Do" in stream
    ]
    assert forms and b"gs" not in forms[-1]


# --- text -------------------------------------------------------------------


def test_a_text_stamp_is_as_wide_as_its_text():
    # "Page 1 of 10" in 10pt Helvetica is 56.71pt wide, from the font's own
    # metrics; the flat 600/1000 guess this used to have made it 72.
    document = _document(size=200)
    document.pages[0].add_stamp(
        TextStamp(
            "Page 1 of 10",
            font_size=10,
            horizontal_alignment=HorizontalAlignment.CENTER,
        )
    )
    ink = _pixels(_reloaded(document), lambda pixel: sum(pixel) < 400)
    centre = (min(x for x, _ in ink) + max(x for x, _ in ink)) / 2
    assert abs(centre - 100) <= 2  # centred on a 200-wide page


def test_a_text_stamp_is_as_tall_as_the_faces_own_bounds():
    # The form's /BBox clips it, so a box measured too short would cut the tail
    # off a 'g'. Helvetica's own bounds run from 1075 to -299 thousandths, so
    # at 20pt the box is 27.48 tall with the baseline 5.98 up from its bottom.
    document = _document(size=60)
    document.pages[0].add_stamp(
        TextStamp("gggg", font_size=20, vertical_alignment=VerticalAlignment.CENTER)
    )
    engine = document._engine_pdf
    form = next(
        engine._resolve(reference)
        for reference in _xobjects(document).mapping.values()
    )
    box = engine._resolve(form.mapping[PdfName("BBox")])
    assert box.items[3].value == pytest.approx(27.48, abs=0.01)
    assert b"1 0 0 1 0 5.98 Tm" in form.content
    # And the tails really are drawn, not clipped away.
    ink = _pixels(_reloaded(document), lambda pixel: sum(pixel) < 400)
    assert max(y for _, y in ink) - min(y for _, y in ink) > 20 * 0.5


def test_text_a_standard_font_cannot_write_is_refused():
    document = _document()
    with pytest.raises(PdfValidationException, match="cannot write"):
        document.pages[0].add_stamp(TextStamp("中文"))


def test_a_text_stamp_needs_one_of_the_standard_fonts():
    document = _document()
    with pytest.raises(PdfValidationException, match="standard 14"):
        document.pages[0].add_stamp(TextStamp("X", font_name="Comic Sans"))
    document.pages[0].add_stamp(TextStamp("X", font_name="Times-BoldItalic"))


@pytest.mark.parametrize(
    "color", [(0.5,), (1, 0, 0), (0, 1, 1, 0)], ids=["gray", "rgb", "cmyk"]
)
def test_a_text_stamp_takes_any_of_the_three_colour_spaces(color):
    document = _document()
    document.pages[0].add_stamp(TextStamp("X", font_size=40, color=color))
    assert _pixels(_reloaded(document), lambda pixel: sum(pixel) < 700)


def test_a_colour_that_is_not_a_colour_is_refused():
    document = _document()
    with pytest.raises(PdfValidationException, match=r"1 \(grey\), 3 \(RGB\) or 4"):
        document.pages[0].add_stamp(TextStamp("X", color=(1, 0)))
    with pytest.raises(PdfValidationException, match=r"0\.\.1 or 0\.\.255"):
        document.pages[0].add_stamp(TextStamp("X", color=(300, 0, 0)))
    with pytest.raises(PdfValidationException, match="CMYK"):
        document.pages[0].add_stamp(TextStamp("X", color=(0, 2, 0, 0)))


def test_a_stamp_colour_takes_the_same_scales_the_page_api_takes():
    # One rule for colour: a stamp used to insist on 0..1 while Page.add_text
    # had always taken 0..255 as well. Both go through the same normaliser now.
    document = _document()
    document.pages[0].add_stamp(TextStamp("X", font_size=40, color=(255, 0, 0)))
    document.pages[0].add_stamp(TextStamp("Y", font_size=40, color="#ff0000"))
    document.pages[0].add_stamp(TextStamp("Z", font_size=40, color=0.25))
    assert _pixels(_reloaded(document), lambda pixel: sum(pixel) < 700)


def test_a_stamps_text_survives_the_round_trip_as_text():
    document = _document(size=200)
    document.pages[0].add_stamp(TextStamp("CONFIDENTIAL", font_size=12))
    assert "CONFIDENTIAL" in _reloaded(document).extract_text()


# --- page numbers -----------------------------------------------------------


def test_a_page_number_stamp_says_a_different_thing_on_each_page():
    document = _document(3, size=200)
    document.add_stamp(PageNumberStamp("{page} of {total}", font_size=12))
    reopened = _reloaded(document)
    assert [
        reopened.pages[index].extract_text().strip() for index in range(3)
    ] == ["1 of 3", "2 of 3", "3 of 3"]


def test_a_page_number_stamp_can_start_anywhere():
    document = _document(2, size=200)
    document.add_stamp(PageNumberStamp("{page}", font_size=12, starting_number=7))
    reopened = _reloaded(document)
    assert [reopened.pages[i].extract_text().strip() for i in range(2)] == ["7", "8"]


def test_a_page_number_format_may_only_name_the_page_and_the_total():
    document = _document(size=200)
    with pytest.raises(PdfValidationException, match=r"page.*total"):
        document.pages[0].add_stamp(PageNumberStamp("{chapter}"))


# --- stamping many pages ----------------------------------------------------


def test_one_stamp_on_many_pages_is_one_object():
    document = _document(6)
    document.add_stamp(ImageStamp(_png((255, 0, 0)), width=10, height=10))
    engine = document._engine_pdf
    forms = set()
    for index in range(6):
        resources = engine._resolve(
            engine._get_page_dict(index).mapping[PdfName("Resources")]
        )
        xobjects = engine._resolve(resources.mapping[PdfName("XObject")])
        forms.update(
            reference.object_number for reference in xobjects.mapping.values()
        )
    # The form and the image it draws, shared by all six pages.
    assert len(forms) == 1
    assert all(_pixels(_reloaded(document), _red, page=index) for index in range(6))


def test_only_the_pages_asked_for_are_stamped():
    document = _document(4)
    document.add_stamp(ImageStamp(_png((255, 0, 0)), width=10, height=10), pages=[1, 3])
    reopened = _reloaded(document)
    assert [bool(_pixels(reopened, _red, page=index)) for index in range(4)] == [
        False,
        True,
        False,
        True,
    ]


def test_pages_can_be_named_by_slice():
    document = _document(4)
    document.add_stamp(ImageStamp(_png((255, 0, 0)), width=10, height=10), pages=slice(2, None))
    reopened = _reloaded(document)
    assert [bool(_pixels(reopened, _red, page=index)) for index in range(4)] == [
        False,
        False,
        True,
        True,
    ]


def test_a_page_that_does_not_exist_is_refused():
    document = _document(2)
    with pytest.raises(Exception, match=r"page|index"):
        document.add_stamp(TextStamp("X"), pages=[5])


def test_two_stamps_on_one_page_both_land():
    document = _document()
    page = document.pages[0]
    page.add_stamp(
        ImageStamp(
            _png((255, 0, 0)), width=10, height=10,
            horizontal_alignment=HorizontalAlignment.LEFT,
            vertical_alignment=VerticalAlignment.BOTTOM,
        )
    )
    page.add_stamp(
        ImageStamp(
            _png((0, 0, 255)), width=10, height=10,
            horizontal_alignment=HorizontalAlignment.RIGHT,
            vertical_alignment=VerticalAlignment.TOP,
        )
    )
    reopened = _reloaded(document)
    assert min(x for x, _ in _pixels(reopened, _red)) == 0
    assert max(x for x, _ in _pixels(reopened, _blue)) == SIZE - 1


# --- what a stamp will not accept -------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"opacity": 1.5}, "opacity"),
        ({"opacity": -0.1}, "opacity"),
        ({"zoom": 0}, "zoom"),
        ({"zoom": -2}, "zoom"),
        ({"font_size": 0}, "font_size"),
    ],
)
def test_a_stamp_that_cannot_be_drawn_is_refused(kwargs, message):
    document = _document()
    with pytest.raises(PdfValidationException, match=message):
        document.pages[0].add_stamp(TextStamp("X", **kwargs))


def test_an_image_stamp_keeps_the_images_proportions():
    # Four times as wide as it is tall: giving one side derives the other.
    document = _document(size=200)
    wide = _png((255, 0, 0), width=8, height=2)
    document.pages[0].add_stamp(ImageStamp(wide, width=40))
    red = _pixels(_reloaded(document), _red)
    assert max(x for x, _ in red) - min(x for x, _ in red) == 39
    assert max(y for _, y in red) - min(y for _, y in red) == 9

    document = _document(size=200)
    document.pages[0].add_stamp(ImageStamp(wide, height=20))
    red = _pixels(_reloaded(document), _red)
    assert max(x for x, _ in red) - min(x for x, _ in red) == 79
    assert max(y for _, y in red) - min(y for _, y in red) == 19


def test_an_image_stamp_defaults_to_a_point_per_pixel():
    document = _document()
    document.pages[0].add_stamp(ImageStamp(_png((255, 0, 0), size=12)))
    red = _pixels(_reloaded(document), _red)
    assert max(x for x, _ in red) - min(x for x, _ in red) == 11


def test_an_image_stamp_refuses_a_size_that_is_not_one():
    document = _document()
    with pytest.raises(PdfValidationException, match="width"):
        document.pages[0].add_stamp(ImageStamp(_png((255, 0, 0)), width=0))
    with pytest.raises(TypeError):
        document.pages[0].add_stamp(ImageStamp(42))


def test_an_image_stamp_reads_a_file(tmp_path):
    path = tmp_path / "mark.png"
    path.write_bytes(_png((255, 0, 0), size=8))
    document = _document()
    document.pages[0].add_stamp(ImageStamp(path, width=20, height=20))
    assert len(_pixels(_reloaded(document), _red)) == 400

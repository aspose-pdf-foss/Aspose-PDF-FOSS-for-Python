"""Contact sheets preserve page appearance, selection, labels and resource limits."""

from __future__ import annotations

import io
import sys
import weakref

import pytest

from aspose_pdf import (
    Document,
    NumberingStyle,
    PageLabel,
    PdfLoadLimits,
    PdfResourceLimitException,
    RasterizedPage,
)
from aspose_pdf.engine import contact_sheet
from aspose_pdf.exceptions import (
    AsposePdfException,
    PdfValidationException,
    UnsupportedFeatureException,
)


def _colored_page(document, color, size=(20, 20)):
    page = document.pages.add(size=size)
    page.draw_rectangle(0, 0, *size, fill_color=color, stroke_color=None)
    return page


def _pixels(raster, x, y, width, height):
    return b"".join(
        raster.pixels[
            ((y + row) * raster.width + x) * 3 : ((y + row) * raster.width + x + width)
            * 3
        ]
        for row in range(height)
    )


def _saved(document):
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def test_grid_selection_order_repeats_margins_and_last_empty_cell():
    with Document() as document:
        for color in ("#f00", "#0f0", "#00f"):
            _colored_page(document, color)
        sheet = document.render_contact_sheet(
            pages=(i for i in (2, 0, 2)),
            columns=2,
            thumbnail_size=(10, 10),
            margin=2,
            spacing=3,
            labels=False,
            background=(19, 29, 39),
            antialias=False,
        )
        assert isinstance(sheet, RasterizedPage)
        assert (sheet.width, sheet.height, sheet.dpi) == (27, 27, 72)
        assert len(sheet.pixels) == 27 * 27 * 3
        assert _pixels(sheet, 2, 2, 10, 10) == bytes((0, 0, 255)) * 100
        assert _pixels(sheet, 15, 2, 10, 10) == bytes((255, 0, 0)) * 100
        assert _pixels(sheet, 2, 15, 10, 10) == bytes((0, 0, 255)) * 100
        assert _pixels(sheet, 15, 15, 10, 10) == bytes((19, 29, 39)) * 100
        assert sheet.get_pixel(0, 0) == sheet.get_pixel(12, 12) == (19, 29, 39)


@pytest.mark.parametrize("antialias", [False, True, 2, 8])
@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_cropped_rotated_offset_pages_keep_aspect_and_match_page_render(
    rotation, antialias
):
    with Document() as document:
        page = document.pages.add(((-20, 10, 100, 90), b"1 0 0 rg -20 10 50 80 re f"))
        page.crop_box = (-10, 20, 70, 60)
        page.rotation = rotation
        sheet = document.render_contact_sheet(
            thumbnail_size=(40, 40),
            margin=0,
            labels=False,
            antialias=antialias,
            background=(10, 20, 30),
            page_background=(200, 220, 240),
        )
        reference = page.render(
            scale=0.5, antialias=antialias, background=(200, 220, 240)
        )
        x, y = (40 - reference.width) // 2, (40 - reference.height) // 2
        assert (
            _pixels(sheet, x, y, reference.width, reference.height) == reference.pixels
        )
        assert sheet.get_pixel(0, 0) == (10, 20, 30)
        assert len(sheet.pixels) == 40 * 40 * 3


def test_different_page_shapes_are_centered_without_stretching():
    with Document() as document:
        _colored_page(document, "#f00", (40, 20))
        _colored_page(document, "#00f", (20, 40))
        sheet = document.render_contact_sheet(
            thumbnail_size=(20, 20),
            labels=False,
            margin=0,
            spacing=0,
            antialias=False,
        )
        assert (sheet.width, sheet.height) == (40, 20)
        assert _pixels(sheet, 0, 5, 20, 10) == bytes((255, 0, 0)) * 200
        assert _pixels(sheet, 25, 0, 10, 20) == bytes((0, 0, 255)) * 200
        assert sheet.get_pixel(0, 4) == sheet.get_pixel(24, 0) == (240, 240, 240)


def test_text_and_scanned_pages_survive_pdf_and_contact_sheet_png_round_trips():
    with Document() as source:
        source.pages.add(size=(80, 40)).add_text("Review 123", 3, 20, font_size=10)
        scan = RasterizedPage(2, 1, bytes((255, 0, 0, 0, 0, 255))).to_png()
        source.pages.add(size=(80, 40)).add_image(scan, 0, 0, width=80, height=40)
        data = _saved(source)
    with Document(data) as document:
        sheet = document.render_contact_sheet(
            columns=1,
            thumbnail_size=(40, 20),
            labels=False,
            margin=0,
            spacing=0,
            antialias=False,
        )
        assert (
            _pixels(sheet, 0, 0, 40, 20)
            == document.render_page(0, scale=0.5, antialias=False).pixels
        )
        assert sheet.get_pixel(5, 30) == (255, 0, 0)
        assert sheet.get_pixel(35, 30) == (0, 0, 255)
        with Document() as output:
            output.pages.add(size=(40, 40)).add_image(
                sheet.to_png(), 0, 0, width=40, height=40
            )
            assert output.render_page(0, antialias=False).pixels == sheet.pixels


def test_labels_show_source_positions_and_keep_pdf_page_labels_separate():
    with Document() as document:
        for _ in range(12):
            document.pages.add(size=(30, 20))
        document.page_labels[0] = PageLabel(
            prefix="Appendix ", style=NumberingStyle.NUMERALS_ROMAN_LOWERCASE
        )
        options = dict(
            thumbnail_size=(30, 20),
            columns=2,
            margin=0,
            spacing=0,
            antialias=False,
            label_color=(80, 90, 100),
        )
        sheet = document.render_contact_sheet(pages=[11, 0, 11], **options)
        assert (sheet.width, sheet.height) == (60, 84)
        assert _pixels(sheet, 0, 20, 30, 22) == _pixels(sheet, 0, 62, 30, 22)
        assert _pixels(sheet, 0, 20, 30, 22) != _pixels(sheet, 30, 20, 30, 22)
        # The top row of the digit 1 has its central two pixels set.
        assert sheet.get_pixel(44, 24) == (80, 90, 100)
        assert sheet.get_pixel(40, 24) == (240, 240, 240)
        no_labels = document.render_contact_sheet(
            pages=[11, 0, 11], labels=False, **options
        )
        assert (no_labels.width, no_labels.height) == (60, 40)


def test_all_decimal_digits_and_multidigit_numbers_are_distinct_and_contained():
    with Document() as document:
        for _ in range(10):
            document.pages.add(size=(22, 1))
        sheet = document.render_contact_sheet(
            columns=10,
            thumbnail_size=(22, 1),
            margin=0,
            spacing=0,
            antialias=False,
        )
        bands = [_pixels(sheet, i * 22, 1, 22, 22) for i in range(10)]
        assert len(set(bands)) == 10
        assert all(band.count(b"\x00") > 0 for band in bands)
        assert len(sheet.pixels) == 220 * 23 * 3


def test_defaults_and_columns_larger_than_selection():
    with Document() as document:
        document.pages.add()
        default = document.render_contact_sheet(antialias=False)
        assert (default.width, default.height) == (184, 266)
        assert default == document.render_contact_sheet(columns=10**20, antialias=False)


@pytest.mark.parametrize("hidden", [False, True])
def test_annotation_inclusion_uses_visible_appearances_without_modifying_pdf(hidden):
    with Document() as document:
        page = document.pages.add(size=(40, 40))
        page.annotations.add(
            "Square",
            (5, 5, 35, 35),
            "Review",
            properties={"IC": [1, 0, 0], "F": 2 if hidden else 0},
        )
        before = _saved(document)
        options = dict(thumbnail_size=(40, 40), labels=False, margin=0, antialias=False)
        sheet = document.render_contact_sheet(**options)
        assert (sheet.get_pixel(20, 20) == (255, 0, 0)) is (not hidden)
        assert document.render_contact_sheet(
            include_annotations=False, **options
        ).get_pixel(20, 20) == (255, 255, 255)
        assert _saved(document) == before


@pytest.mark.parametrize("source_kind", ["bytes", "file", "streaming"])
@pytest.mark.parametrize("encrypted", [False, True])
def test_round_trips_and_lazy_selected_content_stay_unchanged(
    tmp_path, monkeypatch, source_kind, encrypted
):
    path = tmp_path / "source.pdf"
    with Document() as source:
        _colored_page(source, "#f00", (20, 40))
        _colored_page(source, "#0f0", (40, 20))
        _colored_page(source, "#00f")
        source.pages[0].rotation = 90
        source.info["Title"] = "Original"
        source.add_attachment("note.txt", b"Keep me")
        if encrypted:
            source.encrypt(user_password="secret", owner_password="owner")
        source.save(path)
    original_bytes = path.read_bytes()
    opener = Document.open_streaming if source_kind == "streaming" else Document
    argument = original_bytes if source_kind == "bytes" else path
    with opener(argument, password="secret" if encrypted else None) as document:
        protected = document.is_encrypted
        original = document._engine_pdf.get_page_content
        visited = []

        def content(index):
            assert index in (0, 2)
            visited.append(index)
            return original(index)

        monkeypatch.setattr(document._engine_pdf, "get_page_content", content)
        sheet = document.render_contact_sheet(
            pages=[2, 0],
            thumbnail_size=(20, 20),
            labels=False,
            margin=0,
            spacing=0,
            antialias=False,
        )
        assert _pixels(sheet, 0, 0, 20, 20) == bytes((0, 0, 255)) * 400
        assert set(visited) == {0, 2}
        assert document.is_encrypted is protected
        assert document.info["Title"] == "Original"
        assert document.attachments == {"note.txt": b"Keep me"}
        if source_kind == "streaming":
            assert document._engine_pdf.page_contents == []
            assert document._engine_pdf._extracted_text is None
    assert path.read_bytes() == original_bytes


@pytest.mark.parametrize("pages", [[], (), iter(())])
def test_empty_selection_is_rejected(pages):
    with Document() as document:
        document.pages.add()
        with pytest.raises(PdfValidationException, match="at least one page"):
            document.render_contact_sheet(pages=pages)


def test_empty_document_is_rejected():
    with Document() as document:
        with pytest.raises(PdfValidationException, match="at least one page"):
            document.render_contact_sheet()


@pytest.mark.parametrize(
    "option,value",
    [
        ("columns", True),
        ("columns", 0),
        ("columns", 1.5),
        ("columns", "2"),
        ("margin", -1),
        ("margin", True),
        ("margin", 0.5),
        ("spacing", -1),
        ("spacing", False),
        ("spacing", "3"),
        ("thumbnail_size", None),
        ("thumbnail_size", "12"),
        ("thumbnail_size", (1,)),
        ("thumbnail_size", (0, 1)),
        ("thumbnail_size", (1, -1)),
        ("thumbnail_size", (True, 1)),
        ("thumbnail_size", (1, 2.5)),
        ("thumbnail_size", (1, float("inf"))),
        ("labels", 1),
        ("include_annotations", None),
        ("antialias", None),
        ("antialias", 0),
        ("antialias", 9),
        ("antialias", 1.5),
        ("antialias", "2"),
        ("antialias", float("nan")),
        ("background", "fff"),
        ("background", (0, 0)),
        ("background", (0, 0, 0, 0)),
        ("background", (0, 0, 256)),
        ("background", (-1, 0, 0)),
        ("page_background", (True, 0, 0)),
        ("page_background", (1.0, 0, 0)),
        ("label_color", None),
        ("label_color", (0, 0, float("nan"))),
        ("pages", "0"),
        ("pages", 0),
        ("pages", [0, True]),
        ("pages", [0, -1]),
        ("pages", [0, 1]),
        ("pages", [0, 0.5]),
    ],
)
def test_options_and_complete_selection_are_validated_before_rendering(
    monkeypatch, option, value
):
    with Document() as document:
        document.pages.add()
        monkeypatch.setattr(
            document,
            "render_page",
            lambda *a, **kw: pytest.fail("invalid input must not render"),
        )
        with pytest.raises(PdfValidationException):
            document.render_contact_sheet(**{option: value})


def test_invalid_options_are_validated_even_for_empty_selection():
    with Document() as document:
        with pytest.raises(PdfValidationException, match="columns"):
            document.render_contact_sheet(pages=[], columns=0)


def test_label_width_is_validated_for_all_selected_page_numbers():
    with Document() as document:
        for _ in range(10):
            document.pages.add(size=(10, 10))
        with pytest.raises(PdfValidationException, match="too narrow"):
            document.render_contact_sheet(pages=[0, 9], thumbnail_size=(20, 20))
        assert (
            document.render_contact_sheet(
                pages=[9], thumbnail_size=(1, 1), labels=False, margin=0
            ).width
            == 1
        )


@pytest.mark.parametrize(
    "options,limit",
    [
        (dict(thumbnail_size=(20, 20), labels=False, margin=0, antialias=False), 399),
        (dict(thumbnail_size=(20, 20), labels=False, margin=0, antialias=True), 3599),
        (dict(thumbnail_size=(20, 20), labels=False, margin=0, antialias=8), 25599),
        (dict(thumbnail_size=(1, 1), labels=False, margin=10**100), 1000),
        (dict(thumbnail_size=(10**100, 1), labels=False, margin=0), 1000),
    ],
)
def test_sheet_and_supersampling_limits_are_checked_before_canvas_or_content(
    monkeypatch, options, limit
):
    with Document(limits=PdfLoadLimits(max_raster_pixels=limit)) as document:
        document.pages.add(size=(20, 20))
        monkeypatch.setattr(
            document,
            "render_page",
            lambda *a, **kw: pytest.fail("limits must be checked first"),
        )

        class UnexpectedCanvas(bytearray):
            def __new__(cls, *args, **kwargs):
                pytest.fail("limits must precede allocation")

        monkeypatch.setattr(contact_sheet, "bytearray", UnexpectedCanvas, raising=False)
        with pytest.raises(PdfResourceLimitException, match="max_raster_pixels"):
            document.render_contact_sheet(**options)


def test_supersampling_limit_exact_boundary_and_result_encoder_limits():
    limits = PdfLoadLimits(max_raster_pixels=3600)
    with Document(limits=limits) as document:
        document.pages.add(size=(20, 20))
        raster = document.render_contact_sheet(
            thumbnail_size=(20, 20), labels=False, margin=0
        )
        assert raster.width == raster.height == 20
        assert raster._limits == limits


@pytest.mark.parametrize("pages", [None, [0, 0]])
def test_page_selection_limit_including_repeated_indices(pages):
    with Document(limits=PdfLoadLimits(max_container_items=1)) as document:
        document.pages.add(size=(10, 10))
        document.pages.add(size=(10, 10))
        with pytest.raises(PdfResourceLimitException, match="max_container_items"):
            document.render_contact_sheet(pages=pages)


def test_only_one_small_thumbnail_is_alive_at_a_time(monkeypatch):
    with Document() as document:
        for _ in range(3):
            document.pages.add(size=(10**12, 10**12))
        original = document.render_page
        previous = None

        def render(index, **options):
            nonlocal previous
            assert previous is None or previous() is None
            result = original(index, **options)
            assert (result.width, result.height) == (12, 12)
            previous = weakref.ref(result)
            return result

        monkeypatch.setattr(document, "render_page", render)
        sheet = document.render_contact_sheet(
            thumbnail_size=(12, 12), labels=False, antialias=False
        )
        assert sheet.width == 84
        assert previous() is None


@pytest.mark.parametrize(
    "page_size,thumbnail",
    [
        ((89.174, 37.123), (17, 19)),
        ((10**300, 10**300), (13, 7)),
        ((0.000001, 10**100), (1, 1)),
        ((0.1, 0.3), (3, 9)),
    ],
)
def test_fractional_and_extreme_finite_geometry_stays_inside_cell(page_size, thumbnail):
    with Document() as document:
        document.pages.add(size=page_size)
        sheet = document.render_contact_sheet(
            thumbnail_size=thumbnail, labels=False, margin=0, antialias=False
        )
        assert (sheet.width, sheet.height) == thumbnail
        assert len(sheet.pixels) == thumbnail[0] * thumbnail[1] * 3


def test_invalid_later_page_geometry_is_rejected_before_rendering(monkeypatch):
    with Document() as document:
        document.pages.add()
        document.pages.add()
        document._engine_pdf.pages[1] = (0, 0, float("inf"), 10)
        monkeypatch.setattr(
            document, "render_page", lambda *a, **kw: pytest.fail("preflight all pages")
        )
        with pytest.raises(PdfValidationException, match="finite"):
            document.render_contact_sheet()


@pytest.mark.parametrize(
    "suffix,encoding",
    [
        ("", {}),
        (".png", {}),
        (".PNG", {"mode": "gray"}),
        (
            ".tif",
            {"mode": "bilevel", "compression": "none", "threshold": 90, "dither": True},
        ),
        (".tiff", {"mode": "gray"}),
        (".jpg", {"quality": 60}),
        (".jpeg", {"quality": 80, "mode": "gray"}),
    ],
)
def test_save_formats_and_encoding_options_match_raster_save(
    tmp_path, suffix, encoding
):
    with Document() as document:
        _colored_page(document, "#808080")
        layout = dict(thumbnail_size=(12, 12), labels=False, margin=0, antialias=False)
        expected = document.render_contact_sheet(**layout).save(
            tmp_path / ("expected" + suffix), **encoding
        )
        actual = document.save_contact_sheet(
            tmp_path / "nested" / ("actual" + suffix), **layout, **encoding
        )
        assert actual.read_bytes() == expected.read_bytes()
        assert actual.suffix == (suffix or ".png")


def test_webp_optional_dependency_missing_preserves_destination(tmp_path, monkeypatch):
    path = tmp_path / "sheet.webp"
    path.write_bytes(b"Original")
    monkeypatch.setitem(sys.modules, "PIL", None)
    with Document() as document:
        document.pages.add(size=(10, 10))
        with pytest.raises(UnsupportedFeatureException, match="images"):
            document.save_contact_sheet(
                path, thumbnail_size=(10, 10), labels=False, margin=0
            )
    assert path.read_bytes() == b"Original"


def test_webp_options_with_optional_dependency(tmp_path):
    image = pytest.importorskip("PIL.Image")
    features = pytest.importorskip("PIL.features")
    if not features.check("webp"):
        pytest.skip("Pillow has no WebP encoder")
    with Document() as document:
        _colored_page(document, "#123456")
        path = document.save_contact_sheet(
            tmp_path / "sheet.webp",
            thumbnail_size=(10, 10),
            labels=False,
            margin=0,
            antialias=False,
            lossless=True,
            quality=100,
            method=6,
        )
        with image.open(path) as opened:
            assert opened.size == (10, 10)
            assert opened.convert("RGB").tobytes() == bytes((18, 52, 86)) * 100


@pytest.mark.parametrize("failure", ["selection", "format", "encoding", "render"])
def test_failed_save_preserves_existing_output_and_source(
    tmp_path, monkeypatch, failure
):
    path = tmp_path / ("existing.pdf" if failure == "format" else "existing.png")
    path.write_bytes(b"Original")
    with Document() as document:
        document.pages.add(size=(10, 10))
        before = _saved(document)
        options = dict(thumbnail_size=(10, 10), labels=False, margin=0)
        if failure == "selection":
            options["pages"] = [1]
        elif failure == "encoding":
            options["mode"] = "invalid"
        elif failure == "render":

            def fail(*args, **kwargs):
                raise PdfValidationException("Cannot render")

            monkeypatch.setattr(document, "render_page", fail)
        with pytest.raises(PdfValidationException):
            document.save_contact_sheet(path, **options)
        assert path.read_bytes() == b"Original"
        assert _saved(document) == before


def test_disposed_document_rejects_both_methods(tmp_path):
    document = Document()
    document.dispose()
    with pytest.raises(AsposePdfException):
        document.render_contact_sheet()
    with pytest.raises(AsposePdfException):
        document.save_contact_sheet(tmp_path / "sheet.png")

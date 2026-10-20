"""Automatic margin trimming changes only the visible page box."""

from __future__ import annotations

import io
import struct
import zlib

import pytest

from aspose_pdf import (
    Document,
    PageStamp,
    PdfLoadLimits,
    PdfResourceLimitException,
    RasterizedPage,
)
from aspose_pdf.engine.cos import PdfArray, PdfName, PdfNumber
from aspose_pdf.exceptions import (
    AsposePdfException,
    PdfParseException,
    PdfSecurityException,
    PdfValidationException,
)
from aspose_pdf.pages import Page

_MEDIA = (-30, 10, 114, 202)
_CROP = (-18, 22, 102, 178)
_INK = (6, 46, 66, 130)


def _bytes(document):
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _png(width, height, samples, channels=3):
    def chunk(kind, data):
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    stride = width * channels
    rows = b"".join(
        b"\0" + samples[i : i + stride] for i in range(0, len(samples), stride)
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(
            b"IHDR",
            struct.pack(
                ">IIBBBBB", width, height, 8, 2 if channels == 3 else 6, 0, 0, 0
            ),
        )
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("dpi", [36, 72, 96, 144])
@pytest.mark.parametrize("margin", [0, 3])
def test_crop_uses_unrotated_pdf_coordinates_and_survives_round_trips(
    tmp_path, rotation, dpi, margin
):
    with Document() as document:
        page = document.pages.add((_MEDIA, b""))
        page.crop_box = _CROP
        page.rotation = rotation
        page.bleed_box, page.trim_box, page.art_box = _MEDIA, _CROP, _INK
        page.draw_rectangle(6, 46, 60, 84, fill_color="#000", stroke_color=None)
        expected = (6 - margin, 46 - margin, 66 + margin, 130 + margin)
        reference = page.render(clip=expected, dpi=dpi, antialias=False)
        content = page.content
        assert page.auto_crop(margin, dpi=dpi) is page
        assert page.crop_box == pytest.approx(expected)
        assert page.media_box == _MEDIA and page.rotation == rotation
        assert (page.bleed_box, page.trim_box, page.art_box) == (_MEDIA, _CROP, _INK)
        assert page.content == content
        assert page.render(dpi=dpi, antialias=False).pixels == reference.pixels
        path = tmp_path / "trimmed.pdf"
        document.save(path)
        for source in (_bytes(document), path):
            with Document(source) as loaded:
                assert loaded.pages[0].crop_box == pytest.approx(expected)
                assert loaded.pages[0].media_box == _MEDIA
                assert loaded.pages[0].rotation == rotation
                assert loaded.pages[0].content == content and loaded.validate()


@pytest.mark.parametrize("kind", ["empty", "white", "near_white", "transparent", "ocr"])
@pytest.mark.parametrize("margin", [0, 7, 1e308])
def test_blank_looking_pages_are_exact_no_ops(kind, margin):
    with Document() as document:
        page = document.pages.add(size=(64, 64))
        if kind in ("white", "near_white", "transparent"):
            rgba = {
                "white": b"\xff\xff\xff",
                "near_white": b"\xfa\xfa\xfa",
                "transparent": b"\0\0\0\0",
            }[kind]
            page.add_image(_png(1, 1, rgba, len(rgba)), 0, 0, width=64, height=64)
        elif kind == "ocr":
            page.add_text("OCR", 10, 30)
            document._engine_pdf._set_page_content(
                0, page.content.replace(b"BT", b"BT 3 Tr", 1)
            )
        before = _bytes(document)
        assert page.auto_crop(margin) is page
        assert _bytes(document) == before
        assert PdfName("CropBox") not in document._engine_pdf._get_page_dict(0).mapping


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_margins_are_clamped_to_the_existing_crop_and_never_reveal_hidden_edges(
    rotation,
):
    with Document() as document:
        page = document.pages.add(size=(100, 80))
        page.crop_box = (10, 10, 90, 70)
        page.rotation = rotation
        page.draw_rectangle(0, 0, 5, 5, fill_color="#000", stroke_color=None)
        page.draw_rectangle(12, 20, 30, 30, fill_color="#000", stroke_color=None)
        page.auto_crop(margin=5)
        assert page.crop_box == pytest.approx((10, 15, 47, 55))
        before = _bytes(document)
        page.auto_crop(margin=1e308)
        assert _bytes(document) == before


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_inherited_boxes_are_overridden_only_on_the_selected_page(rotation):
    with Document() as document:
        for _ in range(2):
            document.pages.add((_MEDIA, b""))
        engine = document._engine_pdf
        parent = engine._resolve(engine._get_page_dict(0).mapping[PdfName("Parent")])
        crop = PdfArray([PdfNumber(v) for v in _CROP])
        parent.mapping[PdfName("CropBox")] = crop
        parent.mapping[PdfName("Rotate")] = PdfNumber(rotation + 360)
        page = document.pages[0]
        page.draw_rectangle(6, 46, 60, 84, fill_color="#000", stroke_color=None)
        page.auto_crop()
        assert page.crop_box == pytest.approx(_INK)
        assert document.pages[1].crop_box == _CROP
        assert parent.mapping[PdfName("CropBox")] is crop
        assert PdfName("CropBox") not in engine._get_page_dict(1).mapping
        assert page.rotation == rotation and document.pages[1].rotation == rotation


@pytest.mark.parametrize(
    "rotation,expected",
    [
        (0, (2, 6.95, 8.75, 11.45)),
        (90, (3.5, 5.75, 8, 12.5)),
        (180, (9.35, 7.25, 16.1, 11.75)),
        (270, (10.1, 6.2, 14.6, 12.95)),
    ],
)
def test_pixel_edges_use_dpi_instead_of_rounded_raster_dimensions(
    monkeypatch, rotation, expected
):
    with Document() as document:
        page = document.pages.add(size=(20, 20))
        page.crop_box = (-0.25, 3.5, 18.35, 15.2)
        page.media_box = (-1.25, 2.5, 19.15, 16.2)
        page.rotation = rotation
        width, height = (25, 16) if rotation in (0, 180) else (16, 25)
        pixels = bytearray(b"\xff" * width * height * 3)
        for y in range(5, 11):
            for x in range(3, 12):
                pixels[(y * width + x) * 3 : (y * width + x) * 3 + 3] = b"\0\0\0"

        def render(_page, **options):
            assert options == {
                "dpi": 96.0,
                "background": (255, 255, 255),
                "antialias": False,
                "draw_annotations": True,
            }
            return RasterizedPage(width, height, bytes(pixels), dpi=96)

        monkeypatch.setattr(Page, "render", render)
        page.auto_crop(dpi=96)
        assert page.crop_box == pytest.approx(expected)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("reversed_media", [False, True])
def test_full_bleed_dark_content_is_a_no_op_even_for_fractional_and_reversed_boxes(
    rotation, reversed_media
):
    with Document() as document:
        media = (12.3, 22.4, -1.2, -2.3) if reversed_media else (-1.2, -2.3, 12.3, 22.4)
        page = document.pages.add((media, b"0 g -1.2 -2.3 13.5 24.7 re f\n"))
        page.crop_box = (-10, -20, 30, 40)
        page.rotation = rotation
        before = _bytes(document)
        page.auto_crop(dpi=96)
        assert _bytes(document) == before


@pytest.mark.parametrize(
    "include_annotations,hidden,expected",
    [
        (True, False, (9, 9, 51, 51)),
        (False, False, (39, 39, 51, 51)),
        (True, True, (39, 39, 51, 51)),
    ],
)
def test_annotations_are_counted_without_moving_or_deleting_them(
    include_annotations, hidden, expected
):
    with Document() as document:
        page = document.pages.add(size=(64, 64))
        page.draw_rectangle(40, 40, 10, 10, fill_color="#000", stroke_color=None)
        page.annotations.add(
            "Square",
            (10, 10, 20, 20),
            "Note",
            properties={"IC": [0, 0, 0], "F": 2 if hidden else 0},
        )
        annotation = page.annotations.to_list()[0]
        page.auto_crop(margin=1, include_annotations=include_annotations)
        assert page.crop_box == pytest.approx(expected)
        assert page.annotations.to_list()[0] == annotation


def test_widgets_layers_transparency_and_form_xobjects_follow_the_renderer():
    with Document() as document, Document() as artwork:
        page = document.pages.add(size=(100, 100))
        hidden = document.layers.add("Hidden", visible=False)
        with page.layer(hidden):
            page.draw_rectangle(0, 0, 100, 100, fill_color="#000", stroke_color=None)
        with page.graphics(opacity=0):
            page.draw_rectangle(0, 0, 100, 100, fill_color="#000", stroke_color=None)
        art = artwork.pages.add(size=(20, 20))
        art.draw_rectangle(0, 0, 20, 20, fill_color="#000", stroke_color=None)
        page.add_stamp(
            PageStamp(
                source=art,
                x_indent=30,
                y_indent=40,
                horizontal_alignment="Left",
                vertical_alignment="Bottom",
            )
        )
        page.auto_crop()
        assert page.crop_box == pytest.approx((30, 40, 50, 60))
    with Document() as document:
        page = document.pages.add(size=(100, 100))
        field = document.form.add_text_field(
            "value", 0, (10, 20, 90, 50), value="A field"
        )
        document.generate_field_appearances()
        rect = field.rect
        page.auto_crop(include_annotations=False)
        assert page.crop_box == (0, 0, 100, 100)
        page.auto_crop()
        assert page.crop_box != (0, 0, 100, 100)
        assert field.rect == rect and field.value == "A field" and field.page_index == 0


def test_threshold_and_dark_speckles_apply_to_every_color_channel():
    with Document() as document:
        page = document.pages.add(size=(64, 64))
        page.draw_rectangle(5, 5, 50, 50, fill_color=(255, 255, 249), stroke_color=None)
        page.draw_rectangle(20, 20, 10, 10, fill_color="#000", stroke_color=None)
        page.auto_crop(white_threshold=249)
        assert page.crop_box == (20, 20, 30, 30)
    with Document() as document:
        page = document.pages.add(size=(64, 64))
        page.draw_rectangle(5, 5, 50, 50, fill_color=(255, 255, 249), stroke_color=None)
        page.draw_rectangle(20, 20, 10, 10, fill_color="#000", stroke_color=None)
        page.auto_crop()
        assert page.crop_box == (5, 5, 55, 55)


def test_repeated_cropping_is_stable_and_keeps_cropped_out_data():
    with Document() as document:
        page = document.pages.add(size=(100, 100))
        page.add_text("Hidden text", 2, 2, color="#fff")
        page.draw_rectangle(20, 30, 40, 50, fill_color="#000", stroke_color=None)
        original = page.content
        page.auto_crop(margin=3)
        first = _bytes(document)
        page.auto_crop(margin=3)
        assert _bytes(document) == first
        assert page.content == original and "Hidden text" in page.extract_text()
        page.crop_box = None
        assert page.crop_box == (0, 0, 100, 100) and page.content == original


@pytest.mark.parametrize(
    "option,value",
    [
        ("margin", -1),
        ("margin", True),
        ("margin", "3"),
        ("margin", None),
        ("margin", float("inf")),
        ("margin", float("nan")),
        pytest.param("margin", 10**400, id="overflowing-margin"),
        ("dpi", 0),
        ("dpi", False),
        ("dpi", "72"),
        ("dpi", float("inf")),
        ("dpi", 5e-324),
        ("white_threshold", 0),
        ("white_threshold", 256),
        ("white_threshold", 250.0),
        ("white_threshold", True),
        ("include_annotations", 1),
        ("include_annotations", None),
    ],
)
def test_invalid_options_fail_before_rendering_or_changing_a_box(
    monkeypatch, option, value
):
    with Document() as document:
        page = document.pages.add(size=(10, 10))
        before = _bytes(document)

        def unexpected(*args, **kwargs):
            pytest.fail("Invalid options must not render a page")

        monkeypatch.setattr(Page, "render", unexpected)
        with pytest.raises(PdfValidationException, match=option):
            page.auto_crop(**{option: value})
        assert _bytes(document) == before


def test_render_and_resource_limit_failures_leave_the_crop_unchanged(monkeypatch):
    with Document(limits=PdfLoadLimits(max_raster_pixels=99)) as document:
        page = document.pages.add(size=(10, 10))
        before = _bytes(document)
        with pytest.raises(PdfResourceLimitException, match="max_raster_pixels"):
            page.auto_crop()
        assert _bytes(document) == before
    with Document() as document:
        page = document.pages.add(size=(10, 10))
        page.crop_box = (1, 1, 9, 9)
        before = _bytes(document)

        def failed(*args, **kwargs):
            raise PdfParseException("Cannot render")

        monkeypatch.setattr(Page, "render", failed)
        with pytest.raises(PdfParseException, match="Cannot render"):
            page.auto_crop()
        assert _bytes(document) == before


def test_disposed_and_out_of_range_pages_are_rejected():
    with Document() as document:
        page = document.pages.add(size=(10, 10))
        document.pages.delete(0)
        with pytest.raises(IndexError):
            page.auto_crop()
    with pytest.raises(AsposePdfException, match="disposed"):
        page.auto_crop()


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("encrypted", [False, True])
def test_scanned_pages_crop_lazily_and_keep_their_resources_after_save(
    tmp_path, streaming, encrypted, monkeypatch
):
    source_path = tmp_path / "scans.pdf"
    output_path = tmp_path / "cropped.pdf"
    samples = bytearray(b"\xff" * 20 * 16 * 3)
    for y in range(3, 9):
        for x in range(4, 12):
            samples[(y * 20 + x) * 3 : (y * 20 + x) * 3 + 3] = b"\0\0\xff"
    with Document() as source:
        source.pages.add(size=(100, 80)).add_text("Unselected", 5, 30)
        source.pages.add(size=(100, 80)).add_image(
            _png(20, 16, bytes(samples)), 0, 0, width=100, height=80
        )
        source.pages.add(size=(100, 80))
        if encrypted:
            source.encrypt(user_password="secret", owner_password="owner")
        source.save(source_path)
    before = source_path.read_bytes()
    opener = Document.open_streaming if streaming else Document
    with opener(source_path, password="secret" if encrypted else None) as document:
        protected = document.is_encrypted
        original = document._engine_pdf.get_page_content
        visited = []

        def content(index):
            assert index == 1
            visited.append(index)
            return original(index)

        with monkeypatch.context() as patch:
            patch.setattr(document._engine_pdf, "get_page_content", content)
            document.pages[1].auto_crop(margin=3)
            assert visited and document.pages[1].crop_box == pytest.approx(
                (17, 32, 63, 68)
            )
            if streaming:
                assert document._engine_pdf.page_contents == []
            assert document.is_encrypted == protected
        document.save(output_path)
    if streaming and encrypted:
        with pytest.raises(PdfSecurityException, match="Password required"):
            with Document(output_path):
                pass
        with Document.open_streaming(output_path, password="secret") as loaded:
            assert loaded.is_encrypted
            assert loaded.pages[1].crop_box == pytest.approx((17, 32, 63, 68))
    with Document(output_path, password="secret" if encrypted else None) as loaded:
        assert loaded.pages[1].crop_box == pytest.approx((17, 32, 63, 68))
        assert loaded.pages[1].render(antialias=False).get_pixel(10, 10) == (0, 0, 255)
        assert loaded.pages[0].crop_box == (0, 0, 100, 80)
        assert loaded.pages[2].crop_box == (0, 0, 100, 80)
        assert "Unselected" in loaded.pages[0].extract_text()
    assert source_path.read_bytes() == before

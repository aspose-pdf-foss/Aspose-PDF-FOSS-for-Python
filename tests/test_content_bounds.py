"""Read-only visible content measurements use unrotated PDF page coordinates."""

from __future__ import annotations

import io
import struct
import weakref
import zlib
from itertools import repeat

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
    PdfValidationException,
)
from aspose_pdf.pages import Page

MEDIA = (-30, 10, 114, 202)
CROP = (-18, 22, 102, 178)
INK = (6, 46, 66, 130)


def _bytes(document):
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


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
def test_unrotated_bounds_roundtrip_preserve_boxes_content_and_file_bytes(
    rotation, dpi, tmp_path
):
    path = tmp_path / "measured.pdf"
    with Document() as document:
        page = document.pages.add((MEDIA, b""))
        page.crop_box = CROP
        page.rotation = rotation
        page.bleed_box, page.trim_box, page.art_box = MEDIA, CROP, INK
        page.draw_rectangle(6, 46, 60, 84, fill_color="#000", stroke_color=None)
        before = _bytes(document)
        result = page.get_content_bounds(dpi=dpi)
        assert result == pytest.approx(INK)
        assert isinstance(result, tuple) and len(result) == 4
        assert document.get_content_bounds(dpi=dpi) == {0: result}
        assert (page.media_box, page.crop_box, page.rotation) == (MEDIA, CROP, rotation)
        assert (page.bleed_box, page.trim_box, page.art_box) == (MEDIA, CROP, INK)
        assert _bytes(document) == before
        document.save(path)
    assert result == pytest.approx(INK)
    for source in (before, path):
        with Document(source) as reopened:
            assert reopened.pages[0].get_content_bounds(dpi=dpi) == pytest.approx(INK)
            assert reopened.pages[0].crop_box == CROP


@pytest.mark.parametrize(
    "kind",
    [
        "empty",
        "white",
        "near_white",
        "transparent",
        "invisible_text",
        "cropped_out",
        "hidden_layer",
    ],
)
def test_blank_looking_pages_return_none_without_adding_crop_entries(kind):
    with Document() as document:
        page = document.pages.add(size=(64, 64))
        if kind in ("white", "near_white", "transparent"):
            data = {
                "white": b"\xff\xff\xff",
                "near_white": b"\xfa\xfa\xfa",
                "transparent": b"\0\0\0\0",
            }[kind]
            page.add_image(_png(1, 1, data, len(data)), 0, 0, width=64, height=64)
        elif kind == "invisible_text":
            page.add_text("OCR", 10, 30)
            document._engine_pdf._set_page_content(
                0, page.content.replace(b"BT", b"BT 3 Tr", 1)
            )
        elif kind == "cropped_out":
            page.draw_rectangle(2, 2, 5, 5, fill_color="#000", stroke_color=None)
            page.crop_box = (20, 20, 60, 60)
        elif kind == "hidden_layer":
            hidden = document.layers.add("Hidden", visible=False)
            with page.layer(hidden):
                page.draw_rectangle(0, 0, 64, 64, fill_color="#000", stroke_color=None)
        before = _bytes(document)
        assert page.get_content_bounds() is None
        assert document.get_content_bounds() == {0: None}
        assert _bytes(document) == before
        if kind != "cropped_out":
            assert (
                PdfName("CropBox") not in document._engine_pdf._get_page_dict(0).mapping
            )


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("reverse", [False, True])
def test_full_bleed_content_returns_the_visible_box_even_with_fractional_edges(
    rotation, reverse
):
    with Document() as document:
        media = (12.3, 22.4, -1.2, -2.3) if reverse else (-1.2, -2.3, 12.3, 22.4)
        page = document.pages.add((media, b"0 g -1.2 -2.3 13.5 24.7 re f\n"))
        page.crop_box = (-10, -20, 30, 40)
        page.rotation = rotation
        before = _bytes(document)
        assert page.get_content_bounds(dpi=96) == pytest.approx(
            (-1.2, -2.3, 12.3, 22.4)
        )
        assert _bytes(document) == before


@pytest.mark.parametrize(
    "rotation,expected",
    [
        (0, (2, 6.95, 8.75, 11.45)),
        (90, (3.5, 5.75, 8, 12.5)),
        (180, (9.35, 7.25, 16.1, 11.75)),
        (270, (10.1, 6.2, 14.6, 12.95)),
    ],
)
def test_fractional_pixel_edges_are_mapped_using_dpi_without_stretching_the_raster(
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
        before = page.crop_box
        assert page.get_content_bounds(dpi=96) == pytest.approx(expected)
        assert page.crop_box == before


def test_inherited_boxes_and_rotation_stay_inherited_after_inspection():
    with Document() as document:
        for _ in range(2):
            document.pages.add((MEDIA, b""))
        pdf = document._engine_pdf
        parent = pdf._resolve(pdf._get_page_dict(0).mapping[PdfName("Parent")])
        crop = PdfArray([PdfNumber(v) for v in CROP])
        parent.mapping[PdfName("CropBox")] = crop
        parent.mapping[PdfName("Rotate")] = PdfNumber(450)
        page = document.pages[0]
        page.draw_rectangle(6, 46, 60, 84, fill_color="#000", stroke_color=None)
        before = _bytes(document)
        assert document.get_content_bounds() == {0: INK, 1: None}
        assert parent.mapping[PdfName("CropBox")] is crop
        assert all(
            PdfName("CropBox") not in pdf._get_page_dict(i).mapping for i in range(2)
        )
        assert _bytes(document) == before


@pytest.mark.parametrize(
    "threshold,expected",
    [
        (1, (20, 20, 30, 30)),
        (249, (20, 20, 30, 30)),
        (250, (5, 5, 55, 55)),
        (255, (5, 5, 55, 55)),
    ],
)
def test_threshold_is_strict_and_checks_every_rgb_channel(threshold, expected):
    with Document() as document:
        page = document.pages.add(size=(64, 64))
        page.draw_rectangle(5, 5, 50, 50, fill_color=(255, 255, 249), stroke_color=None)
        page.draw_rectangle(20, 20, 10, 10, fill_color="#000", stroke_color=None)
        assert page.get_content_bounds(white_threshold=threshold) == expected


@pytest.mark.parametrize(
    "include,hidden,expected",
    [
        (True, False, (10, 10, 50, 50)),
        (False, False, (40, 40, 50, 50)),
        (True, True, (40, 40, 50, 50)),
    ],
)
def test_visible_annotation_appearances_are_optional_and_not_edited(
    include, hidden, expected
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
        annotations = page.annotations.to_list()
        before = _bytes(document)
        assert page.get_content_bounds(include_annotations=include) == expected
        assert page.annotations.to_list() == annotations
        assert _bytes(document) == before


def test_clipping_transparency_form_stamps_and_layer_edits_follow_rendering():
    with Document() as document, Document() as artwork:
        page = document.pages.add(size=(100, 100))
        page.crop_box = (10, 10, 90, 90)
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
                x_indent=20,
                y_indent=30,
                horizontal_alignment="Left",
                vertical_alignment="Bottom",
            )
        )
        before = _bytes(document)
        assert page.get_content_bounds() == (30, 40, 50, 60)
        assert _bytes(document) == before
        hidden.visible = True
        assert page.get_content_bounds() == (10, 10, 90, 90)


def test_widget_appearances_and_text_are_detected_without_moving_them():
    with Document() as document:
        page = document.pages.add(size=(100, 100))
        field = document.form.add_text_field(
            "value", 0, (10, 20, 90, 50), value="A field"
        )
        document.generate_field_appearances()
        before = _bytes(document)
        assert page.get_content_bounds(include_annotations=False) is None
        result = page.get_content_bounds()
        assert result is not None and 10 <= result[0] < result[2] <= 90
        assert 20 <= result[1] < result[3] <= 50
        assert field.rect == (10, 20, 90, 50) and field.value == "A field"
        assert _bytes(document) == before
    with Document() as document:
        page = document.pages.add(size=(100, 100))
        page.add_text("Hello", 10, 40, font_size=12)
        bounds = page.get_content_bounds(dpi=144)
        assert bounds is not None and 10 <= bounds[0] < bounds[2] < 100
        assert 20 < bounds[1] < bounds[3] < 60


def test_stroke_extents_and_consistency_with_a_later_crop():
    with Document() as document:
        page = document.pages.add(size=(100, 100))
        page.draw_rectangle(
            20, 30, 40, 50, fill_color=None, stroke_color="#000", line_width=4
        )
        original = page.crop_box
        bounds = page.get_content_bounds()
        assert bounds == (18, 28, 62, 82) and page.crop_box == original
        page.auto_crop()
        assert page.crop_box == bounds


def test_batch_selection_is_validated_deduplicated_ordered_and_detached(monkeypatch):
    with Document() as document:
        for _ in range(3):
            document.pages.add(size=(30, 30))
        document.pages[0].draw_rectangle(
            5, 6, 10, 12, fill_color="#000", stroke_color=None
        )
        document.pages[2].draw_rectangle(
            2, 3, 4, 5, fill_color="#000", stroke_color=None
        )
        render = Page.render
        visited = []

        def track(page, **options):
            visited.append(page.index)
            return render(page, **options)

        monkeypatch.setattr(Page, "render", track)
        bounds = document.get_content_bounds(pages=iter([2, 0, 2, 1]))
        assert list(bounds) == visited == [2, 0, 1]
        assert bounds == {2: (2, 3, 6, 8), 0: (5, 6, 15, 18), 1: None}
        visited.clear()
        assert document.get_content_bounds(pages=[]) == {} and not visited
        for pages in ([0, 3], [-1], [True], [0.0], "0", 0):
            with pytest.raises(PdfValidationException):
                document.get_content_bounds(pages=pages)
        assert not visited
        bounds.clear()
        assert document.pages[0].get_content_bounds() == (5, 6, 15, 18)
        detached = document.get_content_bounds(pages=[0])
    assert detached == {0: (5, 6, 15, 18)}


@pytest.mark.parametrize(
    "option,value",
    [
        ("dpi", 0),
        ("dpi", -1),
        ("dpi", False),
        ("dpi", "72"),
        ("dpi", float("nan")),
        ("dpi", float("inf")),
        ("dpi", 5e-324),
        pytest.param("dpi", 10**400, id="overflowing-dpi"),
        ("white_threshold", 0),
        ("white_threshold", 256),
        ("white_threshold", 250.0),
        ("white_threshold", True),
        ("white_threshold", "250"),
        ("include_annotations", 1),
        ("include_annotations", None),
    ],
)
def test_invalid_options_fail_before_render_even_for_empty_selections(
    monkeypatch, option, value
):
    with Document() as document:
        page = document.pages.add(size=(10, 10))
        monkeypatch.setattr(
            Page,
            "render",
            lambda *a, **k: pytest.fail("invalid options must not render"),
        )
        before = _bytes(document)
        with pytest.raises(PdfValidationException, match=option):
            page.get_content_bounds(**{option: value})
        with pytest.raises(PdfValidationException, match=option):
            document.get_content_bounds(pages=[], **{option: value})
        assert _bytes(document) == before


def test_resource_failures_unbounded_selection_and_failed_second_page_do_not_edit_document(
    monkeypatch,
):
    with Document(limits=PdfLoadLimits(max_raster_pixels=99)) as document:
        page = document.pages.add(size=(10, 10))
        before = _bytes(document)
        with pytest.raises(PdfResourceLimitException, match="max_raster_pixels"):
            page.get_content_bounds()
        assert _bytes(document) == before
    with Document(limits=PdfLoadLimits(max_container_items=20)) as document:
        document.pages.add(size=(10, 10))
        with pytest.raises(PdfResourceLimitException, match="max_container_items"):
            document.get_content_bounds(pages=repeat(0))
    with Document() as document:
        for _ in range(2):
            document.pages.add(size=(10, 10))
        before = _bytes(document)
        render = Page.render

        def fail_second(page, **options):
            if page.index == 1:
                raise PdfParseException("Cannot render page")
            return render(page, **options)

        monkeypatch.setattr(Page, "render", fail_second)
        with pytest.raises(PdfParseException, match="Cannot render"):
            document.get_content_bounds()
        assert _bytes(document) == before


def test_batches_release_each_raster_before_rendering_the_next(monkeypatch):
    with Document() as document:
        for _ in range(3):
            document.pages.add(size=(10, 10))
        document.pages[1].draw_rectangle(
            2, 3, 4, 5, fill_color="#000", stroke_color=None
        )
        previous = []
        render = Page.render

        def track(page, **options):
            assert all(ref() is None for ref in previous)
            raster = render(page, **options)
            previous.append(weakref.ref(raster))
            return raster

        monkeypatch.setattr(Page, "render", track)
        assert document.get_content_bounds() == {0: None, 1: (2, 3, 6, 8), 2: None}
        assert all(ref() is None for ref in previous)


@pytest.mark.parametrize("kind", ["bytes", "file", "streaming"])
@pytest.mark.parametrize("encrypted", [False, True])
def test_scans_stream_lazily_and_preserve_files_cursors_and_metadata(
    tmp_path, monkeypatch, kind, encrypted
):
    path = tmp_path / "source.pdf"
    pixels = bytearray(b"\xff" * 20 * 16 * 3)
    for y in range(3, 9):
        for x in range(4, 12):
            pixels[(y * 20 + x) * 3 : (y * 20 + x) * 3 + 3] = b"\0\0\xff"
    with Document() as source:
        source.pages.add(size=(100, 80)).add_text("Unselected", 5, 30)
        source.pages.add(size=(100, 80)).add_image(
            _png(20, 16, bytes(pixels)), 0, 0, width=100, height=80
        )
        source.pages.add(size=(100, 80))
        source.info["Title"] = "Keep title"
        source.add_attachment("keep.txt", b"keep")
        if encrypted:
            source.encrypt("secret", "owner")
        source.save(path)
    before = path.read_bytes()
    opener = Document.open_streaming if kind == "streaming" else Document
    with opener(
        before if kind == "bytes" else path, password="secret" if encrypted else None
    ) as document:
        pdf = document._engine_pdf
        protection = document.is_encrypted
        cursor = pdf._image_cursor, pdf._image_names
        contents, text = list(pdf.page_contents), pdf._extracted_text
        read = pdf.get_page_content
        visited = []

        def selected(index):
            assert index == 1
            visited.append(index)
            return read(index)

        monkeypatch.setattr(pdf, "get_page_content", selected)
        assert document.get_content_bounds(pages=[1, 1]) == {1: (20, 35, 60, 65)}
        assert visited == [1]
        assert (pdf._image_cursor, pdf._image_names) == cursor
        assert pdf.page_contents == contents and pdf._extracted_text is text
        assert document.is_encrypted is protection
        assert document.pages[1].crop_box == (0, 0, 100, 80)
        assert document.info["Title"] == "Keep title" and document.attachments == {
            "keep.txt": b"keep"
        }
    assert path.read_bytes() == before


def test_empty_documents_deleted_pages_and_disposal_guards():
    with Document() as document:
        assert document.get_content_bounds() == {}
        page = document.pages.add(size=(10, 10))
        document.pages.delete(0)
        with pytest.raises(IndexError):
            page.get_content_bounds()
    with pytest.raises(AsposePdfException, match="disposed"):
        page.get_content_bounds()
    with pytest.raises(AsposePdfException, match="disposed"):
        document.get_content_bounds()


@pytest.mark.parametrize("box", [(10, 20, 10, 70), (10, 20, 60, 20)])
def test_degenerate_page_boxes_use_renderers_fallback_without_repairing_source(box):
    with Document() as document:
        page = document.pages.add((box, b"0 g 0 0 612 792 re f\n"))
        before = _bytes(document)
        assert page.get_content_bounds() == (0, 0, 612, 792)
        assert page.media_box == box
        assert _bytes(document) == before


def test_user_unit_does_not_change_the_returned_page_coordinate_system():
    with Document() as document:
        page = document.pages.add(size=(100, 100))
        page.draw_rectangle(20, 30, 40, 50, fill_color="#000", stroke_color=None)
        document._engine_pdf._get_page_dict(0).mapping[PdfName("UserUnit")] = PdfNumber(
            2
        )
        before = _bytes(document)
        assert page.get_content_bounds(dpi=144) == (20, 30, 60, 80)
        assert _bytes(document) == before

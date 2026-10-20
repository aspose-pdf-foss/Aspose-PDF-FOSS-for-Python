"""Visual blank-page detection includes scans, annotations and view geometry."""

from __future__ import annotations

import io
import math
import struct
import weakref
import zlib

import pytest

from aspose_pdf import Document, PdfLoadLimits, PdfResourceLimitException
from aspose_pdf.exceptions import (
    AsposePdfException,
    PdfParseException,
    PdfValidationException,
)
from aspose_pdf.pages import Page


def _bytes(document):
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _png(rgb, *, alpha=None):
    def chunk(kind, body):
        return (
            struct.pack(">I", len(body))
            + kind
            + body
            + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
        )

    pixel = bytes(rgb) + (bytes([alpha]) if alpha is not None else b"")
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(
            b"IHDR",
            struct.pack(">IIBBBBB", 1, 1, 8, 6 if alpha is not None else 2, 0, 0, 0),
        )
        + chunk(b"IDAT", zlib.compress(b"\0" + pixel))
        + chunk(b"IEND", b"")
    )


@pytest.mark.parametrize("on_disk", [False, True])
@pytest.mark.parametrize(
    "kind,expected",
    [
        ("empty", True),
        ("graphics_state", True),
        ("white_fill", True),
        ("white_text", True),
        ("text", False),
        ("line", False),
        ("fill", False),
        ("white_scan", True),
        ("near_white_scan", True),
        ("dark_scan", False),
        ("transparent_image", True),
    ],
)
def test_visible_content_is_classified_before_and_after_round_trip(
    tmp_path, on_disk, kind, expected
):
    with Document() as document:
        page = document.pages.add(size=(64, 64))
        if kind == "graphics_state":
            document._engine_pdf._set_page_content(0, b"q 2 w 1 0 0 rg Q\n")
        elif kind in ("white_fill", "fill"):
            page.draw_rectangle(
                5,
                5,
                20,
                20,
                fill_color="#fff" if kind == "white_fill" else "#000",
                stroke_color=None,
            )
        elif kind in ("white_text", "text"):
            page.add_text(
                "Ink", 5, 25, color="#fff" if kind == "white_text" else "#000"
            )
        elif kind == "line":
            page.draw_line(5, 5, 45, 45)
        elif kind.endswith("scan") or kind == "transparent_image":
            color = {
                "white_scan": (255, 255, 255),
                "near_white_scan": (250, 250, 250),
            }.get(kind, (0, 0, 0))
            page.add_image(
                _png(color, alpha=0 if kind == "transparent_image" else None),
                0,
                0,
                width=64,
                height=64,
            )
        original = _bytes(document)
        assert page.is_blank() is expected
        assert document.find_blank_pages() == ([0] if expected else [])
        assert _bytes(document) == original
        source = original
        if on_disk:
            source = tmp_path / "scan.pdf"
            document.save(source)
        with Document(source) as loaded:
            assert loaded.pages[0].is_blank() is expected
            assert loaded.find_blank_pages() == ([0] if expected else [])


@pytest.mark.parametrize(
    "rgb,threshold,expected",
    [
        ((250, 250, 250), 250, True),
        ((249, 255, 255), 250, False),
        ((255, 255, 249), 250, False),
        ((255, 255, 255), 255, True),
        ((254, 255, 255), 255, False),
        ((0, 0, 0), 1, False),
        ((1, 1, 1), 1, True),
    ],
)
def test_white_threshold_includes_equality_and_checks_every_color_channel(
    rgb, threshold, expected
):
    with Document() as document:
        page = document.pages.add(size=(7, 7))
        page.add_image(_png(rgb), 0, 0, width=7, height=7)
        assert page.is_blank(white_threshold=threshold) is expected


def test_ink_ratio_boundary_and_noise_tolerance_are_exact():
    with Document() as document:
        page = document.pages.add(size=(7, 7))
        page.draw_rectangle(0, 0, 1, 1, fill_color="#000", stroke_color=None)
        ratio = 1 / 49
        assert page.is_blank() is False
        assert page.is_blank(max_ink_ratio=ratio) is True
        assert page.is_blank(max_ink_ratio=math.nextafter(ratio, 0)) is False
        assert document.find_blank_pages(max_ink_ratio=ratio) == [0]
        page.draw_rectangle(1, 0, 1, 1, fill_color="#000", stroke_color=None)
        assert page.is_blank(max_ink_ratio=ratio) is False
        assert page.is_blank(max_ink_ratio=1) is True


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_crop_box_and_rotation_use_the_visible_part_of_the_page(rotation):
    with Document() as document:
        page = document.pages.add(size=(64, 64))
        page.draw_rectangle(0, 0, 10, 10, fill_color="#000", stroke_color=None)
        page.crop_box = (20, 20, 60, 60)
        page.rotation = rotation
        original = _bytes(document)
        assert page.is_blank() is True
        assert _bytes(document) == original
        page.draw_rectangle(25, 25, 5, 5, fill_color="#000", stroke_color=None)
        assert page.is_blank() is False
        assert page.crop_box == (20, 20, 60, 60) and page.rotation == rotation


def test_hidden_layers_transparency_and_white_overlays_follow_rendered_appearance():
    with Document() as document:
        page = document.pages.add(size=(64, 64))
        layer = document.layers.add("Hidden", visible=False)
        with page.layer(layer):
            page.add_text("Secret", 5, 25)
        with page.graphics(opacity=0):
            page.draw_rectangle(0, 0, 64, 64, fill_color="#000", stroke_color=None)
        assert page.is_blank() is True
        layer.visible = True
        assert page.is_blank() is False
        page.draw_rectangle(0, 0, 64, 64, fill_color="#fff", stroke_color=None)
        assert page.is_blank() is True


def test_invisible_ocr_text_is_visually_blank_even_when_extractable():
    with Document() as document:
        page = document.pages.add(size=(64, 64))
        page.add_text("OCR", 5, 25)
        document._engine_pdf._set_page_content(
            0, page.content.replace(b"BT", b"BT 3 Tr", 1)
        )
        assert "OCR" in page.extract_text()
        assert page.is_blank() is True
        assert "OCR" in page.extract_text()


@pytest.mark.parametrize("hidden", [False, True])
def test_annotations_count_by_default_and_can_be_excluded(hidden):
    with Document() as document:
        page = document.pages.add(size=(64, 64))
        page.annotations.add(
            "Square",
            (5, 5, 25, 25),
            "Visible note",
            properties={"IC": [0, 0, 0], "F": 2 if hidden else 0},
        )
        original = _bytes(document)
        assert page.is_blank() is hidden
        assert page.is_blank(include_annotations=False) is True
        assert document.find_blank_pages(include_annotations=False) == [0]
        assert _bytes(document) == original


def test_form_widget_appearance_counts_as_visible_content():
    with Document() as document:
        page = document.pages.add(size=(100, 64))
        document.form.add_text_field("value", 0, (5, 10, 90, 35), value="Filled field")
        document.generate_field_appearances()
        assert page.is_blank() is False
        assert page.is_blank(include_annotations=False) is True


def test_document_selection_is_snapshot_validated_and_deduplicated(monkeypatch):
    with Document() as document:
        for index in range(4):
            page = document.pages.add(size=(10, 10))
            if index == 1:
                page.draw_rectangle(0, 0, 2, 2, fill_color="#000", stroke_color=None)
        original = Page.render
        visited = []

        def render(page, **options):
            visited.append(page.index)
            return original(page, **options)

        monkeypatch.setattr(Page, "render", render)
        assert document.find_blank_pages(pages=(i for i in (3, 1, 0, 3, 2))) == [
            3,
            0,
            2,
        ]
        assert visited == [3, 1, 0, 2]


@pytest.mark.parametrize(
    "pages", [True, 1, "0", b"0", [True], [0.0], [None], [-1], [0, 5]]
)
def test_invalid_selection_fails_before_any_render(pages, monkeypatch):
    with Document() as document:
        document.pages.add(size=(10, 10))

        def unexpected(*args, **kwargs):
            pytest.fail("Invalid selections must not render any pages")

        monkeypatch.setattr(Page, "render", unexpected)
        with pytest.raises(PdfValidationException):
            document.find_blank_pages(pages=pages)


_INVALID_OPTIONS = [
    ("dpi", 0),
    ("dpi", -1),
    ("dpi", True),
    ("dpi", "72"),
    ("dpi", float("nan")),
    ("dpi", float("inf")),
    ("dpi", 5e-324),
    pytest.param("dpi", 10**400, id="overflowing-dpi"),
    ("white_threshold", 0),
    ("white_threshold", 256),
    ("white_threshold", True),
    ("white_threshold", 250.0),
    ("max_ink_ratio", -0.1),
    ("max_ink_ratio", 1.01),
    ("max_ink_ratio", float("nan")),
    ("max_ink_ratio", float("inf")),
    ("max_ink_ratio", False),
    ("max_ink_ratio", "0.1"),
    ("include_annotations", 1),
    ("include_annotations", None),
]


@pytest.mark.parametrize("option,value", _INVALID_OPTIONS)
def test_options_are_validated_for_page_and_empty_document_selection(
    option, value, monkeypatch
):
    with Document() as document, Document() as empty:
        page = document.pages.add(size=(10, 10))

        def unexpected(*args, **kwargs):
            pytest.fail("Invalid options must not render any pages")

        monkeypatch.setattr(Page, "render", unexpected)
        for call in (page.is_blank, document.find_blank_pages, empty.find_blank_pages):
            with pytest.raises(PdfValidationException, match=option):
                call(**{option: value})
        with pytest.raises(PdfValidationException, match=option):
            document.find_blank_pages(pages=[], **{option: value})


def test_empty_document_and_empty_selection_return_no_matches(monkeypatch):
    with Document() as document:
        assert document.find_blank_pages() == []
        document.pages.add(size=(10, 10))

        def unexpected(*args, **kwargs):
            pytest.fail("An empty selection must not render pages")

        monkeypatch.setattr(Page, "render", unexpected)
        assert document.find_blank_pages(pages=[]) == []


def test_disposed_documents_and_stale_pages_are_rejected():
    with Document() as document:
        page = document.pages.add(size=(10, 10))
        document.pages.delete(0)
        with pytest.raises(IndexError):
            page.is_blank()
    with pytest.raises(AsposePdfException, match="disposed"):
        document.find_blank_pages(pages=[])
    with pytest.raises(AsposePdfException, match="disposed"):
        page.is_blank()


def test_render_failures_propagate_and_do_not_modify_the_document(monkeypatch):
    with Document() as document:
        for _ in range(2):
            document.pages.add(size=(10, 10))
        before = _bytes(document)
        original = Page.render

        def render(page, **options):
            if page.index == 1:
                raise PdfParseException("Cannot render page")
            return original(page, **options)

        monkeypatch.setattr(Page, "render", render)
        with pytest.raises(PdfParseException, match="Cannot render"):
            document.find_blank_pages()
        assert document.page_count == 2 and _bytes(document) == before


def test_resource_limits_bound_rasters_and_selection_before_work(monkeypatch):
    with Document(limits=PdfLoadLimits(max_raster_pixels=99)) as document:
        page = document.pages.add(size=(10, 10))
        with pytest.raises(PdfResourceLimitException, match="max_raster_pixels"):
            page.is_blank()
        with pytest.raises(PdfResourceLimitException, match="max_raster_pixels"):
            document.find_blank_pages()
        assert page.is_blank(dpi=36) is True
    with Document(limits=PdfLoadLimits(max_container_items=2)) as document:
        for _ in range(3):
            document.pages.add(size=(10, 10))

        def unexpected(*args, **kwargs):
            pytest.fail("Oversized selections must be rejected before rendering")

        monkeypatch.setattr(Page, "render", unexpected)
        for selection in (None, [0, 1, 2], (0 for _ in range(3))):
            with pytest.raises(PdfResourceLimitException, match="max_container_items"):
                document.find_blank_pages(pages=selection)


def test_each_raster_is_released_before_rendering_the_next(monkeypatch):
    with Document() as document:
        for _ in range(3):
            document.pages.add(size=(10, 10))
        original = Page.render
        previous = None

        def render(page, **options):
            nonlocal previous
            assert previous is None or previous() is None
            raster = original(page, **options)
            previous = weakref.ref(raster)
            return raster

        monkeypatch.setattr(Page, "render", render)
        assert document.find_blank_pages() == [0, 1, 2]
        assert previous() is None


@pytest.mark.parametrize("dpi", [36, 72, 96, 144])
def test_detection_uses_the_requested_resolution_without_changing_page_geometry(dpi):
    with Document() as document:
        page = document.pages.add(size=(72, 72))
        assert page.is_blank(dpi=dpi) is True
        page.draw_rectangle(0, 0, 72, 36, fill_color="#000", stroke_color=None)
        assert page.is_blank(dpi=dpi, max_ink_ratio=0.49) is False
        assert page.is_blank(dpi=dpi, max_ink_ratio=0.51) is True
        assert page.media_box == (0, 0, 72, 72)


def test_streaming_content_limit_is_enforced_only_when_that_page_is_selected(tmp_path):
    path = tmp_path / "limited.pdf"
    with Document() as source:
        source.pages.add(((0, 0, 10, 10), b"q Q\n" * 30))
        source.pages.add(size=(10, 10))
        source.save(path)
    with Document.open_streaming(
        path, limits=PdfLoadLimits(max_content_stream_bytes=32)
    ) as document:
        assert document.find_blank_pages(pages=[1]) == [1]
        with pytest.raises(PdfResourceLimitException, match="max_content_stream_bytes"):
            document.find_blank_pages(pages=[0])
        assert document._engine_pdf.page_contents == []


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("encrypted", [False, True])
def test_selected_scanned_pages_work_with_streaming_and_encryption(
    tmp_path, streaming, encrypted, monkeypatch
):
    path = tmp_path / "scans.pdf"
    with Document() as source:
        for color in ((255, 255, 255), (0, 0, 0), (250, 250, 250), (255, 0, 0)):
            source.pages.add(size=(64, 64)).add_image(
                _png(color), 0, 0, width=64, height=64
            )
        if encrypted:
            source.encrypt(user_password="secret", owner_password="owner")
        source.save(path)
    original_bytes = path.read_bytes()
    opener = Document.open_streaming if streaming else Document
    with opener(path, password="secret" if encrypted else None) as document:
        original = document._engine_pdf.get_page_content
        visited = []

        def content(index):
            visited.append(index)
            assert index in (0, 2)
            return original(index)

        monkeypatch.setattr(document._engine_pdf, "get_page_content", content)
        assert document.find_blank_pages(pages=[2, 0, 2]) == [2, 0]
        assert set(visited) == {0, 2}
        if streaming:
            assert document._engine_pdf.page_contents == []
    assert path.read_bytes() == original_bytes

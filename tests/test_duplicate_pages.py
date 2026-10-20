"""Visual duplicate groups depend on rendered pixels, never stored PDF bytes."""

from __future__ import annotations

import io
import struct
import weakref
import zlib
from types import SimpleNamespace

import pytest

from aspose_pdf import (
    Document,
    PageStamp,
    PdfLoadLimits,
    PdfResourceLimitException,
    RasterizedPage,
)
from aspose_pdf.engine import duplicate_pages
from aspose_pdf.exceptions import (
    AsposePdfException,
    PdfParseException,
    PdfValidationException,
)
from aspose_pdf.outlines import OutlineItem
from aspose_pdf.pages import Page


def _bytes(document):
    stream = io.BytesIO()
    document.save(stream)
    return stream.getvalue()


def _png(rgb, alpha=None):
    def chunk(kind, body):
        return (
            struct.pack(">I", len(body))
            + kind
            + body
            + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
        )

    pixel = bytes(rgb) + (b"" if alpha is None else bytes([alpha]))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(
            b"IHDR",
            struct.pack(">IIBBBBB", 1, 1, 8, 2 if alpha is None else 6, 0, 0, 0),
        )
        + chunk(b"IDAT", zlib.compress(b"\0" + pixel))
        + chunk(b"IEND", b"")
    )


def _page(document, color, size=(48, 24)):
    page = document.pages.add(size=size)
    if color is not None:
        page.draw_rectangle(6, 6, 12, 6, fill_color=color, stroke_color=None)
    return page


@pytest.mark.parametrize("dpi", [36, 72, 96, 144])
@pytest.mark.parametrize("on_disk", [False, True])
def test_groups_contain_first_occurrences_and_survive_round_trips(
    tmp_path, dpi, on_disk
):
    with Document() as document:
        for color in ("#000", "#00f", "#000", None, None, "#00f", "#f00"):
            _page(document, color)
        document.info["Title"] = "Original batch"
        before = _bytes(document)
        assert document.find_duplicate_pages(dpi=dpi) == [[0, 2], [1, 5], [3, 4]]
        assert _bytes(document) == before and document.page_count == 7
        source = before
        if on_disk:
            source = tmp_path / "batch.pdf"
            document.save(source)
        with Document(source) as loaded:
            assert loaded.find_duplicate_pages(dpi=dpi) == [[0, 2], [1, 5], [3, 4]]
            assert loaded.info["Title"] == "Original batch"


def test_different_pdf_content_can_have_the_same_rendered_view():
    with Document() as document:
        filled = document.pages.add(size=(24, 24))
        filled.draw_rectangle(0, 0, 24, 24, fill_color="#f00", stroke_color=None)
        scanned = document.pages.add(size=(24, 24))
        scanned.add_image(_png((255, 0, 0)), 0, 0, width=24, height=24)
        assert filled.content != scanned.content
        before = _bytes(document)
        assert document.find_duplicate_pages() == [[0, 1]]
        assert _bytes(document) == before


def test_invisible_data_hidden_layers_and_transparency_do_not_prevent_a_match():
    with Document() as document:
        _page(document, None)
        ocr = _page(document, None)
        ocr.add_text("Stored OCR", 5, 10)
        document._engine_pdf._set_page_content(
            1, ocr.content.replace(b"BT", b"BT 3 Tr", 1)
        )
        transparent = _page(document, None)
        transparent.add_image(_png((0, 0, 0), alpha=0), 0, 0, width=48, height=24)
        white = document.pages.add(size=(48, 24))
        white.draw_rectangle(0, 0, 48, 24, fill_color="#fff", stroke_color=None)
        hidden = _page(document, None)
        layer = document.layers.add("Hidden content", visible=False)
        with hidden.layer(layer):
            hidden.add_text("Hidden", 5, 10)
        _page(document, "#000")
        before = _bytes(document)
        assert document.find_duplicate_pages() == [[0, 1, 2, 3, 4]]
        assert "Stored OCR" in ocr.extract_text()
        assert _bytes(document) == before


@pytest.mark.parametrize("dpi", [36, 72, 96, 144])
def test_rotation_is_applied_to_the_visible_view_instead_of_compared_as_metadata(dpi):
    with Document() as document:
        for size, rotation, rectangle in (
            ((48, 24), 0, (6, 6, 12, 6)),
            ((24, 48), 90, (12, 6, 6, 12)),
            ((48, 24), 180, (30, 12, 12, 6)),
            ((24, 48), 270, (6, 30, 6, 12)),
        ):
            page = document.pages.add(size=size)
            page.rotation = rotation
            page.draw_rectangle(*rectangle, fill_color="#000", stroke_color=None)
        assert document.find_duplicate_pages(dpi=dpi) == [[0, 1, 2, 3]]
        assert [page.rotation for page in document.pages] == [0, 90, 180, 270]


def test_only_the_current_crop_counts_and_box_origins_are_not_compared():
    with Document() as document:
        first = document.pages.add(size=(80, 60))
        first.crop_box = (10, 10, 58, 34)
        first.draw_rectangle(16, 16, 12, 6, fill_color="#000", stroke_color=None)
        first.draw_rectangle(0, 0, 5, 5, fill_color="#f00", stroke_color=None)
        second = document.pages.add(((100, 200, 180, 260), b""))
        second.crop_box = (110, 210, 158, 234)
        second.draw_rectangle(116, 216, 12, 6, fill_color="#000", stroke_color=None)
        assert document.find_duplicate_pages() == [[0, 1]]
        assert first.crop_box == (10, 10, 58, 34)
        assert second.crop_box == (110, 210, 158, 234)


def test_different_dimensions_colors_and_pixel_positions_do_not_match():
    with Document() as document:
        for size in ((12, 8), (8, 12), (12, 9)):
            document.pages.add(size=size)
        for rgb in ((255, 255, 255), (250, 250, 250), (255, 255, 249)):
            document.pages.add(size=(16, 16)).add_image(
                _png(rgb), 0, 0, width=16, height=16
            )
        for x in (1, 2):
            document.pages.add(size=(10, 10)).draw_rectangle(
                x, 1, 1, 1, fill_color="#000", stroke_color=None
            )
        assert document.find_duplicate_pages() == []


@pytest.mark.parametrize("hidden", [0, 2, 32])
@pytest.mark.parametrize("include_annotations", [False, True])
def test_annotation_visibility_and_exclusion_control_the_comparison(
    hidden, include_annotations
):
    with Document() as document:
        for _ in range(3):
            _page(document, None)
        for index in (1, 2):
            document.pages[index].annotations.add(
                "Square",
                (5, 5, 20, 15),
                f"Different note {index}",
                properties={"IC": [0, 0, 0], "F": hidden},
            )
        before = _bytes(document)
        expected = [[1, 2]] if include_annotations and hidden == 0 else [[0, 1, 2]]
        assert (
            document.find_duplicate_pages(include_annotations=include_annotations)
            == expected
        )
        assert _bytes(document) == before


def test_visible_widget_appearances_count_while_field_names_do_not():
    with Document() as document:
        for _ in range(4):
            document.pages.add(size=(100, 64))
        for index, value in ((1, "Same"), (2, "Other"), (3, "Same")):
            document.form.add_text_field(
                f"field-{index}", index, (5, 10, 90, 35), value=value
            )
        document.generate_field_appearances()
        before = _bytes(document)
        assert document.find_duplicate_pages() == [[1, 3]]
        assert document.find_duplicate_pages(include_annotations=False) == [
            [0, 1, 2, 3]
        ]
        assert _bytes(document) == before


def test_selection_is_validated_deduplicated_and_keeps_first_occurrence_order():
    with Document() as document:
        for color in ("#000", "#00f", "#000", "#00f", "#000", "#f00"):
            _page(document, color)
        assert document.find_duplicate_pages(pages=(i for i in (3, 4, 1, 2, 4, 0))) == [
            [3, 1],
            [4, 2, 0],
        ]
        assert document.find_duplicate_pages(pages=[4, 4]) == []


@pytest.mark.parametrize(
    "selection", [True, 1, "0", b"0", [True], [0.0], [None], [-1], [0, 5]]
)
def test_invalid_selection_fails_before_rendering(monkeypatch, selection):
    with Document() as document:
        _page(document, None)

        def forbidden(*args, **kwargs):
            pytest.fail("Invalid selections must not render any page")

        monkeypatch.setattr(Page, "render", forbidden)
        with pytest.raises(PdfValidationException):
            document.find_duplicate_pages(pages=selection)


@pytest.mark.parametrize(
    "option,value",
    [
        ("dpi", 0),
        ("dpi", -1),
        ("dpi", True),
        ("dpi", "72"),
        ("dpi", float("nan")),
        ("dpi", float("inf")),
        ("dpi", 5e-324),
        pytest.param("dpi", 10**400, id="overflowing-dpi"),
        ("include_annotations", 1),
        ("include_annotations", None),
    ],
)
def test_options_are_validated_even_when_no_comparison_is_needed(
    monkeypatch, option, value
):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid options must not render")

    monkeypatch.setattr(Page, "render", forbidden)
    with Document() as empty, Document() as document:
        _page(document, None)
        for instance, pages in ((empty, None), (document, []), (document, [0])):
            with pytest.raises(PdfValidationException, match=option):
                instance.find_duplicate_pages(pages=pages, **{option: value})


def test_empty_and_single_distinct_page_selections_do_not_render(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Fewer than two distinct pages must not render")

    monkeypatch.setattr(Page, "render", forbidden)
    with Document() as document:
        assert document.find_duplicate_pages() == []
        _page(document, None)
        assert document.find_duplicate_pages() == []
        assert document.find_duplicate_pages(pages=[]) == []
        assert document.find_duplicate_pages(pages=[0, 0]) == []


def test_disposed_document_is_rejected():
    with Document() as document:
        _page(document, None)
    with pytest.raises(AsposePdfException, match="disposed"):
        document.find_duplicate_pages(pages=[])


def test_hash_collisions_are_verified_against_all_representatives(monkeypatch):
    monkeypatch.setattr(
        duplicate_pages, "sha256", lambda data: SimpleNamespace(digest=lambda: b"same")
    )
    with Document() as document:
        for color in ("#000", "#00f", "#000", "#f00", "#00f"):
            _page(document, color)
        assert document.find_duplicate_pages() == [[0, 2], [1, 4]]


def test_raster_dimensions_are_part_of_the_fingerprint_even_with_identical_pixel_bytes(
    monkeypatch,
):
    visited = []

    def render(page, **options):
        visited.append(page.index)
        width, height = (2, 3) if page.index == 0 else (3, 2)
        return RasterizedPage(width, height, b"\xff" * 18)

    with Document() as document:
        for _ in range(2):
            _page(document, None)
        monkeypatch.setattr(Page, "render", render)
        assert document.find_duplicate_pages() == []
        assert visited == [0, 1]


def test_at_most_two_rasters_are_live_and_all_are_released_after_success(monkeypatch):
    references = []
    original = Page.render

    def render(page, **options):
        assert sum(reference() is not None for reference in references) <= 1
        raster = original(page, **options)
        references.append(weakref.ref(raster))
        return raster

    with Document() as document:
        for color in ("#000", "#00f", "#000", "#00f", "#000"):
            _page(document, color)
        monkeypatch.setattr(Page, "render", render)
        assert document.find_duplicate_pages() == [[0, 2, 4], [1, 3]]
        assert all(reference() is None for reference in references)


@pytest.mark.parametrize("failure", ["selected", "reference"])
@pytest.mark.parametrize("error", [PdfParseException, PdfResourceLimitException])
def test_render_failures_do_not_return_partial_groups_or_modify_the_pdf(
    monkeypatch, failure, error
):
    with Document() as document:
        for _ in range(4):
            _page(document, None)
        before = _bytes(document)
        original = Page.render
        seen = set()

        def render(page, **options):
            if (failure == "selected" and page.index == 3) or (
                failure == "reference" and page.index in seen
            ):
                raise error("Rendering failed")
            seen.add(page.index)
            return original(page, **options)

        with monkeypatch.context() as patch:
            patch.setattr(Page, "render", render)
            with pytest.raises(error, match="Rendering failed"):
                document.find_duplicate_pages()
        assert document.page_count == 4 and _bytes(document) == before


def test_raster_and_selection_limits_are_enforced_before_comparison(monkeypatch):
    with Document(limits=PdfLoadLimits(max_raster_pixels=99)) as document:
        for _ in range(2):
            document.pages.add(size=(10, 10))
        with pytest.raises(PdfResourceLimitException, match="max_raster_pixels"):
            document.find_duplicate_pages()
        assert document.find_duplicate_pages(dpi=36) == [[0, 1]]
    with Document(limits=PdfLoadLimits(max_container_items=2)) as document:
        for _ in range(3):
            _page(document, None)

        def forbidden(*args, **kwargs):
            pytest.fail("Selection limits must fail before rendering")

        monkeypatch.setattr(Page, "render", forbidden)
        for pages in (None, [0, 1, 2], (0 for _ in range(3))):
            with pytest.raises(PdfResourceLimitException, match="max_container_items"):
                document.find_duplicate_pages(pages=pages)


def test_streaming_limits_apply_only_to_selected_content(tmp_path):
    path = tmp_path / "limited.pdf"
    with Document() as source:
        source.pages.add(((0, 0, 10, 10), b"q Q\n" * 30))
        for _ in range(2):
            source.pages.add(size=(10, 10))
        source.save(path)
    with Document.open_streaming(
        path, limits=PdfLoadLimits(max_content_stream_bytes=32)
    ) as document:
        assert document.find_duplicate_pages(pages=[2, 1]) == [[2, 1]]
        with pytest.raises(PdfResourceLimitException, match="max_content_stream_bytes"):
            document.find_duplicate_pages(pages=[0, 1])
        assert document._engine_pdf.page_contents == []


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("encrypted", [False, True])
def test_selected_scans_remain_lazy_and_keep_source_data_and_encryption(
    tmp_path, streaming, encrypted, monkeypatch
):
    path = tmp_path / "scans.pdf"
    with Document() as source:
        for rgb in ((255, 0, 0), (0, 0, 255), (255, 0, 0), (0, 0, 0)):
            source.pages.add(size=(24, 24)).add_image(
                _png(rgb), 0, 0, width=24, height=24
            )
        source.add_attachment("notes.txt", b"Original attachment")
        source.info["Title"] = "Original scans"
        if encrypted:
            source.encrypt(user_password="secret", owner_password="owner")
        source.save(path)
    before = path.read_bytes()
    opener = Document.open_streaming if streaming else Document
    with opener(path, password="secret" if encrypted else None) as document:
        original = document._engine_pdf.get_page_content
        protected = document.is_encrypted
        visited = []

        def content(index):
            assert index in (0, 2)
            visited.append(index)
            return original(index)

        with monkeypatch.context() as patch:
            patch.setattr(document._engine_pdf, "get_page_content", content)
            assert document.find_duplicate_pages(pages=[2, 0, 2]) == [[2, 0]]
        assert set(visited) == {0, 2}
        assert document.is_encrypted == protected
        assert document.info["Title"] == "Original scans"
        assert document.attachments == {"notes.txt": b"Original attachment"}
        if streaming:
            assert document._engine_pdf.page_contents == []
            assert document._engine_pdf._extracted_text is None
    assert path.read_bytes() == before


def test_render_options_and_reference_verification_are_explicit(monkeypatch):
    visited = []

    def render(page, **options):
        assert options == {
            "dpi": 96.0,
            "background": (255, 255, 255),
            "antialias": False,
            "draw_annotations": False,
        }
        visited.append(page.index)
        return RasterizedPage(2, 1, b"\0" * 6, dpi=96)

    with Document() as document:
        for _ in range(2):
            _page(document, None)
        monkeypatch.setattr(Page, "render", render)
        assert document.find_duplicate_pages(dpi=96, include_annotations=False) == [
            [0, 1]
        ]
        assert visited == [0, 1, 0]


def test_invoked_form_xobjects_match_the_same_direct_drawing():
    with Document() as document, Document() as artwork:
        direct = _page(document, "#000")
        art = _page(artwork, "#000")
        stamped = _page(document, None)
        stamped.add_stamp(PageStamp(source=art))
        assert direct.content != stamped.content
        assert document.find_duplicate_pages() == [[0, 1]]


def test_identical_unrotated_content_with_different_visible_orientation_does_not_match():
    with Document() as document:
        for _ in range(2):
            _page(document, "#000")
        document.pages[1].rotation = 180
        assert document.find_duplicate_pages() == []


def test_fractional_page_sizes_are_compared_at_the_chosen_raster_resolution():
    with Document() as document:
        for size in ((10.1, 10.1), (10.9, 10.9)):
            document.pages.add(size=size)
        assert document.find_duplicate_pages() == [[0, 1]]
        assert document.find_duplicate_pages(dpi=144) == []


def test_new_calls_observe_page_edits_and_do_not_reuse_stale_fingerprints():
    with Document() as document:
        for _ in range(2):
            _page(document, "#000")
        assert document.find_duplicate_pages() == [[0, 1]]
        layer = document.layers.add("Changed view", visible=False)
        with document.pages[1].layer(layer):
            document.pages[1].draw_rectangle(
                30, 10, 5, 5, fill_color="#f00", stroke_color=None
            )
        assert document.find_duplicate_pages() == [[0, 1]]
        layer.visible = True
        assert document.find_duplicate_pages() == []
        layer.visible = False
        assert document.find_duplicate_pages() == [[0, 1]]


def test_group_order_uses_first_occurrence_instead_of_first_completed_pair():
    with Document() as document:
        for color in ("#000", "#00f", "#00f", "#000"):
            _page(document, color)
        assert document.find_duplicate_pages() == [[0, 3], [1, 2]]
        assert document.find_duplicate_pages(pages=[2, 3, 1, 0]) == [[2, 1], [3, 0]]


def test_detection_preserves_live_outlines_full_text_cache_and_cursor():
    with Document() as document:
        for _ in range(2):
            _page(document, None)
        cached = document.extract_text()
        engine = document._engine_pdf
        engine._page_text_cursor = 1
        model = engine._outlines_data
        bookmark = document.outlines.add(OutlineItem("Unsaved bookmark", 1))
        assert document.find_duplicate_pages() == [[0, 1]]
        assert engine._outlines_data is model and engine._outlines_data == []
        assert document.outlines[0] is bookmark and bookmark.page_index == 1
        assert engine._extracted_text is cached and engine._page_text_cursor == 1


def test_reference_verification_obeys_the_cumulative_decoding_limit(tmp_path):
    path = tmp_path / "cumulative.pdf"
    with Document() as source:
        for _ in range(2):
            source.pages.add(((0, 0, 10, 10), b"q Q\n"))
        source.save(path)
    before = path.read_bytes()
    with Document.open_streaming(
        path, limits=PdfLoadLimits(max_total_decoded_bytes=10)
    ) as document:
        with pytest.raises(PdfResourceLimitException, match="max_total_decoded_bytes"):
            document.find_duplicate_pages()
        assert document._engine_pdf.page_contents == []
    with Document.open_streaming(
        path, limits=PdfLoadLimits(max_total_decoded_bytes=12)
    ) as document:
        assert document.find_duplicate_pages() == [[0, 1]]
        assert document._engine_pdf.page_contents == []
    assert path.read_bytes() == before

"""Visual comparison reports exact pixel changes and bounded optional previews."""

from __future__ import annotations

import io
import weakref
from dataclasses import FrozenInstanceError
from random import Random

import pytest

from aspose_pdf import (
    Document,
    PageVisualComparison,
    PdfLoadLimits,
    PdfResourceLimitException,
    RasterizedPage,
)
from aspose_pdf.engine import visual_comparison
from aspose_pdf.exceptions import AsposePdfException, PdfValidationException
from aspose_pdf.pages import Page


def _page(document, color=None, size=(10, 10)):
    page = document.pages.add(size=size)
    if color:
        page.draw_rectangle(0, 0, *size, fill_color=color, stroke_color=None)
    return page


def _bytes(document):
    stream = io.BytesIO()
    document.save(stream)
    return stream.getvalue()


def test_identical_views_ignore_different_streams_and_hidden_ocr_data():
    with Document() as left, Document() as right:
        _page(left, "#f00")
        page = right.pages.add(
            (
                (0, 0, 10, 10),
                b"1 0 0 rg 0 0 10 10 re f BT 3 Tr /F1 5 Tf 1 2 Td (hidden) Tj ET",
            )
        )
        assert left.pages[0].content != page.content
        (result,) = left.compare_visual(right)
        assert isinstance(result, PageVisualComparison)
        assert result.page_index == result.other_page_index == 0
        assert result.size == result.other_size == (10, 10)
        assert result.matches and result.changed_pixels == result.difference_ratio == 0
        assert result.total_pixels == 100 and result.bbox is None
        assert result.diff_image is None
        with pytest.raises(FrozenInstanceError):
            result.matches = False


@pytest.mark.parametrize(
    "dpi,expected",
    [
        (36, (5, 5, 2, (1, 2, 3, 3))),
        (72, (10, 10, 8, (2, 5, 6, 7))),
        (144, (20, 20, 32, (4, 10, 12, 14))),
    ],
)
def test_changed_pixels_ratio_and_top_left_exclusive_bbox(dpi, expected):
    with Document() as left, Document() as right:
        _page(left)
        page = _page(right)
        page.draw_rectangle(2, 3, 4, 2, fill_color="#000", stroke_color=None)
        (result,) = left.compare_visual(right, dpi=dpi, include_diff_images=True)
        width, height, changed, bbox = expected
        assert result.size == result.other_size == (width, height)
        assert result.changed_pixels == changed
        assert result.total_pixels == width * height
        assert result.difference_ratio == changed / (width * height)
        assert result.bbox == bbox and not result.matches
        assert result.diff_image.dpi == dpi
        assert result.diff_image.get_pixel(bbox[0], bbox[1]) == (255, 0, 0)
        assert result.diff_image.get_pixel(0, 0) == (255, 255, 255)


def test_diff_image_shows_unchanged_content_as_pale_gray_and_outlives_documents(
    tmp_path,
):
    with Document() as left, Document() as right:
        for document in (left, right):
            _page(document).draw_rectangle(
                0, 0, 2, 2, fill_color="#000", stroke_color=None
            )
        right.pages[0].draw_rectangle(4, 4, 2, 2, fill_color="#f00", stroke_color=None)
        (report,) = left.compare_visual(right, include_diff_images=True)
    assert report.diff_image.get_pixel(0, 9) == (192, 192, 192)
    assert report.diff_image.get_pixel(4, 4) == (255, 0, 0)
    for suffix in (".png", ".tif", ".jpg"):
        path = report.diff_image.save(tmp_path / ("diff" + suffix))
        assert path.stat().st_size > 0


@pytest.mark.parametrize(
    "left_color,right_color,tolerance,changed",
    [
        ("#000000", "#010000", 0, 100),
        ("#000000", "#010000", 1, 0),
        ("#787878", "#78797a", 1, 100),
        ("#787878", "#78797a", 2, 0),
        ("#000000", "#ffffff", 254, 100),
        ("#000000", "#ffffff", 255, 0),
    ],
)
def test_tolerance_uses_any_channel_and_strict_greater_than_boundary(
    left_color, right_color, tolerance, changed
):
    with Document() as left, Document() as right:
        _page(left, left_color)
        _page(right, right_color)
        (result,) = left.compare_visual(
            right, tolerance=tolerance, include_diff_images=True
        )
        assert result.changed_pixels == changed
        assert result.matches is (changed == 0)
        assert (result.bbox is None) is (changed == 0)
        assert (result.diff_image.get_pixel(0, 0) == (255, 0, 0)) is (changed != 0)


@pytest.mark.parametrize(
    "allowance,matches", [(0, False), (1 / 49 - 1e-8, False), (1 / 49, True), (1, True)]
)
def test_ratio_allowance_keeps_actual_metrics_and_includes_exact_boundary(
    allowance, matches
):
    with Document() as left, Document() as right:
        _page(left, size=(7, 7))
        _page(right, size=(7, 7)).draw_rectangle(
            1, 1, 1, 1, fill_color="#000", stroke_color=None
        )
        (result,) = left.compare_visual(right, max_diff_ratio=allowance)
        assert result.changed_pixels == 1 and result.difference_ratio == 1 / 49
        assert result.bbox == (1, 5, 2, 6) and result.matches is matches


@pytest.mark.parametrize(
    "size,other_size,changed,total",
    [
        ((10, 10), (12, 10), 20, 120),
        ((10, 20), (20, 10), 200, 300),
        ((10, 10), (10, 12), 20, 120),
    ],
)
def test_different_page_sizes_are_structural_differences_even_when_white(
    size, other_size, changed, total
):
    with Document() as left, Document() as right:
        _page(left, size=size)
        _page(right, size=other_size)
        (report,) = left.compare_visual(
            right, tolerance=255, max_diff_ratio=1, include_diff_images=True
        )
        assert not report.matches
        assert report.size == size and report.other_size == other_size
        assert report.changed_pixels == changed and report.total_pixels == total
        assert report.difference_ratio == changed / total
        assert (report.diff_image.width, report.diff_image.height) == (
            max(size[0], other_size[0]),
            max(size[1], other_size[1]),
        )
        assert report.diff_image.get_pixel(0, 0) == (255, 255, 255)
        if size == (10, 20):
            assert report.diff_image.get_pixel(19, 19) == (255, 255, 255)
            assert report.bbox == (0, 0, 20, 20)


@pytest.mark.parametrize("extra_on_left", [False, True])
def test_added_and_removed_blank_pages_never_match(extra_on_left):
    with Document() as left, Document() as right:
        _page(left)
        _page(right)
        _page(left if extra_on_left else right, size=(5, 7))
        results = left.compare_visual(
            right, tolerance=255, max_diff_ratio=1, include_diff_images=True
        )
        assert len(results) == 2 and results[0].matches
        report = results[1]
        assert not report.matches and report.changed_pixels == report.total_pixels == 35
        assert report.difference_ratio == 1 and report.bbox == (0, 0, 5, 7)
        assert report.page_index == (1 if extra_on_left else None)
        assert report.other_page_index == (None if extra_on_left else 1)
        assert report.diff_image.pixels == b"\xff\x00\x00" * 35


def test_independent_page_selections_preserve_order_repeats_and_missing_tail():
    with Document() as left, Document() as right:
        for color in ("#f00", "#00f"):
            _page(left, color)
        for color in ("#00f", "#0f0", "#f00"):
            _page(right, color)
        results = left.compare_visual(
            right, pages=(i for i in (1, 0, 1)), other_pages=(i for i in (0, 2))
        )
        assert [(r.page_index, r.other_page_index, r.matches) for r in results] == [
            (1, 0, True),
            (0, 2, True),
            (1, None, False),
        ]
        assert len(left.compare_visual(right, pages=[])) == 3
        assert left.compare_visual(right, pages=[], other_pages=[]) == []


def test_empty_documents_and_self_comparison():
    with Document() as left, Document() as right:
        assert left.compare_visual(right) == []
        _page(left)
        assert left.compare_visual(left)[0].matches
        assert left.compare_visual(right)[0].other_page_index is None


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_crop_rotation_origins_and_physical_sizes_follow_rendered_views(rotation):
    with Document() as left, Document() as right:
        a = _page(left, "#f00", size=(20, 10))
        a.crop_box = (0, 0, 10, 10)
        a.rotation = rotation
        right.pages.add(((-20, 5, -10, 15), b"1 0 0 rg -20 5 10 10 re f"))
        assert left.compare_visual(right)[0].matches
    with Document() as left, Document() as right:
        _page(left, size=(10.1, 10.1))
        _page(right, size=(10.9, 10.9))
        assert left.compare_visual(right)[0].matches
        assert not left.compare_visual(right, dpi=144)[0].matches


@pytest.mark.parametrize("hidden", [False, True])
def test_annotations_and_layer_visibility_are_observed_without_mutation(hidden):
    with Document() as left, Document() as right:
        _page(left)
        page = _page(right)
        page.annotations.add(
            "Square",
            (2, 2, 8, 8),
            "Review",
            properties={"IC": [1, 0, 0], "F": 2 if hidden else 0},
        )
        before = (_bytes(left), _bytes(right))
        assert left.compare_visual(right)[0].matches is hidden
        assert left.compare_visual(right, include_annotations=False)[0].matches
        assert (_bytes(left), _bytes(right)) == before
        layer = right.layers.add("Hidden", visible=False)
        with page.layer(layer):
            page.draw_rectangle(0, 0, 1, 1, fill_color="#000", stroke_color=None)
        assert left.compare_visual(right, include_annotations=False)[0].matches
        layer.visible = True
        assert not left.compare_visual(right, include_annotations=False)[0].matches


def test_quarter_turn_rotation_matches_equivalent_upright_content():
    with Document() as left, Document() as right:
        a = _page(left, size=(20, 10))
        a.draw_rectangle(0, 0, 10, 10, fill_color="#f00", stroke_color=None)
        a.rotation = 90
        b = _page(right, size=(10, 20))
        b.draw_rectangle(0, 10, 10, 10, fill_color="#f00", stroke_color=None)
        assert left.compare_visual(right)[0].matches
        a.rotation = 0
        (result,) = left.compare_visual(right)
        assert not result.matches and result.size == (20, 10)


def test_visible_text_changes_and_form_widget_appearances_are_compared():
    with Document() as left, Document() as right:
        for document in (left, right):
            _page(document, size=(120, 60)).add_text("Invoice", 5, 40, font_size=12)
        assert left.compare_visual(right)[0].matches
        right.pages[0].add_text("Paid", 5, 5, font_size=12)
        (result,) = left.compare_visual(right)
        assert not result.matches and result.changed_pixels > 0
    with Document() as left, Document() as right:
        _page(left, size=(120, 60))
        _page(right, size=(120, 60))
        right.form.add_text_field("Review", 0, (5, 10, 100, 35), value="Approved")
        assert not left.compare_visual(right)[0].matches
        assert left.compare_visual(right, include_annotations=False)[0].matches


def test_each_documents_font_substitution_setting_is_used(monkeypatch):
    from aspose_pdf import FontSubstitutionOptions
    from aspose_pdf.engine import rasterizer

    with Document() as left, Document() as right:
        for document in (left, right):
            _page(document)
            document.font_substitution = FontSubstitutionOptions()
        settings = []
        original = rasterizer.resolver_for

        def resolver(options):
            settings.append(options)
            return original(options)

        monkeypatch.setattr(rasterizer, "resolver_for", resolver)
        assert left.compare_visual(right)[0].matches
        assert settings == [left.font_substitution, right.font_substitution]


@pytest.mark.parametrize(
    "name,value",
    [
        ("dpi", 0),
        ("dpi", -1),
        ("dpi", True),
        ("dpi", "72"),
        ("dpi", float("nan")),
        ("dpi", float("inf")),
        ("dpi", 10**1000),
        ("dpi", 5e-324),
        ("tolerance", -1),
        ("tolerance", 256),
        ("tolerance", True),
        ("tolerance", 1.5),
        ("tolerance", "1"),
        ("max_diff_ratio", -0.1),
        ("max_diff_ratio", 1.1),
        ("max_diff_ratio", True),
        ("max_diff_ratio", "0"),
        ("max_diff_ratio", float("inf")),
        ("max_diff_ratio", float("nan")),
        ("include_annotations", 0),
        ("include_diff_images", 1),
        ("pages", "0"),
        ("pages", 0),
        ("pages", [0, -1]),
        ("pages", [0, 1]),
        ("pages", [0, True]),
        ("pages", [0, 0.5]),
        ("other_pages", [0, 1]),
        ("other_pages", [False]),
        ("other_pages", b"0"),
    ],
)
def test_invalid_arguments_are_rejected_before_any_render(monkeypatch, name, value):
    with Document() as left, Document() as right:
        _page(left)
        _page(right)
        monkeypatch.setattr(
            visual_comparison,
            "_render",
            lambda *a, **kw: pytest.fail("must validate first"),
        )
        with pytest.raises(PdfValidationException):
            left.compare_visual(right, **{name: value})


@pytest.mark.parametrize(
    "name,value",
    [
        ("dpi", 0),
        ("tolerance", True),
        ("max_diff_ratio", 2),
        ("include_annotations", 1),
        ("include_diff_images", None),
    ],
)
def test_empty_comparisons_still_validate_options(name, value):
    with Document() as left, Document() as right:
        with pytest.raises(PdfValidationException):
            left.compare_visual(right, **{name: value})


@pytest.mark.parametrize("other", [None, "other.pdf", b"%PDF-1.7", object()])
def test_other_requires_an_open_document(other):
    with Document() as document:
        with pytest.raises(PdfValidationException, match="other must be a Document"):
            document.compare_visual(other)


@pytest.mark.parametrize("dispose_left", [False, True])
def test_disposed_document_is_rejected(dispose_left):
    with Document() as left, Document() as right:
        (left if dispose_left else right).dispose()
        with pytest.raises(AsposePdfException, match="disposed"):
            left.compare_visual(right)


@pytest.mark.parametrize("strict_on_left", [False, True])
@pytest.mark.parametrize("selected", [False, True])
def test_selection_limit_uses_stricter_document_for_all_pages_or_repeats(
    strict_on_left, selected
):
    strict = PdfLoadLimits(max_container_items=1)
    with (
        Document(limits=strict if strict_on_left else None) as left,
        Document(limits=None if strict_on_left else strict) as right,
    ):
        for document in (left, right):
            _page(document)
            _page(document)
        options = dict(pages=[0, 0], other_pages=[0, 0]) if selected else {}
        with pytest.raises(PdfResourceLimitException, match="max_container_items"):
            left.compare_visual(right, **options)


@pytest.mark.parametrize("strict_on_left", [False, True])
def test_enclosing_canvas_is_limited_even_when_each_thin_page_fits(
    monkeypatch, strict_on_left
):
    strict = PdfLoadLimits(max_raster_pixels=99)
    with (
        Document(limits=strict if strict_on_left else None) as left,
        Document(limits=None if strict_on_left else strict) as right,
    ):
        _page(left, size=(10, 1))
        _page(right, size=(1, 10))
        monkeypatch.setattr(
            visual_comparison,
            "_render",
            lambda *a, **kw: pytest.fail("must preflight first"),
        )
        with pytest.raises(PdfResourceLimitException, match="visual comparison canvas"):
            left.compare_visual(right)


def test_retained_images_have_a_cumulative_limit_checked_before_rendering(monkeypatch):
    with (
        Document(limits=PdfLoadLimits(max_raster_pixels=100)) as left,
        Document() as right,
    ):
        for document in (left, right):
            _page(document)
            _page(document)
        assert len(left.compare_visual(right)) == 2
        monkeypatch.setattr(
            visual_comparison,
            "_render",
            lambda *a, **kw: pytest.fail("must preflight first"),
        )
        with pytest.raises(
            PdfResourceLimitException, match="retained visual comparison images"
        ):
            left.compare_visual(right, include_diff_images=True)


def test_retained_image_limit_exact_boundary_and_stricter_encoder_limits():
    with (
        Document(limits=PdfLoadLimits(max_raster_pixels=None)) as left,
        Document(
            limits=PdfLoadLimits(max_raster_pixels=200, max_codec_work_bytes=1000)
        ) as right,
    ):
        for document in (left, right):
            _page(document)
            _page(document)
        results = left.compare_visual(right, include_diff_images=True)
        assert len(results) == 2
        for result in results:
            assert result.diff_image._limits.max_raster_pixels == 200
            with pytest.raises(PdfResourceLimitException, match="max_codec_work_bytes"):
                result.diff_image.to_webp()


@pytest.mark.parametrize("kind", ["nonfinite", "overflow", "overbudget"])
def test_later_page_geometry_and_limits_are_checked_before_any_allocation(
    monkeypatch, kind
):
    with Document() as left, Document() as right:
        for document in (left, right):
            _page(document)
            _page(document)
        right._engine_pdf.pages[1] = {
            "nonfinite": (0, 0, float("nan"), 1),
            "overflow": (-1e308, 0, 1e308, 1),
            "overbudget": (0, 0, 10**12, 10**12),
        }[kind]
        monkeypatch.setattr(
            visual_comparison,
            "_render",
            lambda *a, **kw: pytest.fail("must preflight every page"),
        )

        class UnexpectedCanvas(bytearray):
            def __new__(cls, *args, **kwargs):
                pytest.fail("must preflight before allocating")

        monkeypatch.setattr(
            visual_comparison, "bytearray", UnexpectedCanvas, raising=False
        )
        expected = (
            PdfResourceLimitException
            if kind == "overbudget"
            else PdfValidationException
        )
        with pytest.raises(expected):
            left.compare_visual(right, include_diff_images=True)


@pytest.mark.parametrize("include_diff_images", [False, True])
def test_page_rasters_are_released_between_pairs(monkeypatch, include_diff_images):
    with Document() as left, Document() as right:
        for document in (left, right):
            for _ in range(3):
                _page(document)
        original = Page.render
        references = []

        def render(page, **options):
            if len(references) % 2 == 0:
                assert all(ref() is None for ref in references)
            assert options == dict(
                dpi=72.0,
                background=(255, 255, 255),
                antialias=False,
                draw_annotations=True,
            )
            raster = original(page, **options)
            references.append(weakref.ref(raster))
            return raster

        monkeypatch.setattr(Page, "render", render)
        results = left.compare_visual(right, include_diff_images=include_diff_images)
        assert len(results) == 3
        assert all(ref() is None for ref in references)


@pytest.mark.parametrize("source_kind", ["bytes", "file", "streaming"])
@pytest.mark.parametrize("encrypted", [False, True])
def test_scans_round_trip_and_only_selected_streams_are_read(
    tmp_path, monkeypatch, source_kind, encrypted
):
    from aspose_pdf import RasterizedPage

    paths = [tmp_path / "left.pdf", tmp_path / "right.pdf"]
    for index, path in enumerate(paths):
        with Document() as document:
            for color in ((255, 0, 0), (0, 0, 255), (255, 0, 0)):
                png = RasterizedPage(1, 1, bytes(color)).to_png()
                document.pages.add(size=(10, 10)).add_image(
                    png, 0, 0, width=10, height=10
                )
            document.info["Title"] = f"Original {index}"
            document.add_attachment("note.txt", b"Keep me")
            if encrypted:
                document.encrypt(user_password="secret", owner_password="owner")
            document.save(path)
    before = [path.read_bytes() for path in paths]
    opener = Document.open_streaming if source_kind == "streaming" else Document
    arguments = before if source_kind == "bytes" else paths
    with (
        opener(arguments[0], password="secret" if encrypted else None) as left,
        opener(arguments[1], password="secret" if encrypted else None) as right,
    ):
        visited = [set(), set()]
        protection = [document.is_encrypted for document in (left, right)]

        def restrict(document, document_index, allowed):
            original = document._engine_pdf.get_page_content

            def content(index):
                assert index in allowed
                visited[document_index].add(index)
                return original(index)

            monkeypatch.setattr(document._engine_pdf, "get_page_content", content)

        restrict(left, 0, {0, 1})
        restrict(right, 1, {1, 2})
        results = left.compare_visual(
            right, pages=[0, 1], other_pages=[2, 1], include_diff_images=True
        )
        assert all(result.matches for result in results)
        assert visited == [{0, 1}, {1, 2}]
        for index, document in enumerate((left, right)):
            assert document.is_encrypted is protection[index]
            assert document.info["Title"] == f"Original {index}"
            assert document.attachments == {"note.txt": b"Keep me"}
            if source_kind == "streaming":
                assert document._engine_pdf.page_contents == []
                assert document._engine_pdf._extracted_text is None
    assert [path.read_bytes() for path in paths] == before


def test_render_failure_propagates_without_modifying_sources(monkeypatch):
    with Document() as left, Document() as right:
        _page(left)
        _page(right)
        before = (_bytes(left), _bytes(right))

        def fail(*args, **kwargs):
            raise PdfValidationException("Cannot render")

        monkeypatch.setattr(visual_comparison, "_render", fail)
        with pytest.raises(PdfValidationException, match="Cannot render"):
            left.compare_visual(right, include_diff_images=True)
        assert (_bytes(left), _bytes(right)) == before


def test_pixel_comparison_agrees_with_an_independent_coordinate_map_oracle():
    random = Random(29)
    for _ in range(100):
        rasters, coordinate_maps = [], []
        for side in range(2):
            if side == 0 and random.randrange(4) == 0:
                rasters.append(None)
                coordinate_maps.append({})
                continue
            width, height = random.randrange(1, 9), random.randrange(1, 9)
            pixels = bytes(random.randrange(256) for _ in range(width * height * 3))
            raster = RasterizedPage(width, height, pixels)
            rasters.append(raster)
            coordinate_maps.append(
                {
                    (x, y): raster.get_pixel(x, y)
                    for y in range(height)
                    for x in range(width)
                }
            )
        left, right = coordinate_maps
        tolerance = random.choice((0, 1, 20, 254, 255))
        positions = left.keys() | right.keys()
        changed = {
            point
            for point in positions
            if point not in left
            or point not in right
            or max(abs(a - b) for a, b in zip(left[point], right[point])) > tolerance
        }
        report = visual_comparison._compare(
            *rasters,
            0 if rasters[0] else None,
            0,
            tolerance,
            0.5,
            True,
            72,
            PdfLoadLimits(),
        )
        assert report.changed_pixels == len(changed)
        assert report.total_pixels == len(positions)
        assert report.difference_ratio == len(changed) / len(positions)
        expected_bbox = (
            (
                min(x for x, y in changed),
                min(y for x, y in changed),
                max(x for x, y in changed) + 1,
                max(y for x, y in changed) + 1,
            )
            if changed
            else None
        )
        assert report.bbox == expected_bbox
        assert report.matches is (
            report.size == report.other_size and report.difference_ratio <= 0.5
        )
        image = report.diff_image
        red_pixels = {
            (x, y)
            for y in range(image.height)
            for x in range(image.width)
            if image.get_pixel(x, y) == (255, 0, 0)
        }
        assert red_pixels == changed

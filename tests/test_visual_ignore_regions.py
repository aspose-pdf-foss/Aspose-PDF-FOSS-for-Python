"""Visual exclusions remove dynamic page areas from every comparison metric."""

from __future__ import annotations

import io
import math
import weakref
from dataclasses import FrozenInstanceError
from itertools import repeat
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
from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.pages import Page

_BLUE = (192, 224, 255)


def _page(document, *, size=(10, 10), black=False):
    page = document.pages.add(size=size)
    if black:
        page.draw_rectangle(0, 0, *size, fill_color="#000", stroke_color=None)
    return page


def _bytes(document):
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


@pytest.mark.parametrize("include_images", [False, True])
def test_ignored_header_changes_leave_only_body_metrics_and_bounds(include_images):
    with Document() as left, Document() as right:
        _page(left)
        page = _page(right)
        page.draw_rectangle(0, 8, 10, 2, fill_color="#000", stroke_color=None)
        page.draw_rectangle(3, 3, 2, 2, fill_color="#000", stroke_color=None)
        (result,) = left.compare_visual(
            right, ignore_regions=[(0, 0, 10, 2)], include_diff_images=include_images
        )
        assert result.ignored_pixels == 20
        assert result.total_pixels == 80 and result.changed_pixels == 4
        assert result.difference_ratio == 4 / 80 and not result.matches
        assert result.bbox == (3, 5, 5, 7)
        if include_images:
            assert result.diff_image.get_pixel(1, 1) == _BLUE
            assert result.diff_image.get_pixel(3, 5) == (255, 0, 0)
            assert result.diff_image.get_pixel(0, 4) == (255, 255, 255)
        else:
            assert result.diff_image is None


@pytest.mark.parametrize("dpi", [36, 72, 96, 144])
def test_regions_use_displayed_points_and_scale_with_dpi(dpi):
    with Document() as left, Document() as right:
        _page(left)
        page = _page(right)
        page.draw_rectangle(2, 4, 4, 4, fill_color="#000", stroke_color=None)
        (result,) = left.compare_visual(
            right, dpi=dpi, ignore_regions=[(2, 2, 6, 6)], include_diff_images=True
        )
        x0, y0 = math.floor(2 * dpi / 72), math.floor(2 * dpi / 72)
        x1, y1 = math.ceil(6 * dpi / 72), math.ceil(6 * dpi / 72)
        assert result.matches and result.changed_pixels == 0 and result.bbox is None
        assert result.ignored_pixels == (x1 - x0) * (y1 - y0)
        assert (
            result.total_pixels + result.ignored_pixels == math.ceil(10 * dpi / 72) ** 2
        )
        assert result.diff_image.get_pixel(x0, y0) == _BLUE


def test_fractional_bounds_round_outward_and_clip_negative_edges():
    with Document() as left, Document() as right:
        _page(left)
        _page(right, black=True)
        (result,) = left.compare_visual(
            right, ignore_regions=[(-5, -5, 2.1, 1.2)], include_diff_images=True
        )
        assert result.ignored_pixels == 6
        assert result.changed_pixels == result.total_pixels == 94
        assert result.diff_image.get_pixel(2, 1) == _BLUE
        assert result.diff_image.get_pixel(3, 1) == (255, 0, 0)


@pytest.mark.parametrize("include_images", [False, True])
@pytest.mark.parametrize("identical", [False, True])
def test_overlapping_adjacent_nested_and_duplicate_regions_count_once(
    include_images, identical
):
    with Document() as left, Document() as right:
        _page(left)
        _page(right, black=not identical)
        regions = [(1, 1, 5, 5), (3, 3, 7, 7), (1, 1, 5, 5), (2, 2, 3, 3), (7, 3, 8, 7)]
        (result,) = left.compare_visual(
            right, ignore_regions=regions, include_diff_images=include_images
        )
        assert result.ignored_pixels == 16 + 16 - 4 + 4
        assert result.total_pixels == 68
        assert result.changed_pixels == (0 if identical else 68)
        assert result.matches is identical


@pytest.mark.parametrize("include_images", [False, True])
@pytest.mark.parametrize("identical", [False, True])
def test_covering_every_pixel_has_zero_denominator_and_can_match(
    include_images, identical
):
    with Document() as left, Document() as right:
        _page(left)
        _page(right, black=not identical)
        (result,) = left.compare_visual(
            right,
            ignore_regions=[(-1e308, -1e308, 1e308, 1e308)],
            dpi=144,
            include_diff_images=include_images,
        )
        assert result.matches and result.total_pixels == result.changed_pixels == 0
        assert result.difference_ratio == 0 and result.bbox is None
        assert result.ignored_pixels == 400
        if include_images:
            assert set(
                result.diff_image.pixels[index : index + 3]
                for index in range(0, len(result.diff_image.pixels), 3)
            ) == {bytes(_BLUE)}


@pytest.mark.parametrize("missing", [False, True])
def test_exclusions_cannot_hide_missing_pages_or_unequal_dimensions(missing):
    with Document() as left, Document() as right:
        _page(left, size=(10, 1))
        if not missing:
            _page(right, size=(1, 10))
        (result,) = left.compare_visual(
            right,
            ignore_regions=[(0, 0, 10, 10)],
            max_diff_ratio=1,
            include_diff_images=True,
        )
        assert not result.matches and result.total_pixels == result.changed_pixels == 0
        assert result.difference_ratio == 0 and result.bbox is None
        assert result.ignored_pixels == (10 if missing else 19)
        assert result.diff_image.get_pixel(0, 0) == _BLUE
        if not missing:
            assert result.diff_image.get_pixel(9, 9) == (255, 255, 255)


def test_mask_can_remove_single_page_coverage_but_not_change_geometry_status():
    with Document() as left, Document() as right:
        _page(left, size=(10, 5))
        _page(right, size=(5, 5))
        (result,) = left.compare_visual(right, ignore_regions=[(5, 0, 10, 5)])
        assert result.ignored_pixels == 25 and result.total_pixels == 25
        assert result.changed_pixels == 0 and result.bbox is None
        assert not result.matches


def test_difference_allowance_uses_only_nonignored_area():
    with Document() as left, Document() as right:
        _page(left)
        page = _page(right)
        page.draw_rectangle(9, 0, 1, 1, fill_color="#000", stroke_color=None)
        regions = [(0, 0, 9, 10)]
        (result,) = left.compare_visual(
            right, ignore_regions=regions, max_diff_ratio=0.1
        )
        assert result.matches and result.difference_ratio == 0.1
        assert result.changed_pixels == 1 and result.total_pixels == 10
        assert not left.compare_visual(
            right, ignore_regions=regions, max_diff_ratio=0.1 - 1e-8
        )[0].matches


def test_color_tolerance_and_ignored_pixels_remain_independent():
    with Document() as left, Document() as right:
        _page(left)
        page = _page(right)
        page.draw_rectangle(0, 0, 10, 10, fill_color="#fefefe", stroke_color=None)
        (result,) = left.compare_visual(
            right, tolerance=1, ignore_regions=[(0, 0, 5, 10)]
        )
        assert (
            result.matches
            and result.changed_pixels == 0
            and result.ignored_pixels == 50
        )
        assert (
            left.compare_visual(right, tolerance=0, ignore_regions=[(0, 0, 5, 10)])[
                0
            ].changed_pixels
            == 50
        )


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_regions_are_relative_to_the_rotated_visible_crop_not_pdf_origin(rotation):
    with Document() as left, Document() as right:
        for document in (left, right):
            page = _page(document, size=(40, 30))
            page.crop_box = (10, 5, 30, 25)
            page.rotation = rotation
        right.pages[0].draw_rectangle(
            12, 17, 4, 4, fill_color="#000", stroke_color=None
        )
        bounds = {
            0: (2, 4, 6, 8),
            90: (12, 2, 16, 6),
            180: (14, 12, 18, 16),
            270: (4, 14, 8, 18),
        }[rotation]
        (result,) = left.compare_visual(right, ignore_regions=[bounds])
        assert result.matches and result.ignored_pixels == 16
        assert result.changed_pixels == 0


def test_none_empty_and_outside_regions_preserve_existing_behavior():
    with Document() as left, Document() as right:
        _page(left)
        _page(right, black=True)
        (baseline,) = left.compare_visual(right, include_diff_images=True)
        for regions in (None, [], (), [(20, 20, 30, 30)], [(-10, -10, -1, -1)]):
            (result,) = left.compare_visual(
                right, ignore_regions=regions, include_diff_images=True
            )
            assert result == baseline and result.ignored_pixels == 0
            assert result.diff_image.pixels == baseline.diff_image.pixels
        assert (
            PageVisualComparison(0, 0, (1, 1), (1, 1), 0, 1, None, True).ignored_pixels
            == 0
        )


def test_region_generator_is_consumed_once_and_snapshotted_for_every_selected_pair(
    monkeypatch,
):
    with Document() as left, Document() as right:
        for document in (left, right):
            _page(document)
            _page(document)
        bounds = [0, 0, 2, 2]
        visited = []

        def regions():
            visited.append("consumed")
            yield bounds

        original = Page.render

        def render(page, **options):
            assert visited == ["consumed"]
            bounds[:] = [0, 0, 10, 10]
            return original(page, **options)

        monkeypatch.setattr(Page, "render", render)
        results = left.compare_visual(
            right, pages=[1, 0, 1], other_pages=[0, 1], ignore_regions=regions()
        )
        assert [result.ignored_pixels for result in results] == [4, 4, 4]
        assert [result.matches for result in results] == [True, True, False]


def test_empty_documents_still_snapshot_valid_regions():
    with Document() as left, Document() as right:
        visited = []

        def regions():
            visited.append(True)
            yield (0, 0, 1, 1)

        assert left.compare_visual(right, ignore_regions=regions()) == []
        assert visited == [True]


@pytest.mark.parametrize(
    "regions",
    [
        1,
        True,
        "0,0,1,1",
        b"bad",
        {"box": (0, 0, 1, 1)},
        [None],
        [1],
        ["1234"],
        [b"1234"],
        [(0, 0, 1)],
        [(0, 0, 1, 1, 2)],
        [(0, 0, 0, 1)],
        [(0, 0, 1, 0)],
        [(2, 0, 1, 1)],
        [(0, 2, 1, 1)],
        [(False, 0, 1, 1)],
        [(0, None, 1, 1)],
        [(0, "0", 1, 1)],
        [(0, 0, float("nan"), 1)],
        [(0, 0, float("inf"), 1)],
        [(0, 0, 10**1000, 1)],
        [(0, 0, 1, 1), (0, 0, -1, 1)],
    ],
)
@pytest.mark.parametrize("empty", [False, True])
def test_all_regions_are_validated_before_geometry_or_rendering(
    monkeypatch, regions, empty
):
    with Document() as left, Document() as right:
        if not empty:
            _page(left)
            _page(right)

        def forbidden(*a, **kw):
            pytest.fail("validate every rectangle first")

        monkeypatch.setattr(visual_comparison, "_size", forbidden)
        monkeypatch.setattr(visual_comparison, "_render", forbidden)
        with pytest.raises(PdfValidationException, match="ignore_regions"):
            left.compare_visual(right, ignore_regions=regions)


@pytest.mark.parametrize("strict_on_left", [False, True])
def test_region_count_uses_stricter_limit_and_stops_unbounded_generators(
    monkeypatch, strict_on_left
):
    strict = PdfLoadLimits(max_container_items=2)
    with (
        Document(limits=strict if strict_on_left else None) as left,
        Document(limits=None if strict_on_left else strict) as right,
    ):
        monkeypatch.setattr(
            visual_comparison, "_render", lambda *a, **kw: pytest.fail("limit first")
        )
        with pytest.raises(PdfResourceLimitException, match="max_container_items"):
            left.compare_visual(right, ignore_regions=repeat((0, 0, 1, 1)))


def test_ignoring_all_pixels_does_not_bypass_canvas_or_retained_image_limits(
    monkeypatch,
):
    with (
        Document(limits=PdfLoadLimits(max_raster_pixels=100)) as left,
        Document() as right,
    ):
        for document in (left, right):
            _page(document)
            _page(document)
        monkeypatch.setattr(
            visual_comparison,
            "_render",
            lambda *a, **kw: pytest.fail("preflight first"),
        )
        with pytest.raises(
            PdfResourceLimitException, match="retained visual comparison images"
        ):
            left.compare_visual(
                right, ignore_regions=[(0, 0, 10, 10)], include_diff_images=True
            )
    with (
        Document(limits=PdfLoadLimits(max_raster_pixels=99)) as left,
        Document() as right,
    ):
        _page(left)
        _page(right)
        with pytest.raises(PdfResourceLimitException, match="visual comparison canvas"):
            left.compare_visual(right, ignore_regions=[(0, 0, 10, 10)])


@pytest.mark.parametrize("kind", ["bytes", "file", "streaming"])
@pytest.mark.parametrize("encrypted", [False, True])
def test_round_trips_preserve_sources_and_outputs_outlive_them(
    tmp_path, kind, encrypted
):
    paths = [tmp_path / "before.pdf", tmp_path / "after.pdf"]
    for index, path in enumerate(paths):
        with Document() as document:
            _page(document, black=bool(index))
            document.info["Title"] = f"Source {index}"
            document.add_attachment("note.txt", b"Keep")
            if encrypted:
                document.encrypt("secret", "owner")
            document.save(path)
    original = [path.read_bytes() for path in paths]
    opener = Document.open_streaming if kind == "streaming" else Document
    arguments = original if kind == "bytes" else paths
    with (
        opener(arguments[0], password="secret" if encrypted else None) as left,
        opener(arguments[1], password="secret" if encrypted else None) as right,
    ):
        protection = [document.is_encrypted for document in (left, right)]
        before = (
            [_bytes(document) for document in (left, right)]
            if kind != "streaming" and not encrypted
            else None
        )
        (result,) = left.compare_visual(
            right, ignore_regions=[(0, 0, 5, 10)], include_diff_images=True
        )
        assert (
            result.ignored_pixels == result.changed_pixels == result.total_pixels == 50
        )
        for index, document in enumerate((left, right)):
            assert document.is_encrypted is protection[index]
            assert document.info["Title"] == f"Source {index}"
            assert document.attachments == {"note.txt": b"Keep"}
            if kind == "streaming":
                assert document._engine_pdf.page_contents == []
                assert document._engine_pdf._extracted_text is None
        if before:
            assert [_bytes(document) for document in (left, right)] == before
    assert [path.read_bytes() for path in paths] == original
    assert result.diff_image.get_pixel(0, 0) == _BLUE
    result.diff_image.save(tmp_path / "diff.png")
    assert (tmp_path / "diff.png").read_bytes().startswith(b"\x89PNG")
    with pytest.raises(FrozenInstanceError):
        result.ignored_pixels = 0


def test_masks_do_not_retain_page_rasters_between_pairs(monkeypatch):
    with Document() as left, Document() as right:
        for document in (left, right):
            for _ in range(3):
                _page(document)
        original = Page.render
        references = []

        def render(page, **options):
            if len(references) % 2 == 0:
                assert all(reference() is None for reference in references)
            raster = original(page, **options)
            references.append(weakref.ref(raster))
            return raster

        monkeypatch.setattr(Page, "render", render)
        assert (
            len(
                left.compare_visual(
                    right, ignore_regions=[(0, 0, 2, 2)], include_diff_images=True
                )
            )
            == 3
        )
        assert all(reference() is None for reference in references)


def test_full_exclusions_do_not_bypass_render_failures_or_modify_sources(monkeypatch):
    with Document() as left, Document() as right:
        for document in (left, right):
            _page(document)
            _page(document)
        before = [_bytes(document) for document in (left, right)]
        original = Page.render

        def render(page, **options):
            if page.index == 1:
                raise PdfValidationException("Unreadable ignored page")
            return original(page, **options)

        monkeypatch.setattr(Page, "render", render)
        with pytest.raises(PdfValidationException, match="Unreadable"):
            left.compare_visual(
                right, ignore_regions=[(0, 0, 10, 10)], include_diff_images=True
            )
        assert [_bytes(document) for document in (left, right)] == before


def test_full_exclusions_keep_streaming_content_limits(tmp_path):
    path = tmp_path / "source.pdf"
    with Document() as document:
        page = _page(document)
        page.add_text("Content", 0, 5, font_size=3)
        document.save(path)
    with (
        Document.open_streaming(
            path, limits=PdfLoadLimits(max_content_stream_bytes=4)
        ) as left,
        Document(path) as right,
    ):
        with pytest.raises(PdfResourceLimitException, match="max_content_stream_bytes"):
            left.compare_visual(right, ignore_regions=[(0, 0, 10, 10)])


def test_masked_pixel_comparison_agrees_with_independent_coordinate_sets():
    random = Random(33)
    for _ in range(150):
        rasters, maps = [], []
        for side in range(2):
            if random.randrange(6) == 0 and (side == 0 or rasters[0] is not None):
                rasters.append(None)
                maps.append({})
                continue
            width, height = random.randrange(1, 9), random.randrange(1, 9)
            raster = RasterizedPage(
                width,
                height,
                bytes(random.randrange(256) for _ in range(width * height * 3)),
            )
            rasters.append(raster)
            maps.append(
                {
                    (x, y): raster.get_pixel(x, y)
                    for y in range(height)
                    for x in range(width)
                }
            )
        width = max(r.width if r else 0 for r in rasters)
        height = max(r.height if r else 0 for r in rasters)
        rectangles = []
        for _ in range(random.randrange(1, 6)):
            x0, y0 = random.randrange(width), random.randrange(height)
            rectangles.append(
                (
                    x0,
                    y0,
                    random.randrange(x0 + 1, width + 1),
                    random.randrange(y0 + 1, height + 1),
                )
            )
        positions = maps[0].keys() | maps[1].keys()
        ignored = {
            p
            for p in positions
            if any(a <= p[0] < c and b <= p[1] < d for a, b, c, d in rectangles)
        }
        compared = positions - ignored
        tolerance = random.choice((0, 20, 254, 255))
        changed = {
            p
            for p in compared
            if p not in maps[0]
            or p not in maps[1]
            or max(abs(a - b) for a, b in zip(maps[0][p], maps[1][p])) > tolerance
        }
        result = visual_comparison._compare(
            *rasters,
            0 if rasters[0] else None,
            0 if rasters[1] else None,
            tolerance,
            0.5,
            True,
            72,
            PdfLoadLimits(),
            ignore_regions=tuple(rectangles),
        )
        assert result.ignored_pixels == len(ignored)
        assert result.total_pixels == len(compared) and result.changed_pixels == len(
            changed
        )
        assert result.difference_ratio == (
            len(changed) / len(compared) if compared else 0
        )
        assert result.matches is (
            result.size is not None
            and result.size == result.other_size
            and result.difference_ratio <= 0.5
        )
        assert result.bbox == (
            (
                min(x for x, y in changed),
                min(y for x, y in changed),
                max(x for x, y in changed) + 1,
                max(y for x, y in changed) + 1,
            )
            if changed
            else None
        )
        image = result.diff_image
        assert {
            (x, y)
            for y in range(height)
            for x in range(width)
            if image.get_pixel(x, y) == _BLUE
        } == ignored
        assert {
            (x, y)
            for y in range(height)
            for x in range(width)
            if image.get_pixel(x, y) == (255, 0, 0)
        } == changed

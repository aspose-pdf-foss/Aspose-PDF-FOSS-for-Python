"""Imposition trim guides follow actual placed bounds and stay in safe whitespace."""

from __future__ import annotations

import io
import re

import pytest

from aspose_pdf import Document, PdfFileEditor, PdfLoadLimits, PdfResourceLimitException
from aspose_pdf.exceptions import PdfValidationException


def _saved(document):
    with io.BytesIO() as output:
        document.save(output)
        return output.getvalue()


def _segments(page):
    blocks = re.findall(
        rb"/Artifact[^\n]*B[MD]C\s*(.*?)\s*EMC", page.content, flags=re.S
    )
    return [
        tuple(float(value) for value in found)
        for block in blocks
        for found in re.findall(rb"([-\d.]+) ([-\d.]+) m ([-\d.]+) ([-\d.]+) l", block)
    ]


@pytest.fixture
def source():
    with Document() as document:
        for number in range(1, 7):
            document.pages.add(size=(100, 100)).add_text(f"Page {number}", 20, 50)
        yield document


def test_exact_trim_coordinates_and_black_vector_state():
    with Document() as source:
        source.pages.add(size=(100, 100))
        with source.n_up(
            1, 1, page_size=(300, 300), margin=20, crop_marks=True
        ) as imposed:
            assert set(_segments(imposed.pages[1])) == {
                (8, 20, 17, 20),
                (8, 280, 17, 280),
                (283, 20, 292, 20),
                (283, 280, 292, 280),
                (20, 8, 20, 17),
                (280, 8, 280, 17),
                (20, 283, 20, 292),
                (280, 283, 280, 292),
            }
            content = imposed.pages[1].content
            assert b"0 0 0 1 K" in content and b"0.5 w" in content
            assert b"0 J" in content and b"[] 0 d" in content
            assert content.count(b"/Artifact << /Type /Layout >> BDC") == 1


def test_rendered_marks_leave_a_white_gap_and_do_not_cover_the_page():
    with Document() as source:
        page = source.pages.add(size=(100, 100))
        page.draw_rectangle(0, 0, 100, 100, fill_color=(1, 0, 0), stroke_color=None)
        with source.n_up(
            1, 1, page_size=(300, 300), margin=20, crop_marks=True, crop_mark_width=2
        ) as imposed:
            with Document(_saved(imposed)) as loaded:
                raster = loaded.pages[1].render(dpi=72)
                assert raster.get_pixel(12, 280) == (0, 0, 0)
                assert raster.get_pixel(18, 280) == (255, 255, 255)
                assert raster.get_pixel(150, 150) == (255, 0, 0)
                assert raster.get_pixel(20, 288) == (0, 0, 0)


def test_letterboxed_pages_use_their_actual_trim_bounds():
    with Document() as source:
        source.pages.add(size=(100, 50))
        with source.n_up(
            1, 1, page_size=(300, 300), margin=20, crop_marks=True
        ) as imposed:
            segments = _segments(imposed.pages[1])
            assert (8, 85, 17, 85) in segments and (8, 215, 17, 215) in segments
            assert (20, 73, 20, 82) in segments and (280, 218, 280, 227) in segments


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_crop_box_origin_and_rotation_are_reflected_in_mark_positions(rotation):
    with Document() as source:
        page = source.pages.add(size=(400, 300))
        page.crop_box = (50, 60, 250, 160)
        page.rotation = rotation
        with source.n_up(
            1, 1, page_size=(300, 300), margin=20, crop_marks=True
        ) as imposed:
            segments = _segments(imposed.pages[1])
            if rotation in (90, 270):
                assert (73, 20, 82, 20) in segments
                assert (85, 283, 85, 292) in segments
            else:
                assert (8, 85, 17, 85) in segments
                assert (280, 218, 280, 227) in segments
        assert page.rotation == rotation and page.crop_box == (50, 60, 250, 160)


@pytest.mark.parametrize("order", ["row", "column"])
def test_all_occupied_grid_slots_are_marked_without_changing_page_order(source, order):
    options = dict(page_size=(400, 400), margin=20, gutter=30, order=order)
    before = _saved(source)
    with (
        source.n_up(2, 2, **options) as plain,
        source.n_up(2, 2, crop_marks=True, **options) as marked,
    ):
        assert [len(_segments(page)) for page in marked.pages] == [32, 16]
        for original, page in zip(plain.pages, marked.pages, strict=True):
            assert page.extract_text() == original.extract_text()
            original_calls = re.findall(rb"q [-\d. ]+ cm /\w+ Do Q", original.content)
            assert (
                re.findall(rb"q [-\d. ]+ cm /\w+ Do Q", page.content) == original_calls
            )
            for segment in _segments(page):
                assert all(0 <= value <= 400 for value in segment)
    assert _saved(source) == before


@pytest.mark.parametrize(
    "count,expected", [(1, [8, 0]), (4, [16, 16]), (6, [8, 8, 16, 16])]
)
def test_booklet_padding_stays_unmarked_and_folding_order_stays_the_same(
    count, expected
):
    with Document() as source:
        for number in range(1, count + 1):
            source.pages.add(size=(100, 100)).add_text(f"Page {number}", 20, 50)
        options = dict(page_size=(300, 200), margin=20, gutter=30)
        with (
            source.booklet(**options) as plain,
            source.booklet(crop_marks=True, **options) as marked,
        ):
            assert [len(_segments(page)) for page in marked.pages] == expected
            assert [page.extract_text() for page in marked.pages] == [
                page.extract_text() for page in plain.pages
            ]
            with Document(_saved(marked)) as loaded:
                assert [len(_segments(page)) for page in loaded.pages] == expected


def test_cell_whitespace_can_accommodate_marks_without_a_gutter():
    with Document() as source:
        for _ in range(2):
            source.pages.add(size=(50, 100))
        with source.n_up(
            1, 2, page_size=(500, 300), margin=20, gutter=0, crop_marks=True
        ) as marked:
            assert len(_segments(marked.pages[1])) == 16


def test_custom_dimensions_are_sheet_points_and_zero_offset_is_allowed():
    with Document() as source:
        source.pages.add(size=(100, 100))
        with source.n_up(
            1,
            1,
            page_size=(300, 300),
            margin=20,
            crop_marks=True,
            crop_mark_length=5,
            crop_mark_offset=0,
            crop_mark_width=1.25,
        ) as marked:
            assert (15, 20, 20, 20) in _segments(marked.pages[1])
            assert b"1.25 w" in marked.pages[1].content
            assert all(
                abs(c - a) + abs(d - b) == 5
                for a, b, c, d in _segments(marked.pages[1])
            )


@pytest.mark.parametrize("method", ["n_up", "booklet"])
def test_disabled_marks_keep_the_existing_output(source, method):
    options = dict(page_size=(400, 400), margin=20, gutter=30)
    with (
        getattr(source, method)(**options) as default,
        getattr(source, method)(crop_marks=False, **options) as explicit,
    ):
        assert [page.content for page in default.pages] == [
            page.content for page in explicit.pages
        ]
        assert all(_segments(page) == [] for page in default.pages)


@pytest.mark.parametrize(
    "options",
    [
        {"crop_marks": 1},
        {"crop_marks": None},
        {"crop_marks": "yes"},
        {"crop_mark_length": 0},
        {"crop_mark_length": -1},
        {"crop_mark_length": float("inf")},
        {"crop_mark_length": 1e-9},
        {"crop_mark_offset": -1},
        {"crop_mark_offset": float("nan")},
        {"crop_mark_offset": True},
        {"crop_mark_width": 0},
        {"crop_mark_width": "thin"},
        {"crop_mark_width": float("nan")},
        {"crop_mark_width": True},
    ],
)
@pytest.mark.parametrize("method", ["n_up", "booklet"])
def test_invalid_mark_options_fail_before_content_reads(
    source, monkeypatch, method, options
):
    before = _saved(source)
    with monkeypatch.context() as patch:
        patch.setattr(
            source._engine_pdf,
            "get_page_content",
            lambda *a: pytest.fail("Unexpected page content read"),
        )
        with pytest.raises(PdfValidationException, match="crop_mark"):
            getattr(source, method)(**options)
    assert _saved(source) == before


@pytest.mark.parametrize(
    "method,options",
    [
        ("n_up", {"margin": 5, "gutter": 30}),
        ("booklet", {"margin": 5, "gutter": 30}),
        ("n_up", {"margin": float("nan"), "gutter": 30}),
        ("booklet", {"margin": 20, "gutter": float("inf")}),
    ],
)
def test_insufficient_or_nonfinite_layout_space_fails_before_importing_pages(
    source, monkeypatch, method, options
):
    with monkeypatch.context() as patch:
        patch.setattr(
            source._engine_pdf,
            "get_page_content",
            lambda *a: pytest.fail("Unexpected page content read"),
        )
        with pytest.raises(PdfValidationException):
            getattr(source, method)(page_size=(400, 400), crop_marks=True, **options)


def test_stroke_width_is_included_in_the_space_check(monkeypatch):
    with Document() as source:
        source.pages.add(size=(100, 100))
        with source.n_up(
            1, 1, page_size=(300, 300), margin=12, crop_marks=True
        ) as marked:
            assert len(_segments(marked.pages[1])) == 8
        monkeypatch.setattr(
            source._engine_pdf,
            "get_page_content",
            lambda *a: pytest.fail("Unexpected content read"),
        )
        with pytest.raises(PdfValidationException, match="do not fit"):
            source.n_up(
                1,
                1,
                page_size=(300, 300),
                margin=12,
                crop_marks=True,
                crop_mark_width=30,
            )


@pytest.mark.parametrize(
    "limits,method",
    [
        (PdfLoadLimits(max_container_items=32), "n_up"),
        (PdfLoadLimits(max_content_tokens=63), "n_up"),
        (PdfLoadLimits(max_content_stream_bytes=32), "booklet"),
    ],
)
def test_mark_resource_limits_fail_before_content_reads(
    source, monkeypatch, limits, method
):
    source._engine_pdf._load_budget.limits = limits
    monkeypatch.setattr(
        source._engine_pdf,
        "get_page_content",
        lambda *a: pytest.fail("Unexpected page content read"),
    )
    with pytest.raises(PdfResourceLimitException, match="crop mark"):
        getattr(source, method)(
            page_size=(400, 400), margin=20, gutter=30, crop_marks=True
        )


def test_booklet_padding_obeys_output_page_limits():
    with Document(limits=PdfLoadLimits(max_pages=1)) as source:
        source.pages.add(size=(100, 100))
        with pytest.raises(PdfResourceLimitException, match="max_pages"):
            source.booklet(page_size=(300, 200), margin=20, gutter=30, crop_marks=True)


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("encrypted", [False, True])
def test_round_trip_independence_and_source_state(
    source, tmp_path, streaming, encrypted
):
    if encrypted:
        source.encrypt("user", "owner", algorithm="AES-256")
    path = tmp_path / "source.pdf"
    source.save(path)
    original = path.read_bytes()
    with (Document.open_streaming if streaming else Document)(
        path, password="owner" if encrypted else None
    ) as loaded:
        protected = loaded.is_encrypted
        engine = loaded._engine_pdf
        engine._page_text_cursor = 2
        marked = loaded.n_up(
            2, 2, page_size=(400, 400), margin=20, gutter=30, crop_marks=True
        )
        assert loaded.is_encrypted is protected and engine._page_text_cursor == 2
        assert engine._extracted_text is None
        if streaming:
            assert engine._lazy and engine.page_contents == []
    try:
        output = tmp_path / "marked.pdf"
        marked.save(output)
        with Document(output) as reopened:
            assert [len(_segments(page)) for page in reopened.pages] == [32, 16]
            assert all(
                f"Page {number}" in reopened.extract_text() for number in range(1, 7)
            )
    finally:
        marked.dispose()
    assert path.read_bytes() == original


@pytest.mark.parametrize("method", ["make_n_up", "make_booklet"])
def test_file_facades_forward_options_and_preserve_outputs_on_failure(
    source, tmp_path, method
):
    path, output = tmp_path / "source.pdf", tmp_path / "marked.pdf"
    source.save(path)
    editor = PdfFileEditor()
    impose = getattr(editor, method)
    assert impose(
        str(path),
        str(output),
        page_size=(400, 400),
        margin=20,
        gutter=30,
        crop_marks=True,
        crop_mark_length=5,
        crop_mark_offset=2,
        crop_mark_width=1,
    )
    with Document(output) as reopened:
        assert _segments(reopened.pages[1]) and b"1 w" in reopened.pages[1].content
    before = output.read_bytes()
    assert (
        impose(str(path), str(output), page_size=(400, 400), crop_marks=True) is False
    )
    assert isinstance(editor.last_exception, PdfValidationException)
    assert output.read_bytes() == before

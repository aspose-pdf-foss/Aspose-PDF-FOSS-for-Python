"""Resizing a page so its drawing goes with it.

Setting ``Page.size`` has always changed the **sheet** and left the drawing
where it was, so a smaller sheet crops it and a larger one pads it -- which is
right for trimming a page and wrong for every other sense of "resize".
``Page.resize`` scales the drawing onto the new sheet instead, and
``Page.scale_content`` scales it inside the sheet it is already on.

The page's operators are never rewritten: the whole content stream goes inside
one ``q`` ... ``Q`` with the matrix at the top, which is exact and cheap -- and
the interesting case is a stream that restores a graphics state it never saved,
because a ``q`` in front of it changes what that restore does.
"""

from __future__ import annotations

import io
import re

import pytest

from aspose_pdf import Document, PageSize
from aspose_pdf.engine.content_isolation import wrap_in_force
from aspose_pdf.engine.cos import PdfArray, PdfName, PdfNumber, PdfString
from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.load_limits import _coerce_limits, _LoadBudget

A4 = (595.2755905511812, 841.8897637795276)
LETTER = (612.0, 792.0)


def _budget() -> _LoadBudget:
    return _LoadBudget(_coerce_limits(None))


def _page(size: PageSize = PageSize.A4):
    document = Document()
    page = document.pages.add(size)
    page.add_text("corner", 20, 20)
    return document, page


def _matrix(page) -> list[float] | None:
    """The ``cm`` a scale put at the top of the page's content."""
    match = re.search(rb"\Aq\n([-\d.e ]+) cm", page.content)
    return [float(value) for value in match.group(1).split()] if match else None


def _annots(document: Document) -> list:
    engine = document._engine_pdf
    page = engine._get_page_dict(0)
    array = engine._resolve(page.mapping.get(PdfName("Annots")))
    return [engine._resolve(item) for item in array.items] if array else []


def _numbers(owner, name: str) -> list[float]:
    array = owner.mapping[PdfName(name)]
    return [round(float(item.value), 4) for item in array.items]


# ---------------------------------------------------------------------------
# Putting a transform around a content stream
# ---------------------------------------------------------------------------
class TestWrapInForce:
    def test_a_balanced_stream(self):
        out = wrap_in_force(b"0 0 5 5 re f", b"2 0 0 2 0 0 cm", budget=_budget())
        assert out == b"q\n2 0 0 2 0 0 cm\n0 0 5 5 re f\nQ\n"

    def test_a_trailing_newline_is_not_doubled(self):
        out = wrap_in_force(b"x\n", b"M cm", budget=_budget())
        assert out == b"q\nM cm\nx\nQ\n"

    def test_empty_content(self):
        assert wrap_in_force(b"", b"M cm", budget=_budget()) == b"q\nM cm\nQ\n"

    def test_a_stray_q_gets_one_to_consume(self):
        """A viewer ignores it today; with a ``q`` in front it would not."""
        out = wrap_in_force(b"Q 0 0 1 1 re f", b"M cm", budget=_budget())
        assert out == b"q\nM cm\nq\nQ 0 0 1 1 re f\nQ\nQ\n"
        assert out.count(b"q\n") == out.count(b"Q\n")

    def test_two_strays_get_two(self):
        out = wrap_in_force(b"Q Q x", b"M cm", budget=_budget())
        assert out.startswith(b"q\nM cm\nq\nq\n")
        assert out.endswith(b"Q\nQ\nQ\n")

    def test_a_level_left_open_is_closed(self):
        out = wrap_in_force(b"q 1 0 0 1 2 2 cm x", b"M cm", budget=_budget())
        assert out == b"q\nM cm\nq 1 0 0 1 2 2 cm x\nQ\nQ\n"

    def test_a_text_object_left_open_is_ended_first(self):
        out = wrap_in_force(b"BT /F1 12 Tf (x) Tj", b"M cm", budget=_budget())
        assert out.endswith(b"ET\nQ\n")

    def test_a_restore_of_what_was_never_saved_is_refused(self):
        """The one case a wrap would change how the page itself draws."""
        assert (
            wrap_in_force(b"1 0 0 1 9 9 cm Q 0 0 1 1 re f", b"M cm", budget=_budget())
            is None
        )

    def test_a_string_that_looks_like_an_operator_is_not_one(self):
        out = wrap_in_force(b"BT (Q q) Tj ET", b"M cm", budget=_budget())
        assert out == b"q\nM cm\nBT (Q q) Tj ET\nQ\n"


# ---------------------------------------------------------------------------
# Scaling the content
# ---------------------------------------------------------------------------
class TestScaleContent:
    def test_the_matrix_goes_at_the_top(self):
        document, page = _page()
        page.scale_content(0.5, about="origin")
        assert _matrix(page) == [0.5, 0.0, 0.0, 0.5, 0.0, 0.0]
        assert page.content.rstrip().endswith(b"Q")
        document.close()

    def test_two_factors_scale_the_axes_apart(self):
        document, page = _page()
        page.scale_content(0.5, 2.0, about="origin")
        assert _matrix(page) == [0.5, 0.0, 0.0, 2.0, 0.0, 0.0]
        document.close()

    def test_centred_by_default(self):
        document, page = _page()
        page.scale_content(0.95)
        matrix = _matrix(page)
        assert matrix[0] == matrix[3] == 0.95
        assert matrix[4] == pytest.approx(A4[0] / 2 * 0.05)
        assert matrix[5] == pytest.approx(A4[1] / 2 * 0.05)
        document.close()

    def test_the_sheet_is_not_touched(self):
        document, page = _page()
        before = page.rect
        page.scale_content(0.5)
        assert page.rect == before
        document.close()

    def test_the_text_still_says_the_same_thing(self):
        document, page = _page()
        page.scale_content(0.5)
        assert page.extract_text() == "corner"
        document.close()

    def test_the_identity_rewrites_nothing(self):
        document, page = _page()
        before = page.content
        page.scale_content(1.0, about="origin")
        assert page.content == before
        document.close()

    @pytest.mark.parametrize("bad", [0, -1, float("inf"), float("nan"), True, "big", None])
    def test_a_bad_factor_is_refused(self, bad):
        document, page = _page()
        with pytest.raises(PdfValidationException):
            page.scale_content(bad)
        document.close()

    def test_a_bad_anchor_is_refused(self):
        document, page = _page()
        with pytest.raises(PdfValidationException, match="'center' or 'origin'"):
            page.scale_content(0.5, about="corner")
        document.close()

    def test_centre_may_be_spelled_either_way(self):
        for spelling in ("center", "centre"):
            document, page = _page()
            page.scale_content(0.5, about=spelling)
            assert _matrix(page)[4] != 0.0
            document.close()

    def test_a_page_that_cannot_be_wrapped_says_which_one(self):
        document, page = _page()
        engine = document._engine_pdf
        engine._set_page_content(0, b"1 0 0 1 9 9 cm Q 0 0 1 1 re f\n")
        with pytest.raises(PdfValidationException, match="page 1 cannot be scaled"):
            page.scale_content(0.5)
        document.close()


# ---------------------------------------------------------------------------
# Annotations travel with the drawing
# ---------------------------------------------------------------------------
class TestAnnotations:
    def _annotated(self, **entries):
        document, page = _page()
        engine = document._engine_pdf
        annotation = {
            PdfName("Type"): PdfName("Annot"),
            PdfName("Subtype"): PdfName("Square"),
            PdfName("Rect"): PdfArray([PdfNumber(v) for v in (10, 20, 110, 220)]),
        }
        for name, values in entries.items():
            annotation[PdfName(name)] = values
        reference = engine._cos_doc.register_object(
            __import__("aspose_pdf.engine.cos", fromlist=["PdfDictionary"]).PdfDictionary(
                annotation
            )
        )
        engine._get_page_dict(0).mapping[PdfName("Annots")] = PdfArray([reference])
        return document, page

    def test_the_rectangle_moves(self):
        document, page = self._annotated()
        page.scale_content(0.5, about="origin")
        assert _numbers(_annots(document)[0], "Rect") == [5, 10, 55, 110]
        document.close()

    def test_a_shift_moves_it_too(self):
        document, page = self._annotated()
        page.scale_content(1.0, about="origin")  # identity: nothing happens
        page._document._engine_pdf.scale_page_content(0, 2.0, dx=7.0, dy=9.0)
        assert _numbers(_annots(document)[0], "Rect") == [27, 49, 227, 449]
        document.close()

    @pytest.mark.parametrize(
        ("name", "given", "expected"),
        [
            ("QuadPoints", (0, 0, 10, 0, 0, 10, 10, 10), [0, 0, 5, 0, 0, 5, 5, 5]),
            ("Vertices", (2, 4, 6, 8), [1, 2, 3, 4]),
            ("L", (0, 0, 100, 50), [0, 0, 50, 25]),
            ("CL", (1, 2, 3, 4, 5, 6), [0.5, 1, 1.5, 2, 2.5, 3]),
        ],
    )
    def test_every_run_of_points_moves(self, name, given, expected):
        document, page = self._annotated(
            **{name: PdfArray([PdfNumber(v) for v in given])}
        )
        page.scale_content(0.5, about="origin")
        assert _numbers(_annots(document)[0], name) == expected
        document.close()

    def test_an_ink_stroke_is_a_list_of_runs(self):
        document, page = self._annotated(
            InkList=PdfArray(
                [
                    PdfArray([PdfNumber(v) for v in (0, 0, 10, 10)]),
                    PdfArray([PdfNumber(v) for v in (20, 20, 30, 30)]),
                ]
            )
        )
        page.scale_content(0.5, about="origin")
        engine = document._engine_pdf
        ink = engine._resolve(_annots(document)[0].mapping[PdfName("InkList")])
        assert [
            [round(float(n.value), 4) for n in engine._resolve(run).items]
            for run in ink.items
        ] == [[0, 0, 5, 5], [10, 10, 15, 15]]
        document.close()

    def test_a_difference_rectangle_is_distances_and_is_never_shifted(self):
        """``/RD`` insets a rectangle (Table 164); it names no point in one."""
        document, _page_object = self._annotated(
            RD=PdfArray([PdfNumber(v) for v in (2, 4, 6, 8)])
        )
        engine = document._engine_pdf
        engine.scale_page_content(0, 0.5, dx=100.0, dy=100.0)
        assert _numbers(_annots(document)[0], "RD") == [1, 2, 3, 4]
        document.close()

    def test_geometry_that_is_not_numbers_is_left_alone(self):
        document, page = self._annotated(
            Vertices=PdfArray([PdfNumber(1), PdfString(b"two")])
        )
        page.scale_content(0.5, about="origin")
        engine = document._engine_pdf
        kept = engine._resolve(_annots(document)[0].mapping[PdfName("Vertices")])
        assert isinstance(kept.items[1], PdfString)
        document.close()

    def test_an_odd_number_of_coordinates_is_left_alone(self):
        document, page = self._annotated(
            Vertices=PdfArray([PdfNumber(1), PdfNumber(2), PdfNumber(3)])
        )
        page.scale_content(0.5, about="origin")
        assert _numbers(_annots(document)[0], "Vertices") == [1, 2, 3]
        document.close()

    def test_a_link_moves_with_the_text_it_covers(self):
        document, page = _page()
        page.add_link((20, 790, 120, 830), "https://example.com")
        page.resize(PageSize.LETTER)
        scale = LETTER[1] / A4[1]
        shift = (LETTER[0] - A4[0] * scale) / 2
        assert _numbers(_annots(document)[0], "Rect") == [
            pytest.approx(20 * scale + shift, abs=1e-3),
            pytest.approx(790 * scale, abs=1e-3),
            pytest.approx(120 * scale + shift, abs=1e-3),
            pytest.approx(830 * scale, abs=1e-3),
        ]
        document.close()


# ---------------------------------------------------------------------------
# Resizing the sheet
# ---------------------------------------------------------------------------
class TestResize:
    def test_the_sheet_becomes_the_size_asked_for(self):
        document, page = _page()
        page.resize(PageSize.LETTER)
        assert (page.size.width, page.size.height) == LETTER
        document.close()

    def test_fit_keeps_the_shape_and_centres_what_is_left(self):
        document, page = _page()
        page.resize(PageSize.LETTER, mode="fit")
        scale = min(LETTER[0] / A4[0], LETTER[1] / A4[1])
        matrix = _matrix(page)
        assert matrix[0] == matrix[3] == pytest.approx(scale)
        assert matrix[4] == pytest.approx((LETTER[0] - A4[0] * scale) / 2)
        assert matrix[5] == pytest.approx(0.0, abs=1e-9)
        document.close()

    def test_fill_covers_the_sheet(self):
        document, page = _page()
        page.resize(PageSize.LETTER, mode="fill")
        scale = max(LETTER[0] / A4[0], LETTER[1] / A4[1])
        matrix = _matrix(page)
        assert matrix[0] == matrix[3] == pytest.approx(scale)
        assert matrix[5] < 0  # the taller drawing is centred, so it hangs over
        document.close()

    def test_stretch_scales_the_axes_apart(self):
        document, page = _page()
        page.resize(PageSize.LETTER, mode="stretch")
        matrix = _matrix(page)
        assert matrix[0] == pytest.approx(LETTER[0] / A4[0])
        assert matrix[3] == pytest.approx(LETTER[1] / A4[1])
        assert matrix[4] == pytest.approx(0.0, abs=1e-9)
        document.close()

    def test_the_same_size_is_a_no_op(self):
        document, page = _page()
        before = page.content
        page.resize(PageSize.A4)
        assert page.content == before
        document.close()

    def test_a_bad_mode_is_refused(self):
        document, page = _page()
        with pytest.raises(PdfValidationException, match="mode is one of"):
            page.resize(PageSize.LETTER, mode="squash")
        document.close()

    def test_the_sheet_keeps_its_own_origin(self):
        document, page = _page()
        page.media_box = (10, 20, 10 + A4[0], 20 + A4[1])
        page.resize(PageSize.LETTER)
        assert page.rect == pytest.approx((10, 20, 10 + LETTER[0], 20 + LETTER[1]))
        document.close()

    def test_the_other_boxes_travel_with_the_drawing(self):
        document, page = _page()
        page.crop_box = (0, 0, 300, 400)
        page.trim_box = (10, 10, 290, 390)
        page.resize(PageSize.LETTER, mode="stretch")
        sx, sy = LETTER[0] / A4[0], LETTER[1] / A4[1]
        assert page.crop_box == pytest.approx((0, 0, 300 * sx, 400 * sy))
        assert page.trim_box == pytest.approx(
            (10 * sx, 10 * sy, 290 * sx, 390 * sy)
        )
        document.close()

    def test_rotation_is_left_alone(self):
        document, page = _page()
        page.rotation = 90
        page.resize(PageSize.LETTER)
        assert page.rotation == 90
        document.close()

    def test_a_sheet_with_no_area_is_measured_as_letter(self):
        """Which is what ``rect`` and the renderer already answer for one."""
        document, _page_object = _page()
        engine = document._engine_pdf
        engine.pages[0] = (0.0, 0.0, 0.0, 0.0)
        engine.resize_page(0, LETTER[0] / 2, LETTER[1] / 2)
        matrix = _matrix(document.pages[0])
        assert matrix[0] == pytest.approx(0.5)
        assert matrix[3] == pytest.approx(0.5)
        document.close()

    def test_a_size_may_be_named_or_a_pair(self):
        for size in (PageSize.LETTER, "letter", (612, 792)):
            document, page = _page()
            page.resize(size)
            assert (page.size.width, page.size.height) == LETTER
            document.close()


# ---------------------------------------------------------------------------
# Over a document, and through the facade
# ---------------------------------------------------------------------------
class TestDocumentAndFacade:
    def _mixed(self) -> Document:
        document = Document()
        for size in (PageSize.A4, PageSize.A5, PageSize.LEDGER):
            page = document.pages.add(size)
            page.add_text("x", 10, 10)
        return document

    def test_every_page_by_default(self):
        with self._mixed() as document:
            assert document.resize_pages(PageSize.LETTER) == 3
            assert all(
                (page.size.width, page.size.height) == LETTER
                for page in document.pages
            )

    def test_a_selection(self):
        with self._mixed() as document:
            assert document.resize_pages(PageSize.LETTER, pages=[1]) == 1
            assert (document.pages[0].size.width, document.pages[0].size.height) != LETTER
            assert (document.pages[1].size.width, document.pages[1].size.height) == LETTER

    def test_a_slice(self):
        with self._mixed() as document:
            assert document.resize_pages(PageSize.LETTER, pages=slice(1, None)) == 2
            assert (document.pages[0].size.width, document.pages[0].size.height) != LETTER

    def test_the_mode_is_passed_on(self):
        with self._mixed() as document:
            document.resize_pages(PageSize.LETTER, mode="stretch")
            matrix = _matrix(document.pages[1])
            assert matrix[0] != matrix[3]

    def test_the_facade_still_changes_the_box_alone(self):
        """Which is what it has always done, so nothing that called it moved."""
        from aspose_pdf import PdfPageEditor

        with self._mixed() as document:
            editor = PdfPageEditor()
            editor.bind_pdf(document)
            assert editor.resize(PageSize.LETTER, [1])
            assert _matrix(document.pages[0]) is None
            assert (document.pages[0].size.width, document.pages[0].size.height) == LETTER

    def test_the_facade_can_scale_the_content(self):
        from aspose_pdf import PdfPageEditor

        with self._mixed() as document:
            editor = PdfPageEditor()
            editor.bind_pdf(document)
            assert editor.resize(PageSize.LETTER, [1], scale_content=True)
            assert _matrix(document.pages[0]) is not None

    def test_the_facade_passes_the_mode_on(self):
        from aspose_pdf import PdfPageEditor

        with self._mixed() as document:
            editor = PdfPageEditor()
            editor.bind_pdf(document)
            assert editor.resize(
                PageSize.LETTER, [1], scale_content=True, mode="stretch"
            )
            matrix = _matrix(document.pages[0])
            assert matrix[0] != matrix[3]


# ---------------------------------------------------------------------------
# Through a save
# ---------------------------------------------------------------------------
def test_a_resized_page_survives_a_save(tmp_path):
    target = tmp_path / "resized.pdf"
    document, page = _page()
    page.add_text("upper left", 20, 800, font_size=18)
    page.resize(PageSize.LETTER)
    document.save(target)
    document.close()

    with Document(target) as reopened:
        assert (reopened.pages[0].size.width, reopened.pages[0].size.height) == LETTER
        text = reopened.pages[0].extract_text()
    assert "upper left" in text and "corner" in text


def test_the_drawing_lands_where_the_matrix_says(tmp_path):
    """Measured through the renderer, which is a second opinion on the content."""
    document, page = _page()
    page.draw_rectangle(0, 0, A4[0], A4[1], stroke_color=(0, 0, 0), line_width=8)
    page.resize(PageSize.LETTER)
    raster = page.render(dpi=72)
    document.close()
    assert raster.width == 612
    assert raster.height == 792
    # The frame was the whole A4 sheet; fitted onto Letter it is inset
    # horizontally by the centring shift and touches top and bottom.
    scale = LETTER[1] / A4[1]
    shift = (LETTER[0] - A4[0] * scale) / 2
    assert shift == pytest.approx(26.0, abs=0.5)


def test_scaling_twice_compounds():
    document, page = _page()
    page.scale_content(0.5, about="origin")
    page.scale_content(0.5, about="origin")
    content = page.content
    assert content.count(b"0.5 0 0 0.5 0 0 cm") == 2
    assert content.startswith(b"q\n0.5 0 0 0.5 0 0 cm\nq\n")
    document.close()


def test_a_scaled_page_still_extracts_and_renders():
    document, page = _page()
    page.add_text("readable", 50, 400, font_size=30)
    page.scale_content(0.4)
    buffer = io.BytesIO()
    document.save(buffer)
    document.close()
    with Document(buffer.getvalue()) as reopened:
        assert "readable" in reopened.pages[0].extract_text()
        raster = reopened.render_page(0, dpi=36)
        assert raster.width > 0

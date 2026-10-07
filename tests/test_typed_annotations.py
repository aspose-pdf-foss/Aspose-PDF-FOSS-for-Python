"""One annotation class per subtype, and the links the property channel cannot carry.

Reading and writing annotations worked: every entry travelled through
``Annotation.properties``, which is what preserves subtypes this API has no class
for. What it meant in practice is that a caller had to know the standard's
spellings -- ``/IC``, ``/QuadPoints``, ``/Vertices``, ``/InkList``, ``/L``,
``/LE``, ``/CA``, ``/BS`` -- and get the corner order of a quadrilateral right by
hand. The typed classes put names on those entries; the property channel is still
underneath, so nothing is hidden and nothing new can be lost.

The one real capability added here is **replies and popups**. Those are
references between annotations rather than values, which the property channel
drops on purpose (a reference handed out as a nested dictionary could be written
back as a second copy of an annotation that already exists), so there was no way
to thread a comment or give one a window.

Cross-checked with **qpdf 12.4.2**: a reply's ``/IRT`` is the same object as the
annotation it answers and carries ``/RT /Reply``; a popup's ``/Parent`` and its
annotation's ``/Popup`` are each other, as 12.5.6.14 requires; and the entries the
factories write are the ones the tables name.
"""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document, PageSize
from aspose_pdf.annotations import (
    CircleAnnotation,
    FreeTextAnnotation,
    HighlightAnnotation,
    InkAnnotation,
    LineAnnotation,
    MarkupAnnotation,
    PolygonAnnotation,
    PolyLineAnnotation,
    PopupAnnotation,
    RedactAnnotation,
    SquareAnnotation,
    SquigglyAnnotation,
    StampAnnotation,
    StrikeOutAnnotation,
    TextAnnotation,
    UnderlineAnnotation,
    quad_from_rect,
)
from aspose_pdf.exceptions import PdfValidationException


def _page(text: bool = True):
    document = Document()
    page = document.pages.add(PageSize.A5)
    if text:
        page.add_text("The quick brown fox jumps over the lazy dog", 50, 500)
    return document, page


def _roundtrip(document: Document) -> Document:
    buffer = io.BytesIO()
    document.save(buffer)
    buffer.seek(0)
    return Document(buffer)


def _saved(document: Document) -> bytes:
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Which class reads which subtype
# ---------------------------------------------------------------------------


def test_each_subtype_is_read_as_its_own_class():
    document, page = _page()
    annotations = page.annotations
    annotations.add_text((40, 560, 60, 580), "note")
    annotations.add_free_text((200, 400, 380, 440), "typed")
    annotations.add_square((50, 300, 200, 360))
    annotations.add_circle((220, 300, 340, 360))
    annotations.add_line((50, 260), (340, 280))
    annotations.add_polygon([(50, 150), (120, 220), (190, 150)])
    annotations.add_polyline([(210, 150), (260, 210), (310, 150)])
    annotations.add_ink([[(50, 80), (70, 110)]])
    annotations.add_highlight((48, 495, 300, 515))
    annotations.add_underline((48, 475, 300, 492))
    annotations.add_strike_out((48, 455, 300, 472))
    annotations.add_squiggly((48, 435, 300, 452))
    annotations.add_stamp((250, 560, 380, 590), "Approved")
    annotations.add_redact((48, 415, 200, 432))

    expected = [
        TextAnnotation,
        FreeTextAnnotation,
        SquareAnnotation,
        CircleAnnotation,
        LineAnnotation,
        PolygonAnnotation,
        PolyLineAnnotation,
        InkAnnotation,
        HighlightAnnotation,
        UnderlineAnnotation,
        StrikeOutAnnotation,
        SquigglyAnnotation,
        StampAnnotation,
        RedactAnnotation,
    ]
    assert [type(item) for item in page.annotations] == expected
    # And the classes come back after a save, from the file's own /Subtype.
    assert [type(item) for item in _roundtrip(document).pages[0].annotations] == expected


def test_a_subtype_with_no_class_of_its_own_is_still_read():
    _, page = _page()
    page.annotations.add("Caret", (10, 10, 30, 30), "a caret")
    page.annotations.add("Movie", (40, 10, 60, 30), "a movie")
    kinds = [type(item) for item in page.annotations]
    # Caret is a markup annotation; Movie is not one at all.
    assert kinds[0] is MarkupAnnotation
    assert kinds[1].__name__ == "Annotation"
    assert page.annotations[1].subtype == "Movie"


def test_the_typed_classes_are_slotted_like_the_base():
    for cls in (TextAnnotation, SquareAnnotation, HighlightAnnotation, PopupAnnotation):
        assert "__dict__" not in dir(cls), cls.__name__


# ---------------------------------------------------------------------------
# What every markup annotation carries
# ---------------------------------------------------------------------------


def test_opacity_subject_and_dates():
    document, page = _page()
    note = page.annotations.add_text((40, 560, 60, 580), "note", title="Sergey")
    assert note.opacity is None  # unsaid means fully opaque
    note.opacity = 0.5
    note.subject = "A question"
    note.creation_date = "D:20261007120000+02'00'"
    note.modified = "D:20261007130000+02'00'"
    reloaded = _roundtrip(document).pages[0].annotations[0]
    assert reloaded.opacity == 0.5
    assert reloaded.subject == "A question"
    assert reloaded.creation_date.startswith("D:20261007120000")
    assert reloaded.modified.startswith("D:20261007130000")
    assert b"/CA 0.5" in _saved(document)


def test_the_border_is_one_dictionary_edited_entry_by_entry():
    document, page = _page()
    square = page.annotations.add_square((50, 300, 200, 360))
    square.border_width = 3
    square.border_style = "D"
    square.border_dash = [4, 2]
    assert (square.border_width, square.border_style) == (3.0, "D")
    assert square.border_dash == (4.0, 2.0)
    # Setting one entry leaves the others alone, rather than replacing /BS.
    square.border_width = 1
    assert square.border_style == "D"
    assert square.border_dash == (4.0, 2.0)
    reloaded = _roundtrip(document).pages[0].annotations[0]
    assert (reloaded.border_width, reloaded.border_style) == (1.0, "D")
    assert reloaded.border_dash == (4.0, 2.0)


def test_removing_the_last_border_entry_removes_the_dictionary():
    document, page = _page()
    square = page.annotations.add_square((50, 300, 200, 360), border_width=2)
    square.border_style = None
    square.border_width = None
    assert square.border_width is None
    assert b"/BS" not in _saved(document)


def test_a_colour_entry_takes_grey_rgb_or_cmyk():
    _, page = _page()
    square = page.annotations.add_square((50, 300, 200, 360))
    square.color = "#cc3300"
    assert square.color == pytest.approx((0.8, 0.2, 0.0))
    square.interior_color = (0, 0.2, 1, 0.05)
    assert square.interior_color == (0.0, 0.2, 1.0, 0.05)
    square.interior_color = 0.5
    assert square.interior_color == (0.5,)
    square.interior_color = None
    assert square.interior_color == ()


# ---------------------------------------------------------------------------
# The geometry each subtype is defined by
# ---------------------------------------------------------------------------


def test_a_line_knows_where_it_runs():
    document, page = _page()
    line = page.annotations.add_line(
        (50, 260), (340, 280), line_endings=["OpenArrow", "ClosedArrow"]
    )
    assert line.start == (50.0, 260.0)
    assert line.end == (340.0, 280.0)
    assert line.line_endings == ("OpenArrow", "ClosedArrow")
    line.set_line((10, 20), (30, 40))
    assert (line.start, line.end) == ((10.0, 20.0), (30.0, 40.0))
    reloaded = _roundtrip(document).pages[0].annotations[0]
    assert reloaded.end == (30.0, 40.0)


def test_a_line_gets_a_rectangle_that_holds_it():
    # 12.5.2: the annotation rectangle has to contain what the annotation draws,
    # and a stroke needs room for its width plus what an arrow head reaches past.
    _, page = _page()
    line = page.annotations.add_line((50, 100), (150, 100), border_width=4)
    x0, y0, x1, y1 = line.rect
    assert x0 < 50 and x1 > 150
    assert y0 < 100 < y1


def test_vertices_are_points_in_and_points_out():
    document, page = _page()
    polygon = page.annotations.add_polygon([(50, 150), (120, 220), (190, 150)])
    assert polygon.vertices == [(50.0, 150.0), (120.0, 220.0), (190.0, 150.0)]
    polygon.vertices = [10, 20, 30, 40]  # a flat sequence is taken too
    assert polygon.vertices == [(10.0, 20.0), (30.0, 40.0)]
    assert _roundtrip(document).pages[0].annotations[0].vertices == [
        (10.0, 20.0),
        (30.0, 40.0),
    ]


def test_a_polyline_also_has_line_endings():
    _, page = _page()
    polyline = page.annotations.add_polyline(
        [(10, 10), (20, 20)], line_endings=["None", "OpenArrow"]
    )
    assert polyline.line_endings == ("None", "OpenArrow")
    assert polyline.vertices == [(10.0, 10.0), (20.0, 20.0)]


def test_ink_is_a_list_of_strokes():
    document, page = _page()
    ink = page.annotations.add_ink(
        [[(50, 80), (70, 110), (90, 80)], [(110, 80), (130, 110)]]
    )
    assert ink.ink_list == [
        [(50.0, 80.0), (70.0, 110.0), (90.0, 80.0)],
        [(110.0, 80.0), (130.0, 110.0)],
    ]
    reloaded = _roundtrip(document).pages[0].annotations[0]
    assert len(reloaded.ink_list) == 2  # two marks, not one with a jump in it


def test_quad_points_are_groups_of_four_corners():
    document, page = _page()
    highlight = page.annotations.add_highlight([quad_from_rect(48, 495, 300, 515)])
    quads = highlight.quad_points
    assert len(quads) == 1
    assert quads[0] == ((48.0, 515.0), (300.0, 515.0), (48.0, 495.0), (300.0, 495.0))
    assert _roundtrip(document).pages[0].annotations[0].quad_points == quads


def test_a_single_rectangle_is_taken_as_the_one_quad_it_describes():
    # Marking one box is the common case, and the corner order is the thing
    # everyone gets wrong (12.5.6.10), so it is done here rather than by hand.
    _, page = _page()
    direct = page.annotations.add_highlight((48, 495, 300, 515))
    by_hand = page.annotations.add_highlight([quad_from_rect(48, 495, 300, 515)])
    assert direct.quad_points == by_hand.quad_points


def test_quad_from_rect_puts_the_corners_in_the_order_viewers_expect():
    (ul, ur, ll, lr) = quad_from_rect(10, 20, 110, 70)
    assert ul == (10, 70)
    assert ur == (110, 70)
    assert ll == (10, 20)
    assert lr == (110, 20)
    # Either pair of opposite corners describes the same rectangle.
    assert quad_from_rect(110, 70, 10, 20) == quad_from_rect(10, 20, 110, 70)


def test_several_quads_mark_several_lines():
    _, page = _page()
    highlight = page.annotations.add_highlight(
        [quad_from_rect(48, 495, 300, 515), quad_from_rect(48, 475, 200, 492)]
    )
    assert len(highlight.quad_points) == 2


def test_a_coordinate_count_that_cannot_be_the_shape_is_refused():
    _, page = _page()
    square = page.annotations.add_square((0, 0, 10, 10))
    with pytest.raises(PdfValidationException, match="numbers per entry"):
        square.set_property("Vertices", None)  # clear, then write a bad one
        page.annotations.add_polygon([(1, 2), (3,)])
    with pytest.raises(PdfValidationException, match="numbers per entry"):
        page.annotations.add_highlight([1, 2, 3, 4, 5, 6])
    with pytest.raises(PdfValidationException, match="made of numbers"):
        page.annotations.add_polygon(["a", "b"])


def test_the_text_entries_of_a_free_text_annotation():
    document, page = _page()
    free_text = page.annotations.add_free_text(
        (200, 400, 380, 440),
        "Typed on the page",
        default_appearance="/Helv 11 Tf 0 g",
        alignment="center",
    )
    assert free_text.default_appearance == "/Helv 11 Tf 0 g"
    assert free_text.alignment == 1
    free_text.alignment = "right"
    assert free_text.alignment == 2
    free_text.rich_text = "<p>rich</p>"
    reloaded = _roundtrip(document).pages[0].annotations[0]
    assert reloaded.alignment == 2
    assert reloaded.rich_text == "<p>rich</p>"
    with pytest.raises(PdfValidationException, match="left"):
        free_text.alignment = "sideways"
    with pytest.raises(PdfValidationException, match=r"0 \(left\)"):
        free_text.alignment = 7


def test_the_sticky_note_entries():
    document, page = _page()
    note = page.annotations.add_text(
        (40, 560, 60, 580), "note", icon="Help", is_open=True
    )
    assert note.icon == "Help"
    assert note.is_open is True
    note.state_model = "Review"
    note.state = "Accepted"
    reloaded = _roundtrip(document).pages[0].annotations[0]
    assert (reloaded.state, reloaded.state_model) == ("Accepted", "Review")
    assert b"/Name /Help" in _saved(document)


def test_a_stamp_is_named():
    document, page = _page()
    stamp = page.annotations.add_stamp((250, 560, 380, 590), "Approved")
    assert stamp.icon == "Approved"
    stamp.icon = "Draft"
    assert _roundtrip(document).pages[0].annotations[0].icon == "Draft"
    assert b"/Name /Draft" in _saved(document)


def test_a_redaction_marks_and_says_what_to_write_over_it():
    document, page = _page()
    redact = page.annotations.add_redact(
        (48, 415, 200, 432), overlay_text="REDACTED", interior_color=(0, 0, 0)
    )
    assert redact.overlay_text == "REDACTED"
    assert redact.interior_color == (0.0, 0.0, 0.0)
    assert len(redact.quad_points) == 1
    assert _roundtrip(document).pages[0].annotations[0].overlay_text == "REDACTED"


# ---------------------------------------------------------------------------
# Replies
# ---------------------------------------------------------------------------


def test_a_reply_threads_onto_the_annotation_it_answers():
    _, page = _page()
    note = page.annotations.add_text((40, 560, 60, 580), "Look here", title="Sergey")
    first = note.reply("Agreed", title="Reviewer")
    second = note.reply("Still open", title="Sergey")

    assert [item.contents for item in note.replies] == ["Agreed", "Still open"]
    assert first.in_reply_to.contents == "Look here"
    assert second.in_reply_to.contents == "Look here"
    assert note.in_reply_to is None
    assert first.replies == []


def test_a_reply_says_it_is_a_reply():
    # 12.5.6.2: /RT /Reply is what distinguishes a reply from a group.
    document, page = _page()
    note = page.annotations.add_text((40, 560, 60, 580), "note")
    note.reply("Agreed")
    data = _saved(document)
    assert b"/RT /Reply" in data
    assert b"/IRT" in data


def test_a_reply_survives_the_round_trip_as_a_reply():
    document, page = _page()
    note = page.annotations.add_text((40, 560, 60, 580), "Look here")
    note.reply("Agreed")
    note.reply("And again")
    reloaded = _roundtrip(document).pages[0].annotations
    assert [item.contents for item in reloaded[0].replies] == ["Agreed", "And again"]
    assert reloaded[1].in_reply_to.contents == "Look here"


def test_a_reply_takes_its_own_place_and_subtype_when_asked():
    _, page = _page()
    note = page.annotations.add_text((40, 560, 60, 580), "note")
    reply = note.reply("Over here", rect=(100, 100, 120, 120), subtype="Text")
    assert reply.rect == (100.0, 100.0, 120.0, 120.0)
    # Without a rectangle a reply sits where the annotation it answers does.
    assert note.reply("Here").rect == note.rect


def test_replies_can_be_threaded_onto_a_reply():
    _, page = _page()
    note = page.annotations.add_text((40, 560, 60, 580), "note")
    reply = note.reply("first")
    nested = reply.reply("second")
    assert [item.contents for item in reply.replies] == ["second"]
    assert nested.in_reply_to.contents == "first"
    assert [item.contents for item in note.replies] == ["first"]


# ---------------------------------------------------------------------------
# Popups
# ---------------------------------------------------------------------------


def test_a_popup_is_linked_both_ways():
    document, page = _page()
    note = page.annotations.add_text((40, 560, 60, 580), "Look here")
    popup = note.add_popup(is_open=True)

    assert isinstance(popup, PopupAnnotation)
    assert popup.is_open is True
    assert popup.parent_annotation.contents == "Look here"
    assert note.popup.rect == popup.rect
    reloaded = _roundtrip(document).pages[0].annotations
    assert reloaded[0].popup is not None
    assert reloaded[1].parent_annotation.contents == "Look here"


def test_a_popup_takes_a_rectangle_or_gets_one_beside_the_annotation():
    _, page = _page()
    note = page.annotations.add_text((40, 560, 60, 580), "note")
    given = note.add_popup((200, 400, 380, 480))
    assert given.rect == (200.0, 400.0, 380.0, 480.0)

    other = page.annotations.add_text((40, 200, 60, 220), "another")
    automatic = other.add_popup()
    assert automatic.rect[0] >= other.rect[2]  # beside it, not on top of it


def test_a_popup_is_not_a_markup_annotation():
    # It has no author and no date of its own: it is the window of the one that
    # does (12.5.6.14).
    _, page = _page()
    popup = page.annotations.add_text((40, 560, 60, 580), "note").add_popup()
    assert not isinstance(popup, MarkupAnnotation)
    assert not hasattr(popup, "opacity")


def test_an_annotation_without_either_link_says_so():
    _, page = _page()
    square = page.annotations.add_square((50, 300, 200, 360))
    assert square.popup is None
    assert square.in_reply_to is None
    assert square.parent_annotation is None
    assert square.replies == []


def test_linking_to_an_annotation_that_is_not_there_is_refused():
    document, page = _page()
    page.annotations.add_square((0, 0, 10, 10))
    engine = document._engine_pdf
    with pytest.raises(PdfValidationException, match="out of range"):
        engine.add_annotation_reply(0, 5, {"Subtype": "Text", "Rect": (0, 0, 1, 1)})
    with pytest.raises(PdfValidationException, match="out of range"):
        engine.annotation_links(0, 5)


# ---------------------------------------------------------------------------
# The typed layer is the property channel, not a second one
# ---------------------------------------------------------------------------


def test_a_typed_property_and_the_raw_entry_are_the_same_thing():
    _, page = _page()
    square = page.annotations.add_square((50, 300, 200, 360))
    square.interior_color = (1, 1, 0.8)
    assert square.get_property("IC") == [1.0, 1.0, 0.8]
    square.set_property("IC", [0.0, 0.0, 0.0])
    assert square.interior_color == (0.0, 0.0, 0.0)


def test_an_entry_the_typed_layer_does_not_name_still_round_trips():
    document, page = _page()
    square = page.annotations.add_square((50, 300, 200, 360))
    square.set_property("BE", {"S": "C", "I": 2})  # a cloud border
    reloaded = _roundtrip(document).pages[0].annotations[0]
    assert reloaded.get_property("BE") == {"S": "C", "I": 2}


def test_the_generated_appearance_uses_the_typed_entries():
    document, page = _page()
    page.annotations.add_square(
        (50, 300, 200, 360), interior_color=(1, 0, 0), border_width=3
    )
    assert page.annotations.generate_appearances() == 1
    raster = _roundtrip(document).pages[0].render(dpi=72)
    # The inside of the square is the interior colour it was given.
    assert raster.get_pixel(125, 595 - 330)[0] > 200


def test_a_shape_rectangle_is_not_moved_by_the_typed_layer():
    _, page = _page()
    square = page.annotations.add_square((50, 300, 200, 360))
    square.interior_color = (1, 0, 0)
    square.opacity = 0.5
    assert square.rect == (50.0, 300.0, 200.0, 360.0)

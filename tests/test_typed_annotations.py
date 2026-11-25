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
    CaretAnnotation,
    CircleAnnotation,
    FreeTextAnnotation,
    HighlightAnnotation,
    InkAnnotation,
    LineAnnotation,
    MarkupAnnotation,
    Name,
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
    annotations.add_caret((50, 50, 80, 80), "insert here", symbol="Paragraph")

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
        CaretAnnotation,
    ]
    assert [type(item) for item in page.annotations] == expected
    # And the classes come back after a save, from the file's own /Subtype.
    assert [type(item) for item in _roundtrip(document).pages[1].annotations] == expected


def test_a_subtype_with_no_class_of_its_own_is_still_read():
    _, page = _page()
    page.annotations.add("Movie", (40, 10, 60, 30), "a movie")
    kinds = [type(item) for item in page.annotations]
    # Movie is not a markup annotation at all.
    assert kinds[0].__name__ == "Annotation"
    assert page.annotations[0].subtype == "Movie"


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
    reloaded = _roundtrip(document).pages[1].annotations[0]
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
    reloaded = _roundtrip(document).pages[1].annotations[0]
    assert (reloaded.border_width, reloaded.border_style) == (1.0, "D")
    assert reloaded.border_dash == (4.0, 2.0)


def test_removing_the_last_border_entry_removes_the_dictionary():
    document, page = _page()
    square = page.annotations.add_square((50, 300, 200, 360), border_width=2)
    square.border_style = None
    square.border_width = None
    assert square.border_width is None
    assert b"/BS" not in _saved(document)


def test_the_border_property_returns_a_3_tuple():
    _document, page = _page()
    square = page.annotations.add_square((50, 300, 200, 360))

    # Initially all entries are absent
    assert square.border == (None, None, ())

    # Set individual entries
    square.border_width = 2.5
    square.border_style = "D"
    square.border_dash = (4.0, 2.0)

    # The border property returns all three as a tuple
    assert square.border == (2.5, "D", (4.0, 2.0))


def test_the_border_property_sets_all_entries_at_once():
    _document, page = _page()
    square = page.annotations.add_square((50, 300, 200, 360))
    square.border_width = 2.5
    square.border_style = "D"
    square.border_dash = (4.0, 2.0)

    # Set all three at once with a new value
    square.border = (1.0, "S", ())

    assert square.border_width == 1.0
    assert square.border_style == "S"
    assert square.border_dash == ()


def test_the_border_property_allows_partial_update_with_none():
    _document, page = _page()
    square = page.annotations.add_square((50, 300, 200, 360))
    square.border = (2.5, "D", (4.0, 2.0))

    # Update only style and dash, leave width unchanged
    square.border = (None, "B", (1.0, 1.0))

    # Width stays at 2.5, style and dash change
    assert square.border == (2.5, "B", (1.0, 1.0))


def test_the_border_property_raises_valueerror_for_wrong_length():
    _document, page = _page()
    square = page.annotations.add_square((50, 300, 200, 360))

    with pytest.raises(ValueError) as exc_info:
        square.border = (1.0, "D")
    assert "3-tuple" in str(exc_info.value)

    with pytest.raises(ValueError) as exc_info:
        square.border = (1.0, "D", (1.0,), "extra")
    assert "3-tuple" in str(exc_info.value)


def test_the_border_property_works_independently_per_instance():
    _document, page = _page()
    sq1 = page.annotations.add_square((50, 300, 200, 360))
    sq2 = page.annotations.add_square((50, 200, 200, 260))

    sq1.border = (2.5, "D", (4.0, 2.0))
    sq2.border = (1.0, "S", ())

    assert sq1.border == (2.5, "D", (4.0, 2.0))
    assert sq2.border == (1.0, "S", ())

    # Update sq1 only
    sq1.border = (None, "B", (1.0, 1.0))

    assert sq1.border == (2.5, "B", (1.0, 1.0))
    assert sq2.border == (1.0, "S", ())


def test_roundtrip_preserves_border_property():
    document, page = _page()
    square = page.annotations.add_square((50, 300, 200, 360))
    square.border = (2.5, "D", (4.0, 2.0))

    reloaded = _roundtrip(document).pages[1].annotations[0]

    assert reloaded.border == (2.5, "D", (4.0, 2.0))


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
    reloaded = _roundtrip(document).pages[1].annotations[0]
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
    assert _roundtrip(document).pages[1].annotations[0].vertices == [
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


def test_polygon_perimeter():
    document, page = _page()
    # Triangle with vertices at (0,0), (3,0), (0,4) -> perimeter = 3 + 4 + 5 = 12
    polygon = page.annotations.add_polygon([(0.0, 0.0), (3.0, 0.0), (0.0, 4.0)])
    assert polygon.perimeter == 12.0

    # Single point -> perimeter = 0.0 (fewer than 2 vertices)
    single = page.annotations.add_polygon([(10.0, 10.0)])
    assert single.perimeter == 0.0

    # Two points -> perimeter = 2 * distance (closing the loop)
    two = page.annotations.add_polygon([(0.0, 0.0), (3.0, 0.0)])
    assert two.perimeter == 6.0

    # Round-trip preserves perimeter
    reloaded = _roundtrip(document).pages[1].annotations[0]
    assert reloaded.perimeter == 12.0


def test_ink_is_a_list_of_strokes():
    document, page = _page()
    ink = page.annotations.add_ink(
        [[(50, 80), (70, 110), (90, 80)], [(110, 80), (130, 110)]]
    )
    assert ink.ink_list == [
        [(50.0, 80.0), (70.0, 110.0), (90.0, 80.0)],
        [(110.0, 80.0), (130.0, 110.0)],
    ]
    reloaded = _roundtrip(document).pages[1].annotations[0]
    assert len(reloaded.ink_list) == 2  # two marks, not one with a jump in it


def test_quad_points_are_groups_of_four_corners():
    document, page = _page()
    highlight = page.annotations.add_highlight([quad_from_rect(48, 495, 300, 515)])
    quads = highlight.quad_points
    assert len(quads) == 1
    assert quads[0] == ((48.0, 515.0), (300.0, 515.0), (48.0, 495.0), (300.0, 495.0))
    assert _roundtrip(document).pages[1].annotations[0].quad_points == quads


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
    reloaded = _roundtrip(document).pages[1].annotations[0]
    assert reloaded.alignment == 2
    assert reloaded.rich_text == "<p>rich</p>"
    with pytest.raises(PdfValidationException, match="left"):
        free_text.alignment = "sideways"
    with pytest.raises(PdfValidationException, match=r"0 \(left\)"):
        free_text.alignment = 7
    # Float 1.0 is accepted (represents integer 1)
    free_text.alignment = 1.0
    assert free_text.alignment == 1
    # Float 1.5 is rejected (non-zero fractional part)
    with pytest.raises(PdfValidationException, match="integer"):
        free_text.alignment = 1.5


def test_the_sticky_note_entries():
    document, page = _page()
    note = page.annotations.add_text(
        (40, 560, 60, 580), "note", icon="Help", is_open=True
    )
    assert note.icon == "Help"
    assert note.is_open is True
    note.state_model = "Review"
    note.state = "Accepted"
    reloaded = _roundtrip(document).pages[1].annotations[0]
    assert (reloaded.state, reloaded.state_model) == ("Accepted", "Review")
    assert b"/Name /Help" in _saved(document)


def test_a_stamp_is_named():
    document, page = _page()
    stamp = page.annotations.add_stamp((250, 560, 380, 590), "Approved")
    assert stamp.icon == "Approved"
    stamp.icon = "Draft"
    assert _roundtrip(document).pages[1].annotations[0].icon == "Draft"
    assert b"/Name /Draft" in _saved(document)


def test_a_redaction_marks_and_says_what_to_write_over_it():
    document, page = _page()
    redact = page.annotations.add_redact(
        (48, 415, 200, 432), overlay_text="REDACTED", interior_color=(0, 0, 0)
    )
    assert redact.overlay_text == "REDACTED"
    assert redact.interior_color == (0.0, 0.0, 0.0)
    assert len(redact.quad_points) == 1
    assert _roundtrip(document).pages[1].annotations[0].overlay_text == "REDACTED"


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
    reloaded = _roundtrip(document).pages[1].annotations
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


def test_reply_performance_is_linear():
    """Test that adding n replies to the same annotation runs in O(n) time.

    The reply() operation should not scale quadratically with the number of
    annotations on the page. This test verifies that adding 256 replies completes
    quickly (under 200ms) without scanning all annotations for each reply.
    """
    import time

    doc = Document()
    page = doc.pages.add(PageSize.A4)

    # Pre-create 256 annotations (repliable) to simulate a realistic workload
    annos = []
    for i in range(256):
        annos.append(page.annotations.add_text((100 + i, 100, 100 + i + 10, 110), f"Annotation {i}"))

    # Reply to the first annotation 256 times
    start = time.perf_counter()
    for i in range(256):
        annos[0].reply(f"Reply {i}")
    elapsed = (time.perf_counter() - start) * 1000

    # Verify correctness
    assert len(annos[0].replies) == 256
    assert all(r.contents == f"Reply {i}" for i, r in enumerate(annos[0].replies))

    # Performance check: 256 replies should complete in under 200ms (linear scaling)
    # With the old O(n²) implementation, this would take several seconds
    assert elapsed < 200, f"reply() took {elapsed:.1f}ms for 256 replies, expected < 200ms"


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
    reloaded = _roundtrip(document).pages[1].annotations
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


def test_delete_reply_by_integer_index():
    _, page = _page()
    note = page.annotations.add_text((40, 560, 60, 580), "Main", title="Alice")
    note.reply("First reply", title="Bob")
    note.reply("Second reply", title="Carol")
    note.reply("Third reply", title="Dave")

    # Delete by positive index (0)
    note.delete_reply(0)
    assert [r.contents for r in note.replies] == ["Second reply", "Third reply"]

    # Delete by positive index (1)
    note.delete_reply(1)
    assert [r.contents for r in note.replies] == ["Second reply"]

    # Re-add for negative index test
    note.reply("Fourth reply", title="Eve")
    note.delete_reply(-1)  # Delete last
    assert [r.contents for r in note.replies] == ["Second reply"]


def test_delete_reply_by_annotation_object():
    _, page = _page()
    note = page.annotations.add_text((40, 560, 60, 580), "Main", title="Alice")
    reply1 = note.reply("First reply", title="Bob")
    reply2 = note.reply("Second reply", title="Carol")

    # Delete by annotation object
    note.delete_reply(reply1)
    assert [r.contents for r in note.replies] == ["Second reply"]

    # Delete remaining reply
    note.delete_reply(reply2)
    assert note.replies == []


def test_delete_reply_invalid_index_raises_indexerror():
    _, page = _page()
    note = page.annotations.add_text((40, 560, 60, 580), "Main", title="Alice")
    note.reply("Reply", title="Bob")

    with pytest.raises(IndexError, match="out of range"):
        note.delete_reply(5)

    with pytest.raises(IndexError, match="out of range"):
        note.delete_reply(-5)


def test_delete_reply_wrong_annotation_raises_valueerror():
    _, page = _page()
    note1 = page.annotations.add_text((40, 560, 60, 580), "Main1", title="Alice")
    note2 = page.annotations.add_text((40, 540, 60, 560), "Main2", title="Alice")
    reply = note1.reply("Reply", title="Bob")

    # Try to delete a reply that belongs to note1 from note2
    with pytest.raises(ValueError, match="Annotation is not a reply to this annotation"):
        note2.delete_reply(reply)


def test_delete_reply_preserves_popup():
    _, page = _page()
    note = page.annotations.add_text((40, 560, 60, 580), "Main", title="Alice")
    reply = note.reply("Reply", title="Bob")
    reply.add_popup()

    popup_ref = reply.popup
    assert popup_ref is not None

    note.delete_reply(0)

    # Popup should still exist (it's not deleted with the reply)
    assert popup_ref is not None
    assert popup_ref.contents == ""  # Popup still exists, just detached


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
    reloaded = _roundtrip(document).pages[1].annotations[0]
    assert reloaded.get_property("BE") == {"S": "C", "I": 2}


def test_the_generated_appearance_uses_the_typed_entries():
    document, page = _page()
    page.annotations.add_square(
        (50, 300, 200, 360), interior_color=(1, 0, 0), border_width=3
    )
    assert page.annotations.generate_appearances() == 1
    raster = _roundtrip(document).pages[1].render(dpi=72)
    # The inside of the square is the interior colour it was given.
    assert raster.get_pixel(125, 595 - 330)[0] > 200


def test_a_shape_rectangle_is_not_moved_by_the_typed_layer():
    _, page = _page()
    square = page.annotations.add_square((50, 300, 200, 360))
    square.interior_color = (1, 0, 0)
    square.opacity = 0.5
    assert square.rect == (50.0, 300.0, 200.0, 360.0)


# ---------------------------------------------------------------------------
# CaretAnnotation
# ---------------------------------------------------------------------------


def test_caret_annotation_is_read_as_its_own_class():
    _document, page = _page()
    caret = page.annotations.add_caret((50, 300, 90, 340), "insert text here", symbol="Paragraph")
    assert type(caret) is CaretAnnotation
    assert caret.symbol == "Paragraph"
    assert caret.subtype == "Caret"


def test_caret_annotation_symbol_property():
    document, page = _page()
    caret = page.annotations.add_caret((50, 300, 90, 340), "insert text here", symbol="Paragraph")
    assert caret.symbol == "Paragraph"
    caret.symbol = "None"
    assert caret.symbol == "None"
    # Round trip preserves symbol
    reloaded = _roundtrip(document).pages[1].annotations[-1]
    assert reloaded.symbol == "None"


def test_caret_annotation_with_none_symbol():
    _, page = _page()
    caret = page.annotations.add_caret((150, 300, 190, 340), "break para", symbol="None")
    assert caret.symbol == "None"
    assert type(caret) is CaretAnnotation


def test_caret_annotation_survives_round_trip():
    document, page = _page()
    page.annotations.add_caret((50, 100, 90, 140), "test caret", symbol="Paragraph")
    reloaded = _roundtrip(document).pages[1].annotations[-1]
    assert type(reloaded) is CaretAnnotation
    assert reloaded.symbol == "Paragraph"
    assert reloaded.contents == "test caret"


def test_generic_add_caret_returns_caret_annotation():
    """Verify generic add("Caret") returns the new dedicated CaretAnnotation class."""
    _, page = _page()
    caret = page.annotations.add("Caret", (10, 10, 30, 30), "note")
    assert type(caret) is CaretAnnotation
    assert caret.subtype == "Caret"
    assert hasattr(caret, "symbol")


def test_caret_symbol_validation():
    """Verify symbol property only accepts 'None' or 'Paragraph'."""
    _, page = _page()
    caret = page.annotations.add_caret((50, 300, 90, 340), "test", symbol="Paragraph")
    # Valid values work
    caret.symbol = "None"
    assert caret.symbol == "None"
    caret.symbol = "Paragraph"
    assert caret.symbol == "Paragraph"
    # Invalid value raises ValueError
    with pytest.raises(ValueError, match="Invalid symbol value"):
        caret.symbol = "InvalidValue"


def test_circle_frame_initial_value_is_none():
    """Verify a newly created circle annotation has no frame by default."""
    _, page = _page()
    circle = page.annotations.add_circle((100, 200, 300, 400))
    assert circle.frame is None


def test_circle_frame_set_and_get():
    """Verify frame can be set and retrieved."""
    _, page = _page()
    circle = page.annotations.add_circle((100, 200, 300, 400))
    circle.frame = (1.0, 2.0, 3.0, 4.0)
    assert circle.frame == (1.0, 2.0, 3.0, 4.0)


def test_circle_frame_set_none():
    """Verify frame can be set to None."""
    _, page = _page()
    circle = page.annotations.add_circle((100, 200, 300, 400))
    circle.frame = (1.0, 1.0, 1.0, 1.0)
    assert circle.frame == (1.0, 1.0, 1.0, 1.0)
    circle.frame = None
    assert circle.frame is None


def test_circle_frame_validation_type():
    """Verify frame raises TypeError for non-tuple input."""
    _, page = _page()
    circle = page.annotations.add_circle((100, 200, 300, 400))
    with pytest.raises(TypeError, match="frame must be a 4-tuple"):
        circle.frame = "invalid"


def test_circle_frame_validation_length():
    """Verify frame raises ValueError for wrong-length tuple."""
    _, page = _page()
    circle = page.annotations.add_circle((100, 200, 300, 400))
    with pytest.raises(ValueError, match="frame must be a 4-tuple"):
        circle.frame = (1.0, 2.0, 3.0)


def test_circle_frame_validation_numeric():
    """Verify frame raises TypeError for non-numeric items."""
    _, page = _page()
    circle = page.annotations.add_circle((100, 200, 300, 400))
    with pytest.raises(TypeError, match="frame\\[2\\] must be a number"):
        circle.frame = (1.0, 2.0, "text", 4.0)


def test_circle_frame_validation_boolean():
    """Verify frame raises TypeError for boolean items (bools are not numbers)."""
    _, page = _page()
    circle = page.annotations.add_circle((100, 200, 300, 400))
    with pytest.raises(TypeError, match="frame\\[2\\] must be a number"):
        circle.frame = (1.0, 2.0, True, 4.0)


def test_circle_frame_survives_round_trip():
    """Verify frame value is preserved after save/load."""
    document, page = _page()
    circle = page.annotations.add_circle((100, 200, 300, 400))
    circle.frame = (0.5, 1.0, 1.5, 2.0)
    reloaded = _roundtrip(document).pages[1].annotations[-1]
    assert reloaded.frame == (0.5, 1.0, 1.5, 2.0)


def test_circle_center_initial_no_frame():
    """Verify a newly created circle annotation without frame has correct center."""
    _, page = _page()
    circle = page.annotations.add_circle((100, 200, 300, 400))
    # center of rect (100,200,300,400) = ((100+300)/2, (200+400)/2) = (200, 300)
    assert circle.center == (200.0, 300.0)


def test_circle_center_with_frame():
    """Verify center is computed from inner rect when frame is set."""
    _, page = _page()
    circle = page.annotations.add_circle((100, 200, 300, 400))
    # frame = (10, 30, 50, 70) -> inner rect = (110, 230, 250, 330)
    # center = ((110+250)/2, (230+330)/2) = (180.0, 280.0)
    circle.frame = (10, 30, 50, 70)
    assert circle.center == (180.0, 280.0)


def test_circle_center_read_only():
    """Verify center is read-only and assignment raises TypeError."""
    _, page = _page()
    circle = page.annotations.add_circle((100, 200, 300, 400))
    with pytest.raises(TypeError, match="read-only"):
        circle.center = (100.0, 200.0)


def test_circle_center_degenerate_frame():
    """Verify center falls back to rect center when inner rect is degenerate."""
    _, page = _page()
    circle = page.annotations.add_circle((100, 200, 300, 400))
    # frame = (100, 100, 100, 100) -> inner rect = (200, 300, 200, 300) which is degenerate
    circle.frame = (100, 100, 100, 100)
    # Should fall back to center of rect: (200.0, 300.0)
    assert circle.center == (200.0, 300.0)


def test_circle_center_updates_when_frame_changes():
    """Verify center value updates when frame is changed."""
    _, page = _page()
    circle = page.annotations.add_circle((100, 200, 300, 400))
    # Without frame: center of rect (100,200,300,400) = (200.0, 300.0)
    assert circle.center == (200.0, 300.0)
    # With frame (10,10,10,10): inner rect (110,210,290,390), center (200.0, 300.0)
    circle.frame = (10, 10, 10, 10)
    assert circle.center == (200.0, 300.0)
    # Back to no frame
    circle.frame = None
    assert circle.center == (200.0, 300.0)


def test_circle_center_updates_when_rect_changes():
    """Verify center value updates when rect is changed."""
    _, page = _page()
    circle = page.annotations.add_circle((100, 200, 300, 400))
    circle.frame = (10, 10, 10, 10)
    # Initial: rect (100,200,300,400), frame (10,10,10,10)
    # Inner rect (110,210,290,390), center = (200.0, 300.0)
    assert circle.center == (200.0, 300.0)
    # Change rect
    circle.rect = (0, 0, 100, 100)
    # New: rect (0,0,100,100), frame (10,10,10,10)
    # Inner rect (10,10,90,90), center = (50.0, 50.0)
    assert circle.center == (50.0, 50.0)


def test_circle_center_type():
    """Verify center returns a tuple of floats."""
    _, page = _page()
    circle = page.annotations.add_circle((0, 0, 100, 100))
    center = circle.center
    assert isinstance(center, tuple)
    assert len(center) == 2
    assert isinstance(center[0], float)
    assert isinstance(center[1], float)


def test_circle_center_round_trip():
    """Verify center value is consistent after save/load."""
    document, page = _page()
    circle = page.annotations.add_circle((100, 200, 300, 400))
    circle.frame = (10, 20, 30, 40)
    expected_center = circle.center
    reloaded = _roundtrip(document).pages[1].annotations[-1]
    assert reloaded.center == expected_center


def test_square_frame_works_like_circle():
    """Verify SquareAnnotation also has the frame property."""
    _, page = _page()
    square = page.annotations.add_square((50, 50, 150, 150))
    assert hasattr(square, "frame")
    assert square.frame is None
    square.frame = (2.0, 2.0, 2.0, 2.0)
    assert square.frame == (2.0, 2.0, 2.0, 2.0)


def test_caret_annotation_has_frame_property():
    """Verify CaretAnnotation has the frame property like Square and Circle."""
    _, page = _page()
    caret = page.annotations.add_caret((50, 50, 150, 150), "test")
    assert hasattr(caret, "frame")
    assert caret.frame is None
    caret.frame = (1.0, 2.0, 3.0, 4.0)
    assert caret.frame == (1.0, 2.0, 3.0, 4.0)


def test_caret_annotation_frame_set_none():
    """Verify setting frame to None removes the /RD entry."""
    _, page = _page()
    caret = page.annotations.add_caret((50, 50, 150, 150), "test")
    caret.frame = (1.0, 2.0, 3.0, 4.0)
    assert caret.frame == (1.0, 2.0, 3.0, 4.0)
    caret.frame = None
    assert caret.frame is None


def test_caret_annotation_frame_validation():
    """Verify frame setter validates input types and lengths."""
    _, page = _page()
    caret = page.annotations.add_caret((50, 50, 150, 150), "test")

    with pytest.raises(TypeError, match="frame must be a 4-tuple of floats or None"):
        caret.frame = [1.0, 2.0, 3.0, 4.0]

    with pytest.raises(ValueError, match="frame must be a 4-tuple, not a 3-tuple"):
        caret.frame = (1.0, 2.0, 3.0)

    with pytest.raises(TypeError, match="frame\\[0\\] must be a number, not str"):
        caret.frame = ("a", 2.0, 3.0, 4.0)

    with pytest.raises(TypeError, match="frame\\[1\\] must be a number, not bool"):
        caret.frame = (1.0, True, 3.0, 4.0)


# ---------------------------------------------------------------------------
# LineAnnotation.starting_style
# ---------------------------------------------------------------------------


def test_line_annotation_has_starting_style_property():
    """Verify starting_style property exists and is accessible via dir()."""
    _, page = _page()
    line = page.annotations.add_line((10, 10), (100, 10))
    assert hasattr(line, "starting_style")
    assert "starting_style" in dir(line)


def test_line_annotation_starting_style_getter():
    """Verify starting_style returns the first element of LE array."""
    _, page = _page()
    line = page.annotations.add_line((10, 10), (100, 10))
    line.set_property("LE", [Name("Square"), Name("OpenArrow")])
    assert line.starting_style == "Square"


def test_line_annotation_starting_style_getter_default():
    """Verify starting_style returns 'None' when LE array is missing."""
    _, page = _page()
    line = page.annotations.add_line((10, 10), (100, 10))
    assert line.starting_style == "None"


def test_line_annotation_starting_style_setter():
    """Verify starting_style setter updates the LE array."""
    document, page = _page()
    line = page.annotations.add_line((10, 10), (100, 10))
    line.starting_style = "Square"
    assert line.get_property("LE") == [Name("Square")]
    assert line.starting_style == "Square"
    reloaded = _roundtrip(document).pages[1].annotations[0]
    assert reloaded.starting_style == "Square"


def test_line_annotation_starting_style_setter_preserves_end():
    """Verify starting_style setter preserves the end style."""
    document, page = _page()
    line = page.annotations.add_line((10, 10), (100, 10))
    line.set_property("LE", [Name("OpenArrow"), Name("ClosedArrow")])
    line.starting_style = "Square"
    assert line.get_property("LE") == [Name("Square"), Name("ClosedArrow")]
    assert line.line_endings == ("Square", "ClosedArrow")
    reloaded = _roundtrip(document).pages[1].annotations[0]
    assert reloaded.line_endings == ("Square", "ClosedArrow")


def test_line_annotation_starting_style_setter_clears_le_when_both_none():
    """Verify starting_style setter removes LE array when both start and end are None."""
    document, page = _page()
    line = page.annotations.add_line((10, 10), (100, 10))
    line.set_property("LE", [Name("Square"), Name("None")])
    line.starting_style = "None"
    assert line.get_property("LE") is None
    reloaded = _roundtrip(document).pages[1].annotations[0]
    assert reloaded.starting_style == "None"


def test_line_annotation_starting_style_validation():
    """Verify starting_style setter raises ValueError for invalid values."""
    _, page = _page()
    line = page.annotations.add_line((10, 10), (100, 10))
    with pytest.raises(ValueError, match="Invalid starting_style"):
        line.starting_style = "InvalidValue"


def test_line_annotation_ending_style_validation():
    """Verify ending_style setter raises ValueError for invalid values."""
    _, page = _page()
    line = page.annotations.add_line((10, 10), (100, 10))
    with pytest.raises(ValueError, match="Invalid ending_style"):
        line.ending_style = "InvalidValue"


def test_line_annotation_starting_style_clears_appearance():
    """Verify starting_style setter clears existing appearance stream."""
    _document, page = _page()
    line = page.annotations.add_line((10, 10), (100, 10))
    line.starting_style = "Square"
    line.generate_appearance()
    assert line.has_appearance is True
    line.starting_style = "OpenArrow"
    assert line.has_appearance is False


def test_all_valid_starting_styles():
    """Verify all valid line ending styles can be set."""
    valid_styles = ["None", "Square", "Circle", "Diamond", "OpenArrow", "ClosedArrow", "Butt", "ROpenArrow", "RClosedArrow", "Slash"]
    _, page = _page()
    for style in valid_styles:
        line = page.annotations.add_line((10, 10), (100, 10))
        line.starting_style = style
        assert line.starting_style == style


def test_starting_style_preserves_end_none():
    """Verify starting_style preserves Name(\"None\") end style."""
    document, page = _page()
    line = page.annotations.add_line((10, 10), (100, 10))
    # Start with LE = [Name("Square"), Name("None")]
    line.set_property("LE", [Name("Square"), Name("None")])
    assert line.get_property("LE") == [Name("Square"), Name("None")]
    assert line.line_endings == ("Square", "None")

    # Change start style to "Circle" - should preserve the "None" end
    line.starting_style = "Circle"
    assert line.get_property("LE") == [Name("Circle"), Name("None")]
    assert line.line_endings == ("Circle", "None")

    # Roundtrip should preserve the end style
    reloaded = _roundtrip(document).pages[1].annotations[0]
    assert reloaded.line_endings == ("Circle", "None")


def test_all_valid_ending_styles():
    """Verify all valid line ending styles can be set via ending_style."""
    valid_styles = ["None", "Square", "Circle", "Diamond", "OpenArrow", "ClosedArrow", "Butt", "ROpenArrow", "RClosedArrow", "Slash"]
    _, page = _page()
    for style in valid_styles:
        line = page.annotations.add_line((10, 10), (100, 10))
        line.ending_style = style
        assert line.ending_style == style


def test_ending_style_preserves_start_none():
    """Verify ending_style preserves Name(\"None\") start style."""
    document, page = _page()
    line = page.annotations.add_line((10, 10), (100, 10))
    # Start with LE = [Name("None"), Name("Square")]
    line.set_property("LE", [Name("None"), Name("Square")])
    assert line.get_property("LE") == [Name("None"), Name("Square")]
    assert line.line_endings == ("None", "Square")

    # Change end style to "Circle" - should preserve the "None" start
    line.ending_style = "Circle"
    assert line.get_property("LE") == [Name("None"), Name("Circle")]
    assert line.line_endings == ("None", "Circle")

    # Roundtrip should preserve the start style
    reloaded = _roundtrip(document).pages[1].annotations[0]
    assert reloaded.line_endings == ("None", "Circle")


def test_ending_style_with_start_none_removes_array():
    """Verify setting end to \"None\" with start \"None\" removes the LE array."""
    document, page = _page()
    line = page.annotations.add_line((10, 10), (100, 10))
    # Start with LE = [Name("None"), Name("Square")]
    line.set_property("LE", [Name("None"), Name("Square")])

    # Change end style to "None" - should remove the LE array
    line.ending_style = "None"
    assert line.get_property("LE") is None
    assert line.line_endings == ()

    # Roundtrip should have no LE array
    reloaded = _roundtrip(document).pages[1].annotations[0]
    assert reloaded.line_endings == ()


def test_ending_style_setter_preserves_start():
    """Verify ending_style setter preserves the start style."""
    document, page = _page()
    line = page.annotations.add_line((10, 10), (100, 10))
    line.set_property("LE", [Name("OpenArrow"), Name("ClosedArrow")])
    line.ending_style = "Square"
    assert line.get_property("LE") == [Name("OpenArrow"), Name("Square")]
    assert line.line_endings == ("OpenArrow", "Square")
    reloaded = _roundtrip(document).pages[1].annotations[0]
    assert reloaded.line_endings == ("OpenArrow", "Square")


def test_ending_style_read_write_symmetry():
    """Verify ending_style read-write symmetry with line_endings[1]."""
    _document, page = _page()
    line = page.annotations.add_line((10, 10), (100, 10))
    line.line_endings = (None, "Square")

    # Read via ending_style should match line_endings[1]
    assert line.ending_style == line.line_endings[1]

    # Write via ending_style should update line_endings[1]
    line.ending_style = "Circle"
    assert line.line_endings[1] == "Circle"
    assert line.ending_style == "Circle"


def test_polyline_has_ending_style_property():
    """Verify PolyLineAnnotation inherits the ending_style property."""
    _, page = _page()
    polyline = page.annotations.add_polyline([(10, 10), (100, 10)])
    assert hasattr(polyline, "ending_style")
    polyline.ending_style = "Square"
    assert polyline.ending_style == "Square"


def test_starting_style_with_end_none_removes_array():
    """Verify setting start to \"None\" with end \"None\" removes the LE array."""
    document, page = _page()
    line = page.annotations.add_line((10, 10), (100, 10))
    # Start with LE = [Name("Square"), Name("None")]
    line.set_property("LE", [Name("Square"), Name("None")])

    # Change start style to "None" - should remove the LE array
    line.starting_style = "None"
    assert line.get_property("LE") is None
    assert line.line_endings == ()

    # Roundtrip should have no LE array
    reloaded = _roundtrip(document).pages[1].annotations[0]
    assert reloaded.line_endings == ()


def test_polyline_has_starting_style_property():
    """Verify PolyLineAnnotation inherits the starting_style property."""
    _, page = _page()
    polyline = page.annotations.add_polyline([(10, 10), (100, 10)])
    assert hasattr(polyline, "starting_style")
    polyline.starting_style = "Square"
    assert polyline.starting_style == "Square"


def test_page_stamp_count_property_exists():
    """Verify Page.stamp_count property exists and is accessible."""
    _, page = _page()
    assert hasattr(page, "stamp_count")
    # Property should be a read-only property
    assert isinstance(type(page).stamp_count, property)


def test_page_stamp_count_returns_zero_when_no_stamps():
    """Verify stamp_count returns 0 when there are no stamp annotations."""
    _, page = _page()
    assert page.stamp_count == 0


def test_page_stamp_count_returns_one_after_adding_stamp():
    """Verify stamp_count returns 1 after adding a stamp annotation."""
    _, page = _page()
    page.annotations.add_stamp((100, 100, 200, 200), "Approved")
    assert page.stamp_count == 1


def test_page_stamp_count_counts_all_stamps():
    """Verify stamp_count counts all stamp annotations, not other annotation types."""
    _, page = _page()
    page.annotations.add_stamp((100, 100, 200, 200), "Approved")
    page.annotations.add_stamp((200, 200, 300, 300), "Draft")
    page.annotations.add_text((50, 50, 60, 60), "note")
    page.annotations.add_square((50, 300, 200, 360))
    assert page.stamp_count == 2


def test_page_stamp_count_is_page_specific():
    """Verify stamp_count is per-page, not shared across pages."""
    document = Document()
    page1 = document.pages.add(PageSize.A4)
    page2 = document.pages.add(PageSize.A4)

    page1.annotations.add_stamp((100, 100, 200, 200), "Approved")
    page1.annotations.add_stamp((200, 200, 300, 300), "Draft")
    page2.annotations.add_stamp((50, 50, 100, 100), "Signed")

    assert page1.stamp_count == 2
    assert page2.stamp_count == 1

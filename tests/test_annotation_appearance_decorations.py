"""Annotation appearance decorations: dash patterns, line endings, cloud borders."""

from __future__ import annotations

from aspose_pdf.engine.appearance import build_appearance
from aspose_pdf.engine.cos import AnnotationName as N

_RECT = (0, 0, 120, 80)


# ---------------------------------------------------------------------------
# Dash patterns (/BS /S /D, /BS /D, legacy /Border dash array)
# ---------------------------------------------------------------------------


def test_dash_from_border_style_array():
    gen = build_appearance(
        "Square", _RECT, {"C": [0, 0, 0], "BS": {"S": N("D"), "D": [3, 2], "W": 2}}
    )
    assert gen is not None
    assert b"[3 2] 0 d" in gen.content


def test_dash_style_d_without_array_defaults():
    gen = build_appearance(
        "Square", _RECT, {"C": [0, 0, 0], "BS": {"S": N("D"), "W": 1}}
    )
    assert b"[3] 0 d" in gen.content


def test_solid_border_emits_no_dash():
    gen = build_appearance("Square", _RECT, {"C": [0, 0, 0], "BS": {"W": 1}})
    assert b" d\n" not in gen.content


def test_dash_from_legacy_border_array():
    gen = build_appearance(
        "Circle", _RECT, {"C": [0, 0, 0], "Border": [0, 0, 1, [2, 2]]}
    )
    assert b"[2 2] 0 d" in gen.content


def test_dash_applies_to_line_and_freetext():
    line = build_appearance(
        "Line", _RECT, {"L": [10, 10, 100, 60], "BS": {"S": N("D"), "D": [4]}}
    )
    assert b"[4] 0 d" in line.content
    ft = build_appearance(
        "FreeText",
        _RECT,
        {"Contents": "hi", "BS": {"S": N("D"), "D": [5, 3], "W": 2}},
    )
    assert b"[5 3] 0 d" in ft.content


# ---------------------------------------------------------------------------
# Line endings (/LE)
# ---------------------------------------------------------------------------


def test_line_open_arrow_end_adds_strokes():
    plain = build_appearance("Line", _RECT, {"L": [10, 40, 100, 40], "C": [0, 0, 0]})
    arrow = build_appearance(
        "Line",
        _RECT,
        {"L": [10, 40, 100, 40], "C": [0, 0, 0], "LE": [N("None"), N("OpenArrow")]},
    )
    # The arrowhead adds two more line segments and a stroke beyond the shaft.
    assert arrow.content.count(b" l") > plain.content.count(b" l")
    assert arrow.content.count(b"\nS\n") == 2  # shaft + arrowhead


def test_line_closed_arrow_is_filled():
    gen = build_appearance(
        "Line",
        _RECT,
        {"L": [10, 40, 100, 40], "C": [1, 0, 0], "LE": [N("None"), N("ClosedArrow")]},
    )
    assert b"\nb\n" in gen.content  # closepath-fill-stroke triangle
    assert b"1 0 0 rg" in gen.content  # filled with the line colour


def test_line_diamond_and_circle_endings():
    diamond = build_appearance(
        "Line", _RECT, {"L": [10, 40, 100, 40], "LE": [N("Diamond"), N("None")]}
    )
    assert b"\nb\n" in diamond.content
    circle = build_appearance(
        "Line", _RECT, {"L": [10, 40, 100, 40], "LE": [N("None"), N("Circle")]}
    )
    assert circle.content.count(b" c") == 4  # four Bézier arcs


def test_line_none_ending_draws_only_the_shaft():
    gen = build_appearance(
        "Line", _RECT, {"L": [10, 40, 100, 40], "LE": [N("None"), N("None")]}
    )
    assert gen.content.count(b"\nS\n") == 1  # shaft only


def test_polyline_endings_orient_to_end_edges():
    gen = build_appearance(
        "PolyLine",
        _RECT,
        {"Vertices": [10, 10, 60, 60, 110, 10], "LE": [N("OpenArrow"), N("OpenArrow")]},
    )
    assert gen is not None
    # Shaft stroke plus one stroke per open-arrow head.
    assert gen.content.count(b"\nS\n") == 3


# ---------------------------------------------------------------------------
# Cloud borders (/BE /S /C)
# ---------------------------------------------------------------------------


def test_square_cloud_border_replaces_rectangle():
    gen = build_appearance(
        "Square", _RECT, {"C": [0, 0, 1], "BE": {"S": N("C"), "I": 2}, "BS": {"W": 1}}
    )
    assert gen is not None
    assert b" c\n" in gen.content  # scalloped Bézier edges
    assert b" re" not in gen.content  # not a plain rectangle


def test_square_without_be_stays_rectangular():
    gen = build_appearance("Square", _RECT, {"C": [0, 0, 0], "BS": {"W": 1}})
    assert b" re" in gen.content
    assert b" c\n" not in gen.content


def test_polygon_cloud_border():
    gen = build_appearance(
        "Polygon",
        _RECT,
        {"Vertices": [10, 10, 110, 10, 60, 70], "C": [0, 0, 0], "BE": {"S": N("C"), "I": 1}},
    )
    assert gen is not None
    assert b" c\n" in gen.content


def test_be_non_cloud_style_is_ignored():
    # /BE with a solid style ("S") is not a cloud: the border stays straight.
    gen = build_appearance(
        "Square", _RECT, {"C": [0, 0, 0], "BE": {"S": N("S")}, "BS": {"W": 1}}
    )
    assert b" re" in gen.content
    assert b" c\n" not in gen.content

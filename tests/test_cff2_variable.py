"""Variable CFF2: drawing an instance rather than a default master.

A CFF2 charstring carries its variation *deltas* inline, as blend operands,
while the font's ItemVariationStore says how many there are and what each one
means. The store was never found -- the Top DICT parser stopped at CFF1's
operator range, so vstore (operator 24) was read as an operand -- which
left blend unable to tell a delta from a coordinate. A variable CFF2 font
therefore drew *garbage*, not the default master the documentation promised.

With the store readable, the deltas can be dropped (the default master, and
what happens with no instance requested) or applied (an interpolated one). The
fixture is a two-master font whose glyph is a rectangle 100 units wide at
wght 100 and 400 wide at 900, so an instance's geometry is arithmetic
anyone can check; it was built with fontTools, which also produced the
reference outlines this was verified against.
"""

from __future__ import annotations

import pytest

from aspose_pdf.engine.cff_outlines import CffOutlines
from aspose_pdf.engine.font_resolver import FontResolver

_VARIABLE_CFF2 = bytes.fromhex(
    "4f54544f000c00800003004043464632a4fb7ce90000027800000051485641520011"
    "0025000002cc0000002a4f532f3243f444fd0000013000000060535441547870688c"
    "000002f80000001c636d6170000c00940000019800000034667661727bc569920000"
    "031400000024686561642daddf28000000cc0000003668686561044e032200000104"
    "00000024686d747802bc006400000190000000066d61787000025000000001280000"
    "00066e616d65762c1706000001cc00000081706f7374002800000000025000000026"
    "0001000000010000d416353f5f0f3cf5000303e800000000e6b5cdc600000000e6b5"
    "cdc60064000000c802bc000000030002000000000000000100000320ff3800000258"
    "0064019000c800010000000000000000000000000000000100005000000200000003"
    "02580064000500040000000000000000000000000000000000000000000000000000"
    "000000000000000000010000000000000000000000003f3f3f3f0000004100410320"
    "00000000032000c80000000000000000000000000000002000000258006400640000"
    "00000002000000030000001400030001000000140004002000000004000400010000"
    "0041ffff00000041ffffffc000010000000000000006004e00010000000000010007"
    "000000010000000000020004000700010000000001000006000b0003000104090001"
    "000e001100030001040900020008001f0003000104090100000c0027566172546573"
    "745468696e5765696768740056006100720054006500730074005400680069006e00"
    "57006500690067006800740000000002000000000000000000000000000000000000"
    "00000000000000000000000000020000002400000200050007d20c24bb119b180000"
    "0000001e00010000000c000100000016000100010000400040000000000000010000"
    "0000000201010110ef16eff7c08c10f95027fbc08c1006000000010101048bdc1200"
    "0000000100000000001400000000000000000000000000010000000c000100000010"
    "00010000000200000000000000010001000800010000001400000000000000027767"
    "68740100000000010000001000020001001400000008776768740064000000640000"
    "0384000000000100"
)


def _glyph_width(outlines: CffOutlines, gid: int = 1) -> float:
    xs = [point[0] for contour in outlines.outline(gid) for point in contour]
    assert xs, "the glyph drew nothing"
    return max(xs) - min(xs)


# ---------------------------------------------------------------------------
# Reading the font
# ---------------------------------------------------------------------------


def test_the_axes_are_read_from_fvar():
    outlines = CffOutlines(_VARIABLE_CFF2)

    assert outlines.ok
    assert outlines.axes == [
        {"tag": "wght", "min": 100.0, "default": 100.0, "max": 900.0}
    ]


def test_the_variation_store_is_found_at_all():
    """vstore is operator 24, past where a CFF1 DICT parser stops looking.

    Without it there are no region counts, and blend cannot tell how many
    stack entries are deltas -- the failure that made these fonts draw noise.
    """
    outlines = CffOutlines(_VARIABLE_CFF2)

    assert outlines._region_counts, "no ItemVariationStore was read"
    assert outlines._regions, "no variation regions were read"


# ---------------------------------------------------------------------------
# Drawing an instance
# ---------------------------------------------------------------------------


def test_no_instance_draws_the_default_master():
    outlines = CffOutlines(_VARIABLE_CFF2)

    assert _glyph_width(outlines) == pytest.approx(100.0)


@pytest.mark.parametrize(
    ("weight", "width"),
    [(100, 100.0), (300, 175.0), (500, 250.0), (700, 325.0), (900, 400.0)],
)
def test_an_instance_interpolates_between_the_masters(weight: int, width: float):
    """Verified against fontTools' own instancer, which agrees exactly."""
    outlines = CffOutlines(_VARIABLE_CFF2, variation={"wght": weight})

    assert _glyph_width(outlines) == pytest.approx(width)


def test_a_coordinate_outside_the_axis_is_clamped():
    beyond = CffOutlines(_VARIABLE_CFF2, variation={"wght": 5000})
    at_max = CffOutlines(_VARIABLE_CFF2, variation={"wght": 900})

    assert _glyph_width(beyond) == pytest.approx(_glyph_width(at_max))


def test_an_unknown_axis_is_ignored():
    outlines = CffOutlines(_VARIABLE_CFF2, variation={"wdth": 200})

    assert _glyph_width(outlines) == pytest.approx(100.0)


def test_a_non_variable_font_ignores_the_request():
    """A plain CFF has no axes; asking for one must not disturb it."""
    from aspose_pdf.engine.std_font_data import load_substitute_sfnt

    program = load_substitute_sfnt("sans-regular")
    plain = CffOutlines(program)
    asked = CffOutlines(program, variation={"wght": 700})

    assert plain.axes == [] and asked.axes == []
    assert plain.ok == asked.ok


# ---------------------------------------------------------------------------
# Reaching a style through the axes
# ---------------------------------------------------------------------------


def test_a_variable_substitute_face_supplies_bold():
    """Modern system fonts ship one variable file, not four static ones."""
    resolver = FontResolver(programs=(("VarTest", _VARIABLE_CFF2),))

    regular = resolver.by_name("VarTest")
    bold = resolver.by_name("VarTest", flags=1 << 18)

    assert regular.variation is None
    assert bold.variation == {"wght": 700.0}
    assert _glyph_width(
        CffOutlines(bold.data, variation=bold.variation)
    ) > _glyph_width(CffOutlines(regular.data, variation=regular.variation))


def test_a_face_that_is_already_bold_is_left_where_it_is():
    """Moving the axis of a file that already says Bold would overshoot."""
    resolver = FontResolver(programs=(("VarTest Bold", _VARIABLE_CFF2),))

    face = resolver.by_name("VarTest Bold", flags=1 << 18)

    assert face is not None
    assert face.variation is None


def test_a_non_variable_face_asks_for_no_coordinates():
    from aspose_pdf.engine.std_font_data import load_substitute_sfnt

    resolver = FontResolver(
        programs=(("Plain", load_substitute_sfnt("sans-regular")),)
    )

    face = resolver.by_name("Plain", flags=1 << 18)

    assert face is not None
    assert face.variation is None

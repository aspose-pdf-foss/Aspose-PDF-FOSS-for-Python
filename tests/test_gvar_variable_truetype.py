"""Variable TrueType: drawing the instance asked for, not the default master.

A variable ``glyf`` font stores one outline and, in ``gvar``, the deltas that
move its points for each region of the design space. Nothing read that table, so
a variable TrueType always drew its default master -- and since a modern system
font ships as one variable file rather than four static ones, a substitute face
asked for Bold came back Regular. (Variable CFF2 was already instanced; see
:mod:`tests.test_cff2_variable`.)

The fixture is a three-glyph font on a single ``wght`` axis from 100 to 900,
built with fontTools so its arithmetic is checkable by hand:

* ``rect`` is 100 units wide at 100 and 400 at 900 -- a tuple that names every
  point it moves;
* ``tri`` names two of its five points, leaving the one between them to be
  inferred (the rule OpenType calls IUP), and that point sits exactly midway;
* ``both`` is a composite of two ``rect`` components whose second one moves
  right, because a composite's deltas move its components rather than points.

Verified against fontTools' own instancer on eight combinations of real variable
fonts and axes (NewYork, SFCompact, SFNSMono, SFHebrew, SFArmenian, SFGeorgian;
``wght``, ``opsz``, ``GRAD``, and all three at once): over 2 442 glyphs and
102 827 points no contour differed in structure and no coordinate differed by
more than 1.5 font units, which is the rounding fontTools applies and this does
not.
"""

from __future__ import annotations

import pytest

from aspose_pdf.engine.glyph_outlines import TrueTypeOutlines
from aspose_pdf.engine.gvar import GlyphVariations, _infer_unreferenced

#: gid 1 ``rect``, gid 2 ``tri``, gid 3 ``both``; one ``wght`` axis, 100 to 900.
_VARIABLE_TRUETYPE = bytes.fromhex(
    "00010000000c0080000300404f532f32413840b30000014800000060636d6170000c"
    "0096000001b800000034667661727bc569920000030800000024676c79663265febf"
    "000001f800000046677661726c4847150000032c00000058686561642e04a5ec0000"
    "00cc0000003668686561044e01950000010400000024686d747804b00000000001a8"
    "000000106c6f6361002e0017000001ec0000000a6d617870000a0010000001280000"
    "00206e616d655efb6476000002400000008d706f73743e5156e4000002d000000038"
    "0001000000010000772b294e5f0f3cf5000303e800000000e6e131be00000000e6e1"
    "31be00000000012c0190000000030002000000000000000100000320ff3800000258"
    "00000000012c00010000000000000000000000000000000400010000000400050001"
    "000800020002000000000000000000000000000200010003012c0190000500040000"
    "00000000000000000000000000000000000000000000000000000000000000000001"
    "0000000000000000000000003f3f3f3f000000410043000000000000000000000000"
    "00000000000000000000000000200000025800000064000000640000019000000000"
    "00020000000300000014000300010000001400040020000000040004000100000043"
    "ffff00000041ffffffc000010000000000000000000b001700230000000100000000"
    "00640190000300003133112364640190000100000000006400c80004000031333335"
    "23323264c800ffff00000000012c01900026000100000007000100c8000000000000"
    "0006004e000100000000000100080000000100000000000200070008000100000000"
    "01000006000f0003000104090001001000150003000104090002000e002500030001"
    "04090100000c00334776617254657374526567756c61725765696768740047007600"
    "61007200540065007300740052006500670075006c00610072005700650069006700"
    "68007400000000020000000000000000000000000000000000000000000000000000"
    "000000000004000001020103010404726563740374726904626f7468000100000010"
    "00020001001400000008776768740064000000640000038400000000010000010000"
    "000100010000001e000400000000002000000000000b0013001c4000800100080008"
    "00000403000101018041012c012c8083800100080004000002010002800064818001"
    "00080005000002010001804000c88100"
)


def _outlines(**variation: float) -> TrueTypeOutlines:
    outlines = TrueTypeOutlines(
        _VARIABLE_TRUETYPE, variation=variation or None
    )
    assert outlines.ok
    return outlines


def _points(outlines: TrueTypeOutlines, gid: int) -> list[tuple[float, float]]:
    return [point for contour in outlines.outline(gid) for point in contour]


def _width(outlines: TrueTypeOutlines, gid: int) -> float:
    xs = [x for x, _y in _points(outlines, gid)]
    assert xs, "the glyph drew nothing"
    return max(xs) - min(xs)


# --- the instance asked for ---------------------------------------------------


def test_the_default_instance_is_the_outlines_as_stored():
    assert _width(_outlines(), 1) == 100.0
    assert _width(_outlines(wght=100), 1) == 100.0


def test_an_instance_moves_the_points_the_deltas_name():
    assert _width(_outlines(wght=900), 1) == 400.0


@pytest.mark.parametrize(
    ("wght", "expected"),
    [(100, 100.0), (300, 175.0), (500, 250.0), (700, 325.0), (900, 400.0)],
)
def test_an_instance_between_the_masters_is_interpolated(wght, expected):
    # Linear in the normalised coordinate, which is linear in wght here.
    assert _width(_outlines(wght=wght), 1) == pytest.approx(expected)


def test_an_axis_value_outside_its_range_is_clamped_to_it():
    assert _width(_outlines(wght=5000), 1) == _width(_outlines(wght=900), 1)
    assert _width(_outlines(wght=-100), 1) == _width(_outlines(wght=100), 1)


def test_an_axis_the_font_does_not_have_changes_nothing():
    assert _width(_outlines(slnt=-10), 1) == _width(_outlines(), 1)


def test_the_axes_are_reported():
    outlines = _outlines()
    assert [axis["tag"] for axis in outlines.axes] == ["wght"]
    assert outlines.axes[0]["min"] == 100 and outlines.axes[0]["max"] == 900


def test_the_coordinates_say_where_the_instance_sits():
    assert _outlines(wght=900).coordinates == (1.0,)
    assert _outlines(wght=100).coordinates == (0.0,)
    assert _outlines().coordinates == (0.0,)


# --- points the deltas leave out ----------------------------------------------


def test_a_point_between_two_moved_ones_follows_them():
    # tri names points 0 and 2, moving them by 0 and 100 in x. Point 1 starts
    # midway between them, so it moves midway: 50 at full weight.
    assert [
        point[0] for point in _points(_outlines(wght=900), 2)
    ] == pytest.approx([0.0, 100.0, 200.0, 200.0, 0.0, 0.0])
    assert [
        point[0] for point in _points(_outlines(wght=500), 2)
    ] == pytest.approx([0.0, 75.0, 150.0, 150.0, 0.0, 0.0])


def test_a_contour_with_one_moved_point_moves_with_it():
    moved = _infer_unreferenced(
        {1: (10.0, 20.0)}, 4, [3], [(0, 0), (10, 0), (10, 10), (0, 10)]
    )
    assert moved == [(10.0, 20.0)] * 4


def test_a_contour_with_no_moved_point_does_not_move():
    moved = _infer_unreferenced({}, 3, [2], [(0, 0), (10, 0), (10, 10)])
    assert moved == [(0.0, 0.0)] * 3


def test_a_point_beyond_the_moved_ones_takes_the_nearest():
    # Points 0 and 1 move; 2 and 3 are outside the span they cover in x, so each
    # follows whichever of the two it is nearest to along that coordinate.
    moved = _infer_unreferenced(
        {0: (0.0, 0.0), 1: (10.0, 0.0)},
        4,
        [3],
        [(0, 0), (10, 0), (20, 0), (-5, 0)],
    )
    assert moved[2][0] == pytest.approx(10.0)
    assert moved[3][0] == pytest.approx(0.0)


def test_each_coordinate_is_inferred_on_its_own():
    # A point's x says nothing about where its y should land.
    moved = _infer_unreferenced(
        {0: (0.0, 0.0), 2: (100.0, 40.0)},
        3,
        [2],
        [(0, 0), (50, 10), (100, 20)],
    )
    assert moved[1] == pytest.approx((50.0, 20.0))


# --- composites ---------------------------------------------------------------


def test_a_composite_s_deltas_move_its_components():
    # Two rect components, the second offset by 200. At full weight each rect
    # widens by 300 and the second component moves a further 200.
    assert _width(_outlines(), 3) == 300.0
    assert _width(_outlines(wght=900), 3) == 800.0
    assert len(_outlines(wght=900).outline(3)) == 2  # still two contours


def test_a_composite_interpolates_like_anything_else():
    assert _width(_outlines(wght=500), 3) == pytest.approx(550.0)


# --- fonts that do not vary ---------------------------------------------------


def test_a_font_with_no_axes_ignores_a_request():
    from aspose_pdf.engine.std_font_data import load_substitute_sfnt

    sfnt = load_substitute_sfnt("sans-regular")
    assert sfnt, "the bundled substitute is missing"
    plain = TrueTypeOutlines(sfnt)
    asked = TrueTypeOutlines(sfnt, variation={"wght": 900})
    assert plain.ok and asked.ok
    assert asked.axes == [] and asked.coordinates == ()
    assert plain.outline(40) == asked.outline(40)


@pytest.mark.parametrize(
    "table",
    [None, b"", b"\x00\x01", b"\x00\x02" + bytes(30), bytes(30)],
    ids=["missing", "empty", "truncated", "wrong version", "zeroed"],
)
def test_a_gvar_table_that_cannot_be_read_is_no_variation(table):
    assert not GlyphVariations(table, 1).ok


def test_variation_data_for_a_glyph_the_table_does_not_cover_is_none():
    variations = GlyphVariations(None, 1)
    assert variations.deltas(0, 4, (1.0,), [3], [(0, 0)] * 4) is None


#: A second font for the *encodings* rather than the meaning: two axes, a glyph
#: of 40 points whose deltas need two bytes each, a glyph varied by three tuples
#: (one per axis plus one that only applies in the middle of the weight range),
#: one whose deltas are mostly runs of zero, one moved by negative deltas, one of
#: 500 points whose tuple names two of them, and one whose two tuples share a
#: point list. gid 1 ``many``, 2 ``twoaxis``, 3 ``zeros``, 4 ``negative``,
#: 5 ``sparse``, 6 ``shared``; ``wght`` 100-900 (default 400) and ``wdth``
#: 50-200 (default 100).
_VARIABLE_SHAPES = bytes.fromhex(
    "00010000000c0080000300404f532f324138411a0000014800000060636d6170000c"
    "0099000001b80000003466766172f5b4deff0000085800000038676c79662861d398"
    "000001fc0000054467766172d004aefc00000890000001966865616430beaaa80000"
    "00cc00000036686865610708fe740000010400000024686d747801900000000001a8"
    "000000106c6f6361031b033c000001ec000000106d617870000901f6000001280000"
    "00206e616d65db03c0ee00000740000000ba706f737409e25369000007fc0000005a"
    "00010000000100003626e9515f0f3cf5000303e800000000e6e1348000000000e6e1"
    "34800000000003e600c8000000030002000000000000000100000320ff3800000190"
    "0000fdaa03e600010000000000000000000000000000000100010000000701f40001"
    "00000000000200000000000000000000000000000000000301900190000500040000"
    "00000000000000000000000000000000000000000000000000000000000000000001"
    "0000000000000000000000003f3f3f3f000000410046000000000000000000000000"
    "00000000000000000000000000200000019000000000000000000000000000000000"
    "00020000000300000014000300010000001400040020000000040004000100000046"
    "ffff00000041ffffffc00001000000000000000000370042004d0058029702a20001"
    "00000000018600b400270000313f05173f05173f05173f05173f05173f030a0a0a0a"
    "0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a"
    "0a1e1e1e1e1e1eb41e1e1e1e1e1eb41e1e1e1e1e1eb41e1e1e1e1e1eb41e1e1e1e1e"
    "1eb41e1e1e1e00010000000000c800c80003000031333523c8c8c800000100000000"
    "00c800c80003000031333523c8c8c80000010000000000c800c80003000031333523"
    "c8c8c80000010000000003e600c801f30000313f09173f09173f09173f09173f0917"
    "3f09173f09173f09173f09173f09173f09173f09173f09173f09173f09173f09173f"
    "09173f09173f09173f09173f09173f09173f09173f09173f09173f09173f09173f09"
    "173f09173f09173f09173f09173f09173f09173f09173f09173f09173f09173f0917"
    "3f09173f09173f09173f09173f09173f09173f030202020202020202020202020202"
    "02020202020202020202020202020202020202020202020202020202020202020202"
    "02020202020202020202020202020202020202020202020202020202020202020202"
    "02020202020202020202020202020202020202020202020202020202020202020202"
    "02020202020202020202020202020202020202020202020202020202020202020202"
    "02020202020202020202020202020202020202020202020202020202020202020202"
    "02020202020202020202020202020202020202020202020202020202020202020202"
    "02020202020202020202020202020202020202020202020202020202020202020202"
    "02020202020202020202020202020202020202020202020202020202020202020202"
    "02020202020202020202020202020202020202020202020202020202020202020202"
    "02020202020202020202020202020202020202020202020202020202020202020202"
    "02020202020202020202020202020202020202020202020202020202020202020202"
    "02020202020202020202020202020202020202020202020202020202020202020202"
    "02020202020202020202020202020202020202020202020202020202020202020202"
    "02020202020202020202020202020202020202020202020202020202020202020202"
    "02020202020202020214141414141414141414c814141414141414141414c8141414"
    "14141414141414c814141414141414141414c814141414141414141414c814141414"
    "141414141414c814141414141414141414c814141414141414141414c81414141414"
    "1414141414c814141414141414141414c814141414141414141414c8141414141414"
    "14141414c814141414141414141414c814141414141414141414c814141414141414"
    "141414c814141414141414141414c814141414141414141414c81414141414141414"
    "1414c814141414141414141414c814141414141414141414c8141414141414141414"
    "14c814141414141414141414c814141414141414141414c814141414141414141414"
    "c814141414141414141414c814141414141414141414c814141414141414141414c8"
    "14141414141414141414c814141414141414141414c814141414141414141414c814"
    "141414141414141414c814141414141414141414c814141414141414141414c81414"
    "1414141414141414c814141414141414141414c814141414141414141414c8141414"
    "14141414141414c814141414141414141414c814141414141414141414c814141414"
    "141414141414c814141414141414141414c814141414141414141414c81414141414"
    "1414141414c814141414141414141414c814141414141414141414c8141414140001"
    "0000000000c800c80003000031333523c8c8c8000000000800660001000000000001"
    "000a000000010000000000020007000a000100000000010000060011000100000000"
    "01010005001700030001040900010014001c0003000104090002000e003000030001"
    "04090100000c003e0003000104090101000a004a4776617253686170657352656775"
    "6c617257656967687457696474680047007600610072005300680061007000650073"
    "0052006500670075006c006100720057006500690067006800740057006900640074"
    "00680000000200000000000000000000000000000000000000000000000000000000"
    "000000070000010201030104010501060107046d616e790774776f61786973057a65"
    "726f73086e6567617469766506737061727365067368617265640000000100000010"
    "0002000200140000000c776768740064000001900000038400000000010077647468"
    "003200000064000000c8000000000101000100000002000200000024000700000000"
    "002c00000000006a0089009200a000a900b540000000000040008001000800a20000"
    "28270001010101010101010101010101010101010101010101010101010101010101"
    "010101010101010167012c012d012e012f0130013101320133013401350136013701"
    "380139013a013b013c013d013e013f01400141014201430144014501460147014801"
    "49014a014b014c014d014e014f0150015101520153670190018f018e018d018c018b"
    "018a0189018801870186018501840183018201810180017f017e017d017c017b017a"
    "0179017801770176017501740173017201710170016f016e016d016c016b016a0169"
    "8003001c00040000000a20010010e000200000001000000030000000020100010164"
    "64810201000281410096009604030001010103282828280328282828800100080004"
    "000004030001010182005a8380010008000e000004030001010103d8d8d8d843ff38"
    "ff38ff38ff38800100080004000002006480012c011e3c818002000c000400000004"
    "00010201000101323281810146460000"
)


def _shape_points(gid: int, **variation: float) -> list[tuple[float, float]]:
    outlines = TrueTypeOutlines(_VARIABLE_SHAPES, variation=variation or None)
    assert outlines.ok
    return [point for contour in outlines.outline(gid) for point in contour]


def test_a_tuple_that_moves_every_point_needs_no_point_list():
    # 40 points, each moved by more than a byte holds: the deltas come as words
    # and the point numbers are left out because the tuple moves all of them.
    default = _shape_points(1)
    moved = _shape_points(1, wght=900)
    assert len(moved) == len(default) == 41
    assert default[0] == (0.0, 0.0) and moved[0] == (300.0, 400.0)
    # Point 39 starts at (390, 120) and its delta is (300 + 39, 400 - 39).
    assert moved[39] == pytest.approx((729.0, 481.0))


def test_only_the_tuples_that_apply_are_added():
    # Three tuples: one per axis, and one that peaks halfway up the weight range.
    assert _shape_points(2, wght=900)[0] == pytest.approx((100.0, 0.0))
    assert _shape_points(2, wdth=200)[0] == pytest.approx((0.0, 150.0))
    # Asking for one axis must not bring in the other axis's tuple.
    assert _shape_points(2, wght=900)[0][1] == 0.0
    assert _shape_points(2, wdth=200)[0][0] == 0.0


def test_a_region_in_the_middle_of_an_axis_applies_only_there():
    # The third tuple runs from 0.25 through 0.5 to 0.75 in normalised weight.
    # wght 650 is 0.5: it applies in full, while the axis tuple applies at half.
    assert _shape_points(2, wght=650)[0] == pytest.approx((90.0, 40.0))
    # At the top of the axis it is past the region's end and adds nothing.
    assert _shape_points(2, wght=900)[0] == pytest.approx((100.0, 0.0))


def test_runs_of_zero_deltas_move_nothing():
    moved = _shape_points(3, wght=900)
    assert moved[:3] == [(0.0, 0.0), (200.0, 0.0), (200.0, 200.0)]
    assert moved[3] == pytest.approx((90.0, 200.0))


def test_both_axes_at_once_add_up():
    assert _shape_points(2, wght=900, wdth=200)[0] == pytest.approx((100.0, 150.0))


# --- reaching a style through the axis ---------------------------------------


def test_a_substituted_variable_face_is_drawn_at_the_weight_asked_for():
    # A modern system font ships as one variable file, so the renderer reaches
    # Bold by moving the weight axis. Left un-instanced, it drew Regular.
    import io

    from aspose_pdf import Document, FontSubstitutionOptions

    document = Document()
    page = document.pages.add()
    page.add_text("A", 20, 40, font_size=80, font_name="GvarShapes-Bold")
    buffer = io.BytesIO()
    document.save(buffer)
    document = Document(io.BytesIO(buffer.getvalue()))

    options = FontSubstitutionOptions(fonts={"GvarShapes": _VARIABLE_SHAPES})
    ink = _ink(document, options)
    assert ink, "the substitute drew nothing"

    # The same page with the weight axis left alone: its glyph is the default
    # master, which is smaller, so it puts less ink on the page.
    plain = FontSubstitutionOptions(fonts={"GvarShapes": _VARIABLE_SHAPES})
    with _no_variation():
        assert len(_ink(document, plain)) < len(ink)


def _ink(document, options) -> set[tuple[int, int]]:
    raster = document.pages[0].render(antialias=False, font_substitution=options)
    return {
        (x, y)
        for y in range(raster.height)
        for x in range(raster.width)
        if sum(raster.get_pixel(x, y)[:3]) < 600
    }


def _no_variation():
    """Pretend the decoder ignores the requested instance, as it used to."""
    import contextlib

    from aspose_pdf.engine import rasterizer

    @contextlib.contextmanager
    def patched():
        original = rasterizer.TrueTypeOutlines
        rasterizer.TrueTypeOutlines = lambda data, **_kwargs: original(data)
        try:
            yield
        finally:
            rasterizer.TrueTypeOutlines = original

    return patched()


def test_the_default_instance_does_not_read_the_table_at_all():
    # Nothing to apply, so nothing is parsed: the outlines as stored are the
    # default master by definition.
    assert TrueTypeOutlines(_VARIABLE_SHAPES)._variations is None
    assert TrueTypeOutlines(_VARIABLE_SHAPES, variation={"wght": 400})._variations is None
    assert TrueTypeOutlines(_VARIABLE_SHAPES, variation={"wght": 900})._variations is not None


def test_a_delta_that_moves_a_point_back_is_negative():
    # -40 fits in a signed byte and -200 needs a signed word; read as unsigned
    # either one would fling the point across the em square.
    assert _shape_points(4, wght=900)[0] == pytest.approx((-40.0, -200.0))
    assert _shape_points(4, wght=900)[2] == pytest.approx((160.0, 0.0))


def test_a_point_number_past_the_first_byte_is_read_whole():
    # A tuple over a 500-point glyph that names points 100 and 400: that step
    # does not fit in a byte, so the numbers are written as words. Each is an
    # increment on the one before, so read as absolute numbers the second point
    # would be 300 rather than 400 and the wrong part of the glyph would move.
    moved = _shape_points(5, wght=900)
    default = _shape_points(5)
    assert len(moved) == 501
    assert moved[100] == pytest.approx((230.0, 20.0))
    assert moved[400] == pytest.approx((860.0, 80.0))
    # The two named points move by 30 and 60, so the ones between them ramp
    # from one to the other -- and where that ramp *ends* is what says the
    # second point number was read as 400 and not as the 300 it is written as.
    for index, delta in ((250, 45.0), (350, 55.0), (400, 60.0), (450, 60.0)):
        assert moved[index][0] - default[index][0] == pytest.approx(delta)


def test_tuples_may_share_one_list_of_point_numbers():
    # Both tuples move the same two points, so the list is written once before
    # them rather than inside each.
    assert _shape_points(6, wght=900)[0] == pytest.approx((50.0, 0.0))
    assert _shape_points(6, wdth=200)[0] == pytest.approx((0.0, 70.0))
    assert _shape_points(6, wght=900, wdth=200)[0] == pytest.approx((50.0, 70.0))


def test_a_glyph_the_table_leaves_alone_has_no_deltas():
    from aspose_pdf.engine.font_variations import sfnt_table

    variations = GlyphVariations(sfnt_table(_VARIABLE_SHAPES, b"gvar"), 2)
    assert variations.ok
    # .notdef draws nothing and varies by nothing: its two offsets are equal.
    assert variations.deltas(0, 4, (1.0, 0.0), [3], [(0.0, 0.0)] * 4) is None


def test_a_gvar_whose_axis_count_is_not_the_font_s_is_refused():
    from aspose_pdf.engine.font_variations import sfnt_table

    table = sfnt_table(_VARIABLE_SHAPES, b"gvar")
    assert GlyphVariations(table, 2).ok  # the font really has two axes
    assert not GlyphVariations(table, 1).ok
    assert not GlyphVariations(table, 3).ok


def _gvar_header(
    *,
    axis_count: int = 1,
    glyph_count: int = 1,
    long_offsets: bool = False,
    offsets: tuple[int, ...] = (0, 0),
    body: bytes = b"",
) -> bytes:
    """A ``gvar`` table built by hand, for the shapes a font would not give."""
    import struct

    if not long_offsets:
        assert all(value % 2 == 0 for value in offsets), "short offsets are halved"
    shared_at = 20 + len(offsets) * (4 if long_offsets else 2)
    data_at = shared_at
    head = struct.pack(
        ">HHHHIHHI", 1, 0, axis_count, 0, shared_at, glyph_count,
        1 if long_offsets else 0, data_at,
    )
    if long_offsets:
        table = head + b"".join(struct.pack(">I", value) for value in offsets)
    else:
        table = head + b"".join(struct.pack(">H", value // 2) for value in offsets)
    return table + body


def test_long_glyph_offsets_are_read_as_the_words_they_are():
    # A big font needs 32-bit offsets, and the header's flag says so. Read as
    # 16-bit they would point into the middle of another glyph's data.
    import struct

    # One tuple moving point 0 by (7, 0): a peak of 1.0 on the only axis.
    points = bytes([1, 0, 0])      # one point number; a one-byte run; point 0
    deltas = bytes([0x00, 7]) + bytes([0x00, 0])  # one byte each: x = 7, y = 0
    tuple_header = struct.pack(">HH", len(points) + len(deltas), 0x8000 | 0x2000)
    tuple_header += struct.pack(">h", 16384)  # the peak: 1.0 on the only axis
    glyph = (
        struct.pack(">HH", 1, 4 + len(tuple_header))
        + tuple_header
        + points
        + deltas
    )
    table = _gvar_header(
        long_offsets=True, glyph_count=1, offsets=(0, len(glyph)), body=glyph
    )
    variations = GlyphVariations(table, 1)
    assert variations.ok
    deltas = variations.deltas(0, 1, (1.0,), [0], [(0.0, 0.0)])
    assert deltas == [(7.0, 0.0)]


def test_offsets_that_run_past_the_table_are_refused():
    table = _gvar_header(glyph_count=1, offsets=(0, 4096), body=b"\x00\x01\x00\x08")
    variations = GlyphVariations(table, 1)
    assert variations.ok  # the header itself is fine
    assert variations.deltas(0, 4, (1.0,), [3], [(0.0, 0.0)] * 4) is None


def test_a_glyph_with_no_data_of_its_own_is_left_alone():
    # Equal offsets mean this glyph does not vary. Read as data anyway, the
    # bytes that follow are the *next* glyph's, and it would move by those.
    import struct

    points = bytes([1, 0, 0])
    deltas = bytes([0x00, 9]) + bytes([0x00, 0])
    header = struct.pack(">HH", len(points) + len(deltas), 0x8000 | 0x2000)
    header += struct.pack(">h", 16384)
    second = struct.pack(">HH", 1, 4 + len(header)) + header + points + deltas
    second += b"\x00" * (len(second) % 2)  # short offsets are stored halved
    table = _gvar_header(
        glyph_count=2, offsets=(0, 0, len(second)), body=second
    )
    variations = GlyphVariations(table, 1)
    assert variations.ok
    assert variations.deltas(0, 1, (1.0,), [0], [(0.0, 0.0)]) is None
    # while the glyph those bytes really belong to does move
    assert variations.deltas(1, 1, (1.0,), [0], [(0.0, 0.0)]) == [(9.0, 0.0)]

"""Auto-generation of annotation appearance streams (/AP /N)."""

from __future__ import annotations

import io

from aspose_pdf import Document
from aspose_pdf.engine.appearance import build_appearance
from aspose_pdf.engine.cos import AnnotationName


def _new_page_doc() -> Document:
    doc = Document()
    doc.pages.add()
    return doc


# ---------------------------------------------------------------------------
# build_appearance (pure unit tests)
# ---------------------------------------------------------------------------


def test_build_square_with_stroke_and_fill():
    gen = build_appearance(
        "Square", (0, 0, 100, 100), {"C": [1, 0, 0], "IC": [0, 1, 0], "BS": {"W": 2}}
    )
    assert gen is not None
    assert b"1 0 0 RG" in gen.content  # red stroke
    assert b"0 1 0 rg" in gen.content  # green fill
    assert b"2 w" in gen.content
    assert b" re" in gen.content
    assert b"\nB\n" in gen.content  # fill + stroke
    assert gen.ext_gstates == {}


def test_build_square_without_colour_defaults_to_black_border():
    gen = build_appearance("Square", (0, 0, 50, 50), {})
    assert gen is not None
    assert b"0 G" in gen.content
    assert b"\nS\n" in gen.content  # stroke only


def test_build_circle_uses_bezier_curves():
    gen = build_appearance("Circle", (0, 0, 80, 60), {"IC": [0.5]})
    assert gen is not None
    assert gen.content.count(b" c\n") == 4  # four quarter-arc curves
    assert b"0.5 g" in gen.content  # grayscale fill


def test_build_line_converts_to_local_coordinates():
    # Rect origin (100,100); L is absolute -> local subtracts the origin.
    gen = build_appearance("Line", (100, 100, 300, 300), {"L": [120, 120, 220, 180]})
    assert gen is not None
    assert b"20 20 m" in gen.content
    assert b"120 80 l" in gen.content
    assert b"\nS\n" in gen.content


def test_build_polygon_closes_and_fills():
    gen = build_appearance(
        "Polygon", (0, 0, 100, 100), {"Vertices": [0, 0, 100, 0, 50, 100], "IC": [0, 0, 1]}
    )
    assert gen is not None
    assert b"0 0 m" in gen.content
    assert b"\nh\n" in gen.content  # closed
    assert b"\nB\n" in gen.content  # fill + stroke


def test_build_polyline_is_open_and_stroke_only():
    gen = build_appearance(
        "PolyLine", (0, 0, 100, 100), {"Vertices": [0, 0, 50, 50, 100, 0]}
    )
    assert gen is not None
    assert b"\nh\n" not in gen.content
    assert b"\nS\n" in gen.content


def test_build_ink_draws_each_path():
    gen = build_appearance(
        "Ink", (0, 0, 100, 100), {"InkList": [[0, 0, 50, 50], [10, 10, 60, 10, 60, 60]]}
    )
    assert gen is not None
    assert gen.content.count(b" m\n") == 2  # one moveto per path
    assert gen.content.count(b"\nS\n") == 2


def test_build_highlight_uses_multiply_blend():
    gen = build_appearance(
        "Highlight",
        (100, 100, 300, 140),
        {"QuadPoints": [100, 140, 300, 140, 100, 100, 300, 100], "C": [1, 1, 0]},
    )
    assert gen is not None
    assert gen.ext_gstates == {"GsMul": {"BM": "Multiply"}}
    assert b"/GsMul gs" in gen.content
    assert b"1 1 0 rg" in gen.content
    assert b"0 0 200 40 re" in gen.content  # quad bbox in local coords
    assert b"\nf\n" in gen.content


def test_build_underline_and_strikeout_draw_lines():
    quad = {"QuadPoints": [0, 20, 100, 20, 0, 0, 100, 0]}
    under = build_appearance("Underline", (0, 0, 100, 20), quad)
    strike = build_appearance("StrikeOut", (0, 0, 100, 20), quad)
    assert under is not None and strike is not None
    assert b"\nS\n" in under.content
    assert b"\nS\n" in strike.content
    # Strike-out sits higher than the underline.
    assert under.content != strike.content


# ---------------------------------------------------------------------------
# FreeText / Stamp / Caret builders
# ---------------------------------------------------------------------------


def test_build_freetext_draws_wrapped_text_and_border():
    gen = build_appearance(
        "FreeText",
        (0, 0, 60, 80),
        {"Contents": "the quick brown fox jumps", "DA": "/Helv 10 Tf 0 g"},
    )
    assert gen is not None
    assert b"/Helv 10 Tf" in gen.content
    assert b"0 g" in gen.content  # DA text colour
    assert gen.content.count(b" Tj") > 1  # wrapped across lines
    assert b" re" in gen.content and b"\nS\n" in gen.content  # border box
    # A font resource is requested so the caller can build /Resources /Font.
    assert "Helv" in gen.fonts


def test_build_freetext_fills_background_from_c():
    gen = build_appearance(
        "FreeText", (0, 0, 100, 40), {"Contents": "hi", "C": [1, 1, 0]}
    )
    assert gen is not None
    assert b"1 1 0 rg" in gen.content  # yellow background fill
    assert b"\nf\n" in gen.content


def test_build_freetext_uses_default_appearance_size_and_colour():
    gen = build_appearance(
        "FreeText", (0, 0, 200, 30), {"Contents": "hi", "DA": "/Helv 14 Tf 1 0 0 rg"}
    )
    assert gen is not None
    assert b"/Helv 14 Tf" in gen.content
    assert b"1 0 0 rg" in gen.content


def test_build_stamp_draws_named_caption_in_red_by_default():
    gen = build_appearance(
        "Stamp", (0, 0, 120, 40), {"Name": "NotApproved"}
    )
    assert gen is not None
    assert b"1 0 0 RG" in gen.content  # default rubber-stamp red border
    assert b"(NOT APPROVED) Tj" in gen.content  # camel-split, upper-cased
    assert "Helv" in gen.fonts


def test_build_stamp_honours_colour_and_contents_fallback():
    gen = build_appearance(
        "Stamp", (0, 0, 120, 40), {"C": [0, 0, 1], "Contents": "Reviewed"}
    )
    assert gen is not None
    assert b"0 0 1 RG" in gen.content and b"0 0 1 rg" in gen.content
    assert b"(Reviewed) Tj" in gen.content  # falls back to /Contents


def test_build_caret_draws_filled_triangle():
    gen = build_appearance("Caret", (0, 0, 20, 20), {})
    assert gen is not None
    assert b"0 g" in gen.content  # defaults to black
    assert gen.content.count(b" l\n") == 2  # two edges of the triangle
    assert b"\nh\n" in gen.content and b"\nf\n" in gen.content
    assert gen.fonts == {}  # marker shape needs no font


def test_build_caret_honours_rgb_colour():
    gen = build_appearance("Caret", (0, 0, 20, 20), {"C": [1, 0, 0]})
    assert gen is not None
    assert b"1 0 0 rg" in gen.content


def test_build_unsupported_subtype_returns_none():
    assert build_appearance("Text", (0, 0, 20, 20), {}) is None
    assert build_appearance("Popup", (0, 0, 20, 20), {}) is None
    assert build_appearance("Widget", (0, 0, 20, 20), {}) is None


def test_build_missing_geometry_returns_none():
    assert build_appearance("Line", (0, 0, 100, 100), {}) is None
    assert build_appearance("Ink", (0, 0, 100, 100), {}) is None
    assert build_appearance("Highlight", (0, 0, 100, 100), {}) is None


def test_build_degenerate_rect_returns_none():
    assert build_appearance("Square", (0, 0, 0, 100), {"C": [0]}) is None


# ---------------------------------------------------------------------------
# Public API integration
# ---------------------------------------------------------------------------


def test_annotation_generate_appearance_sets_ap():
    doc = _new_page_doc()
    ann = doc.pages[0].annotations.add(
        "Square", (100, 100, 200, 200), "", properties={"C": [1, 0, 0], "IC": [0, 1, 0]}
    )
    assert not ann.has_appearance
    assert ann.generate_appearance() is True
    assert ann.has_appearance
    assert b"1 0 0 RG" in ann.appearance_normal
    assert b"0 1 0 rg" in ann.appearance_normal


def test_highlight_generate_appearance_has_extgstate_resource():
    doc = _new_page_doc()
    ann = doc.pages[0].annotations.add(
        "Highlight",
        (100, 100, 300, 140),
        "",
        properties={"QuadPoints": [100, 140, 300, 140, 100, 100, 300, 100]},
    )
    assert ann.generate_appearance() is True
    assert b"/GsMul gs" in ann.appearance_normal


def test_freetext_generate_appearance_registers_font_resource():
    doc = _new_page_doc()
    ann = doc.pages[0].annotations.add(
        "FreeText",
        (100, 100, 300, 160),
        "hello from a free text box",
        properties={"DA": "/Helv 12 Tf 0 g"},
    )
    assert ann.generate_appearance() is True
    assert b"/Helv 12 Tf" in ann.appearance_normal
    # The generated form XObject carries the /Helv font in its /Resources.
    engine = doc._engine_pdf
    annot = engine.get_annotations(0)[0]
    assert annot["has_AP"] is True
    assert b"Tj" in annot["AP_N"]


def test_stamp_generate_appearance_end_to_end():
    doc = _new_page_doc()
    ann = doc.pages[0].annotations.add(
        "Stamp", (100, 100, 260, 150), "", properties={"Name": AnnotationName("Approved")}
    )
    assert ann.generate_appearance() is True
    assert b"(APPROVED) Tj" in ann.appearance_normal


def test_caret_generate_appearance_end_to_end():
    doc = _new_page_doc()
    ann = doc.pages[0].annotations.add("Caret", (100, 100, 120, 120), "")
    assert ann.generate_appearance() is True
    assert b"\nf\n" in ann.appearance_normal


def test_generate_appearance_unsupported_subtype_returns_false():
    doc = _new_page_doc()
    ann = doc.pages[0].annotations.add("Text", (0, 0, 20, 20), "note")
    assert ann.generate_appearance() is False
    assert not ann.has_appearance


def test_generate_appearance_keeps_existing_unless_forced():
    doc = _new_page_doc()
    ann = doc.pages[0].annotations.add(
        "Square", (0, 0, 50, 50), "x", appearance_normal=b"0 0 50 50 re f\n"
    )
    assert ann.has_appearance
    original = ann.appearance_normal
    # Idempotent: an existing appearance is preserved.
    assert ann.generate_appearance() is True
    assert ann.appearance_normal == original
    # force=True regenerates from the annotation geometry.
    assert ann.generate_appearance(force=True) is True
    assert ann.appearance_normal != original


def test_page_generate_appearances_counts_created():
    doc = _new_page_doc()
    page = doc.pages[0]
    page.annotations.add("Square", (0, 0, 50, 50), "")
    page.annotations.add("Circle", (60, 0, 110, 50), "")
    page.annotations.add("Text", (0, 60, 20, 80), "")  # unsupported -> skipped
    assert page.annotations.generate_appearances() == 2


def test_document_generate_appearances_across_pages():
    doc = Document()
    doc.pages.add()
    doc.pages.add()
    doc.pages[0].annotations.add("Square", (0, 0, 50, 50), "")
    doc.pages[1].annotations.add("Line", (0, 0, 50, 50), "", properties={"L": [0, 0, 50, 50]})
    assert doc.generate_appearances() == 2
    # Second call is a no-op (appearances already present).
    assert doc.generate_appearances() == 0


def test_generated_appearance_survives_save_load():
    doc = _new_page_doc()
    ann = doc.pages[0].annotations.add(
        "Circle", (100, 100, 200, 160), "", properties={"C": [0, 0, 1]}
    )
    ann.generate_appearance()
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    reopened = Document()
    reopened.load_from(buf)
    assert reopened.pages[0].annotations[0].has_appearance


# ---------------------------------------------------------------------------
# Flatten integration (generation + correct BBox -> Rect matrix)
# ---------------------------------------------------------------------------


def test_flatten_generates_and_inlines_missing_appearance():
    doc = _new_page_doc()
    doc.pages[0].annotations.add("Square", (100, 100, 200, 200), "", properties={"C": [0, 0, 0]})
    before = doc._engine_pdf.page_contents[0]
    doc.flatten()
    after = doc._engine_pdf.page_contents[0]
    assert len(after) > len(before)
    assert b"Do" in after
    assert len(doc._engine_pdf.get_annotations(0)) == 0


def test_flatten_matrix_maps_bbox_to_rect_without_double_scaling():
    doc = _new_page_doc()
    # Manual appearance authored in annot-local coords; BBox is [0 0 100 100].
    doc.pages[0].annotations.add(
        "Square", (100, 100, 200, 200), "", appearance_normal=b"0.5 g\n0 0 100 100 re f\n"
    )
    doc.flatten()
    content = doc._engine_pdf.page_contents[0]
    # BBox [0 0 100 100] -> Rect [100 100 200 200] is a pure translation, not the
    # old "[w 0 0 h x y]" double-scale.
    assert b"1 0 0 1 100 100 cm" in content
    assert b"100 0 0 100 100 100 cm" not in content

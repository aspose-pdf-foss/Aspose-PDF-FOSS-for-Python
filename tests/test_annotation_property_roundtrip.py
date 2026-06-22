"""Round-trip of type-specific annotation properties for all standard subtypes.

Closes the audit gap: previously only ``Text``/``Link``/``Highlight``/``Square``/
``Circle`` were verified to survive save/load. These tests assert that the
*defining* entries of the full set of standard annotation subtypes (colour, line
endpoints, quadpoints, vertices, ink lists, icon names, ...) persist through a
round trip, and that PDF names stay distinguishable from strings via
``annotations.Name``.
"""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document
from aspose_pdf.annotations import AnnotationType, MarkupAnnotation, Name


def _roundtrip(doc: Document) -> Document:
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    doc2 = Document()
    doc2.load_from(buf)
    return doc2


def _new_page():
    doc = Document()
    doc.pages.add()
    return doc, doc.pages[0]


def test_line_properties_roundtrip():
    doc, page = _new_page()
    page.annotations.add(
        "Line",
        (10, 10, 200, 60),
        "measure",
        properties={
            "L": [10, 20, 200, 50],
            "LE": [Name("OpenArrow"), Name("None")],
            "C": [1, 0, 0],
        },
    )
    a = _roundtrip(doc).pages[0].annotations[0]
    assert a.subtype == "Line"
    assert a.get_property("L") == [10, 20, 200, 50]
    assert a.get_property("LE") == ["OpenArrow", "None"]
    assert a.color == (1.0, 0.0, 0.0)


def test_text_markup_quadpoints_roundtrip():
    doc, page = _new_page()
    quad = [10, 90, 110, 90, 10, 60, 110, 60]
    for st in ("Highlight", "Underline", "StrikeOut", "Squiggly"):
        page.annotations.add(st, (10, 60, 110, 90), "", properties={"QuadPoints": quad})
    page2 = _roundtrip(doc).pages[0]
    assert [a.subtype for a in page2.annotations] == [
        "Highlight",
        "Underline",
        "StrikeOut",
        "Squiggly",
    ]
    for a in page2.annotations:
        assert a.get_property("QuadPoints") == quad


def test_polygon_polyline_vertices_roundtrip():
    doc, page = _new_page()
    verts = [10, 10, 100, 10, 55, 90]
    page.annotations.add(
        "Polygon", (10, 10, 100, 90), "", properties={"Vertices": verts, "IC": [0, 1, 0]}
    )
    page.annotations.add(
        "PolyLine", (10, 10, 100, 90), "", properties={"Vertices": verts}
    )
    page2 = _roundtrip(doc).pages[0]
    assert page2.annotations[0].get_property("Vertices") == verts
    assert page2.annotations[0].get_property("IC") == [0, 1, 0]
    assert page2.annotations[1].subtype == "PolyLine"
    assert page2.annotations[1].get_property("Vertices") == verts


def test_ink_nested_list_roundtrip():
    doc, page = _new_page()
    ink = [[10, 10, 20, 20, 30, 10], [40, 40, 50, 50]]
    page.annotations.add("Ink", (10, 10, 60, 60), "", properties={"InkList": ink})
    a = _roundtrip(doc).pages[0].annotations[0]
    assert a.subtype == "Ink"
    assert a.get_property("InkList") == ink


def test_name_marker_roundtrip_and_equality():
    doc, page = _new_page()
    page.annotations.add(
        "Text",
        (10, 10, 30, 30),
        "note",
        properties={"Name": Name("Comment"), "Open": True},
    )
    a = _roundtrip(doc).pages[0].annotations[0]
    name = a.get_property("Name")
    assert name == "Comment"  # equality with a plain str still holds
    assert isinstance(name, Name)  # but it is still marked as a PDF name
    assert a.get_property("Open") is True


def test_freetext_da_quadding_roundtrip():
    doc, page = _new_page()
    page.annotations.add(
        "FreeText",
        (10, 10, 200, 40),
        "hi",
        properties={"DA": "0 0 1 rg /Helv 12 Tf", "Q": 1},
    )
    a = _roundtrip(doc).pages[0].annotations[0]
    assert a.get_property("DA") == "0 0 1 rg /Helv 12 Tf"
    assert a.get_property("Q") == 1


def test_stamp_name_roundtrip():
    doc, page = _new_page()
    page.annotations.add(
        "Stamp", (10, 10, 110, 60), "", properties={"Name": Name("Approved")}
    )
    a = _roundtrip(doc).pages[0].annotations[0]
    assert a.subtype == "Stamp"
    assert a.get_property("Name") == "Approved"


@pytest.mark.parametrize("subtype", [t.value for t in AnnotationType])
def test_every_known_subtype_roundtrips(subtype):
    doc, page = _new_page()
    page.annotations.add(
        subtype, (5, 5, 55, 55), f"c-{subtype}", properties={"NM": f"id-{subtype}"}
    )
    a = _roundtrip(doc).pages[0].annotations[0]
    assert a.subtype == subtype
    assert a.contents == f"c-{subtype}"
    assert a.get_property("NM") == f"id-{subtype}"


def test_set_property_update_and_delete():
    doc, page = _new_page()
    ann = page.annotations.add("Square", (0, 0, 50, 50), "x", properties={"C": [1, 0, 0]})
    ann.set_property("IC", [0, 0, 1])
    assert page.annotations[0].get_property("IC") == [0, 0, 1]
    ann.set_property("C", None)  # delete
    assert "C" not in page.annotations[0].properties

    a = _roundtrip(doc).pages[0].annotations[0]
    assert a.get_property("IC") == [0, 0, 1]
    assert "C" not in a.properties


def test_color_convenience_setter():
    doc, page = _new_page()
    ann = page.annotations.add("Circle", (0, 0, 50, 50), "")
    ann.color = (0.2, 0.4, 0.6)
    a = _roundtrip(doc).pages[0].annotations[0]
    assert a.color == pytest.approx((0.2, 0.4, 0.6))


def test_markup_subclass_wrapping():
    doc, page = _new_page()
    page.annotations.add("Highlight", (0, 0, 10, 10), "")
    assert isinstance(page.annotations[0], MarkupAnnotation)


def test_enum_accepted_as_subtype():
    doc, page = _new_page()
    page.annotations.add(
        AnnotationType.POLYGON,
        (0, 0, 10, 10),
        "",
        properties={"Vertices": [0, 0, 10, 0, 5, 10]},
    )
    a = _roundtrip(doc).pages[0].annotations[0]
    assert a.subtype == "Polygon"
    assert a.get_property("Vertices") == [0, 0, 10, 0, 5, 10]

"""Portable XFDF review exports preserve geometry, threads and XML text safely."""

from __future__ import annotations

import io
import json
import xml.etree.ElementTree as ET
from itertools import repeat

import pytest

from aspose_pdf import (
    AnnotationFlags,
    AnnotationType,
    Document,
    PdfLoadLimits,
    PdfResourceLimitException,
)
from aspose_pdf.engine.cos import PdfArray, PdfName
from aspose_pdf.exceptions import AsposePdfException, PdfValidationException

_NS = "{http://ns.adobe.com/xfdf/}"


def _annots(data):
    root = ET.fromstring(data)
    assert root.tag == _NS + "xfdf"
    assert root.attrib["{http://www.w3.org/XML/1998/namespace}space"] == "preserve"
    return root.find(_NS + "annots")


def _saved(document):
    with io.BytesIO() as output:
        document.save(output)
        return output.getvalue()


@pytest.fixture
def document():
    with Document() as document:
        page = document.pages.add(size=(200, 200))
        note = page.annotations.add_text(
            (10, 20, 30, 40),
            'Привет 🌍 <&> "reader"\r\n\t',
            title='Alice <&> "Иванова"',
        )
        note.update_properties(
            NM="comment-1",
            C=[1, 0, 0],
            CA=0.5,
            M="D:20260203040506Z",
            CreationDate="D:20260102030405+02'00'",
            Subj="Review",
            F=int(AnnotationFlags.PRINT | AnnotationFlags.HIDDEN),
            State="Accepted",
            StateModel="Review",
        )
        note.reply("Reply", title="Bob").reply("Nested")
        note.add_popup((40, 20, 180, 100), is_open=True)
        document.pages.add(size=(200, 200)).annotations.add_highlight(
            (50, 100, 150, 120), "Highlight"
        )
        yield document


@pytest.mark.parametrize("source", ["live", "bytes", "file", "streaming"])
def test_namespace_metadata_unicode_and_threads_survive_pdf_round_trips(
    document, source, tmp_path
):
    raw = _saved(document)
    if source == "live":
        reopened = document
    elif source == "bytes":
        reopened = Document(raw)
    else:
        path = tmp_path / "source.pdf"
        path.write_bytes(raw)
        reopened = (Document.open_streaming if source == "streaming" else Document)(
            path
        )
    try:
        data = reopened.export_annotations_xfdf()
        annotations = _annots(data)
        assert data.startswith(b'<?xml version="1.0" encoding="UTF-8"?>')
        assert data.endswith(b"\n") and not data.startswith(b"\xef\xbb\xbf")
        assert "Привет 🌍".encode() in data and b"&lt;&amp;&gt;" in data
        assert [node.tag for node in annotations] == [_NS + "text"] * 3 + [
            _NS + "highlight"
        ]
        note, reply, nested, highlight = annotations
        assert note.attrib == dict(
            page="0",
            rect="10,20,30,40",
            name="comment-1",
            title='Alice <&> "Иванова"',
            date="D:20260203040506Z",
            creationdate="D:20260102030405+02'00'",
            subject="Review",
            color="#FF0000",
            opacity="0.5",
            flags="hidden,print",
            state="Accepted",
            statemodel="Review",
        )
        assert note.find(_NS + "contents").text == 'Привет 🌍 <&> "reader"\r\n\t'
        popup = note.find(_NS + "popup")
        assert popup.attrib["rect"] == "40,20,180,100" and popup.attrib["open"] == "yes"
        assert (
            reply.attrib["inreplyto"] == note.attrib["name"]
            and reply.attrib["replyType"] == "Reply"
        )
        assert nested.attrib["inreplyto"] == reply.attrib["name"]
        assert (
            highlight.attrib["page"] == "1"
            and len(highlight.attrib["coords"].split(",")) == 8
        )
        assert reopened.export_annotations_xfdf() == data
        assert len(_annots(reopened.pages[0].annotations.export_xfdf())) == 3
    finally:
        if reopened is not document:
            reopened.dispose()


def test_page_order_repeats_type_filters_and_orphan_popup_behavior(document):
    nodes = _annots(
        document.export_annotations_xfdf(pages=(value for value in (1, 0, 1)))
    )
    assert [node.attrib["page"] for node in nodes] == ["1", "0", "0", "0"]
    nodes = _annots(document.export_annotations_xfdf(subtypes=[AnnotationType.TEXT]))
    assert len(nodes) == 3 and nodes[0].find(_NS + "popup") is None
    assert len(_annots(document.export_annotations_xfdf(subtypes="/Highlight"))) == 1
    for options in (
        {"pages": []},
        {"subtypes": []},
        {"subtypes": "Popup"},
        {"subtypes": "VendorType"},
    ):
        assert len(_annots(document.export_annotations_xfdf(**options))) == 0


def test_missing_and_duplicate_names_are_unique_without_changing_source():
    with Document() as document:
        annotations = document.pages.add().annotations
        first = annotations.add_text(
            (0, 0, 10, 10), "First", properties={"NM": "duplicate"}
        )
        second = annotations.add_text(
            (20, 0, 30, 10), "Second", properties={"NM": "duplicate"}
        )
        second.reply("Second's reply")
        annotations.add_text((40, 0, 50, 10), "Missing")
        before = annotations.to_list()
        nodes = _annots(document.export_annotations_xfdf())
        names = [node.attrib["name"] for node in nodes]
        assert names[0] == "duplicate" and len(set(names)) == 4
        assert nodes[2].attrib["inreplyto"] == names[1]
        assert (
            annotations.to_list() == before and first.get_property("NM") == "duplicate"
        )


def test_reply_targets_outside_selected_pages_are_omitted():
    with Document() as document:
        document.pages.add()
        document.pages.add()
        rows = [
            dict(
                id=name,
                page_index=index,
                annotation_index=0,
                subtype="Text",
                rect=[0, 0, 10, 10],
                contents=name,
                author="",
                properties={"RT": "Reply"} if index else {},
                links=dict(in_reply_to="a" if index else None, popup=None, parent=None),
            )
            for index, name in enumerate(("a", "b"))
        ]
        document.import_annotations_json(
            json.dumps(dict(schema_version=1, annotations=rows)).encode()
        )
        all_nodes = _annots(document.export_annotations_xfdf())
        assert all_nodes[1].attrib["inreplyto"] == all_nodes[0].attrib["name"]
        reply = _annots(document.export_annotations_xfdf(pages=[1]))[0]
        assert "inreplyto" not in reply.attrib and "replyType" not in reply.attrib


def test_line_polygon_polyline_ink_and_free_text_geometry():
    with Document() as document:
        annotations = document.pages.add().annotations
        annotations.add_line(
            (10, 20),
            (100, 80),
            line_endings=["OpenArrow", "ClosedArrow"],
            border_width=2,
        )
        annotations.add_polygon([(10, 10), (20, 10), (15, 20)])
        annotations.add_polyline([(10, 30), (20, 40)])
        annotations.add_ink([[(10, 10), (20, 30)], [(40, 50), (60, 70)]])
        annotations.add_free_text(
            (10, 50, 80, 100),
            "Plain",
            properties={
                "DA": "/Helv 10 Tf 0 g",
                "DS": "font: 10pt Helvetica",
                "Q": 2,
                "Rotate": 90,
                "RC": "<body>Rich & unparsed</body>",
            },
        )
        line, polygon, polyline, ink, text = _annots(document.export_annotations_xfdf())
        assert line.attrib["start"] == "10,20" and line.attrib["end"] == "100,80"
        assert (
            line.attrib["head"] == "OpenArrow" and line.attrib["tail"] == "ClosedArrow"
        )
        assert line.attrib["width"] == "2" and line.attrib["style"] == "solid"
        assert polygon.find(_NS + "vertices").text == "10,10;20,10;15,20"
        assert polyline.find(_NS + "vertices").text == "10,30;20,40"
        assert [stroke.text for stroke in ink.find(_NS + "inklist")] == [
            "10,10;20,30",
            "40,50;60,70",
        ]
        assert (
            text.attrib["rotation"] == "90" and text.attrib["justification"] == "right"
        )
        assert text.find(_NS + "defaultappearance").text == "/Helv 10 Tf 0 g"
        assert text.find(_NS + "defaultstyle").text == "font: 10pt Helvetica"
        assert text.find(_NS + "contents-richtext") is None


@pytest.mark.parametrize(
    "subtype", ["Text", "FreeText", "Square", "Circle", "Stamp", "Caret", "Redact"]
)
def test_supported_rectangle_types_export_as_standard_elements(subtype):
    with Document() as document:
        document.pages.add().annotations.add(subtype, (1, 2, 30, 40), "Comment")
        (node,) = _annots(document.export_annotations_xfdf())
        assert node.tag == _NS + subtype.lower() and node.attrib["rect"] == "1,2,30,40"


@pytest.mark.parametrize("subtype", ["Highlight", "Underline", "Squiggly", "StrikeOut"])
def test_all_text_markup_types_keep_quadpoint_order(subtype):
    with Document() as document:
        quads = [1, 8, 9, 8, 1, 2, 9, 2, 20, 8, 30, 8, 20, 2, 30, 2]
        document.pages.add().annotations.add(
            subtype, (1, 2, 30, 8), "Markup", properties={"QuadPoints": quads}
        )
        (node,) = _annots(document.export_annotations_xfdf())
        assert node.attrib["coords"] == ",".join(map(str, quads))


@pytest.mark.parametrize(
    "components,expected",
    [([], None), ([0.5], "#808080"), ([1, 0, 0], "#FF0000"), ([0, 1, 1, 0], "#FF0000")],
)
def test_gray_rgb_and_cmyk_colors_map_to_xfdf_rgb(components, expected):
    with Document() as document:
        document.pages.add().annotations.add_text(
            (1, 2, 3, 4), properties={"C": components, "IC": components}
        )
        (node,) = _annots(document.export_annotations_xfdf())
        assert node.attrib.get("color") == node.attrib.get("interior-color") == expected


@pytest.mark.parametrize(
    "subtype",
    ["Widget", "Link", "FileAttachment", "Sound", "Movie", "Screen", "VendorType"],
)
def test_non_review_types_are_skipped(subtype):
    with Document() as document:
        document.pages.add().annotations.add(subtype, (1, 2, 3, 4), "Private")
        assert len(_annots(document.export_annotations_xfdf())) == 0
        assert len(_annots(document.export_annotations_xfdf(subtypes=subtype))) == 0


@pytest.mark.parametrize(
    "options",
    [
        {"pages": 0},
        {"pages": "0"},
        {"pages": [True]},
        {"pages": [-1]},
        {"pages": [0, 9]},
        {"subtypes": True},
        {"subtypes": b"Text"},
        {"subtypes": ["Text", None]},
        {"subtypes": ""},
        {"overwrite": 1},
    ],
)
def test_invalid_options_fail_before_reading_metadata_or_writing(
    document, monkeypatch, options
):
    monkeypatch.setattr(
        document._engine_pdf, "_get_page_dict", lambda *a: pytest.fail("validate first")
    )
    output = io.BytesIO(b"Keep")
    with pytest.raises(PdfValidationException):
        document.export_annotations_xfdf(output, **options)
    assert output.getvalue() == b"Keep"


@pytest.mark.parametrize(
    "property_name,value",
    [
        ("CA", 2),
        ("C", [1, 0]),
        ("C", [1.1]),
        ("F", -1),
        ("F", 1024),
        ("F", True),
        ("Open", 1),
        ("NM", 42),
        ("BS", "bad"),
        ("BS", {"S": ["D"]}),
        ("BS", {"W": -1}),
        ("BS", {"D": [0, 0]}),
        ("BS", {"D": [-1]}),
    ],
)
def test_malformed_selected_properties_leave_outputs_untouched(
    tmp_path, property_name, value
):
    with Document() as document:
        annotations = document.pages.add().annotations
        annotations.add_text((1, 2, 3, 4), "Valid")
        annotations.add_text((5, 6, 7, 8), "Invalid", properties={property_name: value})
        path = tmp_path / "comments.xfdf"
        path.write_bytes(b"Keep")
        with pytest.raises(PdfValidationException):
            document.export_annotations_xfdf(path)
        assert path.read_bytes() == b"Keep"


@pytest.mark.parametrize("value", ["bad\x00text", "bad\x01", "bad\ufffe", "bad\uffff"])
@pytest.mark.parametrize("field", ["contents", "author", "name"])
def test_xml_forbidden_characters_are_rejected(document, value, field):
    note = document.pages[0].annotations[0]
    if field == "name":
        note.set_property("NM", value)
    else:
        setattr(note, field, value)
    output = io.BytesIO(b"Keep")
    with pytest.raises((PdfValidationException, UnicodeError)):
        document.export_annotations_xfdf(output)
    assert output.getvalue() == b"Keep"


def test_empty_outputs_and_disposed_collection():
    with Document() as document:
        assert len(_annots(document.export_annotations_xfdf())) == 0
        page = document.pages.add()
        assert page.annotations.export_xfdf() == document.export_annotations_xfdf()
        collection = page.annotations
    with pytest.raises(AsposePdfException, match="disposed"):
        document.export_annotations_xfdf()
    with pytest.raises(AsposePdfException, match="disposed"):
        collection.export_xfdf()


def test_files_streams_overwrite_short_writes_and_stalled_streams(document, tmp_path):
    path = tmp_path / "new" / "comments.xfdf"
    expected = document.export_annotations_xfdf()
    assert document.export_annotations_xfdf(path) == expected == path.read_bytes()
    with pytest.raises(FileExistsError):
        document.export_annotations_xfdf(path, overwrite=False)

    class Short(io.BytesIO):
        def write(self, data):
            return super().write(data[:3])

    with Short(b"prefix") as output:
        output.seek(6)
        assert document.export_annotations_xfdf(output) == expected
        assert output.getvalue() == b"prefix" + expected and not output.closed

    class Stalled:
        def write(self, data):
            return 0

    with pytest.raises(AsposePdfException):
        document.export_annotations_xfdf(Stalled())


@pytest.mark.parametrize(
    "limits,match",
    [
        (PdfLoadLimits(max_input_bytes=100), "max_input_bytes"),
        (PdfLoadLimits(max_object_bytes=10), "max_object_bytes"),
        (PdfLoadLimits(max_codec_work_bytes=100), "max_codec_work_bytes"),
        (PdfLoadLimits(max_total_decoded_bytes=100), "max_total_decoded_bytes"),
        (PdfLoadLimits(max_container_items=10), "max_container_items"),
        (PdfLoadLimits(max_nesting_depth=1), "max_nesting_depth"),
    ],
)
def test_output_and_metadata_resource_limits_leave_outputs_untouched(
    document, limits, match
):
    document._load_limits = limits
    output = io.BytesIO(b"Keep")
    with pytest.raises(PdfResourceLimitException, match=match):
        document.export_annotations_xfdf(output)
    assert output.getvalue() == b"Keep"


def test_unbounded_selection_is_stopped_by_resource_policy():
    with Document(limits=PdfLoadLimits(max_container_items=2)) as document:
        document.pages.add()
        with pytest.raises(PdfResourceLimitException, match="max_container_items"):
            document.export_annotations_xfdf(pages=repeat(0))


@pytest.mark.parametrize(
    "subtype,properties",
    [
        ("Line", {}),
        ("Line", {"L": [0, 1, 2]}),
        ("Line", {"L": [0, 1, 2, 3], "LE": ["Invalid", "None"]}),
        ("Polygon", {"Vertices": [0, 1, 2, 3]}),
        ("PolyLine", {"Vertices": [0, 1, 2]}),
        ("Highlight", {"QuadPoints": [0, 1, 2, 3]}),
        ("Ink", {"InkList": []}),
        ("Ink", {"InkList": [[0, 1, 2]]}),
        ("FreeText", {"Q": 1.5}),
        ("FreeText", {"Rotate": True}),
        ("Square", {"RD": [0, 1, 2]}),
    ],
)
def test_invalid_type_geometry_and_styles_fail_atomically(subtype, properties):
    with Document() as document:
        annotations = document.pages.add().annotations
        annotations.add_text((0, 0, 10, 10), "Valid")
        annotations.add(subtype, (10, 10, 20, 20), "Invalid", properties=properties)
        output = io.BytesIO(b"Keep")
        with pytest.raises(PdfValidationException):
            document.export_annotations_xfdf(output)
        assert output.getvalue() == b"Keep"


def test_existing_names_are_reserved_before_fallback_names_are_generated():
    with Document() as document:
        annotations = document.pages.add().annotations
        first = annotations.add_text((0, 0, 10, 10), "Missing")
        identity = annotations.to_list()[0]["id"]
        expected = "xfdf-" + identity
        annotations.add_text((20, 0, 30, 10), "Reserved", properties={"NM": expected})
        first.reply("Reply")
        first_node, reserved, reply = _annots(document.export_annotations_xfdf())
        assert reserved.attrib["name"] == expected
        assert first_node.attrib["name"] != expected
        assert reply.attrib["inreplyto"] == first_node.attrib["name"]


def test_xml_injection_and_attribute_whitespace_remain_literal_text():
    value = '  Review\r\n\t<&" </contents><script>attack</script>  '
    with Document() as document:
        document.pages.add().annotations.add_text(
            (0, 0, 10, 10), value, title=value, properties={"NM": value}
        )
        nodes = _annots(document.export_annotations_xfdf())
        assert len(nodes) == 1
        assert nodes[0].attrib["name"] == nodes[0].attrib["title"] == value
        assert nodes[0].find(_NS + "contents").text == value
        assert nodes[0].find(_NS + "script") is None


def test_long_utf8_text_is_escaped_in_chunks_and_raw_byte_limit_is_checked():
    text = "Привет 🌍\r<&>" * 1000
    with Document() as document:
        document.pages.add().annotations.add_text((0, 0, 10, 10), text)
        assert (
            _annots(document.export_annotations_xfdf())[0].find(_NS + "contents").text
            == text
        )
        document._load_limits = PdfLoadLimits(max_object_bytes=len(text))
        with pytest.raises(PdfResourceLimitException, match="max_object_bytes"):
            document.export_annotations_xfdf()


@pytest.mark.parametrize(
    "kind",
    [
        "self_reply",
        "reply_cycle",
        "reply_to_popup",
        "cross_page_popup",
        "conflicting_popup",
        "repeated_object",
    ],
)
def test_invalid_selected_relationships_are_rejected_atomically(document, kind):
    pdf = document._engine_pdf
    page = pdf._get_page_dict(0)
    array = pdf._resolve(page.mapping[PdfName("Annots")])
    first, _reply, _nested, popup = [pdf._resolve(ref) for ref in array.items]
    if kind == "self_reply":
        first.mapping[PdfName("IRT")] = array.items[0]
    elif kind == "reply_cycle":
        first.mapping[PdfName("IRT")] = array.items[1]
    elif kind == "reply_to_popup":
        first.mapping[PdfName("IRT")] = array.items[3]
    elif kind == "cross_page_popup":
        target = pdf._get_page_dict(1)
        pdf._resolve(target.mapping[PdfName("Annots")]).items.append(array.items.pop())
    elif kind == "conflicting_popup":
        popup.mapping[PdfName("Parent")] = array.items[1]
    else:
        array.items.append(array.items[0])
    output = io.BytesIO(b"Keep")
    with pytest.raises(PdfValidationException):
        document.export_annotations_xfdf(output)
    assert output.getvalue() == b"Keep"


@pytest.mark.parametrize("kind", ["bytes", "file", "streaming"])
@pytest.mark.parametrize("encrypted", [False, True])
def test_sources_remain_unchanged_without_decoding_content_or_appearance_streams(
    tmp_path, monkeypatch, kind, encrypted
):
    path = tmp_path / "source.pdf"
    with Document() as source:
        note = source.pages.add().annotations.add_text(
            (1, 2, 30, 40), "Привет", title="Alice"
        )
        note.appearance_normal = b"q\nQ\n" * 1000
        note.set_property("RC", "<body>Not parsed & not exported</body>")
        note.reply("Reply")
        source.pages.add().annotations.add_text((10, 20, 30, 40), "Unselected")
        source.info["Title"] = "Original"
        source.add_attachment("private.bin", b"Secret")
        if encrypted:
            source.encrypt("user", "owner")
        source.save(path)
    raw = path.read_bytes()
    opener = Document.open_streaming if kind == "streaming" else Document
    with opener(
        raw if kind == "bytes" else path, password="user" if encrypted else None
    ) as source:
        pdf = source._engine_pdf
        protection = source.is_encrypted
        before = source.pages[0].annotations.to_list()

        def no_content(*args, **kwargs):
            pytest.fail("XFDF metadata export must not decode page or asset streams")

        monkeypatch.setattr(pdf, "_decode_cos_stream", no_content)
        monkeypatch.setattr(pdf, "get_page_content", no_content)
        data = source.export_annotations_xfdf(pages=[0])
        assert len(_annots(data)) == 2 and "Привет".encode() in data
        assert (
            b"Not parsed" not in data
            and b"private.bin" not in data
            and b"Secret" not in data
        )
        assert source.pages[0].annotations.to_list() == before
        assert source.is_encrypted is protection and source.info["Title"] == "Original"
        if kind == "streaming":
            assert pdf.page_contents == [] and pdf._extracted_text is None
    assert path.read_bytes() == raw


def test_hidden_comments_keep_unrotated_coordinates_on_cropped_pages():
    with Document() as document:
        page = document.pages.add()
        page.annotations.add_text((-10, 20, 30, 40), "Hidden", properties={"F": 2})
        page.rotation = 90
        page.crop_box = (100, 100, 300, 300)
        (node,) = _annots(document.export_annotations_xfdf())
        assert (
            node.attrib["rect"] == "-10,20,30,40" and node.attrib["flags"] == "hidden"
        )


def test_unselected_annotations_and_unmapped_metadata_are_not_read(
    document, monkeypatch
):
    pdf = document._engine_pdf
    first = pdf._get_page_dict(0)
    ref = pdf._resolve(first.mapping[PdfName("Annots")]).items[0]
    pdf._resolve(ref).mapping[PdfName("RC")] = PdfArray([ref])
    original = pdf._get_page_dict

    def page(index):
        assert index == 0
        return original(index)

    monkeypatch.setattr(pdf, "_get_page_dict", page)
    assert len(_annots(document.export_annotations_xfdf(pages=[0]))) == 3


@pytest.mark.parametrize(
    "source,expected",
    [
        ("S", "solid"),
        ("D", "dash"),
        ("B", "bevelled"),
        ("I", "inset"),
        ("U", "underline"),
    ],
)
def test_border_styles_use_xfdf_names(source, expected):
    with Document() as document:
        document.pages.add().annotations.add_square(
            (0, 0, 10, 10), properties={"BS": {"S": source, "W": 2, "D": [2, 1]}}
        )
        (node,) = _annots(document.export_annotations_xfdf())
        assert node.attrib["style"] == expected
        assert node.attrib["width"] == "2" and node.attrib["dashes"] == "2,1"

"""Portable review comments restore geometry and relationships atomically."""

from __future__ import annotations

import io
import json
from dataclasses import replace

import pytest

from aspose_pdf import (
    AnnotationFlags,
    Document,
    PdfLoadLimits,
    PdfResourceLimitException,
)
from aspose_pdf.annotations import Name
from aspose_pdf.exceptions import AsposePdfException, PdfValidationException

_NS = "http://ns.adobe.com/xfdf/"


def _xfdf(annotations="", *, metadata="", namespace=_NS):
    declaration = f' xmlns="{namespace}"' if namespace else ""
    return (
        f"<xfdf{declaration}>{metadata}<annots>{annotations}</annots></xfdf>".encode()
    )


def _note(**attrs):
    attributes = {"page": "0", "rect": "1,2,30,40", **attrs}
    return (
        "<text "
        + " ".join(f'{key}="{value}"' for key, value in attributes.items())
        + "><contents>Review</contents></text>"
    )


def _saved(document):
    with io.BytesIO() as output:
        document.save(output)
        return output.getvalue()


@pytest.fixture
def document():
    with Document() as document:
        document.pages.add(size=(200, 200)).add_text("Original text", 10, 100)
        document.pages[0].annotations.add_text((1, 2, 10, 20), "Existing")
        document.pages.add(size=(200, 200)).add_text("Second page", 10, 100)
        yield document


@pytest.mark.parametrize(
    "kind", ["bytes", "bytearray", "path", "str", "stream", "short"]
)
def test_input_forms_append_without_replacing_existing_annotations(
    document, tmp_path, kind
):
    data = _xfdf(_note(name="note", title="Reviewer", color="#FF0000", opacity="0.5"))
    path = tmp_path / "review.xfdf"
    path.write_bytes(data)

    class ShortReader(io.BytesIO):
        def read(self, size=-1):
            return super().read(min(size, 7) if size >= 0 else size)

    with io.BytesIO(b"skip" + data) as stream, ShortReader(data) as short:
        stream.seek(4)
        source = {
            "bytes": data,
            "bytearray": bytearray(data),
            "path": path,
            "str": str(path),
            "stream": stream,
            "short": short,
        }[kind]
        assert document.import_annotations_xfdf(source) == 1
        assert not stream.closed and not short.closed
        assert document.import_annotations_xfdf(data) == 1
    destination = tmp_path / "reviewed.pdf"
    document.save(destination)
    for source in (destination, _saved(document)):
        with Document(source) as reopened:
            notes = reopened.pages[0].annotations
            assert [note.contents for note in notes] == ["Existing", "Review", "Review"]
            assert notes[1].rect == (1, 2, 30, 40)
            assert notes[1].title == "Reviewer"
            assert notes[1].color == (1, 0, 0) and notes[1].opacity == 0.5
            assert notes[1].get_property("NM") == "note"
            assert not notes[1].has_appearance


def test_export_import_export_preserves_supported_review_data_and_threads(tmp_path):
    with Document() as source:
        annotations = source.pages.add().annotations
        note = annotations.add_text(
            (10, 20, 30, 40), 'Привет 🌍 <&> "review"\r\n\t', title="Alice Иванова"
        )
        note.update_properties(
            NM="note",
            Name=Name("Comment"),
            Open=True,
            C=[0.5],
            CA=0.5,
            M="D:20260102030405Z",
            CreationDate="D:20260101000000+02'00'",
            Subj="Review",
            State="Accepted",
            StateModel="Review",
            F=int(AnnotationFlags.PRINT | AnnotationFlags.LOCKED),
            BS={"W": 2, "S": Name("D"), "D": [3, 1]},
            RD=[1, 2, 3, 4],
        )
        note.reply("Reply", title="Bob").set_property("NM", "reply")
        note.replies[0].reply("Nested").set_property("NM", "nested")
        note.add_popup((40, 20, 150, 100), is_open=True).set_property("NM", "popup")
        annotations.add_free_text(
            (10, 50, 100, 80),
            "Plain text",
            properties={
                "DA": "/Helv 10 Tf 0 g",
                "DS": "font: 10pt Helvetica",
                "Q": 2,
                "Rotate": 90,
            },
        )
        annotations.add_line(
            (10, 100),
            (100, 120),
            line_endings=["OpenArrow", "ClosedArrow"],
            border_width=2,
        )
        annotations.add_polygon([(10, 10), (20, 10), (15, 20)])
        annotations.add_polyline([(10, 30), (20, 40)])
        annotations.add_ink([[(10, 10), (20, 30)], [(40, 50), (60, 70)]])
        for subtype in ("Square", "Circle", "Stamp", "Caret", "Redact"):
            annotations.add(
                subtype,
                (100, 100, 150, 150),
                subtype,
                properties={"Name": Name("Approved"), "Rotate": 45}
                if subtype == "Stamp"
                else None,
            )
        for subtype in ("Highlight", "Underline", "Squiggly", "StrikeOut"):
            annotations.add(
                subtype,
                (10, 140, 100, 160),
                subtype,
                properties={"QuadPoints": [10, 160, 100, 160, 10, 140, 100, 140]},
            )
        data = source.export_annotations_xfdf()
        expected_count = len(annotations)
    with Document() as target:
        target.pages.add()
        assert target.import_annotations_xfdf(data) == expected_count
        assert target.export_annotations_xfdf() == data
        notes = target.pages[0].annotations
        by_name = {annotation.get_property("NM"): annotation for annotation in notes}
        assert by_name["reply"].in_reply_to.get_property("NM") == "note"
        assert by_name["nested"].in_reply_to.get_property("NM") == "reply"
        assert by_name["note"].popup.get_property("NM") == "popup"
        assert by_name["popup"].parent_annotation.get_property("NM") == "note"
        destination = tmp_path / "roundtrip.pdf"
        target.save(destination)
    for source in (destination.read_bytes(), destination):
        with Document(source) as reopened:
            assert reopened.export_annotations_xfdf() == data
            free_text = next(
                a for a in reopened.pages[0].annotations if a.subtype == "FreeText"
            )
            assert free_text.get_property("Rotate") == 90
            assert free_text.get_property("Q") == 2


def test_json_annotation_exchange_also_preserves_rotation(document):
    document.pages[0].annotations.add_free_text(
        (10, 20, 100, 50), "Rotated", properties={"Rotate": 90}
    )
    data = document.pages[0].annotations.export_json()
    assert document.pages[1].annotations.import_json(data) == 2
    assert document.pages[1].annotations[1].get_property("Rotate") == 90


def test_page_mapping_and_page_api_keep_unrotated_geometry(document):
    data = _xfdf(
        _note(page="7", name="first")
        + _note(page="8", name="reply", inreplyto="first", replyType="Group")
    )
    assert document.import_annotations_xfdf(data, page_map={7: 1, 8: 0}) == 2
    reply = document.pages[0].annotations[1]
    rows = json.loads(document.export_annotations_json())["annotations"]
    by_name = {row["properties"].get("NM"): row for row in rows}
    assert by_name["reply"]["links"]["in_reply_to"] == by_name["first"]["id"]
    assert reply.get_property("RT") == Name("Group")
    assert document.pages[1].annotations.import_xfdf(_xfdf(_note(page="25"))) == 1
    assert document.pages[1].annotations[1].rect == (1, 2, 30, 40)
    before = document.export_annotations_json()
    with pytest.raises(PdfValidationException, match="single source page"):
        document.pages[0].annotations.import_xfdf(data)
    assert document.export_annotations_json() == before


@pytest.mark.parametrize("reply_name", ["existing", "xfdf-record-0"])
def test_missing_names_and_external_reply_names_never_bind_to_existing_objects(
    document,
    reply_name,
):
    document.pages[0].annotations[0].set_property("NM", "existing")
    data = _xfdf(_note() + _note(name="reply", inreplyto=reply_name))
    assert document.import_annotations_xfdf(data) == 2
    assert document.pages[0].annotations[1].get_property("NM") is None
    assert document.pages[0].annotations[2].in_reply_to is None


def test_forward_replies_and_implicit_popup_page_are_supported(document):
    data = _xfdf(
        _note(name="reply", inreplyto="parent")
        + '<text page="0" rect="1,2,30,40" name="parent"><popup rect="5,6,50,60" name="popup" open="yes"/></text>'
    )
    assert document.import_annotations_xfdf(data) == 3
    reply, parent, popup = list(document.pages[0].annotations)[1:]
    assert reply.in_reply_to.get_property("NM") == "parent"
    assert parent.popup.get_property("NM") == "popup"
    assert popup.parent_annotation.get_property("NM") == "parent"


@pytest.mark.parametrize("namespace", [_NS, ""])
@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16"])
def test_xml_encodings_namespaces_and_file_metadata_do_not_load_external_resources(
    document, namespace, encoding
):
    raw = _xfdf(
        _note(),
        namespace=namespace,
        metadata='<f href="https://example.invalid/source.pdf"/><ids original="old" modified="new"/>',
    )
    data = raw.decode().encode(encoding)
    assert document.import_annotations_xfdf(data) == 1
    assert len(document.attachments) == 0


@pytest.mark.parametrize(
    "subtype,attributes,children,property_name,expected",
    [
        ("line", 'start="1,2" end="3,4" head="Circle"', "", "L", [1, 2, 3, 4]),
        (
            "polygon",
            "",
            "<vertices>1,2;3,4;5,6</vertices>",
            "Vertices",
            [1, 2, 3, 4, 5, 6],
        ),
        ("polyline", "", "<vertices>1,2;3,4</vertices>", "Vertices", [1, 2, 3, 4]),
        (
            "ink",
            "",
            "<inklist><gesture>1,2;3,4</gesture><gesture>5,6</gesture></inklist>",
            "InkList",
            [[1, 2, 3, 4], [5, 6]],
        ),
        (
            "highlight",
            'coords="1,8,9,8,1,2,9,2"',
            "",
            "QuadPoints",
            [1, 8, 9, 8, 1, 2, 9, 2],
        ),
        (
            "freetext",
            'rotation="90" justification="centered"',
            "<defaultappearance>/Helv 12 Tf 0 g</defaultappearance>",
            "Rotate",
            90,
        ),
        ("text", 'flags="" open="no"', "", "F", 0),
    ],
)
def test_geometry_and_styles_map_to_pdf_properties(
    document, subtype, attributes, children, property_name, expected
):
    data = _xfdf(
        f'<{subtype} page="0" rect="1,2,30,40" {attributes}>{children}</{subtype}>'
    )
    assert document.import_annotations_xfdf(data) == 1
    assert document.pages[0].annotations[-1].get_property(property_name) == expected


@pytest.mark.parametrize(
    "invalid",
    [
        '<widget page="0" rect="1,2,3,4"/>',
        '<link page="0" rect="1,2,3,4"/>',
        '<fileattachment page="0" rect="1,2,3,4"/>',
        '<vendor page="0" rect="1,2,3,4"/>',
        '<popup page="0" rect="1,2,3,4"/>',
        '<text page="0"/>',
        '<text rect="1,2,3,4"/>',
        '<text page="-1" rect="1,2,3,4"/>',
        '<text page="1.0" rect="1,2,3,4"/>',
        '<text page="99" rect="1,2,3,4"/>',
        '<text page="0" rect="1,2,nan,4"/>',
        '<text page="0" rect="1,2,3"/>',
        '<text page="0" rect="1,2,3,4" color="#fff"/>',
        '<text page="0" rect="1,2,3,4" opacity="1.1"/>',
        '<text page="0" rect="1,2,3,4" opacity="inf"/>',
        '<text page="0" rect="1,2,3,4" width="-1"/>',
        '<text page="0" rect="1,2,3,4" dashes="0,0"/>',
        '<text page="0" rect="1,2,3,4" dashes="-1,2"/>',
        '<text page="0" rect="1,2,3,4" style="unknown"/>',
        '<text page="0" rect="1,2,3,4" open="true"/>',
        '<text page="0" rect="1,2,3,4" flags="print,unknown"/>',
        '<text page="0" rect="1,2,3,4" flags="print,print"/>',
        '<text page="0" rect="1,2,3,4" replyType="Reply"/>',
        '<text page="0" rect="1,2,3,4" href="file.pdf"/>',
        '<text page="0" rect="1,2,3,4"><contents-richtext>Rich</contents-richtext></text>',
        '<text page="0" rect="1,2,3,4"><contents><b>Rich</b></contents></text>',
        '<text page="0" rect="1,2,3,4"><contents>A</contents><contents>B</contents></text>',
        '<text page="0" rect="1,2,3,4"><popup page="1" rect="1,2,3,4"/></text>',
        '<text page="0" rect="1,2,3,4"><popup rect="1,2,3,4"/><popup rect="1,2,3,4"/></text>',
        '<line page="0" rect="1,2,3,4"/>',
        '<line page="0" rect="1,2,3,4" start="0,1" end="2,3" head="invalid"/>',
        '<highlight page="0" rect="1,2,3,4"/>',
        '<highlight page="0" rect="1,2,3,4" coords="1,2,3,4"/>',
        '<polygon page="0" rect="1,2,3,4"><vertices>1,2;3,4</vertices></polygon>',
        '<ink page="0" rect="1,2,3,4"><inklist/></ink>',
        '<ink page="0" rect="1,2,3,4"><inklist><gesture>1,2,3</gesture></inklist></ink>',
        '<freetext page="0" rect="1,2,3,4" justification="invalid"/>',
    ],
)
def test_invalid_later_annotation_leaves_all_existing_annotations_unchanged(
    document, invalid
):
    before = document.export_annotations_json()
    with pytest.raises(PdfValidationException):
        document.import_annotations_xfdf(_xfdf(_note(name="valid") + invalid))
    assert document.export_annotations_json() == before


@pytest.mark.parametrize(
    "xml",
    [
        b"not XML",
        b"<xfdf>",
        b"<other><annots/></other>",
        b"<xfdf/>",
        b'<xfdf xmlns="https://invalid.example/"><annots/></xfdf>',
        b"<xfdf><annots/><annots/></xfdf>",
        b"<xfdf><fields/></xfdf>",
        b'<xfdf><annots><text page="0" rect="1,2,3,4" page="1"/></annots></xfdf>',
        b'<xfdf><annots><x:text xmlns:x="urn:foreign" page="0" rect="1,2,3,4"/></annots></xfdf>',
    ],
)
def test_malformed_or_unsupported_xml_is_atomic(document, xml):
    before = document.export_annotations_json()
    with pytest.raises(PdfValidationException):
        document.import_annotations_xfdf(xml)
    assert document.export_annotations_json() == before


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
@pytest.mark.parametrize(
    "doctype",
    [
        '<!DOCTYPE xfdf [<!ENTITY secret SYSTEM "file:///must-not-be-opened">]>',
        '<!DOCTYPE xfdf SYSTEM "https://example.invalid/external.dtd">',
        '<!DOCTYPE xfdf [<!ENTITY x "expanded">]>',
    ],
)
def test_doctype_and_entity_declarations_are_refused_in_every_xml_encoding(
    document, encoding, doctype
):
    before = document.export_annotations_json()
    data = (doctype + _xfdf(_note()).decode()).encode(encoding)
    with pytest.raises(PdfValidationException, match="DOCTYPE"):
        document.import_annotations_xfdf(data)
    assert document.export_annotations_json() == before


@pytest.mark.parametrize(
    "annotations",
    [
        _note(name="same") + _note(name="same"),
        _note(name="self", inreplyto="self"),
        _note(name="a", inreplyto="b") + _note(name="b", inreplyto="a"),
        '<text page="0" rect="1,2,3,4" name="a" inreplyto="popup"><popup rect="1,2,3,4" name="popup"/></text>',
    ],
)
def test_ambiguous_names_and_invalid_reply_graphs_are_atomic(document, annotations):
    before = document.export_annotations_json()
    with pytest.raises(PdfValidationException):
        document.import_annotations_xfdf(_xfdf(annotations))
    assert document.export_annotations_json() == before


@pytest.mark.parametrize("mapping", [{}, {0: 99}, {0: True}, {"0": 0}, []])
def test_invalid_page_maps_are_atomic(document, mapping):
    before = document.export_annotations_json()
    with pytest.raises(PdfValidationException):
        document.import_annotations_xfdf(_xfdf(_note()), page_map=mapping)
    assert document.export_annotations_json() == before


@pytest.mark.parametrize(
    "changes",
    [
        {"max_input_bytes": 8},
        {"max_container_items": 2},
        {"max_nesting_depth": 2},
        {"max_object_bytes": 8},
        {"max_total_decoded_bytes": 8},
    ],
)
def test_input_xml_text_and_nesting_limits_leave_annotations_unchanged(changes):
    limits = replace(PdfLoadLimits.unlimited(), **changes)
    with Document(limits=limits) as document:
        document.pages.add()
        data = _xfdf(
            '<text page="0" rect="1,2,3,4"><contents>Long review text</contents></text>'
        )
        with pytest.raises(PdfResourceLimitException):
            document.import_annotations_xfdf(data)
        assert len(document.pages[0].annotations) == 0


def test_imported_redact_markup_preserves_page_content(document):
    before = document.pages[0].content
    assert (
        document.import_annotations_xfdf(_xfdf('<redact page="0" rect="0,0,200,200"/>'))
        == 1
    )
    assert document.pages[0].content == before
    assert "Original text" in document.pages[0].extract_text()


def test_imported_highlight_can_generate_visible_appearance():
    with Document() as document:
        page = document.pages.add(size=(200, 200))
        page.add_text("Review", 10, 100, font_size=20)
        before = page.render(dpi=72).pixels
        data = _xfdf(
            '<highlight page="0" rect="10,95,90,120" coords="10,120,90,120,10,95,90,95" color="#FFFF00" opacity="0.5"/>'
        )
        assert document.import_annotations_xfdf(data) == 1
        assert page.annotations.generate_appearances() == 1
        assert page.render(dpi=72).pixels != before


def test_streaming_encrypted_import_does_not_decode_content_or_appearances(
    document, tmp_path, monkeypatch
):
    path = tmp_path / "protected.pdf"
    document.encrypt("user", "owner")
    document.save(path)
    original = path.read_bytes()
    with Document.open_streaming(path, password="user") as streamed:

        def no_streams(*args, **kwargs):
            raise AssertionError("Annotation import must not decode streams")

        with monkeypatch.context() as patch:
            patch.setattr(streamed._engine_pdf, "get_page_content", no_streams)
            patch.setattr(streamed._engine_pdf, "_decode_cos_stream", no_streams)
            assert streamed.import_annotations_xfdf(_xfdf(_note(page="1"))) == 1
        data = _saved(streamed)
    with Document(data, password="user") as reopened:
        assert reopened.pages[1].annotations[0].contents == "Review"
        assert "Second page" in reopened.pages[1].extract_text()
    assert path.read_bytes() == original


def test_empty_input_and_disposed_apis(document):
    assert document.import_annotations_xfdf(_xfdf()) == 0
    assert document.pages[0].annotations.import_xfdf(_xfdf()) == 0
    collection = document.pages[0].annotations
    document.dispose()
    with pytest.raises(AsposePdfException, match="disposed"):
        document.import_annotations_xfdf(_xfdf())
    with pytest.raises(AsposePdfException, match="disposed"):
        collection.import_xfdf(_xfdf())


def test_prefixed_namespace_is_accepted(document):
    data = (
        f'<x:xfdf xmlns:x="{_NS}"><x:annots><x:text page="0" rect="1,2,3,4">'
        "<x:contents>Review &amp; verify</x:contents></x:text></x:annots></x:xfdf>"
    ).encode()
    assert document.import_annotations_xfdf(data) == 1
    assert document.pages[0].annotations[-1].contents == "Review & verify"


def test_empty_document_does_not_create_pages_for_import():
    with Document() as document:
        assert document.import_annotations_xfdf(_xfdf()) == 0
        with pytest.raises(PdfValidationException, match="out of range"):
            document.import_annotations_xfdf(_xfdf(_note()))
        assert document.page_count == 0


def test_failed_stream_read_keeps_annotations_and_stream_ownership(document):
    class FailedStream(io.BytesIO):
        def read(self, size=-1):
            raise OSError("Read failed")

    before = document.export_annotations_json()
    with FailedStream(_xfdf(_note())) as stream:
        with pytest.raises(OSError, match="Read failed"):
            document.import_annotations_xfdf(stream)
        assert not stream.closed
    assert document.export_annotations_json() == before


def test_new_annotation_object_limit_fails_before_attachment(document):
    cos = document._engine_pdf._cos_doc
    before_objects = set(cos.objects)
    before = document.export_annotations_json()
    document._engine_pdf._load_limits = replace(
        PdfLoadLimits.unlimited(), max_objects=max(cos.objects) + 1
    )
    with pytest.raises(PdfResourceLimitException, match="max_objects"):
        document.import_annotations_xfdf(_xfdf(_note()))
    assert document.export_annotations_json() == before
    assert set(cos.objects) == before_objects


def test_large_coordinate_arrays_are_bounded_before_attachment():
    limits = replace(PdfLoadLimits.unlimited(), max_container_items=32)
    with Document(limits=limits) as document:
        document.pages.add()
        coordinates = ",".join(["1"] * 1024)
        data = _xfdf(f'<highlight page="0" rect="1,2,3,4" coords="{coordinates}"/>')
        with pytest.raises(PdfResourceLimitException, match="max_container_items"):
            document.import_annotations_xfdf(data)
        assert len(document.pages[0].annotations) == 0


def test_xml_text_size_limit_applies_across_parser_chunks():
    limits = replace(PdfLoadLimits.unlimited(), max_object_bytes=70_000)
    with Document(limits=limits) as document:
        document.pages.add()
        data = _xfdf(
            '<text page="0" rect="1,2,3,4"><contents>'
            + "a" * 100_000
            + "</contents></text>"
        )
        with pytest.raises(PdfResourceLimitException, match="max_object_bytes"):
            document.import_annotations_xfdf(data)
        assert len(document.pages[0].annotations) == 0


def test_imported_threads_survive_incremental_save_without_changing_original_bytes(
    document,
):
    original = _saved(document)
    with Document(original) as loaded:
        data = _xfdf(_note(name="parent") + _note(name="reply", inreplyto="parent"))
        assert loaded.import_annotations_xfdf(data) == 2
        with io.BytesIO() as output:
            loaded.save(output, incremental=True)
            result = output.getvalue()
    assert result.startswith(original)
    with Document(result) as reopened:
        note, reply = list(reopened.pages[0].annotations)[1:]
        assert reply.in_reply_to.get_property("NM") == note.get_property("NM")
        assert "Original text" in reopened.pages[0].extract_text()

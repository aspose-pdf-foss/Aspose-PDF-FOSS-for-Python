"""Duplex scan assembly keeps page order, resources and page-bound references."""

from __future__ import annotations

import io
import struct
import zlib
from contextlib import ExitStack
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from aspose_pdf import (
    Document,
    FitDestination,
    GoToAction,
    GoToRAction,
    NumberingStyle,
    PageLabel,
    PdfLoadLimits,
    PdfResourceLimitException,
    URIAction,
    XYZDestination,
)
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.exceptions import (
    AsposePdfException,
    PdfParseException,
    PdfValidationException,
)
from aspose_pdf.images import ImagePlacementAbsorber
from aspose_pdf.outlines import OutlineItem


def _source(prefix, count, *, limits=None, font="Helvetica"):
    document = Document(limits=limits)
    for index in range(count):
        document.pages.add().add_text(f"{prefix}{index}", 72, 700, font_name=font)
    return document


def _bytes(document):
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _texts(document):
    return [page.extract_text().strip() for page in document.pages]


def _png(rgb):
    def chunk(kind, body):
        return (
            struct.pack(">I", len(body))
            + kind
            + body
            + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\0" + bytes(rgb)))
        + chunk(b"IEND", b"")
    )


@pytest.mark.parametrize("on_disk", [False, True])
@pytest.mark.parametrize(
    "fronts,backs,reverse,expected",
    [
        (0, 0, False, []),
        (1, 1, False, ["F0", "B0"]),
        (3, 3, False, ["F0", "B0", "F1", "B1", "F2", "B2"]),
        (3, 3, True, ["F0", "B2", "F1", "B1", "F2", "B0"]),
        (3, 2, False, ["F0", "B0", "F1", "B1", "F2"]),
        (3, 2, True, ["F0", "B1", "F1", "B0", "F2"]),
        (2, 3, True, ["F0", "B2", "F1", "B1", "B0"]),
        (1, 4, False, ["F0", "B0", "B1", "B2", "B3"]),
        (0, 3, True, ["B2", "B1", "B0"]),
        (3, 0, True, ["F0", "F1", "F2"]),
    ],
)
def test_order_and_unmatched_tails_survive_memory_and_file_round_trips(
    tmp_path, on_disk, fronts, backs, reverse, expected
):
    with _source("F", fronts) as first, _source("B", backs) as second:
        original = _bytes(first), _bytes(second)
        with first.interleave(
            second, reverse_other=reverse, strict=fronts == backs
        ) as result:
            assert result is not first and result is not second
            assert _texts(result) == expected and result.page_count == fronts + backs
            assert result.load_limits is first.load_limits
            assert result._engine_pdf._load_budget is not first._engine_pdf._load_budget
            if expected:
                assert result.validate()
            if on_disk:
                path = tmp_path / "duplex.pdf"
                result.save(path)
                source = path
            else:
                source = _bytes(result)
            with Document(source) as reopened:
                assert _texts(reopened) == expected
                assert reopened.page_count == fronts + backs
        assert (_bytes(first), _bytes(second)) == original


@pytest.mark.parametrize("counts", [(0, 1), (1, 0), (2, 3), (3, 2)])
@pytest.mark.parametrize("reverse", [False, True])
def test_default_rejects_mismatched_counts_before_reading_page_content(
    counts, reverse, monkeypatch
):
    with _source("F", counts[0]) as first, _source("B", counts[1]) as second:

        def unexpected(*args, **kwargs):
            raise AssertionError("Invalid requests must not read page streams")

        monkeypatch.setattr(first._engine_pdf, "get_page_content", unexpected)
        monkeypatch.setattr(second._engine_pdf, "get_page_content", unexpected)
        with pytest.raises(PdfValidationException, match="equal page counts"):
            first.interleave(second, reverse_other=reverse)


@pytest.mark.parametrize(
    "option,value",
    [
        ("reverse_other", 1),
        ("reverse_other", None),
        ("reverse_other", "true"),
        ("strict", 0),
        ("strict", None),
        ("strict", "false"),
    ],
)
def test_options_require_booleans_even_for_empty_documents(option, value):
    with Document() as first, Document() as second:
        with pytest.raises(PdfValidationException, match="booleans"):
            first.interleave(second, **{option: value})


@pytest.mark.parametrize("value", [None, "backs.pdf", b"%PDF", [], object()])
def test_second_input_must_be_a_document(value):
    with Document() as document:
        with pytest.raises(TypeError, match="Document"):
            document.interleave(value)


def test_disposed_input_is_rejected():
    with _source("F", 1) as first, _source("B", 1) as second:
        first.dispose()
        with pytest.raises(AsposePdfException, match="disposed"):
            first.interleave(second)
    with _source("F", 1) as first, _source("B", 1) as second:
        second.dispose()
        with pytest.raises(AsposePdfException, match="disposed"):
            first.interleave(second)


def test_outputs_and_sources_can_be_edited_and_disposed_independently():
    with _source("F", 2) as first, _source("B", 2) as second:
        result = first.interleave(second, reverse_other=True)
        result.pages[0].add_text("OUTPUT ONLY", 72, 650)
        assert "OUTPUT ONLY" not in first.extract_text()
        first.pages[1].add_text("INPUT ONLY", 72, 650)
        assert "INPUT ONLY" not in result.extract_text()
        second.dispose()
        assert "B1" in result.pages[1].extract_text()
    with result:
        result.pages[1].add_text("AFTER CLOSE", 72, 600)
        with Document(_bytes(result)) as reopened:
            assert "AFTER CLOSE" in reopened.pages[1].extract_text()


@pytest.mark.parametrize("reverse", [False, True])
def test_local_links_named_destinations_and_actions_follow_source_pages(reverse):
    with _source("F", 3) as first, _source("B", 3) as second:
        for source in (first, second):
            page = source.pages[0]
            page.add_link((10, 10, 100, 30), FitDestination(2))
            source.destinations["same-name"] = XYZDestination(1, 10, 700, 1.5)
            page.add_link((10, 40, 100, 60), "same-name")
            page.add_link((10, 70, 100, 90), GoToAction(FitDestination(1)))
            page.add_link((10, 100, 100, 120), URIAction("https://example.org"))
            page.add_link(
                (10, 130, 100, 150), GoToRAction("remote.pdf", FitDestination(2))
            )
        with first.interleave(second, reverse_other=reverse) as result:
            for document in (result, Document(_bytes(result))):
                try:
                    for page_index, last, middle in (
                        (0, 4, 2),
                        (5 if reverse else 1, 1 if reverse else 5, 3),
                    ):
                        properties = [
                            annotation.properties
                            for annotation in document.pages[page_index].annotations
                        ]
                        assert properties[0]["Dest"] == FitDestination(last)
                        assert properties[1]["Dest"] == XYZDestination(
                            middle, 10, 700, 1.5
                        )
                        assert properties[2]["A"]["D"] == FitDestination(middle)
                        assert properties[3]["A"]["URI"] == "https://example.org"
                        assert properties[4]["A"]["F"] == "remote.pdf"
                        assert properties[4]["A"]["D"] == [2, "Fit"]
                finally:
                    if document is not result:
                        document.dispose()


@pytest.mark.parametrize("reload_sources", [False, True])
def test_live_and_loaded_bookmarks_follow_pages_without_changing_source_models(
    reload_sources,
):
    with ExitStack() as stack:
        first, second = [stack.enter_context(_source(label, 2)) for label in ("F", "B")]
        for source, label in ((first, "F"), (second, "B")):
            root = source.outlines.add(
                OutlineItem(
                    label, destination=GoToAction(XYZDestination(1, 10, 700, 2))
                )
            )
            root.add(OutlineItem(label + " child", destination=FitDestination(0)))
            source.outlines.add(
                OutlineItem(
                    label + " web", destination=URIAction("https://example.org")
                )
            )
        if reload_sources:
            first, second = [
                stack.enter_context(Document(_bytes(source)))
                for source in (first, second)
            ]
        originals = first._engine_pdf._outlines_data, second._engine_pdf._outlines_data
        with first.interleave(second, reverse_other=True) as result:
            for document in (result, stack.enter_context(Document(_bytes(result)))):
                assert [
                    (item.title, item.page_index) for item in document.outlines
                ] == [("F", 2), ("F web", None), ("B", 1), ("B web", None)]
                assert document.outlines[0].destination == GoToAction(
                    XYZDestination(2, 10, 700, 2)
                )
                assert document.outlines[2].destination == GoToAction(
                    XYZDestination(1, 10, 700, 2)
                )
                assert document.outlines[0].children[0].destination == FitDestination(0)
                assert document.outlines[2].children[0].destination == FitDestination(3)
        assert first._engine_pdf._outlines_data is originals[0]
        assert second._engine_pdf._outlines_data is originals[1]


def test_page_labels_are_reordered_as_values_and_inputs_keep_their_labels():
    with _source("F", 2) as first, _source("B", 2) as second:
        first.page_labels[0] = PageLabel(NumberingStyle.NUMERALS_ARABIC, "F-", 10)
        second.page_labels[0] = PageLabel(
            NumberingStyle.NUMERALS_ROMAN_LOWERCASE, "B-", 1
        )
        with first.interleave(second, reverse_other=True) as result:
            assert [page.label for page in result.pages] == [
                "F-10",
                "B-ii",
                "F-11",
                "B-i",
            ]
            with Document(_bytes(result)) as reopened:
                assert [page.label for page in reopened.pages] == [
                    "F-10",
                    "B-ii",
                    "F-11",
                    "B-i",
                ]
        assert [page.label for page in first.pages] == ["F-10", "F-11"]
        assert [page.label for page in second.pages] == ["B-i", "B-ii"]


@pytest.mark.parametrize("label_first", [False, True])
def test_unlabelled_pages_retain_importer_blank_label_semantics(label_first):
    with _source("F", 1) as first, _source("B", 1) as second:
        (first if label_first else second).page_labels[0] = PageLabel(
            prefix="Only", style=NumberingStyle.NONE
        )
        with first.interleave(second) as result:
            assert [page.label for page in result.pages] == (
                ["Only", ""] if label_first else ["", "Only"]
            )


def test_field_name_collisions_keep_distinct_widgets_on_their_pages():
    with _source("F", 2) as first, _source("B", 2) as second:
        first.form.add_text_field("answer", 0, (50, 400, 200, 425), value="front")
        second.form.add_text_field("answer", 0, (50, 400, 200, 425), value="back")
        with first.interleave(second, reverse_other=True) as result:
            for document in (result, Document(_bytes(result))):
                try:
                    by_value = {field.value: field for field in document.form}
                    assert (
                        len(by_value) == 2
                        and len({field.name for field in document.form}) == 2
                    )
                    assert by_value["front"].page_index == 0
                    assert by_value["back"].page_index == 3
                    by_value["back"].value = "edited"
                finally:
                    if document is not result:
                        document.dispose()
            assert second.form["answer"].value == "back"
        assert first.form["answer"].value == "front"


def test_images_fonts_rotation_and_boxes_render_identically_after_interleaving():
    with (
        _source("F", 2, font="Helvetica") as first,
        _source("B", 2, font="Courier") as second,
    ):
        first.pages[0].add_image(_png((255, 0, 0)), 20, 100, width=30, height=40)
        second.pages[0].add_image(_png((0, 0, 255)), 60, 200, width=50, height=60)
        first.pages[1].rotation = 90
        second.pages[1].crop_box = (10, 10, 400, 700)
        expected = [first.pages[0], second.pages[1], first.pages[1], second.pages[0]]
        pixels = [page.render(dpi=18, antialias=False).pixels for page in expected]
        with first.interleave(second, reverse_other=True) as result:
            for index, original in enumerate(expected):
                page = result.pages[index]
                assert page.rotation == original.rotation
                assert (
                    page.media_box == original.media_box
                    and page.crop_box == original.crop_box
                )
                assert page.render(dpi=18, antialias=False).pixels == pixels[index]
            absorber = ImagePlacementAbsorber()
            absorber.visit(result)
            placements = {
                placement.page_index: placement
                for placement in absorber.image_placements
            }
            assert set(placements) == {0, 3}
            assert (placements[0].rectangle.x, placements[0].rectangle.y) == (20, 100)
            assert (placements[3].rectangle.width, placements[3].rectangle.height) == (
                50,
                60,
            )
            assert placements[3].matrix == (50, 0, 0, 60, 60, 200)


def test_layers_keep_visibility_and_remain_independent():
    with _source("F", 1) as first, _source("B", 1) as second:
        for source, label in ((first, "F"), (second, "B")):
            layer = source.layers.add(label + " hidden", visible=False)
            with source.pages[0].layer(layer):
                source.pages[0].add_text(label + " secret", 72, 650)
        with first.interleave(second) as result:
            assert _texts(result) == ["F0", "B0"]
            assert {layer.name for layer in result.layers} == {"F hidden", "B hidden"}
            result.layers[0].visible = True
            assert "secret" in result.extract_text()
            assert (
                "secret" not in first.extract_text()
                and "secret" not in second.extract_text()
            )


def test_structure_page_references_follow_the_physical_page_permutation():
    with _source("F", 2) as first, _source("B", 2) as second:
        for source, label in ((first, "F"), (second, "B")):
            for index in range(2):
                source.tagged_content.add_element(
                    "P", page_number=index + 1, mcids=[0], actual_text=f"{label}{index}"
                )
        with first.interleave(second, reverse_other=True) as result:
            by_text = {
                element.actual_text: element.page_number
                for element in result.tagged_content.root_elements
            }
            assert by_text == {"F0": 1, "F1": 3, "B0": 4, "B1": 2}


@pytest.mark.parametrize(
    "stream_first,stream_second",
    [(False, False), (False, True), (True, False), (True, True)],
)
@pytest.mark.parametrize("encrypted", [False, True])
def test_streaming_and_encrypted_inputs_can_close_before_output_is_used(
    tmp_path, stream_first, stream_second, encrypted
):
    paths = [tmp_path / "fronts.pdf", tmp_path / "backs.pdf"]
    with ExitStack() as stack:
        for label, path in zip(("F", "B"), paths):
            with _source(label, 2) as source:
                if encrypted:
                    source.encrypt(user_password="secret", owner_password="owner")
                source.save(path)
        inputs = []
        for path, streaming in zip(paths, (stream_first, stream_second)):
            inputs.append(
                stack.enter_context(
                    (Document.open_streaming if streaming else Document)(
                        path, password="secret" if encrypted else None
                    )
                )
            )
        result = inputs[0].interleave(inputs[1], reverse_other=True)
        for source, streaming in zip(inputs, (stream_first, stream_second)):
            if streaming:
                assert source._engine_pdf.page_contents == []
    with result:
        assert _texts(result) == ["F0", "B1", "F1", "B0"]
        assert not result.is_encrypted
        with Document(_bytes(result)) as reopened:
            assert _texts(reopened) == ["F0", "B1", "F1", "B0"]


def test_metadata_version_and_imported_attachments_follow_the_document_contract():
    with _source("F", 1) as first, _source("B", 1) as second:
        first.info["Title"] = "Front source"
        second.info["Title"] = "Back source"
        first.version, second.version = "1.4", "2.0"
        first.add_attachment("note.txt", b"front")
        second.add_attachment("note.txt", b"back")
        with first.interleave(second) as result:
            assert result.info["Title"] == "Front source" and result.version == "2.0"
            assert sorted(file.contents for file in result.embedded_files) == [
                b"back",
                b"front",
            ]
            with Document(_bytes(result)) as reopened:
                assert (
                    reopened.info["Title"] == "Front source"
                    and reopened.version == "2.0"
                )
                assert sorted(file.contents for file in reopened.embedded_files) == [
                    b"back",
                    b"front",
                ]


def test_a_document_can_be_interleaved_with_an_independent_copy_of_itself():
    with _source("P", 2) as source:
        source.pages[0].add_link((10, 10, 30, 30), FitDestination(1))
        with source.interleave(source, reverse_other=True) as result:
            assert _texts(result) == ["P0", "P1", "P1", "P0"]
            assert result.pages[0].annotations[0].get_property(
                "Dest"
            ) == FitDestination(2)
            assert result.pages[3].annotations[0].get_property(
                "Dest"
            ) == FitDestination(1)
            result.pages[0].add_text("ONE COPY", 72, 650)
            assert "ONE COPY" not in result.pages[3].extract_text()
        assert _texts(source) == ["P0", "P1"]


@pytest.mark.parametrize("limit", ["max_pages", "max_container_items"])
def test_total_page_limits_are_checked_before_import(limit, monkeypatch):
    with Document(limits=PdfLoadLimits(**{limit: 3})) as first, Document() as second:
        for _ in range(2):
            first.pages.add()
            second.pages.add()

        def unexpected(*args, **kwargs):
            raise AssertionError("Import must not run over the output page limit")

        monkeypatch.setattr(SimplePdf, "append", unexpected)
        with pytest.raises(PdfResourceLimitException, match=limit):
            first.interleave(second)


def test_output_object_and_content_limits_apply_to_the_second_source_too():
    with (
        _source("F", 1, limits=PdfLoadLimits(max_objects=6)) as first,
        _source("B", 1) as second,
    ):
        with pytest.raises(PdfResourceLimitException, match="max_objects"):
            first.interleave(second)
    with (
        _source("F", 1, limits=PdfLoadLimits(max_content_stream_bytes=200)) as first,
        _source("B", 1) as second,
    ):
        second.pages[0].add_text("x" * 500, 72, 650)
        with pytest.raises(PdfResourceLimitException, match="max_content_stream_bytes"):
            first.interleave(second)


def test_bookmark_depth_and_combined_node_limits_are_enforced():
    with (
        Document(limits=PdfLoadLimits(max_nesting_depth=3)) as first,
        Document() as second,
    ):
        first.pages.add()
        second.pages.add()
        item = second.outlines.add(OutlineItem("Root", 0))
        for _ in range(3):
            item = item.add(OutlineItem("Child", 0))
        with pytest.raises(PdfResourceLimitException, match="max_nesting_depth"):
            first.interleave(second)
    with (
        Document(limits=PdfLoadLimits(max_container_items=8)) as first,
        Document() as second,
    ):
        for source in (first, second):
            source.pages.add()
            for index in range(5):
                source.outlines.add(OutlineItem(str(index), 0))
        with pytest.raises(PdfResourceLimitException, match="max_container_items"):
            first.interleave(second)


def test_partial_import_failures_restore_sources_and_dispose_the_output(monkeypatch):
    with _source("F", 1) as first, _source("B", 1) as second:
        first.outlines.add(OutlineItem("Front", 0))
        second.outlines.add(OutlineItem("Back", 0))
        original_models = (
            first._engine_pdf._outlines_data,
            second._engine_pdf._outlines_data,
        )
        original = SimplePdf.append
        created = []

        def append(target, source, *args, **kwargs):
            if created:
                raise PdfParseException("simulated import failure")
            created.append(target)
            return original(target, source, *args, **kwargs)

        monkeypatch.setattr(SimplePdf, "append", append)
        with pytest.raises(PdfParseException, match="simulated"):
            first.interleave(second)
        assert first._engine_pdf._outlines_data is original_models[0]
        assert second._engine_pdf._outlines_data is original_models[1]
        with pytest.raises(AsposePdfException, match="disposed"):
            created[0]._ensure_not_disposed()
        assert _texts(first) == ["F0"] and _texts(second) == ["B0"]


def test_each_source_is_imported_as_one_batch_so_cross_page_links_survive(monkeypatch):
    with _source("F", 4) as first, _source("B", 4) as second:
        original = SimplePdf.append
        calls = []

        def append(target, source, pages=None, at=None):
            calls.append((source, pages, at))
            return original(target, source, pages=pages, at=at)

        monkeypatch.setattr(SimplePdf, "append", append)
        with first.interleave(second) as result:
            assert result.page_count == 8
        assert calls == [
            (first._engine_pdf, None, None),
            (second._engine_pdf, None, None),
        ]


def test_blank_scanned_sides_are_preserved_without_inserting_additional_blanks():
    with Document() as first, Document() as second:
        first.pages.add()
        first.pages.add().add_text("F1", 72, 700)
        second.pages.add()
        second.pages.add().add_text("B1", 72, 700)
        with first.interleave(second, reverse_other=True) as result:
            assert _texts(result) == ["", "B1", "F1", ""]
            assert result.page_count == 4 and result.validate()


def test_output_object_limit_includes_bookmark_objects_created_for_saving():
    with (
        _source("F", 1, limits=PdfLoadLimits(max_objects=8)) as first,
        _source("B", 1) as second,
    ):
        first.outlines.add(OutlineItem("One", 0))
        first.outlines.add(OutlineItem("Two", 0))
        with pytest.raises(PdfResourceLimitException, match="max_objects"):
            first.interleave(second)


def test_a_cycle_in_live_bookmarks_is_rejected_without_replacing_the_model():
    with _source("F", 1) as first, _source("B", 1) as second:
        item = first.outlines.add(OutlineItem("Cycle", 0))
        item.children.append(item)
        original = first._engine_pdf._outlines_data
        with pytest.raises(PdfParseException, match="cycle"):
            first.interleave(second)
        assert first._engine_pdf._outlines_data is original
        assert first.outlines[0] is item and second.page_count == 1


def test_first_source_decoded_stream_policy_also_bounds_the_second_source():
    with (
        _source("F", 1, limits=PdfLoadLimits(max_decoded_stream_bytes=200)) as first,
        _source("B", 1) as second,
    ):
        second.pages[0].add_text("x" * 500, 72, 650)
        with pytest.raises(PdfResourceLimitException, match="max_decoded_stream_bytes"):
            first.interleave(second)


def test_combined_page_content_is_bounded_even_when_each_source_fits():
    with (
        _source("F", 1, limits=PdfLoadLimits(max_total_decoded_bytes=70)) as first,
        _source("B", 1) as second,
    ):
        for source in (first, second):
            source._engine_pdf._set_page_content(
                0, b"BT /F1 12 Tf 72 700 Td (A page of text) Tj ET"
            )
        with pytest.raises(PdfResourceLimitException, match="max_total_decoded_bytes"):
            first.interleave(second)


@pytest.mark.parametrize("encrypted", [False, True])
def test_scanned_images_still_render_after_streaming_inputs_close(tmp_path, encrypted):
    paths = [tmp_path / "fronts.pdf", tmp_path / "backs.pdf"]
    pixels = []
    for path, color in zip(paths, ((255, 0, 0), (0, 0, 255))):
        with Document() as source:
            page = source.pages.add()
            page.add_image(_png(color), 50, 100, width=100, height=200)
            pixels.append(page.render(dpi=18, antialias=False).pixels)
            if encrypted:
                source.encrypt(user_password="secret", owner_password="owner")
            source.save(path)
    password = "secret" if encrypted else None
    with (
        Document.open_streaming(paths[0], password=password) as first,
        Document.open_streaming(paths[1], password=password) as second,
    ):
        result = first.interleave(second)
    with result:
        assert result.page_count == 2
        for index, expected in enumerate(pixels):
            assert (
                result.pages[index].render(dpi=18, antialias=False).pixels == expected
            )
        with Document(_bytes(result)) as reopened:
            assert reopened.pages[1].render(dpi=18, antialias=False).pixels == pixels[1]


@pytest.mark.parametrize("reload_sources", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_colliding_image_names_remain_editable_before_and_after_save(
    reload_sources, reverse
):
    with ExitStack() as stack:
        sources = [stack.enter_context(Document()) for _ in range(2)]
        for source, color in zip(sources, ((255, 0, 0), (0, 0, 255))):
            source.pages.add().add_image(_png(color), 20, 100, width=40, height=40)
            source.pages.add()
        if reload_sources:
            sources = [
                stack.enter_context(Document(_bytes(source))) for source in sources
            ]
        originals = [_bytes(source) for source in sources]
        front_pixels = sources[0].pages[0].render(dpi=18, antialias=False).pixels
        blank_pixels = sources[0].pages[1].render(dpi=18, antialias=False).pixels
        result = stack.enter_context(
            sources[0].interleave(sources[1], reverse_other=reverse)
        )
        target_page = 3 if reverse else 1
        for document in (result, stack.enter_context(Document(_bytes(result)))):
            absorber = ImagePlacementAbsorber()
            absorber.visit(document.pages[target_page])
            [placement] = absorber.image_placements
            assert placement.replace(_png((0, 255, 0))) is True
            assert placement.image_data == b"\x00\xff\x00"
            assert placement.hide() is True
            assert (
                document.pages[target_page].render(dpi=18, antialias=False).pixels
                == blank_pixels
            )
            assert placement.replace(_png((0, 255, 0))) is True
            absorber.visit(document.pages[target_page])
            assert absorber.image_placements[-1].image_data == b"\x00\xff\x00"
            with Document(_bytes(document)) as saved:
                assert (
                    saved.pages[target_page].render(dpi=18, antialias=False).pixels
                    != blank_pixels
                )
                assert (
                    saved.pages[0].render(dpi=18, antialias=False).pixels
                    == front_pixels
                )
        assert [_bytes(source) for source in sources] == originals


def test_interleaving_the_same_scan_keeps_image_edits_independent():
    with Document() as source:
        source.pages.add().add_image(_png((0, 0, 255)), 20, 100, width=40, height=40)
        with source.interleave(source) as result:
            absorber = ImagePlacementAbsorber()
            absorber.visit(result)
            first, second = absorber.image_placements
            assert first.replace(_png((0, 255, 0))) is True
            absorber.visit(result)
            by_page = {item.page_index: item for item in absorber.image_placements}
            assert by_page[0].image_data == b"\x00\xff\x00"
            assert by_page[1].image_data == b"\x00\x00\xff"
            assert second.hide() is True
            assert first.replace(_png((255, 0, 0))) is True
            with Document(_bytes(result)) as saved:
                assert saved.pages[0].render(dpi=18, antialias=False).pixels != (
                    saved.pages[1].render(dpi=18, antialias=False).pixels
                )


@pytest.mark.parametrize("reload_sources", [False, True])
def test_every_scanned_page_has_its_own_editable_image(reload_sources):
    colors = ((255, 0, 0), (255, 255, 0), (0, 0, 255), (0, 255, 255))
    with ExitStack() as stack:
        first, second = [stack.enter_context(Document()) for _ in range(2)]
        for source, pair in ((first, colors[:2]), (second, colors[2:])):
            for color in pair:
                source.pages.add().add_image(_png(color), 20, 100, width=40, height=40)
        if reload_sources:
            first, second = [
                stack.enter_context(Document(_bytes(source)))
                for source in (first, second)
            ]
        with first.interleave(second, reverse_other=True) as result:
            absorber = ImagePlacementAbsorber()
            absorber.visit(result)
            placements = {item.page_index: item for item in absorber.image_placements}
            expected = dict(enumerate(bytes(colors[i]) for i in (0, 3, 1, 2)))
            assert {i: p.image_data for i, p in placements.items()} == expected
            for index in range(4):
                assert placements[index].replace(_png((0, 255, 0))) is True
                expected[index] = b"\x00\xff\x00"
                absorber.visit(result)
                assert {
                    p.page_index: p.image_data for p in absorber.image_placements
                } == expected


def test_public_image_alias_does_not_edit_a_literal_resource_with_the_same_name():
    with Document() as first, Document() as second, Document() as expected:
        first.pages.add().add_image(_png((255, 0, 0)), 20, 100, width=40, height=40)
        page = second.pages.add()
        page.add_image(_png((0, 0, 255)), 20, 100, width=40, height=40)
        page.add_image(_png((255, 255, 0)), 80, 100, width=40, height=40, name="Im1_2")
        expected_page = expected.pages.add()
        expected_page.add_image(_png((0, 255, 0)), 20, 100, width=40, height=40)
        expected_page.add_image(_png((255, 255, 0)), 80, 100, width=40, height=40)
        pixels = expected_page.render(dpi=18, antialias=False).pixels
        with first.interleave(second) as result:
            absorber = ImagePlacementAbsorber()
            absorber.visit(result.pages[1])
            blue = next(
                p for p in absorber.image_placements if p.image_data == b"\0\0\xff"
            )
            assert blue.replace(_png((0, 255, 0))) is True
            assert result.pages[1].render(dpi=18, antialias=False).pixels == pixels
            with Document(_bytes(result)) as saved:
                assert saved.pages[1].render(dpi=18, antialias=False).pixels == pixels


@pytest.mark.parametrize("source_state", ["authored", "loaded", "edited"])
@pytest.mark.parametrize("operation", ["interleave", "merge"])
def test_attachment_metadata_and_compression_survive_import_and_name_clashes(
    source_state,
    operation,
):
    with ExitStack() as stack:
        sources = [stack.enter_context(_source(label, 1)) for label in ("F", "B")]
        created = datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC)
        modified = datetime(2025, 2, 3, 4, 5, 6, tzinfo=UTC)
        for index, source in enumerate(sources):
            source.add_attachment(
                "note.txt",
                (f"Payload {index} " * 100).encode(),
                mime="text/plain",
                description=f"Description {index}",
                creation_date=created,
                mod_date=modified,
                relationship="Source" if index == 0 else "Data",
                compress=False,
            )
        if source_state != "authored":
            sources = [
                stack.enter_context(Document(_bytes(source))) for source in sources
            ]
        if source_state == "edited":
            for source in sources:
                source.update_attachment(
                    "note.txt", description="Edited", compress=False
                )
        expected = [
            sources[0].embedded_files[0],
            replace(sources[1].embedded_files[0], name="note_2.txt"),
        ]
        originals = [_bytes(source) for source in sources]
        result = stack.enter_context(
            sources[0].interleave(sources[1])
            if operation == "interleave"
            else Document().merge(*sources)
        )
        with result:
            assert result.embedded_files == expected
            output = _bytes(result)
            with Document(output) as reopened:
                assert reopened.embedded_files == expected
            if source_state != "loaded":
                assert all(spec.contents in output for spec in expected)
            result.update_attachment("note_2.txt", description="Output only")
            assert sources[1].embedded_files[0].description != "Output only"
        assert [_bytes(source) for source in sources] == originals

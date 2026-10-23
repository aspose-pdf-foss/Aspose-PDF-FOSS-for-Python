"""Public layout snapshots and JSON exports retain document structure."""

from __future__ import annotations

import base64
import io
import json
from dataclasses import FrozenInstanceError
from itertools import repeat

import pytest

from aspose_pdf import (
    Document,
    ExtractedBlock,
    PdfLoadLimits,
    PdfResourceLimitException,
    PageStamp,
    RasterizedPage,
)
from aspose_pdf.block_extraction import ExtractedBlock as ModuleBlock
from aspose_pdf.engine.std_font_data import load_substitute_sfnt
from aspose_pdf.exceptions import AsposePdfException, PdfValidationException


def _bytes(document):
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _report(document):
    page = document.pages.add()
    page.add_text("Inventory report", 60, 760, font_size=20)
    page.add_text("The table lists available inventory.", 60, 730, font_size=10)
    for row, cells in enumerate((("Part", "Count"), ("Bolt", "20"), ("Nut", "30"))):
        for x, cell in zip((60, 170), cells):
            page.add_text(cell, x, 690 - row * 15, font_size=10)
    page.add_text("- First check", 70, 610, font_size=10)
    page.add_text("- Second check", 70, 596, font_size=10)
    page.add_text("The report is complete.", 60, 540, font_size=10)
    return page


@pytest.mark.parametrize("source", ["live", "bytes", "file", "stream", "streaming"])
def test_blocks_cover_structure_and_anchors_through_round_trips(source, tmp_path):
    with Document() as original:
        _report(original)
        expected = original.extract_blocks()
        if source == "live":
            document = original
        elif source in ("bytes", "stream"):
            data = _bytes(original)
            document = Document(data if source == "bytes" else io.BytesIO(data))
        else:
            path = tmp_path / "source.pdf"
            original.save(path)
            opener = Document.open_streaming if source == "streaming" else Document
            document = opener(path)
        try:
            blocks = document.extract_blocks()
            assert blocks == expected == document.pages[0].extract_blocks()
            assert ModuleBlock is ExtractedBlock
            assert all(isinstance(block, ExtractedBlock) for block in blocks)
            assert [block.kind for block in blocks] == [
                "heading", "paragraph", "table", "list", "paragraph",
            ]
            assert blocks[0].text == "Inventory report" and blocks[0].level == 1
            assert [(b.page_index, b.x, b.y) for b in blocks] == [
                (0, 60, 760), (0, 60, 730), (0, 60, 690),
                (0, 70, 610), (0, 60, 540),
            ]
            assert blocks[2].rows == (("Part", "Count"), ("Bolt", "20"), ("Nut", "30"))
            assert blocks[2].column_spans == ((1, 1),) * 3
            assert blocks[3].items == ("First check", "Second check")
            assert not blocks[3].ordered
        finally:
            if document is not original:
                document.dispose()


def test_snapshots_and_json_ready_dictionaries_are_independent():
    with Document() as document:
        _report(document)
        blocks = document.extract_blocks()
        payload = blocks[2].to_dict()
        payload["rows"][0][0] = "Edited"
        payload["column_spans"][0][0] = 2
        with pytest.raises(FrozenInstanceError):
            blocks[0].text = "Edited"
        with pytest.raises(TypeError):
            blocks[3].items[0] = "Edited"
        document.pages[0].replace_text("Inventory report", "Other report")
    assert blocks[0].text == "Inventory report"
    assert blocks[2].rows[0] == ("Part", "Count")
    assert blocks[2].column_spans[0] == (1, 1)
    assert json.dumps(blocks[2].to_dict())


def test_page_selection_preserves_order_repetitions_and_original_indices():
    with Document() as document:
        for number in range(3):
            document.pages.add().add_text(f"Page {number}", 20, 700)
        blocks = document.extract_blocks(pages=(i for i in (2, 0, 2)))
        assert [(b.page_index, b.text) for b in blocks] == [
            (2, "Page 2"), (0, "Page 0"), (2, "Page 2"),
        ]
        assert document.extract_blocks(pages=[]) == []
        assert document.pages[1].extract_blocks()[0].page_index == 1
        payload = json.loads(document.pages[1].export_blocks_json())
        assert [b["page_index"] for b in payload["blocks"]] == [1]


def test_json_schema_unicode_and_output_destinations(tmp_path):
    with Document() as document:
        document.pages.add().add_text(
            "café Ångström", 30, 700, font=load_substitute_sfnt("sans-regular")
        )
        blocks = document.extract_blocks()
        data = document.export_blocks_json()
        assert "café Ångström".encode() in data
        assert data.endswith(b"\n")
        assert json.loads(data) == {
            "schema_version": 1, "blocks": [b.to_dict() for b in blocks],
        }
        assert set(json.loads(data)["blocks"][0]) == {
            "page_index", "kind", "x", "y", "text", "level", "items",
            "rows", "column_spans", "ordered", "alt", "image",
        }
        assert document.export_blocks_json() == data
        target = tmp_path / "nested" / "blocks.json"
        assert document.export_blocks_json(target) == data
        assert target.read_bytes() == data
        with pytest.raises(FileExistsError):
            document.export_blocks_json(target, overwrite=False)
        assert target.read_bytes() == data
        stream = io.BytesIO(b"prefix:")
        stream.seek(0, 2)
        assert document.export_blocks_json(stream) == data
        assert not stream.closed and stream.getvalue() == b"prefix:" + data
        assert document.pages[0].export_blocks_json() == data


def test_short_and_stalled_binary_stream_writes():
    class ShortWriter(io.BytesIO):
        def write(self, data):
            return super().write(data[:3])

    class StalledWriter(io.BytesIO):
        def write(self, data):
            return 0

    with Document() as document:
        _report(document)
        stream = ShortWriter()
        assert document.export_blocks_json(stream) == stream.getvalue()
        with pytest.raises(AsposePdfException, match="Stream write"):
            document.export_blocks_json(StalledWriter())


def test_ordered_list_and_untruncated_table_cells():
    with Document() as document:
        page = document.pages.add()
        for number in (1, 2):
            page.add_text(f"{number}. Step {number}", 60, 740 - number * 14, font_size=10)
        block = page.extract_blocks()[0]
        assert block.kind == "list" and block.ordered
        assert block.items == ("Step 1", "Step 2")
        assert (block.x, block.y) == (60, 726)
        page = document.pages.add(size=(100000, 1000))
        long_text = "x" * 5000
        for row in range(3):
            page.add_text(long_text if row else "Heading", 60, 700 - row * 15, font_size=10)
            page.add_text(str(row), 40000, 700 - row * 15, font_size=10)
        tables = [block for block in page.extract_blocks() if block.kind == "table"]
        assert len(tables) == 1 and tables[0].rows[1][0] == long_text
        assert json.loads(page.export_blocks_json())["blocks"][0]["rows"][1][0] == long_text


def test_crop_rotation_and_paragraph_gap():
    with Document() as document:
        page = document.pages.add()
        page.add_text("One line", 60, 700, font_size=10)
        page.add_text("Next line", 60, 686, font_size=10)
        assert len(page.extract_blocks()) == 1
        assert len(page.extract_blocks(paragraph_gap=1)) == 2
        page.crop_box = (0, 690, 300, 750)
        page.rotation = 90
        (block,) = page.extract_blocks()
        assert block.text == "One line" and (block.x, block.y) == (60, 700)
        assert page.extract_blocks(clip_to_page=False)[0].text == "One line Next line"


def test_figures_are_opt_in_with_detached_png_and_base64(monkeypatch):
    png = RasterizedPage(1, 1, b"\xff\x00\x00").to_png()
    with Document() as document:
        page = document.pages.add()
        page.add_image(png, 60, 500, width=30, height=20, name="Picture")
        blocks = page.extract_blocks(include_images=True)
        (figure,) = blocks
        assert figure.kind == "figure" and figure.alt == "Picture"
        assert (figure.x, figure.y) == (75, 510)
        assert figure.image.startswith(b"\x89PNG\r\n\x1a\n")
        data_uri = figure.to_dict()["image"]
        assert data_uri.startswith("data:image/png;base64,")
        assert base64.b64decode(data_uri.partition(",")[2]) == figure.image
        payload = json.loads(document.export_blocks_json(include_images=True))
        assert payload["blocks"] == [figure.to_dict()]

        def no_decode(*args):
            raise AssertionError("Images must not be decoded unless requested")

        monkeypatch.setattr("aspose_pdf.engine.text_export._figure_png", no_decode)
        assert page.extract_blocks() == []


@pytest.mark.parametrize("pages", [0, "0", b"0", bytearray(b"0"), [True], [0.0], ["0"], [-1], [1]])
def test_invalid_page_selections_fail_before_decoding(pages, monkeypatch):
    with Document() as document:
        document.pages.add()

        def no_decode(*args):
            raise AssertionError("Invalid input must fail before decoding")

        monkeypatch.setattr(document._engine_pdf, "get_page_content", no_decode)
        with pytest.raises(PdfValidationException):
            document.extract_blocks(pages=pages)
        with pytest.raises(PdfValidationException):
            document.export_blocks_json(pages=[0, 10])


@pytest.mark.parametrize("options", [
    {"include_images": 1}, {"clip_to_page": None}, {"paragraph_gap": 0},
    {"paragraph_gap": -1}, {"paragraph_gap": float("inf")},
    {"paragraph_gap": float("nan")}, {"paragraph_gap": True},
    {"paragraph_gap": "2"}, {"paragraph_gap": object()},
])
def test_options_are_validated_on_empty_inputs(options):
    with Document() as document:
        with pytest.raises(PdfValidationException):
            document.extract_blocks(**options)
        with pytest.raises(PdfValidationException):
            document.export_blocks_json(**options)


def test_empty_disposed_and_stale_pages():
    with Document() as document:
        assert document.extract_blocks() == []
        assert json.loads(document.export_blocks_json()) == {"schema_version": 1, "blocks": []}
        page = document.pages.add()
        assert page.extract_blocks() == []
        document.pages.delete(0)
        with pytest.raises(PdfValidationException):
            page.extract_blocks()
    for call in (document.extract_blocks, document.export_blocks_json, page.extract_blocks,
                 page.export_blocks_json):
        with pytest.raises(AsposePdfException):
            call()


def test_limits_bound_unbounded_selection_and_output():
    limits = PdfLoadLimits(max_container_items=4)
    with Document(limits=limits) as document:
        document.pages.add()
        with pytest.raises(PdfResourceLimitException, match="page selection"):
            document.extract_blocks(pages=repeat(0))
    with Document(limits=PdfLoadLimits(max_total_decoded_bytes=5)) as document:
        document.pages.add().add_text("Too much text", 60, 700)
        with pytest.raises(PdfResourceLimitException):
            document.extract_blocks()


def test_streaming_only_decodes_selected_pages_and_does_not_change_source(tmp_path, monkeypatch):
    path = tmp_path / "source.pdf"
    with Document() as document:
        for number in range(3):
            document.pages.add().add_text(f"Page {number}", 60, 700)
        document.encrypt("user", "owner")
        document.save(path)
    before = path.read_bytes()
    with Document.open_streaming(path, password="user") as document:
        read = document._engine_pdf.get_page_content
        seen = []

        def selected(index):
            seen.append(index)
            return read(index)

        monkeypatch.setattr(document._engine_pdf, "get_page_content", selected)
        assert [b.text for b in document.extract_blocks(pages=[1])] == ["Page 1"]
        assert seen == [1]
        assert document._engine_pdf._extracted_text is None
        assert document._engine_pdf.page_contents == []
    assert path.read_bytes() == before


def test_json_byte_limits_cover_escaping_and_leave_destinations_untouched(tmp_path):
    with Document() as source:
        source.pages.add().add_text('Quoted "name" and café', 60, 700)
        original = _bytes(source)
        expected = source.export_blocks_json()
        assert source.extract_blocks()[0].text == 'Quoted "name" and café'
        assert _bytes(source) == original
    target = tmp_path / "existing.json"
    target.write_bytes(b"previous")
    for limit in (len(expected) - 1, len(expected)):
        with Document(original, limits=PdfLoadLimits(max_codec_work_bytes=limit)) as document:
            stream = io.BytesIO(b"previous")
            if limit < len(expected):
                for destination in (target, stream):
                    with pytest.raises(PdfResourceLimitException, match="block JSON bytes"):
                        document.export_blocks_json(destination)
                assert target.read_bytes() == stream.getvalue() == b"previous"
            else:
                assert document.export_blocks_json(target) == expected
    assert target.read_bytes() == expected


def test_json_serialization_bounds_long_strings_and_chunked_base64():
    from aspose_pdf.block_extraction import to_json
    from aspose_pdf.load_limits import _LoadBudget

    image = bytes(range(256)) * 150
    block = ExtractedBlock(
        0, "figure", 3, 4, alt='café "quoted" \\ \n' * 5000, image=image,
    )
    encoded = to_json([block], _LoadBudget())
    assert json.loads(encoded)["blocks"] == [block.to_dict()]
    with pytest.raises(PdfResourceLimitException):
        to_json([block], _LoadBudget(PdfLoadLimits(max_codec_work_bytes=100)))


def test_invalid_overwrite_never_decodes_content(monkeypatch):
    with Document() as document:
        document.pages.add()

        def no_decode(*args):
            raise AssertionError("Invalid options must fail before reading pages")

        monkeypatch.setattr(document._engine_pdf, "get_page_content", no_decode)
        with pytest.raises(PdfValidationException, match="overwrite"):
            document.export_blocks_json(overwrite=1)


@pytest.mark.parametrize("options", [
    {"page_index": True}, {"page_index": -1}, {"kind": "unknown"},
    {"x": float("nan")}, {"y": float("inf")}, {"x": True},
    {"x": 10**1000},
    {"text": None}, {"alt": 2}, {"items": "text"}, {"items": [1]},
    {"rows": [["a"], ["a", "b"]]}, {"column_spans": [[2]]},
    {"ordered": 1}, {"level": True}, {"level": 1}, {"image": bytearray(b"png")},
])
def test_invalid_public_snapshots_are_rejected(options):
    values = {"page_index": 0, "kind": "paragraph", "x": 0, "y": 0, **options}
    with pytest.raises(PdfValidationException):
        ExtractedBlock(**values)


def test_mutable_constructor_inputs_are_snapshotted():
    rows, items, spans = [["a", ""]], ["one"], [[2, 1]]
    block = ExtractedBlock(0, "table", 0, 0, rows=rows, items=items, column_spans=spans)
    rows[0][0], items[0], spans[0][0] = "changed", "changed", 1
    assert block.rows == (("a", ""),) and block.items == ("one",)
    assert block.column_spans == ((2, 1),)


def test_sparse_table_cells_are_bounded_before_padding():
    with Document(limits=PdfLoadLimits(max_container_items=100)) as document:
        page = document.pages.add()
        for row in range(20):
            for column in (range(10) if row < 3 else (0, 9)):
                page.add_text("X", 60 + column * 20, 700 - row * 15, font_size=10)
        # There are only 64 text objects, but padding would create 200 cells.
        with pytest.raises(PdfResourceLimitException, match="export table cells"):
            document.extract_blocks()


def test_nested_forms_decode_their_own_fonts_and_transform_anchors(tmp_path):
    with Document() as source, Document() as middle, Document() as document:
        source.pages.add(size=(200, 200)).add_text(
            "café", 10, 100, font_size=10, font=load_substitute_sfnt("sans-regular"),
        )
        middle.pages.add(size=(300, 300)).add_stamp(PageStamp(
            source=source.pages[0], horizontal_alignment="Left", vertical_alignment="Bottom",
            x_indent=20, y_indent=30,
        ))
        page = document.pages.add(size=(400, 400))
        page.add_stamp(PageStamp(
            source=middle.pages[0], horizontal_alignment="Left", vertical_alignment="Bottom",
            x_indent=40, y_indent=50,
        ))
        # F1 on the page must not replace the nested form's embedded font.
        page.add_text("Page font", 10, 300, font_name="Courier", font_size=10)
        path = tmp_path / "nested.pdf"
        document.save(path)
    with Document.open_streaming(path) as document:
        blocks = document.extract_blocks()
        assert [(b.text, b.x, b.y) for b in blocks] == [
            ("Page font", 10, 300), ("café", 70, 180),
        ]
        assert json.loads(document.export_blocks_json())["blocks"] == [b.to_dict() for b in blocks]


def test_two_columns_are_extracted_in_reading_order_without_markup(monkeypatch):
    def no_markup(*args, **kwargs):
        raise AssertionError("Structured extraction must not generate markup")

    monkeypatch.setattr(Document, "to_html", no_markup)
    monkeypatch.setattr(Document, "to_markdown", no_markup)
    with Document() as document:
        page = document.pages.add()
        for row in range(5):
            page.add_text(f"Left line {row}", 60, 700 - row * 14, font_size=10)
            page.add_text(f"Right line {row}", 320, 700 - row * 14, font_size=10)
        blocks = page.extract_blocks()
        assert len(blocks) == 2
        assert blocks[0].text == " ".join(f"Left line {row}" for row in range(5))
        assert blocks[1].text == " ".join(f"Right line {row}" for row in range(5))
        assert [(b.x, b.y) for b in blocks] == [(60, 700), (320, 700)]


def test_horizontal_spans_and_blank_cells_survive_json():
    with Document() as document:
        page = document.pages.add()
        rows = (("Merged title", "", "C"), ("a", "b", "c"), ("d", "e", "f"))
        for row, cells in enumerate(rows):
            for column, text in enumerate(cells):
                if text:
                    page.add_text(text, 60 + column * 25, 700 - row * 15, font_size=10)
        (table,) = page.extract_blocks()
        assert table.rows == rows
        assert table.column_spans == ((2, 1, 1), (1, 1, 1), (1, 1, 1))
        payload = json.loads(page.export_blocks_json())["blocks"][0]
        assert payload["rows"] == [list(row) for row in rows]
        assert payload["column_spans"][0] == [2, 1, 1]

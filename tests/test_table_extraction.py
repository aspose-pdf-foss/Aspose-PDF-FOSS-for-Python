"""Direct text-table extraction and reusable list/CSV snapshots."""

from __future__ import annotations

import csv
import io
import zlib
from dataclasses import FrozenInstanceError

import pytest

from aspose_pdf import (
    Document,
    ExtractedTable,
    PdfLoadLimits,
    PdfResourceLimitException,
    Table,
)
from aspose_pdf.engine.std_font_data import load_substitute_sfnt
from aspose_pdf.exceptions import AsposePdfException, PdfValidationException

ROWS = [["Item", "Qty", "Price"], ["Widget", "2", "9.99"], ["Nut", "4", "0.50"]]


def _grid(page, rows=ROWS, *, y=700, xs=(60, 120, 180), font=None):
    for offset, row in enumerate(rows):
        for x, text in zip(xs, row):
            if text:
                page.add_text(text, x, y - offset * 15, font_size=10, font=font)


def _saved(document):
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _stream(content, entries=b""):
    return (
        b"<< " + entries + b" /Length %d >>\nstream\n" % len(content)
        + content + b"\nendstream"
    )


def _pdf(objects):
    """Build a minimal PDF for resource-scope and lazy-decoding regressions."""
    data = bytearray(b"%PDF-1.7\n")
    offsets = {}
    for number, value in sorted(objects.items()):
        offsets[number] = len(data)
        data += b"%d 0 obj\n" % number + value + b"\nendobj\n"
    start = len(data)
    size = max(objects) + 1
    data += b"xref\n0 %d\n" % size
    for number in range(size):
        data += (
            b"%010d 00000 n \n" % offsets[number]
            if number in offsets else b"0000000000 65535 f \n"
        )
    data += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (size, start)
    return bytes(data)


def _objects(content):
    return {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        3: b"<< /Type /Page /Parent 2 0 R /Contents 4 0 R >>",
        4: _stream(content),
        5: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }


def _content():
    return b"\n".join(
        f"BT /F1 10 Tf {x} {700 - i * 15} Td ({cell}) Tj ET".encode()
        for i, row in enumerate(ROWS)
        for x, cell in zip((60, 120, 180), row)
    )


@pytest.mark.parametrize("source", ["live", "bytes", "file", "streaming"])
def test_extract_table_from_public_text_api_and_pdf_round_trips(source, tmp_path):
    with Document() as original:
        _grid(original.pages.add())
        data = _saved(original)
        if source == "live":
            document = original
        elif source == "bytes":
            document = Document(data)
        else:
            path = tmp_path / "input.pdf"
            path.write_bytes(data)
            document = (Document.open_streaming if source == "streaming" else Document)(path)
        try:
            (table,) = document.extract_tables()
            assert isinstance(table, ExtractedTable)
            assert table.page_index == 0
            assert table.row_count == 3 and table.column_count == 3
            assert table.to_list() == ROWS
            assert document.pages[0].extract_tables() == [table]
            assert list(csv.reader(io.StringIO(table.to_csv(), newline=""))) == ROWS
        finally:
            if document is not original:
                document.dispose()


def test_authored_table_is_extracted_without_requiring_html_or_markdown(monkeypatch):
    def no_markup(*args, **kwargs):
        raise AssertionError("Table extraction must not convert through markup")

    monkeypatch.setattr(Document, "to_html", no_markup)
    monkeypatch.setattr(Document, "to_markdown", no_markup)
    with Document() as document:
        table = Table(column_widths=[60, 60, 60], font_size=10)
        for row in ROWS:
            table.add_row(row)
        document.pages.add().add_table(table, 60, 700)
        assert document.extract_tables()[0].to_list() == ROWS


def test_prose_lists_and_separate_tables_keep_their_boundaries():
    with Document() as document:
        page = document.pages.add()
        page.add_text("Inventory report", 60, 760, font_size=20)
        page.add_text("The table below lists available inventory.", 60, 730, font_size=10)
        _grid(page)
        page.add_text("- First inventory check", 70, 630, font_size=10)
        page.add_text("- Second inventory check", 70, 615, font_size=10)
        _grid(page, [["Part", "Count"], ["Bolt", "20"]], y=580, xs=(60, 120))
        page.add_text("End of the inventory report.", 60, 530, font_size=10)
        tables = document.extract_tables()
        assert [table.to_list() for table in tables] == [ROWS, [["Part", "Count"], ["Bolt", "20"]]]


def test_page_selection_order_repeats_generator_and_original_page_indices():
    with Document() as document:
        for number in range(3):
            _grid(document.pages.add(), [["N", "V"], [str(number), "x"]], xs=(60, 85))
        tables = document.extract_tables(pages=(i for i in [2, 0, 2]))
        assert [table.page_index for table in tables] == [2, 0, 2]
        assert [table.rows[1][0] for table in tables] == ["2", "0", "2"]
        assert document.pages[1].extract_tables()[0].page_index == 1
        assert [table.page_index for table in document.extract_tables()] == [0, 1, 2]
        assert document.extract_tables(pages=[]) == []


@pytest.mark.parametrize("pages", [0, "0", b"0", [True], [False], [0.0], ["0"], [None], [-1], [1]])
def test_invalid_page_selection_is_rejected(pages):
    with Document() as document:
        _grid(document.pages.add())
        with pytest.raises(PdfValidationException):
            document.extract_tables(pages=pages)


def test_all_page_indices_are_validated_before_decoding(monkeypatch):
    with Document() as document:
        document.pages.add()

        def no_decode(*args):
            raise AssertionError("Invalid selection must be rejected before decoding")

        monkeypatch.setattr(document._engine_pdf, "get_page_content", no_decode)
        with pytest.raises(PdfValidationException, match="out of range"):
            document.extract_tables(pages=[0, 100])
        assert document.extract_tables(pages=[]) == []


def test_no_tables_in_empty_document_empty_page_or_ordinary_text_columns():
    with Document() as document:
        assert document.extract_tables() == []
        page = document.pages.add()
        assert page.extract_tables() == []
        for offset in range(5):
            page.add_text(f"A prose line {offset}.", 60, 700 - 15 * offset, font_size=10)
        assert document.extract_tables() == []
        for offset in range(5):
            page.add_text(f"More prose here {offset}.", 320, 700 - 15 * offset, font_size=10)
        assert document.extract_tables() == []


def test_blank_cells_are_padded_without_shifting_following_columns():
    rows = [["A", "B", "C"], ["a", "", "c"], ["d", "e", "f"]]
    with Document() as document:
        _grid(document.pages.add(), rows, xs=(60, 85, 110))
        (table,) = document.extract_tables()
        assert table.to_list() == rows
        assert table.column_spans == ((1, 1, 1),) * 3
        assert list(csv.reader(io.StringIO(table.to_csv()))) == rows


def test_horizontal_merge_keeps_covered_columns_empty():
    rows = [["A merged heading", "", "C"], ["a", "b", "c"], ["d", "e", "f"]]
    with Document() as document:
        _grid(document.pages.add(), rows)
        (table,) = document.extract_tables()
        assert table.to_list() == rows
        assert table.column_spans[0] == (2, 1, 1)
        assert list(csv.reader(io.StringIO(table.to_csv()))) == rows


def test_crop_box_filters_anchors_and_can_be_disabled():
    with Document() as document:
        page = document.pages.add()
        _grid(page)
        page.crop_box = (0, 0, 595, 660)
        assert page.extract_tables() == []
        assert document.extract_tables() == []
        assert page.extract_tables(clip_to_page=False)[0].to_list() == ROWS


@pytest.mark.parametrize("value", [0, 1, "yes", None])
def test_clip_to_page_requires_a_boolean(value):
    with Document() as document:
        page = document.pages.add()
        with pytest.raises(PdfValidationException, match="clip_to_page"):
            page.extract_tables(clip_to_page=value)


def test_snapshots_survive_document_edits_and_disposal():
    with Document() as document:
        page = document.pages.add()
        _grid(page)
        (table,) = page.extract_tables()
        copy = table.to_list()
        copy[1][0] = "changed"
        copy.append(["extra"])
        page.add_text("later content", 60, 600)
    assert table.to_list() == ROWS
    assert "Widget" in table.to_csv()
    with pytest.raises(FrozenInstanceError):
        table.page_index = 2
    with pytest.raises(TypeError):
        table.rows[0][0] = "changed"
    with pytest.raises(AsposePdfException, match="disposed"):
        document.extract_tables()
    with pytest.raises(AsposePdfException, match="disposed"):
        page.extract_tables()


def test_extraction_does_not_modify_the_source():
    with Document() as document:
        _grid(document.pages.add())
        before = _saved(document)
        document.extract_tables()
        assert _saved(document) == before


def test_long_cell_text_is_not_truncated_to_markup_limit():
    value = "x" * 5000 + "END"
    with Document() as document:
        _grid(document.pages.add(), [["A", "B"], [value, "z"]], xs=(60, 120))
        (table,) = document.extract_tables()
        assert table.rows[1][0] == value
        assert table.column_spans[1] == (1, 1)
        assert value in table.to_csv()
        assert value not in document.to_html()
        assert "<td>z</td>" in document.to_html()


def test_unicode_text_is_decoded_from_an_embedded_font_after_reload():
    rows = [["Key", "Value"], ["café", "Ångström"]]
    with Document() as document:
        _grid(document.pages.add(), rows, xs=(60, 120), font=load_substitute_sfnt("sans-regular"))
        with Document(_saved(document)) as reopened:
            (table,) = reopened.extract_tables()
            assert table.to_list() == rows
            assert "Ångström" in table.to_csv()


def test_cell_whitespace_is_normalized_without_markup_escaping():
    rows = [["Key", "Value"], ["a  b", "<x>&y"]]
    with Document() as document:
        _grid(document.pages.add(), rows, xs=(60, 85))
        assert document.extract_tables()[0].to_list() == [["Key", "Value"], ["a b", "<x>&y"]]


def test_csv_special_characters_are_preserved_when_extracted_from_pdf():
    rows = [["Name", "Amount"], ['A, "B"', "001"]]
    with Document() as document:
        _grid(document.pages.add(), rows, xs=(60, 120))
        with Document(_saved(document)) as reopened:
            (table,) = reopened.extract_tables()
            assert table.to_list() == rows
            assert list(csv.reader(io.StringIO(table.to_csv()))) == rows


def test_transformed_nested_forms_keep_their_own_font_scope():
    objects = _objects(b"q 1 0 0 1 0 -100 cm /Outer Do Q")
    objects[3] = b"<< /Type /Page /Parent 2 0 R /Contents 4 0 R /Resources << " \
        b"/Font << /F1 5 0 R >> /XObject << /Outer 6 0 R >> >> >>"
    objects[6] = _stream(
        b"/Inner Do", b"/Subtype /Form /BBox [0 0 595 842] /Matrix [1 0 0 1 0 50] "
        b"/Resources << /XObject << /Inner 7 0 R >> >>",
    )
    objects[7] = _stream(
        _content(), b"/Subtype /Form /BBox [0 0 595 842] "
        b"/Resources << /Font << /F1 8 0 R >> >>",
    )
    objects[8] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /ToUnicode 9 0 R >>"
    objects[9] = _stream(
        b"begincmap 1 begincodespacerange <00> <FF> endcodespacerange "
        b"1 beginbfrange <20> <7e> <0020> endbfrange "
        b"1 beginbfchar <49> <041f04400438043204350442> endbfchar endcmap"
    )
    with Document(_pdf(objects)) as document:
        (table,) = document.extract_tables()
        assert table.rows[0] == ("Приветtem", "Qty", "Price")
        assert table.rows[1:] == tuple(tuple(row) for row in ROWS[1:])


def test_selected_streaming_page_does_not_decode_other_pages(tmp_path):
    objects = _objects(_content())
    objects[2] = objects[2].replace(b"[3 0 R] /Count 1", b"[3 0 R 6 0 R] /Count 2")
    objects[6] = b"<< /Type /Page /Parent 2 0 R /Contents 7 0 R >>"
    objects[7] = _stream(zlib.compress(b" " * 20_000), b"/Filter /FlateDecode")
    path = tmp_path / "streaming.pdf"
    path.write_bytes(_pdf(objects))
    with Document.open_streaming(path, limits=PdfLoadLimits(max_decoded_stream_bytes=1000)) as document:
        assert document.extract_tables(pages=[0])[0].to_list() == ROWS
        with pytest.raises(PdfResourceLimitException, match="max_decoded_stream_bytes"):
            document.extract_tables(pages=[1])


def test_images_and_unused_forms_are_not_decoded_or_ocr_processed(tmp_path):
    objects = _objects(_content() + b" /Im Do")
    objects[3] = b"<< /Type /Page /Parent 2 0 R /Contents 4 0 R /Resources << " \
        b"/Font << /F1 5 0 R >> /XObject << /Im 6 0 R /Unused 7 0 R >> >> >>"
    objects[6] = _stream(
        zlib.compress(b" " * 20_000), b"/Subtype /Image /Width 1 /Height 1 "
        b"/BitsPerComponent 8 /ColorSpace /DeviceGray /Filter /FlateDecode",
    )
    objects[7] = _stream(
        zlib.compress(b" " * 20_000), b"/Subtype /Form /BBox [0 0 595 842] /Filter /FlateDecode",
    )
    path = tmp_path / "images.pdf"
    path.write_bytes(_pdf(objects))
    with Document.open_streaming(
        path, limits=PdfLoadLimits(max_decoded_stream_bytes=1000, max_image_pixels=1)
    ) as document:
        assert document.extract_tables()[0].to_list() == ROWS
    objects[4] = _stream(b"/Im Do")
    path.write_bytes(_pdf(objects))
    with Document.open_streaming(
        path, limits=PdfLoadLimits(max_decoded_stream_bytes=1000, max_image_pixels=1)
    ) as document:
        assert document.extract_tables() == []


def test_invoked_form_content_limit_is_not_silently_swallowed(tmp_path):
    objects = _objects(b"/Fm Do")
    objects[3] = b"<< /Type /Page /Parent 2 0 R /Contents 4 0 R /Resources << " \
        b"/XObject << /Fm 6 0 R >> >> >>"
    objects[6] = _stream(
        zlib.compress(b" " * 20_000), b"/Subtype /Form /BBox [0 0 595 842] /Filter /FlateDecode",
    )
    path = tmp_path / "form-limit.pdf"
    path.write_bytes(_pdf(objects))
    with Document.open_streaming(path, limits=PdfLoadLimits(max_decoded_stream_bytes=1000)) as document:
        with pytest.raises(PdfResourceLimitException, match="max_decoded_stream_bytes"):
            document.extract_tables()


@pytest.mark.parametrize("delimiter", [",", ";", "\t", "|"])
def test_csv_quotes_delimiters_quotes_newlines_unicode_and_empty_cells(delimiter):
    rows = [
        ["name", "value", "empty"],
        ['a,b;c\td|e "quoted"', "Привет\nsecond\rthird\r\nfourth", ""],
        ["001", "=SUM(A1:A2)", "<x>&y"],
    ]
    table = ExtractedTable(2, rows)
    csv_text = table.to_csv(delimiter=delimiter)
    assert list(csv.reader(io.StringIO(csv_text, newline=""), delimiter=delimiter)) == rows
    assert csv_text.endswith("\r\n")
    assert '""quoted""' in csv_text


@pytest.mark.parametrize("delimiter", [None, 1, "", "::", '"', "\r", "\n", "\x00"])
def test_csv_rejects_invalid_delimiters(delimiter):
    with pytest.raises(PdfValidationException, match="delimiter"):
        ExtractedTable(0, [["x"]]).to_csv(delimiter=delimiter)


def test_csv_save_utf8_bom_directories_and_overwrite_protection(tmp_path):
    table = ExtractedTable(0, [["Key", "Value"], ["café", "Привет"]])
    path = tmp_path / "nested" / "table.csv"
    assert table.save_csv(path) == path
    assert path.read_bytes() == table.to_csv().encode("utf-8")
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        table.save_csv(path, overwrite=False)
    assert path.read_bytes() == before
    table.save_csv(path, delimiter=";", encoding="utf-8-sig")
    assert path.read_bytes() == table.to_csv(delimiter=";").encode("utf-8-sig")


def test_csv_encoding_failure_preserves_existing_file_and_creates_no_directories(tmp_path):
    table = ExtractedTable(0, [["Привет"]])
    path = tmp_path / "table.csv"
    path.write_bytes(b"original")
    with pytest.raises(UnicodeEncodeError):
        table.save_csv(path, encoding="ascii")
    assert path.read_bytes() == b"original"
    with pytest.raises(PdfValidationException):
        table.save_csv(tmp_path / "new" / "table.csv", delimiter="invalid")
    assert not (tmp_path / "new").exists()


def test_csv_write_failure_preserves_existing_file_and_cleans_staging(tmp_path, monkeypatch):
    import aspose_pdf.engine.file_output as output

    path = tmp_path / "table.csv"
    path.write_bytes(b"original")

    def fail_replace(*args):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(output.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated disk failure"):
        ExtractedTable(0, [["new"]]).save_csv(path)
    assert path.read_bytes() == b"original"
    assert list(tmp_path.iterdir()) == [path]


def test_empty_snapshot_and_mutable_constructor_inputs():
    table = ExtractedTable(0, [])
    assert table.to_list() == [] and table.to_csv() == ""
    assert table.row_count == table.column_count == 0
    rows = [["a", "b"]]
    spans = [[1, 1]]
    table = ExtractedTable(0, rows, spans)
    rows[0][0] = "changed"
    spans[0][0] = 2
    assert table.rows == (("a", "b"),)
    assert table.column_spans == ((1, 1),)


@pytest.mark.parametrize("arguments", [
    {"page_index": -1, "rows": []},
    {"page_index": True, "rows": []},
    {"page_index": 0.0, "rows": []},
    {"page_index": 0, "rows": "abc"},
    {"page_index": 0, "rows": ["abc"]},
    {"page_index": 0, "rows": None},
    {"page_index": 0, "rows": [[1]]},
    {"page_index": 0, "rows": [["a"], ["a", "b"]]},
    {"page_index": 0, "rows": [["a"]], "column_spans": [[1, 1]]},
    {"page_index": 0, "rows": [["a"]], "column_spans": [[0]]},
    {"page_index": 0, "rows": [["a"]], "column_spans": [[2]]},
    {"page_index": 0, "rows": [["a"]], "column_spans": [[True]]},
    {"page_index": 0, "rows": [["a", "b"]], "column_spans": [[2, 1]]},
    {"page_index": 0, "rows": [["a", "", ""]], "column_spans": [[3, 2, 1]]},
    {"page_index": 0, "rows": [["a"]], "column_spans": None},
])
def test_invalid_snapshot_data_is_rejected(arguments):
    with pytest.raises(PdfValidationException):
        ExtractedTable(**arguments)

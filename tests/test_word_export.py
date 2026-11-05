"""Word reports preserve source geometry, selection and output safety."""

from __future__ import annotations

import csv
import io
import json
from itertools import repeat

import pytest

from aspose_pdf import Document, PdfLoadLimits, TextWord
from aspose_pdf.engine.word_export import to_json
from aspose_pdf.exceptions import (
    AsposePdfException,
    PdfResourceLimitException,
    PdfValidationException,
)
from aspose_pdf.load_limits import _LoadBudget


def _bytes(document):
    with io.BytesIO() as output:
        document.save(output)
        return output.getvalue()


def _records(words):
    return [
        dict(
            page_index=word.page_index,
            text=word.text,
            rect=list(word.rect),
            quad=[list(point) for point in word.quad],
        )
        for word in words
    ]


def _report(target, export_name, **options):
    data = getattr(target, export_name)(**options)
    if export_name == "export_words_json":
        report = json.loads(data)
        assert report["schema_version"] == 1
        return report["words"]
    return [
        dict(
            page_index=int(row["page_number"]),
            text=row["text"],
            rect=[float(row[key]) for key in ("x0", "y0", "x1", "y1")],
            quad=json.loads(row["quad"]),
        )
        for row in csv.DictReader(io.StringIO(data.decode("utf-8")))
    ]


@pytest.fixture(params=["export_words_json", "export_words_csv"])
def export_name(request):
    return request.param


@pytest.fixture
def source():
    with Document() as document:
        for number in range(1, 4):
            document.pages.add().add_text(f"Page{number} café", 72, 700)
        yield document


def test_schema_unicode_escaping_geometry_and_source_preservation(source):
    source.pages[1].add_text('"Quoted" \\path', 72, 650)
    before = _bytes(source)
    words = source.extract_words()
    data = source.export_words_json()
    assert data.endswith(b"\n") and "café".encode() in data
    assert json.loads(data) == dict(schema_version=1, words=_records(words))
    assert [record["text"] for record in json.loads(data)["words"]][:4] == [
        "Page1",
        "café",
        '"Quoted"',
        "\\path",
    ]
    assert source.export_words_json() == data
    assert _bytes(source) == before


def test_page_order_repeats_and_original_page_numbers(source, export_name):
    records = _report(source, export_name, pages=(page for page in [3, 1, 3]))
    assert records == _records(source.extract_words(pages=[3, 1, 3]))
    assert [word["page_index"] for word in records] == [
        3,
        3,
        1,
        1,
        3,
        3,
    ]
    assert _report(source.pages[2], export_name) == _records(
        source.pages[2].extract_words()
    )


@pytest.mark.parametrize("selection", [None, [], ()])
def test_empty_documents_pages_and_selections_have_no_records(selection, export_name):
    with Document() as document:
        assert _report(document, export_name, pages=selection) == []
        document.pages.add()
        assert _report(document, export_name, pages=selection) == []


def test_clipping_selects_complete_words_with_unchanged_bounds(source, export_name):
    word = source.pages[1].extract_words()[0]
    x0, y0, x1, y1 = word.rect
    clip = (x0 + (x1 - x0) / 3, y0, x0 + (x1 - x0) / 2, y1)
    assert _report(source, export_name, pages=[1], clip=clip) == _records([word])
    assert getattr(source.pages[1], export_name)(clip=clip) == getattr(
        source, export_name
    )(pages=[1], clip=clip)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_transformed_quads_and_pdf_coordinate_bounds_are_preserved(
    rotation, export_name
):
    with Document() as document:
        page = document.pages.add()
        page.add_text("Seed", 72, 700)
        document._engine_pdf._set_page_content(
            0, b"q 0 1 -1 0 400 0 cm BT /F1 10 Tf 72 200 Td (Words here) Tj ET Q"
        )
        page.rotation = rotation
        page.crop_box = (10, 10, 500, 780)
        records = _report(page, export_name)
        assert records == _records(page.extract_words())
        assert len(records) == 2
        assert records[0]["quad"][0][0] == records[0]["quad"][1][0]
        assert records[0]["quad"][0][1] < records[0]["quad"][1][1]
        assert page.rotation == rotation and page.crop_box == (10, 10, 500, 780)


def test_artifacts_hidden_layers_and_invisible_ocr_follow_word_extraction(export_name):
    with Document() as document:
        page = document.pages.add()
        page.add_text("Seed", 72, 700)
        document._engine_pdf._set_page_content(
            0,
            b"/Artifact BMC BT /F1 10 Tf 72 700 Td (Header) Tj ET EMC "
            b"BT /F1 10 Tf 72 650 Td (Body) Tj ET "
            b"BT /F1 10 Tf 3 Tr 72 600 Td (OCR) Tj ET",
        )
        layer = document.layers.add("Hidden", visible=False)
        with page.layer(layer):
            page.add_text("Hidden", 72, 550)
        assert [record["text"] for record in _report(page, export_name)] == [
            "Header",
            "Body",
            "OCR",
        ]
        records = _report(page, export_name, skip_artifacts=True)
        assert [record["text"] for record in records] == [
            "Body",
            "OCR",
        ]
        assert records == _records(page.extract_words(skip_artifacts=True))


def test_file_and_binary_stream_outputs_short_writes_and_overwrite(
    source, tmp_path, export_name
):
    export = getattr(source, export_name)
    expected = export()
    path = tmp_path / "new" / "words.json"
    assert export(path) == path.read_bytes() == expected
    with pytest.raises(FileExistsError):
        export(path, overwrite=False)
    assert path.read_bytes() == expected

    class Short(io.BytesIO):
        def write(self, data):
            return super().write(data[:3])

    with Short(b"prefix") as output:
        output.seek(6)
        assert export(output) == expected
        assert output.getvalue() == b"prefix" + expected and not output.closed


def test_later_extraction_failure_leaves_destinations_untouched(
    source, tmp_path, monkeypatch, export_name
):
    original = source._engine_pdf.get_page_content

    def content(index):
        if index == 1:
            raise PdfValidationException("Damaged later page")
        return original(index)

    monkeypatch.setattr(source._engine_pdf, "get_page_content", content)
    path = tmp_path / "words.json"
    path.write_bytes(b"Keep")
    output = io.BytesIO(b"Keep")
    for destination in (path, output):
        with pytest.raises(PdfValidationException, match="Damaged later page"):
            getattr(source, export_name)(destination)
        assert path.read_bytes() == output.getvalue() == b"Keep"
    assert not output.closed


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("encrypted", [False, True])
def test_pdf_round_trip_selected_page_only_and_source_state(
    source, tmp_path, monkeypatch, streaming, encrypted, export_name
):
    path = tmp_path / "source.pdf"
    if encrypted:
        source.encrypt(
            user_password="secret", owner_password="owner", algorithm="AES-256"
        )
    source.save(path)
    before = path.read_bytes()
    with (Document.open_streaming if streaming else Document)(
        path, password="secret" if encrypted else None
    ) as document:
        engine = document._engine_pdf
        protected = document.is_encrypted
        original = engine.get_page_content
        engine._page_text_cursor = 1
        reads = []

        def content(index):
            assert index == 1
            reads.append(index)
            return original(index)

        monkeypatch.setattr(engine, "get_page_content", content)
        records = _report(document, export_name, pages=[2])
        assert [record["text"] for record in records] == ["Page2", "café"]
        assert [record["page_index"] for record in records] == [2, 2]
        assert reads == [1]
        assert document.is_encrypted is protected and engine._page_text_cursor == 1
        assert engine._extracted_text is None
        if streaming:
            assert engine.page_contents == []
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "options",
    [
        {"pages": "1"},
        {"pages": [True]},
        {"pages": [0]},
        {"pages": [1, 4]},
        {"pages": [1, "2"]},
        {"clip": []},
        {"clip": (0, 0, 0, 1)},
        {"clip": (0, 0, float("inf"), 1)},
        {"skip_artifacts": None},
        {"skip_artifacts": 1},
        {"overwrite": 1},
    ],
)
def test_invalid_options_precede_content_reads_and_output(
    source, monkeypatch, options, export_name
):
    def unexpected(*args, **kwargs):
        raise AssertionError("Invalid options must not decode page content")

    monkeypatch.setattr(source._engine_pdf, "get_page_content", unexpected)
    output = io.BytesIO(b"Keep")
    with pytest.raises(PdfValidationException):
        getattr(source, export_name)(output, **options)
    assert output.getvalue() == b"Keep"


def test_empty_selection_validates_options_and_does_not_decode(
    source, monkeypatch, export_name
):
    def unexpected(*args, **kwargs):
        raise AssertionError("Empty selections must not decode page content")

    monkeypatch.setattr(source._engine_pdf, "get_page_content", unexpected)
    export = getattr(source, export_name)
    assert _report(source, export_name, pages=[]) == []
    with pytest.raises(PdfValidationException):
        export(pages=[], skip_artifacts=None)
    with pytest.raises(PdfValidationException):
        export(pages=[], clip=(0, 0, 0, 1))


@pytest.mark.parametrize(
    "limit_name",
    ["max_codec_work_bytes", "max_decoded_stream_bytes", "max_total_decoded_bytes"],
)
def test_exact_encoded_output_limits_preserve_destinations(
    source, tmp_path, limit_name
):
    expected = source.export_words_json()
    path = tmp_path / "words.json"
    path.write_bytes(b"Keep")
    output = io.BytesIO(b"Keep")
    source._load_limits = PdfLoadLimits(**{limit_name: len(expected) - 1})
    for destination in (path, output):
        with pytest.raises(PdfResourceLimitException, match=limit_name):
            source.export_words_json(destination)
        assert path.read_bytes() == output.getvalue() == b"Keep"
    source._load_limits = PdfLoadLimits(**{limit_name: len(expected)})
    assert source.export_words_json(path) == path.read_bytes() == expected


def test_selection_and_cumulative_word_counts_obey_resource_limits(source, export_name):
    export = getattr(source, export_name)
    source._load_limits = PdfLoadLimits(max_container_items=3)
    with pytest.raises(PdfResourceLimitException, match="max_container_items"):
        export(pages=repeat(1))
    source._load_limits = PdfLoadLimits(max_container_items=64)
    source._engine_pdf._load_budget.limits = source._load_limits
    with pytest.raises(PdfResourceLimitException, match="extracted document words"):
        export(pages=[1] * 33)


def test_long_escaped_strings_and_invalid_unicode_are_handled_by_serializer():
    rect = (0, 0, 10, 10)
    quad = ((0, 0), (10, 0), (10, 10), (0, 10))
    text = 'café"\\' * 5000
    word = TextWord(0, text, rect, quad)
    data = to_json([word], _LoadBudget())
    assert json.loads(data)["words"][0]["text"] == text
    with pytest.raises(PdfResourceLimitException, match="word JSON bytes"):
        to_json([word], _LoadBudget(PdfLoadLimits(max_codec_work_bytes=len(data) - 1)))
    with pytest.raises(PdfValidationException, match="UTF-8 JSON"):
        to_json([TextWord(0, "\ud800", rect, quad)], _LoadBudget())


def test_disposed_documents_stale_pages_and_stalled_streams_are_rejected(
    source, export_name
):
    class Stalled:
        def write(self, data):
            return 0

    with pytest.raises(AsposePdfException, match="Stream write"):
        getattr(source, export_name)(Stalled())
    page = source.pages[3]
    source.pages.delete(3)
    with pytest.raises(PdfValidationException, match="out of range"):
        getattr(page, export_name)()
    source.dispose()
    with pytest.raises(AsposePdfException, match="disposed"):
        getattr(source, export_name)()
    with pytest.raises(AsposePdfException, match="disposed"):
        getattr(page, export_name)()


def test_csv_schema_unicode_quoting_and_source_preservation(source):
    source.pages[1].add_text('"Quoted",word \\path', 72, 650)
    before = _bytes(source)
    data = source.export_words_csv()
    rows = list(csv.DictReader(io.StringIO(data.decode("utf-8"))))
    assert list(rows[0]) == ["page_number", "text", "x0", "y0", "x1", "y1", "quad"]
    assert [row["text"] for row in rows][:4] == [
        "Page1",
        "café",
        '"Quoted",word',
        "\\path",
    ]
    assert _report(source, "export_words_csv") == _records(source.extract_words())
    assert source.export_words_csv() == data and data.endswith(b"\r\n")
    assert _bytes(source) == before


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16"])
def test_csv_custom_delimiter_and_encoding_use_one_encoder_state(source, encoding):
    source.pages[1].add_text("semi;colon", 72, 650)
    data = source.export_words_csv(delimiter=";", encoding=encoding)
    rows = list(csv.DictReader(io.StringIO(data.decode(encoding)), delimiter=";"))
    assert [row["text"] for row in rows][:3] == ["Page1", "café", "semi;colon"]
    if encoding == "utf-8-sig":
        assert data.startswith(b"\xef\xbb\xbf") and data.count(b"\xef\xbb\xbf") == 1
    assert source.pages[1].export_words_csv(
        delimiter=";", encoding=encoding
    ) == source.export_words_csv(pages=[1], delimiter=";", encoding=encoding)


@pytest.mark.parametrize(
    "options,error",
    [
        ({"delimiter": ""}, PdfValidationException),
        ({"delimiter": "\n"}, PdfValidationException),
        ({"delimiter": '"'}, PdfValidationException),
        ({"delimiter": None}, TypeError),
        ({"encoding": "missing-codec"}, PdfValidationException),
        ({"encoding": None}, TypeError),
    ],
)
def test_invalid_csv_options_precede_extraction(source, monkeypatch, options, error):
    monkeypatch.setattr(
        source._engine_pdf,
        "get_page_content",
        lambda *a: pytest.fail("Unexpected content read"),
    )
    with io.BytesIO(b"Keep") as output:
        with pytest.raises(error):
            source.export_words_csv(output, **options)
        assert output.getvalue() == b"Keep"


@pytest.mark.parametrize(
    "limit", ["max_decoded_stream_bytes", "max_total_decoded_bytes"]
)
def test_csv_output_limits_and_encoding_failure_preserve_destinations(
    source, tmp_path, limit
):
    expected = source.export_words_csv()
    path = tmp_path / "words.csv"
    path.write_bytes(b"Keep")
    with io.BytesIO(b"Keep") as output:
        for destination in (path, output):
            with pytest.raises(PdfValidationException, match="encoded as CSV"):
                source.export_words_csv(destination, encoding="ascii")
            assert path.read_bytes() == output.getvalue() == b"Keep"
        source._load_limits = PdfLoadLimits(**{limit: len(expected) - 1})
        for destination in (path, output):
            with pytest.raises(PdfResourceLimitException, match=limit):
                source.export_words_csv(destination)
            assert path.read_bytes() == output.getvalue() == b"Keep"
    source._load_limits = PdfLoadLimits(**{limit: len(expected)})
    assert source.export_words_csv(path) == path.read_bytes() == expected

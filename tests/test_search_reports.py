"""Search reports retain context, source offsets and reliable geometry."""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import FrozenInstanceError, asdict
from itertools import repeat

import pytest

from aspose_pdf import Document, PdfLoadLimits, TextSearchMatch
from aspose_pdf.engine.std_font_data import load_substitute_sfnt
from aspose_pdf.exceptions import (
    AsposePdfException,
    PdfResourceLimitException,
    PdfValidationException,
)


def _bytes(document):
    with io.BytesIO() as output:
        document.save(output)
        return output.getvalue()


def _rows(data, **options):
    return list(
        csv.DictReader(
            io.StringIO(data.decode(options.pop("encoding", "utf-8"))), **options
        )
    )


@pytest.fixture
def document():
    with Document() as source:
        source.pages.add().add_text("Before Invoice 123 after Invoice 456 end", 72, 700)
        source.pages.add().add_text("Invoice 789", 72, 700)
        source.pages.add().add_text("No matches here", 72, 700)
        yield source


def test_literal_context_offsets_geometry_and_immutable_detached_results(document):
    before = _bytes(document)
    matches = document.search_text("Invoice", context_chars=4)
    assert all(isinstance(match, TextSearchMatch) for match in matches)
    assert [match.page_index for match in matches] == [1, 1, 2]
    assert [(match.start, match.end) for match in matches] == [
        (7, 14),
        (25, 32),
        (0, 7),
    ]
    assert [(match.context_before, match.context_after) for match in matches] == [
        ("ore ", " 123"),
        ("ter ", " 456"),
        ("", " 789"),
    ]
    assert all(match.text == "Invoice" and match.rect is not None for match in matches)
    assert matches[0].rect[0] < matches[1].rect[0]
    assert len(matches[0].quads) == 1 and len(matches[0].quads[0]) == 4
    with pytest.raises(FrozenInstanceError):
        matches[0].text = "changed"
    document.pages[1].replace_text("Invoice", "Receipt")
    document.dispose()
    assert matches[0].text == "Invoice" and matches[0].context_after == " 123"
    with Document(before) as original:
        assert original.search_text("Invoice", context_chars=4) == matches


def test_json_and_csv_share_results_and_do_not_change_the_pdf(document):
    before = _bytes(document)
    matches = document.search_text("Invoice", context_chars=6)
    data = document.export_search_json("Invoice", context_chars=6)
    report = json.loads(data)
    assert report == {
        "schema_version": 1,
        "matches": json.loads(json.dumps([asdict(m) for m in matches])),
    }
    assert (
        data.endswith(b"\n")
        and document.export_search_json("Invoice", context_chars=6) == data
    )
    rows = _rows(document.export_search_csv("Invoice", context_chars=6))
    for row, match in zip(rows, matches, strict=True):
        assert int(row["page_number"]) == match.page_index
        assert row["text"] == match.text
        assert (int(row["start"]), int(row["end"])) == (match.start, match.end)
        assert (row["context_before"], row["context_after"]) == (
            match.context_before,
            match.context_after,
        )
        assert tuple(float(row[key]) for key in ("x0", "y0", "x1", "y1")) == match.rect
        assert json.loads(row["quads"]) == json.loads(json.dumps(match.quads))
    assert _bytes(document) == before


def test_page_selection_order_repeats_and_page_wrappers(document):
    matches = document.search_text("Invoice", pages=(index for index in [2, 1, 2]))
    assert [match.page_index for match in matches] == [2, 1, 1, 2]
    assert document.pages[2].search_text("Invoice") == [matches[0]]
    assert document.pages[2].export_search_json(
        "Invoice"
    ) == document.export_search_json("Invoice", pages=[2])
    assert document.pages[2].export_search_csv("Invoice") == document.export_search_csv(
        "Invoice", pages=[2]
    )


def test_regex_flags_zero_width_and_overlapping_literals():
    with Document() as document:
        page = document.pages.add()
        page.add_text("aaa Invoice 123 invoice 456", 72, 700)
        assert [(m.start, m.end) for m in page.search_text("aa")] == [(0, 2), (1, 3)]
        assert [m.text for m in page.search_text("INVOICE", case_sensitive=False)] == [
            "Invoice",
            "invoice",
        ]
        assert [m.text for m in page.search_text(re.compile(r"invoice \d+", re.I))] == [
            "Invoice 123",
            "invoice 456",
        ]
        assert [m.text for m in page.search_text(r"Invoice \d+", regex=True)] == [
            "Invoice 123"
        ]
        assert page.search_text(r"^|$", regex=True) == []
        assert [m.text for m in page.search_text(r"(?=Invoice)|\d+", regex=True)] == [
            "123",
            "456",
        ]
        [match] = page.search_text("123", context_chars=0)
        assert match.context_before == match.context_after == ""


def test_case_insensitive_unicode_offsets_refer_to_original_characters():
    with Document() as document:
        page = document.pages.add()
        page.add_text("İ café CAFÉ", 72, 700, font=load_substitute_sfnt("sans-regular"))
        text = page.extract_text()
        matches = page.search_text("café", case_sensitive=False, context_chars=2)
        assert [match.text for match in matches] == ["café", "CAFÉ"]
        for match in matches:
            assert text[match.start : match.end] == match.text
        assert matches[0].start == 2 and matches[0].context_before == "İ "
        assert "café".encode() in page.export_search_json("café")


def test_unplaceable_multiline_matches_remain_in_both_reports():
    with Document() as document:
        page = document.pages.add()
        page.add_text("First line", 72, 700)
        page.add_text("Second line", 72, 650)
        [match] = page.search_text(r"line\s+Second", regex=True)
        assert match.rect is match.quads is None
        report = json.loads(page.export_search_json(r"line\s+Second", regex=True))
        assert report["matches"][0]["rect"] is None
        row = _rows(page.export_search_csv(r"line\s+Second", regex=True))[0]
        assert all(row[key] == "" for key in ("x0", "y0", "x1", "y1", "quads"))


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_geometry_keeps_unrotated_pdf_coordinates(rotation):
    with Document() as document:
        page = document.pages.add()
        page.add_text("Invoice", 72, 700)
        [original] = page.search_text("Invoice")
        page.rotation = rotation
        page.crop_box = (10, 20, 500, 780)
        assert page.search_text("Invoice") == [original]


def test_scope_uses_page_text_and_omits_hidden_layers_and_annotation_values():
    with Document() as document:
        page = document.pages.add()
        page.add_text("Invoice", 72, 700)
        page.annotations.add("Text", (20, 20, 40, 40), contents="Invoice note")
        document.form.add_text_field(
            "answer", page, (50, 20, 150, 40), value="Invoice form"
        )
        layer = document.layers.add("Hidden", visible=False)
        with page.layer(layer):
            page.add_text("Invoice hidden", 72, 650)
        matches = page.search_text("Invoice")
        assert [match.text for match in matches] == ["Invoice"]
        assert "hidden" not in matches[0].context_after


def test_csv_quoting_unicode_newlines_delimiters_and_bom():
    with Document() as document:
        page = document.pages.add()
        page.add_text('café,"quoted"', 72, 700)
        page.add_text("Invoice", 72, 650)
        page.add_text("next;line", 72, 600)
        [match] = page.search_text("Invoice", context_chars=40)
        data = page.export_search_csv("Invoice", delimiter=";", encoding="utf-8-sig")
        assert data.startswith(b"\xef\xbb\xbf") and data.endswith(b"\r\n")
        row = _rows(data, delimiter=";", encoding="utf-8-sig")[0]
        assert row["context_before"] == match.context_before
        assert (
            'café,"quoted"' in row["context_before"] and "\n" in row["context_before"]
        )
        assert row["context_after"] == match.context_after
        assert "next;line" in row["context_after"]


def test_csv_encoding_failure_and_output_limits_leave_destinations_untouched(tmp_path):
    with Document() as document:
        document.pages.add().add_text("café Invoice", 72, 700)
        path = tmp_path / "report.csv"
        path.write_bytes(b"Keep")
        with io.BytesIO(b"Keep") as output:
            for destination in (path, output):
                with pytest.raises(PdfValidationException, match="encoded as CSV"):
                    document.export_search_csv("Invoice", destination, encoding="ascii")
                assert path.read_bytes() == output.getvalue() == b"Keep"
        expected = document.export_search_csv("Invoice")
        document._load_limits = PdfLoadLimits(
            max_decoded_stream_bytes=len(expected) - 1
        )
        with pytest.raises(PdfResourceLimitException, match="max_decoded_stream_bytes"):
            document.export_search_csv("Invoice", path)
        assert path.read_bytes() == b"Keep"


@pytest.mark.parametrize("method", ["export_search_json", "export_search_csv"])
def test_outputs_paths_overwrite_short_writes_and_stream_ownership(
    document, tmp_path, method
):
    export = getattr(document, method)
    expected = export("Invoice")
    path = tmp_path / "new" / method
    assert export("Invoice", path) == path.read_bytes() == expected
    with pytest.raises(FileExistsError):
        export("Invoice", path, overwrite=False)
    assert path.read_bytes() == expected

    class Short(io.BytesIO):
        def write(self, data):
            return super().write(data[:3])

    with Short(b"prefix") as output:
        output.seek(6)
        assert export("Invoice", output) == expected
        assert output.getvalue() == b"prefix" + expected and not output.closed

    class Stalled:
        def write(self, data):
            return 0

    with pytest.raises(AsposePdfException, match="Stream write"):
        export("Invoice", Stalled())


@pytest.mark.parametrize(
    "options",
    [
        {"pages": "1"},
        {"pages": [True]},
        {"pages": [0]},
        {"pages": [1, 4]},
        {"regex": 1},
        {"case_sensitive": None},
        {"context_chars": True},
        {"context_chars": -1},
        {"context_chars": 1.5},
        {"overwrite": 1},
    ],
)
@pytest.mark.parametrize("method", ["export_search_json", "export_search_csv"])
def test_invalid_options_precede_reads_and_output(
    document, monkeypatch, options, method
):
    monkeypatch.setattr(
        document._engine_pdf,
        "get_page_content",
        lambda *a: pytest.fail("Unexpected content read"),
    )
    with io.BytesIO(b"Keep") as output:
        with pytest.raises(PdfValidationException):
            getattr(document, method)("Invoice", output, **options)
        assert output.getvalue() == b"Keep"


@pytest.mark.parametrize(
    "pattern,options",
    [
        ("", {}),
        (None, {}),
        (b"Invoice", {}),
        (re.compile(b"Invoice"), {}),
        ("[", {"regex": True}),
        ("\ud800", {}),
    ],
)
def test_invalid_patterns_are_rejected_even_for_empty_selections(
    document, monkeypatch, pattern, options
):
    monkeypatch.setattr(
        document._engine_pdf,
        "get_page_content",
        lambda *a: pytest.fail("Unexpected content read"),
    )
    with pytest.raises(PdfValidationException):
        document.search_text(pattern, pages=[], **options)


@pytest.mark.parametrize(
    "options", [{"delimiter": ""}, {"delimiter": "\n"}, {"encoding": "missing-codec"}]
)
def test_invalid_csv_options_precede_extraction(document, monkeypatch, options):
    monkeypatch.setattr(
        document._engine_pdf,
        "get_page_content",
        lambda *a: pytest.fail("Unexpected content read"),
    )
    with pytest.raises(PdfValidationException):
        document.export_search_csv("Invoice", **options)


@pytest.mark.parametrize("method", ["export_search_json", "export_search_csv"])
def test_later_extraction_or_encoding_failure_preserves_destinations(
    document, tmp_path, monkeypatch, method
):
    path = tmp_path / "report"
    path.write_bytes(b"Keep")
    original = document._engine_pdf.get_page_content

    def content(index):
        if index == 1:
            raise PdfValidationException("Damaged later page")
        return original(index)

    monkeypatch.setattr(document._engine_pdf, "get_page_content", content)
    with io.BytesIO(b"Keep") as output:
        for destination in (path, output):
            with pytest.raises(PdfValidationException, match="Damaged later page"):
                getattr(document, method)("Invoice", destination)
            assert path.read_bytes() == output.getvalue() == b"Keep"


def test_empty_documents_missing_matches_and_empty_selections(document, monkeypatch):
    with Document() as empty:
        assert empty.search_text("Invoice") == []
        assert json.loads(empty.export_search_json("Invoice")) == {
            "schema_version": 1,
            "matches": [],
        }
        assert _rows(empty.export_search_csv("Invoice")) == []
    assert document.search_text("Missing") == []
    monkeypatch.setattr(
        document._engine_pdf,
        "get_page_content",
        lambda *a: pytest.fail("Unexpected content read"),
    )
    assert json.loads(document.export_search_json("Invoice", pages=[]))["matches"] == []
    assert _rows(document.export_search_csv("Invoice", pages=[])) == []


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("encrypted", [False, True])
def test_saved_inputs_only_read_selected_pages_and_keep_caches_lazy(
    document, tmp_path, monkeypatch, streaming, encrypted
):
    if encrypted:
        document.encrypt("user", "owner", algorithm="AES-256")
    path = tmp_path / "source.pdf"
    document.save(path)
    original_bytes = path.read_bytes()
    with (Document.open_streaming if streaming else Document)(
        path, password="user" if encrypted else None
    ) as loaded:
        engine = loaded._engine_pdf
        protected = loaded.is_encrypted
        original = engine.get_page_content
        reads = []

        def content(index):
            assert index == 1
            reads.append(index)
            return original(index)

        monkeypatch.setattr(engine, "get_page_content", content)
        engine._page_text_cursor = 1
        [match] = loaded.search_text("Invoice", pages=[2])
        assert match.page_index == 2 and match.rect is not None and reads
        assert loaded.is_encrypted is protected
        assert engine._page_text_cursor == 1 and engine._extracted_text is None
        if streaming:
            assert engine.page_contents == []
    assert path.read_bytes() == original_bytes
    with Document(original_bytes, password="user" if encrypted else None) as loaded:
        assert loaded.pages[2].search_text("Invoice") == [match]


def test_bounded_page_selection_result_count_and_pattern_size(document, monkeypatch):
    document._load_limits = PdfLoadLimits(max_container_items=4)
    with pytest.raises(PdfResourceLimitException, match="max_container_items"):
        document.search_text("Invoice", pages=repeat(1))
    with pytest.raises(PdfResourceLimitException, match="search matches"):
        document.search_text("Invoice", pages=[1, 1, 1])
    document._load_limits = PdfLoadLimits(max_object_bytes=3)
    monkeypatch.setattr(
        document._engine_pdf,
        "get_page_content",
        lambda *a: pytest.fail("Unexpected content read"),
    )
    with pytest.raises(PdfResourceLimitException, match="search text"):
        document.search_text("Invoice")


@pytest.mark.parametrize(
    "limit",
    ["max_codec_work_bytes", "max_decoded_stream_bytes", "max_total_decoded_bytes"],
)
def test_json_output_limits_preserve_existing_destinations(document, tmp_path, limit):
    data = document.export_search_json("Invoice")
    path = tmp_path / "report.json"
    path.write_bytes(b"Keep")
    document._load_limits = PdfLoadLimits(**{limit: len(data) - 1})
    with pytest.raises(PdfResourceLimitException, match=limit):
        document.export_search_json("Invoice", path)
    assert path.read_bytes() == b"Keep"
    document._load_limits = PdfLoadLimits(**{limit: len(data)})
    assert document.export_search_json("Invoice") == data


def test_stale_pages_and_disposed_sources_are_rejected(document):
    page = document.pages[3]
    document.pages.delete(3)
    for method in (page.search_text, page.export_search_json, page.export_search_csv):
        with pytest.raises(PdfValidationException, match="out of range"):
            method("Invoice")
    document.dispose()
    for method in (
        document.search_text,
        document.export_search_json,
        document.export_search_csv,
        page.search_text,
    ):
        with pytest.raises(AsposePdfException, match="disposed"):
            method("Invoice")

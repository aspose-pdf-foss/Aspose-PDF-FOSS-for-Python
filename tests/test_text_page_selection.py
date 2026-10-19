"""Ordered page selection across document, facade and low-code text APIs."""

from __future__ import annotations

import io
import zlib

import pytest

from aspose_pdf import Document, PdfExtractor, PdfLoadLimits, PdfResourceLimitException
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.engine.std_font_data import load_substitute_sfnt
from aspose_pdf.exceptions import AsposePdfException, PdfValidationException
from aspose_pdf.lowcode import (
    ByteArrayDataSource,
    FileDataSource,
    StreamDataSource,
    TextExtractor,
    TextExtractorOptions,
)


def _bytes(document):
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _add_pages(document, texts=("First", "Second", "Third"), *, footer=False):
    for text in texts:
        page = document.pages.add()
        if text:
            page.add_text(text, 72, 700, font_size=12)
        if footer:
            page.add_text("Footer", 72, 100, font_size=12)


def _source_bytes(texts=("First", "Second", "Third"), *, footer=False):
    with Document() as document:
        _add_pages(document, texts, footer=footer)
        return _bytes(document)


def _stream(content, entries=b""):
    return (
        b"<< "
        + entries
        + b" /Length %d >>\nstream\n" % len(content)
        + content
        + b"\nendstream"
    )


def _lazy_pdf():
    """Keep an oversized stream on an unselected page."""
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 7 0 R >> >> >>",
        3: b"<< /Type /Page /Parent 2 0 R /Contents 4 0 R >>",
        4: _stream(b"BT /F1 12 Tf 72 700 Td (Selected) Tj ET"),
        5: b"<< /Type /Page /Parent 2 0 R /Contents 6 0 R >>",
        6: _stream(zlib.compress(b" " * 20_000), b"/Filter /FlateDecode"),
        7: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
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
            if number in offsets
            else b"0000000000 65535 f \n"
        )
    data += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        size,
        start,
    )
    return bytes(data)


@pytest.mark.parametrize("source", ["live", "bytes", "file", "streaming"])
def test_document_selection_survives_pdf_round_trips_and_does_not_change_full_text(
    source, tmp_path
):
    with Document() as original:
        _add_pages(original)
        raw = _bytes(original)
        if source == "live":
            document = original
        elif source == "bytes":
            document = Document(raw)
        else:
            path = tmp_path / "source.pdf"
            path.write_bytes(raw)
            document = (Document.open_streaming if source == "streaming" else Document)(
                path
            )
        try:
            assert document.extract_text(pages=[2, 0, 2]) == "Third\nFirst\nThird"
            assert document.extract_text() == "First\nSecond\nThird"
            assert document.extract_text(pages=(i for i in [1])) == "Second"
            assert document.extract_text(pages=range(2)) == "First\nSecond"
            assert document.extract_text(pages=[]) == ""
            assert document.extract_text() == "First\nSecond\nThird"
        finally:
            if document is not original:
                document.dispose()


def test_page_breaks_preserve_empty_pages_and_original_line_breaks():
    with Document() as document:
        _add_pages(document, ("First", "", "Third"))
        document.pages[2].add_text("Another line", 72, 680)
        assert (
            document.extract_text(pages=[1, 2, 1, 0])
            == "\nThird\nAnother line\n\nFirst"
        )
        assert document.extract_text(pages=[1, 1]) == "\n"
        assert document.extract_text(pages=[1]) == ""


def test_selection_and_clip_work_together_in_every_text_api():
    clip = (0, 680, 300, 730)
    raw = _source_bytes(footer=True)
    with Document(raw) as document:
        full = document.extract_text()
        assert (
            document.extract_text(pages=[2, 0, 2], clip=clip) == "Third\nFirst\nThird"
        )
        assert document.extract_text(pages=[0, 2], clip=(0, 200, 1, 201)) == "\n"
        assert document.extract_text() == full
    with PdfExtractor() as extractor:
        extractor.bind_pdf(raw)
        extractor.extract_text(page_numbers=[3, 1, 3], clip=clip)
        assert extractor.get_text() == "Third\nFirst\nThird"
    options = TextExtractorOptions(PdfLoadLimits(), pages=[2, 0, 2], clip=clip)
    options.add_input(ByteArrayDataSource(raw))
    assert TextExtractor().process(options)[0].to_string() == "Third\nFirst\nThird"


@pytest.mark.parametrize(
    "pages",
    [
        0,
        True,
        "0",
        b"0",
        bytearray(),
        [True],
        [False],
        [0.0],
        ["0"],
        [None],
        [-1],
        [3],
        slice(0, 2),
    ],
)
def test_document_rejects_invalid_selection_before_extracting_a_page(
    pages, monkeypatch
):
    with Document() as document:
        _add_pages(document)

        def no_extraction(*args, **kwargs):
            raise AssertionError("Validate the entire selection before extracting")

        monkeypatch.setattr(document._engine_pdf, "extract_page_text", no_extraction)
        with pytest.raises(PdfValidationException):
            document.extract_text(pages=pages)


def test_later_invalid_index_is_checked_even_when_full_text_is_cached(monkeypatch):
    with Document() as document:
        _add_pages(document)
        expected = document.extract_text()

        def no_extraction(*args, **kwargs):
            raise AssertionError("Invalid selection must not partially extract")

        monkeypatch.setattr(document._engine_pdf, "extract_page_text", no_extraction)
        with pytest.raises(PdfValidationException, match="out of range"):
            document.extract_text(pages=(i for i in [0, 99]))
        assert document.extract_text() == expected


@pytest.mark.parametrize("clip", [(0, 0, 0, 1), "invalid"])
def test_invalid_clip_is_rejected_even_for_an_empty_selection(clip):
    with Document() as document:
        with pytest.raises(PdfValidationException, match="clip"):
            document.extract_text(pages=[], clip=clip)
    with PdfExtractor() as extractor:
        with pytest.raises(PdfValidationException, match="clip"):
            extractor.extract_text(page_numbers=[], clip=clip)
    with pytest.raises(PdfValidationException, match="clip"):
        TextExtractorOptions(pages=[], clip=clip)


@pytest.mark.parametrize("clipped", [False, True])
def test_streaming_document_decodes_only_selected_pages_and_propagates_limits(
    tmp_path, clipped
):
    path = tmp_path / "lazy.pdf"
    path.write_bytes(_lazy_pdf())
    clip = (0, 680, 300, 730) if clipped else None
    with Document.open_streaming(
        path, limits=PdfLoadLimits(max_decoded_stream_bytes=1000)
    ) as document:
        assert document.extract_text(pages=[0, 0], clip=clip) == "Selected\nSelected"
        assert document._engine_pdf.page_contents == []
        with pytest.raises(PdfResourceLimitException, match="max_decoded_stream_bytes"):
            document.extract_text(pages=[1], clip=clip)
        assert document.extract_text(pages=[0], clip=clip) == "Selected"


def test_streaming_selection_does_not_touch_other_page_content(tmp_path, monkeypatch):
    path = tmp_path / "source.pdf"
    path.write_bytes(_source_bytes())
    with Document.open_streaming(path) as document:
        original = document._engine_pdf.get_page_content
        seen = []

        def selected_only(index):
            assert index in (0, 2)
            seen.append(index)
            return original(index)

        monkeypatch.setattr(document._engine_pdf, "get_page_content", selected_only)
        assert document.extract_text(pages=[2, 0, 2]) == "Third\nFirst\nThird"
        assert seen == [2, 0, 2]


def test_facade_uses_one_based_pages_and_cursor_follows_the_selection():
    with PdfExtractor() as extractor:
        extractor.bind_pdf(_source_bytes(("First", "", "Third")))
        extractor.extract_text(page_numbers=(i for i in [3, 2, 1, 3]))
        assert extractor.get_text() == "Third\n\nFirst\nThird"
        assert [extractor.get_next_page_text() for _ in range(4)] == [
            "Third",
            "",
            "First",
            "Third",
        ]
        assert not extractor.has_next_page_text()
        with pytest.raises(StopIteration):
            extractor.get_next_page_text()
        extractor.extract_text(page_numbers=1)
        assert extractor.get_next_page_text() == "First"
        extractor.extract_text(page_numbers=[])
        assert extractor.get_text() == "" and not extractor.has_next_page_text()
        extractor.extract_text()
        assert extractor.get_text() == "First\n\nThird"


@pytest.mark.parametrize(
    "page_numbers", [True, "1", b"1", [0], [-1], [4], [1.5], ["1"], [False], [None]]
)
def test_failed_facade_validation_preserves_previous_text_and_cursor(page_numbers):
    with PdfExtractor() as extractor:
        extractor.bind_pdf(_source_bytes())
        extractor.extract_text(page_numbers=[3, 1])
        assert extractor.get_next_page_text() == "Third"
        with pytest.raises(PdfValidationException):
            extractor.extract_text(page_numbers=page_numbers)
        assert extractor.get_text() == "Third\nFirst"
        assert extractor.get_next_page_text() == "First"


def test_failed_facade_extraction_does_not_publish_partial_results(tmp_path):
    path = tmp_path / "lazy.pdf"
    path.write_bytes(_lazy_pdf())
    with PdfExtractor() as extractor:
        # Target the lazy engine integration, matching the facade's existing tests.
        extractor._bound_pdf = SimplePdf.from_file_lazy(
            path, limits=PdfLoadLimits(max_decoded_stream_bytes=1000)
        )
        extractor.extract_text(page_numbers=[1, 1])
        assert extractor.get_next_page_text() == "Selected"
        with pytest.raises(PdfResourceLimitException):
            extractor.extract_text(page_numbers=[1, 2])
        assert extractor.get_text() == "Selected\nSelected"
        assert extractor.get_next_page_text() == "Selected"


def test_unselected_text_token_limits_are_not_triggered_in_facade_or_lowcode():
    with Document() as document:
        _add_pages(document, ("Selected",))
        page = document.pages.add()
        for number in range(100):
            page.add_text(f"Line {number}", 72, 700 - number * 5)
        raw = _bytes(document)
    limits = PdfLoadLimits(max_content_tokens=40)
    with PdfExtractor() as extractor:
        extractor.bind_pdf(raw, limits=limits)
        extractor.extract_text(page_numbers=[1])
        assert extractor.get_text() == "Selected"
        with pytest.raises(PdfResourceLimitException, match="max_content_tokens"):
            extractor.extract_text(page_numbers=[2])
        assert extractor.get_text() == "Selected"
    options = TextExtractorOptions(limits, pages=[0])
    options.add_input(ByteArrayDataSource(raw))
    assert TextExtractor().process(options)[0].to_string() == "Selected"
    options.pages = [1]
    with pytest.raises(PdfResourceLimitException, match="max_content_tokens"):
        TextExtractor().process(options)


def test_failed_clip_validation_preserves_facade_results_and_cursor():
    with PdfExtractor() as extractor:
        extractor.bind_pdf(_source_bytes())
        extractor.extract_text(page_numbers=[3, 1])
        assert extractor.get_next_page_text() == "Third"
        with pytest.raises(PdfValidationException, match="clip"):
            extractor.extract_text(page_numbers=[], clip=(0, 0, 0, 1))
        assert extractor.get_text() == "Third\nFirst"
        assert extractor.get_next_page_text() == "First"


def test_unbound_empty_and_disposed_objects_validate_selection():
    with Document() as document:
        assert document.extract_text(pages=[]) == document.extract_text() == ""
        with pytest.raises(PdfValidationException):
            document.extract_text(pages=[0])
    with pytest.raises(AsposePdfException, match="disposed"):
        document.extract_text(pages=[])
    with PdfExtractor() as extractor:
        extractor.extract_text(page_numbers=[])
        assert extractor.get_text() == ""
        with pytest.raises(PdfValidationException):
            extractor.extract_text(page_numbers=1)
    with pytest.raises(AsposePdfException, match="disposed"):
        extractor.extract_text(page_numbers=[])


def test_lowcode_generator_selection_is_reusable_across_inputs_and_runs():
    options = TextExtractorOptions(pages=(i for i in [2, 0, 2]))
    options.add_input(ByteArrayDataSource(_source_bytes(("A0", "A1", "A2"))))
    options.add_input(ByteArrayDataSource(_source_bytes(("B0", "B1", "B2"))))
    for _ in range(2):
        result = TextExtractor().process(options)
        assert [item.to_string() for item in result] == ["A2\nA0\nA2", "B2\nB0\nB2"]
    options.pages = (i for i in [1, 0])
    for _ in range(2):
        assert TextExtractor().process(options)[0].to_string() == "A1\nA0"


def test_lowcode_options_snapshot_mutable_input_and_validate_reassignments():
    pages = [1, 0]
    options = TextExtractorOptions(pages=pages)
    pages[0] = 999
    assert options.pages == (1, 0)
    with pytest.raises(PdfValidationException):
        options.pages = [True]
    assert options.pages == (1, 0)
    options.pages = None
    assert options.pages is None


@pytest.mark.parametrize("source_kind", ["bytes", "file", "stream"])
def test_lowcode_input_and_output_sources_support_page_selection(tmp_path, source_kind):
    raw = _source_bytes()
    if source_kind == "file":
        path = tmp_path / "input.pdf"
        path.write_bytes(raw)
        source = FileDataSource(path)
    elif source_kind == "stream":
        source = StreamDataSource(io.BytesIO(raw))
    else:
        source = ByteArrayDataSource(raw)
    options = TextExtractorOptions(pages=[1, 0])
    options.add_input(source)
    sink = ByteArrayDataSource(b"original")
    options.add_output(sink)
    result = TextExtractor().process(options)
    assert result[0].to_string() == "Second\nFirst"
    assert sink.read_bytes() == b"Second\nFirst"


def test_lowcode_empty_selection_emits_one_empty_result_per_input():
    options = TextExtractorOptions(pages=[])
    options.add_input(ByteArrayDataSource(_source_bytes()))
    options.add_input(ByteArrayDataSource(_source_bytes(("Only",))))
    options.add_input(ByteArrayDataSource(_source_bytes(())))
    result = TextExtractor().process(options)
    assert len(result) == 3 and [item.to_string() for item in result] == ["", "", ""]


def test_lowcode_validates_selection_against_each_input_before_emitting_outputs():
    options = TextExtractorOptions(pages=[2])
    options.add_input(ByteArrayDataSource(_source_bytes()))
    options.add_input(ByteArrayDataSource(_source_bytes(("Only",))))
    sinks = [ByteArrayDataSource(b"original") for _ in range(2)]
    for sink in sinks:
        options.add_output(sink)
    with pytest.raises(PdfValidationException, match="out of range"):
        TextExtractor().process(options)
    assert [sink.read_bytes() for sink in sinks] == [b"original", b"original"]


@pytest.mark.parametrize("pages", [0, "0", b"", [True], [0.0], ["0"], [-1]])
def test_lowcode_options_reject_invalid_page_values(pages):
    with pytest.raises(PdfValidationException):
        TextExtractorOptions(pages=pages)


def test_page_selection_resource_limits_apply_before_page_extraction():
    limits = PdfLoadLimits(max_container_items=3)
    with pytest.raises(PdfResourceLimitException, match="page selection"):
        TextExtractorOptions(limits, pages=(0 for _ in range(4)))
    with Document(limits=limits) as document:
        document.pages.add()
        with pytest.raises(PdfResourceLimitException, match="page selection"):
            document.extract_text(pages=[0] * 4)
    with PdfExtractor() as extractor:
        extractor.bind_pdf(
            _source_bytes(), limits=PdfLoadLimits(max_container_items=20)
        )
        with pytest.raises(PdfResourceLimitException, match="page selection"):
            extractor.extract_text(page_numbers=[1] * 21)


@pytest.mark.parametrize("streaming", [False, True])
def test_encrypted_pdf_selection_and_unicode_survive_reload(tmp_path, streaming):
    path = tmp_path / "encrypted.pdf"
    with Document() as document:
        document.pages.add().add_text("Unselected", 72, 700)
        document.pages.add().add_text(
            "café Ångström", 72, 700, font=load_substitute_sfnt("sans-regular")
        )
        document.encrypt(user_password="secret", owner_password="owner")
        document.save(path)
    with (Document.open_streaming if streaming else Document)(
        path, password="secret"
    ) as document:
        assert document.extract_text(pages=[1, 1]) == "café Ångström\ncafé Ångström"
    with PdfExtractor() as extractor:
        extractor.bind_pdf(path, password="secret")
        extractor.extract_text(page_numbers=[2])
        assert extractor.get_text() == "café Ångström"


def test_selected_damaged_content_keeps_existing_recovery_behavior():
    data = bytearray(_source_bytes())
    offset = data.index(b"BT")
    data[offset : offset + 2] = b"B?"
    with Document(bytes(data)) as document:
        assert (
            document.extract_text(pages=[0])
            == document.pages[0].extract_text()
            == "First"
        )


def test_selected_extraction_does_not_change_pdf_bytes():
    with Document() as document:
        _add_pages(document)
        before = _bytes(document)
        document.extract_text(pages=[2, 0, 2])
        document.extract_text(pages=[1], clip=(0, 680, 300, 730))
        assert _bytes(document) == before

"""Page-text iteration and TXT output stay lazy and preserve extraction rules."""

from __future__ import annotations

import errno
import gc
import io
import weakref
from contextlib import closing

import pytest

from aspose_pdf import Document, PdfLoadLimits, PdfResourceLimitException
from aspose_pdf.engine import file_output
from aspose_pdf.engine.file_output import write_file_chunks_atomically
from aspose_pdf.exceptions import (
    AsposePdfException,
    PdfParseException,
    PdfValidationException,
)


def _document(texts=("First", "", "Third"), *, footer=False, limits=None):
    document = Document(limits=limits)
    for text in texts:
        page = document.pages.add(size=(300, 200))
        if text:
            page.add_text(text, 10, 140, font_size=12)
        if footer:
            page.add_text("Footer", 10, 20, font_size=12, artifact=True)
    return document


def _bytes(document):
    with io.BytesIO() as output:
        document.save(output)
        return output.getvalue()


@pytest.mark.parametrize("kind", ["live", "bytes", "file", "streaming"])
def test_iterator_and_txt_match_existing_extraction_across_round_trips(kind, tmp_path):
    with _document() as source:
        raw = _bytes(source)
    path = tmp_path / "source.pdf"
    path.write_bytes(raw)
    if kind == "live":
        document = _document()
    elif kind == "bytes":
        document = Document(raw)
    else:
        document = (Document.open_streaming if kind == "streaming" else Document)(path)
    with document:
        before = _bytes(document)
        assert list(document.iter_text_pages()) == ["First", "", "Third"]
        assert list(document.iter_text_pages(pages=[2, 1, 0, 2])) == [
            "Third",
            "",
            "First",
            "Third",
        ]
        output = tmp_path / "nested" / "extracted.txt"
        count = document.save_as_text(output)
        assert (
            output.read_bytes() == document.extract_text().encode() == b"First\n\nThird"
        )
        assert count == len(output.read_bytes())
        assert _bytes(document) == before
    assert path.read_bytes() == raw


def test_iteration_is_lazy_closable_and_strings_outlive_the_source(
    tmp_path, monkeypatch
):
    path = tmp_path / "source.pdf"
    with _document() as source:
        source.save(path)
    with Document.open_streaming(path) as document:
        original = document._engine_pdf.get_page_content
        reads = []

        def read(index):
            reads.append(index)
            assert index == 0, "Closing early must leave later pages unread"
            return original(index)

        monkeypatch.setattr(document._engine_pdf, "get_page_content", read)
        parts = document.iter_text_pages()
        assert reads == []
        with closing(parts):
            first = next(parts)
            assert reads == [0] and first == "First"
        assert document._engine_pdf.page_contents == []
        assert document._engine_pdf._extracted_text is None
    assert first == "First"
    with pytest.raises(StopIteration):
        next(parts)


def test_iteration_preserves_existing_full_text_cache_and_cursor():
    with _document() as document:
        expected = document.extract_text()
        document._engine_pdf._page_text_cursor = 1
        assert list(document.iter_text_pages(pages=[2, 0])) == ["Third", "First"]
        assert document._engine_pdf._extracted_text == expected
        assert document._engine_pdf._page_text_cursor == 1
        with io.BytesIO() as output:
            document.save_as_text(output, pages=[0])
        assert document._engine_pdf._extracted_text == expected
        assert document._engine_pdf._page_text_cursor == 1


def test_selection_clip_and_exclusion_generators_are_snapshotted_on_call():
    with _document(footer=True) as document:
        pages = [2, 0]
        clip = [0, 0, 300, 200]
        exclusion = [0, 0, 300, 50]
        parts = document.iter_text_pages(
            pages=(index for index in pages),
            clip=clip,
            exclude_regions=(region for region in [exclusion]),
        )
        pages[:] = [1]
        clip[:] = [0, 0, 1, 1]
        exclusion[:] = [0, 100, 300, 200]
        assert list(parts) == ["Third", "First"]


@pytest.mark.parametrize(
    "options",
    [
        {"clip": (0, 100, 300, 180)},
        {"skip_artifacts": True},
        {"exclude_regions": [(0, 0, 300, 50)]},
    ],
)
def test_filters_keep_existing_page_text_semantics(options):
    with _document(footer=True) as document, io.BytesIO() as output:
        assert list(document.iter_text_pages(**options)) == ["First", "", "Third"]
        document.save_as_text(output, page_separator="\f", **options)
        assert output.getvalue() == b"First\f\fThird"


@pytest.mark.parametrize("separator", ["\n", "\f", "", "\r\n---\r\n"])
@pytest.mark.parametrize(
    "encoding", ["utf-8", "utf-8-sig", "utf-16", "utf-16-be", "cp1252"]
)
def test_separators_empty_pages_and_encodings_match_join_and_encode(
    separator, encoding
):
    texts = ["café", "", "Ångström"]
    with _document(texts) as document, io.BytesIO() as output:
        count = document.save_as_text(
            output, encoding=encoding, page_separator=separator
        )
        expected = separator.join(texts).encode(encoding)
        assert output.getvalue() == expected
        assert count == len(expected)


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16"])
def test_empty_documents_and_empty_selections_export_encoded_empty_text(encoding):
    with Document() as document, io.BytesIO() as output:
        assert list(document.iter_text_pages()) == []
        count = document.save_as_text(output, encoding=encoding)
        assert output.getvalue() == "".encode(encoding)
        assert count == len(output.getvalue())
    with _document() as document, io.BytesIO() as output:
        document.save_as_text(output, pages=[], encoding=encoding)
        assert output.getvalue() == "".encode(encoding)


def test_binary_streams_use_current_position_and_retry_short_writes():
    class ShortWriter(io.BytesIO):
        def write(self, data):
            return super().write(data[:2])

    with _document() as document, ShortWriter(b"prefix") as output:
        output.seek(6)
        count = document.save_as_text(output)
        assert output.getvalue() == b"prefixFirst\n\nThird"
        assert count == len(b"First\n\nThird") and not output.closed


def test_stalled_stream_stops_before_reading_later_pages(monkeypatch):
    class StalledWriter:
        def write(self, data):
            return 0

    with _document() as document:
        original = document._engine_pdf.extract_page_text
        reads = []

        def read(index, **options):
            reads.append(index)
            return original(index, **options)

        monkeypatch.setattr(document._engine_pdf, "extract_page_text", read)
        with pytest.raises(AsposePdfException, match="did not consume"):
            document.save_as_text(StalledWriter())
        assert reads == [0]


def test_output_reaches_the_stream_before_next_page_is_decoded(monkeypatch):
    with _document(("First", "Second", "Third")) as document, io.BytesIO() as output:
        original = document._engine_pdf.extract_page_text
        reads = []

        def read(index, **options):
            expected = [b"", b"First", b"First\nSecond"][index]
            assert output.getvalue() == expected
            reads.append(index)
            return original(index, **options)

        monkeypatch.setattr(document._engine_pdf, "extract_page_text", read)
        document.save_as_text(output)
        assert reads == [0, 1, 2]


@pytest.mark.parametrize("method", ["iterate", "save"])
def test_previous_page_text_is_released_before_decoding_next(monkeypatch, method):
    class PageText(str):
        pass

    with _document() as document, io.BytesIO() as output:
        references = []

        def read(index, **options):
            gc.collect()
            assert all(reference() is None for reference in references)
            text = PageText(f"Page {index}")
            references.append(weakref.ref(text))
            return text

        monkeypatch.setattr(document._engine_pdf, "extract_page_text", read)
        if method == "iterate":
            for text in document.iter_text_pages():
                assert text.startswith("Page")
                del text
        else:
            document.save_as_text(output)
        gc.collect()
        assert all(reference() is None for reference in references)


def test_large_unicode_pages_are_written_in_bounded_encoded_chunks(monkeypatch):
    value = "Привет🙂\n" * 10000

    class BoundedWriter(io.BytesIO):
        def write(self, data):
            assert len(data) <= 65536
            return super().write(data)

    with _document(("First", "Second")) as document, BoundedWriter() as output:
        monkeypatch.setattr(
            document._engine_pdf, "extract_page_text", lambda *args, **kwargs: value
        )
        count = document.save_as_text(output, encoding="utf-16", page_separator="\f")
        assert output.getvalue() == (value + "\f" + value).encode("utf-16")
        assert count == len(output.getvalue())


@pytest.mark.parametrize(
    "options",
    [
        {"pages": [0, 3]},
        {"pages": [True]},
        {"pages": "0"},
        {"pages": [-1]},
        {"clip": (0, 0, 0, 10)},
        {"skip_artifacts": 1},
        {"exclude_regions": [(0, 0, 0, 10)]},
    ],
)
def test_invalid_extraction_options_fail_on_call_without_reading_pages(
    monkeypatch, tmp_path, options
):
    with _document() as document:

        def forbidden(*args, **kwargs):
            pytest.fail("Invalid options must fail before extracting")

        monkeypatch.setattr(document._engine_pdf, "extract_page_text", forbidden)
        with pytest.raises(PdfValidationException):
            document.iter_text_pages(**options)
        target = tmp_path / "new" / "text.txt"
        with pytest.raises(PdfValidationException):
            document.save_as_text(target, **options)
        assert not target.parent.exists()


@pytest.mark.parametrize(
    "options,error",
    [
        ({"page_separator": b"\n"}, TypeError),
        ({"page_separator": "\ud800"}, PdfValidationException),
        ({"encoding": None}, TypeError),
        ({"encoding": "not-an-encoding"}, PdfValidationException),
        ({"encoding": "base64_codec"}, PdfValidationException),
        ({"encoding": "punycode"}, PdfValidationException),
        ({"encoding": "idna"}, PdfValidationException),
        ({"overwrite": 1}, PdfValidationException),
        ({"page_separator": "é", "encoding": "ascii"}, PdfValidationException),
    ],
)
def test_invalid_output_options_preserve_destinations(
    monkeypatch, tmp_path, options, error
):
    with _document() as document:

        def forbidden(*args, **kwargs):
            pytest.fail("Invalid output options must fail before extraction")

        monkeypatch.setattr(document._engine_pdf, "extract_page_text", forbidden)
        target = tmp_path / "text.txt"
        target.write_bytes(b"original")
        with pytest.raises(error):
            document.save_as_text(target, **options)
        assert target.read_bytes() == b"original"
        assert sorted(path.name for path in tmp_path.iterdir()) == ["text.txt"]


def test_text_streams_are_refused_even_for_empty_documents():
    with Document() as document, io.StringIO() as output:
        with pytest.raises(TypeError, match="binary stream"):
            document.save_as_text(output)


def test_overwrite_false_refuses_existing_files_before_extraction(
    tmp_path, monkeypatch
):
    path = tmp_path / "text.txt"
    path.write_bytes(b"original")
    with _document() as document:

        def forbidden(*args, **kwargs):
            pytest.fail("Existing paths must be refused before extraction")

        monkeypatch.setattr(document._engine_pdf, "extract_page_text", forbidden)
        with pytest.raises(FileExistsError):
            document.save_as_text(path, overwrite=False)
    assert path.read_bytes() == b"original"


@pytest.mark.parametrize("existing", [False, True])
def test_later_extraction_failure_leaves_file_intact_and_cleans_staging(
    tmp_path, monkeypatch, existing
):
    path = tmp_path / "text.txt"
    if existing:
        path.write_bytes(b"original")
    with _document() as document:
        original = document._engine_pdf.extract_page_text

        def read(index, **options):
            if index == 2:
                raise PdfParseException("Later page failed")
            return original(index, **options)

        monkeypatch.setattr(document._engine_pdf, "extract_page_text", read)
        with pytest.raises(PdfParseException, match="Later page failed"):
            document.save_as_text(path)
        assert path.exists() is existing
        if existing:
            assert path.read_bytes() == b"original"
        assert not list(tmp_path.glob("*.tmp"))
        with io.BytesIO() as output:
            with pytest.raises(PdfParseException, match="Later page failed"):
                document.save_as_text(output)
            assert output.getvalue() == b"First\n"


def test_later_encoding_failure_preserves_files(tmp_path):
    path = tmp_path / "text.txt"
    path.write_bytes(b"original")
    with _document(("First", "café")) as document:
        with pytest.raises(PdfValidationException, match="encoded using"):
            document.save_as_text(path, encoding="ascii")
    assert path.read_bytes() == b"original"
    assert not list(tmp_path.glob("*.tmp"))


def test_disk_flush_failure_preserves_existing_txt_and_cleans_staging(
    tmp_path, monkeypatch
):
    path = tmp_path / "text.txt"
    path.write_bytes(b"original")

    def disk_full(descriptor):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(file_output.os, "fsync", disk_full)
    with _document() as document:
        with pytest.raises(OSError) as error:
            document.save_as_text(path)
    assert error.value.errno == errno.ENOSPC
    assert path.read_bytes() == b"original"
    assert not list(tmp_path.glob("*.tmp"))


def test_disposed_or_changed_sources_stop_iteration():
    with _document() as document:
        parts = document.iter_text_pages()
        assert next(parts) == "First"
        document.pages.add()
        with pytest.raises(AsposePdfException, match="changed"):
            next(parts)
        parts = document.iter_text_pages()
        assert next(parts) == "First"
        document.dispose()
        with pytest.raises(AsposePdfException, match="disposed"):
            next(parts)
    with pytest.raises(AsposePdfException, match="disposed"):
        document.iter_text_pages()
    with pytest.raises(AsposePdfException, match="disposed"):
        document.save_as_text(io.BytesIO())


@pytest.mark.parametrize("encrypted", [False, True])
def test_streaming_encrypted_sources_read_selected_content_only(
    tmp_path, monkeypatch, encrypted
):
    path = tmp_path / "source.pdf"
    with _document() as source:
        if encrypted:
            source.encrypt("secret", "owner")
        source.save(path)
    before = path.read_bytes()
    with Document.open_streaming(
        path, password="secret" if encrypted else None
    ) as document:
        original = document._engine_pdf.get_page_content
        seen = []

        def read(index):
            assert index in (0, 2)
            seen.append(index)
            return original(index)

        monkeypatch.setattr(document._engine_pdf, "get_page_content", read)
        assert list(document.iter_text_pages(pages=[2, 0, 2])) == [
            "Third",
            "First",
            "Third",
        ]
        assert seen == [2, 0, 2]
        seen.clear()
        with io.BytesIO() as output:
            document.save_as_text(output, pages=[2, 0, 2], page_separator="\f")
            assert output.getvalue() == b"Third\fFirst\fThird"
        assert seen == [2, 0, 2]
        assert document._engine_pdf.page_contents == []
        assert document._engine_pdf._extracted_text is None
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "limits,text,context",
    [
        (PdfLoadLimits(max_decoded_stream_bytes=4), "ééé", "extracted page text"),
        (PdfLoadLimits(max_total_decoded_bytes=5), "abc", "extracted text"),
        (PdfLoadLimits(max_container_items=1), "a", "text page selection"),
    ],
)
def test_iterator_output_and_selection_limits(monkeypatch, limits, text, context):
    with Document(limits=limits) as document:
        document.pages.add()
        document.pages.add()
        monkeypatch.setattr(
            document._engine_pdf, "extract_page_text", lambda *args, **kwargs: text
        )
        with pytest.raises(PdfResourceLimitException, match=context):
            list(document.iter_text_pages())


def test_encoded_output_and_separator_limits_preserve_files(tmp_path, monkeypatch):
    path = tmp_path / "text.txt"
    path.write_bytes(b"original")
    cases = [
        (
            PdfLoadLimits(max_decoded_stream_bytes=4),
            {"encoding": "utf-16"},
            "encoded page text",
        ),
        (
            PdfLoadLimits(max_total_decoded_bytes=8),
            {"page_separator": "---"},
            "TXT output bytes",
        ),
        (
            PdfLoadLimits(max_object_bytes=2),
            {"page_separator": "---"},
            "text page separator",
        ),
    ]
    for limits, options, context in cases:
        with Document(limits=limits) as document:
            document.pages.add()
            document.pages.add()
            monkeypatch.setattr(
                document._engine_pdf, "extract_page_text", lambda *args, **kwargs: "abc"
            )
            with pytest.raises(PdfResourceLimitException, match=context):
                document.save_as_text(path, **options)
            assert path.read_bytes() == b"original"
            assert not list(tmp_path.glob("*.tmp"))


def test_atomic_chunk_writer_preserves_target_when_chunk_production_fails(tmp_path):
    path = tmp_path / "text.txt"
    path.write_bytes(b"original")

    def chunks():
        yield b"first"
        raise RuntimeError("Producer failed")

    with pytest.raises(RuntimeError, match="Producer failed"):
        write_file_chunks_atomically(path, chunks())
    assert path.read_bytes() == b"original"
    assert not list(tmp_path.glob("*.tmp"))
    assert write_file_chunks_atomically(path, [b"first", b"", b"second"]) == 11
    assert path.read_bytes() == b"firstsecond"

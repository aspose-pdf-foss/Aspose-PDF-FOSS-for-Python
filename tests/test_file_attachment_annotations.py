"""Clickable file annotations own their payload independently of catalog attachments."""

from __future__ import annotations

import datetime
import io

import pytest

from aspose_pdf import (
    Document,
    FileAttachmentAnnotation,
    FileSpecification,
    PdfLoadLimits,
)
from aspose_pdf.engine.cos import (
    PdfDictionary,
    PdfName,
    PdfNumber,
    PdfString,
)
from aspose_pdf.exceptions import (
    AsposePdfException,
    PdfResourceLimitException,
    PdfSecurityException,
    PdfValidationException,
)


@pytest.fixture
def document():
    with Document() as doc:
        doc.pages.add(size=(300, 200))
        yield doc


def _bytes(document, **kwargs):
    buffer = io.BytesIO()
    document.save(buffer, **kwargs)
    return buffer.getvalue()


def _add(document, file=None, **kwargs):
    return document.pages[0].annotations.add_file_attachment(
        (20, 100, 44, 124), file or FileSpecification("notes.txt", b"Hello"), **kwargs
    )


def _raw(document, index=0):
    engine = document._engine_pdf
    return engine._resolve(engine._annotation_refs(0)[index])


def _stream(document, index=0):
    engine = document._engine_pdf
    spec = engine._resolve(_raw(document, index).mapping[PdfName("FS")])
    ef = engine._resolve(spec.mapping[PdfName("EF")])
    return engine._resolve(ef.mapping[PdfName("UF")])


@pytest.mark.parametrize("on_disk", [False, True])
@pytest.mark.parametrize("icon", ["PushPin", "Graph", "Paperclip", "Tag"])
def test_file_and_metadata_round_trip_with_clickable_reference(
    document, tmp_path, on_disk, icon
):
    created = datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.UTC)
    modified = datetime.datetime(2026, 2, 3, 4, 5, 6)
    file = FileSpecification(
        "rapport-été-文档.txt",
        b"Hello " * 50,
        mime_type="text/plain",
        description="Source details",
        creation_date=created,
        mod_date=modified,
        relationship="Supplement",
    )
    annotation = _add(document, file, icon=icon, title="Reviewer")
    assert isinstance(annotation, FileAttachmentAnnotation)
    assert annotation.icon == icon and annotation.title == "Reviewer"
    assert annotation.contents == "Source details"
    assert annotation.file_specification == file
    assert document.attachments == {} and document.embedded_files == []
    engine = document._engine_pdf
    filespec = engine._resolve(_raw(document).mapping[PdfName("FS")])
    assert filespec.mapping[PdfName("Type")] == PdfName("Filespec")
    assert _stream(document).mapping[PdfName("Type")] == PdfName("EmbeddedFile")
    if on_disk:
        source = tmp_path / "annotated.pdf"
        document.save(source)
    else:
        source = _bytes(document)
    with Document(source) as reopened:
        edited = reopened.pages[0].annotations[0]
        assert edited.file_specification == file
        assert edited.generate_appearance()
        assert edited.appearance_normal
        edited.contents = "Open the source file"
        edited.icon = "Paperclip"
        extracted = tmp_path / "extracted.txt"
        edited.file_specification.save(extracted)
        assert extracted.read_bytes() == file.contents
        with Document(_bytes(reopened)) as again:
            assert again.pages[0].annotations[0].file_specification == file
            assert again.pages[0].annotations[0].contents == "Open the source file"


@pytest.mark.parametrize("compress", [False, True])
@pytest.mark.parametrize("payload", [b"", b"\x01\x02", b"Repeated text " * 100])
def test_empty_small_and_compressible_payloads(document, compress, payload):
    file = FileSpecification("data.bin", payload)
    annotation = _add(document, file, compress=compress)
    assert annotation.contents == file.name and annotation.file_specification == file
    filtered = PdfName("Filter") in _stream(document).mapping
    assert filtered is (compress and len(payload) > 100)
    with Document(_bytes(document)) as reopened:
        assert reopened.pages[0].annotations[0].file_specification == file


def test_annotation_files_do_not_follow_catalog_replacements_renames_or_removals(
    document,
):
    document.add_attachment("notes.txt", b"Catalog", description="Catalog note")
    file = FileSpecification("notes.txt", b"Page note")
    _add(document, file)
    _bytes(document)
    document.update_attachment(
        "notes.txt", content=b"Changed catalog", new_name="renamed.txt"
    )
    _bytes(document)
    assert document.remove_attachment("renamed.txt")
    document.add_attachment("unrelated.txt", b"Other")
    with Document(_bytes(document)) as reopened:
        assert reopened.attachments == {"unrelated.txt": b"Other"}
        assert reopened.pages[0].annotations[0].file_specification == file
        for _ in range(3):
            saved = _bytes(reopened)
        with Document(saved) as final:
            assert final.pages[0].annotations[0].file_specification == file


def test_same_filename_can_have_distinct_annotation_payloads(document):
    _add(document, FileSpecification("notes.txt", b"First"))
    _add(document, FileSpecification("notes.txt", b"Second"))
    with Document(_bytes(document)) as reopened:
        assert [
            item.file_specification.contents for item in reopened.pages[0].annotations
        ] == [b"First", b"Second"]
        assert (
            _raw(reopened, 0).mapping[PdfName("FS")].object_number
            != _raw(reopened, 1).mapping[PdfName("FS")].object_number
        )


@pytest.mark.parametrize("foreign", [False, True])
def test_copied_pages_keep_the_embedded_file(document, foreign):
    file = FileSpecification("source.txt", b"Original")
    _add(document, file)
    with Document() as other:
        target = other if foreign else document
        copied = target.pages.add(document.pages[0])
        with Document(_bytes(target)) as reopened:
            assert (
                reopened.pages[copied.index].annotations[0].file_specification == file
            )


def test_removing_icon_preserves_unrelated_catalog_files(document):
    document.add_attachment("catalog.txt", b"Keep")
    _add(document)
    document.pages[0].annotations.delete(0)
    with Document(_bytes(document)) as reopened:
        assert not len(reopened.pages[0].annotations)
        assert reopened.attachments == {"catalog.txt": b"Keep"}


@pytest.mark.parametrize("source_kind", ["bytes", "streaming"])
@pytest.mark.parametrize("encrypted", [False, True])
def test_loaded_and_new_file_streams_round_trip_under_encryption(
    document, source_kind, encrypted, tmp_path
):
    _add(document, FileSpecification("first.txt", b"SECRET FIRST PAYLOAD"))
    if encrypted:
        document.encrypt("secret", "owner")
    path = tmp_path / "original.pdf"
    document.save(path)
    source = path.read_bytes() if source_kind == "bytes" else path
    factory = Document if source_kind == "bytes" else Document.open_streaming
    with factory(source, password="secret" if encrypted else None) as opened:
        assert (
            opened.pages[0].annotations[0].file_specification.contents
            == b"SECRET FIRST PAYLOAD"
        )
        _add(opened, FileSpecification("new.txt", b"SECRET NEW PAYLOAD"))
        result = _bytes(opened)
    with Document(result, password="secret" if encrypted else None) as reopened:
        assert [
            item.file_specification.contents for item in reopened.pages[0].annotations
        ] == [b"SECRET FIRST PAYLOAD", b"SECRET NEW PAYLOAD"]
    if encrypted:
        assert (
            b"SECRET FIRST PAYLOAD" not in result
            and b"SECRET NEW PAYLOAD" not in result
        )
        with pytest.raises(PdfSecurityException):
            Document(result)


def test_reading_annotations_is_lazy_until_the_file_is_requested(
    document, tmp_path, monkeypatch
):
    _add(document)
    path = tmp_path / "source.pdf"
    document.save(path)
    with Document.open_streaming(path) as opened:
        engine = opened._engine_pdf
        original = engine._decode_cos_stream
        calls = []

        def guarded(stream, ref=None):
            assert stream.mapping.get(PdfName("Type")) == PdfName("EmbeddedFile")
            calls.append(stream)
            return original(stream, ref)

        monkeypatch.setattr(engine, "_decode_cos_stream", guarded)
        annotation = opened.pages[0].annotations[0]
        assert not calls and engine.page_contents == []
        assert annotation.file_specification.contents == b"Hello"
        assert len(calls) == 1 and engine.page_contents == []


@pytest.mark.parametrize(
    "fields",
    [
        {"name": ""},
        {"name": "\ud800"},
        {"description": "\ud800"},
        {"mime_type": "téxt/plain"},
        {"relationship": "Invalid"},
    ],
)
def test_invalid_file_metadata_leaves_pdf_unchanged(document, fields):
    options = {"name": "notes.txt", "contents": b"Hello", **fields}
    before = _bytes(document)
    with pytest.raises(PdfValidationException):
        _add(document, FileSpecification(**options))
    assert _bytes(document) == before and not len(document.pages[0].annotations)


@pytest.mark.parametrize(
    "fields",
    [{"description": 1}, {"mime_type": 1}, {"creation_date": "bad"}, {"mod_date": 1}],
)
def test_metadata_types_are_validated_before_mutation(document, fields):
    before = _bytes(document)
    with pytest.raises(TypeError):
        _add(document, FileSpecification("file", b"Hello", **fields))
    assert _bytes(document) == before


@pytest.mark.parametrize(
    "rect",
    [(0, 0, 0, 1), (1, 0, 0, 1), (0, 0, 1), (0, 0, float("inf"), 1), (True, 0, 2, 2)],
)
def test_invalid_rectangles_do_not_add_an_attachment(document, rect):
    before = _bytes(document)
    with pytest.raises(PdfValidationException):
        document.pages[0].annotations.add_file_attachment(
            rect, FileSpecification("file", b"x")
        )
    assert _bytes(document) == before


@pytest.mark.parametrize(
    "option,value,error",
    [
        ("icon", "", PdfValidationException),
        ("icon", 1, TypeError),
        ("contents", 1, TypeError),
        ("title", 1, TypeError),
        ("compress", 1, TypeError),
    ],
)
def test_factory_arguments_are_checked_before_mutation(document, option, value, error):
    before = _bytes(document)
    with pytest.raises(error):
        _add(document, **{option: value})
    assert _bytes(document) == before


@pytest.mark.parametrize(
    "name,limit",
    [
        ("max_input_bytes", 32),
        ("max_decoded_stream_bytes", 32),
        ("max_codec_work_bytes", 1000),
        ("max_objects", 5),
    ],
)
def test_authoring_limits_are_checked_before_any_new_objects(name, limit):
    with Document(limits=PdfLoadLimits(**{name: limit})) as document:
        document.pages.add()
        count = len(document._engine_pdf._cos_doc.objects)
        with pytest.raises(PdfResourceLimitException, match=name):
            _add(document, FileSpecification("file", b"x" * 64))
        assert len(document._engine_pdf._cos_doc.objects) == count
        assert not len(document.pages[0].annotations)


def test_cumulative_authoring_limits_leave_the_first_file_intact():
    with Document(limits=PdfLoadLimits(max_total_decoded_bytes=100)) as document:
        document.pages.add()
        _add(document, FileSpecification("first", b"x" * 64), compress=False)
        with pytest.raises(PdfResourceLimitException, match="max_total_decoded_bytes"):
            _add(document, FileSpecification("second", b"x" * 64))
        assert len(document.pages[0].annotations) == 1


def test_compression_falls_back_when_the_stream_ratio_policy_requires_it():
    with Document(limits=PdfLoadLimits(max_compression_ratio=1)) as document:
        document.pages.add()
        _add(document, FileSpecification("file", b"x" * 1000))
        assert PdfName("Filter") not in _stream(document).mapping
        assert (
            document.pages[0].annotations[0].file_specification.contents == b"x" * 1000
        )


def test_external_or_missing_files_are_not_opened(document):
    annotation = document.pages[0].annotations.add(
        "FileAttachment", (20, 20, 44, 44), "External"
    )
    assert annotation.file_specification is None
    _raw(document).mapping[PdfName("FS")] = PdfDictionary(
        {PdfName("F"): PdfString("/unreadable/external.txt")}
    )
    assert annotation.file_specification is None


@pytest.mark.parametrize(
    "embedded", [PdfString("bad"), PdfDictionary({PdfName("F"): PdfNumber(1)})]
)
def test_malformed_embedded_targets_raise_clear_errors(document, embedded):
    annotation = _add(document)
    spec = document._engine_pdf._resolve(_raw(document).mapping[PdfName("FS")])
    spec.mapping[PdfName("EF")] = embedded
    with pytest.raises(PdfValidationException, match="Attachment /EF"):
        _ = annotation.file_specification


def test_disposed_document_rejects_factory_and_payload_reads(document):
    collection = document.pages[0].annotations
    annotation = _add(document)
    document.dispose()
    with pytest.raises(AsposePdfException, match="disposed"):
        _ = annotation.file_specification
    with pytest.raises(AsposePdfException, match="disposed"):
        collection.add_file_attachment((0, 0, 20, 20), FileSpecification("file", b"x"))


@pytest.mark.parametrize(
    "limits", [PdfLoadLimits(max_container_items=7), PdfLoadLimits(max_nesting_depth=1)]
)
def test_nested_metadata_limits_are_checked_before_adding_objects(limits):
    with Document(limits=limits) as document:
        document.pages.add()
        count = len(document._engine_pdf._cos_doc.objects)
        with pytest.raises(
            PdfResourceLimitException, match=r"max_container_items|max_nesting_depth"
        ):
            _add(document, FileSpecification("file", b"x", description="Details"))
        assert len(document._engine_pdf._cos_doc.objects) == count
        assert not len(document.pages[0].annotations)


@pytest.mark.parametrize(
    "file", [FileSpecification("n" * 20, b"x"), FileSpecification("n", b"x" * 64)]
)
def test_object_size_limits_cover_metadata_and_encoded_file_bytes(file):
    with Document(limits=PdfLoadLimits(max_object_bytes=32)) as document:
        document.pages.add()
        count = len(document._engine_pdf._cos_doc.objects)
        with pytest.raises(PdfResourceLimitException, match="max_object_bytes"):
            _add(document, file, compress=False)
        assert len(document._engine_pdf._cos_doc.objects) == count
        assert not len(document.pages[0].annotations)


def test_loaded_payload_decoding_obeys_limits_without_loading_page_content(
    document, tmp_path
):
    _add(document, FileSpecification("large", b"x" * 1000))
    path = tmp_path / "limited.pdf"
    document.save(path)
    with Document.open_streaming(
        path, limits=PdfLoadLimits(max_decoded_stream_bytes=100)
    ) as opened:
        annotation = opened.pages[0].annotations[0]
        assert opened._engine_pdf.page_contents == []
        with pytest.raises(PdfResourceLimitException, match="max_decoded_stream_bytes"):
            _ = annotation.file_specification


@pytest.mark.parametrize("key", ["F", "UF", "Unix", "Mac", "DOS"])
def test_foreign_filespec_filename_and_stream_variants_are_supported(document, key):
    annotation = _add(document, FileSpecification("данные.txt", b"Payload"))
    engine = document._engine_pdf
    spec = engine._resolve(_raw(document).mapping[PdfName("FS")])
    embedded = engine._resolve(spec.mapping[PdfName("EF")])
    stream_ref = embedded.mapping[PdfName("UF")]
    spec.mapping.pop(PdfName("F"))
    spec.mapping.pop(PdfName("UF"))
    spec.mapping[PdfName(key)] = engine._cos_doc.register_object(
        PdfString("данные.txt")
    )
    embedded.mapping = {PdfName(key): stream_ref}
    assert annotation.file_specification == FileSpecification("данные.txt", b"Payload")
    with Document(_bytes(document)) as reopened:
        assert reopened.pages[0].annotations[0].file_specification == FileSpecification(
            "данные.txt", b"Payload"
        )

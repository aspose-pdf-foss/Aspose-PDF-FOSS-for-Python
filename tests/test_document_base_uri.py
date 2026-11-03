"""Read and change the catalog URI base without rewriting link actions."""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document, PdfLoadLimits, PdfResourceLimitException, URIAction
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfDictionary,
    PdfIndirectReference,
    PdfName,
    PdfNull,
    PdfNumber,
    PdfStream,
    PdfString,
)
from aspose_pdf.exceptions import (
    AsposePdfException,
    PdfParseException,
    PdfValidationException,
)


def _saved(document):
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _catalog(document):
    pdf = document._engine_pdf
    pdf._ensure_cos()
    return pdf._resolve(pdf._cos_doc.trailer.mapping[PdfName("Root")])


@pytest.fixture
def document():
    with Document() as doc:
        doc.pages.add().add_link((20, 20, 120, 40), URIAction("../item.pdf?q=1#part"))
        yield doc


def test_absent_base_is_none_and_reading_or_clearing_does_not_create_metadata():
    with Document() as document:
        original = document._engine_pdf._cos_doc
        assert document.base_uri is None
        document.base_uri = None
        assert document._engine_pdf._cos_doc is original
        document.pages.add()
        before = _saved(document)
        assert document.base_uri is None
        document.base_uri = None
        assert _saved(document) == before


@pytest.mark.parametrize("on_disk", [False, True])
@pytest.mark.parametrize(
    "base",
    [
        "https://example.test/docs/",
        "HTTPS://EXAMPLE.test:443/docs/index.html?q=1#old",
        "https://example.test/%D0%B4%D0%BE%D0%BA/",
        "file:///tmp/docs/",
        "ftp://example.test/docs/",
        "urn:example:catalog",
    ],
)
def test_ascii_base_round_trips(document, tmp_path, on_disk, base):
    document.base_uri = base
    assert document.base_uri == base
    if on_disk:
        source = tmp_path / "base.pdf"
        document.save(source)
    else:
        source = _saved(document)
    with Document(source) as reopened:
        assert reopened.base_uri == base
        uri = reopened._engine_pdf._resolve(_catalog(reopened).mapping[PdfName("URI")])
        assert reopened._engine_pdf._resolve(
            uri.mapping[PdfName("Base")]
        ).value == base.encode("ascii")


def test_repoint_and_remove_base_change_resolved_addresses_without_rewriting_links(
    document,
):
    document.base_uri = "https://example.test/docs/"
    (first,) = document.extract_links()
    assert first.resolved_uri == "https://example.test/item.pdf?q=1#part"
    document.base_uri = "https://other.test/new/docs/"
    (changed,) = document.extract_links()
    assert changed.uri == first.uri
    assert changed.resolved_uri == "https://other.test/new/item.pdf?q=1#part"
    document.base_uri = None
    (removed,) = document.extract_links()
    assert removed.uri == removed.resolved_uri == first.uri
    assert PdfName("URI") not in _catalog(document).mapping
    with Document(_saved(document)) as reopened:
        assert reopened.base_uri is None


def test_absolute_links_ignore_the_base_and_link_reports_use_it(document):
    document.pages[1].add_link(
        (20, 60, 120, 80), URIAction("https://external.test/page")
    )
    document.base_uri = "https://example.test/docs/"
    first, second = document.extract_links()
    assert first.resolved_uri == "https://example.test/item.pdf?q=1#part"
    assert second.resolved_uri == second.uri == "https://external.test/page"
    assert b"https://example.test/item.pdf?q=1#part" in document.export_links_json()
    assert b"https://example.test/item.pdf?q=1#part" in document.export_links_csv()


@pytest.mark.parametrize("value", [False, 1, b"https://example.test", [], object()])
def test_wrong_types_fail_without_mutation(document, value):
    before = _saved(document)
    with pytest.raises(TypeError, match="base_uri"):
        document.base_uri = value
    assert _saved(document) == before


@pytest.mark.parametrize(
    "value",
    [
        "",
        "docs/",
        "//example.test/docs/",
        "https://",
        "https:///docs/",
        "https://[broken/",
        "https://example.test:bad/",
        "https://example.test:99999/",
        "https://example.test/space here",
        " https://example.test/",
        "https://example.test/\n",
        "https://example.test/\x00",
        "https://example.test/café",
        "https://example.test/\ud800",
        "https://example.test/%ZZ",
        "https://example.test/\\file",
        "https://example.test/<file>",
    ],
)
def test_invalid_values_fail_without_mutation(document, value):
    document.base_uri = "https://example.test/old/"
    before = _saved(document)
    with pytest.raises(PdfValidationException):
        document.base_uri = value
    assert _saved(document) == before


def test_shared_uri_dictionary_and_base_string_are_not_modified(document):
    pdf = document._engine_pdf
    root = _catalog(document)
    base = pdf._cos_doc.register_object(PdfString("https://old.test/"))
    original = PdfDictionary(
        {PdfName("Base"): base, PdfName("Custom"): PdfString("keep")}
    )
    reference = pdf._cos_doc.register_object(original)
    root.mapping[PdfName("URI")] = reference
    root.mapping[PdfName("OtherURI")] = reference
    annotation = pdf._resolve(pdf._annotation_refs(0)[0])
    action = pdf._resolve(annotation.mapping[PdfName("A")])
    action.mapping[PdfName("URI")] = base
    document.base_uri = "https://new.test/"
    updated = pdf._resolve(root.mapping[PdfName("URI")])
    assert updated is not original
    assert pdf._resolve(original.mapping[PdfName("Base")]).value == b"https://old.test/"
    assert updated.mapping[PdfName("Custom")] is original.mapping[PdfName("Custom")]
    assert document.extract_links()[0].uri == "https://old.test/"
    document.base_uri = None
    updated = pdf._resolve(root.mapping[PdfName("URI")])
    assert PdfName("Base") not in updated.mapping
    assert pdf._resolve(root.mapping[PdfName("OtherURI")]) is original
    assert pdf._resolve(original.mapping[PdfName("Base")]).value == b"https://old.test/"


@pytest.mark.parametrize(
    "value",
    [None, PdfNull(), PdfDictionary({}), PdfDictionary({PdfName("Base"): PdfNull()})],
)
def test_missing_and_null_entries_read_as_none(document, value):
    _catalog(document).mapping[PdfName("URI")] = value
    assert document.base_uri is None


@pytest.mark.parametrize("value", [PdfNumber(1), PdfArray([]), PdfStream(b"invalid")])
def test_malformed_uri_containers_are_reported_and_can_be_replaced(document, value):
    _catalog(document).mapping[PdfName("URI")] = value
    with pytest.raises(PdfParseException):
        _ = document.base_uri
    document.base_uri = "https://example.test/"
    assert document.base_uri == "https://example.test/"


@pytest.mark.parametrize(
    "value", [PdfNumber(1), PdfString(b"\xff"), PdfStream(b"invalid")]
)
def test_malformed_base_values_are_reported_without_decoding_streams(
    document, monkeypatch, value
):
    _catalog(document).mapping[PdfName("URI")] = PdfDictionary({PdfName("Base"): value})
    monkeypatch.setattr(
        document._engine_pdf,
        "_decode_cos_stream",
        lambda *a: pytest.fail("No stream decoding"),
    )
    with pytest.raises(PdfParseException):
        _ = document.base_uri


def test_cyclic_and_missing_references_are_bounded(document):
    pdf = document._engine_pdf
    root = _catalog(document)
    reference = PdfIndirectReference(999, 0)
    root.mapping[PdfName("URI")] = reference
    with pytest.raises(PdfParseException, match="reference"):
        _ = document.base_uri
    pdf._cos_doc.objects[999] = reference
    with pytest.raises(PdfParseException, match="cycle"):
        _ = document.base_uri


def test_set_and_get_respect_metadata_limits(document):
    root = _catalog(document)
    document._load_limits = PdfLoadLimits(max_object_bytes=8)
    with pytest.raises(PdfResourceLimitException, match="max_object_bytes"):
        document.base_uri = "https://example.test/"
    assert PdfName("URI") not in root.mapping
    root.mapping[PdfName("URI")] = PdfDictionary(
        {PdfName("Base"): PdfString("https://example.test/")}
    )
    with pytest.raises(PdfResourceLimitException, match="max_object_bytes"):
        _ = document.base_uri


def test_indirect_metadata_depth_limit_is_respected(document):
    pdf = document._engine_pdf
    root = _catalog(document)
    value = PdfDictionary({PdfName("Base"): PdfString("https://example.test/")})
    for _ in range(5):
        value = pdf._cos_doc.register_object(value)
    root.mapping[PdfName("URI")] = value
    document._load_limits = PdfLoadLimits(max_nesting_depth=2)
    with pytest.raises(PdfResourceLimitException, match="max_nesting_depth"):
        _ = document.base_uri


def test_streaming_metadata_changes_do_not_read_page_content(
    document, tmp_path, monkeypatch
):
    path = tmp_path / "streaming.pdf"
    document.save(path)
    with Document.open_streaming(path) as streamed:
        pdf = streamed._engine_pdf
        monkeypatch.setattr(
            pdf, "get_page_content", lambda *a: pytest.fail("No page content reads")
        )
        assert streamed.base_uri is None
        streamed.base_uri = "https://example.test/docs/"
        assert streamed.base_uri == "https://example.test/docs/"
        assert (
            streamed.extract_links()[0].resolved_uri
            == "https://example.test/item.pdf?q=1#part"
        )
        assert pdf._lazy and pdf.page_contents == []
        with Document(_saved(streamed)) as reopened:
            assert reopened.base_uri == streamed.base_uri


@pytest.mark.parametrize("value", [None, "https://new.test/docs/"])
def test_incremental_save_preserves_original_bytes_and_updates_catalog(document, value):
    document.base_uri = "https://old.test/docs/"
    original = _saved(document)
    with Document(original) as reopened:
        reopened.base_uri = value
        output = io.BytesIO()
        reopened.save(output, incremental=True)
    updated = output.getvalue()
    assert updated.startswith(original)
    with Document(updated) as final:
        assert final.base_uri == value
        assert final.extract_links()[0].uri == "../item.pdf?q=1#part"


@pytest.mark.parametrize("algorithm", ["AES-128", "AES-256"])
@pytest.mark.parametrize("incremental", [False, True])
@pytest.mark.parametrize("value", [None, "https://new.test/docs/"])
def test_encrypted_metadata_can_be_read_changed_and_removed(
    document, algorithm, incremental, value
):
    document.base_uri = "https://old.test/docs/"
    document.encrypt("user", "owner", algorithm=algorithm)
    original = _saved(document)
    with Document(original, password="owner") as reopened:
        assert reopened.base_uri == "https://old.test/docs/"
        reopened.base_uri = value
        output = io.BytesIO()
        reopened.save(output, incremental=incremental)
    if incremental:
        assert output.getvalue().startswith(original)
    with Document(output.getvalue(), password="owner") as final:
        assert final.base_uri == value
        assert final.extract_links()[0].uri == "../item.pdf?q=1#part"


def test_dictionary_limits_fail_without_replacing_existing_metadata(document):
    root = _catalog(document)
    original = PdfDictionary(
        {
            PdfName("Base"): PdfString("https://old.test/"),
            **{PdfName(f"Custom{i}"): PdfNumber(i) for i in range(3)},
        }
    )
    root.mapping[PdfName("URI")] = original
    document._load_limits = PdfLoadLimits(max_container_items=3)
    with pytest.raises(PdfResourceLimitException, match="max_container_items"):
        document.base_uri = "https://new.test/"
    assert root.mapping[PdfName("URI")] is original


def test_initial_catalog_creation_respects_object_limits():
    with Document(limits=PdfLoadLimits(max_objects=1)) as document:
        with pytest.raises(PdfResourceLimitException, match="max_objects"):
            document.base_uri = "https://example.test/"
        assert document._engine_pdf._cos_doc is None


def test_disposed_document_rejects_read_and_write(document):
    document.dispose()
    with pytest.raises(AsposePdfException):
        _ = document.base_uri
    with pytest.raises(AsposePdfException):
        document.base_uri = None

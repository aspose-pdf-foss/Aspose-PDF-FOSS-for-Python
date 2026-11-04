"""Read, correct and remove a PDF's default language without conversion."""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document, PdfLoadLimits
from aspose_pdf.engine.cos import (
    PdfArray,
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
    PdfResourceLimitException,
    PdfValidationException,
)


def _bytes(document, **options):
    with io.BytesIO() as output:
        document.save(output, **options)
        return output.getvalue()


def _catalog(document):
    pdf = document._engine_pdf
    pdf._ensure_cos()
    return pdf._resolve(pdf._cos_doc.trailer.mapping[PdfName("Root")])


@pytest.fixture
def document():
    with Document() as doc:
        doc.pages.add().add_text("Keep this text", 40, 700)
        doc.info["Title"] = "Original title"
        yield doc


def test_absent_language_and_removal_do_not_create_metadata():
    with Document() as document:
        assert document._engine_pdf._cos_doc is None
        assert document.language is None
        document.language = None
        assert document._engine_pdf._cos_doc is None
        document.pages.add()
        before = _bytes(document)
        assert document.language is None
        document.language = None
        assert _bytes(document) == before


def test_language_can_be_set_before_adding_pages():
    with Document() as document:
        document.language = "en-US"
        document.pages.add().add_text("New document", 40, 700)
        with Document(_bytes(document)) as reopened:
            assert reopened.language == "en-US"
            assert "New document" in reopened.extract_text()


@pytest.mark.parametrize("on_disk", [False, True])
@pytest.mark.parametrize(
    "language",
    ["en", "en-US", "EN-us", "zh-Hant-TW", "es-419", "de-CH-1901", "x-company", ""],
)
def test_language_round_trips_without_changing_text_or_other_metadata(
    document, tmp_path, on_disk, language
):
    content = document.pages[1].content
    document.language = language
    assert document.language == language
    if on_disk:
        source = tmp_path / "language.pdf"
        document.save(source)
    else:
        source = _bytes(document)
    with Document(source) as reopened:
        assert reopened.language == language
        assert reopened.info["Title"] == "Original title"
        assert reopened.pages[1].content == content
        assert "Keep this text" in reopened.pages[1].extract_text()
        reopened.language = None
        assert reopened.language is None
        assert PdfName("Lang") not in _catalog(reopened).mapping
        with Document(_bytes(reopened)) as removed:
            assert removed.language is None


@pytest.mark.parametrize("value", [True, 1, b"en", [], object()])
def test_nontext_values_fail_without_mutation(document, value):
    document.language = "en"
    before = _bytes(document)
    with pytest.raises(TypeError, match="language"):
        document.language = value
    assert _bytes(document) == before


@pytest.mark.parametrize(
    "value",
    [
        "en_US",
        " en",
        "en ",
        "en--US",
        "en-",
        "123",
        "en-toolongsubtag",
        "ru\n",
        "en\x00",
        "русский",
        "\ud800",
    ],
)
def test_invalid_identifiers_fail_without_mutation(document, value):
    document.language = "en"
    before = _bytes(document)
    with pytest.raises(PdfValidationException, match="language"):
        document.language = value
    assert _bytes(document) == before


@pytest.mark.parametrize(
    "data",
    [
        b"en-US",
        b"\xfe\xff\x00e\x00n\x00-\x00U\x00S",
        b"\xff\xfee\x00n\x00-\x00U\x00S\x00",
        b"\xef\xbb\xbfen-US",
    ],
)
def test_indirect_text_encodings_and_shared_values_are_preserved(document, data):
    pdf = document._engine_pdf
    root = _catalog(document)
    reference = pdf._cos_doc.register_object(PdfString(data))
    root.mapping[PdfName("Lang")] = reference
    root.mapping[PdfName("CustomLanguage")] = reference
    assert document.language == "en-US"
    document.language = "fr-CA"
    assert document.language == "fr-CA"
    assert pdf._resolve(root.mapping[PdfName("CustomLanguage")]).value == data


@pytest.mark.parametrize(
    "value", [PdfNumber(1), PdfName("en"), PdfArray([]), PdfStream(b"not a language")]
)
def test_malformed_language_types_raise_and_can_be_replaced(
    document, monkeypatch, value
):
    _catalog(document).mapping[PdfName("Lang")] = value
    monkeypatch.setattr(
        document._engine_pdf,
        "_decode_cos_stream",
        lambda *a: pytest.fail("No stream decoding"),
    )
    with pytest.raises(PdfParseException, match="language"):
        _ = document.language
    document.language = "en"
    assert document.language == "en"


def test_loaded_strings_are_reported_verbatim_and_null_means_absent(document):
    root = _catalog(document)
    root.mapping[PdfName("Lang")] = PdfString("producer's nonstandard value")
    assert document.language == "producer's nonstandard value"
    root.mapping[PdfName("Lang")] = PdfNull()
    assert document.language is None
    document.language = None
    assert PdfName("Lang") not in root.mapping


def test_missing_and_cyclic_references_raise_bounded_errors(document):
    pdf = document._engine_pdf
    reference = PdfIndirectReference(999, 0)
    _catalog(document).mapping[PdfName("Lang")] = reference
    with pytest.raises(PdfParseException, match="reference"):
        _ = document.language
    pdf._cos_doc.objects[999] = reference
    with pytest.raises(PdfParseException, match="cycle"):
        _ = document.language
    document.language = None
    assert document.language is None


def test_metadata_size_and_reference_depth_obey_limits(document):
    pdf = document._engine_pdf
    root = _catalog(document)
    document._load_limits = PdfLoadLimits(max_object_bytes=4)
    with pytest.raises(PdfResourceLimitException, match="max_object_bytes"):
        document.language = "en-US"
    assert PdfName("Lang") not in root.mapping
    root.mapping[PdfName("Lang")] = PdfString("en-US")
    with pytest.raises(PdfResourceLimitException, match="max_object_bytes"):
        _ = document.language
    value = PdfString("en")
    for _ in range(4):
        value = pdf._cos_doc.register_object(value)
    root.mapping[PdfName("Lang")] = value
    document._load_limits = PdfLoadLimits(max_nesting_depth=2)
    with pytest.raises(PdfResourceLimitException, match="max_nesting_depth"):
        _ = document.language


@pytest.mark.parametrize(
    "limits", [PdfLoadLimits(max_objects=1), PdfLoadLimits(max_container_items=2)]
)
def test_initial_catalog_limits_do_not_leave_partial_metadata(limits):
    with Document(limits=limits) as document:
        with pytest.raises(PdfResourceLimitException):
            document.language = "en"
        assert document._engine_pdf._cos_doc is None


@pytest.mark.parametrize("language", [None, "de-CH"])
@pytest.mark.parametrize("algorithm", ["AES-128", "AES-256"])
def test_encrypted_streaming_edits_remain_lazy_and_save_incrementally(
    document, tmp_path, monkeypatch, language, algorithm
):
    document.language = "en"
    document.encrypt("user", "owner", algorithm=algorithm)
    path = tmp_path / "encrypted.pdf"
    document.save(path)
    original = path.read_bytes()
    with Document.open_streaming(path, password="owner") as reopened:
        pdf = reopened._engine_pdf
        with monkeypatch.context() as patch:
            patch.setattr(
                pdf, "get_page_content", lambda *a: pytest.fail("No page content reads")
            )
            assert reopened.language == "en"
            reopened.language = language
            assert reopened.language == language
            assert pdf._lazy and pdf.page_contents == []
        updated = _bytes(reopened, incremental=True)
    assert updated.startswith(original)
    with Document(updated, password="owner") as final:
        assert final.language == language
        assert "Keep this text" in final.extract_text()


def test_pdfua_language_can_be_corrected_without_repeating_conversion(document):
    document.convert_to_pdfua(language="fr", title="Original title")
    assert document.language == "fr"
    root = _catalog(document)
    unchanged = {
        key: value for key, value in root.mapping.items() if key != PdfName("Lang")
    }
    document.language = "fr-CA"
    assert document.language == "fr-CA"
    assert {
        key: value for key, value in root.mapping.items() if key != PdfName("Lang")
    } == unchanged
    assert not any("Lang" in warning for warning in document.validate_pdfua().warnings)
    document.language = None
    assert any("Lang" in warning for warning in document.validate_pdfua().warnings)


def test_disposed_document_rejects_language_access(document):
    document.dispose()
    with pytest.raises(AsposePdfException, match="disposed"):
        _ = document.language
    with pytest.raises(AsposePdfException, match="disposed"):
        document.language = "en"
    with pytest.raises(AsposePdfException, match="disposed"):
        document.language = None

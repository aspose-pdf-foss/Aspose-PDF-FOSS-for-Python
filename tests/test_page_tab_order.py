"""Page-local keyboard navigation preferences survive PDF editing and saves."""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document, URIAction
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfBoolean,
    PdfDictionary,
    PdfName,
    PdfNull,
    PdfNumber,
    PdfStream,
    PdfString,
)
from aspose_pdf.exceptions import (
    AsposePdfException,
    PdfSecurityException,
    PdfValidationException,
)


@pytest.fixture
def document():
    with Document() as doc:
        for _ in range(3):
            doc.pages.add(size=(300, 200))
        yield doc


def _bytes(document, **kwargs):
    output = io.BytesIO()
    document.save(output, **kwargs)
    return output.getvalue()


def _dictionary(document, index=0):
    return document._engine_pdf._get_page_dict(index)


@pytest.mark.parametrize("on_disk", [False, True])
def test_each_page_has_its_own_persisted_tab_order(document, tmp_path, on_disk):
    assert [page.tab_order for page in document.pages] == [None, None, None]
    for page, order, code in zip(
        document.pages, ["row", "column", "structure"], "RCS", strict=True
    ):
        page.tab_order = order
        assert page.tab_order == order
        assert _dictionary(document, page.index).mapping[PdfName("Tabs")] == PdfName(
            code
        )
    if on_disk:
        source = tmp_path / "tabs.pdf"
        document.save(source)
    else:
        source = _bytes(document)
    with Document(source) as reopened:
        assert [page.tab_order for page in reopened.pages] == [
            "row",
            "column",
            "structure",
        ]


@pytest.mark.parametrize(
    "value,expected",
    [
        ("ROW", "row"),
        ("r", "row"),
        ("R", "row"),
        (" row ", "row"),
        ("Column", "column"),
        ("C", "column"),
        (" c ", "column"),
        ("STRUCTURE", "structure"),
        ("S", "structure"),
        ("s", "structure"),
    ],
)
def test_friendly_names_and_pdf_codes_are_normalized(document, value, expected):
    document.pages[0].tab_order = value
    assert document.pages[0].tab_order == expected


def test_none_removes_the_local_setting_and_does_not_inherit_from_parent(document):
    page = _dictionary(document)
    engine = document._engine_pdf
    parent = engine._resolve(page.mapping[PdfName("Parent")])
    parent.mapping[PdfName("Tabs")] = PdfName("S")
    assert document.pages[0].tab_order is None
    document.pages[0].tab_order = "row"
    document.pages[1].tab_order = "column"
    document.pages[0].tab_order = None
    assert PdfName("Tabs") not in page.mapping
    assert document.pages[0].tab_order is None
    assert document.pages[1].tab_order == "column"
    assert parent.mapping[PdfName("Tabs")] == PdfName("S")
    with Document(_bytes(document)) as reopened:
        assert [page.tab_order for page in reopened.pages] == [None, "column", None]


@pytest.mark.parametrize("value", ["", "rows", "manual", "default", "/R", "A", "W"])
def test_unknown_orders_are_rejected_before_any_pdf_changes(document, value):
    document.pages[0].tab_order = "column"
    before = _bytes(document)
    with pytest.raises(PdfValidationException, match="tab_order"):
        document.pages[0].tab_order = value
    assert _bytes(document) == before


@pytest.mark.parametrize("value", [True, False, 0, 1, 2.5, b"R", [], {}, ["row"]])
def test_only_strings_or_none_can_be_assigned(document, value):
    before = _bytes(document)
    with pytest.raises(TypeError, match="string or None"):
        document.pages[0].tab_order = value
    assert _bytes(document) == before


@pytest.mark.parametrize(
    "value",
    [
        PdfName("A"),
        PdfName("W"),
        PdfName("row"),
        PdfString("R"),
        PdfNull(),
        PdfNumber(0),
        PdfBoolean(True),
        PdfArray([PdfName("R")]),
        PdfDictionary({PdfName("R"): PdfName("S")}),
        PdfStream(b"not a tab order"),
    ],
)
def test_unsupported_or_malformed_entries_are_read_as_none_and_preserved(
    document, value
):
    if isinstance(value, PdfStream):
        value = document._engine_pdf._cos_doc.register_object(value)
    _dictionary(document).mapping[PdfName("Tabs")] = value
    before = _bytes(document)
    assert document.pages[0].tab_order is None
    assert _dictionary(document).mapping[PdfName("Tabs")] is value
    assert _bytes(document) == before
    with Document(before) as reopened:
        assert reopened.pages[0].tab_order is None
        assert PdfName("Tabs") in _dictionary(reopened).mapping


@pytest.mark.parametrize(
    "code,expected", [("R", "row"), ("C", "column"), ("S", "structure")]
)
def test_indirect_name_entries_in_existing_documents_are_read(document, code, expected):
    _dictionary(document).mapping[PdfName("Tabs")] = (
        document._engine_pdf._cos_doc.register_object(PdfName(code))
    )
    with Document(_bytes(document)) as reopened:
        assert reopened.pages[0].tab_order == expected


def test_assignment_changes_only_the_selected_pages_tabs_entry(document):
    page = document.pages[0]
    field = document.form.add_text_field("last", 0, (20, 100, 120, 125), value="Last")
    document.form.add_text_field("first", 0, (20, 150, 120, 175), value="First")
    page.add_link((150, 100, 230, 125), URIAction("https://example.test"))
    page.add_text("Heading", 10, 10)
    dictionary = _dictionary(document)
    before = dict(dictionary.mapping)
    content = page.content
    names = [entry.name for entry in document.form]
    widgets = field.widgets
    appearances = [annotation.appearance_normal for annotation in page.annotations]
    page.tab_order = "structure"
    assert {
        key: value
        for key, value in dictionary.mapping.items()
        if key != PdfName("Tabs")
    } == before
    assert page.content == content and field.widgets == widgets
    assert [entry.name for entry in document.form] == names
    assert [
        annotation.appearance_normal for annotation in page.annotations
    ] == appearances
    root = document._engine_pdf._resolve(
        document._engine_pdf._cos_doc.trailer.mapping[PdfName("Root")]
    )
    assert PdfName("StructTreeRoot") not in root.mapping
    assert all(other.tab_order is None for other in list(document.pages)[1:])


@pytest.mark.parametrize("foreign", [False, True])
def test_page_copies_keep_their_order_and_can_be_edited_independently(
    document, foreign
):
    document.pages[0].tab_order = "column"
    with Document() as other:
        target = other if foreign else document
        copied = target.pages.add(document.pages[0])
        assert copied.tab_order == "column"
        copied.tab_order = "row"
        assert document.pages[0].tab_order == "column"
        with Document(_bytes(target)) as reopened:
            assert reopened.pages[copied.index].tab_order == "row"


def test_pdfua_structure_order_can_be_read_and_conversion_still_sets_it(document):
    document.form.add_text_field("notes", 0, (20, 100, 120, 125))
    document.pages[0].tab_order = "row"
    document.convert_to_pdfua(title="Application", language="en-US")
    assert document.pages[0].tab_order == "structure"
    with Document(_bytes(document)) as reopened:
        assert reopened.pages[0].tab_order == "structure"


@pytest.mark.parametrize("version", ["1.4", "1.5", "1.7", "2.0"])
def test_explicit_document_version_stays_under_caller_control(document, version):
    document.version = version
    document.pages[0].tab_order = "row"
    assert document.version == version
    document.pages[0].tab_order = None
    assert document.version == version


@pytest.mark.parametrize("encrypted", [False, True])
def test_streaming_read_and_edit_do_not_decode_content_or_appearances(
    document, tmp_path, monkeypatch, encrypted
):
    document.pages[0].add_text("Content", 10, 10)
    document.form.add_text_field("notes", 0, (20, 100, 120, 125), value="Answer")
    document.pages[0].tab_order = "column"
    if encrypted:
        document.encrypt("secret", "owner")
    path = tmp_path / "source.pdf"
    document.save(path)
    original = path.read_bytes()
    with Document.open_streaming(
        path, password="secret" if encrypted else None
    ) as opened:

        def forbidden(*args, **kwargs):
            pytest.fail("Tab-order access must not decode page or appearance streams")

        with monkeypatch.context() as context:
            context.setattr(opened._engine_pdf, "_decode_cos_stream", forbidden)
            context.setattr(opened._engine_pdf, "get_page_content", forbidden)
            assert opened.pages[0].tab_order == "column"
            opened.pages[0].tab_order = "row"
            assert opened.pages[0].tab_order == "row"
            assert opened._engine_pdf.page_contents == []
        result = _bytes(opened)
    assert path.read_bytes() == original
    with Document(result, password="secret" if encrypted else None) as reopened:
        assert reopened.pages[0].tab_order == "row"
        assert reopened.form["notes"].value == "Answer"
    if encrypted:
        with pytest.raises(PdfSecurityException):
            Document(result)


def test_disposed_documents_reject_reads_and_writes(document):
    page = document.pages[0]
    document.dispose()
    with pytest.raises(AsposePdfException, match="disposed"):
        _ = page.tab_order
    with pytest.raises(AsposePdfException, match="disposed"):
        page.tab_order = "row"

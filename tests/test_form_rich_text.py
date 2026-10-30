"""Public rich-text editing keeps field data and visible appearances consistent."""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document, PdfLoadLimits
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfDictionary,
    PdfName,
    PdfNumber,
    PdfStream,
    PdfString,
)
from aspose_pdf.engine.form_fields import field_dictionary
from aspose_pdf.exceptions import (
    AsposePdfException,
    PdfResourceLimitException,
    PdfValidationException,
    UnsupportedFeatureException,
)

MARKUP = (
    '<body xmlns="http://www.w3.org/1999/xhtml"><p><b>Approved</b> '
    '<span style="color:#ff0000;font-size:16pt">today</span></p>'
    "<p><i>Next</i><br/>step</p></body>"
)
PLAIN = "Approved today\nNext\nstep"
RICH_FLAG = 1 << 25


@pytest.fixture
def document():
    with Document() as doc:
        doc.pages.add(size=(320, 200))
        yield doc


def _text(document, name="notes", **kwargs):
    return document.form.add_text_field(
        name, 0, (20, 90, 300, 180), value="Template", multiline=True, **kwargs
    )


def _bytes(document):
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _appearance(document, index=0):
    return document.pages[0].annotations[index].appearance_normal


@pytest.mark.parametrize("on_disk", [False, True])
def test_rich_and_plain_values_and_appearance_survive_save(document, tmp_path, on_disk):
    field = _text(document, read_only=True, required=True)
    field.max_length = 200
    field.rich_text = MARKUP
    assert field.rich_text == MARKUP and field.value == PLAIN
    assert field.flags == RICH_FLAG | (1 << 12) | 3
    assert field.default_value == "Template" and field.max_length == 200
    assert document.form["notes"] is field
    assert document.form.to_dict() == {"notes": PLAIN}
    assert not document.form.validate()
    content = _appearance(document)
    assert b"/HeBo " in content and b"/HeOb " in content and b"1 0 0 rg" in content
    before = document.pages[0].render(antialias=False).pixels
    if on_disk:
        source = tmp_path / "rich.pdf"
        document.save(source)
    else:
        source = _bytes(document)
    with Document(source) as reopened:
        assert reopened.form["notes"].rich_text == MARKUP
        assert reopened.form["notes"].value == PLAIN
        assert _appearance(reopened) == content
        assert reopened.pages[0].render(antialias=False).pixels == before
        reopened.form.generate_appearances()
        assert reopened.pages[0].render(antialias=False).pixels == before
        reopened.form.flatten()
        assert len(reopened.form) == 0
        assert reopened.pages[0].render(antialias=False).pixels == before


def test_entities_unicode_and_paragraphs_use_the_shared_parser(document):
    field = _text(document)
    markup = "<body><p>A &amp; B &#xA0; &lt;C&gt; café Привет 😀</p><p>Next<br/>line</p></body>"
    field.rich_text = markup
    assert field.rich_text == markup
    assert field.value == "A & B \u00a0 <C> café Привет 😀\nNext\nline"
    with Document(_bytes(document)) as reopened:
        assert reopened.form["notes"].value == field.value
        assert reopened.form["notes"].rich_text == markup


def test_none_keeps_plain_answer_and_returns_to_plain_appearance(document):
    field = _text(document)
    field.rich_text = MARKUP
    field.rich_text = None
    assert field.rich_text is None and field.value == PLAIN
    assert not field.flags & RICH_FLAG and field.multiline
    assert b"/HeBo " not in _appearance(document)
    assert b"(Approved today) Tj" in _appearance(document)
    assert field.default_value == "Template"
    with Document(_bytes(document)) as reopened:
        assert reopened.form["notes"].rich_text is None
        assert reopened.form["notes"].value == PLAIN


def test_empty_body_clears_the_answer_without_using_the_old_value(document):
    field = _text(document)
    field.rich_text = "<body><p/></body>"
    assert field.value == "" and field.rich_text == "<body><p/></body>"
    assert b"Template" not in _appearance(document)
    assert field.flags & RICH_FLAG


@pytest.mark.parametrize("operation", ["value", "fill", "reset", "json", "fdf", "xfdf"])
def test_plain_answer_replaces_stale_markup_in_every_fill_path(document, operation):
    field = _text(document)
    field.rich_text = MARKUP
    if operation == "value":
        field.value = "Replacement"
    elif operation == "fill":
        document.form.fill({"notes": "Replacement"})
    elif operation == "reset":
        document.form.reset()
    else:
        with Document() as source:
            source.pages.add()
            _text(source).value = "Replacement"
            payload = getattr(source.form, f"export_{operation}")()
        getattr(document.form, f"import_{operation}")(payload)
    expected = "Template" if operation == "reset" else "Replacement"
    assert field.value == expected and field.rich_text is None
    assert not field.flags & RICH_FLAG
    assert expected.encode() in _appearance(document)
    assert b"Approved" not in _appearance(document)


@pytest.mark.parametrize("clear", ["none", "value", "markup"])
def test_inherited_markup_is_overridden_locally_and_siblings_keep_theirs(
    document, clear
):
    field = _text(document, "group.first")
    sibling = _text(document, "group.second")
    engine = document._engine_pdf
    first = field_dictionary(engine, field.name)
    second = field_dictionary(engine, sibling.name)
    parent = engine._resolve(first.mapping[PdfName("Parent")])
    parent.mapping.update(
        {PdfName("RV"): PdfString(MARKUP), PdfName("Ff"): PdfNumber(RICH_FLAG | 4097)}
    )
    for dictionary in (first, second):
        dictionary.mapping.pop(PdfName("Ff"), None)
    document.form.generate_appearances()
    sibling_appearance = _appearance(document, 1)
    assert field.rich_text == sibling.rich_text == MARKUP
    if clear == "none":
        field.rich_text = None
        assert field.value == "Template" and field.rich_text is None
    elif clear == "value":
        field.value = "Plain"
        assert field.rich_text is None
    else:
        field.rich_text = "<body><p>Local</p></body>"
        assert field.value == "Local"
    assert field.read_only and field.multiline
    assert sibling.rich_text == MARKUP and sibling.flags == RICH_FLAG | 4097
    assert _appearance(document, 1) == sibling_appearance
    with Document(_bytes(document)) as reopened:
        assert reopened.form[field.name].rich_text == field.rich_text
        assert reopened.form[sibling.name].rich_text == MARKUP


def test_separate_widgets_all_receive_the_updated_appearance(document):
    field = _text(document)
    engine = document._engine_pdf
    dictionary = field_dictionary(engine, field.name)
    page = engine._get_page_dict(0)
    annotations = engine._resolve(page.mapping[PdfName("Annots")])
    field_ref = annotations.items[0]
    kids = []
    for rect in [(20, 90, 300, 180), (20, 0, 300, 85)]:
        widget = PdfDictionary(
            {
                PdfName("Subtype"): PdfName("Widget"),
                PdfName("Parent"): field_ref,
                PdfName("Rect"): PdfArray([PdfNumber(v) for v in rect]),
            }
        )
        kids.append(engine._cos_doc.register_object(widget))
    for key in ("Subtype", "Type", "Rect", "AP", "P"):
        dictionary.mapping.pop(PdfName(key), None)
    dictionary.mapping[PdfName("Kids")] = PdfArray(kids)
    page.mapping[PdfName("Annots")] = PdfArray(kids)
    field.rich_text = MARKUP
    assert len(field.widgets) == 2
    for index in range(2):
        assert b"/HeBo " in _appearance(document, index)
        assert b"(Approved) Tj" in _appearance(document, index)
    field.value = "Plain"
    for index in range(2):
        assert b"(Plain) Tj" in _appearance(document, index)
        assert b"Approved" not in _appearance(document, index)


@pytest.mark.parametrize(
    "markup",
    [
        "",
        "plain",
        "<p>Missing body</p>",
        "<body><b>broken</body>",
        "<body>bad & entity</body>",
        "<body>\x00</body>",
        "<body>\ud800</body>",
        '<!DOCTYPE body [<!ENTITY x "expanded">]><body>&x;</body>',
        '<!DOCTYPE body SYSTEM "https://example.invalid/remote.dtd"><body/>',
        '<x:body xmlns:x="http://www.w3.org/1999/xhtml"/>',
        '<body xmlns="https://example.invalid/other"/>',
    ],
)
def test_invalid_markup_is_rejected_before_changing_values_or_appearances(
    document, markup
):
    field = _text(document)
    field.rich_text = MARKUP
    before = _bytes(document)
    with pytest.raises(PdfValidationException):
        field.rich_text = markup
    assert field.rich_text == MARKUP and field.value == PLAIN
    assert _bytes(document) == before


@pytest.mark.parametrize("value", [True, 1, b"<body/>", [], {}])
def test_rich_text_rejects_non_strings(document, value):
    field = _text(document)
    with pytest.raises(TypeError, match="string or None"):
        field.rich_text = value
    assert field.rich_text is None and field.value == "Template"


@pytest.mark.parametrize(
    "kind", ["checkbox", "radio", "listbox", "combobox", "pushbutton", "signature"]
)
def test_other_field_types_report_none_and_reject_edits(document, kind):
    args = ("other", 0, (5, 5, 40, 40))
    if kind == "radio":
        field = document.form.add_radio_group("other", 0, [("A", (5, 5, 40, 40))])
    elif kind in ("listbox", "combobox"):
        factory = (
            document.form.add_list_box
            if kind == "listbox"
            else document.form.add_combo_box
        )
        field = factory(*args, ["A", "B"])
    else:
        factory = getattr(
            document.form,
            {
                "checkbox": "add_checkbox",
                "pushbutton": "add_push_button",
                "signature": "add_signature_field",
            }[kind],
        )
        field = factory(*args)
    assert field.rich_text is None
    with pytest.raises(PdfValidationException, match="only to text"):
        field.rich_text = MARKUP


@pytest.mark.parametrize("mode", ["password", "comb", "file_selection"])
def test_incompatible_modes_are_rejected_before_changes(document, mode):
    field = _text(document)
    field.multiline = False
    if mode == "comb":
        field.max_length = 10
        field.comb = True
    elif mode == "password":
        field.password = True
    else:
        field_dictionary(document._engine_pdf, field.name).mapping[PdfName("Ff")] = (
            PdfNumber(1 << 20)
        )
    before = _bytes(document)
    with pytest.raises(PdfValidationException, match="cannot be combined"):
        field.rich_text = MARKUP
    assert _bytes(document) == before


@pytest.mark.parametrize("mode", ["password", "comb"])
def test_rich_text_rejects_enabling_conflicting_modes(document, mode):
    field = _text(document)
    field.multiline = False
    field.max_length = 50
    field.rich_text = MARKUP
    before = _bytes(document)
    with pytest.raises(PdfValidationException):
        setattr(field, mode, True)
    assert _bytes(document) == before


@pytest.mark.parametrize(
    "limit,markup",
    [
        (PdfLoadLimits(max_object_bytes=512), "<body>" + "x" * 256 + "</body>"),
        (
            PdfLoadLimits(max_nesting_depth=10),
            "<body>" + "<p>" * 11 + "x" + "</p>" * 11 + "</body>",
        ),
        (PdfLoadLimits(max_container_items=12), "<body>" + "<p>x</p>" * 12 + "</body>"),
    ],
)
def test_markup_resource_limits_are_checked_before_mutation(limit, markup):
    with Document(limits=limit) as document:
        document.pages.add()
        field = _text(document)
        before = field.value, field.flags, _appearance(document)
        with pytest.raises(PdfResourceLimitException):
            field.rich_text = markup
        assert (field.value, field.flags, _appearance(document)) == before
        assert field.rich_text is None


def test_getter_is_lazy_and_reports_unsupported_stream_values(document, monkeypatch):
    field = _text(document)
    dictionary = field_dictionary(document._engine_pdf, field.name)
    dictionary.mapping[PdfName("RV")] = PdfString(MARKUP)

    def unexpected(*args, **kwargs):
        pytest.fail("Reading rich text must not decode a stream")

    monkeypatch.setattr(document._engine_pdf, "_decode_cos_stream", unexpected)
    assert field.rich_text == MARKUP
    dictionary.mapping[PdfName("RV")] = PdfStream(b"<body/>")
    with pytest.raises(UnsupportedFeatureException, match="Stream-valued"):
        _ = field.rich_text


@pytest.mark.parametrize("encrypted", [False, True])
def test_streaming_edits_preserve_rich_text_and_protection(
    document, tmp_path, encrypted
):
    _text(document).rich_text = MARKUP
    if encrypted:
        document.encrypt("secret", "owner")
    path = tmp_path / "source.pdf"
    document.save(path)
    with Document.open_streaming(
        path, password="secret" if encrypted else None
    ) as opened:
        field = opened.form["notes"]
        assert field.rich_text == MARKUP
        field.rich_text = "<body><p><b>Edited</b></p></body>"
        result = _bytes(opened)
    with Document(result, password="secret" if encrypted else None) as reopened:
        assert reopened.form["notes"].value == "Edited"
        assert b"/HeBo " in _appearance(reopened)
    if encrypted:
        from aspose_pdf.exceptions import PdfSecurityException

        with pytest.raises(PdfSecurityException):
            Document(result)


def test_removed_and_disposed_fields_reject_rich_text_access(document):
    field = _text(document)
    field.remove()
    replacement = _text(document)
    with pytest.raises(KeyError, match="no longer"):
        field.rich_text = MARKUP
    with pytest.raises(KeyError, match="no longer"):
        _ = field.rich_text
    document.dispose()
    with pytest.raises(AsposePdfException, match="disposed"):
        replacement.rich_text = MARKUP


@pytest.mark.parametrize("clearing", [False, True])
def test_cid_appearance_rebuilds_are_refused_before_mutation(document, clearing):
    field = _text(document)
    if clearing:
        field.rich_text = MARKUP
    engine = document._engine_pdf
    root = engine._resolve(engine._cos_doc.trailer.mapping[PdfName("Root")])
    acro = engine._resolve(root.mapping[PdfName("AcroForm")])
    resources = engine._resolve(acro.mapping[PdfName("DR")])
    fonts = engine._resolve(resources.mapping[PdfName("Font")])
    fonts.mapping[PdfName("CID")] = PdfDictionary(
        {PdfName("Subtype"): PdfName("Type0")}
    )
    field_dictionary(engine, field.name).mapping[PdfName("DA")] = PdfString(
        "/CID 12 Tf 0 g"
    )
    before = field.rich_text, field.value, field.flags, _appearance(document)
    with pytest.raises(UnsupportedFeatureException, match="embedded CID"):
        field.rich_text = None if clearing else MARKUP
    assert (field.rich_text, field.value, field.flags, _appearance(document)) == before


@pytest.mark.parametrize(
    "style",
    [
        "font-size:" + "9" * 400,
        "font-weight:" + "9" * 5000,
    ],
)
def test_invalid_numeric_styles_do_not_erase_the_previous_answer(document, style):
    field = _text(document)
    before = field.value, field.flags, _appearance(document)
    with pytest.raises(PdfValidationException):
        field.rich_text = f'<body><p style="{style}">New text</p></body>'
    assert (field.value, field.flags, _appearance(document)) == before

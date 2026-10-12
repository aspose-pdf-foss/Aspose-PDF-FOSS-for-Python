"""Taking out of a document everything that is not what it shows.

Each piece was already removable on its own -- ``info``, ``javascript``,
``attachments``, ``flatten_layers``, the annotation collections. What was
missing was the pass that does all of them and *says what it took*, and the
parts with no API at all: a page's additional actions, a field's keystroke and
format scripts, a link whose action launches a program, the producer's private
``/PieceInfo``, the ``/AF`` entries pointing at embedded files from outside the
name tree.

The tests that matter most here are the ones about what is left **in the
file**, not what the object model reports. Dropping a reference to an
attachment or a comment leaves the object itself in the document -- unreachable
and still written out, readable with any COS tool -- so several of these assert
on the saved bytes rather than on the API, and on the previous revision of an
incrementally-saved file, which is where a sanitizer most easily lies.
"""

from __future__ import annotations

import io

import pytest

from aspose_pdf import (
    Document,
    JavaScriptAction,
    OutlineItem,
    PageSize,
    SanitizationReport,
    XYZDestination,
)
from aspose_pdf.engine.cos import PdfArray, PdfDictionary, PdfName, PdfString
from aspose_pdf.exceptions import AsposePdfException, PdfSecurityException

SECRET_AUTHOR = "Jordan-Avery-SECRET"
SECRET_PAYLOAD = b"CLASSIFIED-PAYLOAD"
SECRET_NOTE = "a private reviewer note"
SECRET_SCRIPT = "app.alert('on open');"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _reopen(document: Document) -> Document:
    buffer = io.BytesIO()
    document.save(buffer)
    document.close()
    return Document(buffer.getvalue())


def _saved(document: Document) -> bytes:
    buffer = io.BytesIO()
    document.save(buffer)
    document.close()
    return buffer.getvalue()


def _plain() -> Document:
    """A document carrying nothing but its pages."""
    document = Document()
    document.pages.add(PageSize.A4).add_text("Visible content", 50, 700, font_size=20)
    document.pages.add(PageSize.A4).add_text("Page two", 50, 700)
    return _reopen(document)


def _dirty() -> Document:
    """A document carrying one of everything a sanitizer is for."""
    document = Document()
    first = document.pages.add(PageSize.A4)
    first.add_text("Visible content", 50, 700, font_size=20)
    second = document.pages.add(PageSize.A4)
    second.add_text("Page two", 50, 700)

    document.info = {"Title": "Internal", "Author": SECRET_AUTHOR, "Keywords": "x"}
    document.javascript["init"] = SECRET_SCRIPT
    document.actions["will_print"] = JavaScriptAction("app.alert('printing');")
    document.open_action = JavaScriptAction("app.alert('opened');")
    document.add_attachment("payload.bin", SECRET_PAYLOAD)
    first.annotations.add("Text", (100, 500, 120, 520), SECRET_NOTE, title="Reviewer")
    first.add_link([60, 690, 300, 720], "https://example.invalid")
    document.form.add_text_field("Comment", second, [50, 400, 200, 430], value="typed")
    document.layers.add("Draft watermark")
    document.layers["Draft watermark"].visible = False
    return _reopen(document)


def _catalog(document: Document) -> PdfDictionary:
    engine = document._engine_pdf
    return engine._resolve(engine._cos_doc.trailer.mapping.get(PdfName("Root")))


def _subtypes(document: Document) -> list[list[str]]:
    return [[a.subtype for a in page.annotations] for page in document.pages]


def _js_action(script: str) -> PdfDictionary:
    return PdfDictionary(
        {
            PdfName("S"): PdfName("JavaScript"),
            PdfName("JS"): PdfString(script.encode("latin-1")),
        }
    )


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


def test_a_clean_document_reports_nothing_removed():
    with _plain() as document:
        report = document.sanitize()
        assert isinstance(report, SanitizationReport)
        assert not report
        assert report.total == 0
        assert str(report) == "removed nothing"


def test_the_report_counts_what_went():
    with _dirty() as document:
        report = document.sanitize()
        assert report
        assert report.metadata_entries > 0
        assert report.scripts == 1
        assert report.actions >= 2
        assert report.attachments == 1
        assert report.layers == 1
        assert report.comments == 1
        assert report.links == 0  # off by default
        assert report.form_fields == 0  # off by default
        assert "attachments" in str(report)


def test_pages_flattened_is_not_counted_as_a_thing_removed():
    """It counts pages changed; adding it would count the layers twice."""
    report = SanitizationReport(layers=2, pages_flattened=5)
    assert report.total == 2
    assert bool(report)


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


def test_the_info_dictionary_is_emptied_and_stays_out_of_the_file():
    document = _dirty()
    document.sanitize()
    data = _saved(document)
    assert SECRET_AUTHOR.encode() not in data
    assert b"/Producer" not in data
    with Document(data) as saved:
        assert saved.info == {}


def test_an_xmp_packet_is_removed():
    document = _dirty()
    catalog = _catalog(document)
    engine = document._engine_pdf
    from aspose_pdf.engine.cos import PdfNumber, PdfStream

    packet = b"<?xpacket?><x:xmpmeta>SECRET-XMP</x:xmpmeta>"
    catalog.mapping[PdfName("Metadata")] = engine._cos_doc.register_object(
        PdfStream(packet, {PdfName("Length"): PdfNumber(len(packet))})
    )
    document.sanitize()
    data = _saved(document)
    assert b"SECRET-XMP" not in data


def test_the_producers_private_store_is_removed_from_catalog_and_pages():
    """``/PieceInfo`` is where an editor keeps its own working data (14.5)."""
    document = _dirty()
    engine = document._engine_pdf
    private = PdfDictionary(
        {PdfName("SomeEditor"): PdfDictionary({PdfName("Private"): PdfString(b"PIECE")})}
    )
    _catalog(document).mapping[PdfName("PieceInfo")] = private
    engine._get_page_dict(0).mapping[PdfName("PieceInfo")] = private
    report = document.sanitize()
    data = _saved(document)
    assert b"PIECE" not in data
    assert b"PieceInfo" not in data
    assert report.metadata_entries >= 2


def test_metadata_can_be_kept():
    document = _dirty()
    document.sanitize(metadata=False)
    with Document(_saved(document)) as saved:
        assert saved.info.get("Author") == SECRET_AUTHOR


# ---------------------------------------------------------------------------
# Scripts and actions
# ---------------------------------------------------------------------------


def test_document_scripts_go_with_their_name_tree():
    document = _dirty()
    document.sanitize()
    data = _saved(document)
    assert SECRET_SCRIPT.encode() not in data
    with Document(data) as saved:
        assert dict(saved.javascript) == {}


def test_the_catalogs_additional_actions_and_open_action_go():
    document = _dirty()
    document.sanitize()
    with Document(_saved(document)) as saved:
        assert dict(saved.actions) == {}
        assert saved.open_action is None


def test_an_open_action_that_only_names_a_page_is_kept():
    """A destination says which page to show; it runs nothing (12.6.2)."""
    document = _plain()
    document.open_action = XYZDestination(page=1, top=700)
    document.sanitize()
    with Document(_saved(document)) as saved:
        assert saved.open_action is not None


def test_a_pages_additional_actions_go():
    document = _plain()
    engine = document._engine_pdf
    engine._get_page_dict(0).mapping[PdfName("AA")] = PdfDictionary(
        {PdfName("O"): _js_action("pageOpenScript();")}
    )
    report = document.sanitize()
    assert report.actions == 1
    assert b"pageOpenScript" not in _saved(document)


def test_a_fields_keystroke_and_format_scripts_go():
    document = _dirty()
    engine = document._engine_pdf
    for page_index in range(len(document.pages)):
        page = engine._get_page_dict(page_index)
        annots = engine._resolve(page.mapping.get(PdfName("Annots")))
        for reference in annots.items if isinstance(annots, PdfArray) else []:
            annotation = engine._resolve(reference)
            if engine._get_name(annotation.mapping.get(PdfName("Subtype"))) == "Widget":
                annotation.mapping[PdfName("AA")] = PdfDictionary(
                    {
                        PdfName("K"): _js_action("keystrokeScript();"),
                        PdfName("F"): _js_action("formatScript();"),
                    }
                )
    document.sanitize()
    data = _saved(document)
    assert b"keystrokeScript" not in data
    assert b"formatScript" not in data


def test_a_link_that_launches_a_program_loses_its_action_but_stays_a_link():
    document = _plain()
    page = document.pages[0]
    page.add_link([60, 690, 300, 720], "https://example.invalid")
    engine = document._engine_pdf
    annots = engine._resolve(engine._get_page_dict(0).mapping.get(PdfName("Annots")))
    annotation = engine._resolve(annots.items[0])
    annotation.mapping[PdfName("A")] = PdfDictionary(
        {PdfName("S"): PdfName("Launch"), PdfName("F"): PdfString(b"evil.exe")}
    )
    report = document.sanitize()
    data = _saved(document)
    assert report.actions == 1
    assert b"evil.exe" not in data
    with Document(data) as saved:
        assert _subtypes(saved)[0] == ["Link"]


def test_a_link_that_opens_a_url_keeps_working():
    document = _plain()
    document.pages[0].add_link([60, 690, 300, 720], "https://example.invalid")
    document.sanitize()
    data = _saved(document)
    assert b"example.invalid" in data
    with Document(data) as saved:
        assert _subtypes(saved)[0] == ["Link"]


def test_an_action_chained_behind_a_harmless_one_is_still_found():
    """``/Next`` can hide a script behind a navigation action (12.6.1)."""
    document = _plain()
    document.pages[0].add_link([60, 690, 300, 720], "https://example.invalid")
    engine = document._engine_pdf
    annots = engine._resolve(engine._get_page_dict(0).mapping.get(PdfName("Annots")))
    annotation = engine._resolve(annots.items[0])
    annotation.mapping[PdfName("A")] = PdfDictionary(
        {
            PdfName("S"): PdfName("URI"),
            PdfName("URI"): PdfString(b"https://example.invalid"),
            PdfName("Next"): PdfArray([_js_action("chainedScript();")]),
        }
    )
    report = document.sanitize()
    assert report.actions == 1
    assert b"chainedScript" not in _saved(document)


def test_a_plain_navigation_action_with_no_chain_is_kept():
    document = _plain()
    document.pages[0].add_link([60, 690, 300, 720], "https://example.invalid")
    engine = document._engine_pdf
    annots = engine._resolve(engine._get_page_dict(0).mapping.get(PdfName("Annots")))
    annotation = engine._resolve(annots.items[0])
    annotation.mapping[PdfName("A")] = PdfDictionary(
        {
            PdfName("S"): PdfName("URI"),
            PdfName("URI"): PdfString(b"https://kept.invalid"),
        }
    )
    assert document.sanitize().actions == 0
    assert b"kept.invalid" in _saved(document)


def test_scripts_can_be_kept():
    document = _dirty()
    document.sanitize(javascript=False)
    with Document(_saved(document)) as saved:
        assert dict(saved.javascript) == {"init": SECRET_SCRIPT}


# ---------------------------------------------------------------------------
# Embedded files
# ---------------------------------------------------------------------------


def test_an_attachment_goes_and_its_bytes_go_with_it():
    """Dropping the name-tree entry is not enough: the stream is still there."""
    document = _dirty()
    report = document.sanitize()
    data = _saved(document)
    assert report.attachments == 1
    assert SECRET_PAYLOAD not in data
    assert b"payload.bin" not in data
    with Document(data) as saved:
        assert list(saved.attachments) == []


def test_the_associated_file_entries_go_too():
    document = _dirty()
    assert PdfName("AF") in _catalog(document).mapping
    document.sanitize()
    with Document(_saved(document)) as saved:
        assert PdfName("AF") not in _catalog(saved).mapping


def test_attachments_can_be_kept():
    document = _dirty()
    document.sanitize(attachments=False)
    with Document(_saved(document)) as saved:
        assert list(saved.attachments) == ["payload.bin"]


# ---------------------------------------------------------------------------
# Layers
# ---------------------------------------------------------------------------


def test_a_hidden_layer_is_resolved_away():
    document = _dirty()
    report = document.sanitize()
    data = _saved(document)
    assert report.layers == 1
    assert b"Draft watermark" not in data
    with Document(data) as saved:
        assert len(saved.layers) == 0
        assert PdfName("OCProperties") not in _catalog(saved).mapping


def test_layers_can_be_kept():
    document = _dirty()
    document.sanitize(hidden_layers=False)
    with Document(_saved(document)) as saved:
        assert [layer.name for layer in saved.layers] == ["Draft watermark"]


def test_a_document_without_layers_reports_none():
    with _plain() as document:
        assert document.sanitize().layers == 0


# ---------------------------------------------------------------------------
# Annotations and forms
# ---------------------------------------------------------------------------


def test_a_comment_goes_with_its_author_and_its_text():
    document = _dirty()
    report = document.sanitize()
    data = _saved(document)
    assert report.comments == 1
    assert SECRET_NOTE.encode() not in data
    assert b"Reviewer" not in data


def test_links_and_widgets_are_left_alone_by_default():
    document = _dirty()
    document.sanitize()
    with Document(_saved(document)) as saved:
        assert _subtypes(saved) == [["Link"], ["Widget"]]
        assert [(f.name, f.value) for f in saved.form] == [("Comment", "typed")]


def test_links_go_when_asked_for():
    document = _dirty()
    report = document.sanitize(links=True)
    assert report.links == 1
    with Document(_saved(document)) as saved:
        assert _subtypes(saved) == [[], ["Widget"]]


def test_the_form_goes_when_asked_for():
    document = _dirty()
    report = document.sanitize(forms=True)
    assert report.form_fields == 1
    with Document(_saved(document)) as saved:
        assert list(saved.form) == []
        assert _subtypes(saved) == [["Link"], []]
        assert PdfName("AcroForm") not in _catalog(saved).mapping


def test_comments_can_be_kept():
    document = _dirty()
    document.sanitize(comments=False)
    with Document(_saved(document)) as saved:
        assert "Text" in _subtypes(saved)[0]


# ---------------------------------------------------------------------------
# What is left alone
# ---------------------------------------------------------------------------


def test_the_pages_and_their_content_are_untouched():
    document = _dirty()
    document.sanitize()
    with Document(_saved(document)) as saved:
        assert len(saved.pages) == 2
        assert [p.extract_text().strip() for p in saved.pages] == [
            "Visible content",
            "Page two",
        ]


def test_bookmarks_edited_before_the_pass_are_not_lost():
    """The outline collection is a copy; it has to be put back, not dropped."""
    document = _plain()
    document.outlines.add(OutlineItem("Edited bookmark", 1))
    document.sanitize()
    with Document(_saved(document)) as saved:
        assert [(o.title, o.page_index) for o in saved.outlines] == [
            ("Edited bookmark", 1)
        ]


def test_the_form_view_is_rebuilt_after_the_fields_go():
    document = _dirty()
    assert [f.name for f in document.form] == ["Comment"]
    document.sanitize(forms=True)
    assert list(document.form) == []
    document.close()


# ---------------------------------------------------------------------------
# The save contract
# ---------------------------------------------------------------------------


def test_an_incremental_save_is_refused_after_sanitizing():
    document = _dirty()
    document.sanitize()
    with pytest.raises(PdfSecurityException, match=r"(?i)incremental"):
        document.save(io.BytesIO(), incremental=True)
    document.close()


def test_the_previous_revision_goes_too():
    """Where a sanitizer most easily lies: the secret is in revision one."""
    first = Document()
    first.pages.add(PageSize.A4).add_text("Public", 50, 700)
    first.info = {"Author": SECRET_AUTHOR}
    first.add_attachment("payload.bin", SECRET_PAYLOAD)
    buffer = io.BytesIO()
    first.save(buffer)
    first.close()

    second = Document(buffer.getvalue())
    second.pages.add(PageSize.A4).add_text("Added later", 50, 700)
    two_revisions = io.BytesIO()
    second.save(two_revisions, incremental=True)
    second.close()
    assert SECRET_PAYLOAD in two_revisions.getvalue()

    document = Document(two_revisions.getvalue())
    document.sanitize()
    data = _saved(document)
    assert SECRET_PAYLOAD not in data
    assert SECRET_AUTHOR.encode() not in data
    with Document(data) as saved:
        assert [p.extract_text().strip() for p in saved.pages] == [
            "Public",
            "Added later",
        ]


def test_sanitizing_twice_is_harmless():
    document = _dirty()
    first = document.sanitize()
    second = document.sanitize()
    assert first
    assert not second
    document.close()


# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------


def test_everything_can_be_declined():
    document = _dirty()
    report = document.sanitize(
        metadata=False,
        javascript=False,
        attachments=False,
        hidden_layers=False,
        comments=False,
    )
    assert not report
    with Document(_saved(document)) as saved:
        assert saved.info.get("Author") == SECRET_AUTHOR
        assert list(saved.attachments) == ["payload.bin"]
        assert "Text" in _subtypes(saved)[0]


def test_a_disposed_document_refuses_to_be_sanitized():
    document = _plain()
    document.close()
    with pytest.raises(Exception, match=r"(?i)dispos"):
        document.sanitize()


def test_a_document_with_no_engine_refuses():
    document = Document()
    document._engine_pdf = None
    with pytest.raises(AsposePdfException, match="No document loaded"):
        document.sanitize()


def test_an_emptied_names_dictionary_does_not_stay_behind():
    """It held only the scripts and the files; with those gone it says nothing."""
    document = _dirty()
    assert PdfName("Names") in _catalog(document).mapping
    document.sanitize()
    with Document(_saved(document)) as saved:
        assert PdfName("Names") not in _catalog(saved).mapping


def test_a_names_dictionary_holding_something_else_is_kept():
    document = _plain()
    document.destinations["chapter-2"] = XYZDestination(page=1, top=700)
    document.sanitize()
    with Document(_saved(document)) as saved:
        assert "chapter-2" in saved.destinations

"""Filtered annotation cleanup plans changes and preserves unrelated PDF data."""

from __future__ import annotations

import datetime as dt
import io
from itertools import repeat

import pytest

from aspose_pdf import (
    AnnotationType,
    Document,
    FitDestination,
    PdfLoadLimits,
    PdfResourceLimitException,
    URIAction,
)
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfDictionary,
    PdfIndirectReference,
    PdfName,
    PdfNull,
    PdfNumber,
    PdfString,
)
from aspose_pdf.engine.simple_pdf import CosExtractor
from aspose_pdf.exceptions import (
    AsposePdfException,
    PdfParseException,
    PdfValidationException,
)

RECT = (10, 20, 30, 40)


@pytest.fixture
def document():
    with Document() as doc:
        for _ in range(3):
            doc.pages.add(size=(100, 100))
        yield doc


def _save(doc):
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def _add(
    doc,
    author="Alice",
    modified="D:20260115090000Z",
    created="D:20260101090000Z",
    subtype="Text",
    page=0,
    contents="note",
):
    properties = {}
    if modified is not None:
        properties["M"] = modified
    if created is not None:
        properties["CreationDate"] = created
    return doc.pages[page].annotations.add(
        subtype, RECT, contents, title=author, properties=properties
    )


def _objects(doc, page=0):
    pdf = doc._engine_pdf
    return [pdf._resolve(ref) for ref in pdf._annotation_refs(page)]


def _contents(doc, page=0):
    return [a.contents for a in doc.pages[page].annotations]


@pytest.mark.parametrize("conflict", ["parent", "other_owner"])
def test_ambiguous_popup_ownership_does_not_remove_another_reviewers_popup(
    document, conflict
):
    first = _add(document, author="Alice")
    first.add_popup()
    _add(document, author="Bob")
    _parent, popup, other = _objects(document)
    refs = document._engine_pdf._annotation_refs(0)
    if conflict == "parent":
        popup.mapping[PdfName("Parent")] = refs[2]
    else:
        other.mapping[PdfName("Popup")] = refs[1]
    before = _save(document)
    for dry_run in (True, False):
        with pytest.raises(PdfValidationException, match="Conflicting popup ownership"):
            document.remove_annotations(authors="Alice", dry_run=dry_run)
        assert _save(document) == before
    assert document.remove_annotations(authors=["Alice", "Bob"]) == 3


def test_filter_text_budgets_and_invalid_subtype_unicode_fail_before_mutation(document):
    _add(document)
    before = _save(document)
    original = document.load_limits
    document._load_limits = document._engine_pdf._load_limits = PdfLoadLimits(
        max_total_decoded_bytes=5
    )
    with pytest.raises(PdfResourceLimitException, match="author bytes"):
        document.remove_annotations(authors=["Alice", "Bob"])
    with pytest.raises(PdfResourceLimitException, match="subtype bytes"):
        document.remove_annotations(subtypes=["Text", "Ink"])
    document._load_limits = document._engine_pdf._load_limits = original
    with pytest.raises(PdfValidationException, match="subtypes"):
        document.remove_annotations(subtypes="\ud800", pages=[])
    assert _save(document) == before


def test_default_review_cleanup_preserves_links_widgets_files_and_unknown_kinds(
    document,
):
    for kind in (
        "Text",
        "FreeText",
        "Line",
        "Square",
        "Circle",
        "Polygon",
        "PolyLine",
        "Highlight",
        "Underline",
        "Squiggly",
        "StrikeOut",
        "Stamp",
        "Caret",
        "Ink",
        "Redact",
    ):
        _add(document, subtype=kind)
    page = document.pages[0]
    page.add_link(RECT, URIAction("https://example.test/keep"))
    field = document.form.add_text_field(
        "Alice", 0, (40, 20, 90, 40), value="Keep value"
    )
    _add(document, subtype="FileAttachment", contents="keep file icon")
    _add(document, subtype="VendorReview", contents="keep unknown")
    page.add_text("Keep page content", 10, 60)
    document.info["Title"] = "Keep title"
    document.add_attachment("keep.txt", b"keep bytes")
    content, boxes = page.content, (page.media_box, page.crop_box)
    assert document.remove_annotations(dry_run=True) == 15
    assert document.remove_annotations() == 15
    assert [a.subtype for a in page.annotations] == [
        "Link",
        "Widget",
        "FileAttachment",
        "VendorReview",
    ]
    assert page.content == content and (page.media_box, page.crop_box) == boxes
    assert field.value == "Keep value" and document.info["Title"] == "Keep title"
    assert document.attachments == {"keep.txt": b"keep bytes"}
    with Document(_save(document)) as reopened:
        assert [a.subtype for a in reopened.pages[0].annotations] == [
            "Link",
            "Widget",
            "FileAttachment",
            "VendorReview",
        ]
        assert reopened.form["Alice"].value == "Keep value"
        assert reopened.attachments == {"keep.txt": b"keep bytes"}
        assert reopened.extract_links()[0].uri == "https://example.test/keep"


@pytest.mark.parametrize(
    "authors,case_sensitive,remaining",
    [
        ("Alice", True, ["alice", "ALICE", "Bob", ""]),
        (["Alice", "Bob"], True, ["alice", "ALICE", ""]),
        ("alice", False, ["Bob", ""]),
        ([], True, ["Alice", "alice", "ALICE", "Bob", ""]),
        ("", True, ["Alice", "alice", "ALICE", "Bob"]),
    ],
)
def test_author_exact_alternatives_empty_and_casefold_filters(
    document, authors, case_sensitive, remaining
):
    for author in ("Alice", "alice", "ALICE", "Bob", ""):
        _add(document, author=author, contents=author)
    assert document.remove_annotations(
        authors=authors, case_sensitive=case_sensitive
    ) == 5 - len(remaining)
    assert _contents(document) == remaining


def test_unicode_author_casefold_and_whitespace_are_explicit(document):
    for author in ("Straße", "STRASSE", "Alice ", "Alice", "Ирина"):
        _add(document, author=author, contents=author)
    assert (
        document.remove_annotations(authors=["strasse", "Ирина"], case_sensitive=False)
        == 3
    )
    assert _contents(document) == ["Alice ", "Alice"]
    assert document.remove_annotations(authors="Alice") == 1
    assert _contents(document) == ["Alice "]


@pytest.mark.parametrize(
    "before",
    [
        dt.date(2026, 2, 1),
        dt.datetime(2026, 2, 1),
        dt.datetime(2026, 2, 1, tzinfo=dt.UTC),
        "2026-02-01",
        "2026-02-01T00:00:00Z",
        "D:20260201000000Z",
    ],
)
def test_before_accepts_supported_cutoffs_and_is_exclusive(document, before):
    for stamp in ("D:20260131235959Z", "D:20260201000000Z", "D:20260201000001Z"):
        _add(document, modified=stamp, contents=stamp)
    assert document.remove_annotations(before=before) == 1
    assert _contents(document) == ["D:20260201000000Z", "D:20260201000001Z"]


def test_after_and_combined_filters_preserve_both_date_boundaries(document):
    for index, (author, stamp, kind) in enumerate(
        [
            ("Alice", "D:20260101000000Z", "Square"),
            ("Alice", "D:20260115000000Z", "Square"),
            ("Bob", "D:20260115000000Z", "Square"),
            ("Alice", "D:20260115000000Z", "Text"),
            ("Alice", "D:20260201000000Z", "Square"),
        ]
    ):
        _add(document, author=author, modified=stamp, subtype=kind, contents=str(index))
    assert (
        document.remove_annotations(
            authors="Alice",
            after="2026-01-01",
            before="2026-02-01",
            subtypes=AnnotationType.SQUARE,
        )
        == 1
    )
    assert _contents(document) == ["0", "2", "3", "4"]


@pytest.mark.parametrize(
    "stamp,removed",
    [
        ("D:20260201010000+02'00'", 1),
        ("D:20260131230000-02'00'", 0),
        ("D:20260201000000", 0),
        ("D:20260131235959", 1),
        ("D:2026", 1),
        ("D:202601", 1),
        ("20260101", 1),
        ("D:20260101000000+0200", 1),
        ("D:20260101000000+02", 1),
    ],
)
def test_pdf_partial_dates_timezones_and_naive_utc_policy(document, stamp, removed):
    _add(document, modified=stamp)
    assert document.remove_annotations(before="2026-02-01") == removed


@pytest.mark.parametrize(
    "stamp",
    [
        None,
        "",
        "unknown",
        "D:20260101000000Zjunk",
        "D:20261301000000Z",
        "D:20260230000000Z",
        "D:20260101000000+24'00'",
        "D:20260101000000+02'60'",
        "D:٢٠٢٦0101000000Z",
        "2026-01-01",
    ],
)
def test_missing_or_invalid_pdf_dates_are_preserved_by_date_filters(document, stamp):
    _add(document, modified=stamp)
    assert document.remove_annotations(before="2026-02-01") == 0
    assert document.remove_annotations(authors="Alice") == 1


def test_created_modified_are_independent_without_fallback_and_bad_text_types_do_not_match(
    document,
):
    _add(document, modified="D:20260301000000Z", created="D:20260101000000Z")
    _add(document, modified=None, created="D:20260101000000Z")
    assert document.remove_annotations(before="2026-02-01") == 0
    assert document.remove_annotations(before="2026-02-01", date_field="created") == 2
    _add(document)
    _objects(document)[0].mapping[PdfName("M")] = PdfNumber(123)
    assert document.remove_annotations(before="2026-02-01") == 0
    _objects(document)[0].mapping[PdfName("T")] = PdfNumber(123)
    assert document.remove_annotations(authors="Alice") == 0


def test_dry_run_page_wrapper_selection_order_and_explicit_subtypes(document):
    for page in range(3):
        _add(document, page=page, contents=str(page))
    before = _save(document)
    assert document.remove_annotations(pages=iter([2, 0, 2]), dry_run=True) == 2
    assert _save(document) == before
    assert document.pages[1].annotations.remove_matching(authors="Alice") == 1
    assert document.remove_annotations(pages=[2, 0, 2]) == 2
    assert all(len(page.annotations) == 0 for page in document.pages)
    _add(document, subtype="FileAttachment")
    document.pages[0].add_link(RECT, FitDestination(1))
    assert document.remove_annotations(subtypes=["/Link", "FileAttachment"]) == 2


@pytest.mark.parametrize(
    "options",
    [
        {"authors": 1},
        {"authors": b"Alice"},
        {"authors": ["Alice", False]},
        {"authors": "\ud800"},
        {"before": True},
        {"before": "bad"},
        {"before": "D:20260101000000+99'00'"},
        {"after": []},
        {"after": "2026-02-01", "before": "2026-02-01"},
        {"after": "2026-03-01", "before": "2026-02-01"},
        {"date_field": "unknown"},
        {"date_field": None},
        {"case_sensitive": 1},
        {"dry_run": 1},
        {"subtypes": ["Widget"]},
        {"subtypes": [1]},
        {"subtypes": b"Text"},
    ],
)
def test_invalid_options_and_selection_fail_before_metadata_or_mutation(
    document, monkeypatch, options
):
    _add(document)
    before = _save(document)
    monkeypatch.setattr(
        document._engine_pdf,
        "_get_page_dict",
        lambda *a: pytest.fail("invalid options must not read annotations"),
    )
    with pytest.raises(PdfValidationException):
        document.remove_annotations(pages=[], **options)
    monkeypatch.undo()
    assert _save(document) == before


def test_complete_selection_validation_and_empty_filters_do_not_read_metadata(
    document, monkeypatch
):
    _add(document)
    monkeypatch.setattr(
        document._engine_pdf,
        "_get_page_dict",
        lambda *a: pytest.fail("must not read annotations"),
    )
    for pages in ([0, 3], [-1], [False], [0.0], "0", 0):
        with pytest.raises(PdfValidationException):
            document.remove_annotations(pages=pages)
    assert document.remove_annotations(pages=[]) == 0
    assert document.remove_annotations(authors=[]) == 0
    assert document.remove_annotations(subtypes=[]) == 0


def test_owned_popups_are_counted_and_surviving_reply_threads_are_detached(document):
    note = _add(document, contents="parent")
    note.add_popup()
    reply = note.reply("reply", title="Bob")
    reply.add_popup()
    reply.reply("nested", title="Carol")
    before = _save(document)
    assert document.remove_annotations(authors="Alice", dry_run=True) == 2
    assert _save(document) == before
    assert document.remove_annotations(authors="Alice") == 2
    assert _contents(document) == ["reply", "", "nested"]
    current = document.pages[0].annotations
    assert current[0].in_reply_to is None and "RT" not in current[0].properties
    assert current[0].popup.parent_annotation.contents == "reply"
    assert current[2].in_reply_to.contents == "reply"
    with Document(_save(document)) as reopened:
        current = reopened.pages[0].annotations
        assert current[0].in_reply_to is None and current[0].popup is not None
        assert current[2].in_reply_to.contents == "reply"


@pytest.mark.parametrize("edge", ["both", "owner_only", "popup_only"])
def test_one_way_popup_relationships_are_included_in_cleanup(document, edge):
    note = _add(document)
    note.add_popup()
    parent, popup = _objects(document)
    if edge == "owner_only":
        popup.mapping.pop(PdfName("Parent"))
    if edge == "popup_only":
        parent.mapping.pop(PdfName("Popup"))
    assert document.remove_annotations(authors="Alice") == 2
    assert len(document.pages[0].annotations) == 0


def test_explicit_popup_removal_keeps_owner_and_clears_its_popup_link(document):
    note = _add(document)
    note.add_popup()
    assert document.remove_annotations(subtypes="Popup") == 1
    assert _contents(document) == ["note"]
    assert document.pages[0].annotations[0].popup is None


def test_cross_page_reply_links_are_repaired_without_removing_replies(document):
    _add(document, contents="parent")
    _add(document, author="Bob", page=1, contents="reply")
    pdf = document._engine_pdf
    reply = _objects(document, 1)[0]
    reply.mapping[PdfName("IRT")] = pdf._annotation_refs(0)[0]
    reply.mapping[PdfName("RT")] = PdfName("Reply")
    original_array = pdf._get_page_dict(1).mapping[PdfName("Annots")]
    assert document.remove_annotations(pages=[0], authors="Alice") == 1
    assert _contents(document, 1) == ["reply"]
    assert PdfName("IRT") not in reply.mapping and PdfName("RT") not in reply.mapping
    assert pdf._get_page_dict(1).mapping[PdfName("Annots")] is original_array


def test_cross_page_popup_scope_failure_is_atomic_and_broader_selection_succeeds(
    document,
):
    _add(document)
    _add(document, subtype="Popup", page=1, author="")
    pdf = document._engine_pdf
    owner, popup = _objects(document)[0], _objects(document, 1)[0]
    owner.mapping[PdfName("Popup")] = pdf._annotation_refs(1)[0]
    popup.mapping[PdfName("Parent")] = pdf._annotation_refs(0)[0]
    before = _save(document)
    for dry_run in (True, False):
        with pytest.raises(PdfValidationException, match="orphan a popup"):
            document.remove_annotations(pages=[0], authors="Alice", dry_run=dry_run)
        assert _save(document) == before
    assert document.remove_annotations(pages=[0, 1], authors="Alice") == 2
    assert all(len(page.annotations) == 0 for page in document.pages)


@pytest.mark.parametrize("indirect", [False, True])
def test_shared_arrays_are_copied_and_shared_surviving_targets_keep_reply_links(
    document, indirect
):
    _add(document, contents="shared parent")
    _add(document, author="Bob", page=2, contents="reply")
    pdf = document._engine_pdf
    first, second = pdf._get_page_dict(0), pdf._get_page_dict(1)
    array = first.mapping[PdfName("Annots")]
    if indirect:
        array = pdf._cos_doc.register_object(array)
    first.mapping[PdfName("Annots")] = second.mapping[PdfName("Annots")] = array
    reply = _objects(document, 2)[0]
    target = pdf._annotation_refs(0)[0]
    reply.mapping[PdfName("IRT")] = target
    reply.mapping[PdfName("RT")] = PdfName("Reply")
    assert document.remove_annotations(pages=[0]) == 1
    assert _contents(document) == [] and _contents(document, 1) == ["shared parent"]
    assert second.mapping[PdfName("Annots")] is array
    assert reply.mapping[PdfName("IRT")] is target and reply.mapping[
        PdfName("RT")
    ] == PdfName("Reply")


def test_later_malformed_metadata_and_indirect_cycles_leave_prior_matches_untouched(
    document,
):
    _add(document)
    pdf = document._engine_pdf
    last = pdf._get_page_dict(2)
    last.mapping[PdfName("Annots")] = PdfNumber(1)
    before = _save(document)
    with pytest.raises(PdfParseException, match="Annots"):
        document.remove_annotations(pages=[0])
    assert _save(document) == before
    cycle = pdf._cos_doc.register_object(PdfNull())
    pdf._cos_doc.objects[cycle.object_number] = cycle
    last.mapping[PdfName("Annots")] = cycle
    before = _save(document)
    with pytest.raises(PdfParseException, match="cycle"):
        document.remove_annotations(pages=[0])
    assert _save(document) == before


def test_no_match_does_not_scan_unselected_annotation_metadata(document):
    _add(document)
    document._engine_pdf._get_page_dict(2).mapping[PdfName("Annots")] = PdfNumber(1)
    assert document.remove_annotations(pages=[0], authors="Nobody") == 0


def test_runtime_commit_failure_rolls_back_page_arrays_and_reply_edits(document):
    _add(document, page=0)
    _add(document, page=1)
    _add(document, page=2, author="Bob", contents="reply")
    pdf = document._engine_pdf
    reply = _objects(document, 2)[0]
    reply.mapping[PdfName("IRT")] = pdf._annotation_refs(0)[0]
    reply.mapping[PdfName("RT")] = PdfName("Reply")

    class FailOnce(dict):
        failed = False

        def __setitem__(self, key, value):
            if key == PdfName("Annots") and not self.failed:
                self.failed = True
                raise RuntimeError("Cannot install annotation array")
            return super().__setitem__(key, value)

    pdf._get_page_dict(1).mapping = FailOnce(pdf._get_page_dict(1).mapping)
    before = _save(document)
    with pytest.raises(RuntimeError, match="Cannot install"):
        document.remove_annotations(authors="Alice")
    assert _save(document) == before
    assert PdfName("IRT") in reply.mapping and PdfName("RT") in reply.mapping


def test_limits_bound_generators_strings_and_metadata_before_any_edit(document):
    _add(document)
    before = _save(document)
    pdf = document._engine_pdf
    original = document.load_limits
    for options in ({"pages": repeat(0)}, {"authors": repeat("Alice")}):
        document._load_limits = pdf._load_limits = PdfLoadLimits(max_container_items=20)
        with pytest.raises(PdfResourceLimitException):
            document.remove_annotations(**options)
    document._load_limits = pdf._load_limits = PdfLoadLimits(max_object_bytes=4)
    with pytest.raises(PdfResourceLimitException, match="max_object_bytes"):
        document.remove_annotations(authors="Alice")
    document._load_limits = pdf._load_limits = original
    assert _save(document) == before
    for _ in range(10):
        _add(document)
    before = _save(document)
    document._load_limits = pdf._load_limits = PdfLoadLimits(max_container_items=20)
    with pytest.raises(PdfResourceLimitException, match="metadata work"):
        document.remove_annotations()
    document._load_limits = pdf._load_limits = original
    assert _save(document) == before


@pytest.mark.parametrize("kind", ["bytes", "file", "streaming"])
@pytest.mark.parametrize("encrypted", [False, True])
def test_streaming_encryption_roundtrip_preserve_sources_and_do_not_decode_content(
    tmp_path, monkeypatch, kind, encrypted
):
    path, output = tmp_path / "source.pdf", tmp_path / "cleaned.pdf"
    with Document() as source:
        for _ in range(3):
            source.pages.add(size=(100, 100)).add_text("Keep page content", 5, 60)
        _add(source, author="Alice", page=1)
        _add(source, author="Bob", page=1)
        source.info["Title"] = "Keep title"
        source.add_attachment("keep.txt", b"keep")
        if encrypted:
            source.encrypt("secret", "owner")
        source.save(path)
    before = path.read_bytes()
    opener = Document.open_streaming if kind == "streaming" else Document
    with opener(
        before if kind == "bytes" else path, password="secret" if encrypted else None
    ) as doc:
        pdf = doc._engine_pdf
        content, text = list(pdf.page_contents), pdf._extracted_text
        cursor, protection = (pdf._image_names, pdf._image_cursor), doc.is_encrypted
        with monkeypatch.context() as patch:
            patch.setattr(
                pdf,
                "get_page_content",
                lambda *a: pytest.fail("must not read page content"),
            )
            patch.setattr(
                CosExtractor,
                "_decode_stream",
                lambda *a, **k: pytest.fail("must not decode streams"),
            )
            assert doc.remove_annotations(pages=[1], authors="Alice", dry_run=True) == 1
            assert doc.remove_annotations(pages=[1], authors="Alice") == 1
        assert (
            _contents(doc, 1) == ["note"]
            and doc.pages[1].annotations[0].author == "Bob"
        )
        assert pdf.page_contents == content and pdf._extracted_text is text
        assert (
            pdf._image_names,
            pdf._image_cursor,
        ) == cursor and doc.is_encrypted is protection
        assert doc.info["Title"] == "Keep title" and doc.attachments == {
            "keep.txt": b"keep"
        }
        doc.save(output)
    with Document(output, password="secret" if encrypted else None) as reopened:
        assert [a.author for a in reopened.pages[1].annotations] == ["Bob"]
        assert "Keep page content" in reopened.pages[1].extract_text()
    assert path.read_bytes() == before


def test_empty_documents_and_disposal_guards():
    with Document() as document:
        assert document.remove_annotations() == 0
        page = document.pages.add(size=(100, 100))
        collection = page.annotations
    with pytest.raises(AsposePdfException, match="disposed"):
        document.remove_annotations()
    with pytest.raises(AsposePdfException, match="disposed"):
        collection.remove_matching()


@pytest.mark.parametrize(
    "entry",
    [
        PdfNumber(1),
        PdfDictionary({}),
        PdfDictionary({PdfName("Subtype"): PdfString("Text")}),
        PdfDictionary({PdfName("Subtype"): PdfName("")}),
    ],
)
def test_malformed_later_annotation_entries_fail_before_removal(document, entry):
    _add(document)
    document._engine_pdf._get_page_dict(2).mapping[PdfName("Annots")] = PdfArray(
        [entry]
    )
    before = _save(document)
    with pytest.raises(PdfParseException):
        document.remove_annotations(pages=[0])
    assert _save(document) == before


def test_missing_annotation_array_references_are_not_treated_as_empty_pages(document):
    _add(document)
    document._engine_pdf._get_page_dict(2).mapping[PdfName("Annots")] = (
        PdfIndirectReference(9999)
    )
    before = _save(document)
    with pytest.raises(PdfParseException, match="reference was not found"):
        document.remove_annotations(pages=[0])
    assert _save(document) == before


def test_aware_cutoff_and_unrepresentable_utc_dates_are_handled_before_mutation(
    document,
):
    _add(document, modified="D:20260131235959Z")
    cutoff = dt.datetime(2026, 2, 1, 2, tzinfo=dt.timezone(dt.timedelta(hours=2)))
    assert document.remove_annotations(before=cutoff) == 1
    _add(document, modified="D:99991231235959-23'00'")
    assert document.remove_annotations(before="9999-12-31") == 0
    before = _save(document)
    with pytest.raises(PdfValidationException, match="before"):
        document.remove_annotations(
            before=dt.datetime(1, 1, 1, tzinfo=dt.timezone(dt.timedelta(hours=2)))
        )
    assert _save(document) == before

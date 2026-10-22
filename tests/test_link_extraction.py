"""Detached inventories include valid, invalid and chained link targets."""

from __future__ import annotations

import io
import math
from dataclasses import FrozenInstanceError, replace
from itertools import repeat

import pytest

from aspose_pdf import (
    Document,
    ExtractedLink,
    FitBDestination,
    FitBHDestination,
    FitBVDestination,
    FitDestination,
    FitHDestination,
    FitRDestination,
    FitVDestination,
    GoToAction,
    GoToRAction,
    JavaScriptAction,
    LaunchAction,
    NamedAction,
    PdfLoadLimits,
    PdfResourceLimitException,
    SubmitFormAction,
    URIAction,
    XYZDestination,
)
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
from aspose_pdf.engine.simple_pdf import CosExtractor
from aspose_pdf.exceptions import (
    AsposePdfException,
    PdfParseException,
    PdfValidationException,
)
from aspose_pdf.link_extraction import ExtractedLink as ModuleLink

RECT = (10, 20, 100, 40)


@pytest.fixture
def document():
    with Document() as doc:
        for _ in range(3):
            doc.pages.add()
        yield doc


def _save(doc):
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def _catalog(doc):
    pdf = doc._engine_pdf
    pdf._ensure_cos()
    return pdf._resolve(pdf._cos_doc.trailer.mapping[PdfName("Root")])


def _entry(doc, page=0, **entries):
    doc.pages[page].add_link(RECT, FitDestination(0))
    pdf = doc._engine_pdf
    annotation = pdf._resolve(pdf._annotation_refs(page)[-1])
    annotation.mapping.pop(PdfName("Dest"), None)
    annotation.mapping.update({PdfName(key): value for key, value in entries.items()})
    return annotation


def _action(kind="GoTo", **entries):
    return PdfDictionary(
        {PdfName("S"): PdfName(kind), **{PdfName(k): v for k, v in entries.items()}}
    )


def _destination(doc, page=1, kind="Fit", params=()):
    return PdfArray(
        [
            doc._engine_pdf._make_page_ref(page),
            PdfName(kind),
            *[PdfNull() if p is None else PdfNumber(p) for p in params],
        ]
    )


def _names(doc, pairs):
    _catalog(doc).mapping[PdfName("Names")] = PdfDictionary(
        {
            PdfName("Dests"): PdfDictionary(
                {
                    PdfName("Names"): PdfArray(
                        [
                            item
                            for name, value in pairs
                            for item in (PdfString(name), value)
                        ]
                    )
                }
            )
        }
    )


@pytest.mark.parametrize(
    "uri",
    [
        "https://example.test/a?q=1#part",
        "mailto:team@example.test",
        "tel:+123456",
        "relative.pdf",
        "https://example.test/日本",
    ],
)
def test_public_uri_records_are_frozen_detached_and_keep_each_occurrence(document, uri):
    assert ModuleLink is ExtractedLink
    document.pages[1].add_link(RECT, URIAction(uri))
    document.pages[1].add_link(RECT, URIAction(uri))
    records = document.pages[1].extract_links()
    assert len(records) == 2
    assert [r.annotation_index for r in records] == [0, 1]
    for record in records:
        assert record.page_index == 1 and record.rect == RECT
        assert record.action_type == "URI" and record.action_path == "A"
        assert record.uri == record.resolved_uri == uri
        assert record.target_page_index is None and record.issues == ()
        with pytest.raises(FrozenInstanceError):
            record.uri = "changed"
    document.dispose()
    assert records[0].uri == uri


@pytest.mark.parametrize(
    "target",
    [
        FitDestination(1),
        FitBDestination(2),
        XYZDestination(0, None, 100, 0),
        FitHDestination(1, 100),
        FitBHDestination(1),
        FitVDestination(1, 20),
        FitBVDestination(2),
        FitRDestination(2, 1, 2, 10, 20),
    ],
)
@pytest.mark.parametrize("action", [False, True])
def test_all_local_views_roundtrip_and_typed_targets(document, target, action):
    document.pages[0].add_link(RECT, GoToAction(target) if action else target)
    before = document.extract_links()
    (record,) = before
    assert record.destination == target
    assert record.target_page_index == target.page
    assert record.action_type == "GoTo" and record.issues == ()
    assert record.action_path == ("A" if action else "Dest")
    with Document(_save(document)) as reopened:
        assert reopened.extract_links() == before


def test_both_named_formats_aliases_dictionary_and_reference_targets(document):
    target = _destination(document, 2, "XYZ", (10, 400, None))
    _catalog(document).mapping[PdfName("Dests")] = PdfDictionary(
        {PdfName("legacy"): target}
    )
    ref = document._engine_pdf._cos_doc.register_object(
        PdfDictionary({PdfName("D"): PdfString("legacy")})
    )
    _names(document, [(b"alias", ref), (b"binary\xe9\x00", target)])
    _entry(document, Dest=PdfString("alias"))
    _entry(document, A=_action(D=PdfString(b"binary\xe9\x00")))
    first, second = document.extract_links()
    assert first.destination_name == "alias" and first.target_page_index == 2
    assert second.destination_name == "binaryé\x00" and second.target_page_index == 2
    assert first.destination == second.destination == XYZDestination(2, 10, 400, None)
    assert not first.issues and not second.issues


def test_name_string_priorities_and_literal_slash_names(document):
    _names(document, [("same", _destination(document, 1))])
    _catalog(document).mapping[PdfName("Dests")] = PdfDictionary(
        {
            PdfName("same"): _destination(document, 2),
            PdfName("//chapter"): _destination(document, 1),
        }
    )
    _entry(document, Dest=PdfString("same"))
    _entry(document, Dest=PdfName("same"))
    _entry(document, Dest=PdfName("//chapter"))
    assert [r.target_page_index for r in document.extract_links()] == [1, 2, 1]
    assert document.extract_links()[2].destination_name == "/chapter"


def test_local_page_resolution_follows_unsaved_insertions(document):
    document.pages[0].add_link(RECT, FitDestination(2))
    document.pages.insert(1)
    (record,) = document.extract_links()
    assert record.target_page_index == 3 and record.destination == FitDestination(3)


def test_remote_launch_named_submit_and_unknown_actions(document):
    actions = [
        GoToRAction("next.pdf", XYZDestination(7, 10, 200, 1)),
        LaunchAction("notes.txt"),
        NamedAction("NextPage"),
        SubmitFormAction("https://example.test/submit"),
        JavaScriptAction("secret()"),
    ]
    for action in actions:
        document.pages[0].add_link(RECT, action)
    remote, launch, named, submit, script = document.extract_links()
    assert remote.file == "next.pdf" and remote.destination == XYZDestination(
        7, 10, 200, 1
    )
    assert remote.target_page_index is None and not remote.issues
    assert launch.file == "notes.txt" and not launch.issues
    assert named.named_action == "NextPage" and not named.issues
    assert submit.file == "https://example.test/submit" and not submit.issues
    assert script.action_type == "JavaScript" and not script.issues
    assert "secret" not in repr(script)


def test_remote_named_destination_and_unicode_file_spec_preference(document):
    _entry(
        document,
        A=_action(
            "GoToR",
            F=PdfDictionary(
                {
                    PdfName("UF"): PdfString("資料.pdf"),
                    PdfName("F"): PdfString("fallback.pdf"),
                }
            ),
            D=PdfString("chapter"),
        ),
    )
    (record,) = document.extract_links()
    assert record.file == "資料.pdf" and record.destination_name == "chapter"
    assert record.destination is None and not record.issues


@pytest.mark.parametrize(
    "uri,expected",
    [
        ("../next.pdf?q=1#part", "https://example.test/next.pdf?q=1#part"),
        ("#part", "https://example.test/docs/index.html#part"),
        ("https://other.test/", "https://other.test/"),
        ("mailto:a@example.test", "mailto:a@example.test"),
    ],
)
def test_relative_uri_resolution_uses_catalog_base_without_fetching(
    document, uri, expected
):
    _catalog(document).mapping[PdfName("URI")] = PdfDictionary(
        {PdfName("Base"): PdfString("https://example.test/docs/index.html")}
    )
    document.pages[0].add_link(RECT, URIAction(uri))
    (record,) = document.extract_links()
    assert record.uri == uri and record.resolved_uri == expected and not record.issues


def test_next_chains_branch_preorder_shared_nodes_and_cycles(document):
    child = _action("URI", URI=PdfString("https://example.test/child"))
    action = _action(
        "Named",
        N=PdfName("NextPage"),
        Next=PdfArray([child, child, _action(D=_destination(document, 2))]),
    )
    _entry(document, A=action)
    records = document.extract_links()
    assert [r.action_path for r in records] == [
        "A",
        "A.Next[0]",
        "A.Next[1]",
        "A.Next[2]",
    ]
    assert [r.action_type for r in records] == ["Named", "URI", "URI", "GoTo"]
    assert all(not r.issues for r in records)
    child.mapping[PdfName("Next")] = action
    records = document.extract_links()
    assert [(r.action_path, r.issues) for r in records if r.issues] == [
        ("A.Next[0].Next[0]", ("cyclic_reference",)),
        ("A.Next[1].Next[0]", ("cyclic_reference",)),
    ]


@pytest.mark.parametrize(
    "bad,issue",
    [
        (None, "missing_target"),
        (PdfString("missing"), "missing_named_destination"),
        (PdfNumber(1), "invalid_destination"),
        (PdfArray([PdfIndirectReference(9999), PdfName("Fit")]), "missing_page"),
        (PdfArray([PdfNumber(0), PdfName("Fit")]), "invalid_destination"),
    ],
)
def test_bad_local_targets_are_retained(document, bad, issue):
    _entry(document, **({} if bad is None else {"Dest": bad}))
    (record,) = document.extract_links()
    assert record.issues == (issue,) and record.destination is None
    if isinstance(bad, PdfString):
        assert record.destination_name == "missing"


def test_conflicting_targets_and_indirect_null_are_handled(document):
    first = _entry(
        document,
        Dest=_destination(document),
        A=_action("URI", URI=PdfString("https://example.test/")),
    )
    records = document.extract_links()
    assert len(records) == 2 and all(
        r.issues == ("conflicting_targets",) for r in records
    )
    first.mapping[PdfName("Dest")] = document._engine_pdf._cos_doc.register_object(
        PdfNull()
    )
    (record,) = document.extract_links()
    assert record.action_type == "URI" and record.issues == ()


@pytest.mark.parametrize(
    "rect,expected,issues",
    [
        ((100, 40, 10, 20), RECT, ()),
        ((0, 0, 0, 0), (0, 0, 0, 0), ()),
        ((0, 1, 2), None, ("invalid_rect",)),
        ((0, 1, math.nan, 4), None, ("invalid_rect",)),
        ((0, True, 2, 4), None, ("invalid_rect",)),
    ],
)
def test_rectangle_normalization_and_invalid_geometry(document, rect, expected, issues):
    _entry(
        document,
        Dest=_destination(document),
        Rect=PdfArray([PdfNumber(v) for v in rect]),
    )
    (record,) = document.extract_links()
    assert (
        record.rect == expected
        and record.issues == issues
        and record.target_page_index == 1
    )


def test_invalid_actions_and_siblings_do_not_hide_valid_targets(document):
    _entry(
        document,
        A=_action(
            "URI",
            URI=PdfNumber(123),
            Next=PdfArray(
                [
                    PdfString("bad"),
                    _action("URI", URI=PdfString("https://example.test/good")),
                ]
            ),
        ),
    )
    first, second, third = document.extract_links()
    assert first.issues == ("invalid_uri",)
    assert second.action_path == "A.Next[0]" and second.issues == ("invalid_action",)
    assert third.uri == "https://example.test/good" and not third.issues


def test_selection_page_wrapper_order_nonlinks_and_empty_documents(
    document, monkeypatch
):
    document.pages[0].add_link(RECT, FitDestination(1))
    document.pages[2].add_link(RECT, URIAction("https://example.test/"))
    _entry(document, page=2, Subtype=PdfName("Text"), Dest=_destination(document))
    records = document.extract_links(pages=iter([2, 0, 2]))
    assert [(r.page_index, r.annotation_index) for r in records] == [(2, 0), (0, 0)]
    assert document.pages[2].extract_links() == records[:1]
    assert document.extract_links(pages=[]) == []
    monkeypatch.setattr(
        document._engine_pdf,
        "_get_page_dict",
        lambda *a: pytest.fail("selection must validate first"),
    )
    for pages in ([0, 3], [-1], [True], [0.0], "0", 0):
        with pytest.raises(PdfValidationException):
            document.extract_links(pages=pages)
    with Document() as empty:
        assert empty.extract_links() == []


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("encrypted", [False, True])
def test_roundtrip_encryption_streaming_no_content_decoding_or_mutation(
    tmp_path, streaming, encrypted, monkeypatch
):
    path = tmp_path / "links.pdf"
    with Document() as source:
        for _ in range(3):
            source.pages.add()
        source.pages[2].add_link(RECT, URIAction("https://example.test/資料"))
        source.pages[2].add_link(RECT, FitDestination(1))
        source.info["Title"] = "Keep title"
        source.add_attachment("keep.txt", b"keep")
        expected = source.pages[2].extract_links()
        if encrypted:
            source.encrypt("secret", "owner")
        source.save(path)
    before = path.read_bytes()
    opener = Document.open_streaming if streaming else Document
    with opener(path, password="secret" if encrypted else None) as reopened:
        pdf = reopened._engine_pdf
        cursor = pdf._image_cursor, pdf._image_names
        content, text = list(pdf.page_contents), pdf._extracted_text
        cache = set(dict.keys(pdf.images)), set(getattr(pdf.images, "_loaders", {}))
        monkeypatch.setattr(
            CosExtractor,
            "_decode_stream",
            lambda *a, **k: pytest.fail("no streams may be decoded"),
        )
        monkeypatch.setattr(
            pdf, "get_page_content", lambda *a: pytest.fail("no page content")
        )
        assert reopened.extract_links(pages=[2]) == expected
        assert (pdf._image_cursor, pdf._image_names) == cursor
        assert pdf.page_contents == content and pdf._extracted_text is text
        assert (
            set(dict.keys(pdf.images)),
            set(getattr(pdf.images, "_loaders", {})),
        ) == cache
        assert reopened.info["Title"] == "Keep title"
        assert reopened.attachments == {"keep.txt": b"keep"}
    assert path.read_bytes() == before


def test_resource_limits_bound_selection_metadata_depth_and_report_text():
    with Document() as document:
        document.pages.add().add_link(RECT, URIAction("https://example.test/long"))
        limits = replace(document.load_limits, max_container_items=10)
        document._load_limits = document._engine_pdf._load_limits = limits
        with pytest.raises(PdfResourceLimitException):
            document.extract_links(pages=repeat(0))
        document._load_limits = document._engine_pdf._load_limits = PdfLoadLimits(
            max_object_bytes=8
        )
        with pytest.raises(PdfResourceLimitException, match="max_object_bytes"):
            document.extract_links()
        document._load_limits = document._engine_pdf._load_limits = PdfLoadLimits(
            max_total_decoded_bytes=8
        )
        with pytest.raises(PdfResourceLimitException, match="report text"):
            document.extract_links()
    with Document() as document:
        document.pages.add()
        action = _action("Named", N=PdfName("NextPage"))
        for _ in range(4):
            action = _action("Named", N=PdfName("NextPage"), Next=action)
        _entry(document, A=action)
        document._load_limits = document._engine_pdf._load_limits = PdfLoadLimits(
            max_nesting_depth=3
        )
        with pytest.raises(PdfResourceLimitException, match="max_nesting_depth"):
            document.extract_links()


def test_malformed_annotation_containers_raise_and_disposed_guard(document):
    page = document._engine_pdf._get_page_dict(0)
    page.mapping[PdfName("Annots")] = PdfString("invalid")
    with pytest.raises(PdfParseException):
        document.extract_links()
    document.dispose()
    with pytest.raises(AsposePdfException):
        document.extract_links()


@pytest.mark.parametrize(
    "target",
    [
        FitDestination(0),
        FitBDestination(5),
        XYZDestination(7),
        FitHDestination(9),
        FitBHDestination(8, 100),
        FitVDestination(2),
        FitBVDestination(3, 20),
        FitRDestination(6, 1, 2, 10, 20),
    ],
)
def test_remote_views_keep_remote_page_numbers_across_roundtrip(document, target):
    document.pages[0].add_link(RECT, GoToRAction("other.pdf", target))
    records = document.extract_links()
    (record,) = records
    assert record.destination == target and record.target_page_index is None
    assert record.file == "other.pdf" and not record.issues
    with Document(_save(document)) as reopened:
        assert reopened.extract_links() == records


@pytest.mark.parametrize(
    "target",
    [
        PdfArray([PdfNumber(0), PdfString("Fit")]),
        PdfArray([PdfNumber(-1), PdfName("Fit")]),
        PdfArray([PdfNumber(True), PdfName("Fit")]),
        PdfArray([PdfNumber(1.5), PdfName("Fit")]),
        PdfArray([PdfNumber(0), PdfName("XYZ"), PdfNull(), PdfNull(), PdfNumber(-1)]),
        PdfArray([PdfNumber(0), PdfName("FitR"), *[PdfNumber(1)] * 4]),
        PdfArray([PdfNumber(0), PdfName("FitH")]),
        PdfArray([PdfNumber(0), PdfName("Fit"), PdfNumber(1)]),
        PdfArray([PdfNumber(0), PdfName("FitH"), PdfNumber(math.inf)]),
    ],
)
def test_invalid_remote_views_keep_file_and_report_diagnostic(document, target):
    _entry(document, A=_action("GoToR", F=PdfString("other.pdf"), D=target))
    (record,) = document.extract_links()
    assert record.file == "other.pdf" and record.destination is None
    assert record.issues == ("invalid_destination",)


def test_bad_file_does_not_hide_valid_remote_destination_and_uf_null_falls_back(
    document,
):
    target = PdfArray([PdfNumber(4), PdfName("Fit")])
    _entry(document, A=_action("GoToR", F=PdfNumber(2), D=target))
    _entry(
        document,
        A=_action(
            "Launch",
            F=PdfDictionary(
                {PdfName("UF"): PdfNull(), PdfName("F"): PdfString("fallback.txt")}
            ),
        ),
    )
    first, second = document.extract_links()
    assert first.destination == FitDestination(4) and first.file is None
    assert first.issues == ("invalid_file",)
    assert second.file == "fallback.txt" and not second.issues


def test_bad_uri_base_keeps_raw_url_and_other_targets(document):
    _catalog(document).mapping[PdfName("URI")] = PdfNumber(1)
    document.pages[0].add_link(RECT, URIAction("relative.pdf"))
    document.pages[0].add_link(RECT, FitDestination(1))
    first, second = document.extract_links()
    assert first.uri == "relative.pdf" and first.resolved_uri is None
    assert first.issues == ("invalid_uri_base",)
    assert second.target_page_index == 1 and not second.issues


def test_unknown_actions_hide_executable_streams_and_additional_actions(
    document, monkeypatch
):
    script = PdfStream(b"arbitrary executable bytes")
    action = _action(
        "JavaScript", JS=script, Next=_action("GoToE", D=PdfString("embedded"))
    )
    _entry(
        document,
        A=action,
        AA=PdfDictionary(
            {PdfName("E"): _action("URI", URI=PdfString("https://excluded.test/"))}
        ),
        F=PdfNumber(2),
    )
    monkeypatch.setattr(
        CosExtractor,
        "_decode_stream",
        lambda *a, **k: pytest.fail("must not decode script streams"),
    )
    records = document.extract_links()
    assert [r.action_type for r in records] == ["JavaScript", "GoToE"]
    assert all(not r.issues for r in records)
    assert "executable" not in repr(records) and "excluded" not in repr(records)


def test_missing_rect_and_broken_action_are_retained(document):
    annotation = _entry(document, A=PdfNumber(1))
    annotation.mapping.pop(PdfName("Rect"))
    (record,) = document.extract_links()
    assert record.rect is None and record.action_type is None
    assert record.issues == ("invalid_rect", "invalid_action")


def test_named_alias_cycles_and_stale_page_generations_are_reported(document):
    _names(document, [("a", PdfString("b")), ("b", PdfString("a"))])
    _entry(document, Dest=PdfString("a"))
    ref = document._engine_pdf._make_page_ref(1)
    _entry(
        document,
        Dest=PdfArray(
            [
                PdfIndirectReference(ref.object_number, ref.gen_number + 1),
                PdfName("Fit"),
            ]
        ),
    )
    first, second = document.extract_links()
    assert first.destination_name == "a" and first.issues == ("cyclic_reference",)
    assert second.issues == ("missing_page",)


def test_shared_action_expansion_and_long_action_names_obey_limits(document):
    action = _action("Named", N=PdfName("NextPage"))
    for _ in range(8):
        action = _action(
            "Named", N=PdfName("NextPage"), Next=PdfArray([action, action])
        )
    _entry(document, A=action)
    document._load_limits = document._engine_pdf._load_limits = PdfLoadLimits(
        max_container_items=100
    )
    with pytest.raises(PdfResourceLimitException, match="max_container_items"):
        document.extract_links()
    with Document() as long_name:
        long_name.pages.add()
        _entry(long_name, A=_action("UnknownActionName"))
        long_name._load_limits = long_name._engine_pdf._load_limits = PdfLoadLimits(
            max_object_bytes=8
        )
        with pytest.raises(PdfResourceLimitException, match="max_object_bytes"):
            long_name.extract_links()


def test_inventory_preserves_serialized_bytes_and_ignores_unselected_bad_pages(
    document,
):
    document.pages[0].add_link(RECT, URIAction("https://example.test/"))
    document.pages[0].add_link(RECT, FitDestination(2))
    before = _save(document)
    records = document.extract_links()
    assert _save(document) == before
    with Document(before) as reopened:
        assert reopened.extract_links() == records
    page = document._engine_pdf._get_page_dict(2)
    page.mapping[PdfName("Annots")] = PdfString("malformed unselected metadata")
    assert document.extract_links(pages=[0]) == records


def test_cyclic_action_type_reference_does_not_hide_next_target(document):
    pdf = document._engine_pdf
    pdf._ensure_cos()
    cycle = pdf._cos_doc.register_object(PdfNull())
    pdf._cos_doc.objects[cycle.object_number] = cycle
    action = _action(
        "Named", Next=_action("URI", URI=PdfString("https://example.test/next"))
    )
    action.mapping[PdfName("S")] = cycle
    _entry(document, A=action)
    first, second = document.extract_links()
    assert first.issues == ("cyclic_reference",)
    assert (
        second.action_path == "A.Next[0]" and second.uri == "https://example.test/next"
    )
    assert not second.issues

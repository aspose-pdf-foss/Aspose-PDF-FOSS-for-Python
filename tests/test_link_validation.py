"""Read-only diagnostics for local links, named targets and live bookmarks."""

from __future__ import annotations

import io
from dataclasses import FrozenInstanceError

import pytest

from aspose_pdf import (
    Document,
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
    LinkValidationIssue,
    OutlineItem,
    PdfLoadLimits,
    PdfResourceLimitException,
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
from aspose_pdf.exceptions import (
    AsposePdfException,
    PdfParseException,
    PdfValidationException,
)
from aspose_pdf.link_validation import LinkValidationIssue as ModuleIssue

_RECT = (10, 20, 100, 40)


@pytest.fixture
def document():
    with Document() as doc:
        for _ in range(3):
            doc.pages.add()
        yield doc


def _saved(doc):
    output = io.BytesIO()
    doc.save(output)
    return output.getvalue()


def _catalog(doc):
    engine = doc._engine_pdf
    return engine._resolve(engine._cos_doc.trailer.mapping[PdfName("Root")])


def _dest(doc, index=1, kind="Fit", params=()):
    return PdfArray(
        [
            doc._engine_pdf._make_page_ref(index),
            PdfName(kind),
            *[PdfNull() if p is None else PdfNumber(p) for p in params],
        ]
    )


def _link(doc, page=0, **entries):
    doc.pages[page].add_link(_RECT, FitDestination(0))
    engine = doc._engine_pdf
    annot = engine._resolve(engine._annotation_refs(page)[-1])
    annot.mapping.pop(PdfName("Dest"), None)
    annot.mapping.update({PdfName(key): value for key, value in entries.items()})
    return annot


def _action(kind="GoTo", **entries):
    return PdfDictionary(
        {
            PdfName("S"): PdfName(kind),
            **{PdfName(key): value for key, value in entries.items()},
        }
    )


def _names(doc, pairs):
    _catalog(doc).mapping[PdfName("Names")] = PdfDictionary(
        {
            PdfName("Dests"): PdfDictionary(
                {
                    PdfName("Names"): PdfArray(
                        [
                            value
                            for name, target in pairs
                            for value in (PdfString(name), target)
                        ]
                    )
                }
            )
        }
    )


@pytest.mark.parametrize(
    "destination",
    [
        FitDestination(1),
        FitBDestination(2),
        XYZDestination(0),
        XYZDestination(2, 1, 2, 0),
        FitHDestination(1),
        FitBHDestination(1, 100),
        FitVDestination(1),
        FitBVDestination(1, 20),
        FitRDestination(2, 1, 2, 10, 20),
    ],
)
@pytest.mark.parametrize("as_action", [False, True])
def test_all_standard_views_are_valid_before_and_after_roundtrip(
    document, destination, as_action
):
    document.pages[0].add_link(
        _RECT, GoToAction(destination) if as_action else destination
    )
    assert document.validate_links() == []
    with Document(_saved(document)) as reopened:
        assert reopened.validate_links() == []


def test_public_issue_is_detached_frozen_and_fresh(document):
    assert ModuleIssue is LinkValidationIssue
    document.pages[1].add_link(_RECT, "missing")
    (issue,) = document.validate_links()
    assert isinstance(issue, LinkValidationIssue)
    assert (issue.source, issue.page_index, issue.annotation_index) == (
        "annotation",
        1,
        0,
    )
    assert (
        issue.code == "missing_named_destination"
        and issue.destination_name == "missing"
    )
    assert issue.target_path == "Dest" and issue.outline_path is None
    with pytest.raises(FrozenInstanceError):
        issue.code = "changed"
    document.destinations["missing"] = 2
    assert document.validate_links() == []
    document.dispose()
    assert issue.destination_name == "missing" and issue.message


@pytest.mark.parametrize(
    "representation", ["string", "name", "goto", "indirect", "dictionary", "legacy"]
)
def test_named_destinations_resolve_both_formats_and_indirections(
    document, representation
):
    array = _dest(document)
    target = PdfString("chapter")
    if representation == "dictionary":
        array = PdfDictionary({PdfName("D"): array})
    if representation == "indirect":
        array = document._engine_pdf._cos_doc.register_object(array)
    if representation == "legacy":
        _catalog(document).mapping[PdfName("Dests")] = PdfDictionary(
            {PdfName("chapter"): array}
        )
    else:
        _names(document, [("chapter", array)])
    if representation == "name":
        target = PdfName("chapter")
    _link(
        document,
        **({"A": _action(D=target)} if representation == "goto" else {"Dest": target}),
    )
    assert document.validate_links() == []


def test_name_and_string_priorities_are_not_conflated(document):
    _names(document, [("same", _dest(document))])
    _catalog(document).mapping[PdfName("Dests")] = PdfDictionary(
        {PdfName("same"): PdfArray([PdfIndirectReference(9999), PdfName("Fit")])}
    )
    _link(document, Dest=PdfString("same"))
    _link(document, Dest=PdfName("same"))
    issues = document.validate_links()
    assert [(i.source, i.code, i.annotation_index) for i in issues] == [
        ("annotation", "missing_page", 1),
        ("named_destination", "missing_page", None),
    ]


def test_latin1_and_binary_name_keys_are_compared_byte_for_byte(document):
    _names(document, [(b"chapter\xe9\x00", _dest(document))])
    _link(document, Dest=PdfString(b"chapter\xe9\x00"))
    assert document.validate_links() == []
    _link(document, Dest=PdfString(b"chapter\xe9"))
    assert document.pages[0].validate_links()[0].destination_name == "chapteré"


@pytest.mark.parametrize(
    "entries,code",
    [
        ({}, "missing_target"),
        ({"Dest": PdfNull()}, "missing_target"),
        ({"A": PdfNull()}, "missing_target"),
        ({"A": PdfNumber(1)}, "invalid_action"),
        ({"A": PdfStream(b"not decoded")}, "invalid_action"),
        ({"A": PdfDictionary()}, "invalid_action"),
        ({"A": _action()}, "missing_target"),
        ({"A": _action(D=PdfNull())}, "missing_target"),
        ({"Dest": PdfNumber(2)}, "invalid_destination"),
        ({"Dest": PdfString("absent")}, "missing_named_destination"),
    ],
)
def test_missing_or_malformed_targets_are_reported(document, entries, code):
    _link(document, **entries)
    (issue,) = document.pages[0].validate_links()
    assert issue.code == code and issue.annotation_index == 0


@pytest.mark.parametrize(
    "kind,params",
    [
        ("XYZ", []),
        ("XYZ", [1, 2]),
        ("XYZ", [None, None, -1]),
        ("FitH", []),
        ("FitH", [1, 2]),
        ("Fit", [1]),
        ("FitB", [None]),
        ("FitR", [1, 2, None, 4]),
        ("FitR", [1, 2, 1, 4]),
        ("FitR", [1, 2, 3, 1]),
        ("Unknown", []),
        ("FitV", [float("nan")]),
        ("FitBH", [float("inf")]),
        ("FitBV", [True]),
        ("FitV", [10**400]),
    ],
)
def test_invalid_view_parameters_are_reported_without_repair(document, kind, params):
    annotation = _link(document, Dest=_dest(document, kind=kind, params=params))
    before = dict(annotation.mapping)
    (issue,) = document.pages[0].validate_links()
    assert issue.code == "invalid_destination"
    assert annotation.mapping == before


@pytest.mark.parametrize(
    "target",
    [
        PdfArray(),
        PdfArray([PdfNumber(1), PdfName("Fit")]),
        PdfArray([PdfIndirectReference(9999), PdfName("Fit")]),
        PdfArray([PdfIndirectReference(0), PdfName("Fit")]),
    ],
)
def test_bad_page_targets(document, target):
    _link(document, Dest=target)
    (issue,) = document.pages[0].validate_links()
    assert issue.code in ("missing_page", "invalid_destination")


def test_wrong_generation_and_deleted_page_are_missing_even_when_objects_exist(
    document,
):
    ref = document._engine_pdf._make_page_ref(1)
    _link(
        document,
        Dest=PdfArray(
            [
                PdfIndirectReference(ref.object_number, ref.gen_number + 1),
                PdfName("Fit"),
            ]
        ),
    )
    _link(document, Dest=_dest(document, 2))
    document.pages.delete(2)
    assert [issue.code for issue in document.pages[0].validate_links()] == [
        "missing_page",
        "missing_target",
    ]


def test_current_page_tree_is_used_after_insert_and_unreferenced_object_is_not_a_page(
    document,
):
    document.pages[0].add_link(_RECT, FitDestination(2))
    document.pages.insert(0)
    assert document.validate_links() == []
    outside = document._engine_pdf._cos_doc.register_object(
        PdfDictionary({PdfName("Type"): PdfName("Page")})
    )
    _link(document, Dest=PdfArray([outside, PdfName("Fit")]))
    assert document.pages[0].validate_links()[0].code == "missing_page"


def test_conflicting_dest_and_action_are_reported_and_both_checked(document):
    _link(document, Dest=PdfString("one"), A=_action(D=PdfString("two")))
    issues = document.pages[0].validate_links()
    assert [i.code for i in issues] == [
        "conflicting_targets",
        "missing_named_destination",
        "missing_named_destination",
    ]
    assert [i.target_path for i in issues] == ["Dest/A", "Dest", "A.D"]


@pytest.mark.parametrize(
    "action",
    [
        URIAction("https://example.invalid"),
        GoToRAction("nonexistent.pdf", FitDestination(99999)),
        JavaScriptAction("app.alert('never execute')"),
    ],
)
def test_nonlocal_actions_are_skipped(document, action):
    document.pages[0].add_link(_RECT, action)
    document.outlines.add(OutlineItem("external", destination=action))
    assert document.validate_links() == []


def test_action_next_arrays_indirections_external_roots_and_shared_nodes(document):
    bad = _action(D=PdfString("absent"))
    ref = document._engine_pdf._cos_doc.register_object(bad)
    _link(
        document,
        A=_action(
            "URI",
            URI=PdfString("https://example.invalid"),
            Next=PdfArray(
                [
                    _action(D=_dest(document)),
                    ref,
                    ref,
                ]
            ),
        ),
    )
    (issue,) = document.pages[0].validate_links()
    assert (
        issue.code == "missing_named_destination" and issue.target_path == "A.Next[1].D"
    )


@pytest.mark.parametrize(
    "next_value",
    [PdfNumber(2), PdfArray([PdfString("wrong")]), PdfArray([PdfArray([])])],
)
def test_malformed_next_actions_are_reported(document, next_value):
    _link(document, A=_action(D=_dest(document), Next=next_value))
    assert document.pages[0].validate_links()[0].code == "invalid_action"


def test_reference_and_action_cycles_are_bounded(document):
    action = _action(D=_dest(document))
    action.mapping[PdfName("Next")] = action
    _link(document, A=action)
    loop = PdfDictionary()
    loop.mapping[PdfName("D")] = loop
    _link(document, Dest=loop)
    reference = document._engine_pdf._cos_doc.register_object(PdfNull())
    document._engine_pdf._cos_doc.objects[reference.object_number] = reference
    _link(document, A=reference)
    assert [i.code for i in document.pages[0].validate_links()] == [
        "cyclic_reference"
    ] * 3


def test_named_alias_cycles_and_missing_definitions_are_diagnosed(document):
    _names(
        document,
        [
            ("a", PdfString("b")),
            ("b", PdfString("a")),
            ("missing", PdfString("absent")),
        ],
    )
    document.pages[0].add_link(_RECT, "a")
    issues = document.validate_links()
    assert issues[0].source == "annotation" and issues[0].code == "cyclic_reference"
    assert [(i.destination_name, i.code) for i in issues[1:]] == [
        ("a", "cyclic_reference"),
        ("b", "cyclic_reference"),
        ("missing", "missing_named_destination"),
    ]


def test_named_aliases_and_dictionary_chains_can_reach_a_page(document):
    _names(
        document,
        [("a", PdfDictionary({PdfName("D"): PdfString("b")})), ("b", _dest(document))],
    )
    document.pages[0].add_link(_RECT, "a")
    assert document.validate_links() == []


def test_live_outline_edits_nested_paths_and_headings(document):
    heading = document.outlines.add(OutlineItem("heading", page_index=None))
    heading.add(OutlineItem("bad", destination="absent"))
    document.outlines.add(OutlineItem("outside", destination=FitDestination(99)))
    issues = document.validate_links(pages=[])
    assert [(i.outline_path, i.code) for i in issues] == [
        ((0, 0), "missing_named_destination"),
        ((1,), "missing_page"),
    ]
    heading.children[0].destination = FitDestination(1)
    document.outlines[1].page_index = None
    assert document.validate_links(pages=[]) == []


@pytest.mark.parametrize(
    "target",
    [
        XYZDestination(0, zoom=-1),
        FitRDestination(0, 2, 2, 1, 1),
        XYZDestination(0, top=float("inf")),
    ],
)
def test_unsaved_typed_outline_parameters_are_checked(document, target):
    document.outlines.add(OutlineItem("bad", destination=GoToAction(target)))
    (issue,) = document.validate_links(pages=[])
    assert issue.source == "outline" and issue.code == "invalid_destination"


def test_loaded_outlines_use_original_references_and_current_named_definitions(
    document,
):
    document.destinations["chapter"] = 2
    document.outlines.add(OutlineItem("named", destination="chapter"))
    document.outlines.add(OutlineItem("explicit", destination=FitDestination(2)))
    with Document(_saved(document)) as reopened:
        assert reopened._outlines is None
        reopened.destinations.remove("chapter")
        reopened.pages.delete(2)
        issues = reopened.validate_links(pages=[])
        assert [(i.outline_path, i.code) for i in issues] == [
            ((0,), "missing_named_destination"),
            ((1,), "missing_page"),
        ]
        assert reopened._outlines is None


def test_live_outline_cycle_is_reported_without_recursion(document):
    root = document.outlines.add(OutlineItem("root", page_index=None))
    root.children.append(root)
    (issue,) = document.validate_links(pages=[])
    assert issue.code == "cyclic_reference" and issue.outline_path == (0, 0)


def test_page_selection_order_duplicates_flags_and_page_convenience(document):
    for index in range(3):
        document.pages[index].add_link(_RECT, f"missing-{index}")
    document.outlines.add(OutlineItem("bad", destination="outline-missing"))
    _names(document, [("invalid", PdfNull())])
    issues = document.validate_links(
        pages=(i for i in [2, 0, 2]),
        include_outlines=False,
        include_named_destinations=False,
    )
    assert [i.page_index for i in issues] == [2, 0]
    assert document.pages[1].validate_links() == document.validate_links(
        pages=[1], include_outlines=False, include_named_destinations=False
    )
    assert [i.source for i in document.validate_links(pages=[])] == [
        "outline",
        "named_destination",
    ]


@pytest.mark.parametrize(
    "options",
    [
        {"pages": [False]},
        {"pages": [0.0]},
        {"pages": [-1]},
        {"pages": [3]},
        {"pages": [0, 99]},
        {"pages": "0"},
        {"pages": 0},
        {"include_outlines": 1},
        {"include_named_destinations": None},
    ],
)
def test_invalid_options_fail_before_metadata_reads(document, monkeypatch, options):
    def forbidden(*args):
        raise AssertionError("Invalid options must not read metadata")

    monkeypatch.setattr(document._engine_pdf, "_get_page_dict", forbidden)
    monkeypatch.setattr(document._engine_pdf, "_page_reference_numbers", forbidden)
    with pytest.raises(PdfValidationException):
        document.validate_links(**options)


@pytest.mark.parametrize(
    "entry", [PdfNumber(1), PdfArray([PdfNumber(1)]), PdfArray([PdfNull()])]
)
def test_malformed_annotation_containers_are_diagnosed(document, entry):
    document._engine_pdf._get_page_dict(0).mapping[PdfName("Annots")] = entry
    assert document.pages[0].validate_links()[0].code == "invalid_structure"


@pytest.mark.parametrize(
    "tree",
    [
        PdfNumber(1),
        PdfDictionary(),
        PdfDictionary({PdfName("Names"): PdfArray([PdfString("odd")])}),
        PdfDictionary({PdfName("Names"): PdfArray([PdfNumber(1), PdfNull()])}),
        PdfDictionary({PdfName("Kids"): PdfNumber(1)}),
    ],
)
def test_malformed_name_trees_are_diagnosed(document, tree):
    _catalog(document).mapping[PdfName("Names")] = PdfDictionary(
        {PdfName("Dests"): tree}
    )
    assert any(
        i.source == "named_destination" and i.code == "invalid_structure"
        for i in document.validate_links()
    )


def test_name_tree_cycle_and_unsorted_duplicate_keys_are_diagnosed(document):
    _names(
        document,
        [("b", _dest(document)), ("a", _dest(document)), ("a", _dest(document))],
    )
    assert (
        len([i for i in document.validate_links() if i.code == "invalid_structure"])
        == 2
    )
    node = PdfDictionary()
    node.mapping[PdfName("Kids")] = PdfArray([node])
    _catalog(document).mapping[PdfName("Names")] = PdfDictionary(
        {PdfName("Dests"): node}
    )
    assert any(i.code == "cyclic_reference" for i in document.validate_links())


def test_validation_is_read_only_for_bytes_caches_and_existing_issues(document):
    document.pages[0].add_link(_RECT, "absent")
    document.outlines.add(OutlineItem("bad", destination="missing"))
    before = _saved(document)
    engine = document._engine_pdf
    numbers = set(engine._cos_doc.objects)
    cached_names = getattr(engine, "_named_destination_cache", None)
    outlines = document._outlines
    issues = document.validate_links()
    assert document.validate_links() == issues
    assert (
        document._outlines is outlines
        and getattr(engine, "_named_destination_cache", None) is cached_names
    )
    assert set(engine._cos_doc.objects) == numbers
    assert _saved(document) == before


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("encrypted", [False, True])
def test_files_streaming_and_encrypted_navigation_do_not_decode_streams(
    document, tmp_path, monkeypatch, streaming, encrypted
):
    document.pages[0].add_text("Page content must not be decoded", 20, 100)
    document.pages[0].add_link(_RECT, "missing")
    if encrypted:
        document.encrypt(user_password="secret", owner_password="owner")
    path = tmp_path / "source.pdf"
    document.save(path)
    opener = Document.open_streaming if streaming else Document
    with opener(path, password="secret" if encrypted else None) as reopened:

        def no_decode(*args, **kwargs):
            raise AssertionError("Link validation must not decode streams")

        monkeypatch.setattr(reopened._engine_pdf, "get_page_content", no_decode)
        monkeypatch.setattr(reopened._engine_pdf, "_decode_cos_stream", no_decode)
        issues = reopened.validate_links()
        assert len(issues) == 1 and issues[0].code == "missing_named_destination"


def test_resource_limits_bound_annotation_work_and_action_depth():
    with Document(limits=PdfLoadLimits(max_container_items=10)) as document:
        document.pages.add()
        for _ in range(11):
            document.pages[0].add_link(_RECT, FitDestination(0))
        with pytest.raises(PdfResourceLimitException, match="max_container_items"):
            document.validate_links()
    with Document(limits=PdfLoadLimits(max_nesting_depth=3)) as document:
        document.pages.add()
        action = _action(D=_dest(document, 0))
        for _ in range(4):
            action = _action(Next=action, D=_dest(document, 0))
        _link(document, A=action)
        with pytest.raises(PdfResourceLimitException, match="max_nesting_depth"):
            document.validate_links()


def test_name_size_budget_and_parser_failures_propagate(document, monkeypatch):
    with Document(limits=PdfLoadLimits(max_object_bytes=8)) as limited:
        limited.pages.add()
        limited.pages[0].add_link(_RECT, "x" * 9)
        with pytest.raises(PdfResourceLimitException, match="max_object_bytes"):
            limited.validate_links()

    def broken(*args):
        raise PdfParseException("injected parser failure")

    monkeypatch.setattr(document._engine_pdf, "_get_page_dict", broken)
    with pytest.raises(PdfParseException, match="injected"):
        document.validate_links()


def test_empty_document_and_disposed_handles():
    with Document() as document:
        assert document.validate_links() == []
        page = document.pages.add()
    for call in (document.validate_links, page.validate_links):
        with pytest.raises(AsposePdfException, match="disposed"):
            call()


@pytest.mark.parametrize("entry", ["Annots", "Names", "Dests", "names-root"])
def test_dangling_container_references_are_not_treated_as_absent(document, entry):
    dangling = PdfIndirectReference(99999)
    if entry == "Annots":
        document._engine_pdf._get_page_dict(0).mapping[PdfName(entry)] = dangling
    elif entry == "names-root":
        _catalog(document).mapping[PdfName("Names")] = PdfDictionary(
            {PdfName("Dests"): dangling}
        )
    else:
        _catalog(document).mapping[PdfName(entry)] = dangling
    assert any(i.code == "invalid_structure" for i in document.validate_links())


def test_loaded_dangling_bookmark_targets_are_reported(document):
    document._engine_pdf._outlines_data = [
        {
            "loaded_target": ("Dest", PdfIndirectReference(99999)),
            "children": [],
        }
    ]
    (issue,) = document.validate_links(pages=[])
    assert issue.source == "outline" and issue.code == "invalid_destination"


def test_empty_selection_with_global_checks_disabled_reads_no_metadata(
    document, monkeypatch
):
    def forbidden(*args):
        raise AssertionError("No sources selected")

    monkeypatch.setattr(document._engine_pdf, "_page_reference_numbers", forbidden)
    assert (
        document.validate_links(
            pages=[], include_outlines=False, include_named_destinations=False
        )
        == []
    )


def test_named_lookup_reads_current_values_even_with_populated_engine_cache(document):
    document.destinations["chapter"] = 1
    assert document.destinations["chapter"] == FitDestination(1)
    _names(document, [("chapter", PdfNull())])
    _link(document, Dest=PdfString("chapter"))
    assert document.pages[0].validate_links()[0].code == "invalid_destination"


def test_indirect_view_parameters_and_explicit_nulls_are_valid(document):
    cos = document._engine_pdf._cos_doc
    view = cos.register_object(PdfName("XYZ"))
    parameter = cos.register_object(PdfNumber(10))
    null = cos.register_object(PdfNull())
    _link(
        document,
        Dest=PdfArray(
            [document._engine_pdf._make_page_ref(1), view, parameter, null, null]
        ),
    )
    assert document.validate_links() == []


def test_named_tree_children_are_traversed_in_order(document):
    leaves = [
        PdfDictionary({PdfName("Names"): PdfArray([PdfString(name), _dest(document)])})
        for name in ("a", "b")
    ]
    root = PdfDictionary({PdfName("Kids"): PdfArray(leaves)})
    _catalog(document).mapping[PdfName("Names")] = PdfDictionary(
        {PdfName("Dests"): root}
    )
    _link(document, Dest=PdfString("b"))
    assert document.validate_links() == []


def test_unsaved_goto_bookmark_can_name_a_destination(document):
    document.outlines.add(OutlineItem("named", destination=GoToAction("missing")))
    (issue,) = document.validate_links(pages=[])
    assert issue.code == "missing_named_destination" and issue.target_path == "A.D"


def test_non_link_annotations_are_not_navigation_inputs(document):
    document.pages[0].annotations.add_text(
        _RECT, "Review", properties={"A": {"S": "GoTo", "D": "missing"}}
    )
    assert document.validate_links() == []


def test_unicode_name_fallback_is_lossless(document):
    _names(document, [("?????", _dest(document)), ("глава".encode(), _dest(document))])
    _link(document, Dest=PdfName("глава"))
    _link(document, Dest=PdfName("другая"))
    issues = document.pages[0].validate_links()
    assert len(issues) == 1 and issues[0].destination_name == "другая"


def test_cyclic_indirect_tree_branch_does_not_hide_valid_sibling(document):
    cos = document._engine_pdf._cos_doc
    reference = cos.register_object(PdfNull())
    cos.objects[reference.object_number] = reference
    valid = PdfDictionary(
        {PdfName("Names"): PdfArray([PdfString("ok"), _dest(document)])}
    )
    _catalog(document).mapping[PdfName("Names")] = PdfDictionary(
        {
            PdfName("Dests"): PdfDictionary(
                {PdfName("Kids"): PdfArray([reference, valid])}
            )
        }
    )
    _link(document, Dest=PdfString("ok"))
    issues = document.validate_links()
    assert len(issues) == 1 and issues[0].source == "named_destination"
    assert issues[0].code == "cyclic_reference"


def test_valid_nonzero_page_generation_after_real_pdf_parsing():
    objects = {
        1: (0, b"<< /Type /Catalog /Pages 2 0 R >>"),
        2: (0, b"<< /Type /Pages /Count 1 /Kids [3 2 R] >>"),
        3: (
            2,
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Annots [4 0 R] >>",
        ),
        4: (
            0,
            b"<< /Type /Annot /Subtype /Link /Rect [0 0 10 10] /Dest [3 2 R /Fit] >>",
        ),
    }
    data = bytearray(b"%PDF-1.7\n")
    offsets = {}
    for number, (generation, value) in objects.items():
        offsets[number] = len(data)
        data += b"%d %d obj\n" % (number, generation) + value + b"\nendobj\n"
    start = len(data)
    data += b"xref\n0 5\n0000000000 65535 f \n"
    for number, (generation, _) in objects.items():
        data += b"%010d %05d n \n" % (offsets[number], generation)
    data += b"trailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % start
    with Document(bytes(data)) as document:
        assert document.validate_links() == []
        _link(document, Dest=PdfArray([PdfIndirectReference(3, 0), PdfName("Fit")]))
        assert document.pages[0].validate_links()[0].code == "missing_page"


def test_metadata_work_budget_is_cumulative_within_each_call():
    with Document(limits=PdfLoadLimits(max_container_items=10)) as document:
        document.pages.add()
        for _ in range(5):
            document.pages[0].add_link(_RECT, FitDestination(0))
        with pytest.raises(PdfResourceLimitException, match="metadata work"):
            document.validate_links()


def test_current_page_index_and_name_tree_depth_obey_limits():
    with Document(limits=PdfLoadLimits(max_container_items=2)) as document:
        for _ in range(3):
            document.pages.add()
        with pytest.raises(PdfResourceLimitException, match="page index"):
            document.validate_links(pages=[], include_outlines=False)
    with Document(limits=PdfLoadLimits(max_nesting_depth=2)) as document:
        document.pages.add()
        node = PdfDictionary({PdfName("Names"): PdfArray([])})
        for _ in range(3):
            node = PdfDictionary({PdfName("Kids"): PdfArray([node])})
        _catalog(document).mapping[PdfName("Names")] = PdfDictionary(
            {PdfName("Dests"): node}
        )
        with pytest.raises(PdfResourceLimitException, match="max_nesting_depth"):
            document.validate_links()


def test_repeated_calls_get_a_fresh_metadata_budget():
    with Document(limits=PdfLoadLimits(max_container_items=10)) as document:
        document.pages.add().add_link(_RECT, "absent")
        expected = document.validate_links()
        for _ in range(20):
            assert document.validate_links() == expected

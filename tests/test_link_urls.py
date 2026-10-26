"""Bulk URL substitution edits Link targets without changing page content."""

from __future__ import annotations

import io
import re

import pytest

from aspose_pdf import Document, OutlineItem, PdfLoadLimits, PdfResourceLimitException
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
    PdfParseException,
    PdfValidationException,
)
from aspose_pdf.interactive import FitDestination, URIAction

OLD = "https://old.example"
NEW = "https://new.example"
RECT = (20, 30, 120, 50)


def _bytes(document):
    with io.BytesIO() as output:
        document.save(output)
        return output.getvalue()


def _uris(document):
    return [link.uri for link in document.extract_links() if link.action_type == "URI"]


def _entry(document, index=0, page=0):
    engine = document._engine_pdf
    return engine._resolve(engine._annotation_refs(page)[index])


def _report(document):
    for index in range(3):
        page = document.pages.add()
        page.add_text(f"Unchanged page {index}: {OLD}", 72, 700)
        page.add_link(RECT, URIAction(f"{OLD}/page-{index}?q=1#part"))
        page.add_link(RECT, URIAction("https://other.example/path"))
        page.add_link(RECT, FitDestination(0))


@pytest.mark.parametrize("source", ["live", "bytes", "file", "streaming"])
def test_bulk_url_replacement_keeps_layout_content_and_survives_round_trips(source, tmp_path):
    with Document() as original:
        _report(original)
        raw = _bytes(original)
        if source == "live":
            document = original
        elif source == "bytes":
            document = Document(raw)
        else:
            path = tmp_path / "input.pdf"
            path.write_bytes(raw)
            document = (Document.open_streaming if source == "streaming" else Document)(path)
        try:
            text = document.extract_text()
            content = [page.content for page in document.pages]
            assert document.replace_link_urls(OLD, NEW) == 3
            expected = [uri for index in range(3) for uri in (
                f"{NEW}/page-{index}?q=1#part", "https://other.example/path",
            )]
            assert _uris(document) == expected
            assert document.extract_text() == text
            assert [page.content for page in document.pages] == content
            for page in document.pages:
                links = page.extract_links()
                assert all(link.rect == RECT for link in links)
                assert links[-1].target_page_index == 0
            path = tmp_path / "output.pdf"
            document.save(path)
            for value in (path, _bytes(document)):
                with Document(value) as reopened:
                    assert reopened.validate() and _uris(reopened) == expected
        finally:
            if document is not original:
                document.dispose()


@pytest.mark.parametrize("search,replacement,options,uri,expected", [
    ("old.example", "new.example", {}, OLD + "/x", NEW + "/x"),
    ("OLD.EXAMPLE", "new.example", {}, OLD, OLD),
    ("OLD.EXAMPLE", "new.example", {"case_sensitive": False}, OLD, NEW),
    (r"old\.(example)", r"new.\1", {"regex": True}, OLD, NEW),
    (re.compile(r"old\.(?P<tld>example)"), r"new.\g<tld>", {}, OLD, NEW),
    (re.compile("OLD.EXAMPLE", re.I), "new.example", {}, OLD, NEW),
    ("[A].+?", r"\g<literal>", {}, "path/[A].+?", r"path/\g<literal>"),
    (r"\?tracking=.*$", "", {"regex": True}, OLD + "/x?tracking=1", OLD + "/x"),
    (r"^", "https://", {"regex": True}, "old.example", OLD),
    ("old", "new", {}, "old/old/old", "new/new/new"),
    ("team@old.example", "team@new.example", {}, "mailto:team@old.example", "mailto:team@new.example"),
    ("docs/old", "docs/new", {}, "../docs/old.pdf", "../docs/new.pdf"),
])
def test_literal_regex_templates_flags_relative_and_custom_urls(search, replacement, options, uri, expected):
    with Document() as document:
        document.pages.add().add_link(RECT, URIAction(uri))
        assert document.replace_link_urls(search, replacement, **options) == (uri != expected)
        assert _uris(document) == [expected]


def test_page_selection_deduplicates_repeats_and_page_method_uses_its_own_index():
    with Document() as document:
        _report(document)
        assert document.replace_link_urls(OLD, NEW, pages=(index for index in [2, 0, 2])) == 2
        assert _uris(document)[::2] == [NEW + "/page-0?q=1#part", OLD + "/page-1?q=1#part", NEW + "/page-2?q=1#part"]
        assert document.pages[1].replace_link_urls(OLD, NEW) == 1
        assert document.replace_link_urls(OLD, NEW, pages=[]) == 0


def test_dry_run_and_noop_keep_pdf_bytes_mappings_and_object_ids():
    with Document() as document:
        _report(document)
        original = _bytes(document)
        entry = _entry(document)
        mapping = entry.mapping
        action = mapping[PdfName("A")]
        objects = set(document._engine_pdf._cos_doc.objects)
        assert document.replace_link_urls(OLD, NEW, dry_run=True) == 3
        assert entry.mapping is mapping and entry.mapping[PdfName("A")] is action
        assert document.replace_link_urls(OLD, OLD) == 0
        assert document.replace_link_urls("missing", NEW) == 0
        assert set(document._engine_pdf._cos_doc.objects) == objects
        assert _bytes(document) == original


def test_other_annotation_fields_and_action_next_are_preserved():
    with Document() as document:
        document.pages.add().add_link(RECT, URIAction(OLD))
        entry = _entry(document)
        entry.mapping.update({
            PdfName("C"): PdfArray([PdfNumber(1), PdfNumber(0), PdfNumber(0)]),
            PdfName("F"): PdfNumber(2), PdfName("NM"): PdfString("identity"),
            PdfName("Contents"): PdfString("Description"),
            PdfName("BS"): PdfDictionary({PdfName("W"): PdfNumber(2)}),
        })
        action = document._engine_pdf._resolve(entry.mapping[PdfName("A")])
        following = PdfDictionary({PdfName("S"): PdfName("URI"), PdfName("URI"): PdfString(OLD + "/next")})
        action.mapping.update({PdfName("IsMap"): PdfBoolean(True), PdfName("Next"): following})
        before = dict(entry.mapping)
        assert document.replace_link_urls(OLD, NEW) == 1
        for key, value in before.items():
            if key != PdfName("A"):
                assert entry.mapping[key] is value
        copied = document._engine_pdf._resolve(entry.mapping[PdfName("A")])
        assert copied is not action
        assert copied.mapping[PdfName("IsMap")].value is True
        assert copied.mapping[PdfName("Next")] is following
        assert action.mapping[PdfName("URI")].value == OLD.encode()
        assert _uris(document) == [NEW, OLD + "/next"]


def test_shared_action_does_not_change_other_pages_bookmarks_or_additional_actions():
    with Document() as document:
        for _ in range(2):
            document.pages.add().add_link(RECT, URIAction(OLD))
        engine = document._engine_pdf
        action = engine._resolve(_entry(document).mapping[PdfName("A")])
        reference = engine._cos_doc.register_object(action)
        for page in range(2):
            _entry(document, page=page).mapping[PdfName("A")] = reference
        _entry(document).mapping[PdfName("AA")] = PdfDictionary({PdfName("E"): reference})
        bookmark = document.outlines.add(OutlineItem("Website", 0))
        bookmark._loaded_target = ("A", reference)
        assert document.pages[0].replace_link_urls(OLD, NEW) == 1
        assert _uris(document) == [NEW, OLD]
        assert action.mapping[PdfName("URI")].value == OLD.encode()
        assert _entry(document).mapping[PdfName("AA")].mapping[PdfName("E")] is reference
        with Document(_bytes(document)) as reopened:
            assert _uris(reopened) == [NEW, OLD]
            assert reopened.outlines[0].destination.uri == OLD


@pytest.mark.parametrize("dry_run", [False, True])
def test_shared_annotation_outside_page_selection_is_refused_without_edits(dry_run):
    with Document() as document:
        document.pages.add().add_link(RECT, URIAction(OLD))
        document.pages.add()
        engine = document._engine_pdf
        original = engine._annotation_refs(0)[0]
        engine._get_page_dict(1).mapping[PdfName("Annots")] = PdfArray([original])
        before = _bytes(document)
        with pytest.raises(PdfValidationException, match="shared with an unselected page"):
            document.pages[0].replace_link_urls(OLD, NEW, dry_run=dry_run)
        assert _bytes(document) == before
        assert document.replace_link_urls(OLD, NEW, dry_run=dry_run) == 1
        assert _uris(document) == ([OLD, OLD] if dry_run else [NEW, NEW])


@pytest.mark.parametrize("search,replacement,options", [
    ("", NEW, {}), (None, NEW, {}), (b"old", NEW, {}), (re.compile(b"old"), NEW, {}),
    ("\ud800", NEW, {}), (OLD, None, {}), (OLD, b"new", {}), (OLD, "\ud800", {}),
    ("[", NEW, {"regex": True}), ("(old)", r"\2", {"regex": True}),
    ("old", r"\g<missing>", {"regex": True}), ("old", r"\q", {"regex": True}),
    (OLD, NEW, {"regex": 1}), (OLD, NEW, {"case_sensitive": None}), (OLD, NEW, {"dry_run": 1}),
    (OLD, NEW, {"pages": [True]}), (OLD, NEW, {"pages": [-1]}),
])
@pytest.mark.parametrize("empty", [False, True])
def test_invalid_options_are_rejected_before_metadata_reads(search, replacement, options, empty, monkeypatch):
    with Document() as document:
        if not empty:
            _report(document)
        monkeypatch.setattr(document._engine_pdf, "_get_page_dict", lambda *a: pytest.fail("Validate first"))
        with pytest.raises(PdfValidationException):
            document.replace_link_urls(search, replacement, **options)


def test_empty_result_and_later_malformed_uri_do_not_partially_update_links():
    with Document() as document:
        document.pages.add().add_link(RECT, URIAction(OLD))
        document.pages[0].add_link(RECT, URIAction(OLD + "/second"))
        entry = _entry(document)
        mapping = entry.mapping
        with pytest.raises(PdfValidationException, match="empty URI"):
            document.replace_link_urls(OLD, "")
        assert entry.mapping is mapping and _uris(document) == [OLD, OLD + "/second"]
        second = _entry(document, 1)
        document._engine_pdf._resolve(second.mapping[PdfName("A")]).mapping[PdfName("URI")] = PdfNumber(7)
        with pytest.raises(PdfParseException, match="PDF string"):
            document.replace_link_urls(OLD, NEW)
        assert entry.mapping is mapping


def test_later_invalid_page_is_rejected_before_any_metadata_access(monkeypatch):
    with Document() as document:
        _report(document)
        monkeypatch.setattr(document._engine_pdf, "_get_page_dict", lambda *a: pytest.fail("Validate first"))
        with pytest.raises(PdfValidationException):
            document.replace_link_urls(OLD, NEW, pages=[0, 99])


def test_disposed_document_and_page_reject_replacement():
    document = Document()
    page = document.pages.add()
    document.dispose()
    for target in (document, page):
        with pytest.raises(AsposePdfException, match="disposed"):
            target.replace_link_urls(OLD, NEW)


@pytest.mark.parametrize("kind", [None, PdfNull(), PdfString("URI"), PdfName("")])
def test_malformed_primary_action_kinds_do_not_partially_update_earlier_links(kind):
    with Document() as document:
        page = document.pages.add()
        for _ in range(2):
            page.add_link(RECT, URIAction(OLD))
        first = _entry(document)
        old_mapping = first.mapping
        action = document._engine_pdf._resolve(_entry(document, 1).mapping[PdfName("A")])
        if kind is None:
            action.mapping.pop(PdfName("S"))
        else:
            action.mapping[PdfName("S")] = kind
        with pytest.raises(PdfParseException, match="action S"):
            document.replace_link_urls(OLD, NEW)
        assert first.mapping is old_mapping


@pytest.mark.parametrize("value", [b"\xfe\xff\x00", b"\xff\xfe\x00", b"\xef\xbb\xbf\xff"])
def test_malformed_bom_encoded_uris_fail_before_any_edit(value):
    with Document() as document:
        page = document.pages.add()
        for _ in range(2):
            page.add_link(RECT, URIAction(OLD))
        first = _entry(document)
        mapping = first.mapping
        action = document._engine_pdf._resolve(_entry(document, 1).mapping[PdfName("A")])
        action.mapping[PdfName("URI")] = PdfString(value)
        with pytest.raises(PdfParseException, match="invalid encoded text"):
            document.replace_link_urls(OLD, NEW)
        assert first.mapping is mapping


def test_reference_cycles_are_rejected_before_any_edit():
    with Document() as document:
        page = document.pages.add()
        for _ in range(2):
            page.add_link(RECT, URIAction(OLD))
        first = _entry(document)
        mapping = first.mapping
        cos = document._engine_pdf._cos_doc
        reference = cos.register_object(PdfDictionary({}))
        cos.objects[reference.object_number] = reference
        _entry(document, 1).mapping[PdfName("A")] = reference
        with pytest.raises(PdfParseException, match="reference cycle"):
            document.replace_link_urls(OLD, NEW)
        assert first.mapping is mapping


@pytest.mark.parametrize("dry_run", [False, True])
def test_expanded_output_is_bounded_before_any_annotation_edit(dry_run):
    with Document(limits=PdfLoadLimits(max_object_bytes=64)) as document:
        page = document.pages.add()
        for uri in ("a", "aaaaa"):
            page.add_link(RECT, URIAction(uri))
        original = _entry(document).mapping
        with pytest.raises(PdfResourceLimitException, match="link URL result"):
            document.replace_link_urls("a", "B" * 20, dry_run=dry_run)
        assert _entry(document).mapping is original
        assert _uris(document) == ["a", "aaaaa"]


def test_unicode_result_size_includes_pdf_byte_order_mark():
    with Document(limits=PdfLoadLimits(max_object_bytes=5)) as document:
        document.pages.add().add_link(RECT, URIAction("a"))
        original = _entry(document).mapping
        with pytest.raises(PdfResourceLimitException, match="link URL result"):
            document.replace_link_urls("a", "🙂")
        assert _entry(document).mapping is original


def test_cumulative_output_size_is_bounded_without_partial_changes():
    with Document(limits=PdfLoadLimits(max_total_decoded_bytes=7)) as document:
        page = document.pages.add()
        for _ in range(3):
            page.add_link(RECT, URIAction("old"))
        original = _entry(document).mapping
        with pytest.raises(PdfResourceLimitException, match="link URL planned text"):
            document.replace_link_urls("old", "new")
        assert _entry(document).mapping is original


def test_capture_expansion_work_is_preflighted():
    with Document(limits=PdfLoadLimits(max_codec_work_bytes=64)) as document:
        document.pages.add().add_link(RECT, URIAction(OLD))
        original = _entry(document).mapping
        with pytest.raises(PdfResourceLimitException, match="link URL regex expansion"):
            document.replace_link_urls("(.+)", r"\1\1", regex=True)
        assert _entry(document).mapping is original


def test_metadata_work_is_bounded():
    with Document(limits=PdfLoadLimits(max_container_items=8)) as document:
        page = document.pages.add()
        for _ in range(3):
            page.add_link(RECT, URIAction(OLD))
        original = _entry(document).mapping
        with pytest.raises(PdfResourceLimitException, match="link URL metadata work"):
            document.replace_link_urls(OLD, NEW)
        assert _entry(document).mapping is original


def test_commit_failure_rolls_back_all_annotation_mappings(monkeypatch):
    with Document() as document:
        page = document.pages.add()
        for _ in range(2):
            page.add_link(RECT, URIAction(OLD))
        entries = [_entry(document, index) for index in range(2)]
        mappings = [entry.mapping for entry in entries]
        before = _bytes(document)
        assign = PdfDictionary.__setattr__
        failed = False

        def injected(obj, name, value):
            nonlocal failed
            if obj is entries[1] and name == "mapping" and not failed:
                failed = True
                raise RuntimeError("Injected annotation write failure")
            return assign(obj, name, value)

        monkeypatch.setattr(PdfDictionary, "__setattr__", injected)
        with pytest.raises(RuntimeError, match="write failure"):
            document.replace_link_urls(OLD, NEW)
        assert all(entry.mapping is mapping for entry, mapping in zip(entries, mappings))
        assert _bytes(document) == before


@pytest.mark.parametrize("uri,search,replacement,options", [
    (b"\xe9", "é", "é", {}),
    (b"\xe9", "É", "é", {"case_sensitive": False}),
    (b"", "^", "", {"regex": True}),
])
def test_noop_preserves_original_uri_encoding_under_tight_limits(uri, search, replacement, options):
    with Document(limits=PdfLoadLimits(max_object_bytes=2)) as document:
        document.pages.add().add_link(RECT, URIAction("x"))
        entry = _entry(document)
        action = document._engine_pdf._resolve(entry.mapping[PdfName("A")])
        value = PdfString(uri)
        action.mapping[PdfName("URI")] = value
        mapping = entry.mapping
        assert document.replace_link_urls(search, replacement, **options) == 0
        assert entry.mapping is mapping and action.mapping[PdfName("URI")] is value


def test_mixed_noop_and_changed_matches_preserve_all_unmatched_url_parts():
    with Document() as document:
        document.pages.add().add_link(RECT, URIAction("aA-aA-end"))
        assert document.replace_link_urls("a", "A", case_sensitive=False) == 1
        assert _uris(document) == ["AA-AA-end"]


def test_streaming_encrypted_input_is_metadata_only_and_keeps_source_file(tmp_path, monkeypatch):
    path = tmp_path / "encrypted.pdf"
    with Document() as document:
        _report(document)
        document.encrypt("user", "owner")
        document.save(path)
    source_bytes = path.read_bytes()
    with Document.open_streaming(path, password="user") as document:
        engine = document._engine_pdf

        def forbidden(*args, **kwargs):
            pytest.fail("URL rewriting must not decode page or asset streams")

        with monkeypatch.context() as patch:
            patch.setattr(engine, "get_page_content", forbidden)
            patch.setattr(engine, "_decode_cos_stream", forbidden)
            assert document.replace_link_urls(OLD, NEW, pages=[1]) == 1
            assert engine.page_contents == [] and engine._extracted_text is None
            assert document.is_encrypted
            assert _uris(document)[::2] == [OLD + "/page-0?q=1#part", NEW + "/page-1?q=1#part", OLD + "/page-2?q=1#part"]
        output = tmp_path / "updated.pdf"
        document.save(output)
        with Document.open_streaming(output, password="user") as reopened:
            assert _uris(reopened)[2] == NEW + "/page-1?q=1#part"
    assert path.read_bytes() == source_bytes


def test_appearances_and_script_next_streams_are_not_decoded_or_changed(monkeypatch):
    with Document() as document:
        document.pages.add().add_link(RECT, URIAction(OLD))
        entry = _entry(document)
        engine = document._engine_pdf
        appearance = engine._cos_doc.register_object(PdfStream(b"not compressed", {
            PdfName("Filter"): PdfName("FlateDecode"), PdfName("Subtype"): PdfName("Form"),
        }))
        script = engine._cos_doc.register_object(PdfStream(b"secret()", {}))
        entry.mapping[PdfName("AP")] = PdfDictionary({PdfName("N"): appearance})
        action = engine._resolve(entry.mapping[PdfName("A")])
        following = PdfDictionary({PdfName("S"): PdfName("JavaScript"), PdfName("JS"): script})
        action.mapping[PdfName("Next")] = following
        monkeypatch.setattr(engine, "_decode_cos_stream", lambda *a, **k: pytest.fail("Do not decode assets"))
        assert document.replace_link_urls(OLD, NEW) == 1
        assert entry.mapping[PdfName("AP")].mapping[PdfName("N")] is appearance
        assert engine._resolve(entry.mapping[PdfName("A")]).mapping[PdfName("Next")] is following
        assert following.mapping[PdfName("JS")] is script


def test_uri_base_and_raw_relative_uri_have_separate_replacement_semantics():
    with Document() as document:
        document.pages.add().add_link(RECT, URIAction("docs/old.pdf?q=old#part"))
        engine = document._engine_pdf
        catalog = engine._resolve(engine._cos_doc.trailer.mapping[PdfName("Root")])
        base = PdfDictionary({PdfName("Base"): PdfString(OLD + "/")})
        catalog.mapping[PdfName("URI")] = base
        assert document.replace_link_urls(OLD, NEW) == 0
        assert document.replace_link_urls("docs/old", "docs/new") == 1
        (link,) = document.extract_links()
        assert link.uri == "docs/new.pdf?q=old#part"
        assert link.resolved_uri == OLD + "/docs/new.pdf?q=old#part"
        assert catalog.mapping[PdfName("URI")] is base


def test_unicode_urls_and_regex_capture_titles_survive_save_and_reload():
    with Document() as document:
        document.pages.add().add_link(RECT, URIAction(OLD + "/資料?name=café#章"))
        assert document.replace_link_urls(re.compile(r"old\.(example)"), r"新.\1") == 1
        expected = "https://新.example/資料?name=café#章"
        assert _uris(document) == [expected]
        with Document(_bytes(document)) as reopened:
            assert _uris(reopened) == [expected]


def test_shared_uri_string_and_nonzero_generations_remain_valid():
    with Document() as document:
        page = document.pages.add()
        for _ in range(2):
            page.add_link(RECT, URIAction(OLD))
        engine = document._engine_pdf
        cos = engine._cos_doc
        value = cos.register_object(PdfString(OLD))
        cos.generations[value.object_number] = 7
        reference = cos.reference(value.object_number)
        for index in range(2):
            engine._resolve(_entry(document, index).mapping[PdfName("A")]).mapping[PdfName("URI")] = reference
        annotation_ref = engine._annotation_refs(0)[0]
        cos.generations[annotation_ref.object_number] = 3
        page_dict = engine._get_page_dict(0)
        engine._resolve(page_dict.mapping[PdfName("Annots")]).items[0] = cos.reference(annotation_ref.object_number)
        assert document.replace_link_urls(OLD, NEW) == 2
        assert engine._resolve(reference).value == OLD.encode()
        assert engine._annotation_refs(0)[0].gen_number == 3
        with Document(_bytes(document)) as reopened:
            assert _uris(reopened) == [NEW, NEW]


def test_primary_non_uri_actions_and_uri_next_chains_are_not_rewritten():
    with Document() as document:
        page = document.pages.add()
        page.add_link(RECT, FitDestination(0))
        page.add_link(RECT, URIAction(OLD))
        entry = _entry(document)
        next_action = PdfDictionary({PdfName("S"): PdfName("URI"), PdfName("URI"): PdfString(OLD)})
        entry.mapping[PdfName("A")] = PdfDictionary({
            PdfName("S"): PdfName("Named"), PdfName("N"): PdfName("NextPage"),
            PdfName("Next"): next_action,
        })
        assert document.replace_link_urls(OLD, NEW) == 1
        assert next_action.mapping[PdfName("URI")].value == OLD.encode()
        assert _uris(document) == [OLD, NEW]


def test_rewriting_preserves_full_text_cache_and_cursor():
    with Document() as document:
        _report(document)
        text = document.extract_text()
        engine = document._engine_pdf
        assert engine.get_next_page_text().startswith("Unchanged page 0")
        assert document.replace_link_urls(OLD, NEW) == 3
        assert engine._extracted_text == text
        assert engine.get_next_page_text().startswith("Unchanged page 1")


@pytest.mark.parametrize("dry_run", [False, True])
def test_new_action_object_limit_is_preflighted_before_edits(dry_run):
    with Document() as source:
        page = source.pages.add()
        for _ in range(2):
            page.add_link(RECT, URIAction(OLD))
        raw = _bytes(source)
    with Document(raw) as source:
        cos = source._engine_pdf._cos_doc
        maximum = max(max(cos.objects), max(cos.generations))
    with Document(raw, limits=PdfLoadLimits(max_objects=maximum + 1)) as document:
        engine = document._engine_pdf
        objects = set(engine._cos_doc.objects)
        entry = _entry(document)
        mapping = entry.mapping
        with pytest.raises(PdfResourceLimitException, match="max_objects"):
            document.replace_link_urls(OLD, NEW, dry_run=dry_run)
        assert entry.mapping is mapping and set(engine._cos_doc.objects) == objects
        assert _uris(document) == [OLD, OLD]


def test_registration_failure_rolls_back_new_objects_and_annotation_mappings(monkeypatch):
    with Document() as document:
        page = document.pages.add()
        for _ in range(2):
            page.add_link(RECT, URIAction(OLD))
        engine = document._engine_pdf
        cos = engine._cos_doc
        entries = [_entry(document, index) for index in range(2)]
        mappings = [entry.mapping for entry in entries]
        before = _bytes(document)
        objects = dict(cos.objects)
        register = cos.register_object
        calls = 0

        def failing(obj):
            nonlocal calls
            result = register(obj)
            calls += 1
            if calls == 2:
                raise RuntimeError("Injected action registration failure")
            return result

        monkeypatch.setattr(cos, "register_object", failing)
        with pytest.raises(RuntimeError, match="registration failure"):
            document.replace_link_urls(OLD, NEW)
        assert all(entry.mapping is mapping for entry, mapping in zip(entries, mappings))
        assert cos.objects == objects
        assert _bytes(document) == before


def test_success_keeps_annotation_identity_and_writes_indirect_action_copies():
    with Document() as document:
        document.pages.add().add_link(RECT, URIAction(OLD))
        engine = document._engine_pdf
        reference = engine._annotation_refs(0)[0]
        entry = engine._resolve(reference)
        old_objects = set(engine._cos_doc.objects)
        assert document.replace_link_urls(OLD, NEW) == 1
        current = engine._annotation_refs(0)[0]
        assert current is reference and engine._resolve(current) is entry
        assert len(set(engine._cos_doc.objects) - old_objects) == 1
        assert engine._resolve(entry.mapping[PdfName("A")]).mapping[PdfName("URI")].value == NEW.encode()

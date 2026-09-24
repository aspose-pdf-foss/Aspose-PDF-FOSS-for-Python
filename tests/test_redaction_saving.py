"""Redaction must remove recoverable content, not only its page reference."""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document, OptimizationOptions
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfDictionary,
    PdfName,
    PdfNumber,
    PdfStream,
    PdfString,
)
from aspose_pdf.engine.signing import SigningUtils
from aspose_pdf.exceptions import PdfSecurityException, PdfValidationException
from tests.test_form_xobject_text import (
    _OWN_FONT,
    _document,
    _form,
    _shared_form_document,
    _shows,
)

_SECRET = "REDACTION_SECRET_123"


def _saved(document, **kwargs):
    output = io.BytesIO()
    document.save(output, **kwargs)
    return output.getvalue()


def _source(*, packed=False, password=None):
    with Document() as document:
        document.pages.add().add_text("Public " + _SECRET, 40, 80)
        if packed:
            document.optimize(OptimizationOptions(use_object_streams=True))
        if password:
            document.encrypt(password, algorithm="AES-256")
        return _saved(document)


def _assert_removed(data, secret=_SECRET, *, password=None):
    assert secret.encode() not in data
    with Document(data, password=password) as loaded:
        assert secret not in loaded.extract_text()
        engine = loaded._engine_pdf
        pending = list(engine._cos_doc.objects.values())
        visited = set()
        while pending:
            obj = pending.pop()
            if id(obj) in visited:
                continue
            visited.add(id(obj))
            if isinstance(obj, PdfStream):
                assert secret.encode() not in engine._decode_cos_stream(obj)
            if isinstance(obj, PdfDictionary):
                pending.extend(obj.mapping.values())
            elif isinstance(obj, PdfArray):
                pending.extend(obj.items)
            elif isinstance(obj, PdfString):
                assert secret.encode() not in obj.value
                assert secret.encode("utf-16-be") not in obj.value


@pytest.mark.parametrize("packed", [False, True])
@pytest.mark.parametrize("password", [None, "secret-password"])
@pytest.mark.parametrize("incremental", [None, False])
def test_full_save_purges_old_content(packed, password, incremental, tmp_path):
    source = _source(packed=packed, password=password)
    with Document(source, password=password) as document:
        assert document.redact_text(_SECRET, overlay=True) == 1
        if packed:
            document.optimize(OptimizationOptions(
                use_object_streams=True,
                remove_unused_objects=False,
                remove_unused_streams=False,
                link_duplicate_streams=False,
            ))
        result = _saved(document, incremental=incremental)
        _assert_removed(result, password=password)
        with Document(result, password=password) as loaded:
            assert "Public" in loaded.extract_text()
        destination = tmp_path / "redacted.pdf"
        document.save(destination)
        _assert_removed(destination.read_bytes(), password=password)
        _assert_removed(_saved(document), password=password)


def test_incremental_save_is_rejected_without_writing(tmp_path):
    with Document(_source()) as document:
        document.redact_text(_SECRET)
        output = io.BytesIO(b"unchanged")
        with pytest.raises(PdfSecurityException, match="redact"):
            document.save(output, incremental=True)
        assert output.getvalue() == b"unchanged"
        with pytest.raises(PdfSecurityException, match="redact"):
            document._engine_pdf.to_bytes_incremental()
        destination = tmp_path / "output.pdf"
        with pytest.raises(PdfSecurityException, match="redact"):
            document._engine_pdf.save_incremental(destination)
        assert not destination.exists()


def test_no_match_does_not_disable_incremental_save():
    source = _source()
    with Document(source) as document:
        assert document.redact_text("absent") == 0
        assert _saved(document, incremental=True).startswith(source)


def test_reused_output_stream_does_not_keep_old_prefix_or_tail():
    source = _source()
    with Document(source) as document:
        document.redact_text(_SECRET)
        output = io.BytesIO(source + b"\n" + _SECRET.encode() * 500)
        output.seek(len(source))
        document.save(output)
        _assert_removed(output.getvalue())


def test_signed_document_defaults_to_full_redacted_save(caplog):
    certificate, key = SigningUtils.create_self_signed_cert()
    with Document(_source()) as document:
        document.sign(certificate=certificate, private_key=key)
        source = _saved(document)
    with Document(source) as document:
        assert document.signatures[0].valid
        document.redact_text(_SECRET)
        result = _saved(document)
    _assert_removed(result)
    assert not result.startswith(source)
    assert "signature" in caplog.text.lower()
    assert "redact" in caplog.text.lower()


def test_nested_forms_do_not_leave_original_streams():
    with _document(
        b"/Fm1 Do\n",
        {
            6: _form(b"/Fm2 Do", b"/XObject << /Fm2 7 0 R >> "),
            7: _form(_shows(_SECRET.encode()), _OWN_FONT),
        },
    ) as document:
        assert document.redact_text(_SECRET) == 1
        _assert_removed(_saved(document))


def test_redacting_all_uses_of_a_shared_form_purges_the_original():
    with _shared_form_document() as document:
        assert document.redact_text("shared") == 2
        _assert_removed(_saved(document), "shared")


def test_selected_page_keeps_another_pages_shared_content():
    with _shared_form_document() as document:
        assert document.pages[0].redact_text("shared") == 1
        result = _saved(document)
    with Document(result) as loaded:
        assert loaded.pages[0].extract_text() == "text"
        assert loaded.pages[1].extract_text() == "shared text"


@pytest.mark.parametrize("property_list", [
    b"<< /ActualText (REDACTION_SECRET_123) /Alt (REDACTION_SECRET_123) >>",
    b"<< /Actual#54ext <524544414354494f4e5f5345435245545f313233> >>",
])
def test_inline_alternate_text_is_removed_with_its_run(property_list):
    content = b"/Span " + property_list + b" BDC " + _shows(_SECRET.encode()) + b" EMC"
    with _document(content, {6: _form(b"")}) as document:
        assert document.redact_text(_SECRET) == 1
        result = _saved(document)
        _assert_removed(result)
        assert b"/ActualText" not in document.pages[0].content
        assert b"/Actual#54ext" not in document.pages[0].content


def test_structure_alternate_text_is_removed_but_unrelated_tags_survive():
    with Document() as document:
        page = document.pages.add()
        page.add_text(_SECRET, 40, 80, tag="P", actual_text=_SECRET)
        page.add_text("Public", 40, 100, tag="P", actual_text="Public alternate")
        source = _saved(document)
    with Document(source) as document:
        assert document.redact_text(_SECRET) == 1
        result = _saved(document)
        _assert_removed(result)
        assert document.tagged_content.root_elements[1].actual_text == "Public alternate"


def test_garbage_collection_writes_free_xref_entries():
    with Document(_source()) as document:
        document.redact_text(_SECRET)
        result = _saved(document)
    assert b"0000000000 00000 n" not in result


@pytest.mark.parametrize("packed", [False, True])
@pytest.mark.parametrize("inherited", [False, True])
def test_named_actual_text_is_not_recoverable(packed, inherited):
    with _document(
        b"/Span /AT BDC " + _shows(_SECRET.encode()) + b" EMC",
        {6: _form(b"")},
    ) as document:
        engine = document._engine_pdf
        page = engine._get_page_dict(0)
        resources = engine._resolve_resources_cos(page)
        resources.mapping[PdfName("Properties")] = PdfDictionary({
            PdfName("AT"): engine._cos_doc.register_object(PdfDictionary({
                PdfName("ActualText"): PdfString(_SECRET),
            })),
        })
        if inherited:
            parent = engine._resolve(page.mapping[PdfName("Parent")])
            parent.mapping[PdfName("Resources")] = page.mapping.pop(PdfName("Resources"))
        if packed:
            document.optimize(OptimizationOptions(use_object_streams=True))
        source = _saved(document)
    with Document(source) as document:
        assert document.redact_text(_SECRET) == 1
        _assert_removed(_saved(document))


def test_named_property_shared_with_unmatched_scope_is_preserved():
    with _document(
        b"/Span /AT BDC " + _shows(_SECRET.encode()) + b" EMC "
        b"/Span /AT BDC " + _shows(b"Public") + b" EMC",
        {6: _form(b"")},
    ) as document:
        engine = document._engine_pdf
        resources = engine._resolve_resources_cos(engine._get_page_dict(0))
        resources.mapping[PdfName("Properties")] = PdfDictionary({
            PdfName("AT"): PdfDictionary({PdfName("ActualText"): PdfString("Alternate")}),
        })
        assert document.redact_text(_SECRET) == 1
        _assert_removed(_saved(document))
        assert document.pages[0].content.count(b"/AT BDC") == 1
        assert b"Alternate" in _saved(document)


def test_tagged_form_is_refused_instead_of_retaining_structure_copies():
    with _document(b"/Fm1 Do", {6: _form(_shows(_SECRET.encode()), _OWN_FONT)}) as document:
        document._engine_pdf._cos_doc.objects[6].mapping[PdfName("StructParents")] = PdfNumber(0)
        with pytest.raises(PdfValidationException, match="tagged Form"):
            document.redact_text(_SECRET)
        assert _SECRET in document.pages[0].extract_text()


def test_engine_save_cos_cannot_bypass_cleanup(tmp_path):
    with Document(_source()) as document:
        document.redact_text(_SECRET)
        destination = tmp_path / "cos.pdf"
        document._engine_pdf.save_cos(destination)
        _assert_removed(destination.read_bytes())


def test_redacted_output_refuses_append_and_nonseekable_streams(tmp_path):
    class Nonseekable(io.BytesIO):
        def seekable(self):
            return False

    with Document(_source()) as document:
        document.redact_text(_SECRET)
        output = Nonseekable()
        with pytest.raises(PdfSecurityException, match="seekable"):
            document.save(output)
        assert output.getvalue() == b""
        destination = tmp_path / "append.pdf"
        destination.write_bytes(b"original")
        with destination.open("ab") as output:
            with pytest.raises(PdfSecurityException, match="append"):
                document.save(output)
        assert destination.read_bytes() == b"original"


def test_alternate_text_around_a_form_invocation_is_removed():
    with _document(
        b"/Span << /ActualText (" + _SECRET.encode() + b") >> BDC /Fm1 Do EMC",
        {6: _form(_shows(_SECRET.encode()), _OWN_FONT)},
    ) as document:
        assert document.redact_text(_SECRET) == 1
        _assert_removed(_saved(document))


def test_unused_alias_of_a_redacted_form_does_not_keep_the_old_stream():
    with _document(b"/Fm1 Do", {6: _form(_shows(_SECRET.encode()), _OWN_FONT)}) as document:
        engine = document._engine_pdf
        resources = engine._resolve_resources_cos(engine._get_page_dict(0))
        xobjects = resources.mapping[PdfName("XObject")]
        xobjects.mapping[PdfName("UnusedAlias")] = xobjects.mapping[PdfName("Fm1")]
        assert document.redact_text(_SECRET) == 1
        _assert_removed(_saved(document))


def test_named_alternate_text_in_forms_fails_closed():
    with _document(b"/Fm1 Do", {6: _form(
        b"/Span /AT BDC " + _shows(_SECRET.encode()) + b" EMC",
        _OWN_FONT + b"/Properties << /AT << /ActualText (" + _SECRET.encode() + b") >> >>",
    )}) as document:
        with pytest.raises(PdfValidationException, match="named alternate-text"):
            document.redact_text(_SECRET)


def test_redaction_discards_all_previous_incremental_revisions():
    source = _source()
    with Document(source) as document:
        document.pages.add().add_text("Another page", 40, 80)
        revised = _saved(document, incremental=True)
    assert revised.startswith(source)
    with Document(revised) as document:
        assert document.redact_text(_SECRET) == 1
        result = _saved(document)
    assert result.count(b"%%EOF") == 1
    _assert_removed(result)


def test_lazy_redaction_can_overwrite_its_input_file(tmp_path):
    source = tmp_path / "source.pdf"
    source.write_bytes(_source(packed=True))
    with Document.open_streaming(source) as document:
        assert document.pages[0].redact_text(_SECRET) == 1
        document.save(source, overwrite=True)
        _assert_removed(source.read_bytes())
        _assert_removed(_saved(document))


def test_redacting_compressed_content_array_removes_all_source_streams():
    import zlib

    with _document(_shows(b"Public"), {6: _form(b"")}) as document:
        engine = document._engine_pdf
        page = engine._get_page_dict(0)
        secret_stream = engine._cos_doc.register_object(PdfStream(
            content=zlib.compress(_shows(_SECRET.encode())),
            mapping={PdfName("Filter"): PdfName("FlateDecode")},
        ))
        page.mapping[PdfName("Contents")] = PdfArray([
            page.mapping[PdfName("Contents")], secret_stream,
        ])
        source = _saved(document)
    with Document(source) as document:
        assert document.redact_text(_SECRET) == 1
        _assert_removed(_saved(document))


def test_partial_redaction_failure_still_disables_incremental_save():
    with Document() as document:
        document.pages.add().add_text(_SECRET, 40, 80)
        document.pages.add().add_text(_SECRET, 40, 80)
        source = _saved(document)
    with Document(source) as document:
        engine = document._engine_pdf
        engine._set_page_content(1, b"/Span /Missing BDC " + _shows(_SECRET.encode()) + b" EMC")
        with pytest.raises(PdfValidationException, match="resolve"):
            document.redact_text(_SECRET)
        assert _SECRET not in document.pages[0].extract_text()
        with pytest.raises(PdfSecurityException, match="redact"):
            _saved(document, incremental=True)


@pytest.mark.parametrize("missing", ["ParentTree", "StructParents"])
def test_redaction_refuses_missing_structure_parent_mappings(missing):
    with Document() as document:
        document.pages.add().add_text(_SECRET, 40, 80, tag="P", actual_text=_SECRET)
        engine = document._engine_pdf
        root, _ref = engine._tagged_struct_tree_root()
        owner = root if missing == "ParentTree" else engine._get_page_dict(0)
        owner.mapping.pop(PdfName(missing))
        with pytest.raises(PdfValidationException, match="structure"):
            document.redact_text(_SECRET)


def test_redaction_refuses_ambiguous_property_aliases():
    with _document(
        b"/Span /AT BDC " + _shows(_SECRET.encode()) + b" EMC",
        {6: _form(b"")},
    ) as document:
        engine = document._engine_pdf
        resources = engine._resolve_resources_cos(engine._get_page_dict(0))
        properties = PdfDictionary({PdfName("ActualText"): PdfString(_SECRET)})
        resources.mapping[PdfName("Properties")] = PdfDictionary({
            PdfName("AT"): properties,
            PdfName("Alias"): properties,
        })
        with pytest.raises(PdfValidationException, match="aliased"):
            document.redact_text(_SECRET)


def test_page_redaction_does_not_drop_properties_inherited_by_a_form():
    with _document(
        b"/Span /AT BDC " + _shows(_SECRET.encode()) + b" EMC /Fm1 Do",
        {6: _form(b"/Span /AT BDC " + _shows(b"Public") + b" EMC", None)},
    ) as document:
        engine = document._engine_pdf
        resources = engine._resolve_resources_cos(engine._get_page_dict(0))
        resources.mapping[PdfName("Properties")] = PdfDictionary({
            PdfName("AT"): PdfDictionary({PdfName("ActualText"): PdfString("Alternate")}),
        })
        with pytest.raises(PdfValidationException, match="inherited"):
            document.redact_text(_SECRET)
        assert PdfName("AT") in resources.mapping[PdfName("Properties")].mapping

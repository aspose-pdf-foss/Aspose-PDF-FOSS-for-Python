"""Link to remote PDF sections and retain the viewer's window preference."""

from __future__ import annotations

import io
import json

import pytest

from aspose_pdf import (
    Document,
    FitDestination,
    GoToRAction,
    OutlineItem,
    XYZDestination,
)
from aspose_pdf.annotations import Name
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfBoolean,
    PdfDictionary,
    PdfName,
    PdfNull,
    PdfNumber,
    PdfString,
)
from aspose_pdf.engine.simple_pdf import action_from_cos
from aspose_pdf.interactive import action_from_spec


def _bytes(document, **options):
    with io.BytesIO() as output:
        document.save(output, **options)
        return output.getvalue()


@pytest.mark.parametrize("new_window", [None, False, True])
@pytest.mark.parametrize(
    "destination",
    [
        FitDestination(123),
        XYZDestination(45, top=700, zoom=1.5),
        "chapter-2",
        "détails\x80",
        Name("legacy.section"),
        Name("章节 (draft)"),
    ],
)
@pytest.mark.parametrize("on_disk", [False, True])
def test_remote_actions_round_trip_through_links_outlines_buttons_and_open_action(
    tmp_path, new_window, destination, on_disk
):
    action = GoToRAction("missing-manual.pdf", destination, new_window=new_window)
    with Document() as document:
        page = document.pages.add()
        page.add_text("Open the manual", 20, 700)
        content = page.content
        page.add_link((20, 695, 180, 715), action)
        document.outlines.add(OutlineItem("Manual", destination=action))
        document.form.add_push_button(
            "open", page, (20, 600, 180, 630), caption="Manual", action=action
        )
        document.open_action = action
        assert document.open_action == action
        assert document.form["open"].actions["A"] == action
        properties = page.annotations[0].get_property("A")
        if new_window is None:
            assert "NewWindow" not in properties
        else:
            assert properties["NewWindow"] is new_window
        report = json.loads(document.export_bookmarks_json())
        remote = report["bookmarks"][0]["target"]
        assert remote["kind"] == "remote"
        assert remote["new_window"] is new_window
        if isinstance(destination, str):
            assert remote["destination"]["kind"] == "named"
            assert remote["destination"]["name"] == destination
            assert remote["destination"]["representation"] == (
                "name" if isinstance(destination, Name) else "string"
            )
        if on_disk:
            source = tmp_path / "remote-links.pdf"
            document.save(source)
        else:
            source = _bytes(document)
        with Document(source) as reopened:
            assert reopened.open_action == action
            assert reopened.outlines[0].destination == action
            assert reopened.outlines[0].page_index is None
            assert reopened.form["open"].actions["A"] == action
            assert reopened.pages[1].content == content
            reopened.outlines[0].destination = reopened.outlines[0].destination
            with Document(_bytes(reopened)) as final:
                assert final.outlines[0].destination == action
                assert final.open_action == action
                assert (
                    json.loads(final.export_bookmarks_json())["bookmarks"][0]["target"]
                    == remote
                )


@pytest.mark.parametrize("name", ["\xc3\xa9", "\xfe\xff", "\x00chapter", "café"])
def test_remote_destination_strings_are_byte_names_not_local_text_names(name):
    action = GoToRAction("other.pdf", name, new_window=True)
    with Document() as document:
        page = document.pages.add()
        document.destinations[name] = FitDestination(1)
        document.open_action = action
        pdf = document._engine_pdf
        root = pdf._resolve(pdf._cos_doc.trailer.mapping[PdfName("Root")])
        raw = pdf._resolve(root.mapping[PdfName("OpenAction")])
        assert raw[PdfName("D")].value == name.encode("latin-1")
        page.add_link((20, 20, 180, 40), action)
        assert document.open_action == action
        with Document(_bytes(document)) as reopened:
            assert reopened.open_action == action
            assert reopened.open_action.destination == name


@pytest.mark.parametrize("new_window", [None, False, True])
def test_bookmark_json_exchange_keeps_remote_names_and_window_mode(new_window):
    action = GoToRAction("other.pdf", "remote-section", new_window=new_window)
    with Document() as source, Document() as destination:
        source.pages.add()
        source.outlines.add(OutlineItem("Remote", destination=action))
        destination.pages.add()
        data = source.export_bookmarks_json()
        destination.import_bookmarks_json(data)
        assert destination.outlines[0].destination == action
        assert destination.export_bookmarks_json() == data


@pytest.mark.parametrize("flag", [0, 1, "true", [], object()])
def test_invalid_window_options_fail_without_adding_a_link(flag):
    with Document() as document:
        page = document.pages.add()
        before = _bytes(document)
        with pytest.raises(TypeError, match="new_window"):
            page.add_link(
                (20, 20, 180, 40), GoToRAction("other.pdf", "chapter", new_window=flag)
            )
        assert _bytes(document) == before


@pytest.mark.parametrize(
    "destination,error",
    [
        ("", ValueError),
        ("章节", ValueError),
        ("\ud800", ValueError),
        (Name("/chapter"), ValueError),
        (b"chapter", TypeError),
        ([], TypeError),
        (object(), TypeError),
    ],
)
def test_invalid_named_targets_fail_without_adding_a_link(destination, error):
    with Document() as document:
        page = document.pages.add()
        before = _bytes(document)
        with pytest.raises(error, match="destination"):
            page.add_link((20, 20, 180, 40), GoToRAction("other.pdf", destination))
        assert _bytes(document) == before


@pytest.mark.parametrize("new_window", [None, False, True])
def test_action_specs_retain_optional_window_mode(new_window):
    spec = {"S": "GoToR", "F": "other.pdf", "D": "chapter"}
    if new_window is not None:
        spec["NewWindow"] = new_window
    action = action_from_spec(spec)
    assert action == GoToRAction("other.pdf", "chapter", new_window=new_window)
    assert action._spec() == spec


@pytest.mark.parametrize("flag", [0, 1, "true", [], {}])
def test_malformed_window_modes_stay_untyped(flag):
    assert (
        action_from_spec(
            {"S": "GoToR", "F": "other.pdf", "D": "chapter", "NewWindow": flag}
        )
        is None
    )


@pytest.mark.parametrize("value", [PdfNumber(0), PdfString("true"), PdfDictionary({})])
def test_malformed_pdf_window_modes_stay_untyped(value):
    with Document() as document:
        document.pages.add()
        pdf = document._engine_pdf
        raw = PdfDictionary(
            {
                PdfName("S"): PdfName("GoToR"),
                PdfName("F"): PdfString("other.pdf"),
                PdfName("D"): PdfString("chapter"),
                PdfName("NewWindow"): value,
            }
        )
        assert action_from_cos(raw, pdf._resolve, pdf._page_index_of) is None


def test_indirect_boolean_and_name_representation_are_preserved():
    with Document() as document:
        document.pages.add()
        pdf = document._engine_pdf
        raw = PdfDictionary(
            {
                PdfName("S"): PdfName("GoToR"),
                PdfName("F"): PdfString("other.pdf"),
                PdfName("D"): pdf._cos_doc.register_object(PdfName("legacy")),
                PdfName("NewWindow"): pdf._cos_doc.register_object(PdfBoolean(False)),
            }
        )
        action = action_from_cos(raw, pdf._resolve, pdf._page_index_of)
        assert action == GoToRAction("other.pdf", Name("legacy"), new_window=False)
        assert isinstance(action.destination, Name)
        document.open_action = action
        root = pdf._resolve(pdf._cos_doc.trailer.mapping[PdfName("Root")])
        written = root[PdfName("OpenAction")]
        assert isinstance(written[PdfName("D")], PdfName)
        raw.mapping[PdfName("NewWindow")] = PdfNull()
        assert action_from_cos(raw, pdf._resolve, pdf._page_index_of).new_window is None


def test_remote_page_references_are_not_resolved_against_the_local_document():
    with Document() as document:
        document.pages.add()
        pdf = document._engine_pdf
        raw = PdfDictionary(
            {
                PdfName("S"): PdfName("GoToR"),
                PdfName("F"): PdfString("other.pdf"),
                PdfName("D"): PdfArray([pdf._make_page_ref(0), PdfName("Fit")]),
            }
        )
        assert action_from_cos(raw, pdf._resolve, pdf._page_index_of) is None


def test_chained_remote_actions_stay_untyped_and_keep_their_original_dictionary():
    spec = {"S": "GoToR", "F": "other.pdf", "D": "chapter", "Next": {"S": "Named"}}
    assert action_from_spec(spec) is None
    with Document() as document:
        document.pages.add()
        document.open_action = GoToRAction("other.pdf", "chapter", new_window=False)
        pdf = document._engine_pdf
        root = pdf._resolve(pdf._cos_doc.trailer.mapping[PdfName("Root")])
        raw = root[PdfName("OpenAction")]
        raw[PdfName("Next")] = PdfDictionary(
            {PdfName("S"): PdfName("Named"), PdfName("N"): PdfName("NextPage")}
        )
        assert document.open_action is None
        with Document(_bytes(document)) as reopened:
            assert reopened.open_action is None
            pdf = reopened._engine_pdf
            root = pdf._resolve(pdf._cos_doc.trailer.mapping[PdfName("Root")])
            saved = pdf._resolve(root[PdfName("OpenAction")])
            assert saved[PdfName("D")].value == b"chapter"
            assert saved[PdfName("NewWindow")].value is False
            assert saved[PdfName("Next")][PdfName("N")] == PdfName("NextPage")


def test_legacy_destination_free_actions_and_numeric_remote_pages_keep_defaults():
    assert GoToRAction("other.pdf")._spec() == {"S": "GoToR", "F": "other.pdf"}
    assert action_from_spec(
        {"S": "GoToR", "F": "other.pdf", "D": [122, "Fit"]}
    ) == GoToRAction("other.pdf", FitDestination(123))


def test_remote_names_survive_page_edits_and_merging_without_local_resolution():
    action = GoToRAction("other.pdf", "chapter", new_window=True)
    with Document() as source, Document() as target:
        for _ in range(2):
            source.pages.add()
        source.destinations["chapter"] = FitDestination(2)
        source.open_action = action
        source.outlines.add(OutlineItem("Remote", destination=action))
        source.pages[1].add_link((20, 20, 180, 40), action)
        source.pages.delete(2)
        target.pages.add()
        target.merge(source)
        with Document(_bytes(target)) as merged:
            assert merged.outlines[0].destination == action
            properties = merged.pages[2].annotations[0].get_property("A")
            assert properties["D"] == "chapter" and properties["NewWindow"] is True


def test_encrypted_streaming_round_trip_retains_remote_names_and_window_flags(
    tmp_path, monkeypatch
):
    path = tmp_path / "encrypted.pdf"
    action = GoToRAction("other.pdf", "chapter", new_window=False)
    with Document() as document:
        document.pages.add()
        document.open_action = action
        document.outlines.add(OutlineItem("Remote", destination=action))
        document.encrypt("user", "owner", algorithm="AES-256")
        document.save(path)
    original = path.read_bytes()
    with Document.open_streaming(path, password="owner") as reopened:
        pdf = reopened._engine_pdf
        with monkeypatch.context() as patch:
            patch.setattr(
                pdf, "get_page_content", lambda *a: pytest.fail("No page content reads")
            )
            assert reopened.open_action == action
            assert reopened.outlines[0].destination == action
            reopened.open_action = GoToRAction("other.pdf", "appendix", new_window=True)
            assert pdf._lazy and pdf.page_contents == []
        updated = _bytes(reopened, incremental=True)
    assert updated.startswith(original)
    with Document(updated, password="owner") as final:
        assert final.open_action == GoToRAction(
            "other.pdf", "appendix", new_window=True
        )
        assert final.outlines[0].destination == action

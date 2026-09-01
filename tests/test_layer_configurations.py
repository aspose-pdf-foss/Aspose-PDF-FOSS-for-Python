"""Alternate optional content configurations, and usage that switches layers.

A PDF's layer states are not just one list. ``/OCProperties /Configs`` holds
named alternates a viewer offers as presets, and a group's ``/Usage``
dictionary says what it should do when *printed* or *exported* rather than
viewed -- which the configuration's ``/AS`` entries turn into actual states for
an event. Neither was read before: only ``/D`` was applied, and everything
resolved for the on-screen case.

The distinction that matters throughout: ``/Usage`` on its own is a statement
about a group and changes nothing. ``/AS`` is what applies it, so a
"do not print" watermark that no ``/AS`` entry mentions really does print.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.cos import PdfArray, PdfDictionary, PdfName, PdfNumber
from aspose_pdf.exceptions import PdfValidationException


def _document_with_usage() -> Document:
    """A body layer plus a watermark that asks not to be printed or exported."""
    document = Document()
    page = document.pages.add()
    page.add_text("Body text", 60, 700, font_size=14)
    watermark = document.layers.add("Watermark")
    with page.layer(watermark):
        page.add_text("DRAFT", 100, 400, font_size=40)
    document.layers["Watermark"].set_usage(printing=False, export=False)
    return document


def _oc_properties(document: Document) -> PdfDictionary:
    engine = document._engine_pdf
    root = engine._resolve(engine._cos_doc.trailer.mapping.get(PdfName("Root")))
    return engine._resolve(root.mapping.get(PdfName("OCProperties")))


def _default_config(document: Document) -> PdfDictionary:
    engine = document._engine_pdf
    return engine._resolve(_oc_properties(document).mapping.get(PdfName("D")))


def _reference(layer) -> object:
    from aspose_pdf.engine.cos import PdfIndirectReference

    return PdfIndirectReference(layer.object_number, 0)


# ---------------------------------------------------------------------------
# Reading /Configs
# ---------------------------------------------------------------------------


def test_a_document_lists_its_default_configuration_first():
    document = Document()
    document.pages.add()
    document.layers.add("Body")

    configurations = document.layers.configurations

    assert len(configurations) == 1
    assert configurations[0].is_default and configurations[0].index == -1


def test_a_saved_configuration_joins_the_list():
    document = Document()
    document.pages.add()
    document.layers.add("Body")

    saved = document.layers.save_configuration("Clean copy", creator="a test")

    assert saved.name == "Clean copy" and saved.creator == "a test"
    assert saved.index == 0 and not saved.is_default
    assert [config.name for config in document.layers.configurations] == [
        "",
        "Clean copy",
    ]


def test_a_configuration_reports_the_states_it_holds_not_the_current_ones():
    document = Document()
    document.pages.add()
    document.layers.add("Body")
    document.layers.add("Notes")
    document.layers["Notes"].visible = False
    document.layers.save_configuration("Without notes")

    document.layers["Notes"].visible = True  # move on from the snapshot

    saved = document.layers.configurations[1]
    assert saved.shows(document.layers["Body"])
    assert not saved.shows(document.layers["Notes"])
    assert document.layers["Notes"].visible  # ...the document itself moved on


def _order_names(document: Document, config: PdfDictionary) -> list[str]:
    engine = document._engine_pdf
    order = engine._resolve(config.mapping.get(PdfName("Order")))
    names = []
    for item in order.items:
        group = engine._resolve(item)
        names.append(str(group.mapping[PdfName("Name")].value, "latin-1"))
    return names


def _alternate(document: Document, index: int = 0) -> PdfDictionary:
    engine = document._engine_pdf
    configs = engine._resolve(_oc_properties(document).mapping.get(PdfName("Configs")))
    return engine._resolve(configs.items[index])


def test_a_snapshot_does_not_alias_the_default_configurations_arrays():
    # The snapshot has to be a copy: sharing /D's /Order list would make every
    # layer added afterwards silently rewrite the preset too.
    document = Document()
    document.pages.add()
    document.layers.add("Body")
    document.layers.save_configuration("Just the body")

    document.layers.add("Notes")

    assert _order_names(document, _alternate(document)) == ["Body"]
    assert _order_names(document, _default_config(document)) == ["Body", "Notes"]


def test_applying_a_configuration_does_not_alias_its_arrays_either():
    document = Document()
    document.pages.add()
    document.layers.add("Body")
    document.layers.save_configuration("Just the body")
    document.layers.add("Notes")

    document.layers.apply_configuration("Just the body")
    document.layers.add("Later")

    assert _order_names(document, _alternate(document)) == ["Body"]
    assert _order_names(document, _default_config(document)) == ["Body", "Later"]


def test_a_configuration_reports_which_layers_it_locks():
    document = Document()
    document.pages.add()
    body = document.layers.add("Body")
    config = _default_config(document)
    config.mapping[PdfName("Locked")] = PdfArray([_reference(body)])

    notes = document.layers.add("Notes")

    default = document.layers.configurations[0]
    assert default.locks(document.layers["Body"])
    assert not default.locks(notes)


def test_a_configuration_needs_a_name():
    document = Document()
    document.pages.add()
    document.layers.add("Body")
    with pytest.raises(PdfValidationException):
        document.layers.save_configuration("")


# ---------------------------------------------------------------------------
# Applying a configuration
# ---------------------------------------------------------------------------


def test_applying_a_configuration_by_name_adopts_its_states():
    document = Document()
    document.pages.add()
    document.layers.add("Body")
    document.layers.add("Notes")
    document.layers["Notes"].visible = False
    document.layers.save_configuration("Without notes")
    document.layers["Notes"].visible = True

    assert document.layers.apply_configuration("Without notes")

    assert not document.layers["Notes"].visible
    assert document.layers["Body"].visible


def test_a_configuration_can_be_applied_by_index_or_by_object():
    document = Document()
    document.pages.add()
    document.layers.add("Notes")
    document.layers["Notes"].visible = False
    saved = document.layers.save_configuration("Without notes")

    document.layers["Notes"].visible = True
    assert document.layers.apply_configuration(0)
    assert not document.layers["Notes"].visible

    document.layers["Notes"].visible = True
    assert document.layers.apply_configuration(saved)
    assert not document.layers["Notes"].visible


def test_applying_the_default_configuration_does_nothing():
    document = Document()
    document.pages.add()
    document.layers.add("Body")

    assert not document.layers.apply_configuration(document.layers.configurations[0])


def test_applying_an_unknown_configuration_reports_it():
    document = Document()
    document.pages.add()
    document.layers.add("Body")
    document.layers.add("Notes")
    document.layers["Notes"].visible = False
    document.layers.save_configuration("Without notes")
    document.layers["Notes"].visible = True

    with pytest.raises(KeyError):
        document.layers.apply_configuration("no such preset")
    assert not document.layers.apply_configuration(7)  # past the end
    # -1 is the default configuration, not "the last one" -- applying it is a
    # no-op, and must not reach into /Configs from the back.
    assert not document.layers.apply_configuration(-1)
    assert document.layers["Notes"].visible


def test_applying_a_configuration_carries_base_state_across():
    # A configuration that starts everything off and names its exceptions is
    # not reducible to an /OFF list, so /BaseState has to travel too.
    document = Document()
    document.pages.add()
    body = document.layers.add("Body")
    document.layers.add("Notes")
    properties = _oc_properties(document)
    properties.mapping[PdfName("Configs")] = PdfArray(
        [
            PdfDictionary(
                {
                    PdfName("Name"): _text("Only body"),
                    PdfName("BaseState"): PdfName("OFF"),
                    PdfName("ON"): PdfArray([_reference(body)]),
                }
            )
        ]
    )

    assert document.layers.apply_configuration("Only body")

    assert document.layers["Body"].visible
    assert not document.layers["Notes"].visible
    assert _default_config(document).mapping[PdfName("BaseState")].name.lstrip(
        "/"
    ) == "OFF"


def test_applying_a_configuration_leaves_it_available_to_apply_again():
    document = Document()
    document.pages.add()
    document.layers.add("Notes")
    document.layers["Notes"].visible = False
    document.layers.save_configuration("Without notes")
    document.layers["Notes"].visible = True

    document.layers.apply_configuration("Without notes")

    assert [config.name for config in document.layers.configurations] == [
        "",
        "Without notes",
    ]


def test_an_applied_configuration_decides_what_flattening_keeps(tmp_path: Path):
    document = _document_with_usage()
    document.layers["Watermark"].visible = False
    document.layers.save_configuration("No watermark")
    document.layers["Watermark"].visible = True

    document.layers.apply_configuration("No watermark")
    document.flatten_layers()
    target = tmp_path / "flat.pdf"
    document.save(str(target))

    assert b"DRAFT" not in target.read_bytes()


def _text(value: str):
    from aspose_pdf.engine.simple_pdf import _pdf_text_string

    return _pdf_text_string(value)


# ---------------------------------------------------------------------------
# Usage application dictionaries (/AS)
# ---------------------------------------------------------------------------


def test_a_layer_can_be_told_not_to_print():
    document = _document_with_usage()

    assert document.layers.resolve("View")["Watermark"]
    assert not document.layers.resolve("Print")["Watermark"]
    assert not document.layers.resolve("Export")["Watermark"]


def test_usage_without_an_application_entry_changes_nothing():
    # This is the whole point of /AS: a /Usage dictionary is a statement about
    # the group, and only an application entry turns it into a state.
    document = Document()
    document.pages.add()
    layer = document.layers.add("Watermark")
    engine = document._engine_pdf
    group = engine._resolve(_reference(layer))
    group.mapping[PdfName("Usage")] = PdfDictionary(
        {PdfName("Print"): PdfDictionary({PdfName("PrintState"): PdfName("OFF")})}
    )

    assert document.layers.resolve("Print")["Watermark"]


def test_an_application_entry_for_another_event_is_ignored():
    document = _document_with_usage()
    states = document.layers.resolve("View")
    assert states["Watermark"]  # the entries name Print and Export, not View


def test_a_layer_with_no_usage_keeps_its_configured_state():
    document = _document_with_usage()
    document.layers.add("Body")
    assert document.layers.resolve("Print")["Body"]

    document.layers["Body"].visible = False
    assert not document.layers.resolve("Print")["Body"]


def test_categories_combine_so_that_any_off_wins():
    document = Document()
    document.pages.add()
    layer = document.layers.add("Both")
    layer.set_usage(view=True, printing=False)
    # One /AS entry consulting both categories: View says ON, Print says OFF.
    config = _default_config(document)
    entries = document._engine_pdf._resolve(config.mapping.get(PdfName("AS")))
    for entry in entries.items:
        entry.mapping[PdfName("Event")] = PdfName("View")
        entry.mapping[PdfName("Category")] = PdfArray(
            [PdfName("View"), PdfName("Print")]
        )

    assert not document.layers.resolve("View")["Both"]


def test_a_zoom_range_switches_a_layer_inside_and_outside_it():
    document = Document()
    document.pages.add()
    document.layers.add("Detail").set_usage(zoom=(2.0, 8.0))

    assert document.layers.resolve("View", zoom=4.0)["Detail"]
    assert not document.layers.resolve("View", zoom=1.0)["Detail"]
    assert document.layers.resolve("View", zoom=2.0)["Detail"]  # min is inclusive
    assert not document.layers.resolve("View", zoom=8.0)["Detail"]  # max is not


def test_without_a_zoom_a_zoom_layer_keeps_its_configured_state():
    # Inventing a magnification would decide visibility on made-up grounds.
    document = Document()
    document.pages.add()
    document.layers.add("Detail").set_usage(zoom=(2.0, 8.0))

    assert document.layers.resolve("View")["Detail"]
    document.layers["Detail"].visible = False
    assert not document.layers.resolve("View")["Detail"]


def test_a_language_layer_matches_exactly_and_by_prefix():
    document = Document()
    document.pages.add()
    document.layers.add("Deutsch").set_usage(language="de")

    assert document.layers.resolve("View", language="de")["Deutsch"]
    assert document.layers.resolve("View", language="de-AT")["Deutsch"]
    assert not document.layers.resolve("View", language="en-US")["Deutsch"]


def test_a_language_tag_matches_whole_subtags_not_bare_prefixes():
    # "den" (Slave) starts with "de" as a string but is not a German dialect.
    document = Document()
    document.pages.add()
    document.layers.add("Deutsch").set_usage(language="de")

    assert not document.layers.resolve("View", language="den")["Deutsch"]
    assert document.layers.resolve("View", language="de-CH")["Deutsch"]


def test_a_more_specific_language_does_not_match_a_sibling():
    document = Document()
    document.pages.add()
    document.layers.add("British").set_usage(language="en-GB")

    assert document.layers.resolve("View", language="en-GB")["British"]
    assert not document.layers.resolve("View", language="en-US")["British"]


def test_a_preferred_language_layer_stands_in_when_nothing_matches():
    document = Document()
    document.pages.add()
    document.layers.add("English").set_usage(language="en", preferred=True)
    document.layers.add("Deutsch").set_usage(language="de")

    fallback = document.layers.resolve("View", language="fr")
    assert fallback["English"] and not fallback["Deutsch"]

    exact = document.layers.resolve("View", language="de")
    assert exact["Deutsch"] and not exact["English"]


def test_without_a_language_a_language_layer_keeps_its_configured_state():
    document = Document()
    document.pages.add()
    document.layers.add("Deutsch").set_usage(language="de")

    assert document.layers.resolve("View")["Deutsch"]


def test_resolve_rejects_an_event_that_is_not_one():
    document = _document_with_usage()
    with pytest.raises(ValueError, match="View, Print, Export"):
        document.layers.resolve("Fax")


def test_applying_usage_makes_the_event_s_states_the_documents_own():
    document = _document_with_usage()
    assert document.layers["Watermark"].visible

    changed = document.layers.apply_usage("Print")

    assert changed == 1
    assert not document.layers["Watermark"].visible
    assert document.layers.apply_usage("Print") == 0  # already there


def test_applying_usage_then_flattening_removes_what_would_not_print(
    tmp_path: Path,
):
    document = _document_with_usage()

    document.layers.apply_usage("Print")
    document.flatten_layers()
    target = tmp_path / "print.pdf"
    document.save(str(target))

    data = target.read_bytes()
    assert b"DRAFT" not in data
    assert b"Body text" in data


def test_usage_survives_a_save_and_reload(tmp_path: Path):
    document = _document_with_usage()
    document.layers.add("Detail").set_usage(zoom=(2.0, 8.0))
    target = tmp_path / "usage.pdf"
    document.save(str(target))

    reloaded = Document(str(target))

    assert not reloaded.layers.resolve("Print")["Watermark"]
    assert reloaded.layers.resolve("View")["Watermark"]
    assert not reloaded.layers.resolve("View", zoom=1.0)["Detail"]
    assert [config.name for config in reloaded.layers.configurations] == [""]


def test_setting_usage_on_a_missing_group_raises():
    from aspose_pdf.engine.optional_content import set_usage

    document = Document()
    document.pages.add()
    document.layers.add("Body")
    with pytest.raises(PdfValidationException):
        set_usage(document._engine_pdf, 99999, printing=False)


def test_setting_usage_twice_reuses_the_one_application_entry():
    document = Document()
    document.pages.add()
    layer = document.layers.add("Watermark")
    layer.set_usage(printing=False)
    document.layers["Watermark"].set_usage(printing=True)

    entries = document._engine_pdf._resolve(
        _default_config(document).mapping.get(PdfName("AS"))
    )
    print_entries = [
        entry
        for entry in entries.items
        if entry.mapping[PdfName("Event")].name.lstrip("/") == "Print"
    ]
    assert len(print_entries) == 1
    assert len(print_entries[0].mapping[PdfName("OCGs")].items) == 1
    assert document.layers.resolve("Print")["Watermark"]


def test_a_view_usage_reaches_the_renderer_and_the_extractor(tmp_path: Path):
    """The View event is not a separate mode -- it is what everything resolves."""
    document = Document()
    page = document.pages.add()
    page.add_text("Body text", 60, 700, font_size=14)
    hidden = document.layers.add("Screen-hidden")
    with page.layer(hidden):
        page.add_text("NOT ON SCREEN", 60, 500, font_size=30)

    before = _ink(page.render())
    assert "NOT ON SCREEN" in _extracted(document, tmp_path / "before.pdf")

    document.layers["Screen-hidden"].set_usage(view=False)

    assert _ink(document.pages[0].render()) < before
    after = _extracted(document, tmp_path / "after.pdf")
    assert "NOT ON SCREEN" not in after
    assert "Body text" in after


def _extracted(document: Document, target: Path) -> str:
    from aspose_pdf.facades import PdfExtractor

    document.save(str(target))
    extractor = PdfExtractor()
    extractor.bind_pdf(str(target))
    extractor.extract_text()
    return extractor.get_text()


def _ink(raster) -> int:
    pixels = raster.pixels
    return sum(1 for i in range(0, len(pixels), 3) if pixels[i] < 250)


# ---------------------------------------------------------------------------
# Removing a layer has to reach into both
# ---------------------------------------------------------------------------


def test_removing_a_layer_drops_it_from_the_usage_applications():
    document = _document_with_usage()
    document.layers.add("Keeper").set_usage(printing=False)

    document.layers.remove("Watermark")

    entries = document._engine_pdf._resolve(
        _default_config(document).mapping.get(PdfName("AS"))
    )
    remaining = {
        reference.object_number
        for entry in entries.items
        for reference in entry.mapping[PdfName("OCGs")].items
    }
    assert document.layers["Keeper"].object_number in remaining
    assert len(remaining) == 1


def test_an_application_entry_left_governing_nothing_is_dropped():
    document = _document_with_usage()

    document.layers.remove("Watermark")

    assert PdfName("AS") not in _default_config(document).mapping


def test_removing_a_layer_purges_it_from_alternate_configurations():
    document = Document()
    document.pages.add()
    document.layers.add("Body")
    document.layers.add("Notes")
    document.layers["Notes"].visible = False
    document.layers.save_configuration("Without notes")
    gone = document.layers["Notes"].object_number

    document.layers.remove("Notes")

    engine = document._engine_pdf
    configs = engine._resolve(_oc_properties(document).mapping.get(PdfName("Configs")))
    alternate = engine._resolve(configs.items[0])
    off = engine._resolve(alternate.mapping.get(PdfName("OFF")))
    assert all(item.object_number != gone for item in off.items)


def test_a_number_typed_zoom_range_round_trips_as_numbers():
    document = Document()
    document.pages.add()
    document.layers.add("Detail").set_usage(zoom=(2, None))

    engine = document._engine_pdf
    group = engine._resolve(_reference(document.layers["Detail"]))
    usage = engine._resolve(group.mapping.get(PdfName("Usage")))
    zoom = engine._resolve(usage.mapping.get(PdfName("Zoom")))
    assert isinstance(zoom.mapping[PdfName("min")], PdfNumber)
    assert PdfName("max") not in zoom.mapping
    assert document.layers.resolve("View", zoom=99.0)["Detail"]
    assert not document.layers.resolve("View", zoom=1.0)["Detail"]

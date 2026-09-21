"""Layers whose states exclude one another (``/RBGroups``).

``/RBGroups`` names "collections of optional content groups whose states are
intended to be mutually exclusive: if one of the ... groups in such an array is
turned ON, all the others shall be turned OFF" (ISO 32000-1 table 101) -- the
radio buttons of a viewer's layers panel. Switching a layer here ignored the
entry, so a document could end up showing two layers that are meant to exclude
each other, and a file written after such a switch said so.

pdf.js (``OptionalContentConfig.setVisibility``) and MuPDF
(``set_layer_ui_config``) both switch them this way, and both read the files
these tests write as radio groups: turning one on turns the rest off, turning
one off leaves the rest alone.
"""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.cos import PdfArray, PdfIndirectReference, PdfName
from aspose_pdf.engine.optional_content import set_radio_group
from aspose_pdf.exceptions import PdfValidationException


def _reloaded(document: Document) -> Document:
    buffer = io.BytesIO()
    document.save(buffer)
    return Document(io.BytesIO(buffer.getvalue()))


def _states(document: Document) -> dict[str, bool]:
    return {layer.name: layer.visible for layer in document.layers}


def _three(**visible: bool) -> Document:
    document = Document()
    document.pages.add()
    for name in ("Alpha", "Beta", "Gamma"):
        document.layers.add(name, visible=visible.get(name, False))
    return document


def _reference_numbers(engine, array) -> list[int]:
    return [item.object_number for item in engine._resolve(array).items]


def _config(document: Document):
    engine = document._engine_pdf
    catalog = engine._resolve(engine._cos_doc.trailer.mapping[PdfName("Root")])
    properties = engine._resolve(catalog.mapping[PdfName("OCProperties")])
    return engine, engine._resolve(properties.mapping[PdfName("D")])


def test_turning_one_on_turns_the_others_off():
    document = _three(Alpha=True)
    document.layers.make_exclusive(["Alpha", "Beta", "Gamma"])
    document.layers["Beta"].visible = True
    assert _states(document) == {"Alpha": False, "Beta": True, "Gamma": False}
    # And that is what the file says: MuPDF and pdf.js read it the same way.
    reopened = _reloaded(document)
    assert _states(reopened) == {"Alpha": False, "Beta": True, "Gamma": False}
    assert [[layer.name for layer in group] for group in reopened.layers.exclusive_groups] == [
        ["Alpha", "Beta", "Gamma"]
    ]
    reopened.layers["Gamma"].visible = True
    assert _states(reopened) == {"Alpha": False, "Beta": False, "Gamma": True}


def test_turning_one_off_leaves_the_others_alone():
    # A file from another producer may show two members of one group at once;
    # switching one off selects nothing, it does not switch off the rest.
    document = _three(Alpha=True, Beta=True)
    set_radio_group(document._engine_pdf, [layer.object_number for layer in document.layers[:2]])
    document = _reloaded(document)
    assert [layer.name for layer in document.layers.exclusive_groups[0]] == ["Alpha", "Beta"]

    document.layers["Alpha"].visible = False
    assert _states(document) == {"Alpha": False, "Beta": True, "Gamma": False}
    document.layers["Beta"].visible = False
    assert _states(document) == {"Alpha": False, "Beta": False, "Gamma": False}


def test_a_layer_in_two_groups_switches_both():
    document = _three(Alpha=True, Gamma=True)
    document.layers.make_exclusive(["Alpha", "Beta"])
    document.layers.make_exclusive(["Beta", "Gamma"])
    document.layers["Beta"].visible = True
    assert _states(document) == {"Alpha": False, "Beta": True, "Gamma": False}


def test_a_group_is_left_showing_at_most_one_of_its_layers():
    document = _three(Alpha=True, Beta=True, Gamma=True)
    layers = document.layers.make_exclusive(["Beta", "Gamma"])
    assert [layer.name for layer in layers] == ["Beta", "Gamma"]
    # Alpha is not in the group and keeps its state; the group keeps its first.
    assert _states(document) == {"Alpha": True, "Beta": True, "Gamma": False}


def test_a_group_is_written_once():
    document = _three(Alpha=True)
    document.layers.make_exclusive(["Alpha", "Beta"])
    document.layers.make_exclusive(["Beta", "Alpha"])  # the same collection
    engine, config = _config(document)
    arrays = engine._resolve(config.mapping[PdfName("RBGroups")])
    assert isinstance(arrays, PdfArray) and len(arrays.items) == 1
    assert len(_reloaded(document).layers.exclusive_groups) == 1


def test_a_removed_layer_leaves_no_group_naming_it():
    document = _three(Alpha=True)
    document.layers.make_exclusive(["Alpha", "Beta"])
    document.layers.remove("Beta")
    engine, config = _config(document)
    [remaining] = engine._resolve(config.mapping[PdfName("RBGroups")]).items
    assert _reference_numbers(engine, remaining) == [document.layers["Alpha"].object_number]
    # One member left is no longer a group to switch within.
    assert _reloaded(document).layers.exclusive_groups == ()

    # And with nobody left, the entry goes rather than staying as an empty array.
    document.layers.remove("Alpha")
    _engine, config = _config(document)
    assert PdfName("RBGroups") not in config.mapping


def test_a_group_needs_two_layers_of_this_document():
    document = _three(Alpha=True)
    other = _three(Alpha=True)
    with pytest.raises(PdfValidationException, match="at least two"):
        document.layers.make_exclusive(["Alpha"])
    with pytest.raises(PdfValidationException, match="at least two"):
        document.layers.make_exclusive(["Alpha", "Alpha"])
    with pytest.raises(PdfValidationException, match="this document"):
        document.layers.make_exclusive([document.layers["Alpha"], other.layers["Beta"]])
    with pytest.raises(KeyError):
        document.layers.make_exclusive(["Alpha", "Nosuch"])


def test_layers_already_in_hand_report_the_switch():
    document = _three(Alpha=True)
    layers = document.layers
    layers.make_exclusive(["Alpha", "Beta"])
    layers = document.layers
    alpha, beta = layers["Alpha"], layers["Beta"]
    beta.visible = True
    # The same objects, not a collection read again: both say what they show now.
    assert (alpha.visible, beta.visible) == (False, True)


def test_a_group_naming_something_that_is_not_a_layer_keeps_the_rest():
    # A dangling reference from another tool: the members it does name still
    # exclude one another, and reading the group does not trip over it.
    document = _three(Alpha=True)
    document.layers.make_exclusive(["Alpha", "Beta"])
    engine, config = _config(document)
    [members] = engine._resolve(config.mapping[PdfName("RBGroups")]).items
    members.append(PdfIndirectReference(9999, 0))
    document = _reloaded(document)

    assert [layer.name for layer in document.layers.exclusive_groups[0]] == ["Alpha", "Beta"]
    document.layers["Beta"].visible = True
    assert _states(document) == {"Alpha": False, "Beta": True, "Gamma": False}


def test_a_group_read_from_a_file_switches_too():
    # What another producer writes: the entry is read back from /D /RBGroups.
    document = _three(Alpha=True)
    document.layers.make_exclusive(["Alpha", "Beta", "Gamma"])
    reopened = _reloaded(document)
    assert [layer.name for layer in reopened.layers.exclusive_groups[0]] == [
        "Alpha",
        "Beta",
        "Gamma",
    ]
    reopened.layers["Gamma"].visible = True
    assert _states(_reloaded(reopened)) == {"Alpha": False, "Beta": False, "Gamma": True}


def test_an_array_that_names_fewer_than_two_groups_is_no_group():
    document = _three(Alpha=True)
    engine, config = _config(document)
    first = engine._resolve(config.mapping[PdfName("Order")]).items[0]
    config.mapping[PdfName("RBGroups")] = PdfArray([PdfArray([first]), PdfArray([])])
    reopened = _reloaded(document)
    assert reopened.layers.exclusive_groups == ()
    reopened.layers["Beta"].visible = True
    assert _states(reopened)["Alpha"] is True

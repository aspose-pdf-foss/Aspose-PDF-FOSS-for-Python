"""How a form field looks, and what a document, a page or a field *does*.

Two holes, both on the authoring side. A field could be created but not styled:
``/MK`` was written with a hardcoded black border and a white (or light grey)
background, ``/BS`` with a hardcoded width of 1, and the ``/DA`` colour was always
black -- while the appearance generator had read ``/MK /BG`` and ``/MK /BC`` all
along, so the drawing side was waiting for entries nothing could set. And there
were **no actions anywhere**: not a field's ``/AA`` (the keystroke, format,
validate and calculate scripts that make a form compute), not a page's ``/AA``,
not the catalog's, and not the document-level scripts of ``/Names /JavaScript``
where the functions those call are defined. ``JavaScriptAction`` existed with
nowhere to put one but a link.

Where each entry belongs is the thing to get right, and qpdf 12.4.2 confirms it:
``/K``, ``/F``, ``/V`` and ``/C`` on the **field** (a viewer looks there for the
handling of a value), the mouse and focus triggers on the **widget**, ``/AA /O``
and ``/C`` on the page, ``/AA /WC``-``/DP`` on the catalog, and the scripts in a
name tree a viewer runs in key order.
"""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document, PageSize
from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.interactive import JavaScriptAction, URIAction


def _form():
    document = Document()
    page = document.pages.add(PageSize.A5)
    return document, page, document.form


def _roundtrip(document: Document) -> Document:
    buffer = io.BytesIO()
    document.save(buffer)
    buffer.seek(0)
    return Document(buffer)


def _saved(document: Document) -> bytes:
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Appearance characteristics
# ---------------------------------------------------------------------------


def test_a_field_is_created_with_the_look_it_was_asked_for():
    _document, page, form = _form()
    field = form.add_text_field(
        "amount",
        page,
        (50, 500, 250, 525),
        border_color=(0, 0, 0.4),
        background_color="#eef3f8",
        border_width=2,
        border_style="dashed",
        text_color="#003366",
        font_size=11,
    )
    assert field.border_color == (0.0, 0.0, 0.4)
    assert field.background_color == pytest.approx((0.933333, 0.952941, 0.972549), abs=1e-5)
    assert field.border_width == 2.0
    assert field.border_style == "D"
    assert field.text_color == pytest.approx((0.0, 0.2, 0.4), abs=1e-5)
    assert field.font_size == 11.0


def test_the_look_survives_a_round_trip():
    document, page, form = _form()
    form.add_text_field(
        "amount",
        page,
        (50, 500, 250, 525),
        border_color=(1, 0, 0),
        background_color=0.9,
        border_width=3,
        border_style="beveled",
        text_color=(0, 0, 1),
    )
    field = _roundtrip(document).form.fields[0]
    assert field.border_color == (1.0, 0.0, 0.0)
    assert field.background_color == (0.9,)
    assert (field.border_width, field.border_style) == (3.0, "B")
    assert field.text_color == (0.0, 0.0, 1.0)


def test_every_field_type_takes_the_appearance_options():
    # They used to reach the widget for a push button only.
    document, page, form = _form()
    form.add_checkbox("box", page, (50, 420, 68, 438), background_color=(1, 1, 0.85))
    form.add_radio_group("choice", page, {"a": (50, 380, 68, 398)}, background_color=0.8)
    form.add_list_box("list", page, (50, 300, 250, 360), ["one", "two"], border_width=2)
    form.add_combo_box("combo", page, (50, 250, 250, 275), ["x"], border_color=(1, 0, 0))
    form.add_push_button("go", page, (50, 200, 120, 225), caption="Go", border_width=4)
    form.add_signature_field("sig", page, (50, 120, 250, 180), border_color=(0, 0, 1))
    fields = {field.name: field for field in document.form.fields}
    assert fields["box"].background_color == (1.0, 1.0, 0.85)
    assert fields["choice"].background_color == (0.8,)
    assert fields["list"].border_width == 2.0
    assert fields["combo"].border_color == (1.0, 0.0, 0.0)
    assert fields["go"].border_width == 4.0
    assert fields["sig"].border_color == (0.0, 0.0, 1.0)


def test_the_look_can_be_changed_after_the_fact():
    document, page, form = _form()
    field = form.add_text_field("amount", page, (50, 500, 250, 525))
    field.border_color = "#cc3300"
    field.background_color = (1, 1, 0.9)
    field.border_width = 1.5
    field.border_style = "underline"
    field.text_color = 0.25
    field.font_size = 14
    field.rotation = 180
    reloaded = _roundtrip(document).form.fields[0]
    assert reloaded.border_color == pytest.approx((0.8, 0.2, 0.0))
    assert reloaded.background_color == (1.0, 1.0, 0.9)
    assert (reloaded.border_width, reloaded.border_style) == (1.5, "U")
    assert reloaded.text_color == (0.25,)
    assert reloaded.font_size == 14.0
    assert reloaded.rotation == 180


def test_setting_one_entry_leaves_the_others_alone():
    _document, page, form = _form()
    field = form.add_text_field(
        "amount", page, (50, 500, 250, 525), border_width=2, border_style="dashed"
    )
    field.background_color = (1, 1, 1)
    assert (field.border_width, field.border_style) == (2.0, "D")
    field.border_width = 4
    assert field.border_style == "D"
    assert field.background_color == (1.0, 1.0, 1.0)


def test_set_appearance_takes_several_at_once():
    _document, page, form = _form()
    field = form.add_text_field("amount", page, (50, 500, 250, 525))
    assert field.set_appearance(border_width=2, text_color=(1, 0, 0)) is field
    assert field.border_width == 2.0
    assert field.text_color == (1.0, 0.0, 0.0)
    with pytest.raises(TypeError, match="Unknown appearance options"):
        field.set_appearance(colour="red")


def test_none_removes_an_entry():
    document, page, form = _form()
    field = form.add_text_field(
        "amount", page, (50, 500, 250, 525), border_color=(0, 0, 0), background_color=0.9
    )
    field.border_color = None
    field.background_color = None
    assert field.border_color is None
    assert field.background_color is None
    assert b"/MK" not in _saved(document)


def test_a_rotation_is_a_multiple_of_ninety():
    _document, page, form = _form()
    field = form.add_text_field("amount", page, (50, 500, 250, 525))
    with pytest.raises(PdfValidationException, match="multiple of 90"):
        field.rotation = 45
    with pytest.raises(PdfValidationException, match="multiple of 90"):
        form.add_text_field("other", page, (50, 400, 250, 425), rotation=45)


def test_a_border_style_is_one_of_the_five():
    _document, page, form = _form()
    field = form.add_text_field("amount", page, (50, 500, 250, 525))
    for style, letter in (
        ("solid", "S"),
        ("dashed", "D"),
        ("beveled", "B"),
        ("inset", "I"),
        ("underline", "U"),
        ("D", "D"),
    ):
        field.border_style = style
        assert field.border_style == letter
    with pytest.raises(PdfValidationException, match="border style"):
        field.border_style = "wobbly"


def test_a_negative_border_width_is_refused():
    _document, page, form = _form()
    field = form.add_text_field("amount", page, (50, 500, 250, 525))
    with pytest.raises(PdfValidationException, match="negative"):
        field.border_width = -1
    with pytest.raises(PdfValidationException, match="negative"):
        form.add_text_field("other", page, (50, 400, 250, 425), border_width=-2)


def test_a_colour_is_read_the_way_every_colour_in_the_package_is():
    _document, page, form = _form()
    field = form.add_text_field("amount", page, (50, 500, 250, 525))
    field.border_color = (255, 128, 0)
    assert field.border_color == pytest.approx((1.0, 128 / 255, 0.0))
    field.border_color = "#f80"
    assert field.border_color == pytest.approx((1.0, 0.533333, 0.0), abs=1e-5)
    field.background_color = (0, 0.2, 1, 0.05)  # an ink
    assert field.background_color == (0.0, 0.2, 1.0, 0.05)


def test_auto_size_is_a_font_size_of_zero():
    # 12.7.3.3: /DA size 0 means the viewer (and the appearance generator) picks
    # a size that fits the widget.
    document, page, form = _form()
    field = form.add_text_field(
        "amount", page, (50, 500, 250, 530), value="123", font_size=0
    )
    assert field.font_size == 0.0
    assert b"/Helv 0 Tf" in _saved(document)
    assert document.form.generate_appearances() >= 1
    # The appearance is built, which is what "auto" has to end in.
    assert _roundtrip(document).form.fields[0].value == "123"


def test_the_background_is_actually_drawn():
    document, page, form = _form()
    form.add_text_field(
        "amount",
        page,
        (50, 500, 250, 530),
        background_color=(1, 0, 0),
        border_color=None,
    )
    document.form.generate_appearances()
    raster = _roundtrip(document).pages[0].render(dpi=72)
    # A5 is 595.28 pt tall; the middle of the widget is at y = 515.
    red, green, blue = raster.get_pixel(150, round(595.276 - 515))
    assert red > 200 and green < 80 and blue < 80


# ---------------------------------------------------------------------------
# Field actions
# ---------------------------------------------------------------------------


def test_a_field_carries_the_scripts_that_make_a_form_compute():
    _document, page, form = _form()
    amount = form.add_text_field("amount", page, (50, 500, 250, 525), value="0")
    total = form.add_text_field("total", page, (50, 460, 250, 485))
    amount.actions["validate"] = JavaScriptAction("event.rc = event.value >= 0;")
    amount.actions["format"] = JavaScriptAction("event.value = '$' + event.value;")
    amount.actions["keystroke"] = JavaScriptAction("// digits only")
    total.actions["calculate"] = JavaScriptAction("event.value = 1;")

    assert sorted(amount.actions.keys()) == ["format", "keystroke", "validate"]
    assert isinstance(amount.actions["validate"], JavaScriptAction)
    assert "event.rc" in amount.actions["validate"].script
    assert sorted(total.actions.keys()) == ["calculate"]


def test_the_value_scripts_go_on_the_field_and_the_pointer_ones_on_the_widget():
    # Where a viewer looks for each: 12.6.3, tables 195 and 196.
    document, page, form = _form()
    field = form.add_text_field("amount", page, (50, 500, 250, 525))
    field.actions["validate"] = JavaScriptAction("v")
    field.actions["mouse_up"] = JavaScriptAction("u")
    engine = document._engine_pdf
    field_dict, widgets = engine._field_and_widgets("amount")
    from aspose_pdf.engine.cos import PdfName

    assert PdfName("V") in engine._resolve(field_dict.mapping[PdfName("AA")]).mapping
    assert PdfName("U") in engine._resolve(widgets[0].mapping[PdfName("AA")]).mapping


def test_field_actions_survive_a_round_trip():
    document, page, form = _form()
    field = form.add_text_field("amount", page, (50, 500, 250, 525))
    field.actions["validate"] = JavaScriptAction("event.rc = true;")
    field.actions["mouse_down"] = JavaScriptAction("app.beep();")
    reloaded = _roundtrip(document).form.fields[0]
    assert sorted(reloaded.actions.keys()) == ["mouse_down", "validate"]
    assert "event.rc" in reloaded.actions["validate"].script


def test_a_widget_activation_action_is_in_the_same_mapping():
    document, page, form = _form()
    button = form.add_push_button("go", page, (50, 200, 120, 225), caption="Go")
    button.actions["A"] = URIAction("https://example.com")
    assert isinstance(button.actions["A"], URIAction)
    assert _roundtrip(document).form.fields[0].actions["A"].uri == "https://example.com"


def test_an_action_is_removed_by_assigning_none_or_deleting_it():
    document, page, form = _form()
    field = form.add_text_field("amount", page, (50, 500, 250, 525))
    field.actions["validate"] = JavaScriptAction("a")
    field.actions["format"] = JavaScriptAction("b")
    field.actions["validate"] = None
    assert "validate" not in field.actions
    del field.actions["format"]
    assert len(field.actions) == 0
    assert b"/AA" not in _saved(document)
    with pytest.raises(KeyError):
        del field.actions["format"]


def test_a_trigger_the_owner_does_not_have_is_refused():
    # A viewer never fires a trigger that does not belong to the owner, so the
    # mistake would otherwise be invisible.
    document, page, form = _form()
    field = form.add_text_field("amount", page, (50, 500, 250, 525))
    with pytest.raises(PdfValidationException, match="not a field trigger"):
        field.actions["will_print"] = JavaScriptAction("x")
    with pytest.raises(PdfValidationException, match="not a page trigger"):
        page.actions["validate"] = JavaScriptAction("x")
    with pytest.raises(PdfValidationException, match="not a document trigger"):
        document.actions["open"] = JavaScriptAction("x")


def test_the_standards_own_key_is_accepted_too():
    _document, page, form = _form()
    field = form.add_text_field("amount", page, (50, 500, 250, 525))
    field.actions["V"] = JavaScriptAction("x")
    assert "validate" in field.actions
    assert field.actions["V"] is not None


def test_the_collection_behaves_like_a_mapping():
    _document, page, form = _form()
    field = form.add_text_field("amount", page, (50, 500, 250, 525))
    assert len(field.actions) == 0
    assert field.actions.get("validate") is None
    assert field.actions.get("validate", "none") == "none"
    field.actions.set("validate", JavaScriptAction("x"))
    assert list(field.actions) == ["validate"]
    assert [name for name, _ in field.actions.items()] == ["validate"]
    assert len(field.actions.values()) == 1
    assert field.actions.remove("validate") is True
    assert field.actions.remove("validate") is False
    field.actions["format"] = JavaScriptAction("y")
    field.actions.clear()
    assert len(field.actions) == 0
    assert "validate" in field.actions.triggers


# ---------------------------------------------------------------------------
# Page and document actions
# ---------------------------------------------------------------------------


def test_a_page_acts_when_it_is_opened_or_closed():
    document, page, _fields = _form()
    page.actions["open"] = JavaScriptAction("app.alert('here');")
    page.actions["close"] = JavaScriptAction("app.alert('gone');")
    assert sorted(page.actions.keys()) == ["close", "open"]
    reloaded = _roundtrip(document).pages[0]
    assert sorted(reloaded.actions.keys()) == ["close", "open"]
    assert "here" in reloaded.actions["open"].script


def test_the_document_acts_around_saving_and_printing():
    document, _page, _fields = _form()
    document.actions["will_print"] = JavaScriptAction("app.alert('printing');")
    document.actions["did_save"] = JavaScriptAction("app.alert('saved');")
    assert sorted(document.actions.keys()) == ["did_save", "will_print"]
    reloaded = _roundtrip(document)
    assert sorted(reloaded.actions.keys()) == ["did_save", "will_print"]
    assert b"/AA" in _saved(document)


def test_a_page_action_is_the_pages_own():
    document, page, _fields = _form()
    second = document.pages.add(PageSize.A5)
    page.actions["open"] = JavaScriptAction("first")
    assert len(second.actions) == 0
    reloaded = _roundtrip(document)
    assert len(reloaded.pages[0].actions) == 1
    assert len(reloaded.pages[1].actions) == 0


def test_the_open_action_is_a_separate_thing():
    # 12.6.2: what happens when the document is *opened* is /OpenAction, not /AA.
    document, _page, _fields = _form()
    document.open_action = JavaScriptAction("app.alert('opened');")
    document.actions["will_close"] = JavaScriptAction("x")
    data = _saved(document)
    assert b"/OpenAction" in data
    reloaded = Document(io.BytesIO(data))
    assert isinstance(reloaded.open_action, JavaScriptAction)
    assert reloaded.actions.keys() == ["will_close"]


# ---------------------------------------------------------------------------
# Document-level JavaScript
# ---------------------------------------------------------------------------


def test_the_document_carries_the_scripts_a_form_calls():
    document, _page, _fields = _form()
    document.javascript["00_helpers"] = "function vat(x) { return x * 1.2; }"
    document.javascript["10_init"] = "console.println('ready');"
    assert document.javascript.keys() == ["00_helpers", "10_init"]
    assert "vat" in document.javascript["00_helpers"]
    reloaded = _roundtrip(document)
    assert reloaded.javascript.keys() == ["00_helpers", "10_init"]
    assert reloaded.javascript["10_init"] == "console.println('ready');"


def test_the_scripts_are_written_in_the_order_they_run():
    # 12.6.4.17: a viewer runs them in key order, so the tree has to be sorted
    # (and that is why such names are usually numbered).
    document, _page, _fields = _form()
    for name in ("30_c", "10_a", "20_b"):
        document.javascript[name] = f"var {name};"
    data = _saved(document)
    positions = [data.index(name.encode()) for name in ("10_a", "20_b", "30_c")]
    assert positions == sorted(positions)
    assert list(document.javascript) == ["10_a", "20_b", "30_c"]


def test_a_script_is_replaced_and_removed():
    document, _page, _fields = _form()
    document.javascript["a"] = "one"
    document.javascript["a"] = "two"
    assert document.javascript["a"] == "two"
    assert len(document.javascript) == 1
    assert document.javascript.remove("a") is True
    assert document.javascript.remove("a") is False
    assert len(document.javascript) == 0
    assert b"/JavaScript" not in _saved(document)
    document.javascript["b"] = "x"
    del document.javascript["b"]
    assert len(document.javascript) == 0
    with pytest.raises(KeyError):
        del document.javascript["b"]


def test_clearing_removes_every_script():
    document, _page, _fields = _form()
    document.javascript["a"] = "one"
    document.javascript["b"] = "two"
    document.javascript.clear()
    assert len(document.javascript) == 0
    assert len(_roundtrip(document).javascript) == 0


def test_a_script_is_source_text_and_its_name_is_a_latin_1_string():
    document, _page, _fields = _form()
    with pytest.raises(PdfValidationException, match="source text"):
        document.javascript["a"] = 42
    with pytest.raises(PdfValidationException, match="cannot be empty"):
        document.javascript[""] = "x"
    with pytest.raises(PdfValidationException, match=r"(?i)latin-1"):
        document.javascript["скрипт"] = "x"


def test_a_missing_script_raises_and_get_does_not():
    document, _page, _fields = _form()
    assert document.javascript.get("nope") is None
    assert document.javascript.get("nope", "dflt") == "dflt"
    with pytest.raises(KeyError):
        document.javascript["nope"]
    assert "nope" not in document.javascript


def test_the_javascript_tree_lives_beside_the_destinations_tree():
    # Both are /Names entries on the catalog, and neither may displace the other.
    document, _page, _fields = _form()
    document.destinations["cover"] = 0
    document.javascript["a"] = "x"
    reloaded = _roundtrip(document)
    assert reloaded.destinations.keys() == ["cover"]
    assert reloaded.javascript.keys() == ["a"]


def test_a_field_that_does_not_exist_cannot_be_styled():
    document, page, form = _form()
    field = form.add_text_field("amount", page, (50, 500, 250, 525))
    field.remove()
    with pytest.raises(PdfValidationException, match="does not exist"):
        document._engine_pdf.set_field_appearance("amount", border_width=1)

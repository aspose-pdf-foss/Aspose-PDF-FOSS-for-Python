"""Push-button icons (`/MK /I`) and the submit/reset form actions.

A push button could previously only show a caption, and the typed action API had
no way to express submitting or resetting a form.
"""

from __future__ import annotations

from io import BytesIO

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.cos import PdfArray, PdfDictionary, PdfName, PdfStream
from aspose_pdf.engine.jpeg_encoder import encode as encode_jpeg
from aspose_pdf.interactive import ResetFormAction, SubmitFormAction


def _icon_bytes(w: int = 24, h: int = 16) -> bytes:
    samples = bytes(((x * 9 + y * 5 + c * 40) % 256)
                    for y in range(h) for x in range(w) for c in range(3))
    return encode_jpeg(w, h, 3, samples, quality=80)


def _resolve(document: Document, value):
    return document._engine_pdf._resolve(value)


def _widget(document: Document, name: str) -> PdfDictionary:
    engine = document._engine_pdf
    root = _resolve(document, engine._cos_doc.trailer.mapping[PdfName("Root")])
    acro = _resolve(document, root.mapping[PdfName("AcroForm")])
    for ref in _resolve(document, acro.mapping[PdfName("Fields")]).items:
        field = _resolve(document, ref)
        t = _resolve(document, field.mapping.get(PdfName("T")))
        if t is not None and t.value.decode("latin-1") == name:
            kids = _resolve(document, field.mapping[PdfName("Kids")])
            return _resolve(document, kids.items[0])
    raise AssertionError(f"widget {name!r} not found")


def _button(**kwargs) -> Document:
    document = Document()
    page = document.pages.add()
    document.form.add_push_button("Go", page, (40, 600, 200, 660), **kwargs)
    return document


# --- icons -----------------------------------------------------------------
def test_icon_becomes_a_form_xobject_in_mk():
    document = _button(icon=_icon_bytes())
    mk = _resolve(document, _widget(document, "Go").mapping[PdfName("MK")])
    icon = _resolve(document, mk.mapping[PdfName("I")])

    assert isinstance(icon, PdfStream)
    # An icon must be a form XObject, not the image itself.
    assert icon.mapping[PdfName("Subtype")] == PdfName("Form")
    bbox = [n.value for n in icon.mapping[PdfName("BBox")].items]
    assert bbox == [0, 0, 24, 16]

    # The form draws an image XObject of the icon's pixel size.
    inner = _resolve(document, icon.mapping[PdfName("Resources")])
    image = _resolve(document, _resolve(
        document, inner.mapping[PdfName("XObject")]
    ).mapping[PdfName("Icon")])
    assert image.mapping[PdfName("Subtype")] == PdfName("Image")
    assert image.mapping[PdfName("Filter")] == PdfName("DCTDecode")


def _text_position(document: Document) -> int:
    mk = _resolve(document, _widget(document, "Go").mapping[PdfName("MK")])
    return int(_resolve(document, mk.mapping[PdfName("TP")]).value)


def test_caption_position_follows_whether_a_caption_is_given():
    """/MK /TP: 1 is icon only, 2 puts the caption below the icon."""
    assert _text_position(_button(icon=_icon_bytes())) == 1
    assert _text_position(_button(icon=_icon_bytes(), caption="Send")) == 2


def test_icon_fit_is_declared():
    """The /IF must describe what the baked appearance actually does."""
    document = _button(icon=_icon_bytes())
    mk = _resolve(document, _widget(document, "Go").mapping[PdfName("MK")])
    fit = _resolve(document, mk.mapping[PdfName("IF")])
    assert fit.mapping[PdfName("SW")] == PdfName("A")  # always scale
    assert fit.mapping[PdfName("S")] == PdfName("P")  # proportional
    assert [n.value for n in fit.mapping[PdfName("A")].items] == [0.5, 0.5]


def test_appearance_draws_the_icon():
    document = _button(icon=_icon_bytes())
    widget = _widget(document, "Go")
    ap = _resolve(document, widget.mapping[PdfName("AP")])
    normal = _resolve(document, ap.mapping[PdfName("N")])

    assert b"/Icon Do" in normal.content
    resources = _resolve(document, normal.mapping[PdfName("Resources")])
    xobjects = _resolve(document, resources.mapping[PdfName("XObject")])
    assert PdfName("Icon") in xobjects.mapping

    # All three faces carry the icon.
    for key in ("N", "R", "D"):
        face = _resolve(document, ap.mapping[PdfName(key)])
        assert b"/Icon Do" in face.content


def test_icon_is_scaled_proportionally_into_the_face():
    """A 24x16 icon in a 160x60 box is limited by height, not width."""
    document = _button(icon=_icon_bytes(24, 16))
    ap = _resolve(document, _widget(document, "Go").mapping[PdfName("AP")])
    content = _resolve(document, ap.mapping[PdfName("N")]).content.decode("latin-1")
    line = next(ln for ln in content.splitlines() if ln.endswith(" cm"))
    draw_w, _, _, draw_h = (float(v) for v in line.split()[:4])
    assert draw_w / draw_h == pytest.approx(24 / 16, rel=1e-3)
    assert draw_h <= 60


def test_caption_only_button_is_unchanged():
    document = _button(caption="Send")
    mk = _resolve(document, _widget(document, "Go").mapping[PdfName("MK")])
    assert PdfName("I") not in mk.mapping
    ap = _resolve(document, _widget(document, "Go").mapping[PdfName("AP")])
    assert b"/Icon Do" not in _resolve(document, ap.mapping[PdfName("N")]).content


def test_icon_survives_save_and_reload():
    document = _button(icon=_icon_bytes(), caption="Send")
    output = BytesIO()
    document.save(output)
    reloaded = Document().load_from(output.getvalue())
    mk = _resolve(reloaded, _widget(reloaded, "Go").mapping[PdfName("MK")])
    icon = _resolve(reloaded, mk.mapping[PdfName("I")])
    assert icon.mapping[PdfName("Subtype")] == PdfName("Form")


def test_invalid_icon_bytes_are_rejected():
    from aspose_pdf.exceptions import PdfValidationException

    with pytest.raises((PdfValidationException, ValueError)):
        _button(icon=b"not an image")


# --- submit / reset actions ------------------------------------------------
def _action(document: Document) -> PdfDictionary:
    return _resolve(document, _widget(document, "Go").mapping[PdfName("A")])


def test_submit_form_action_serializes():
    document = _button(action=SubmitFormAction("https://example.test/f"))
    action = _action(document)
    assert action.mapping[PdfName("S")] == PdfName("SubmitForm")
    assert action.mapping[PdfName("F")].value == b"https://example.test/f"
    assert action.mapping[PdfName("Flags")].value == 0


@pytest.mark.parametrize(
    "fmt,flag", [("fdf", 0), ("html", 4), ("xfdf", 32), ("pdf", 256)]
)
def test_submit_format_sets_its_flag(fmt, flag):
    document = _button(action=SubmitFormAction("https://x/y", submit_format=fmt))
    assert _action(document).mapping[PdfName("Flags")].value == flag


def test_submit_fields_are_written_as_an_array():
    document = _button(
        action=SubmitFormAction("https://x/y", fields=["Name", "Email"])
    )
    fields = _resolve(document, _action(document).mapping[PdfName("Fields")])
    assert isinstance(fields, PdfArray)
    assert [f.value.decode("latin-1") for f in fields.items] == ["Name", "Email"]


def test_submit_exclude_sets_the_include_exclude_bit():
    document = _button(
        action=SubmitFormAction("https://x/y", fields=["Secret"], exclude=True)
    )
    assert _action(document).mapping[PdfName("Flags")].value & 1


def test_reset_form_action_serializes():
    document = _button(action=ResetFormAction())
    action = _action(document)
    assert action.mapping[PdfName("S")] == PdfName("ResetForm")
    assert PdfName("Fields") not in action.mapping
    assert PdfName("Flags") not in action.mapping


def test_reset_form_with_excluded_fields():
    document = _button(action=ResetFormAction(fields=["Keep"], exclude=True))
    action = _action(document)
    assert action.mapping[PdfName("Flags")].value == 1
    fields = _resolve(document, action.mapping[PdfName("Fields")])
    assert [f.value.decode("latin-1") for f in fields.items] == ["Keep"]


def test_invalid_action_arguments_are_rejected():
    with pytest.raises(ValueError, match="submit_format"):
        _button(action=SubmitFormAction("u", submit_format="csv"))
    with pytest.raises(ValueError, match="fields to exclude"):
        _button(action=ResetFormAction(exclude=True))


def test_actions_survive_save_and_reload():
    document = _button(action=SubmitFormAction("https://x/y", fields=["A"]))
    output = BytesIO()
    document.save(output)
    reloaded = Document().load_from(output.getvalue())
    assert _action(reloaded).mapping[PdfName("S")] == PdfName("SubmitForm")

"""Push-button visual states: normal/rollover/down appearances and /MK colors."""

from __future__ import annotations

from aspose_pdf import Document, URIAction
from aspose_pdf.engine.cos import PdfDictionary, PdfName, PdfStream
from aspose_pdf.engine.simple_pdf import SimplePdf


def _button_widget(caption="Go", **kwargs):
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 612, 792)]
    pdf.page_contents = [b""]
    pdf._ensure_cos()
    doc = Document()
    doc._engine_pdf = pdf
    doc.form.add_push_button(
        "btn", doc.pages[0], (100, 100, 220, 140), caption=caption, **kwargs
    )
    doc.form.generate_appearances()
    engine = doc._engine_pdf
    annots = engine._resolve(
        engine._get_page_dict(0).mapping.get(PdfName("Annots"))
    )
    for ref in annots.items:
        widget = engine._resolve(ref)
        if engine._get_name(widget.mapping.get(PdfName("Subtype"))) == "Widget":
            return engine, widget
    raise AssertionError("no widget")


def test_button_has_normal_rollover_and_down_states():
    engine, widget = _button_widget()
    ap = engine._resolve(widget.mapping.get(PdfName("AP")))
    assert isinstance(ap, PdfDictionary)
    for state in ("N", "R", "D"):
        stream = engine._resolve(ap.mapping.get(PdfName(state)))
        assert isinstance(stream, PdfStream), f"missing /{state} appearance"
        assert engine._get_name(stream.mapping.get(PdfName("Subtype"))) == "Form"


def test_button_mk_border_and_background():
    engine, widget = _button_widget(
        border_color=[0.0, 0.0, 0.0], background=[0.8, 0.8, 0.9]
    )
    mk = engine._resolve(widget.mapping.get(PdfName("MK")))
    assert engine._cos_number_list(mk.mapping.get(PdfName("BC"))) == [0.0, 0.0, 0.0]
    assert engine._cos_number_list(mk.mapping.get(PdfName("BG"))) == [0.8, 0.8, 0.9]


def test_button_action_and_appearance_coexist():
    engine, widget = _button_widget(action=URIAction("https://example.com"))
    action = engine._resolve(widget.mapping.get(PdfName("A")))
    assert engine._get_name(action.mapping.get(PdfName("S"))) == "URI"
    ap = engine._resolve(widget.mapping.get(PdfName("AP")))
    assert isinstance(engine._resolve(ap.mapping.get(PdfName("N"))), PdfStream)


def test_rollover_and_down_differ_from_normal():
    # The three faces shade the background differently, so their content streams
    # are not byte-identical.
    engine, widget = _button_widget(background=[0.5, 0.5, 0.5])
    ap = engine._resolve(widget.mapping.get(PdfName("AP")))
    streams = {
        state: engine._resolve(ap.mapping.get(PdfName(state))).content
        for state in ("N", "R", "D")
    }
    assert streams["N"] != streams["R"]
    assert streams["N"] != streams["D"]
    assert streams["R"] != streams["D"]

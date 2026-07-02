"""Regeneration of AcroForm field appearance streams from field values."""

from __future__ import annotations

import io

from aspose_pdf import Document
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfBoolean,
    PdfDictionary,
    PdfDocument,
    PdfName,
    PdfNumber,
    PdfStream,
    PdfString,
)
from aspose_pdf.engine.field_appearance import (
    auto_font_size,
    build_text_appearance,
    parse_default_appearance,
    _text_width,
    _wrap_text,
)
from aspose_pdf.engine.simple_pdf import SimplePdf


# ---------------------------------------------------------------------------
# parse_default_appearance / build_text_appearance (pure unit tests)
# ---------------------------------------------------------------------------


def test_parse_default_appearance_font_size_colour():
    assert parse_default_appearance("/Helv 12 Tf 0 g") == ("Helv", 12.0, "0 g")
    assert parse_default_appearance("/Arial 0 Tf 1 0 0 rg") == (
        "Arial",
        0.0,
        "1 0 0 rg",
    )
    assert parse_default_appearance("0 0 0 1 k") == (None, 0.0, "0 0 0 1 k")
    assert parse_default_appearance("") == (None, 0.0, "0 g")


def test_auto_font_size():
    assert auto_font_size(20, multiline=False) == 12.0  # clamped to max
    assert auto_font_size(12, multiline=False) == 8.0  # height - 2*padding
    assert auto_font_size(100, multiline=True) == 10.0


def test_build_text_appearance_single_line():
    content = build_text_appearance(
        "Hello", 200, 20, font_name="Helv", font_size=12, color_op="0 0 1 rg"
    )
    assert b"/Tx BMC" in content
    assert b"BT" in content and b"ET" in content
    assert b"/Helv 12 Tf" in content
    assert b"0 0 1 rg" in content
    assert b"(Hello) Tj" in content
    assert b"EMC" in content


def test_build_text_appearance_multiline_has_two_lines():
    content = build_text_appearance(
        "a\nb", 200, 60, font_name="Helv", font_size=10, multiline=True
    )
    assert content.count(b" Tj") == 2
    assert content.count(b" Tm") == 2
    assert b"(a) Tj" in content and b"(b) Tj" in content


def test_build_text_appearance_escapes_literal():
    content = build_text_appearance("a(b)c\\d", 200, 20, font_name="Helv", font_size=10)
    assert rb"(a\(b\)c\\d) Tj" in content


def test_build_text_appearance_autosize_when_zero():
    content = build_text_appearance("x", 100, 20, font_name="Helv", font_size=0)
    assert b"/Helv 12 Tf" in content  # auto-sized to the clamped max


def test_build_text_appearance_quadding_shifts_origin():
    left = build_text_appearance(
        "word", 200, 20, font_name="Helv", font_size=12, quadding=0
    )
    right = build_text_appearance(
        "word", 200, 20, font_name="Helv", font_size=12, quadding=2
    )
    assert left != right  # right-aligned starts further along x


# ---------------------------------------------------------------------------
# Word wrapping for multiline fields
# ---------------------------------------------------------------------------


def test_wrap_text_packs_words_greedily():
    # fs 10 -> char width 6; max_width 24 -> 4 chars per line.
    assert _wrap_text("aaa bbb ccc", 24, 10) == ["aaa", "bbb", "ccc"]


def test_wrap_text_keeps_words_together_when_they_fit():
    assert _wrap_text("a b c", 240, 10) == ["a b c"]


def test_wrap_text_hard_breaks_overlong_word():
    # A 10-char word with a 4-char line is split across lines.
    assert _wrap_text("abcdefghij", 24, 10) == ["abcd", "efgh", "ij"]


def test_wrap_text_honours_explicit_newlines():
    assert _wrap_text("ab\ncd", 240, 10) == ["ab", "cd"]


def test_wrap_text_preserves_blank_paragraph():
    assert _wrap_text("a\n\nb", 240, 10) == ["a", "", "b"]


def test_wrap_text_empty_value_yields_one_line():
    assert _wrap_text("", 240, 10) == [""]


def test_build_multiline_wraps_long_text():
    content = build_text_appearance(
        "one two three four five",
        60,
        80,
        font_name="Helv",
        font_size=10,
        multiline=True,
    )
    # The long line wraps to several rows (one Tj each); no row exceeds the field.
    assert content.count(b" Tj") == 3
    assert content.count(b" Tm") == 3


def test_build_single_line_is_not_wrapped():
    content = build_text_appearance(
        "one two three four five", 60, 20, font_name="Helv", font_size=10
    )
    assert content.count(b" Tj") == 1  # single-line fields never wrap


# ---------------------------------------------------------------------------
# Glyph-metric text measurement (width_fn)
# ---------------------------------------------------------------------------


def _narrow_wide_metric(code: int) -> float:
    """Toy metric: 'i' is 250/1000 units wide, everything else 1000/1000."""
    return 250.0 if chr(code) == "i" else 1000.0


def test_text_width_uses_width_fn():
    assert _text_width("ii", 10, _narrow_wide_metric) == 5.0
    assert _text_width("WW", 10, _narrow_wide_metric) == 20.0
    assert _text_width("WW", 10) == 12.0  # flat 0.6 em fallback


def test_wrap_text_packs_more_narrow_glyphs_with_width_fn():
    # 30pt line at fs 10: twelve 'i' (2.5pt each) fit, but only three 'W' (10pt).
    assert _wrap_text("iiiiiiiiiiii WWW W", 30, 10, _narrow_wide_metric) == [
        "iiiiiiiiiiii",
        "WWW",
        "W",
    ]


def test_wrap_text_hard_breaks_by_measured_width():
    # 'W' is 10pt at fs 10 -> only two fit in a 25pt line.
    assert _wrap_text("WWWWW", 25, 10, _narrow_wide_metric) == ["WW", "WW", "W"]


def test_quadding_centres_by_glyph_metrics():
    def tm_x(content: bytes) -> float:
        for line in content.split(b"\n"):
            if line.endswith(b" Tm"):
                return float(line.split()[4])
        raise AssertionError("no Tm in content")

    flat = build_text_appearance(
        "iiii", 200, 20, font_name="Helv", font_size=10, quadding=1
    )
    metric = build_text_appearance(
        "iiii",
        200,
        20,
        font_name="Helv",
        font_size=10,
        quadding=1,
        width_fn=_narrow_wide_metric,
    )
    # Narrow glyphs measure 2.5pt each vs the flat 6pt -> centring shifts right.
    assert tm_x(metric) > tm_x(flat)


def test_substitute_width_fn_resolves_real_metrics():
    from aspose_pdf.engine.text_metrics import substitute_width_fn

    fn = substitute_width_fn("Helvetica")
    assert fn is not None
    assert fn(ord("i")) < fn(ord("W"))  # real (Liberation) advances, not flat
    assert substitute_width_fn("Symbol") is None  # no substitute -> flat fallback


# ---------------------------------------------------------------------------
# Engine: COS-level field appearance generation
# ---------------------------------------------------------------------------


def _engine_with_acroform(field: PdfDictionary, *, dr=None, da="/Helv 0 Tf 0 g"):
    """Wrap *field* in an AcroForm COS document and return (engine, field, acroform)."""
    doc = PdfDocument()
    field_ref = doc.register_object(field)
    acro_map = {
        PdfName("Fields"): PdfArray([field_ref]),
        PdfName("DA"): PdfString(da.encode()),
    }
    if dr is not None:
        acro_map[PdfName("DR")] = dr
    acro = PdfDictionary(acro_map)
    root = PdfDictionary(
        {
            PdfName("Type"): PdfName("Catalog"),
            PdfName("AcroForm"): doc.register_object(acro),
        }
    )
    doc.trailer = PdfDictionary({PdfName("Root"): doc.register_object(root)})
    engine = SimplePdf()
    engine._cos_doc = doc
    return engine, field, acro


def _text_widget(value: str, *, da: str = "/Helv 12 Tf 0 0 1 rg") -> PdfDictionary:
    return PdfDictionary(
        {
            PdfName("Type"): PdfName("Annot"),
            PdfName("Subtype"): PdfName("Widget"),
            PdfName("FT"): PdfName("Tx"),
            PdfName("T"): PdfString(b"field1"),
            PdfName("Rect"): PdfArray(
                [PdfNumber(100), PdfNumber(700), PdfNumber(300), PdfNumber(720)]
            ),
            PdfName("DA"): PdfString(da.encode()),
            PdfName("V"): PdfString(value.encode()),
        }
    )


def _ap_content(engine: SimplePdf, field: PdfDictionary) -> bytes:
    ap = engine._resolve(field.mapping[PdfName("AP")])
    n = engine._resolve(ap.mapping[PdfName("N")])
    assert isinstance(n, PdfStream)
    return n.content


def test_text_field_appearance_uses_value_and_da_colour():
    engine, field, acro = _engine_with_acroform(_text_widget("Hello"))
    assert engine.generate_field_appearances() == 1
    content = _ap_content(engine, field)
    assert b"(Hello) Tj" in content
    assert b"/Helv 12 Tf" in content
    assert b"0 0 1 rg" in content
    # NeedAppearances is cleared so viewers honour the generated appearance.
    assert isinstance(acro.mapping[PdfName("NeedAppearances")], PdfBoolean)
    assert acro.mapping[PdfName("NeedAppearances")].value is False


def test_text_field_synthesizes_helvetica_when_dr_missing():
    engine, field, acro = _engine_with_acroform(_text_widget("Hi"))
    assert engine.generate_field_appearances() == 1
    # The appearance form references a /Helv font resource...
    ap = engine._resolve(field.mapping[PdfName("AP")])
    form = engine._resolve(ap.mapping[PdfName("N")])
    res = engine._resolve(form.mapping[PdfName("Resources")])
    fonts = engine._resolve(res.mapping[PdfName("Font")])
    assert PdfName("Helv") in fonts.mapping
    # ...and it was cached into the AcroForm /DR for reuse.
    dr = engine._resolve(acro.mapping[PdfName("DR")])
    dr_fonts = engine._resolve(dr.mapping[PdfName("Font")])
    assert PdfName("Helv") in dr_fonts.mapping


def test_text_field_reuses_existing_dr_font():
    font = PdfDictionary(
        {
            PdfName("Type"): PdfName("Font"),
            PdfName("Subtype"): PdfName("Type1"),
            PdfName("BaseFont"): PdfName("Helvetica"),
        }
    )
    # Build doc first so the font can be a registered indirect reference.
    doc = PdfDocument()
    font_ref = doc.register_object(font)
    dr = PdfDictionary({PdfName("Font"): PdfDictionary({PdfName("Helv"): font_ref})})
    field = _text_widget("Hi", da="/Helv 10 Tf 0 g")
    field_ref = doc.register_object(field)
    acro = PdfDictionary(
        {PdfName("Fields"): PdfArray([field_ref]), PdfName("DR"): dr}
    )
    root = PdfDictionary(
        {
            PdfName("Type"): PdfName("Catalog"),
            PdfName("AcroForm"): doc.register_object(acro),
        }
    )
    doc.trailer = PdfDictionary({PdfName("Root"): doc.register_object(root)})
    engine = SimplePdf()
    engine._cos_doc = doc

    assert engine.generate_field_appearances() == 1
    ap = engine._resolve(field.mapping[PdfName("AP")])
    form = engine._resolve(ap.mapping[PdfName("N")])
    fonts = engine._resolve(
        engine._resolve(form.mapping[PdfName("Resources")]).mapping[PdfName("Font")]
    )
    assert fonts.mapping[PdfName("Helv")] is font_ref  # the same object, not a new font


def test_multiline_text_field_breaks_lines():
    widget = _text_widget("line1\nline2")
    widget.mapping[PdfName("Ff")] = PdfNumber(1 << 12)  # multiline flag
    engine, field, _ = _engine_with_acroform(widget)
    assert engine.generate_field_appearances() == 1
    content = _ap_content(engine, field)
    assert b"(line1) Tj" in content and b"(line2) Tj" in content


def test_multiline_text_field_word_wraps_long_value():
    # A long single-paragraph value (no explicit newlines) in a 200-wide,
    # 12pt field wraps into multiple rows.
    widget = _text_widget("the quick brown fox jumps over the lazy dog")
    widget.mapping[PdfName("Ff")] = PdfNumber(1 << 12)  # multiline flag
    engine, field, _ = _engine_with_acroform(widget)
    assert engine.generate_field_appearances() == 1
    content = _ap_content(engine, field)
    assert content.count(b" Tj") > 1  # wrapped across rows despite no newline


def test_centred_field_uses_real_helvetica_metrics():
    def centred_x(value: str) -> float:
        widget = _text_widget(value)
        widget.mapping[PdfName("Q")] = PdfNumber(1)  # centred
        engine, field, _ = _engine_with_acroform(widget)
        assert engine.generate_field_appearances() == 1
        for line in _ap_content(engine, field).split(b"\n"):
            if line.endswith(b" Tm"):
                return float(line.split()[4])
        raise AssertionError("no Tm in content")

    # 'iiii' is far narrower than 'WWWW' in real Helvetica metrics, so its
    # centred origin sits further right; the flat estimate would centre both
    # at the same x.
    assert centred_x("iiii") > centred_x("WWWW") + 10.0


def test_choice_field_renders_selected_value():
    widget = PdfDictionary(
        {
            PdfName("Subtype"): PdfName("Widget"),
            PdfName("FT"): PdfName("Ch"),
            PdfName("T"): PdfString(b"choice"),
            PdfName("Ff"): PdfNumber(1 << 18),  # combo
            PdfName("Rect"): PdfArray(
                [PdfNumber(0), PdfNumber(0), PdfNumber(120), PdfNumber(18)]
            ),
            PdfName("V"): PdfString(b"Option B"),
        }
    )
    engine, field, _ = _engine_with_acroform(widget)
    assert engine.generate_field_appearances() == 1
    assert b"(Option B) Tj" in _ap_content(engine, field)


def _checkbox(value_name: str, *, with_states: bool = True) -> PdfDictionary:
    widget = PdfDictionary(
        {
            PdfName("Subtype"): PdfName("Widget"),
            PdfName("FT"): PdfName("Btn"),
            PdfName("T"): PdfString(b"cb"),
            PdfName("Rect"): PdfArray(
                [PdfNumber(0), PdfNumber(0), PdfNumber(12), PdfNumber(12)]
            ),
            PdfName("V"): PdfName(value_name),
            PdfName("AS"): PdfName("Off"),
        }
    )
    if with_states:
        n = PdfDictionary(
            {
                PdfName("Yes"): PdfStream(mapping={}, content=b""),
                PdfName("Off"): PdfStream(mapping={}, content=b""),
            }
        )
        widget.mapping[PdfName("AP")] = PdfDictionary({PdfName("N"): n})
    return widget


def test_checkbox_checked_sets_as_to_on_state():
    engine, field, _ = _engine_with_acroform(_checkbox("Yes"))
    assert engine.generate_field_appearances() == 1
    assert field.mapping[PdfName("AS")] == PdfName("Yes")


def test_checkbox_unchecked_sets_as_off():
    engine, field, _ = _engine_with_acroform(_checkbox("Off"))
    assert engine.generate_field_appearances() == 1
    assert field.mapping[PdfName("AS")] == PdfName("Off")


def test_radio_selects_matching_kid_widget():
    def _kid(state: str) -> PdfDictionary:
        n = PdfDictionary(
            {
                PdfName(state): PdfStream(mapping={}, content=b""),
                PdfName("Off"): PdfStream(mapping={}, content=b""),
            }
        )
        return PdfDictionary(
            {
                PdfName("Subtype"): PdfName("Widget"),
                PdfName("Rect"): PdfArray(
                    [PdfNumber(0), PdfNumber(0), PdfNumber(12), PdfNumber(12)]
                ),
                PdfName("AP"): PdfDictionary({PdfName("N"): n}),
                PdfName("AS"): PdfName("Off"),
            }
        )

    doc = PdfDocument()
    kid1 = _kid("Opt1")
    kid2 = _kid("Opt2")
    field = PdfDictionary(
        {
            PdfName("FT"): PdfName("Btn"),
            PdfName("T"): PdfString(b"radio"),
            PdfName("Ff"): PdfNumber(1 << 15),  # radio
            PdfName("V"): PdfName("Opt2"),
            PdfName("Kids"): PdfArray(
                [doc.register_object(kid1), doc.register_object(kid2)]
            ),
        }
    )
    acro = PdfDictionary({PdfName("Fields"): PdfArray([doc.register_object(field)])})
    root = PdfDictionary(
        {
            PdfName("Type"): PdfName("Catalog"),
            PdfName("AcroForm"): doc.register_object(acro),
        }
    )
    doc.trailer = PdfDictionary({PdfName("Root"): doc.register_object(root)})
    engine = SimplePdf()
    engine._cos_doc = doc

    assert engine.generate_field_appearances() == 2
    assert kid1.mapping[PdfName("AS")] == PdfName("Off")
    assert kid2.mapping[PdfName("AS")] == PdfName("Opt2")


def test_no_acroform_returns_zero():
    assert SimplePdf().generate_field_appearances() == 0


# ---------------------------------------------------------------------------
# Public API + end-to-end through a real PDF
# ---------------------------------------------------------------------------


def _assemble_pdf(parts):
    header = b"%PDF-1.7\n"
    body = bytearray(header)
    offsets = {}
    max_obj = max(num for num, _ in parts)
    for obj_num, obj_body in sorted(parts, key=lambda x: x[0]):
        offsets[obj_num] = len(body)
        body.extend(f"{obj_num} 0 obj\n".encode())
        body.extend(obj_body)
        body.extend(b"\nendobj\n")
    xref_offset = len(body)
    body.extend(b"xref\n")
    body.extend(f"0 {max_obj + 1}\n".encode())
    body.extend(b"0000000000 65535 f \n")
    for i in range(1, max_obj + 1):
        body.extend(f"{offsets[i]:010d} 00000 n \n".encode())
    body.extend(b"trailer\n")
    body.extend(f"<< /Size {max_obj + 1} /Root 1 0 R >>\n".encode())
    body.extend(b"startxref\n")
    body.extend(f"{xref_offset}\n".encode())
    body.extend(b"%%EOF")
    return bytes(body)


def _form_pdf_bytes(value=b"Hello") -> bytes:
    obj1 = b"<< /Type /Catalog /Pages 2 0 R /AcroForm 4 0 R >>"
    obj2 = b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"
    obj3 = (
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Annots [5 0 R] /Resources << >> /Contents 7 0 R >>"
    )
    obj4 = (
        b"<< /Fields [5 0 R] /DA (/Helv 0 Tf 0 g) "
        b"/DR << /Font << /Helv 6 0 R >> >> /NeedAppearances true >>"
    )
    obj5 = (
        b"<< /Type /Annot /Subtype /Widget /FT /Tx /T (name) "
        b"/Rect [100 700 300 720] /DA (/Helv 12 Tf 0 g) /V (" + value + b") /P 3 0 R >>"
    )
    obj6 = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    obj7 = b"<< /Length 0 >>\nstream\n\nendstream"
    return _assemble_pdf(
        [(1, obj1), (2, obj2), (3, obj3), (4, obj4), (5, obj5), (6, obj6), (7, obj7)]
    )


def test_document_generate_field_appearances_end_to_end():
    doc = Document()
    doc.load_from(_form_pdf_bytes(b"Hello"))
    assert doc.generate_field_appearances() == 1
    # The widget (a page annotation) now carries an appearance with the value.
    annot = doc._engine_pdf.get_annotations(0)[0]
    assert annot["has_AP"] is True
    assert b"(Hello) Tj" in annot["AP_N"]


def test_form_generate_appearances_delegates():
    doc = Document()
    doc.load_from(_form_pdf_bytes(b"World"))
    assert doc.form.generate_appearances() == 1
    assert b"(World) Tj" in doc._engine_pdf.get_annotations(0)[0]["AP_N"]


def test_generated_field_appearance_survives_save_load():
    doc = Document()
    doc.load_from(_form_pdf_bytes(b"Persisted"))
    doc.generate_field_appearances()
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    reopened = Document()
    reopened.load_from(buf)
    assert reopened.pages[0].annotations[0].has_appearance


def test_flatten_inlines_generated_field_appearance():
    doc = Document()
    doc.load_from(_form_pdf_bytes(b"Hello"))
    before = doc._engine_pdf.page_contents[0]
    doc.flatten()
    after = doc._engine_pdf.page_contents[0]
    assert len(after) > len(before)
    assert b"Do" in after
    assert len(doc._engine_pdf.get_annotations(0)) == 0

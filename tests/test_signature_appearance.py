"""What a visible signature looks like, and that the signature covers it.

A signature field is invisible until something is drawn in it. Nothing in the
signature dictionary says what that should be (ISO 32000-1 12.7.4.5): the
appearance is an ordinary annotation appearance stream, and until now this
library wrote only the empty box ``Form.add_signature_field`` authors --
``Document.sign`` had no way to say who signed, when, or why.

``SignatureAppearance`` says it, and these tests cover the three things that
can go wrong with that: the **description** (an argument that cannot mean
anything is refused at the call, not halfway through a save), the **layout**
(the text fits the box, the image keeps its shape, and a background image goes
*behind* the text rather than over it), and the **binding** -- the appearance
is written before the document is serialized, so the signature covers it and a
single edited byte in it breaks the signature.

Fonts and images used here are synthesised or bundled, so nothing depends on
what the machine running the suite has installed.
"""

from __future__ import annotations

import io
import struct
import zlib
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from aspose_pdf import Document, PageSize, SignatureAppearance
from aspose_pdf.engine import signature_appearance as layout
from aspose_pdf.engine.cos import PdfName, PdfStream
from aspose_pdf.exceptions import PdfValidationException

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _signer(common_name: str = "Jordan Avery", organization: str | None = None):
    """A self-signed certificate and its key, named as a person would be."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    attributes = []
    if common_name:
        attributes.append(x509.NameAttribute(NameOID.COMMON_NAME, common_name))
    if organization:
        attributes.append(
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, organization)
        )
    name = x509.Name(attributes)
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(7)
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    return certificate, key


@pytest.fixture(scope="module")
def signer():
    return _signer()


def _png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """A small PNG: a coloured diagonal on white, so a squash is visible."""
    rows = b""
    for y in range(height):
        row = b"\x00"
        for x in range(width):
            on = abs(x * height - y * width) < max(width, height)
            row += bytes(rgb) if on else b"\xff\xff\xff"
        rows += row

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def _cyrillic_face() -> bytes:
    """A TrueType face covering Cyrillic and ASCII, built here.

    The substitute faces shipped with the package are Latin subsets, so a test
    about a Cyrillic name cannot use one -- and a font the machine happens to
    have installed would make the suite depend on the machine.
    """
    fontTools = pytest.importorskip("fontTools")
    assert fontTools
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    scalars = [*range(0x20, 0x7F), *range(0x0410, 0x0450)]
    names = {scalar: f"u{scalar:04X}" for scalar in scalars}
    pen = TTGlyphPen(None)
    pen.moveTo((80, 0))
    pen.lineTo((720, 0))
    pen.lineTo((720, 700))
    pen.lineTo((80, 700))
    pen.closePath()
    box = pen.glyph()

    builder = FontBuilder(unitsPerEm=1000, isTTF=True)
    builder.setupGlyphOrder([".notdef", *names.values()])
    builder.setupCharacterMap(names)
    builder.setupGlyf({".notdef": box, **{name: box for name in names.values()}})
    builder.setupHorizontalMetrics(
        {name: (800, 80) for name in (".notdef", *names.values())}
    )
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": "Test Cyrillic", "styleName": "Regular"})
    builder.setupOS2(sTypoAscender=800, sTypoDescender=-200)
    builder.setupPost()
    buffer = io.BytesIO()
    builder.save(buffer)
    return buffer.getvalue()


def _document(rect=(50, 600, 340, 690), *, field="Approval", pages=1) -> Document:
    """A document with one signature field waiting to be signed."""
    doc = Document()
    page = None
    for _ in range(pages):
        page = doc.pages.add(PageSize.A4)
    if rect is not None:
        doc.form.add_signature_field(field, page, list(rect))
    return doc


def _signed(doc: Document) -> bytes:
    buffer = io.BytesIO()
    doc.save(buffer)
    doc.close()
    return buffer.getvalue()


def _sign(appearance, *, signer, rect=(50, 600, 340, 690), **kwargs) -> bytes:
    certificate, key = signer
    doc = _document(rect)
    doc.sign(
        "Approval" if rect is not None else None,
        certificate=certificate,
        private_key=key,
        reason=kwargs.pop("reason", "Approved"),
        location=kwargs.pop("location", "Prague"),
        appearance=appearance,
        **kwargs,
    )
    return _signed(doc)


def _widget(data: bytes, page_index: int = 0, index: int = 0):
    """``(engine, widget dictionary)`` for one widget of a saved document."""
    doc = Document(data)
    engine = doc._engine_pdf
    annots = engine._resolve(
        engine._get_page_dict(page_index).get(PdfName("Annots"))
    )
    widget = engine._resolve(annots.items[index])
    return doc, engine, widget


def _appearance_stream(data: bytes, page_index: int = 0, index: int = 0):
    """``(document, /N stream)`` -- the document is returned so it stays open."""
    doc, engine, widget = _widget(data, page_index, index)
    ap = engine._resolve(widget.mapping.get(PdfName("AP")))
    assert ap is not None, "the widget has no /AP"
    return doc, engine, engine._resolve(ap.mapping.get(PdfName("N")))


def _text_of(stream: PdfStream) -> str:
    return bytes(stream.content).decode("latin-1")


# ---------------------------------------------------------------------------
# The description refuses what cannot mean anything
# ---------------------------------------------------------------------------


def test_an_unknown_image_position_is_refused():
    with pytest.raises(PdfValidationException, match="image_position"):
        SignatureAppearance(image=b"x", image_position="middle")


def test_image_only_without_an_image_is_refused():
    with pytest.raises(PdfValidationException, match="has to be an image"):
        SignatureAppearance(image_position="only")


@pytest.mark.parametrize("fraction", [0.0, 0.05, 0.95, 1.0, -1.0])
def test_an_image_fraction_outside_the_box_is_refused(fraction):
    with pytest.raises(PdfValidationException, match="image_fraction"):
        SignatureAppearance(image=b"x", image_fraction=fraction)


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"font_size": -1}, "font_size"),
        ({"border_width": -0.5}, "border_width"),
        ({"font_size": "12"}, "font_size"),
        ({"font_size": float("nan")}, "font_size"),
        ({"text": 5}, "text"),
        ({"date_format": ""}, "date_format"),
    ],
)
def test_an_argument_that_is_not_what_it_should_be_is_refused(kwargs, match):
    with pytest.raises(PdfValidationException, match=match):
        SignatureAppearance(**kwargs)


def test_a_page_without_a_rect_says_nothing_about_where_to_draw():
    with pytest.raises(PdfValidationException, match="needs a rect"):
        SignatureAppearance(page=2)


@pytest.mark.parametrize(
    "rect", [(1, 2, 3), (0, 0, 0, 0), (10, 10, 10, 50), (10, 10, 50, 10)]
)
def test_a_rect_that_encloses_nothing_is_refused(rect):
    with pytest.raises(PdfValidationException):
        SignatureAppearance(rect=rect)


def test_a_rect_given_upside_down_is_put_right():
    spec = SignatureAppearance(rect=(340, 690, 50, 600))
    assert spec.rect == (50.0, 600.0, 340.0, 690.0)


def test_a_misspelled_attribute_raises_rather_than_being_lost():
    spec = SignatureAppearance()
    with pytest.raises(AttributeError):
        spec.image_positon = "right"


# ---------------------------------------------------------------------------
# What the lines say
# ---------------------------------------------------------------------------


def test_the_composed_lines_are_labelled_by_default():
    lines = layout.compose_lines(
        name="Jordan Avery",
        date="2026-10-09",
        reason="Approved",
        location="Prague",
        contact=None,
        labels=True,
    )
    assert lines == [
        "Digitally signed by Jordan Avery",
        "Date: 2026-10-09",
        "Reason: Approved",
        "Location: Prague",
    ]


def test_without_labels_the_values_stand_alone():
    lines = layout.compose_lines(
        name="Jordan Avery",
        date="2026-10-09",
        reason=None,
        location=None,
        contact=None,
        labels=False,
    )
    assert lines == ["Jordan Avery", "2026-10-09"]


@pytest.mark.parametrize("empty", [None, "", "   "])
def test_an_entry_with_no_value_is_left_out_rather_than_labelled(empty):
    lines = layout.compose_lines(
        name="Jordan Avery",
        date=None,
        reason=empty,
        location=None,
        contact=None,
        labels=True,
    )
    assert lines == ["Digitally signed by Jordan Avery"]


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------


class _Face:
    """A face whose every character is half an em wide, for exact arithmetic."""

    hex_show_strings = False

    def width(self, text: str, size: float) -> float:
        return len(text) * size * 0.5

    def wrap(self, text: str, max_width: float, size: float) -> list[str]:
        per_line = max(1, int(max_width // (size * 0.5)))
        words, lines, current = text.split(), [], ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if len(candidate) <= per_line or not current:
                current = candidate
            else:
                lines.append(current)
                current = word
        return [*lines, current] if current else lines or [""]

    def show_segments(self, text: str) -> bytes:
        return text.encode("latin-1", "replace")


def test_an_explicit_size_is_used_even_when_the_text_overflows():
    placed = layout.fit(_Face(), ["one", "two", "three"], (0, 0, 100, 20), size=10)
    assert placed.size == 10
    assert placed.overflowed == 2
    assert len(placed.lines) == 1


def test_a_size_is_chosen_that_keeps_each_entry_on_its_own_line():
    """At 12pt this entry does not fit the width; a smaller size does."""
    entry = "aaaa bbbb cccc"
    assert _Face().width(entry, layout.MAX_AUTO_SIZE) > 80
    placed = layout.fit(_Face(), [entry], (0, 0, 80, 60))
    assert not placed.wrapped
    assert len(placed.lines) == 1
    assert layout.NO_WRAP_FLOOR <= placed.size < layout.MAX_AUTO_SIZE


def test_a_line_too_long_to_keep_whole_is_wrapped_rather_than_shrunk_away():
    # Keeping this on one line would need a size far below the floor, so the
    # search gives that up and wraps at the largest size that fits the height.
    placed = layout.fit(_Face(), ["word " * 60], (0, 0, 100, 60))
    assert placed.wrapped
    assert placed.size >= layout.MIN_AUTO_SIZE
    assert len(placed.lines) > 1


def test_lines_are_stacked_downwards_from_the_top_of_the_area():
    placed = layout.fit(_Face(), ["a", "b", "c"], (5, 10, 200, 60), size=10)
    ys = [y for _text, _x, y in placed.lines]
    assert ys == sorted(ys, reverse=True)
    assert all(x == 5 for _text, x, _y in placed.lines)
    assert ys[0] == pytest.approx(10 + 60 - 10)


def test_nothing_is_placed_in_an_area_with_no_room():
    assert layout.fit(_Face(), ["a"], (0, 0, 0, 50)).lines == ()
    assert layout.fit(_Face(), [], (0, 0, 100, 50)).lines == ()


@pytest.mark.parametrize("position", ["left", "right", "top"])
def test_the_image_and_the_text_do_not_overlap(position):
    image, text = layout.image_box(
        200, 100, position=position, fraction=0.4, aspect=1.0
    )
    ix, iy, iw, ih = image
    tx, ty, tw, th = text
    assert iw > 0 and ih > 0 and tw > 0 and th > 0
    if position == "top":
        assert ty + th <= iy + 0.001
    elif position == "left":
        assert tx >= ix + iw - 0.001
    else:
        assert tx + tw <= ix + 0.001


def test_the_image_keeps_its_shape_whatever_the_box():
    for aspect in (0.25, 1.0, 4.0):
        (_x, _y, width, height), _text = layout.image_box(
            200, 100, position="background", fraction=0.4, aspect=aspect
        )
        assert width / height == pytest.approx(aspect)


def test_an_image_on_its_own_leaves_no_room_for_text():
    _image, text = layout.image_box(
        200, 100, position="only", fraction=0.4, aspect=1.0
    )
    assert text == (0.0, 0.0, 0.0, 0.0)


def test_a_background_image_and_the_text_share_the_whole_box():
    image, text = layout.image_box(
        200, 100, position="background", fraction=0.4, aspect=2.0
    )
    assert text[2] > 0 and text[3] > 0
    assert image[0] >= text[0] - 0.001


# ---------------------------------------------------------------------------
# What is drawn into the document
# ---------------------------------------------------------------------------


def test_a_visible_signature_says_who_signed_when_and_why(signer):
    data = _sign(SignatureAppearance(), signer=signer)
    doc, _engine, stream = _appearance_stream(data)
    content = _text_of(stream)
    assert "(Digitally signed by Jordan Avery) Tj" in content
    assert "(Reason: Approved) Tj" in content
    assert "(Location: Prague) Tj" in content
    assert "Date: " in content
    doc.close()


def test_the_name_comes_from_the_certificate_when_none_is_given():
    signer = _signer(common_name="", organization="Northwind Ltd")
    data = _sign(SignatureAppearance(), signer=signer)
    doc, _engine, stream = _appearance_stream(data)
    assert "(Digitally signed by Northwind Ltd) Tj" in _text_of(stream)
    doc.close()


def test_an_explicit_signer_name_wins_over_the_certificate(signer):
    data = _sign(SignatureAppearance(), signer=signer, signer_name="Office Manager")
    doc, _engine, stream = _appearance_stream(data)
    content = _text_of(stream)
    assert "(Digitally signed by Office Manager) Tj" in content
    assert "Jordan Avery" not in content
    doc.close()


def test_a_line_can_be_turned_off(signer):
    data = _sign(
        SignatureAppearance(show_date=False, show_location=False), signer=signer
    )
    doc, _engine, stream = _appearance_stream(data)
    content = _text_of(stream)
    assert "Digitally signed by" in content
    assert "Reason: Approved" in content
    assert "Date:" not in content
    assert "Location:" not in content
    doc.close()


def test_text_of_your_own_replaces_the_composed_lines(signer):
    data = _sign(
        SignatureAppearance(text="Countersigned\nby the board"), signer=signer
    )
    doc, _engine, stream = _appearance_stream(data)
    content = _text_of(stream)
    assert "(Countersigned) Tj" in content
    assert "(by the board) Tj" in content
    assert "Digitally signed by" not in content
    doc.close()


def test_the_contact_line_is_off_unless_asked_for(signer):
    plain = _sign(SignatureAppearance(), signer=signer, contact="jordan@example.com")
    doc, _engine, stream = _appearance_stream(plain)
    assert "example.com" not in _text_of(stream)
    doc.close()

    shown = _sign(
        SignatureAppearance(show_contact=True),
        signer=signer,
        contact="jordan@example.com",
    )
    doc, _engine, stream = _appearance_stream(shown)
    assert "(Contact: jordan@example.com) Tj" in _text_of(stream)
    doc.close()


def test_the_appearance_replaces_the_empty_box_the_field_was_authored_with(signer):
    before = _document()
    buffer = io.BytesIO()
    before.save(buffer)
    before.close()
    doc, _engine, stream = _appearance_stream(buffer.getvalue())
    assert "Tj" not in _text_of(stream)  # the authored box draws no text
    doc.close()

    doc, _engine, stream = _appearance_stream(_sign(SignatureAppearance(), signer=signer))
    assert "Tj" in _text_of(stream)
    doc.close()


def test_the_frame_is_drawn_in_the_colours_asked_for(signer):
    data = _sign(
        SignatureAppearance(
            background="#ff0000", border_color=(0, 0, 1), border_width=3
        ),
        signer=signer,
    )
    doc, _engine, stream = _appearance_stream(data)
    content = _text_of(stream)
    assert "1 0 0 rg" in content
    assert "0 0 1 RG" in content
    assert "3 w" in content
    doc.close()


def test_no_frame_is_drawn_when_none_is_asked_for(signer):
    doc, _engine, stream = _appearance_stream(
        _sign(SignatureAppearance(), signer=signer)
    )
    content = _text_of(stream)
    assert " re\nf\n" not in content
    assert " re\nS\n" not in content
    doc.close()


def test_an_explicit_font_size_is_what_is_written(signer):
    doc, _engine, stream = _appearance_stream(
        _sign(SignatureAppearance(font_size=7.5), signer=signer)
    )
    assert "/F1 7.5 Tf" in _text_of(stream)
    doc.close()


# ---------------------------------------------------------------------------
# The image
# ---------------------------------------------------------------------------


def test_an_image_is_embedded_and_drawn(signer):
    data = _sign(SignatureAppearance(image=_png(60, 30, (0, 0, 255))), signer=signer)
    doc, engine, stream = _appearance_stream(data)
    resources = engine._resolve(stream.mapping.get(PdfName("Resources")))
    xobjects = engine._resolve(resources.mapping.get(PdfName("XObject")))
    assert xobjects is not None and len(xobjects.mapping) == 1
    name = next(iter(xobjects.mapping)).name.lstrip("/")
    content = _text_of(stream)
    assert f"/{name} Do" in content
    assert " cm\n" in content  # placed by a matrix, not drawn at unit size
    doc.close()


def test_an_image_is_scaled_to_fit_without_being_squashed(signer):
    data = _sign(SignatureAppearance(image=_png(60, 30, (0, 0, 255))), signer=signer)
    doc, _engine, stream = _appearance_stream(data)
    matrix = next(
        line for line in _text_of(stream).splitlines() if line.endswith(" cm")
    ).split()
    width, height = float(matrix[0]), float(matrix[3])
    assert width / height == pytest.approx(2.0, abs=0.01)
    doc.close()


def test_a_background_image_is_painted_before_the_text_not_over_it(signer):
    """Operators paint over what came before, so the image has to come first."""
    data = _sign(
        SignatureAppearance(
            image=_png(80, 40, (200, 200, 220)), image_position="background"
        ),
        signer=signer,
    )
    doc, _engine, stream = _appearance_stream(data)
    content = _text_of(stream)
    assert content.index(" Do") < content.index("BT")
    doc.close()


def test_an_image_on_its_own_draws_no_text(signer):
    data = _sign(
        SignatureAppearance(image=_png(40, 40, (0, 128, 0)), image_position="only"),
        signer=signer,
    )
    doc, engine, stream = _appearance_stream(data)
    content = _text_of(stream)
    assert " Do" in content
    assert "BT" not in content
    resources = engine._resolve(stream.mapping.get(PdfName("Resources")))
    assert PdfName("Font") not in resources.mapping
    doc.close()


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------


def test_text_a_standard_font_cannot_draw_is_refused_by_name():
    signer = _signer(common_name="Яна Ковальская")
    doc = _document()
    certificate, key = signer
    doc.sign(
        "Approval",
        certificate=certificate,
        private_key=key,
        appearance=SignatureAppearance(),
    )
    with pytest.raises(PdfValidationException, match="pass font="):
        doc.save(io.BytesIO())
    doc.close()


def test_an_embedded_font_draws_what_the_standard_fonts_cannot():
    signer = _signer(common_name="Яна Ковальская")
    data = _sign(
        SignatureAppearance(font=_cyrillic_face()), signer=signer, reason=None
    )
    doc, engine, stream = _appearance_stream(data)
    content = _text_of(stream)
    # A composite font shows its text as two-byte CID codes, not as literals.
    assert "> Tj" in content
    assert "(Digitally" not in content
    resources = engine._resolve(stream.mapping.get(PdfName("Resources")))
    fonts = engine._resolve(resources.mapping.get(PdfName("Font")))
    font = engine._resolve(next(iter(fonts.mapping.values())))
    assert engine._get_name(font.mapping.get(PdfName("Subtype"))) == "Type0"
    doc.close()


# ---------------------------------------------------------------------------
# Where the field goes
# ---------------------------------------------------------------------------


def test_an_appearance_can_place_the_field_it_is_drawn_in(signer):
    certificate, key = signer
    doc = Document()
    doc.pages.add(PageSize.A4)
    doc.pages.add(PageSize.A4)
    doc.sign(
        certificate=certificate,
        private_key=key,
        appearance=SignatureAppearance(page=1, rect=(40, 500, 300, 580)),
    )
    data = _signed(doc)
    opened, engine, widget = _widget(data, page_index=1)
    rect = engine._cos_number_list(widget.mapping.get(PdfName("Rect")))
    assert rect == [40.0, 500.0, 300.0, 580.0]
    ap = engine._resolve(widget.mapping.get(PdfName("AP")))
    assert "Tj" in _text_of(engine._resolve(ap.mapping.get(PdfName("N"))))
    opened.close()


def test_signing_visibly_with_nowhere_to_draw_is_refused(signer):
    certificate, key = signer
    doc = Document()
    doc.pages.add(PageSize.A4)
    with pytest.raises(PdfValidationException, match="somewhere to be drawn"):
        doc.sign(
            certificate=certificate,
            private_key=key,
            appearance=SignatureAppearance(),
        )
    doc.close()


def test_a_page_the_document_does_not_have_is_refused(signer):
    certificate, key = signer
    doc = Document()
    doc.pages.add(PageSize.A4)
    with pytest.raises(PdfValidationException, match="document has 1"):
        doc.sign(
            certificate=certificate,
            private_key=key,
            appearance=SignatureAppearance(page=4, rect=(10, 10, 100, 60)),
        )
    doc.close()


def test_a_field_with_no_area_cannot_show_an_appearance(signer):
    certificate, key = signer
    doc = _document(rect=None)
    doc.form.add_signature_field("Approval", doc.pages[0], [10, 10, 10, 10])
    doc.sign(
        "Approval",
        certificate=certificate,
        private_key=key,
        appearance=SignatureAppearance(),
    )
    with pytest.raises(PdfValidationException, match="area to draw"):
        doc.save(io.BytesIO())
    doc.close()


def test_an_appearance_must_be_a_signature_appearance(signer):
    certificate, key = signer
    doc = _document()
    with pytest.raises(TypeError, match="SignatureAppearance"):
        doc.sign(
            "Approval",
            certificate=certificate,
            private_key=key,
            appearance={"image": None},
        )
    doc.close()


def test_a_field_shown_on_two_pages_is_drawn_on_both(signer):
    certificate, key = signer
    doc = Document()
    first = doc.pages.add(PageSize.A4)
    second = doc.pages.add(PageSize.A4)
    doc.form._create_field(
        "Approval",
        "signature",
        [
            {"page_index": first.index, "rect": (50, 600, 300, 680)},
            {"page_index": second.index, "rect": (50, 600, 300, 680)},
        ],
    )
    doc.sign(
        "Approval",
        certificate=certificate,
        private_key=key,
        reason="Approved",
        appearance=SignatureAppearance(),
    )
    data = _signed(doc)
    for page_index in (0, 1):
        opened, _engine, stream = _appearance_stream(data, page_index=page_index)
        assert "(Reason: Approved) Tj" in _text_of(stream)
        opened.close()


# ---------------------------------------------------------------------------
# The signature covers the appearance
# ---------------------------------------------------------------------------


def test_the_signature_is_valid_with_an_appearance(signer):
    data = _sign(SignatureAppearance(image=_png(40, 20, (0, 0, 0))), signer=signer)
    with Document(data) as doc:
        assert len(doc.signatures) == 1
        assert doc.signatures[0].valid


def test_editing_the_appearance_breaks_the_signature(signer):
    """The whole point of drawing it before the save rather than after."""
    data = _sign(SignatureAppearance(), signer=signer)
    assert b"(Location: Prague) Tj" in data
    tampered = data.replace(b"(Location: Prague) Tj", b"(Location: Berlin) Tj")
    assert len(tampered) == len(data)
    with Document(tampered) as doc:
        assert not doc.signatures[0].valid


def test_a_second_visible_signature_keeps_the_first_one_valid(signer):
    certificate, key = signer
    doc = Document()
    page = doc.pages.add(PageSize.A4)
    doc.form.add_signature_field("First", page, [40, 600, 290, 680])
    doc.form.add_signature_field("Second", page, [310, 600, 560, 680])
    doc.sign(
        "First",
        certificate=certificate,
        private_key=key,
        reason="Drafted",
        appearance=SignatureAppearance(),
    )
    buffer = io.BytesIO()
    doc.save(buffer)
    doc.sign(
        "Second",
        certificate=certificate,
        private_key=key,
        reason="Countersigned",
        appearance=SignatureAppearance(),
    )
    second = io.BytesIO()
    doc.save(second)
    doc.close()

    with Document(second.getvalue()) as opened:
        assert len(opened.signatures) == 2
        assert all(signature.valid for signature in opened.signatures)
    for index, reason in ((0, "Drafted"), (1, "Countersigned")):
        shown, _engine, stream = _appearance_stream(second.getvalue(), index=index)
        assert f"(Reason: {reason}) Tj" in _text_of(stream)
        shown.close()


def test_signing_without_an_appearance_leaves_the_widget_as_it_was(signer):
    certificate, key = signer
    doc = _document()
    doc.sign("Approval", certificate=certificate, private_key=key)
    opened, _engine, stream = _appearance_stream(_signed(doc))
    assert "Tj" not in _text_of(stream)
    opened.close()

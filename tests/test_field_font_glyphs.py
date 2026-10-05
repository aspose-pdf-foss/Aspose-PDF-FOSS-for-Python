"""A field font must carry the glyphs of the value it was embedded to draw.

``add_text_field(font=...)`` embeds a Type0 (CID) font so a non-Latin value
renders. The graph was built the moment the field was created -- before the
value had been encoded -- and nothing rewrote it afterwards, so the embedded
program kept no glyph at all, ``/W`` was an empty array and ``/CIDToGIDMap``
held the single entry CID 0. The appearance showed CIDs 1..n through a font that
could not draw them: the field came out **blank in every engine**.

Measured on the real thing before the fix: a field authored with Arial Unicode
for ``"Привет мир — ÄÖÜ"`` rendered 0 non-white pixels by us, by MuPDF and by
pdfium alike, while both engines read the field *value* back correctly. After
it, MuPDF extracts the string from the widget appearance and our render differs
from MuPDF's on 533 edge pixels of 484704 -- fewer than the two references
differ from each other (702).

The page-text path has always refreshed the font after encoding
(``_refresh_authored_font_resource``); this is the same call, which the field
path never made. The second half is the ``/MK`` frame: the Standard-14 path
paints the widget's background and border under the text, so a field with an
embedded font drew no box either.

Synthetic fonts here, so the test does not depend on what is installed.
"""

from __future__ import annotations

import io

import pytest

pytest.importorskip("fontTools")

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont

from aspose_pdf import Document
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfDictionary,
    PdfIndirectReference,
    PdfName,
    PdfStream,
)
from aspose_pdf.engine.simple_pdf import SimplePdf

_VALUE = "Привет"
_WHITE = (255, 255, 255)
_ADVANCE = 400
_PAGE = (0, 0, 300, 200)
_RECT = (20.0, 150.0, 280.0, 180.0)


def _font(text: str = _VALUE, *, advance: int = _ADVANCE) -> bytes:
    """A TrueType font with one box glyph per distinct character of *text*."""
    characters = list(dict.fromkeys(text))
    order = [".notdef"] + [f"g{ord(c):04X}" for c in characters]
    glyphs = {".notdef": TTGlyphPen(None).glyph()}
    cmap = {}
    for char in characters:
        pen = TTGlyphPen(None)
        pen.moveTo((0, 0))
        pen.lineTo((advance, 0))
        pen.lineTo((advance, 700))
        pen.closePath()
        glyphs[f"g{ord(char):04X}"] = pen.glyph()
        cmap[ord(char)] = f"g{ord(char):04X}"
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder(order)
    builder.setupCharacterMap(cmap)
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics({name: (advance, 0) for name in order})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": "Boxes", "styleName": "R"})
    builder.setupOS2()
    builder.setupPost()
    builder.setupMaxp()
    builder.font.recalcTimestamp = False
    out = io.BytesIO()
    builder.font.save(out)
    return out.getvalue()


def _blank() -> Document:
    engine = SimplePdf()
    engine.pages = [_PAGE]
    engine.page_contents = [b""]
    engine._ensure_cos()
    document = Document()
    document._engine_pdf = engine
    return document


def _document(*fields: tuple[str, str, tuple[float, float, float, float]]) -> Document:
    """A one-page document carrying one text field per *fields* entry."""
    document = _blank()
    program = _font()
    for name, value, rect in fields:
        document.form.add_text_field(
            name, document.pages[0], rect, value=value, font=program
        )
    return document


def _single(value: str = _VALUE) -> Document:
    return _document(("nm", value, _RECT))


# --- reading the embedded font back out -------------------------------------


def _cid_font(document: Document):
    engine = document._engine_pdf
    root = engine._resolve(engine._cos_doc.trailer.get(PdfName("Root")))
    acro = engine._resolve(root.mapping.get(PdfName("AcroForm")))
    fonts = engine._resolve(
        engine._resolve(acro.mapping.get(PdfName("DR"))).mapping.get(PdfName("Font"))
    )
    for ref in fonts.mapping.values():
        font = engine._resolve(ref)
        if engine._get_name(font.mapping.get(PdfName("Subtype"))) != "Type0":
            continue
        descendants = engine._resolve(font.mapping.get(PdfName("DescendantFonts")))
        return engine, engine._resolve(descendants.items[0])
    raise AssertionError("no Type0 font in /DR")


def _widths(document: Document) -> dict[int, float]:
    """``/W`` flattened to ``{cid: width}`` (ISO 32000-1 table 117)."""
    engine, cid_font = _cid_font(document)
    array = engine._resolve(cid_font.mapping.get(PdfName("W")))
    out: dict[int, float] = {}
    items = list(array.items)
    index = 0
    while index < len(items):
        first = engine._pdf_number(items[index])
        following = engine._resolve(items[index + 1])
        if isinstance(following, PdfArray):
            for offset, entry in enumerate(following.items):
                out[int(first) + offset] = engine._pdf_number(entry)
            index += 2
        else:
            width = engine._pdf_number(items[index + 2])
            for cid in range(int(first), int(following) + 1):
                out[cid] = width
            index += 3
    return out


def _cid_to_gid(document: Document) -> list[int]:
    engine, cid_font = _cid_font(document)
    mapping = engine._resolve(cid_font.mapping.get(PdfName("CIDToGIDMap")))
    assert isinstance(mapping, PdfStream), "a subset needs an explicit map"
    data = mapping.content
    return [int.from_bytes(data[i : i + 2], "big") for i in range(0, len(data), 2)]


def _program(document: Document) -> TTFont:
    engine, cid_font = _cid_font(document)
    descriptor = engine._resolve(cid_font.mapping.get(PdfName("FontDescriptor")))
    stream = engine._resolve(descriptor.mapping.get(PdfName("FontFile2")))
    return TTFont(io.BytesIO(stream.content))


def _appearance(document: Document) -> bytes:
    engine = document._engine_pdf
    annots = engine._resolve(engine._get_page_dict(0).mapping.get(PdfName("Annots")))
    for ref in annots.items:
        widget = engine._resolve(ref)
        if engine._get_name(widget.mapping.get(PdfName("Subtype"))) != "Widget":
            continue
        appearance = engine._resolve(widget.mapping.get(PdfName("AP")))
        normal = engine._resolve(appearance.mapping.get(PdfName("N")))
        if isinstance(normal, PdfStream):
            return normal.content
    raise AssertionError("no widget appearance")


def _shown_cids(content: bytes) -> list[int]:
    """The CIDs of the ``<..> Tj`` operand in an appearance stream."""
    body = content.split(b"<", 1)[1].split(b">", 1)[0]
    digits = bytes(body).decode("ascii")
    return [int(digits[i : i + 4], 16) for i in range(0, len(digits), 4)]


def _unreachable(document: Document) -> set[int]:
    """Object numbers no reference from the trailer leads to."""
    engine = document._engine_pdf
    cos = engine._cos_doc
    seen: set[int] = set()
    stack = list(cos.trailer.mapping.values())
    while stack:
        item = stack.pop()
        if isinstance(item, PdfIndirectReference):
            if item.object_number in seen:
                continue
            seen.add(item.object_number)
            item = cos.objects.get(item.object_number)
        if isinstance(item, PdfStream):
            stack.extend(item.mapping.values())
        elif isinstance(item, PdfDictionary):
            stack.extend(item.mapping.values())
        elif isinstance(item, PdfArray):
            stack.extend(item.items)
    return {number for number in cos.objects if number not in seen}


def _ink(document: Document) -> int:
    raster = document.pages[0].render(dpi=72, antialias=False)
    return sum(
        1
        for y in range(int(_PAGE[3]))
        for x in range(int(_PAGE[2]))
        if raster.get_pixel(x, y) != _WHITE
    )


# --- the font describes the glyphs the appearance asks for ------------------


def test_every_shown_cid_has_a_width():
    document = _single()
    widths = _widths(document)
    shown = _shown_cids(_appearance(document))
    assert shown, "the appearance shows nothing"
    assert set(shown) <= set(widths), f"no /W for {sorted(set(shown) - set(widths))}"
    # The font's own advance, not the /DW 1000 fallback.
    assert {widths[cid] for cid in shown} == {float(_ADVANCE)}


def test_every_shown_cid_maps_to_a_glyph_with_outlines():
    document = _single()
    mapping = _cid_to_gid(document)
    program = _program(document)
    glyf = program["glyf"]
    order = program.getGlyphOrder()
    for cid in _shown_cids(_appearance(document)):
        assert cid < len(mapping), f"CID {cid} is past the end of /CIDToGIDMap"
        gid = mapping[cid]
        assert gid != 0, f"CID {cid} maps to .notdef"
        assert glyf[order[gid]].numberOfContours > 0, f"glyph {gid} is empty"


def test_the_map_is_distinct_per_cid():
    # One entry per distinct character, all different: a single shared glyph
    # would draw the same box for every letter.
    document = _single()
    shown = _shown_cids(_appearance(document))
    mapping = _cid_to_gid(document)
    gids = [mapping[cid] for cid in shown]
    assert len(set(gids)) == len(set(shown))


def test_the_subset_keeps_only_what_the_value_needs():
    document = _single()
    program = _program(document)
    glyf = program["glyf"]
    order = program.getGlyphOrder()
    outlined = {
        gid
        for gid in range(program["maxp"].numGlyphs)
        if glyf[order[gid]].numberOfContours > 0
    }
    assert outlined == set(_cid_to_gid(document)[1:])


# --- the widget frame -------------------------------------------------------


def test_the_appearance_paints_the_widget_box_before_the_text():
    content = _appearance(_single())
    # /MK /BG white fill and /BC black 1pt border, as the Standard-14 path does.
    assert content.index(b" re") < content.index(b"/Tx BMC")
    assert b"0 0 260 30 re" in content
    assert b"f\n" in content and b"S\n" in content


def test_an_empty_value_still_draws_the_box():
    assert _ink(_single("")) > 0


# --- what a viewer ends up seeing -------------------------------------------


def test_the_field_is_not_blank():
    assert _ink(_single()) > _ink(_single("")), "the value drew nothing"


def test_generate_appearances_keeps_the_baked_value():
    document = _single()
    before = _ink(document)
    document.form.generate_appearances()
    assert _ink(document) == before


def test_flattening_keeps_the_value_on_the_page():
    document = _single()
    before = _ink(document)
    document.form.flatten()
    assert _ink(document) == before


def test_each_of_two_fields_gets_its_own_glyphs():
    document = _document(
        ("one", _VALUE, _RECT), ("two", _VALUE, (20.0, 100.0, 280.0, 130.0))
    )
    assert _ink(document) == 2 * _ink(_single())


def test_the_value_survives_a_round_trip():
    document = _single()
    buffer = io.BytesIO()
    document.save(buffer)
    reloaded = Document()
    reloaded.load_from(buffer.getvalue())
    assert reloaded.form["nm"].value == _VALUE
    assert _shown_cids(_appearance(reloaded)) == _shown_cids(_appearance(document))
    assert _widths(reloaded) == _widths(document)
    assert _cid_to_gid(reloaded) == _cid_to_gid(document)


def test_a_wider_font_reports_its_own_advances():
    # Pins the width as read from the font rather than guessed or defaulted.
    document = _blank()
    document.form.add_text_field(
        "nm",
        document.pages[0],
        _RECT,
        value=_VALUE,
        font=_font(advance=712),
    )
    assert set(_widths(document).values()) == {712.0}


def test_the_graph_reuses_its_own_cid_to_gid_stream():
    # Refreshing must rewrite the stream the graph registered, not register a
    # second one and leave the first behind as a dead object in every file.
    document = _single()
    assert _unreachable(document) == set()

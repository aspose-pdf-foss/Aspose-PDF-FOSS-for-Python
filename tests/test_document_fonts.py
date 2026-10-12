"""What fonts a document uses: ``Document.fonts``.

``FontRepository`` answers what fonts are on the *machine*; nothing answered
what a *document* draws with. The engine cached a ``_cached_fonts`` dict keyed
by resource name, which is neither a font list (one font named ``/F1`` on three
pages is three entries) nor reachable without going behind the public API.

The behaviour under test is mostly **grouping and honesty**: the walk is the
one the conformance checks use, so what it finds is settled; what is new is
that font dictionaries saying the same thing become one entry with the pages of
both, that ones saying *different* things stay apart, and that each field says
something true about a font even in the awkward cases -- a Type 3 font with no
program, a symbolic font with no encoding, a font reached only through a form's
default resources, a font with no name at all.
"""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document, DocumentFont, PageSize
from aspose_pdf.engine.cos import (
    PdfArray,
    PdfDictionary,
    PdfName,
    PdfNumber,
    PdfStream,
)
from aspose_pdf.engine.font_inventory import inventory
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.engine.std_font_data import load_substitute_sfnt

# ---------------------------------------------------------------------------
# Fixtures: hand-built font dictionaries, so each field has one cause
# ---------------------------------------------------------------------------


def _pdf_with(fonts: dict[str, PdfDictionary], *, pages: int = 1) -> SimplePdf:
    """A document whose pages all carry *fonts* in their resources."""
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 200, 200)] * pages
    pdf.page_contents = [b"BT /A 12 Tf (x) Tj ET"] * pages
    pdf._ensure_cos()
    registered = {
        PdfName(key): pdf._cos_doc.register_object(value)
        for key, value in fonts.items()
    }
    for index in range(pages):
        pdf._get_page_dict(index).mapping[PdfName("Resources")] = PdfDictionary(
            {PdfName("Font"): PdfDictionary(dict(registered))}
        )
    return pdf


def _simple(
    base: str | None = "Helvetica",
    *,
    subtype: str = "Type1",
    encoding=None,
    program: tuple[str, bytes, str | None] | None = None,
    to_unicode: bool = False,
) -> PdfDictionary:
    mapping = {PdfName("Type"): PdfName("Font"), PdfName("Subtype"): PdfName(subtype)}
    if base is not None:
        mapping[PdfName("BaseFont")] = PdfName(base)
    if encoding is not None:
        mapping[PdfName("Encoding")] = encoding
    if to_unicode:
        mapping[PdfName("ToUnicode")] = PdfStream(
            b"%!PS", {PdfName("Length"): PdfNumber(4)}
        )
    if program is not None:
        key, data, stream_subtype = program
        stream_map = {PdfName("Length"): PdfNumber(len(data))}
        if stream_subtype:
            stream_map[PdfName("Subtype")] = PdfName(stream_subtype)
        mapping[PdfName("FontDescriptor")] = PdfDictionary(
            {
                PdfName("Type"): PdfName("FontDescriptor"),
                PdfName("FontName"): PdfName(base or "Unnamed"),
                PdfName(key): PdfStream(data, stream_map),
            }
        )
    return PdfDictionary(mapping)


def _type3() -> PdfDictionary:
    """A Type 3 font, whose glyphs are content streams rather than a program."""
    return PdfDictionary(
        {
            PdfName("Type"): PdfName("Font"),
            PdfName("Subtype"): PdfName("Type3"),
            PdfName("FontBBox"): PdfArray([PdfNumber(0)] * 4),
            PdfName("FontMatrix"): PdfArray(
                [PdfNumber(v) for v in (0.001, 0, 0, 0.001, 0, 0)]
            ),
            PdfName("CharProcs"): PdfDictionary(
                {
                    PdfName("a"): PdfStream(
                        b"0 0 0 0 0 0 d0", {PdfName("Length"): PdfNumber(14)}
                    )
                }
            ),
            PdfName("Encoding"): PdfName("WinAnsiEncoding"),
            PdfName("FirstChar"): PdfNumber(97),
            PdfName("LastChar"): PdfNumber(97),
            PdfName("Widths"): PdfArray([PdfNumber(500)]),
        }
    )


def _differences() -> PdfDictionary:
    return PdfDictionary(
        {
            PdfName("Type"): PdfName("Encoding"),
            PdfName("BaseEncoding"): PdfName("WinAnsiEncoding"),
            PdfName("Differences"): PdfArray([PdfNumber(65), PdfName("bullet")]),
        }
    )


def _reopen(document: Document) -> Document:
    buffer = io.BytesIO()
    document.save(buffer)
    document.close()
    return Document(buffer.getvalue())


def _by_name(fonts: list[DocumentFont]) -> dict[str, DocumentFont]:
    return {font.name: font for font in fonts}


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------


def test_one_font_used_on_several_pages_is_one_entry():
    fonts = inventory(_pdf_with({"A": _simple("Helvetica")}, pages=3))
    assert len(fonts) == 1
    assert fonts[0].page_indexes == (0, 1, 2)
    assert fonts[0].pages == (1, 2, 3)


def test_a_document_that_repeats_a_font_dictionary_still_uses_one_font():
    """Each page carrying its own /Helv is one font, not one per page."""
    document = Document()
    for _ in range(3):
        page = document.pages.add(PageSize.A4)
        page.add_text("x", 50, 700, font_name="Helvetica")
    with _reopen(document) as saved:
        helvetica = [f for f in saved.fonts if f.name == "Helvetica"]
        assert len(helvetica) == 1
        assert helvetica[0].page_indexes == (0, 1, 2)


def test_two_subsets_of_one_family_stay_apart():
    """They carry different glyphs; folding them would hide a program."""
    fonts = inventory(
        _pdf_with(
            {
                "A": _simple("ABCDEF+Arial", program=("FontFile2", b"a" * 10, None)),
                "B": _simple("GHIJKL+Arial", program=("FontFile2", b"b" * 20, None)),
            }
        )
    )
    assert len(fonts) == 2
    assert {f.base_font for f in fonts} == {"ABCDEF+Arial", "GHIJKL+Arial"}
    assert {f.name for f in fonts} == {"Arial"}
    assert sorted(f.program_bytes for f in fonts) == [10, 20]


def test_one_name_with_two_encodings_stays_apart():
    fonts = inventory(
        _pdf_with(
            {
                "A": _simple("Helvetica", encoding=PdfName("WinAnsiEncoding")),
                "B": _simple("Helvetica", encoding=PdfName("MacRomanEncoding")),
            }
        )
    )
    assert sorted(f.encoding for f in fonts) == [
        "MacRomanEncoding",
        "WinAnsiEncoding",
    ]


def test_one_name_with_two_programs_stays_apart():
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 200, 200)]
    pdf.page_contents = [b""]
    pdf._ensure_cos()
    cos = pdf._cos_doc

    def font(data: bytes) -> PdfDictionary:
        stream = cos.register_object(
            PdfStream(data, {PdfName("Length"): PdfNumber(len(data))})
        )
        descriptor = cos.register_object(
            PdfDictionary(
                {
                    PdfName("Type"): PdfName("FontDescriptor"),
                    PdfName("FontName"): PdfName("Shared"),
                    PdfName("FontFile2"): stream,
                }
            )
        )
        return PdfDictionary(
            {
                PdfName("Type"): PdfName("Font"),
                PdfName("Subtype"): PdfName("TrueType"),
                PdfName("BaseFont"): PdfName("Shared"),
                PdfName("FontDescriptor"): descriptor,
            }
        )

    first, second = font(b"A" * 10), font(b"B" * 10)
    # A third dictionary sharing the first one's descriptor is the same font.
    third = PdfDictionary(dict(first.mapping))
    pdf._get_page_dict(0).mapping[PdfName("Resources")] = PdfDictionary(
        {
            PdfName("Font"): PdfDictionary(
                {
                    PdfName("A"): cos.register_object(first),
                    PdfName("B"): cos.register_object(second),
                    PdfName("C"): cos.register_object(third),
                }
            )
        }
    )
    assert len(inventory(pdf)) == 2


def test_fonts_are_listed_in_the_order_the_pages_reach_them():
    document = Document()
    first = document.pages.add(PageSize.A4)
    first.add_text("a", 50, 700, font_name="Courier")
    second = document.pages.add(PageSize.A4)
    second.add_text("b", 50, 700, font_name="Times-Bold")
    with _reopen(document) as saved:
        assert [f.name for f in saved.fonts] == ["Courier", "Times-Bold"]


# ---------------------------------------------------------------------------
# What each field says
# ---------------------------------------------------------------------------


def test_a_standard_14_font_is_named_as_one_and_is_not_embedded():
    font = inventory(_pdf_with({"A": _simple("Helvetica")}))[0]
    assert font.is_standard_14
    assert not font.embedded
    assert font.program_key is None
    assert font.program_format is None
    assert font.program_bytes is None


@pytest.mark.parametrize(
    "base", ["Helvetica-Bold", "Times-Roman", "Courier-Oblique", "Symbol"]
)
def test_the_other_standard_14_names_are_recognised(base):
    assert inventory(_pdf_with({"A": _simple(base)}))[0].is_standard_14


def test_a_font_of_its_own_is_not_a_standard_14_font():
    assert not inventory(_pdf_with({"A": _simple("Corporate Sans")}))[0].is_standard_14


@pytest.mark.parametrize(
    ("program", "expected"),
    [
        (("FontFile", b"x" * 11, None), "Type 1"),
        (("FontFile2", b"x" * 12, None), "TrueType"),
        (("FontFile3", b"x" * 13, "Type1C"), "Type1C"),
        (("FontFile3", b"x" * 14, "OpenType"), "OpenType"),
        (("FontFile3", b"x" * 15, "CIDFontType0C"), "CIDFontType0C"),
        (("FontFile3", b"x" * 16, None), "compact"),
    ],
)
def test_the_program_is_reported_by_the_key_and_its_own_subtype(program, expected):
    font = inventory(_pdf_with({"A": _simple("Foo", program=program)}))[0]
    assert font.embedded
    assert font.program_key == program[0]
    assert font.program_format == expected
    # Unfiltered here, so the two sizes agree.
    assert font.program_bytes == len(program[1])
    assert font.stored_bytes == len(program[1])


def test_a_compressed_program_reports_its_real_size_and_what_it_costs():
    """Two numbers answering two questions, and they are not the same number."""
    import zlib

    raw = b"T" * 4000
    packed = zlib.compress(raw)
    assert len(packed) < len(raw) / 4
    stream = PdfStream(
        packed,
        {
            PdfName("Length"): PdfNumber(len(packed)),
            PdfName("Filter"): PdfName("FlateDecode"),
        },
    )
    font = PdfDictionary(
        {
            PdfName("Type"): PdfName("Font"),
            PdfName("Subtype"): PdfName("TrueType"),
            PdfName("BaseFont"): PdfName("Packed"),
            PdfName("FontDescriptor"): PdfDictionary(
                {
                    PdfName("Type"): PdfName("FontDescriptor"),
                    PdfName("FontName"): PdfName("Packed"),
                    PdfName("FontFile2"): stream,
                }
            ),
        }
    )
    entry = inventory(_pdf_with({"A": font}))[0]
    assert entry.stored_bytes == len(packed)
    assert entry.program_bytes == len(raw)


def test_length1_is_trusted_rather_than_decoding_every_font():
    """The file's own statement of the decoded size (table 122)."""
    import zlib

    packed = zlib.compress(b"T" * 500)
    stream = PdfStream(
        packed,
        {
            PdfName("Length"): PdfNumber(len(packed)),
            PdfName("Length1"): PdfNumber(500),
            PdfName("Filter"): PdfName("FlateDecode"),
        },
    )
    font = PdfDictionary(
        {
            PdfName("Type"): PdfName("Font"),
            PdfName("Subtype"): PdfName("TrueType"),
            PdfName("BaseFont"): PdfName("Stated"),
            PdfName("FontDescriptor"): PdfDictionary(
                {
                    PdfName("Type"): PdfName("FontDescriptor"),
                    PdfName("FontName"): PdfName("Stated"),
                    PdfName("FontFile2"): stream,
                }
            ),
        }
    )
    entry = inventory(_pdf_with({"A": font}))[0]
    assert entry.program_bytes == 500
    assert entry.stored_bytes == len(packed)


def test_a_subset_tag_is_recognised_and_stripped_from_the_name():
    font = inventory(_pdf_with({"A": _simple("ABCDEF+Arial")}))[0]
    assert font.is_subset
    assert font.base_font == "ABCDEF+Arial"
    assert font.name == "Arial"


@pytest.mark.parametrize("base", ["Arial", "ABCDE+Arial", "abcdef+Arial", "A+Arial"])
def test_what_is_not_a_subset_tag_is_not_treated_as_one(base):
    font = inventory(_pdf_with({"A": _simple(base)}))[0]
    assert not font.is_subset
    assert font.name == base


def test_an_encoding_dictionary_reports_its_base_and_that_it_remaps_codes():
    font = inventory(_pdf_with({"A": _simple("Helvetica", encoding=_differences())}))[0]
    assert font.encoding == "WinAnsiEncoding"
    assert font.has_differences


def test_a_font_that_names_no_encoding_says_so():
    font = inventory(_pdf_with({"A": _simple("Symbol")}))[0]
    assert font.encoding is None
    assert not font.has_differences


def test_a_to_unicode_map_is_reported():
    with_map = inventory(_pdf_with({"A": _simple("Foo", to_unicode=True)}))[0]
    without = inventory(_pdf_with({"A": _simple("Foo")}))[0]
    assert with_map.has_to_unicode
    assert not without.has_to_unicode


def test_a_font_with_no_name_is_listed_rather_than_dropped():
    font = inventory(_pdf_with({"A": _simple(None, subtype="TrueType")}))[0]
    assert font.base_font == ""
    assert font.name == ""
    assert "(unnamed)" in str(font)


def test_a_type3_font_is_embedded_although_it_has_no_program():
    """Its glyphs are the content streams of /CharProcs (9.6.5)."""
    font = inventory(_pdf_with({"T": _type3()}))[0]
    assert font.subtype == "Type3"
    assert font.embedded
    assert font.program_key is None
    assert font.program_format is None


# ---------------------------------------------------------------------------
# Composite fonts
# ---------------------------------------------------------------------------


def test_a_composite_font_reports_its_descendant_and_its_program():
    document = Document()
    page = document.pages.add(PageSize.A4)
    page.add_text("Zażółć", 50, 700, font=load_substitute_sfnt("sans-regular"))
    with _reopen(document) as saved:
        font = next(f for f in saved.fonts if f.subtype == "Type0")
        assert font.is_composite
        assert font.descendant_subtype == "CIDFontType2"
        assert font.encoding == "Identity-H"
        assert font.embedded
        assert font.program_key == "FontFile2"
        assert font.program_bytes and font.program_bytes > 0
        assert font.has_to_unicode
        assert font.is_subset


def test_a_simple_font_has_no_descendant():
    assert inventory(_pdf_with({"A": _simple("Helvetica")}))[0].descendant_subtype is None
    assert not inventory(_pdf_with({"A": _simple("Helvetica")}))[0].is_composite


# ---------------------------------------------------------------------------
# Where a font is used
# ---------------------------------------------------------------------------


def test_a_font_used_only_by_the_form_has_no_page_but_says_where_it_is():
    document = Document()
    page = document.pages.add(PageSize.A4)
    document.form.add_text_field("F", page, [50, 400, 200, 430], value="x")
    with _reopen(document) as saved:
        fonts = saved.fonts
        assert fonts, "the form's default resources were not walked"
        assert any(
            "default resources" in location
            for font in fonts
            for location in font.locations
        )


def test_the_locations_name_every_place_a_font_was_reached():
    document = Document()
    first = document.pages.add(PageSize.A4)
    first.add_text("a", 50, 700, font_name="Helvetica")
    second = document.pages.add(PageSize.A4)
    second.add_text("b", 50, 700, font_name="Helvetica")
    with _reopen(document) as saved:
        helvetica = _by_name(saved.fonts)["Helvetica"]
        assert "page 1" in helvetica.locations
        assert "page 2" in helvetica.locations


def test_the_inventory_and_the_pdfa_check_agree_on_what_is_not_embedded():
    """Both read the same walk, so they cannot disagree."""
    document = Document()
    page = document.pages.add(PageSize.A4)
    page.add_text("a", 50, 700, font_name="Helvetica")
    page.add_text("b", 50, 650, font=load_substitute_sfnt("sans-regular"))
    with _reopen(document) as saved:
        reported = {f.name for f in saved.fonts if not f.embedded}
        errors = " ".join(saved.validate_pdfa("2b").errors)
        for name in reported:
            assert name in errors
        for font in saved.fonts:
            if font.embedded:
                assert f"{font.base_font} in" not in errors


# ---------------------------------------------------------------------------
# The property itself
# ---------------------------------------------------------------------------


def test_a_document_with_no_fonts_has_an_empty_inventory():
    document = Document()
    document.pages.add(PageSize.A4)
    with _reopen(document) as saved:
        assert saved.fonts == []


def test_an_empty_document_has_an_empty_inventory():
    with Document() as document:
        assert document.fonts == []


def test_reading_the_inventory_does_not_change_the_document():
    document = Document()
    document.pages.add(PageSize.A4).add_text("a", 50, 700)
    saved = _reopen(document)
    before = io.BytesIO()
    saved.save(before)
    assert saved.fonts
    after = io.BytesIO()
    saved.save(after)
    saved.close()
    assert before.getvalue() == after.getvalue()


def test_the_inventory_is_a_snapshot_taken_again_after_embedding():
    document = Document()
    document.pages.add(PageSize.A4).add_text("a", 50, 700, font_name="Helvetica")
    with _reopen(document) as saved:
        assert not saved.fonts[0].embedded
        saved.embed_fonts()
        assert saved.fonts[0].embedded
        assert saved.fonts[0].program_key == "FontFile2"


def test_a_disposed_document_refuses_to_be_asked():
    document = Document()
    document.pages.add(PageSize.A4)
    document.close()
    with pytest.raises(Exception, match=r"(?i)dispos"):
        _ = document.fonts


def test_a_font_prints_as_a_line_worth_reading():
    document = Document()
    document.pages.add(PageSize.A4).add_text("a", 50, 700, font_name="Helvetica")
    with _reopen(document) as saved:
        text = str(saved.fonts[0])
        assert "Helvetica" in text
        assert "not embedded" in text
        assert "pages 1" in text


def test_the_same_face_embedded_twice_is_two_entries_until_optimized():
    """Which is the point: the inventory is what shows the duplication.

    Authoring with ``font=`` on several pages embeds the face once per page --
    the same subset tag, the same bytes, separate objects. They are separate
    programs, so they are separate entries, and the total is what the file
    really pays. ``optimize()`` folds them into one, and the inventory says so.
    """
    document = Document()
    for _ in range(4):
        page = document.pages.add(PageSize.A4)
        page.add_text("x", 50, 700, font=load_substitute_sfnt("sans-regular"))
    saved = _reopen(document)

    before = saved.fonts
    assert len(before) == 4
    assert len({f.base_font for f in before}) == 1, "the same face, four times"
    assert all(f.page_indexes != () for f in before)
    weight = sum(f.stored_bytes or 0 for f in before)

    saved.optimize()
    after = _reopen(saved)
    assert len(after.fonts) == 1
    assert after.fonts[0].page_indexes == (0, 1, 2, 3)
    assert sum(f.stored_bytes or 0 for f in after.fonts) < weight
    after.close()

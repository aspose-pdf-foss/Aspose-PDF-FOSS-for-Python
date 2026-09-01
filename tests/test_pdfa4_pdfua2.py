"""The PDF 2.0 conformance parts: ISO 19005-4 and ISO 14289-2.

Both are defined *on* PDF 2.0 rather than merely capped by it, which is the
first thing that separates them from their predecessors: an older header does
not make a conservative PDF/A-4 file, it makes a file claiming a specification
it does not follow.

PDF/A-4 also drops the accessible/basic/unicode split its earlier parts had.
There is no PDF/A-4a; the base level carries no conformance letter at all, and
the two variants are ``e`` (engineering — 3D and rich media, the reason the
level exists) and ``f`` (embedded files of any type). A new ``pdfaid:rev``
identifies the revision year.

PDF/UA-2 adds a ``pdfuaid:rev`` of its own and draws its structure element
types from the PDF 2.0 standard structure namespace rather than the unqualified
names part 1 used.
"""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.cos import PdfArray, PdfDictionary, PdfName, PdfNumber
from aspose_pdf.engine.simple_pdf import (
    PDF2_STRUCTURE_NS,
    PDFA_LEVELS,
    _make_pdfa_xmp,
    _make_pdfua_xmp,
)
from aspose_pdf.exceptions import PdfValidationException


def _document() -> Document:
    document = Document()
    page = document.pages.add()
    page.add_text("Body text", 60, 700, font_size=14)
    return document


def _reloaded(document: Document) -> Document:
    buffer = io.BytesIO()
    document.save(buffer)
    return Document(io.BytesIO(buffer.getvalue()))


def _as_pdfa(level: str, mutate=None) -> Document:
    document = _reloaded(_document())
    document.convert_to_pdfa(level)
    if mutate is not None:
        mutate(document._engine_pdf)
    return _reloaded(document)


def _as_pdfua(part: int, mutate=None) -> Document:
    document = _reloaded(_document())
    document.convert_to_pdfua(part=part, title="Doc", auto_tag=True)
    if mutate is not None:
        mutate(document._engine_pdf)
    return _reloaded(document)


def _bytes(document: Document) -> bytes:
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _errors(document: Document, level: str) -> list[str]:
    return list(document.validate_pdfa(level).errors)


def _with_attachment() -> Document:
    document = _document()
    document.add_attachment("notes.txt", b"hello", mime="text/plain")
    return _reloaded(document)


# ---------------------------------------------------------------------------
# The level table
# ---------------------------------------------------------------------------


def test_the_part_four_levels_are_the_ones_iso_19005_4_defines():
    assert PDFA_LEVELS["4"] == ("4", None)
    assert PDFA_LEVELS["4e"] == ("4", "E")
    assert PDFA_LEVELS["4f"] == ("4", "F")
    assert "4a" not in PDFA_LEVELS  # there is no accessible level in part 4
    assert "4b" not in PDFA_LEVELS
    assert "4u" not in PDFA_LEVELS


@pytest.mark.parametrize("level", ["99z", "4a", "5b", ""])
def test_a_level_that_does_not_exist_is_refused(level):
    # It used to fall back to PDF/A-1b, so asking for a level that does not
    # exist produced metadata claiming a different one.
    with pytest.raises(PdfValidationException, match="Unknown PDF/A level"):
        _make_pdfa_xmp(level, "Doc")


def test_the_long_form_of_a_level_is_accepted():
    assert b"<pdfaid:part>4</pdfaid:part>" in _make_pdfa_xmp("PDF/A-4e", "Doc")


# ---------------------------------------------------------------------------
# Identification metadata
# ---------------------------------------------------------------------------


def test_part_four_declares_a_revision_year():
    packet = _make_pdfa_xmp("4", "Doc")
    assert b"<pdfaid:rev>2020</pdfaid:rev>" in packet


def test_earlier_parts_declare_no_revision():
    # pdfaid:rev arrived with ISO 19005-4; a PDF/A-2 packet must not carry one.
    assert b"pdfaid:rev" not in _make_pdfa_xmp("2b", "Doc")


def test_the_base_level_declares_no_conformance_letter():
    assert b"pdfaid:conformance" not in _make_pdfa_xmp("4", "Doc")


@pytest.mark.parametrize(("level", "letter"), [("4e", b"E"), ("4f", b"F")])
def test_the_variants_declare_their_letter(level, letter):
    packet = _make_pdfa_xmp(level, "Doc")
    assert b"<pdfaid:conformance>" + letter + b"</pdfaid:conformance>" in packet


def test_a_conformance_letter_on_the_base_level_is_an_error():
    # The packet would be claiming PDF/A-4e or -4f rather than PDF/A-4.
    document = _as_pdfa("4e")
    assert any("declares no conformance level" in e for e in _errors(document, "4"))


def test_a_missing_conformance_letter_on_a_variant_is_an_error():
    document = _as_pdfa("4")
    assert any("must declare pdfaid:conformance" in e for e in _errors(document, "4e"))


def test_a_wrong_revision_year_is_an_error():
    def break_revision(engine):
        root = engine._resolve(engine._cos_doc.trailer.mapping[PdfName("Root")])
        metadata = engine._resolve(root.mapping[PdfName("Metadata")])
        metadata.content = metadata.content.replace(b">2020<", b">2005<")

    document = _as_pdfa("4", break_revision)
    assert any("pdfaid:rev = 2020" in e for e in _errors(document, "4"))


def test_a_converted_document_validates_at_the_level_it_was_converted_to():
    for level in ("4", "4e", "4f"):
        assert _errors(_as_pdfa(level), level) == []


# ---------------------------------------------------------------------------
# PDF/A-4 is PDF 2.0
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("level", "header"), [("2b", b"1.7"), ("4", b"2.0")])
def test_conversion_writes_the_header_the_part_is_defined_against(level, header):
    assert _bytes(_as_pdfa(level)).startswith(b"%PDF-" + header)


def test_conversion_raises_an_older_header_rather_than_leaving_it():
    # Parts 1-3 cap the version; part 4 *is* PDF 2.0, so 1.4 is not merely
    # conservative there -- it is wrong.
    document = _reloaded(_document())
    document._engine_pdf.pdf_version = "1.4"
    document.convert_to_pdfa("4")
    assert _bytes(document).startswith(b"%PDF-2.0")


def test_an_older_header_fails_validation_for_part_four():
    def downgrade(engine):
        engine.pdf_version = "1.7"

    errors = _errors(_as_pdfa("4", downgrade), "4")
    assert any("PDF/A-4 requires PDF version 2.0" in e for e in errors)


def test_part_two_still_only_caps_the_version():
    def upgrade(engine):
        engine.pdf_version = "1.4"

    assert _errors(_as_pdfa("2b", upgrade), "2b") == []


# ---------------------------------------------------------------------------
# PDF/A-4e: the level that exists for 3D
# ---------------------------------------------------------------------------


def _add_annotation(subtype: str):
    def mutate(engine):
        page = engine._get_page_dict(0)
        annot = PdfDictionary(
            {
                PdfName("Type"): PdfName("Annot"),
                PdfName("Subtype"): PdfName(subtype),
                PdfName("Rect"): PdfArray([PdfNumber(0)] * 4),
                PdfName("F"): PdfNumber(4),  # Print
                PdfName("AP"): PdfDictionary({PdfName("N"): PdfDictionary({})}),
            }
        )
        page.mapping[PdfName("Annots")] = PdfArray(
            [engine._cos_doc.register_object(annot)]
        )

    return mutate


@pytest.mark.parametrize("subtype", ["3D", "RichMedia"])
def test_the_engineering_level_permits_the_annotations_others_forbid(subtype):
    document = _as_pdfa("4e", _add_annotation(subtype))
    assert not any(subtype in e for e in _errors(document, "4e"))
    # ...and the same file is not conforming at the base level.
    assert any(subtype in e for e in _errors(document, "4"))


@pytest.mark.parametrize("subtype", ["Sound", "Movie", "Screen"])
def test_the_engineering_level_still_forbids_multimedia(subtype):
    document = _as_pdfa("4e", _add_annotation(subtype))
    assert any(subtype in e for e in _errors(document, "4e"))


# ---------------------------------------------------------------------------
# Embedded files
# ---------------------------------------------------------------------------


def _embedded_files(document: Document):
    engine = document._engine_pdf
    root = engine._resolve(engine._cos_doc.trailer.mapping[PdfName("Root")])
    names = engine._resolve(root.mapping.get(PdfName("Names")))
    if not isinstance(names, PdfDictionary):
        return None
    return engine._resolve(names.mapping.get(PdfName("EmbeddedFiles")))


def test_part_four_keeps_embedded_files_where_earlier_parts_dropped_them():
    kept = _with_attachment()
    kept.convert_to_pdfa("4")
    assert _embedded_files(kept) is not None
    assert "notes.txt" in kept._engine_pdf.attachments

    dropped = _with_attachment()
    dropped.convert_to_pdfa("2b")
    assert _embedded_files(dropped) is None
    assert not dropped._engine_pdf.attachments


def test_an_embedded_file_without_a_relationship_is_flagged_for_part_four():
    def strip_relationship(engine):
        from aspose_pdf.engine import conformance

        root = engine._resolve(engine._cos_doc.trailer.mapping[PdfName("Root")])
        names = engine._resolve(root.mapping[PdfName("Names")])
        tree = engine._resolve(names.mapping[PdfName("EmbeddedFiles")])
        for value in conformance._iter_name_tree_values(engine, tree, set(), 0):
            filespec = engine._resolve(value)
            filespec.mapping.pop(PdfName("AFRelationship"), None)

    document = _with_attachment()
    document.convert_to_pdfa("4")
    assert _errors(document, "4") == []  # the conversion stamps one on

    strip_relationship(document._engine_pdf)

    assert any("PDF/A-4 requires /AFRelationship" in e for e in _errors(document, "4"))


def test_part_three_may_carry_attachments_too():
    """The part that exists to carry attachments used to reject them.

    The guard compared the level against the literal ``"3"``, which no real
    level string ever equals, so ``"3b"`` fell on the prohibited side.
    """
    document = _with_attachment()
    document.convert_to_pdfa("3b")
    assert not any("embedded files" in e for e in _errors(document, "3b"))


@pytest.mark.parametrize("level", ["1b", "2b"])
def test_the_earlier_parts_still_prohibit_attachments(level):
    document = _with_attachment()
    # Converting would strip them; check the document as it stands instead.
    assert any("prohibit embedded files" in e for e in _errors(document, level))


# ---------------------------------------------------------------------------
# PDF/UA-2
# ---------------------------------------------------------------------------


def test_part_two_declares_its_part_and_revision():
    packet = _make_pdfua_xmp("Doc", 2)
    assert b"<pdfuaid:part>2</pdfuaid:part>" in packet
    assert b"<pdfuaid:rev>2024</pdfuaid:rev>" in packet


def test_part_one_declares_no_revision():
    packet = _make_pdfua_xmp("Doc", 1)
    assert b"<pdfuaid:part>1</pdfuaid:part>" in packet
    assert b"pdfuaid:rev" not in packet


@pytest.mark.parametrize("part", [0, 3, "2"])
def test_an_unknown_pdfua_part_is_refused(part):
    with pytest.raises(PdfValidationException, match="Unknown PDF/UA part"):
        _make_pdfua_xmp("Doc", part)


def test_converting_to_part_two_writes_pdf_two_zero():
    assert _bytes(_as_pdfua(2)).startswith(b"%PDF-2.0")


def test_converting_to_part_one_leaves_the_version_alone():
    assert _bytes(_as_pdfua(1)).startswith(b"%PDF-1.7")


def test_part_two_declares_the_standard_structure_namespace():
    data = _bytes(_as_pdfua(2))
    assert PDF2_STRUCTURE_NS.encode() in data
    assert b"/Namespaces" in data


def test_a_part_one_document_does_not_validate_as_part_two():
    errors = _as_pdfua(1).validate_pdfua(2).errors
    assert any("PDF/UA-2 requires PDF version 2.0" in e for e in errors)
    assert any("standard structure namespace" in e for e in errors)


def test_a_part_two_document_does_not_validate_as_part_one():
    errors = _as_pdfua(2).validate_pdfua(1).errors
    assert any("pdfuaid:part is '2'" in e for e in errors)


def test_a_missing_revision_is_flagged_for_part_two():
    def break_revision(engine):
        root = engine._resolve(engine._cos_doc.trailer.mapping[PdfName("Root")])
        metadata = engine._resolve(root.mapping[PdfName("Metadata")])
        metadata.content = metadata.content.replace(b">2024<", b">1999<")

    errors = _as_pdfua(2, break_revision).validate_pdfua(2).errors
    assert any("pdfuaid:rev = 2024" in e for e in errors)


def test_the_namespace_is_not_declared_twice_on_reconversion():
    document = _reloaded(_document())
    document.convert_to_pdfua(part=2, title="Doc")
    document.convert_to_pdfua(part=2, title="Doc")
    assert _bytes(document).count(PDF2_STRUCTURE_NS.encode()) == 1


def test_validate_pdfua_rejects_a_part_that_does_not_exist():
    with pytest.raises(PdfValidationException, match="Unknown PDF/UA part"):
        _reloaded(_document()).validate_pdfua(3)


def test_the_default_part_is_one():
    document = _as_pdfua(1)
    assert document.validate_pdfua().errors == document.validate_pdfua(1).errors

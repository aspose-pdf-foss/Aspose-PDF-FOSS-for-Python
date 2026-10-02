"""PDF/A-4 forbids the information dictionary that PDF/A-1 to -3 require.

ISO 19005-4:2020 clause 6.1.3, the two rules veraPDF enforces:

* test 4 -- "The Info key shall not be present in the trailer dictionary of
  PDF/A-4 conforming files unless there exists a PieceInfo entry in the document
  catalog dictionary";
* test 5 -- "If a document information dictionary is present, it shall only
  contain a ModDate entry".

Part 4 dropped the ``/Title`` requirement the earlier parts have and replaced it
with this prohibition. ``convert_to_pdfa`` did the opposite -- its step 2 is
"ensure /Info has a /Title", and it added one -- so **veraPDF rejected every
PDF/A-4 file this library produced**, while ``validate_pdfa("PDF/A-4")``
reported it valid: the validator applied the parts 1-3 title rule to part 4 and
implemented neither 6.1.3 test. Both halves are fixed, and veraPDF now passes
our PDF/A-4, -4E and -4F output along with 1B, 2B, 3B and 2A.

A part-4 document still names itself in the XMP packet's ``dc:title``, which is
where part 4 expects it; only the ``/Info`` dictionary goes.
"""

from __future__ import annotations

import io
import re

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.cos import PdfDictionary, PdfName

TRAILER_INFO = re.compile(rb"/Info \d+ 0 R")


def _converted(level: str, *, piece_info: bool = False, mod_date: str | None = None):
    document = Document()
    document.pages.add()
    document.pages[0].add_text(
        "Heading", 50, 720, tag="H1" if level.endswith("A") else None
    )
    document.info["Title"] = "A Title"
    if mod_date is not None:
        document.info["ModDate"] = mod_date
    if piece_info:
        engine = document._engine_pdf
        engine._ensure_cos()
        root = engine._resolve(engine._cos_doc.trailer.mapping.get(PdfName("Root")))
        root.mapping[PdfName("PieceInfo")] = PdfDictionary(
            {PdfName("Private"): PdfDictionary({})}
        )
    document.convert_to_pdfa(level)
    buffer = io.BytesIO()
    document.save(buffer)
    return document, buffer.getvalue()


@pytest.mark.parametrize("level", ["PDF/A-4", "PDF/A-4E", "PDF/A-4F"])
def test_part_four_writes_no_info_dictionary(level):
    _, data = _converted(level)
    assert not TRAILER_INFO.search(data)
    assert b"/Title" not in data


@pytest.mark.parametrize("level", ["PDF/A-1B", "PDF/A-2B", "PDF/A-3B", "PDF/A-2A"])
def test_the_earlier_parts_still_require_a_title(level):
    _, data = _converted(level)
    assert TRAILER_INFO.search(data)
    assert b"/Title" in data


@pytest.mark.parametrize("level", ["PDF/A-4", "PDF/A-4E"])
def test_part_four_still_names_itself_in_the_xmp(level):
    _, data = _converted(level)
    assert b"dc:title" in data


def test_a_mod_date_alone_is_not_enough_without_a_piece_info():
    """/ModDate is only allowed *because* /PieceInfo is; without one it goes too.

    Test 4 is about the ``Info`` key existing at all, so a document carrying
    nothing but a ``/ModDate`` is still non-conformant unless the catalog has a
    ``/PieceInfo``. Worth its own case: with no date in the document the
    dictionary disappears for want of content rather than by this rule.
    """
    document, data = _converted("PDF/A-4", mod_date="D:20260101000000Z")
    assert not TRAILER_INFO.search(data)
    assert b"ModDate" not in data
    assert document.validate_pdfa("PDF/A-4").is_valid


def test_an_entry_the_view_cannot_see_is_dropped_by_the_conversion():
    """The trailer key goes, which is what takes a non-text entry with it.

    ``doc.info`` cannot see an ``/Info`` value with no text form, so clearing
    the view does not remove it -- the conversion drops the trailer key as well.
    veraPDF passes the result; the detached dictionary stays in the body as an
    unreferenced object, which no conformance rule is about.
    """
    from aspose_pdf.engine.cos import PdfNumber

    document = Document()
    document.pages.add()
    document.pages[0].add_text("Heading", 50, 720)
    engine = document._engine_pdf
    engine._ensure_cos()
    engine._cos_doc.trailer.mapping[PdfName("Info")] = engine._cos_doc.register_object(
        PdfDictionary({PdfName("Revision"): PdfNumber(7)})
    )
    assert "Revision" not in document.info          # invisible to the view

    document.convert_to_pdfa("PDF/A-4")
    buffer = io.BytesIO()
    document.save(buffer)
    assert not TRAILER_INFO.search(buffer.getvalue())
    assert document.validate_pdfa("PDF/A-4").is_valid


def test_the_validator_sees_an_entry_only_the_cos_graph_holds():
    """An /Info value with no text form never appears in ``doc.info``.

    ``_drop_removed_info_entries`` keeps such an entry on purpose, so it would
    survive into a part-4 file. The check reads the graph as well as the pending
    view for exactly this.
    """
    from aspose_pdf.engine.cos import PdfNumber

    document = Document()
    document.pages.add()
    document.pages[0].add_text("Heading", 50, 720)
    document.convert_to_pdfa("PDF/A-4")

    engine = document._engine_pdf
    info = PdfDictionary({PdfName("Revision"): PdfNumber(7)})
    engine._cos_doc.trailer.mapping[PdfName("Info")] = engine._cos_doc.register_object(
        info
    )
    assert "Revision" not in document.info          # invisible to the view

    result = document.validate_pdfa("PDF/A-4")
    assert result.is_valid is False
    assert any("6.1.3" in str(e) for e in result.errors), result.errors


def test_a_piece_info_lets_mod_date_stay():
    # The one case where part 4 allows /Info, and then only /ModDate.
    document, data = _converted(
        "PDF/A-4", piece_info=True, mod_date="D:20260101000000Z"
    )
    assert TRAILER_INFO.search(data)
    assert b"ModDate" in data
    assert b"/Title" not in data
    assert document.validate_pdfa("PDF/A-4").is_valid


@pytest.mark.parametrize(
    "level", ["PDF/A-1B", "PDF/A-2B", "PDF/A-3B", "PDF/A-2A", "PDF/A-4", "PDF/A-4E"]
)
def test_our_validator_accepts_what_we_convert(level):
    document, _ = _converted(level)
    assert document.validate_pdfa(level).is_valid


def test_the_validator_rejects_an_info_dictionary_on_part_four():
    # The other half: a part-4 file that carries /Info used to pass.
    document = Document()
    document.pages.add()
    document.pages[0].add_text("Heading", 50, 720)
    document.convert_to_pdfa("PDF/A-4")
    document.info["Title"] = "Put back after converting"

    result = document.validate_pdfa("PDF/A-4")
    assert result.is_valid is False
    assert any("6.1.3" in str(e) for e in result.errors), result.errors


def test_the_validator_rejects_more_than_mod_date_with_a_piece_info():
    document, _ = _converted(
        "PDF/A-4", piece_info=True, mod_date="D:20260101000000Z"
    )
    document.info["Subject"] = "not allowed here"
    result = document.validate_pdfa("PDF/A-4")
    assert result.is_valid is False
    assert any("only /ModDate" in str(e) for e in result.errors), result.errors


def test_the_title_rule_still_bites_on_the_earlier_parts():
    document = Document()
    document.pages.add()
    document.pages[0].add_text("Heading", 50, 720)
    document.convert_to_pdfa("PDF/A-2B")
    document.info.pop("Title", None)
    result = document.validate_pdfa("PDF/A-2B")
    assert result.is_valid is False
    assert any("Title" in str(e) for e in result.errors), result.errors

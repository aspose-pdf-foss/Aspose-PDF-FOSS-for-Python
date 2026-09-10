"""Tests for the ``/Info`` entry contract: names and text strings, nothing else.

The document information dictionary maps names to text strings (ISO 32000-1
14.3.3). Anything else used to travel all the way down to the COS layer and
die there with ``PdfString value must be bytes or str``, at save time, naming
neither the entry nor the expectation. These tests pin the refusal -- and, for
a date, pin that the formatting it suggests actually works.
"""

from __future__ import annotations

import datetime
import io
import re

import pytest

from aspose_pdf.document import Document
from aspose_pdf.exceptions import PdfValidationException


def _doc(**info) -> Document:
    doc = Document()
    doc.pages.add()
    doc.info.update(info)
    return doc


def _save_reload(doc: Document, tmp_path) -> Document:
    path = tmp_path / "info.pdf"
    doc.save(str(path))
    return Document(str(path))


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "type_name"),
    [
        (datetime.datetime(2026, 9, 10, 8, 30, 15), "datetime"),
        (datetime.date(2026, 9, 10), "date"),
        (3, "int"),
        (None, "NoneType"),
        (["a", "b"], "list"),
        (1.5, "float"),
    ],
)
def test_a_non_string_value_is_refused_naming_entry_and_type(value, type_name):
    doc = _doc(Subject=value)
    with pytest.raises(PdfValidationException) as excinfo:
        doc.save(io.BytesIO())
    message = str(excinfo.value)
    assert "'Subject'" in message
    assert type_name in message


def test_the_refusal_names_the_offending_entry_not_a_neighbour():
    doc = _doc(Title="fine", Keywords=42)
    with pytest.raises(PdfValidationException) as excinfo:
        doc.save(io.BytesIO())
    assert "'Keywords'" in str(excinfo.value)
    assert "'Title'" not in str(excinfo.value)


@pytest.mark.parametrize("key", [7, b"Title", None, ("Title",)])
def test_a_non_string_key_is_refused(key):
    doc = Document()
    doc.pages.add()
    doc.info[key] = "text"
    with pytest.raises(PdfValidationException) as excinfo:
        doc.save(io.BytesIO())
    message = str(excinfo.value)
    assert "key" in message
    assert type(key).__name__ in message


# ---------------------------------------------------------------------------
# The date hint, and whether it tells the truth
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value", [datetime.datetime(2026, 9, 10, 8, 30, 15), datetime.date(2026, 9, 10)]
)
def test_a_date_like_value_is_told_how_a_pdf_date_is_written(value):
    doc = _doc(CreationDate=value)
    with pytest.raises(PdfValidationException) as excinfo:
        doc.save(io.BytesIO())
    assert "strftime" in str(excinfo.value)


@pytest.mark.parametrize("value", [3, None, ["a"], {"a": 1}])
def test_a_value_that_is_not_date_like_gets_no_date_hint(value):
    doc = _doc(ModDate=value)
    with pytest.raises(PdfValidationException) as excinfo:
        doc.save(io.BytesIO())
    assert "strftime" not in str(excinfo.value)


def test_the_suggested_format_produces_an_accepted_pdf_date(tmp_path):
    """Dogfood the hint: take the format out of the message and use it."""
    when = datetime.datetime(2026, 9, 10, 8, 30, 15)
    with pytest.raises(PdfValidationException) as excinfo:
        _doc(CreationDate=when).save(io.BytesIO())
    match = re.search(r'strftime\("(.+?)"\)', str(excinfo.value))
    assert match, f"no quoted format in {excinfo.value}"

    doc = _doc(CreationDate=when.strftime(match.group(1)))
    reloaded = _save_reload(doc, tmp_path)
    assert reloaded.info["CreationDate"] == "D:20260910083015+00'00'"


# ---------------------------------------------------------------------------
# What the contract does accept
# ---------------------------------------------------------------------------


def test_strings_and_bytes_are_accepted_and_round_trip(tmp_path):
    doc = _doc(Title="a title", Author=b"a byte author")
    reloaded = _save_reload(doc, tmp_path)
    assert reloaded.info["Title"] == "a title"
    assert reloaded.info["Author"] == "a byte author"


def test_a_bytearray_is_accepted(tmp_path):
    doc = _doc(Creator=bytearray(b"tool"))
    reloaded = _save_reload(doc, tmp_path)
    assert reloaded.info["Creator"] == "tool"


def test_unicode_and_pdf_dates_survive_the_round_trip(tmp_path):
    doc = _doc(
        Title="Tāraṇa — em–dash ✓",
        CreationDate="D:20260910083015+02'00'",
    )
    reloaded = _save_reload(doc, tmp_path)
    assert reloaded.info["Title"] == "Tāraṇa — em–dash ✓"
    assert reloaded.info["CreationDate"] == "D:20260910083015+02'00'"


def test_an_empty_string_is_a_string(tmp_path):
    doc = _doc(Title="", Subject="kept")
    reloaded = _save_reload(doc, tmp_path)
    assert reloaded.info.get("Title", "") == ""
    assert reloaded.info["Subject"] == "kept"


# ---------------------------------------------------------------------------
# /Trapped is a name, and the types a producer's own entries carry
# ---------------------------------------------------------------------------


def _pdf_with_typed_info() -> bytes:
    """A file whose ``/Info`` carries a name, a number, a boolean and an array.

    Hand-built rather than produced by this library: the point is to load types
    that ``doc.info`` cannot set, so writing the fixture with our own writer
    would test nothing. 14.3.3 types ``/Trapped`` as a name and lets a producer
    add entries of its own of any type.
    """
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>",
        b"<< /Title (t) /Trapped /True /Revision 7 /Approved true "
        b"/Tags [(a) (b)] >>",
    ]
    out = bytearray(b"%PDF-1.7\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += (
        b"trailer\n<< /Size %d /Root 1 0 R /Info 4 0 R >>\nstartxref\n%d\n%%%%EOF\n"
        % (len(objects) + 1, xref_at)
    )
    return bytes(out)


def _typed_info_doc() -> Document:
    doc = Document()
    doc.load_from(io.BytesIO(_pdf_with_typed_info()))
    return doc


def test_info_renders_each_type_as_text_never_as_a_repr():
    info = _typed_info_doc().info
    assert info["Trapped"] == "True"  # not "PdfName(/True)"
    assert info["Approved"] == "true"
    assert info["Revision"] == "7"
    assert info["Title"] == "t"
    assert not any("Pdf" in value for value in info.values())


def test_an_entry_with_no_faithful_text_is_not_shown_as_one():
    """An array has no text rendering; it stays in the file instead."""
    assert "Tags" not in _typed_info_doc().info


def test_a_save_that_touches_nothing_keeps_every_info_type():
    doc = _typed_info_doc()
    buffer = io.BytesIO()
    doc.save(buffer)
    written = buffer.getvalue()

    assert b"/Trapped /True" in written
    assert b"/Revision 7" in written
    assert b"/Approved true" in written
    assert b"/Tags [ (a) (b) ]" in written
    assert b"PdfName" not in written
    assert b"(True)" not in written


@pytest.mark.parametrize(
    ("value", "written"),
    [
        ("True", b"/Trapped /True"),
        ("true", b"/Trapped /True"),
        ("/True", b"/Trapped /True"),
        ("False", b"/Trapped /False"),
        ("/false", b"/Trapped /False"),
        ("Unknown", b"/Trapped /Unknown"),
        ("", b"/Trapped /Unknown"),
        (b"True", b"/Trapped /True"),
    ],
)
def test_trapped_is_written_as_the_name_a_reader_compares_against(value, written):
    doc = _doc(Trapped=value)
    buffer = io.BytesIO()
    doc.save(buffer)
    assert written in buffer.getvalue()


def test_a_trapped_value_outside_the_three_is_kept_as_a_name():
    """Still the right type; what the file says is not ours to reinterpret."""
    doc = _doc(Trapped="Partly")
    buffer = io.BytesIO()
    doc.save(buffer)
    assert b"/Trapped /Partly" in buffer.getvalue()


def test_trapped_round_trips_without_its_slash(tmp_path):
    reloaded = _save_reload(_doc(Trapped="True"), tmp_path)
    assert reloaded.info["Trapped"] == "True"


def test_changing_a_loaded_trapped_still_writes_a_name():
    doc = _typed_info_doc()
    doc.info["Trapped"] = "False"
    buffer = io.BytesIO()
    doc.save(buffer)
    assert b"/Trapped /False" in buffer.getvalue()


def test_changing_a_producers_own_entry_writes_it_as_the_text_it_now_is():
    """The deliberate half: a value set through a text API is text.

    Preserved only while untouched -- guessing a number back out of a string
    is the invention this boundary exists to refuse.
    """
    doc = _typed_info_doc()
    doc.info["Revision"] = "8"
    buffer = io.BytesIO()
    doc.save(buffer)
    assert b"/Revision (8)" in buffer.getvalue()


@pytest.mark.parametrize("value", [True, False, 1, None])
def test_a_non_string_trapped_is_told_the_three_names(value):
    doc = _doc(Trapped=value)
    with pytest.raises(PdfValidationException) as excinfo:
        doc.save(io.BytesIO())
    message = str(excinfo.value)
    assert "'Trapped'" in message
    assert '"True"' in message and '"Unknown"' in message


def test_the_trapped_hint_does_not_leak_onto_other_entries():
    doc = _doc(Title=True)
    with pytest.raises(PdfValidationException) as excinfo:
        doc.save(io.BytesIO())
    assert "Trapped" not in str(excinfo.value)

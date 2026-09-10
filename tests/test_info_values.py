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

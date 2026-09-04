"""A number is written in the only notation PDF has.

ISO 32000-1 7.3.3: a real is decimal digits with an optional sign and a period,
and exponential notation is *not permitted*. Python writes small and large
floats as ``1e-05`` and ``1.5e+20``, and the COS writer handed those straight
to the file -- where ``e`` is not part of a number but the start of a keyword,
so the document stopped parsing there. Setting a crop box a little too small was
enough to produce a file this library could not reload.

Two more things a number cannot be. An infinity or a NaN has no decimal form at
all, and was written as the word Python names it by. And a whole number written
without a period is an *integer*, which is guaranteed only to +/-2,147,483,647
(annex C.1): past that, qpdf resolves the object holding it to null, so a large
coordinate silently deleted the annotation it belonged to.

Content streams had the rule right all along, in their own function. Both paths
share it now, so a coordinate is spelled the same way wherever it lands.
"""

from __future__ import annotations

import io
import re

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.cos import format_pdf_number
from aspose_pdf.exceptions import PdfValidationException

EXPONENT = re.compile(rb"[0-9]e[-+][0-9]", re.IGNORECASE)


def _saved(document: Document) -> bytes:
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _reloaded(document: Document) -> Document:
    return Document(io.BytesIO(_saved(document)))


# ---------------------------------------------------------------------------
# The notation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "token"),
    [
        (0, "0"),
        (1, "1"),
        (-1, "-1"),
        (0.5, "0.5"),
        (3.0, "3"),
        (-0.0, "0"),
        (0.1 + 0.2, "0.3"),
        (1e-5, "0.00001"),
        (1e-7, "0"),  # below what six decimal places can hold
        (-1e-7, "0"),  # and it is zero, not the "-0" rounding would leave
        (300.123456789, "300.123457"),
        (2147483647, "2147483647"),
    ],
)
def test_a_number_is_written_in_decimal(value, token):
    assert format_pdf_number(value) == token


@pytest.mark.parametrize("value", [1e-5, 1.5e20, 1e300, -1e-9, 12345.6789e10])
def test_no_number_is_written_with_an_exponent(value):
    assert "e" not in format_pdf_number(value).lower()


@pytest.mark.parametrize(
    ("value", "token"),
    [
        (2147483648, "2147483648.0"),
        (-2147483648, "-2147483648.0"),
        (1.5e20, "150000000000000000000.0"),
        (99999999999999999999, "99999999999999999999.0"),
    ],
)
def test_a_whole_number_too_large_to_be_an_integer_keeps_its_fraction(value, token):
    """Without the period it is an integer token, and a reader holding those in
    a fixed-width type resolves the object to null instead."""
    assert format_pdf_number(value) == token


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
def test_an_infinity_or_a_nan_is_refused(value):
    with pytest.raises(PdfValidationException, match="infinity or a NaN"):
        format_pdf_number(value)


@pytest.mark.parametrize("value", [True, False])
def test_a_boolean_is_not_a_number(value):
    with pytest.raises(PdfValidationException, match="not booleans"):
        format_pdf_number(value)


@pytest.mark.parametrize("value", ["abc", None, object()])
def test_something_that_is_not_a_number_at_all_is_refused(value):
    with pytest.raises(PdfValidationException, match="must be numbers"):
        format_pdf_number(value)


# ---------------------------------------------------------------------------
# Through the document
# ---------------------------------------------------------------------------


def test_a_crop_box_a_little_too_small_still_produces_a_readable_file():
    document = Document()
    document.pages.add()
    document.pages[0].crop_box = (0, 0, 1e-5, 300.123456789)

    data = _saved(document)

    assert not EXPONENT.search(data)
    assert Document(io.BytesIO(data)).pages[0].crop_box == (0.0, 0.0, 1e-05, 300.123457)


@pytest.mark.parametrize(
    "build",
    [
        lambda d: d.pages[0].annotations.add("Text", (1e-5, 1e-5, 40, 40), "x"),
        lambda d: setattr(d.pages[0], "crop_box", (0, 0, 1e-5, 1e-5)),
        lambda d: d.pages[0].annotations.add(
            "Text", (10, 10, 40, 40), "x", properties={"Zz": 1e-5}
        ),
        lambda d: d.pages[0].annotations.add("Text", (0, 0, 1.5e20, 1.5e20), "x"),
    ],
    ids=["annot-rect", "crop-box", "annot-property", "huge-rect"],
)
def test_no_public_entry_point_writes_an_exponent(build):
    document = Document()
    document.pages.add()
    build(document)

    assert not EXPONENT.search(_saved(document))


def test_a_huge_coordinate_does_not_delete_the_annotation_it_belongs_to():
    """`150000000000000000000` is out of an integer's range; written that way,
    a reader resolves the whole annotation object to null."""
    document = Document()
    document.pages.add()
    document.pages[0].annotations.add("Text", (0, 0, 1.5e20, 40), "x")

    reloaded = _reloaded(document)

    assert [a.contents for a in reloaded.pages[0].annotations] == ["x"]
    assert b"150000000000000000000.0" in _saved(document)


def test_a_nan_coordinate_is_refused_rather_than_written():
    document = Document()
    document.pages.add()

    with pytest.raises(PdfValidationException, match="infinity or a NaN"):
        document.pages[0].annotations.add(
            "Text", (10, 10, 40, 40), "x", properties={"Zz": float("nan")}
        )
        _saved(document)


def test_the_legacy_writer_follows_the_same_rule():
    """It builds its page dictionary as text, so it formats its own numbers."""
    from aspose_pdf.engine.simple_pdf import SimplePdf

    engine = SimplePdf()
    engine.pages = [(0, 0, 1e-7, 1.5e20)]
    engine.page_contents = [b""]

    data = engine.to_bytes()

    assert not EXPONENT.search(data)
    assert len(Document(io.BytesIO(data)).pages) == 1


def test_a_content_stream_and_a_cos_entry_spell_a_number_the_same_way():
    document = Document()
    document.pages.add()
    document.pages[0].draw_rectangle(10.5, 20.25, 30.125, 40.0)
    document.pages[0].annotations.add("Text", (10.5, 20.25, 30.125, 40.0), "x")

    data = _saved(document)

    assert b"10.5" in data and b"20.25" in data and b"30.125" in data

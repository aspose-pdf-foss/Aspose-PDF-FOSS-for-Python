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
import zlib

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


# --- reading: every spelling 7.3.3 allows ------------------------------------
#
# The same clause gives "-.002" among its own examples of a real, and MuPDF and
# Ghostscript both write a real that starts with its period. The COS tokenizer
# dispatched on a digit or a sign and nothing else, so one such number --
# `/MK << /BG [1 1 .8] >>` in a form MuPDF wrote -- made the whole document fail
# to open. And a number with nothing after it, which is how the last member of
# an object stream ends, sent the reference lookahead past the end.


def _read(text: str):
    from aspose_pdf.engine.pdf_parser_cos import _Tokenizer

    return _Tokenizer(text).read()


@pytest.mark.parametrize(
    ("text", "value"),
    [(".5", 0.5), ("-.002", -0.002), ("+.75", 0.75), ("4.", 4.0), ("-4.", -4.0), (".0", 0.0)],
)
def test_every_spelling_of_a_real_is_read(text, value):
    assert _read(text).value == value


def test_a_leading_period_real_inside_an_array_and_a_dictionary():
    from aspose_pdf.engine.cos import PdfName

    array = _read("[1 .5 -.25 +.75]")
    assert [item.value for item in array.items] == [1, 0.5, -0.25, 0.75]
    colours = _read("<< /BG [1 1 .8] >>")
    assert [item.value for item in colours[PdfName("BG")].items] == [1, 1, 0.8]


def test_a_reference_followed_by_a_leading_period_real():
    from aspose_pdf.engine.cos import PdfIndirectReference

    first, second = _read("[1 0 R .5]").items
    assert isinstance(first, PdfIndirectReference)
    assert second.value == 0.5


@pytest.mark.parametrize("text", ["12", "12 ", "-.5", "7 0"])
def test_a_number_with_nothing_after_it(text):
    assert _read(text).value == float(text.split()[0])


def _objstm_file(last_member: bytes) -> bytes:
    """An object stream whose final member is *last_member*, nothing after it."""
    import struct

    m5 = b"<< /Answer 6 0 R >>"
    header = b"5 0 6 %d " % (len(m5) + 1)
    body = zlib.compress(header + m5 + b" " + last_member)
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R /Extra 5 0 R >>",
        2: b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
        3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 50 50] >>",
        4: b"<< /Type /ObjStm /N 2 /First %d /Filter /FlateDecode /Length %d >>\n"
        b"stream\n" % (len(header), len(body))
        + body
        + b"\nendstream",
    }
    out = bytearray(b"%PDF-1.7\n")
    offsets = {}
    for number in sorted(objects):
        offsets[number] = len(out)
        out += b"%d 0 obj\n" % number + objects[number] + b"\nendobj\n"
    rows = [(0, 0, 65535)] + [(1, offsets[n], 0) for n in (1, 2, 3, 4)]
    rows += [(2, 4, 0), (2, 4, 1)]
    xref_at = len(out)
    rows.append((1, xref_at, 0))
    table = zlib.compress(b"".join(struct.pack(">BIH", *row) for row in rows))
    out += (
        b"7 0 obj\n<< /Type /XRef /Size 8 /W [1 4 2] /Root 1 0 R /Filter "
        b"/FlateDecode /Length %d >>\nstream\n" % len(table)
        + table
        + b"\nendstream\nendobj\nstartxref\n%d\n%%%%EOF\n" % xref_at
    )
    return bytes(out)


@pytest.mark.parametrize(("member", "value"), [(b"42", 42), (b".5", 0.5)])
def test_a_number_that_ends_an_object_stream_is_read(member, value):
    # qpdf reads both; a stream's /Length is the usual number stored this way.
    from aspose_pdf.engine.cos import PdfName

    document = Document(io.BytesIO(_objstm_file(member)))
    engine = document._engine_pdf
    root = engine._resolve(engine._cos_doc.trailer.mapping[PdfName("Root")])
    extra = engine._resolve(root.mapping[PdfName("Extra")])
    assert engine._resolve(extra.mapping[PdfName("Answer")]).value == value


def test_a_document_with_a_leading_period_real_opens():
    content = b"0 0 1 rg 0 0 10 10 re f"
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
        3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 50 50] /UserUnit 1 "
        b"/Annots [5 0 R] /Contents 4 0 R >>",
        4: b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content),
        5: b"<< /Type /Annot /Subtype /Square /Rect [5 5 20 20] /C [0 0 .8] >>",
    }
    out = bytearray(b"%PDF-1.7\n")
    offsets = {}
    for number in sorted(objects):
        offsets[number] = len(out)
        out += b"%d 0 obj\n" % number + objects[number] + b"\nendobj\n"
    start = len(out)
    out += b"xref\n0 6\n0000000000 65535 f \n"
    out += b"".join(b"%010d 00000 n \n" % offsets[n] for n in sorted(objects))
    out += b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % start
    document = Document(io.BytesIO(bytes(out)))
    assert len(document.pages) == 1

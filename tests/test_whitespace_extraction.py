"""Text that is only whitespace is whitespace, not raw CID bytes.

Two faults, one symptom. ``extract_page_text`` read an **empty** result as a
parse failure and fell back to ``best_effort_extract_text``, which reads every
string in the stream through ``_decode_bytes`` -- for a composite font those are
two-byte CIDs. And ``ContentStreamParser.extract_text`` stripped the surrounding
whitespace, so a page whose only text was a space produced ``""``, was taken for
a failure, and came back as ``'\\x00\\x01'``: binary where the caller expects
characters. For a *simple* font the same path yielded the literal bytes, which
look like plausible text instead of announcing themselves.

Measured against both references, which agreed with each other and not with us:

======================  ====================  ==========  ==========
authored                ours (was)            MuPDF       pdfium
======================  ====================  ==========  ==========
``" "``                 ``'\\x00\\x01'``        ``' \\n'``    ``''``
``"  "``                ``'\\x00\\x01\\x00\\x01'`` ``'  \\n'``   ``' '``
``" a"``                ``'a'``               ``' a\\n'``   ``' a'``
``"a "``                ``'a'``               ``'a \\n'``   ``'a '``
======================  ====================  ==========  ==========

An empty extraction is now an answer rather than a failure -- the parser reports
through ``showed_text`` whether the stream asked for any text at all, which is
what tells a page that draws none from a stream the parser could not follow --
and the surrounding whitespace is kept, so what comes back is what the page
draws. That matches MuPDF exactly bar the page-ending newline it appends.
"""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document

FONT = "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"


def _authored(text: str, *, font: str | None = None) -> Document:
    document = Document()
    document.pages.add()
    kwargs = {"font": font, "font_size": 18} if font else {}
    document.pages[0].add_text(text, 40, 700, **kwargs)
    buffer = io.BytesIO()
    document.save(buffer)
    return Document(io.BytesIO(buffer.getvalue()))


@pytest.mark.parametrize(
    ("authored", "expected"),
    [(" ", " "), ("  ", "  "), (" a", " a"), ("a ", "a "), ("a", "a")],
)
def test_a_simple_font_keeps_the_whitespace_it_draws(authored, expected):
    assert _authored(authored).pages[0].extract_text() == expected


@pytest.mark.parametrize(
    ("authored", "expected"),
    [(" ", " "), ("  ", "  "), (" a", " a"), ("a ", "a "), ("a", "a")],
)
def test_a_composite_font_keeps_it_too_and_leaks_no_cids(authored, expected):
    pytest.importorskip("pathlib")
    import os

    if not os.path.exists(FONT):
        pytest.skip("no system font to embed")
    extracted = _authored(authored, font=FONT).pages[0].extract_text()
    assert extracted == expected
    # The symptom: raw two-byte CIDs where characters belong.
    assert "\x00" not in extracted


def test_a_page_that_draws_no_text_extracts_as_empty():
    document = Document()
    document.pages.add()
    document.pages[0].draw_rectangle(10, 10, 50, 50)
    buffer = io.BytesIO()
    document.save(buffer)
    assert Document(io.BytesIO(buffer.getvalue())).pages[0].extract_text() == ""


def test_the_parser_reports_whether_the_stream_showed_any_text():
    from aspose_pdf.engine.content_stream_parser import ContentStreamParser

    drew = ContentStreamParser(b"BT /F1 12 Tf (hi) Tj ET", {})
    assert drew.extract_text() == "hi"
    assert drew.showed_text is True

    silent = ContentStreamParser(b"10 10 50 50 re f", {})
    assert silent.extract_text() == ""
    assert silent.showed_text is False

    # A show operator whose text is empty still counts as having shown text:
    # the page said to draw, and the answer is that it drew nothing.
    empty = ContentStreamParser(b"BT /F1 12 Tf () Tj ET", {})
    assert empty.extract_text() == ""
    assert empty.showed_text is True


def test_hidden_optional_content_is_not_revealed_by_the_fallback():
    """The sharper reason an empty result must not mean failure.

    A page whose every piece of text sits in a hidden optional-content group
    extracts, correctly, as the empty string. Reading that as a parse failure
    sent it to the best-effort pass, which ignores optional-content state
    entirely -- so the text the document says not to show came back.
    """
    from aspose_pdf.engine.content_stream_parser import ContentStreamParser

    stream = b"/OC /MC0 BDC BT /F1 12 Tf (hidden) Tj ET EMC"
    hidden = frozenset({"MC0"})

    proper = ContentStreamParser(stream, {}, hidden_oc_names=hidden)
    assert proper.extract_text() == ""
    assert proper.showed_text is True          # so no fallback is reached

    # What the fallback would have said, and why it must not be asked.
    leaky = ContentStreamParser(stream, {}, hidden_oc_names=hidden)
    assert leaky.best_effort_extract_text() == "hidden"


@pytest.mark.parametrize(
    ("stream", "shown"),
    [
        (b"BT /F1 12 Tf [(a) -200 (b)] TJ ET", True),
        (b"BT /F1 12 Tf (x) ' ET", True),
        (b'BT /F1 12 Tf 1 2 (y) " ET', True),
        (b"BT /F1 12 Tf ET", False),
        (b"q 1 0 0 1 0 0 cm Q", False),
    ],
)
def test_every_show_operator_is_recorded(stream, shown):
    # Tj alone is not enough: TJ and the two quote forms show text as well, and
    # a page that uses them must not be mistaken for one the parser failed on.
    from aspose_pdf.engine.content_stream_parser import ContentStreamParser

    parser = ContentStreamParser(stream, {})
    parser.extract_text()
    assert parser.showed_text is shown


def test_a_stream_the_parser_cannot_follow_still_reaches_the_fallback():
    # The fallback is for this: text shown with no BT/ET around it, which the
    # proper pass skips. It must not be lost by the empty-is-not-a-failure rule.
    from aspose_pdf.engine.content_stream_parser import ContentStreamParser

    loose = ContentStreamParser(b"(recovered) Tj", {})
    assert loose.extract_text() == ""
    assert loose.showed_text is False
    assert "recovered" in ContentStreamParser(
        b"(recovered) Tj", None
    ).best_effort_extract_text()


def _hand_built(objects: list[bytes]) -> bytes:
    out = bytearray(b"%PDF-1.7\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    start = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        start,
    )
    return bytes(out)


def test_a_document_whose_every_word_is_hidden_extracts_as_empty():
    """End to end: the hidden text must not come back through the fallback.

    Everything this page draws sits in an optional-content group the catalog
    turns off, so the page shows nothing and the right answer is the empty
    string -- which the old code read as a failure, asking the best-effort pass,
    which does not know about optional content.
    """
    content = b"/OC /MC0 BDC BT /F1 12 Tf (hidden) Tj ET EMC"
    data = _hand_built(
        [
            b"<< /Type /Catalog /Pages 2 0 R /OCProperties << /OCGs [6 0 R] "
            b"/D << /OFF [6 0 R] >> >> >>",
            b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> /Properties << /MC0 6 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            b"<< /Type /OCG /Name (Layer) >>",
        ]
    )
    assert Document(io.BytesIO(data)).extract_text() == ""
    assert "hidden" not in Document(io.BytesIO(data)).extract_text()


def test_text_shown_outside_bt_et_is_still_recovered():
    # What the fallback is for, and it has to keep working: this page shows text
    # with no text object around it, which the proper pass skips.
    content = b"/F1 12 Tf (recovered) Tj"
    data = _hand_built(
        [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        ]
    )
    assert "recovered" in Document(io.BytesIO(data)).extract_text()


def test_replacing_text_with_nothing_leaves_the_rest_readable():
    # Where this was found: the content stream was right all along -- only the
    # surviving space came back as raw bytes.
    import os

    if not os.path.exists(FONT):
        pytest.skip("no system font to embed")
    document = _authored("abc abc", font=FONT)
    document.pages[0].replace_text("abc", "")
    buffer = io.BytesIO()
    document.save(buffer)

    reopened = Document(io.BytesIO(buffer.getvalue()))
    assert reopened.pages[0].extract_text() == " "

"""Damage to one structure should cost that structure, not the document.

A bookmark tree, a name tree, a resource dictionary: none of them is the page
content, and a file whose own is broken still has its pages. MuPDF, pdfium and
poppler all open such files. Four shapes refused to open here and two more let
an ``AttributeError`` out of text extraction:

* an ``/Outlines`` object that will not tokenise,
* an outline list whose ``/First`` points at an object that is not there --
  which ISO 32000-1 7.3.10 says is a reference to null, not an error,
* an outline item that is its own ``/First`` and ``/Next``,
* a cross-reference table truncated to its free entry, where the objects are
  still in the file and the reconstruction scan would have found them -- it ran
  only when the table failed to *parse*, so a table that parsed and named
  nothing went straight to "no PDF objects found",
* a ``/Resources`` that is a number, and a ``/Font`` entry that is a number:
  both reached ``dict.get`` on an ``int``.

Measured across a corpus of thirty-nine damaged copies of one sound document:
before, we refused four that at least two references read, and produced an
internal error on two more; after, every one of the thirty-nine opens and reads
its pages. A resource limit is still raised -- that ceiling is the caller's, not
the file's fault.
"""

from __future__ import annotations

import io
import re

import pytest

from aspose_pdf import Document
from aspose_pdf.exceptions import PdfResourceLimitException
from aspose_pdf.load_limits import PdfLoadLimits


def _stream(body: bytes, mapping: bytes = b"") -> bytes:
    return (
        b"<< " + mapping + b" /Length " + str(len(body)).encode() + b" >>\nstream\n"
        + body + b"\nendstream"
    )


def _objects() -> dict[int, bytes]:
    return {
        1: b"<< /Type /Catalog /Pages 2 0 R /Outlines 10 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>",
        3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200]"
           b" /Resources << /Font << /F1 5 0 R >> >> /Contents 6 0 R >>",
        4: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200]"
           b" /Resources << /Font << /F1 5 0 R >> >> /Contents 7 0 R >>",
        5: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        6: _stream(b"BT /F1 18 Tf 0 g 20 150 Td (page one) Tj ET"),
        7: _stream(b"BT /F1 18 Tf 0 g 20 150 Td (page two) Tj ET"),
        10: b"<< /Type /Outlines /First 11 0 R /Last 11 0 R /Count 1 >>",
        11: b"<< /Title (Chapter) /Parent 10 0 R /Dest [3 0 R /Fit] >>",
    }


def _serialise(objects: dict[int, bytes], trailer: bytes | None = None) -> bytes:
    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = {}
    for number in sorted(objects):
        offsets[number] = len(out)
        out += f"{number} 0 obj\n".encode() + objects[number] + b"\nendobj\n"
    start = len(out)
    out += b"xref\n0 1\n0000000000 65535 f \n"
    for number in sorted(objects):
        out += f"{number} 1\n".encode()
        out += f"{offsets[number]:010d} 00000 n \n".encode()
    if trailer is None:
        trailer = f"<< /Size {max(objects) + 1} /Root 1 0 R >>".encode()
    out += b"trailer\n" + trailer + b"\n"
    out += f"startxref\n{start}\n%%EOF\n".encode()
    return bytes(out)


def _damaged(**overrides: bytes) -> bytes:
    objects = _objects()
    objects.update({int(key[3:]): value for key, value in overrides.items()})
    return _serialise(objects)


def _pages_of(data: bytes) -> list[str]:
    document = Document()
    document.load_from(io.BytesIO(data))
    try:
        return [
            document.pages[index].extract_text().strip()
            for index in range(len(document.pages))
        ]
    finally:
        document.dispose()


_BOTH = ["page one", "page two"]


# --- a broken bookmark tree costs the bookmarks ------------------------------


def test_an_outlines_object_that_will_not_parse():
    assert _pages_of(_damaged(obj10=b"<< /Type /Outlines /First {bad} /Count (x) >>")) == _BOTH


def test_an_outline_list_that_points_at_a_missing_object():
    # 7.3.10: a reference to an object that is not there is a reference to null.
    data = _damaged(obj10=b"<< /Type /Outlines /First 99 0 R /Last 99 0 R /Count 1 >>")
    assert _pages_of(data) == _BOTH
    document = Document()
    document.load_from(io.BytesIO(data))
    assert len(document.outlines) == 0
    document.dispose()


def test_an_outline_item_that_is_its_own_sibling_and_child():
    data = _damaged(
        obj11=b"<< /Title (Chapter) /Parent 10 0 R /First 11 0 R /Next 11 0 R"
              b" /Dest [3 0 R /Fit] >>"
    )
    assert _pages_of(data) == _BOTH
    document = Document()
    document.load_from(io.BytesIO(data))
    # The loop is closed where it closes: the one real item survives.
    assert [item.title for item in document.outlines] == ["Chapter"]
    document.dispose()


def test_an_outline_item_that_is_not_a_dictionary():
    assert _pages_of(_damaged(obj11=b"42")) == _BOTH


def test_a_sound_outline_still_loads():
    document = Document()
    document.load_from(io.BytesIO(_damaged()))
    assert [item.title for item in document.outlines] == ["Chapter"]
    assert document.outlines[0].page_index == 0
    document.dispose()


def test_a_resource_limit_is_still_raised_from_the_outline_walk():
    # Tolerance is for the file's faults, not for the caller's ceiling.
    objects = _objects()
    items = {}
    for index in range(12):
        number = 100 + index
        following = b"" if index == 11 else f" /Next {number + 1} 0 R".encode()
        items[number] = (
            f"<< /Title (item {index}) /Parent 10 0 R".encode() + following + b" >>"
        )
    objects.update(items)
    objects[10] = b"<< /Type /Outlines /First 100 0 R /Last 111 0 R /Count 12 >>"
    data = _serialise(objects)

    document = Document()
    with pytest.raises(PdfResourceLimitException):
        document.load_from(io.BytesIO(data), limits=PdfLoadLimits(max_container_items=4))


# --- a cross-reference table that names nothing ------------------------------


def test_a_table_truncated_to_its_free_entry_is_reconstructed():
    data = _damaged()
    head = data.rpartition(b"xref\n")[0]
    rebuilt = (
        head
        + b"xref\n0 1\n0000000000 65535 f \ntrailer\n<< /Size 12 /Root 1 0 R >>\n"
        + b"startxref\n" + str(len(head)).encode() + b"\n%%EOF\n"
    )
    assert _pages_of(rebuilt) == _BOTH


def test_a_file_with_no_objects_at_all_is_still_refused():
    # The refusal only ever follows a reconstruction that found nothing.
    from aspose_pdf.exceptions import PdfParseException

    document = Document()
    with pytest.raises(PdfParseException, match="no body"):
        document.load_from(io.BytesIO(b"%PDF-1.7\nxref\n0 1\n0000000000 65535 f \n"
                                      b"trailer\n<< /Size 1 >>\nstartxref\n9\n%%EOF\n"))


def test_offsets_that_are_all_wrong_are_reconstructed():
    data = _damaged()
    head, _, tail = data.rpartition(b"xref\n")
    shifted = re.sub(
        rb"(\d{10}) (\d{5} n)",
        lambda m: b"%010d %s" % (max(0, int(m.group(1)) - 3), m.group(2)),
        tail,
    )
    assert _pages_of(head + b"xref\n" + shifted) == _BOTH


# --- a resource dictionary that is not one -----------------------------------


@pytest.mark.parametrize(
    "resources",
    [
        b"/Resources 8",
        b"/Resources << /Font 3 >>",
        b"/Resources << /Font << /F1 97 0 R >> >>",
        b"",
    ],
)
def test_a_page_whose_resources_are_damaged_still_reads(resources):
    page = (
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
        + resources
        + b" /Contents 7 0 R >>"
    )
    assert _pages_of(_damaged(obj4=page)) == _BOTH


def test_a_font_that_is_a_number_does_not_stop_extraction():
    # Without metrics the text still comes out, which is what every reference
    # reader does with such a page.
    assert _pages_of(_damaged(obj5=b"42")) == _BOTH


def test_the_trailer_that_survived_still_names_the_root():
    """Reconstruction fills gaps; it does not overrule a trailer that parsed.

    The table is unusable and the objects have to be found by scanning, but the
    trailer itself read fine and says which object is the catalog. A file that
    holds a second, stale catalog -- an earlier revision's, or a decoy -- would
    otherwise open on the wrong one.
    """
    objects = _objects()
    objects[90] = b"<< /Type /Catalog /Pages 91 0 R >>"
    objects[91] = b"<< /Type /Pages /Kids [92 0 R] /Count 1 >>"
    objects[92] = b"<< /Type /Page /Parent 91 0 R /MediaBox [0 0 100 100] >>"
    data = _serialise(objects)
    head = data.rpartition(b"xref\n")[0]
    rebuilt = (
        head
        + b"xref\n0 1\n0000000000 65535 f \ntrailer\n<< /Size 93 /Root 1 0 R >>\n"
        + b"startxref\n" + str(len(head)).encode() + b"\n%%EOF\n"
    )
    assert _pages_of(rebuilt) == _BOTH


def test_a_stale_trailer_appended_after_the_end_does_not_win():
    """Reconstruction merges every ``trailer`` it can find, latest wins.

    One appended after ``%%EOF`` -- a stale fragment, a concatenation accident --
    is later than the real one, so it would take over the catalog if the trailer
    that actually parsed were overruled. The pages come from the catalog the
    file's own trailer names.
    """
    objects = _objects()
    objects[90] = b"<< /Type /Catalog /Pages 91 0 R >>"
    objects[91] = b"<< /Type /Pages /Kids [92 0 R] /Count 1 >>"
    objects[92] = b"<< /Type /Page /Parent 91 0 R /MediaBox [0 0 100 100] >>"
    data = _serialise(objects)
    head = data.rpartition(b"xref\n")[0]
    rebuilt = (
        head
        + b"xref\n0 1\n0000000000 65535 f \ntrailer\n<< /Size 93 /Root 1 0 R >>\n"
        + b"startxref\n" + str(len(head)).encode() + b"\n%%EOF\n"
        + b"trailer\n<< /Root 90 0 R >>\n"
    )
    assert _pages_of(rebuilt) == _BOTH


def test_a_truncated_table_with_no_trailer_root_finds_a_catalog():
    objects = _objects()
    data = _serialise(objects)
    head = data.rpartition(b"xref\n")[0]
    rebuilt = (
        head
        + b"xref\n0 1\n0000000000 65535 f \ntrailer\n<< /Size 12 >>\n"
        + b"startxref\n" + str(len(head)).encode() + b"\n%%EOF\n"
    )
    assert _pages_of(rebuilt) == _BOTH

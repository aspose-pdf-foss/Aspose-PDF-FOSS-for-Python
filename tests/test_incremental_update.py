"""Tests for the :pymod:`aspose_pdf.engine.incremental_update` module.

The real implementation lives in ``aspose_pdf.engine.incremental_update``;
this file only contains pytest based unit tests that exercise the public
API.  The tests cover parsing of existing PDFs, object management and the
convenience ``append_incremental_update`` function.
"""

from __future__ import annotations

import re

import pytest

from aspose_pdf.engine.incremental_update import (
    IncrementalUpdate,
    append_incremental_update,
)


def _minimal_pdf() -> bytes:
    """Return a tiny but well‑formed PDF suitable for testing.

    The structure is::

        %PDF-1.4\n
        1 0 obj\n<< /Type /Catalog >>\nendobj\n
        xref\n0 2\n0000000000 65535 f \n0000000015 00000 n \n
        trailer\n<< /Size 2 /Root 1 0 R >>\n
        startxref\n45\n%%EOF\n
    The ``startxref`` offset points at the beginning of the ``xref``
    keyword (byte 45).  Offsets are calculated manually for clarity.
    """
    return (
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Type /Catalog >>\nendobj\n"
        b"xref\n0 2\n0000000000 65535 f \n0000000015 00000 n \n"
        b"trailer\n<< /Size 2 /Root 1 0 R >>\n"
        b"startxref\n45\n%%EOF\n"
    )


def test_find_last_eof():
    pdf = _minimal_pdf()
    iu = IncrementalUpdate(pdf)
    eof_pos = iu.find_last_eof()
    # The EOF marker starts at the beginning of "%%EOF".
    expected = pdf.rfind(b"%%EOF")
    assert eof_pos == expected, f"EOF position {eof_pos} != expected {expected}"


def test_find_startxref():
    pdf = _minimal_pdf()
    iu = IncrementalUpdate(pdf)
    startxref = iu.find_startxref()
    # In the minimal PDF the startxref value is the integer 45.
    assert startxref == 45, "Parsed startxref did not match expected value"


def test_add_object_and_update():
    pdf = _minimal_pdf()
    iu = IncrementalUpdate(pdf)
    obj_num = 2
    data = b"2 0 obj\n<< /Type /Page >>\nendobj\n"
    iu.add_object(obj_num, data)
    assert obj_num in iu.modified_objects
    assert iu.modified_objects[obj_num] == data
    # Updating the same object should replace the previous entry.
    new_data = b"2 0 obj\n<< /Type /Page /Count 0 >>\nendobj\n"
    iu.add_object(obj_num, new_data)
    assert iu.modified_objects[obj_num] == new_data


def test_add_new_object_and_next_number():
    pdf = _minimal_pdf()
    iu = IncrementalUpdate(pdf)
    initial_next = iu.next_obj_num
    data = b"<< /Type /Metadata >>"
    new_num = iu.add_new_object(data)
    assert new_num == initial_next
    assert iu.next_obj_num == initial_next + 1
    assert iu.modified_objects[new_num] == data


def test_build_incremental_xref_format():
    """Entries are read by *offset*, so each must be exactly twenty bytes.

    ISO 32000-1 7.5.4 fixes the width: ten offset digits, a space, five
    generation digits, a space, the type letter, and a two-byte end of line. A
    reader multiplies to reach the n-th entry rather than scanning for
    newlines, so the section has to be measured the same way here -- splitting
    on newlines first hides an extra byte, and only the *second* entry of a
    subsection lands in the wrong place, which is why one object appended alone
    still reads.
    """
    pdf = _minimal_pdf()
    iu = IncrementalUpdate(pdf)
    # Two consecutive numbers, so they share one subsection.
    iu.add_object(2, b"2 0 obj\n<<>>\nendobj\n")
    iu.add_object(3, b"3 0 obj\n<<>>\nendobj\n")
    xref = iu.build_incremental_xref(iu.original_eof_offset)

    assert xref.startswith(b"xref\n"), "xref does not start with 'xref' keyword"
    header, entries = xref[len(b"xref\n") :].split(b"\n", 1)
    first_obj, count = map(int, header.split())
    assert first_obj == 2
    assert count == 2

    assert len(entries) == 20 * count
    for index in range(count):
        entry = entries[index * 20 : (index + 1) * 20]
        assert re.fullmatch(rb"\d{10} \d{5} [nf](?: \r| \n|\r\n)", entry), entry


def test_build_incremental_trailer_values():
    pdf = _minimal_pdf()
    iu = IncrementalUpdate(pdf)
    prev = iu.find_startxref()
    trailer = iu.build_incremental_trailer(prev_xref=prev, new_size=5, xref_offset=1234)
    assert b"/Prev 45" in trailer, "Prev entry missing or incorrect"
    assert b"/Size 5" in trailer, "Size entry missing or incorrect"
    assert b"startxref" in trailer and b"1234" in trailer, "startxref offset incorrect"


def test_generate_incremental_update_integration():
    pdf = _minimal_pdf()
    iu = IncrementalUpdate(pdf)
    obj_bytes = b"2 0 obj\n<< /Type /Page >>\nendobj\n"
    iu.add_object(2, obj_bytes)
    result = iu.generate()
    # Result should consist of the new object followed by xref and trailer.
    assert result.startswith(obj_bytes), (
        "Generated data does not start with the new object"
    )
    assert b"xref" in result, "Missing xref section"
    assert b"trailer" in result, "Missing trailer section"


def test_append_incremental_update_preserves_original():
    pdf = _minimal_pdf()
    updates = {2: b"2 0 obj\n<< /Type /Page >>\nendobj\n"}
    new_pdf = append_incremental_update(pdf, updates)
    assert new_pdf.startswith(pdf), "Original PDF data was altered"
    assert new_pdf != pdf, "Incremental update section was not appended"
    # The tail must contain the object we added.
    assert updates[2] in new_pdf, "Added object not found in output"


def test_empty_updates_returns_original():
    pdf = _minimal_pdf()
    result = append_incremental_update(pdf, {})
    assert result == pdf, "Empty updates should return the original PDF unchanged"


def test_invalid_pdf_handling_raises():
    # Data without %%EOF or startxref should trigger errors.
    with pytest.raises(Exception):
        IncrementalUpdate(b"not a pdf")
    # PDF missing startxref but containing EOF.
    broken = b"%PDF-1.4\n%%EOF"
    with pytest.raises(Exception):
        IncrementalUpdate(broken)

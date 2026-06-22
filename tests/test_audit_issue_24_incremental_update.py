"""AUDIT #24: incremental COS writer — offsets, xref subsections, parseability.

The previous implementation used ``original_eof_offset`` (start of ``%%EOF``) as the
base for new object byte offsets, so xref entries pointed a few bytes too early
and the trailer ``startxref`` was wrong. It also emitted a single xref subsection
spanning ``min(id)``..``max(id)``, filling gaps with zero offsets (invalid PDF).

These tests lock in correct absolute offsets, non-contiguous object subsections,
and round-trip parsing via :class:`~aspose_pdf.engine.pdf_parser_cos.PdfCosParser`.
"""

from __future__ import annotations

import re

from aspose_pdf.engine.cos import PdfDictionary, PdfName
from aspose_pdf.engine.incremental_update import (
    IncrementalUpdate,
    append_incremental_update,
)
from aspose_pdf.engine.pdf_parser_cos import PdfCosParser


def _minimal_pdf() -> bytes:
    return (
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Type /Catalog >>\nendobj\n"
        b"xref\n0 2\n0000000000 65535 f \n0000000015 00000 n \n"
        b"trailer\n<< /Size 2 /Root 1 0 R >>\n"
        b"startxref\n45\n%%EOF\n"
    )


def test_incremental_absolute_offsets_and_startxref() -> None:
    """Appended objects start at ``len(original)``; xref/startxref must match."""
    pdf = _minimal_pdf()
    obj2 = b"2 0 obj\n<< /Type /Page >>\nendobj\n"
    new_pdf = append_incremental_update(pdf, {2: obj2})
    append_start = len(pdf)
    assert new_pdf.startswith(pdf)
    assert new_pdf[append_start:].startswith(obj2)
    xref_pos = new_pdf.find(b"xref", append_start)
    assert xref_pos == append_start + len(obj2)
    matches = list(re.finditer(rb"startxref\s*(\d+)", new_pdf))
    assert matches, "no startxref in output"
    assert int(matches[-1].group(1)) == xref_pos


def test_non_contiguous_modified_objects_two_subsections() -> None:
    """Sparse object ids must not use a single subsection with zero-filled gaps."""
    pdf = _minimal_pdf()
    o2 = b"2 0 obj\n<< /Type /Page >>\nendobj\n"
    o5 = b"5 0 obj\n<< /Type /Font >>\nendobj\n"
    new_pdf = append_incremental_update(pdf, {2: o2, 5: o5})
    inc = new_pdf[len(pdf) :]
    assert b"xref\n2 1\n" in inc and b"5 1\n" in inc
    doc = PdfCosParser(new_pdf).parse()
    base = len(pdf)
    assert doc.xref_table[2] == base
    assert doc.xref_table[5] == base + len(o2)
    font = doc.objects[5]
    assert isinstance(font, PdfDictionary)
    assert font.mapping[PdfName("Type")] == PdfName("Font")


def test_xref_entries_track_object_numbers_for_next_free() -> None:
    """Traditional xref parsing must record real object numbers (not always 0)."""
    pdf = _minimal_pdf()
    iu = IncrementalUpdate(pdf)
    nums = {obj for obj, _, _ in iu.xref_entries}
    assert 0 in nums and 1 in nums
    assert iu.next_obj_num == 2


def test_incremental_trailer_size_covers_highest_object() -> None:
    """``/Size`` must be at least max(object id) + 1 for manual large-number updates."""
    pdf = _minimal_pdf()
    o99 = b"99 0 obj\n<< /Type /Catalog >>\nendobj\n"
    new_pdf = append_incremental_update(pdf, {99: o99})
    assert re.search(rb"/Size\s+100\b", new_pdf), (
        "incremental trailer /Size must cover object 99"
    )


def test_incremental_pdf_loads_via_xref_chain() -> None:
    """Parser follows ``/Prev`` and merges xref from incremental revision."""
    pdf = _minimal_pdf()
    obj2 = b"2 0 obj\n<< /Type /Page >>\nendobj\n"
    new_pdf = append_incremental_update(pdf, {2: obj2})
    doc = PdfCosParser(new_pdf).parse()
    assert PdfName("Root") in doc.trailer.mapping
    page = doc.objects[2]
    assert isinstance(page, PdfDictionary)
    assert page.mapping[PdfName("Type")] == PdfName("Page")

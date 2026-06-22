"""AUDIT issue #39: broken outline trees must not silently drop entries — raise instead."""

from __future__ import annotations

import pytest

from aspose_pdf.exceptions import PdfParseException
from aspose_pdf.engine.simple_pdf import OUTLINE_TREE_MAX_DEPTH, SimplePdf


def _assemble_pdf(parts: list[tuple[int, bytes]]) -> bytes:
    """Build minimal PDF 1.7 with objects 1..max(id); gaps emit empty ``<< >>`` stubs."""
    by_id = dict(parts)
    max_obj = max(by_id)
    for i in range(1, max_obj + 1):
        by_id.setdefault(i, b"<< >>")
    ordered = sorted(by_id.items(), key=lambda x: x[0])
    header = b"%PDF-1.7\n"
    body = bytearray(header)
    offsets: dict[int, int] = {}
    for obj_num, obj_body in ordered:
        offsets[obj_num] = len(body)
        body.extend(f"{obj_num} 0 obj\n".encode("ascii"))
        body.extend(obj_body)
        body.extend(b"\nendobj\n")
    xref_offset = len(body)
    xref = bytearray(b"xref\n")
    xref.extend(f"0 {max_obj + 1}\n".encode("ascii"))
    xref.extend(b"0000000000 65535 f \n")
    for i in range(1, max_obj + 1):
        xref.extend(f"{offsets[i]:010d} 00000 n \n".encode("ascii"))
    trailer = f"<< /Size {max_obj + 1} /Root 1 0 R >>\n".encode("ascii")
    body.extend(xref)
    body.extend(b"trailer\n")
    body.extend(trailer)
    body.extend(b"startxref\n")
    body.extend(f"{xref_offset}\n".encode("ascii"))
    body.extend(b"%%EOF")
    return bytes(body)


def _base_pages_catalog(outline_root_id: int = 5) -> list[tuple[int, bytes]]:
    """Catalog (1), Pages (2), Page (3), outline root id *outline_root_id*."""
    return [
        (
            1,
            (
                b"<< /Type /Catalog /Pages 2 0 R /Outlines "
                + f"{outline_root_id} 0 R".encode("ascii")
                + b" >>"
            ),
        ),
        (
            2,
            b"<< /Type /Pages /Kids [ 3 0 R ] /Count 1 >>",
        ),
        (
            3,
            b"<< /Type /Page /Parent 2 0 R /MediaBox [ 0 0 612 792 ] >>",
        ),
    ]


def test_outline_valid_single_item_loads():
    """Well-formed /Outlines with one top-level item loads without error."""
    parts = _base_pages_catalog(5) + [
        (
            5,
            b"<< /Type /Outlines /First 4 0 R /Last 4 0 R /Count 1 >>",
        ),
        (
            4,
            b"<< /Title (Hello) /Parent 5 0 R /Dest [ 3 0 R /XYZ 0 0 0 ] >>",
        ),
    ]
    pdf = _assemble_pdf(parts)
    doc = SimplePdf.from_bytes(pdf)
    assert len(doc._outlines_data) == 1
    assert doc._outlines_data[0]["title"] == "Hello"
    assert doc._outlines_data[0]["page_index"] == 0


def test_outline_next_cycle_raises():
    """A /Next cycle between siblings must raise (AUDIT #39)."""
    parts = _base_pages_catalog(5) + [
        (
            5,
            b"<< /Type /Outlines /First 4 0 R /Last 6 0 R /Count 2 >>",
        ),
        (
            4,
            b"<< /Title (A) /Parent 5 0 R /Next 6 0 R /Dest [ 3 0 R /XYZ 0 0 0 ] >>",
        ),
        (
            6,
            b"<< /Title (B) /Parent 5 0 R /Next 4 0 R /Dest [ 3 0 R /XYZ 0 0 0 ] >>",
        ),
    ]
    pdf = _assemble_pdf(parts)
    with pytest.raises(PdfParseException, match="cycle"):
        SimplePdf.from_bytes(pdf)


def test_outline_next_missing_object_raises():
    """/Next to a non-existent object must raise."""
    parts = _base_pages_catalog(5) + [
        (
            5,
            b"<< /Type /Outlines /First 4 0 R /Last 4 0 R /Count 1 >>",
        ),
        (
            4,
            b"<< /Title (A) /Parent 5 0 R /Next 99 0 R /Dest [ 3 0 R /XYZ 0 0 0 ] >>",
        ),
    ]
    pdf = _assemble_pdf(parts)
    with pytest.raises(PdfParseException, match="missing object"):
        SimplePdf.from_bytes(pdf)


def test_outline_next_non_dictionary_raises():
    """Resolved /Next must be a dictionary."""
    parts = _base_pages_catalog(5) + [
        (
            5,
            b"<< /Type /Outlines /First 4 0 R /Last 4 0 R /Count 1 >>",
        ),
        (
            4,
            b"<< /Title (A) /Parent 5 0 R /Next 7 0 R /Dest [ 3 0 R /XYZ 0 0 0 ] >>",
        ),
        (7, b"(not-a-dict-outline-item)"),
    ]
    pdf = _assemble_pdf(parts)
    with pytest.raises(PdfParseException, match="dictionary"):
        SimplePdf.from_bytes(pdf)


def test_outline_excessive_first_nesting_raises():
    """More than OUTLINE_TREE_MAX_DEPTH nested /First levels must raise."""
    parts: list[tuple[int, bytes]] = list(_base_pages_catalog(5))
    parts.append(
        (
            5,
            b"<< /Type /Outlines /First 4 0 R /Last 4 0 R /Count 1 >>",
        )
    )
    # Top item 4; chain 10..42 each /First next; 42 /First 43 — recursion depth 33 on 43.
    dest = b"/Dest [ 3 0 R /XYZ 0 0 0 ]"
    for n in range(10, 43):
        nxt = n + 1
        parts.append(
            (
                n,
                f"<< /Title (L{n}) /Parent 5 0 R /First {nxt} 0 R ".encode("ascii")
                + dest
                + b" >>",
            )
        )
    parts.append(
        (
            43,
            b"<< /Title (leaf) /Parent 5 0 R " + dest + b" >>",
        )
    )
    parts.append(
        (
            4,
            b"<< /Title (top) /Parent 5 0 R /First 10 0 R " + dest + b" >>",
        )
    )
    pdf = _assemble_pdf(parts)
    with pytest.raises(PdfParseException, match="maximum depth"):
        SimplePdf.from_bytes(pdf)


def test_outline_max_depth_constant_matches_exception_message():
    """Guardrail: constant stays aligned with user-visible error text."""
    assert OUTLINE_TREE_MAX_DEPTH == 32

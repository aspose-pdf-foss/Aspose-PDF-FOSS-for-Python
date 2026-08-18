"""Embedded-file name trees split across ``/Kids`` sub-nodes (ISO 32000-1 7.9.6).

A producer is free to balance a large ``/EmbeddedFiles`` tree into intermediate
nodes instead of one flat ``/Names`` array. Reading only the root's ``/Names``
returns an empty attachment list for such a document — silently, with no error
— so every leaf must be reached through ``/Kids``.
"""

import pytest

from aspose_pdf import PdfLoadLimits, PdfResourceLimitException
from aspose_pdf.engine.simple_pdf import SimplePdf


def _lit(raw: bytes) -> bytes:
    out = bytearray(b"(")
    for b in raw:
        if b in (0x28, 0x29, 0x5C):
            out.append(0x5C)
        out.append(b)
    out.append(0x29)
    return bytes(out)


def _assemble_pdf(parts: list[tuple[int, bytes]]) -> bytes:
    body = bytearray(b"%PDF-1.7\n")
    offsets: dict[int, int] = {}
    max_obj = max(num for num, _ in parts)
    for obj_num, obj_body in sorted(parts, key=lambda x: x[0]):
        offsets[obj_num] = len(body)
        body.extend(f"{obj_num} 0 obj\n".encode("ascii"))
        body.extend(obj_body)
        body.extend(b"\nendobj\n")
    xref_offset = len(body)
    xref = bytearray(b"xref\n")
    xref.extend(f"0 {max_obj + 1}\n".encode("ascii"))
    xref.extend(b"0000000000 65535 f \n")
    for i in range(1, max_obj + 1):
        xref.extend(f"{offsets.get(i, 0):010d} 00000 n \n".encode("ascii"))
    body.extend(xref)
    body.extend(b"trailer\n")
    body.extend(f"<< /Size {max_obj + 1} /Root 1 0 R >>\n".encode("ascii"))
    body.extend(b"startxref\n")
    body.extend(f"{xref_offset}\n".encode("ascii"))
    body.extend(b"%%EOF")
    return bytes(body)


def _filespec_and_stream(first_obj: int, name: str, payload: bytes):
    """Return (objects, filespec_ref) for one attachment."""
    spec_num, stream_num = first_obj, first_obj + 1
    spec = (
        b"<< /Type /Filespec /F "
        + _lit(name.encode("ascii"))
        + f" /EF << /F {stream_num} 0 R >> >>".encode("ascii")
    )
    stream = (
        f"<< /Length {len(payload)} >>\nstream\n".encode("ascii")
        + payload
        + b"\nendstream"
    )
    return [(spec_num, spec), (stream_num, stream)], f"{spec_num} 0 R".encode("ascii")


def test_kids_split_tree_finds_every_attachment():
    """Two leaves under /Kids — the flat-only reader saw neither."""
    objs: list[tuple[int, bytes]] = []
    a_objs, a_ref = _filespec_and_stream(5, "alpha.txt", b"alpha-payload")
    b_objs, b_ref = _filespec_and_stream(7, "beta.txt", b"beta-payload")
    objs += a_objs + b_objs

    # 3 and 4 are the leaf nodes, 2 the intermediate root.
    objs.append((3, b"<< /Limits [(alpha.txt) (alpha.txt)] /Names [ "
                 + _lit(b"alpha.txt") + b" " + a_ref + b" ] >>"))
    objs.append((4, b"<< /Limits [(beta.txt) (beta.txt)] /Names [ "
                 + _lit(b"beta.txt") + b" " + b_ref + b" ] >>"))
    objs.append((2, b"<< /Kids [ 3 0 R 4 0 R ] >>"))
    objs.append((1, b"<< /Type /Catalog /Pages 9 0 R /Names "
                    b"<< /EmbeddedFiles 2 0 R >> >>"))
    objs.append((9, b"<< /Type /Pages /Count 0 /Kids [] >>"))

    pdf = SimplePdf.from_bytes(_assemble_pdf(objs))
    assert pdf.attachments == {
        "alpha.txt": b"alpha-payload",
        "beta.txt": b"beta-payload",
    }


def test_kids_visited_in_document_order():
    """Kids order is the tree's sort order and must be preserved."""
    objs: list[tuple[int, bytes]] = []
    refs = []
    for idx, name in enumerate(("a.bin", "b.bin", "c.bin")):
        made, ref = _filespec_and_stream(10 + idx * 2, name, name.encode())
        objs += made
        refs.append(ref)
    for idx, (name, ref) in enumerate(zip(("a.bin", "b.bin", "c.bin"), refs)):
        objs.append((3 + idx, b"<< /Names [ " + _lit(name.encode()) + b" "
                     + ref + b" ] >>"))
    objs.append((2, b"<< /Kids [ 3 0 R 4 0 R 5 0 R ] >>"))
    objs.append((1, b"<< /Type /Catalog /Pages 9 0 R /Names "
                    b"<< /EmbeddedFiles 2 0 R >> >>"))
    objs.append((9, b"<< /Type /Pages /Count 0 /Kids [] >>"))

    pdf = SimplePdf.from_bytes(_assemble_pdf(objs))
    assert list(pdf.attachments) == ["a.bin", "b.bin", "c.bin"]


def test_deep_kids_chain_and_mixed_node():
    """A node may carry both /Names and /Kids; depth beyond one level works."""
    objs: list[tuple[int, bytes]] = []
    top_objs, top_ref = _filespec_and_stream(10, "top.txt", b"top")
    deep_objs, deep_ref = _filespec_and_stream(12, "deep.txt", b"deep")
    objs += top_objs + deep_objs

    objs.append((4, b"<< /Names [ " + _lit(b"deep.txt") + b" " + deep_ref + b" ] >>"))
    objs.append((3, b"<< /Kids [ 4 0 R ] >>"))
    # Root node mixes its own /Names with /Kids.
    objs.append((2, b"<< /Names [ " + _lit(b"top.txt") + b" " + top_ref
                 + b" ] /Kids [ 3 0 R ] >>"))
    objs.append((1, b"<< /Type /Catalog /Pages 9 0 R /Names "
                    b"<< /EmbeddedFiles 2 0 R >> >>"))
    objs.append((9, b"<< /Type /Pages /Count 0 /Kids [] >>"))

    pdf = SimplePdf.from_bytes(_assemble_pdf(objs))
    assert pdf.attachments == {"top.txt": b"top", "deep.txt": b"deep"}


def test_kids_cycle_terminates():
    """A /Kids cycle is a visited-node no-op, not a hang."""
    objs: list[tuple[int, bytes]] = []
    made, ref = _filespec_and_stream(10, "loop.txt", b"loop")
    objs += made
    objs.append((3, b"<< /Names [ " + _lit(b"loop.txt") + b" " + ref
                 + b" ] /Kids [ 2 0 R ] >>"))
    objs.append((2, b"<< /Kids [ 3 0 R ] >>"))
    objs.append((1, b"<< /Type /Catalog /Pages 9 0 R /Names "
                    b"<< /EmbeddedFiles 2 0 R >> >>"))
    objs.append((9, b"<< /Type /Pages /Count 0 /Kids [] >>"))

    pdf = SimplePdf.from_bytes(_assemble_pdf(objs))
    assert pdf.attachments == {"loop.txt": b"loop"}


def test_deep_kids_chain_hits_nesting_limit():
    """An unbounded /Kids chain is rejected by the shared depth budget."""
    depth = 12
    objs: list[tuple[int, bytes]] = []
    made, ref = _filespec_and_stream(100, "x.txt", b"x")
    objs += made
    # Nodes 2..(depth+1) chain down; the last one carries the leaf.
    for level in range(depth):
        num = 2 + level
        if level == depth - 1:
            objs.append((num, b"<< /Names [ " + _lit(b"x.txt") + b" " + ref + b" ] >>"))
        else:
            objs.append((num, f"<< /Kids [ {num + 1} 0 R ] >>".encode("ascii")))
    objs.append((1, b"<< /Type /Catalog /Pages 99 0 R /Names "
                    b"<< /EmbeddedFiles 2 0 R >> >>"))
    objs.append((99, b"<< /Type /Pages /Count 0 /Kids [] >>"))
    data = _assemble_pdf(objs)

    # Generous depth: the chain resolves.
    assert SimplePdf.from_bytes(
        data, limits=PdfLoadLimits(max_nesting_depth=depth)
    ).attachments == {"x.txt": b"x"}

    with pytest.raises(PdfResourceLimitException):
        SimplePdf.from_bytes(data, limits=PdfLoadLimits(max_nesting_depth=depth - 2))

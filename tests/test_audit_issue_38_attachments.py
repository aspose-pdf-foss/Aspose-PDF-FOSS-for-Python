"""AUDIT issue #38: attachment filename encoding and /EF stream variants round-trip."""

import zlib

from aspose_pdf.engine.simple_pdf import SimplePdf


def _pdf_literal_from_bytes(raw: bytes) -> bytes:
    out = bytearray(b"(")
    for b in raw:
        if b in (0x28, 0x29, 0x5C):
            out.append(0x5C)
        out.append(b)
    out.append(0x29)
    return bytes(out)


def _assemble_pdf(parts: list[tuple[int, bytes]]) -> bytes:
    """Build minimal PDF 1.7 with objects in order 1..N (contiguous)."""
    header = b"%PDF-1.7\n"
    body = bytearray(header)
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
        xref.extend(f"{offsets[i]:010d} 00000 n \n".encode("ascii"))
    trailer = f"<< /Size {max_obj + 1} /Root 1 0 R >>\n".encode("ascii")
    body.extend(xref)
    body.extend(b"trailer\n")
    body.extend(trailer)
    body.extend(b"startxref\n")
    body.extend(f"{xref_offset}\n".encode("ascii"))
    body.extend(b"%%EOF")
    return bytes(body)


def test_extract_utf16be_filename_in_names_key():
    """Names key uses PDF UTF-16BE text string (BOM + 16-bit code units)."""
    unicode_name = "\u043f\u0440\u0438\u0432\u0435\u0442.bin"
    payload = b"payload-\xff\x00-ok"
    utf16 = unicode_name.encode("utf-16-be")
    key_literal = _pdf_literal_from_bytes(b"\xfe\xff" + utf16)

    # 1 Catalog, 2 FileSpec, 3 stream, 4 Pages
    obj1 = (
        b"<< /Type /Catalog /Pages 4 0 R /Names << /EmbeddedFiles "
        b"<< /Names [ " + key_literal + b" 2 0 R ] >> >> >>"
    )
    obj2 = (
        b"<< /Type /Filespec /F "
        + _pdf_literal_from_bytes(b"\xfe\xff" + utf16)
        + b" /EF << /F 3 0 R >> >>"
    )
    obj3 = (
        f"<< /Length {len(payload)} >>\nstream\n".encode("ascii")
        + payload
        + b"\nendstream"
    )
    obj4 = b"<< /Type /Pages /Count 0 /Kids [] >>"
    pdf_bytes = _assemble_pdf([(1, obj1), (2, obj2), (3, obj3), (4, obj4)])

    pdf = SimplePdf.from_bytes(pdf_bytes)
    assert unicode_name in pdf.attachments
    assert pdf.attachments[unicode_name] == payload


def test_extract_ef_embedded_stream_under_uf_only():
    """Some producers place the file stream only under /EF /UF (not /F)."""
    filename = "only-uf.dat"
    payload = b"UF-stream-bytes"
    key_lit = _pdf_literal_from_bytes(filename.encode("ascii"))

    obj1 = (
        b"<< /Type /Catalog /Pages 4 0 R /Names << /EmbeddedFiles "
        b"<< /Names [ " + key_lit + b" 2 0 R ] >> >> >>"
    )
    obj2 = b"<< /Type /Filespec /EF << /UF 3 0 R >> >>"
    obj3 = (
        f"<< /Length {len(payload)} >>\nstream\n".encode("ascii")
        + payload
        + b"\nendstream"
    )
    obj4 = b"<< /Type /Pages /Count 0 /Kids [] >>"
    pdf_bytes = _assemble_pdf([(1, obj1), (2, obj2), (3, obj3), (4, obj4)])

    pdf = SimplePdf.from_bytes(pdf_bytes)
    assert filename in pdf.attachments
    assert pdf.attachments[filename] == payload


def test_extract_ef_flate_decode_stream():
    """Embedded file stream may use FlateDecode."""
    filename = "z.txt"
    payload = b"flate-payload" * 10
    compressed = zlib.compress(payload)
    key_lit = _pdf_literal_from_bytes(filename.encode("ascii"))

    obj1 = (
        b"<< /Type /Catalog /Pages 4 0 R /Names << /EmbeddedFiles "
        b"<< /Names [ " + key_lit + b" 2 0 R ] >> >> >>"
    )
    obj2 = (
        b"<< /Type /Filespec /F "
        + _pdf_literal_from_bytes(filename.encode("ascii"))
        + b" /EF << /F 3 0 R >> >>"
    )
    obj3 = (
        f"<< /Length {len(compressed)} /Filter /FlateDecode >>\nstream\n".encode(
            "ascii"
        )
        + compressed
        + b"\nendstream"
    )
    obj4 = b"<< /Type /Pages /Count 0 /Kids [] >>"
    pdf_bytes = _assemble_pdf([(1, obj1), (2, obj2), (3, obj3), (4, obj4)])

    pdf = SimplePdf.from_bytes(pdf_bytes)
    assert pdf.attachments[filename] == payload


def test_roundtrip_cos_preserves_utf16_attachment_name_and_bytes():
    unicode_name = "文档.pdf"
    payload = b"%PDF-attach%"
    utf16 = unicode_name.encode("utf-16-be")
    key_literal = _pdf_literal_from_bytes(b"\xfe\xff" + utf16)

    obj1 = (
        b"<< /Type /Catalog /Pages 4 0 R /Names << /EmbeddedFiles "
        b"<< /Names [ " + key_literal + b" 2 0 R ] >> >> >>"
    )
    obj2 = (
        b"<< /Type /Filespec /UF "
        + _pdf_literal_from_bytes(b"\xfe\xff" + utf16)
        + b" /EF << /UF 3 0 R >> >>"
    )
    obj3 = (
        f"<< /Length {len(payload)} >>\nstream\n".encode("ascii")
        + payload
        + b"\nendstream"
    )
    obj4 = b"<< /Type /Pages /Count 0 /Kids [] >>"
    pdf_bytes = _assemble_pdf([(1, obj1), (2, obj2), (3, obj3), (4, obj4)])

    pdf1 = SimplePdf.from_bytes(pdf_bytes)
    assert unicode_name in pdf1.attachments
    assert pdf1.attachments[unicode_name] == payload

    out = pdf1.to_bytes()
    pdf2 = SimplePdf.from_bytes(out)
    assert unicode_name in pdf2.attachments
    assert pdf2.attachments[unicode_name] == payload

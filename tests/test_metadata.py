from aspose_pdf.engine.simple_pdf import SimplePdf


def _minimal_pdf_bytes(include_info: bool = False) -> bytes:
    """Return minimal PDF bytes with a proper cross-reference table."""
    header = b"%PDF-1.4\n%" + bytes.fromhex("e2e3cfd3") + b"\n"

    obj1 = b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    obj2 = b"2 0 obj\n<< /Type /Pages /Count 1 /Kids [3 0 R] >>\nendobj\n"
    obj3 = b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\nendobj\n"
    obj4 = b"4 0 obj\n<< /Title (Stub) >>\nendobj\n" if include_info else b""

    objects = [obj1, obj2, obj3]
    if include_info:
        objects.append(obj4)

    offsets = []
    curr = len(header)
    for obj in objects:
        offsets.append(curr)
        curr += len(obj)

    xref_pos = curr
    xref = b"xref\n0 " + str(len(objects) + 1).encode() + b"\n"
    xref += b"0000000000 65535 f \n"
    for off in offsets:
        xref += f"{off:010d} 00000 n \n".encode()

    trailer = b"trailer\n<< /Size " + str(len(objects) + 1).encode() + b" /Root 1 0 R"
    if include_info:
        trailer += b" /Info 4 0 R"
    trailer += b" >>\nstartxref\n" + str(xref_pos).encode() + b"\n%%EOF"

    return header + b"".join(objects) + xref + trailer


def test_metadata_parsing_and_writing():
    pdf_bytes = _minimal_pdf_bytes(include_info=True)
    pdf = SimplePdf.from_bytes(pdf_bytes)
    assert isinstance(pdf.metadata, dict)

    pdf.metadata["Title"] = "Test PDF"
    out_bytes = pdf.to_bytes()

    pdf2 = SimplePdf.from_bytes(out_bytes)
    assert pdf2.metadata.get("Title") == "Test PDF"


def test_metadata_roundtrip():
    pdf = SimplePdf()
    # Add a page so writer doesn't fail
    pdf.pages.append((0, 0, 612, 792))
    meta = {
        "Title": "Test Title",
        "Author": "John Doe",
    }
    pdf.metadata = meta
    data = pdf.to_bytes()
    loaded = SimplePdf.from_bytes(data)
    assert loaded.metadata == meta


def test_missing_metadata_handling():
    raw = _minimal_pdf_bytes(include_info=False)
    pdf = SimplePdf.from_bytes(raw)
    assert not pdf.metadata

    pdf.metadata = {"Title": "Added"}
    # Add a page
    pdf.pages.append((0, 0, 612, 792))
    new_data = pdf.to_bytes()
    reloaded = SimplePdf.from_bytes(new_data)
    assert reloaded.metadata.get("Title") == "Added"

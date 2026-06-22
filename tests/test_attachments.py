from aspose_pdf.engine.simple_pdf import SimplePdf


def create_pdf_with_attachment(filename="test.txt", content=b"Hello World!"):
    """Helper to create a valid PDF binary with an embedded file."""
    # Objects content
    # 1. Catalog
    # 2. FileSpec
    # 3. Stream

    # We construct the body first to calculate offsets
    header = b"%PDF-1.7\n"

    # Object 1: Catalog
    # /Names << /EmbeddedFiles << /Names [ (filename) 2 0 R ] >> >>
    # Note: Strings in PDF can be ( ) escaped. Simple alphanumeric is safe.
    obj1_content = f"<< /Type /Catalog /Pages 4 0 R /Names << /EmbeddedFiles << /Names [ ({filename}) 2 0 R ] >> >> >>".encode(
        "latin1"
    )
    obj1 = b"1 0 obj\n" + obj1_content + b"\nendobj\n"

    # Object 2: FileSpec
    obj2_content = f"<< /Type /Filespec /F ({filename}) /EF << /F 3 0 R >> >>".encode(
        "latin1"
    )
    obj2 = b"2 0 obj\n" + obj2_content + b"\nendobj\n"

    # Object 3: Stream
    stream_data = content
    obj3_content = (
        f"<< /Length {len(stream_data)} >>\nstream\n".encode("latin1")
        + stream_data
        + b"\nendstream"
    )
    obj3 = b"3 0 obj\n" + obj3_content + b"\nendobj\n"

    # Object 4: Pages (Minimal)
    obj4_content = b"<< /Type /Pages /Count 0 /Kids [] >>"
    obj4 = b"4 0 obj\n" + obj4_content + b"\nendobj\n"

    # Assemble and track offsets
    body = header
    offsets = {}

    offsets[1] = len(body)
    body += obj1

    offsets[2] = len(body)
    body += obj2

    offsets[3] = len(body)
    body += obj3

    offsets[4] = len(body)
    body += obj4

    xref_offset = len(body)
    xref = b"xref\n"
    xref += "0 5\n".encode("latin1")
    xref += b"0000000000 65535 f \n"

    for i in range(1, 5):
        xref += f"{offsets[i]:010d} 00000 n \n".encode("latin1")

    trailer = "<< /Size 5 /Root 1 0 R >>\n".encode("latin1")

    footer = (
        xref
        + b"trailer\n"
        + trailer
        + b"startxref\n"
        + f"{xref_offset}\n".encode("latin1")
        + b"%%EOF"
    )

    return body + footer


def test_attachments_empty_by_default():
    pdf = SimplePdf()
    assert pdf.attachments == {}
    assert isinstance(pdf.attachments, dict)


def test_manual_attachment_add():
    pdf = SimplePdf()
    pdf.attachments["foo.txt"] = b"bar"
    assert pdf.attachments["foo.txt"] == b"bar"


def test_extract_attachment():
    """Integration test: Parse a PDF with embedded file."""
    content = b"Important Notice content"
    pdf_bytes = create_pdf_with_attachment(filename="notice.txt", content=content)

    pdf = SimplePdf.from_bytes(pdf_bytes)

    assert "notice.txt" in pdf.attachments
    assert pdf.attachments["notice.txt"] == content


def test_roundtrip_basic():
    """Test that saving and reloading an empty PDF doesn't crash and keeps empty attachments."""
    pdf = SimplePdf()
    data = pdf.to_bytes()
    pdf2 = SimplePdf.from_bytes(data)
    assert pdf2.attachments == {}


# ---------------------------------------------------------------------------
# Adding / embedding attachments (persisted on save)
# ---------------------------------------------------------------------------


def test_add_attachment_persisted_on_save():
    """An attachment added in memory survives a save/reload round trip."""
    pdf = SimplePdf()
    pdf.attachments["notes.txt"] = b"hello world"
    reopened = SimplePdf.from_bytes(pdf.to_bytes())
    assert reopened.attachments["notes.txt"] == b"hello world"


def test_add_multiple_attachments_roundtrip():
    pdf = SimplePdf()
    pdf.attachments["b.bin"] = b"\x00\x01\x02BIN"
    pdf.attachments["a.txt"] = b"alpha"
    reopened = SimplePdf.from_bytes(pdf.to_bytes())
    assert reopened.attachments["a.txt"] == b"alpha"
    assert reopened.attachments["b.bin"] == b"\x00\x01\x02BIN"


def test_add_attachment_unicode_name_roundtrip():
    pdf = SimplePdf()
    name = "rapport-été-文档.txt"
    pdf.attachments[name] = b"payload"
    reopened = SimplePdf.from_bytes(pdf.to_bytes())
    assert reopened.attachments[name] == b"payload"


def test_add_attachment_to_existing_document_preserves_both():
    pdf = SimplePdf.from_bytes(
        create_pdf_with_attachment(filename="orig.txt", content=b"ORIG")
    )
    assert pdf.attachments["orig.txt"] == b"ORIG"
    pdf.attachments["added.txt"] = b"NEW"
    reopened = SimplePdf.from_bytes(pdf.to_bytes())
    assert reopened.attachments["orig.txt"] == b"ORIG"
    assert reopened.attachments["added.txt"] == b"NEW"


def test_added_attachment_uses_embeddedfiles_name_tree():
    pdf = SimplePdf()
    pdf.attachments["doc.dat"] = b"data"
    out = pdf.to_bytes()
    assert b"/EmbeddedFiles" in out
    assert b"/Filespec" in out
    assert b"/EmbeddedFile" in out


def test_empty_attachments_not_persisted():
    pdf = SimplePdf()
    out = pdf.to_bytes()
    assert b"/EmbeddedFiles" not in out
    assert SimplePdf.from_bytes(out).attachments == {}


def test_document_add_attachment_roundtrip():
    import io

    from aspose_pdf.document import Document

    doc = Document()
    doc.add_attachment("readme.txt", b"from document api")
    doc.attachments["second.bin"] = b"\x01\x02"
    buf = io.BytesIO()
    doc.save(buf)

    reopened = Document()
    reopened.load_from(buf.getvalue())
    assert reopened.attachments["readme.txt"] == b"from document api"
    assert reopened.attachments["second.bin"] == b"\x01\x02"

from aspose_pdf.engine.simple_pdf import SimplePdf


def test_set_watermark():
    pdf = SimplePdf()
    pdf.pages.append((0, 0, 612, 792))
    pdf.set_watermark("CONFIDENTIAL")
    data = pdf.to_bytes()
    assert b"(CONFIDENTIAL)" in data


def test_watermark_in_bytes():
    pdf = SimplePdf()
    pdf.pages.append((0, 0, 612, 792))
    pdf.set_watermark("WATERMARK")
    data = pdf.to_bytes()
    assert b"(WATERMARK)" in data
    assert b"BT" in data and b"ET" in data


def test_watermark_roundtrip():
    pdf = SimplePdf()
    pdf.pages.append((0, 0, 612, 792))
    pdf.set_watermark("ROUNDTRIP")
    data = pdf.to_bytes()
    pdf2 = SimplePdf.from_bytes(data)
    data2 = pdf2.to_bytes()
    assert b"(ROUNDTRIP)" in data2


def test_empty_watermark():
    pdf = SimplePdf()
    pdf.pages.append((0, 0, 612, 792))
    pdf.set_watermark("")
    data = pdf.to_bytes()
    # The requirement was "not in data" if empty
    assert b"BT" not in data or b"()" not in data


def test_multiple_watermark_calls():
    pdf = SimplePdf()
    pdf.pages.append((0, 0, 612, 792))
    pdf.set_watermark("FIRST")
    pdf.set_watermark("SECOND")
    data = pdf.to_bytes()
    assert b"(SECOND)" in data
    assert b"(FIRST)" not in data

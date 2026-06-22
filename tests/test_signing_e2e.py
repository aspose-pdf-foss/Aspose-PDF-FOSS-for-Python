import re
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.engine.signing import SigningUtils


def test_sign_pdf_end_to_end():
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 100, 100)]
    pdf.page_contents = [b"Signed Content"]

    cert, key = SigningUtils.create_self_signed_cert()

    pdf.signing_creds = (cert, key)

    pdf_bytes = pdf.to_bytes()

    assert b"/Adobe.PPKLite" in pdf_bytes
    assert b"/Contents <" in pdf_bytes
    assert b"/ByteRange [" in pdf_bytes

    br_match = re.search(rb"/ByteRange \[(\d+) (\d+) (\d+) (\d+)\]", pdf_bytes)
    assert br_match, "ByteRange not found or malformed"

    start1, len1, start2, len2 = map(int, br_match.groups())
    assert start1 == 0
    assert len1 > 0
    assert start2 > len1
    assert len2 > 0

    contents_match = re.search(rb"/Contents <([0-9A-Fa-f]+)>", pdf_bytes)
    assert contents_match
    hex_str = contents_match.group(1)
    assert re.search(rb"[1-9a-fA-F]", hex_str), (
        "Signature content seems to be all zeros (patching failed?)"
    )

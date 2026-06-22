from aspose_pdf.engine.simple_pdf import SimplePdf


def test_aes_generation(tmp_path):
    # Create a simple PDF
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 100, 100)]
    pdf.page_contents = [b"BT /F1 12 Tf (Hello AES) Tj ET"]

    # 1. Test AES-128
    pdf.encrypt("user", "owner", algorithm="AES-128")
    out_128 = tmp_path / "aes128.pdf"
    pdf.save(out_128)

    assert out_128.exists()
    content_128 = out_128.read_bytes()
    assert b"/V 4" in content_128
    assert b"/R 4" in content_128
    assert b"/AESV2" in content_128
    # Content should not be plain text
    assert b"(Hello AES)" not in content_128

    # 2. Test AES-256
    pdf.encrypt("user", "owner", algorithm="AES-256")
    out_256 = tmp_path / "aes256.pdf"
    pdf.save(out_256)

    assert out_256.exists()
    content_256 = out_256.read_bytes()
    # AES-256 usually implied by V5/R5 or R6
    assert b"/V 5" in content_256
    assert b"/R 5" in content_256
    # Updated implementation uses AESV3 for AES-256
    assert b"/AESV3" in content_256

    # 3. Test RC4 (Regression)
    pdf.encrypt("user", "owner", algorithm="RC4")
    out_rc4 = tmp_path / "rc4.pdf"
    pdf.save(out_rc4)

    assert out_rc4.exists()
    content_rc4 = out_rc4.read_bytes()
    assert b"/V 1" in content_rc4
    assert b"/R 2" in content_rc4
    assert b"(Hello AES)" not in content_rc4

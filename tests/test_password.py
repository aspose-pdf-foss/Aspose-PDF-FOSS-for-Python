from aspose_pdf.engine.simple_pdf import SimplePdf


def _minimal_pdf():
    """Create a simple PDF object with one empty page for testing."""
    pdf = SimplePdf()
    pdf.pages = [(0, 0, 100, 100)]
    pdf.page_contents = [b""]
    return pdf


def test_add_password():
    pdf = _minimal_pdf()
    # Add a user password – should enable encryption and embed /Encrypt dict.
    pdf.add_password("secret")
    data = pdf.to_bytes()
    assert b"/Encrypt" in data, "Encryption dictionary not found after adding password"
    assert pdf.encrypted is True
    assert pdf.password == "secret"


def test_remove_password():
    pdf = _minimal_pdf()
    pdf.add_password("secret")
    # Now remove the password – encryption should be disabled.
    pdf.remove_password()
    data = pdf.to_bytes()
    assert b"/Encrypt" not in data, (
        "Encryption dictionary should be absent after removal"
    )
    assert pdf.encrypted is False
    assert pdf.password is None


def test_password_roundtrip_removal():
    pdf = _minimal_pdf()
    pdf.add_password("first")
    pdf.remove_password()
    # After removal we can add a new password again.
    pdf.add_password("second")
    data = pdf.to_bytes()
    assert b"/Encrypt" in data
    pdf.remove_password()
    data2 = pdf.to_bytes()
    assert b"/Encrypt" not in data2


def test_remove_without_password():
    pdf = _minimal_pdf()
    # Removing password from a non‑encrypted PDF should be a no‑op, not raise.
    pdf.remove_password()
    data = pdf.to_bytes()
    assert b"/Encrypt" not in data
    assert pdf.encrypted is False


def test_add_overwrites_existing_password():
    pdf = _minimal_pdf()
    pdf.add_password("first")
    data1 = pdf.to_bytes()
    assert b"/Encrypt" in data1
    # Adding another password should overwrite the previous one without error.
    pdf.add_password("second")
    data2 = pdf.to_bytes()
    assert b"/Encrypt" in data2
    # Ensure the password attribute reflects the newest value.
    assert pdf.password == "second"

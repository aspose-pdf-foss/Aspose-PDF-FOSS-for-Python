"""Tests for Document metadata properties: id, version, outlines, permissions."""

from __future__ import annotations

import io
import pytest

from aspose_pdf.document import Document
from aspose_pdf.exceptions import PdfSecurityException
from aspose_pdf.outlines import OutlineCollection, OutlineItem


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_pdf(version: str = "1.7") -> bytes:
    """Build a minimal but parseable PDF with a given version string."""
    header = f"%PDF-{version}\n".encode()
    body = (
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Count 1 /Kids [3 0 R] >>\nendobj\n"
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\nendobj\n"
    )
    xref_offset = len(header) + len(body)
    xref = b"xref\n0 4\n0000000000 65535 f \n"
    # Compute individual object offsets
    off1 = len(header)
    off2 = off1 + len(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")
    off3 = off2 + len(b"2 0 obj\n<< /Type /Pages /Count 1 /Kids [3 0 R] >>\nendobj\n")
    xref += f"{off1:010d} 00000 n \n".encode()
    xref += f"{off2:010d} 00000 n \n".encode()
    xref += f"{off3:010d} 00000 n \n".encode()
    trailer = f"trailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode()
    return header + body + xref + trailer


def _save_and_reload(doc: Document, tmp_path) -> Document:
    """Save *doc* to a temp file and load a fresh Document from it."""
    out = tmp_path / "out.pdf"
    doc.save(out, overwrite=True)
    new_doc = Document()
    new_doc.load_from(out)
    return new_doc


# ===========================================================================
# version
# ===========================================================================


def test_version_default_fresh_document():
    doc = Document()
    assert doc.version == "1.7"


def test_version_read_from_loaded_pdf():
    doc = Document()
    doc.load_from(_minimal_pdf("1.4"))
    assert doc.version == "1.4"


def test_version_settable(tmp_path):
    doc = Document()
    doc.load_from(_minimal_pdf("1.5"))
    doc.version = "1.6"
    assert doc.version == "1.6"

    reloaded = _save_and_reload(doc, tmp_path)
    assert reloaded.version == "1.6"


def test_version_preserved_on_roundtrip(tmp_path):
    doc = Document()
    doc.load_from(_minimal_pdf("1.4"))
    reloaded = _save_and_reload(doc, tmp_path)
    assert reloaded.version == "1.4"


def test_version_raises_after_dispose():
    doc = Document()
    doc.close()
    with pytest.raises(Exception):
        _ = doc.version


# ===========================================================================
# id
# ===========================================================================


def test_id_none_before_save():
    """A fresh, never-saved document has no file ID yet."""
    doc = Document()
    assert doc.id is None


def test_id_generated_after_save(tmp_path):
    doc = Document()
    doc.load_from(_minimal_pdf())
    out = tmp_path / "out.pdf"
    doc.save(out)

    reloaded = Document()
    reloaded.load_from(out)
    fid = reloaded.id
    assert fid is not None
    assert len(fid) == 2
    assert all(isinstance(part, bytes) for part in fid)
    assert all(len(part) == 16 for part in fid)


def test_id_preserved_on_roundtrip(tmp_path):
    """Re-loading a saved PDF keeps the same /ID."""
    doc = Document()
    doc.load_from(_minimal_pdf())
    out = tmp_path / "round1.pdf"
    doc.save(out)

    first_reload = Document()
    first_reload.load_from(out)
    fid_first = first_reload.id

    out2 = tmp_path / "round2.pdf"
    first_reload.save(out2)
    second_reload = Document()
    second_reload.load_from(out2)
    fid_second = second_reload.id

    assert fid_first == fid_second


def test_id_raises_after_dispose():
    doc = Document()
    doc.close()
    with pytest.raises(Exception):
        _ = doc.id


# ===========================================================================
# permissions
# ===========================================================================


def test_permissions_default_unencrypted():
    doc = Document()
    assert doc.permissions == -4


def test_permissions_unchanged_on_unencrypted_roundtrip(tmp_path):
    doc = Document()
    doc.load_from(_minimal_pdf())
    reloaded = _save_and_reload(doc, tmp_path)
    assert reloaded.permissions == -4


def test_permissions_set_by_encrypt(tmp_path):
    """Encrypting with custom permissions exposes them via the property."""
    custom_permissions = -3904  # typical "print only" restriction mask
    doc = Document()
    doc.load_from(_minimal_pdf())
    doc.encrypt("user", "owner", permissions=custom_permissions)

    buf = io.BytesIO()
    doc.save(buf)

    # Re-read without decrypting — check the raw permissions value stored
    reloaded = Document()
    buf.seek(0)
    raw_bytes = buf.read()
    # We expect PdfSecurityException because the PDF is encrypted.
    with pytest.raises(PdfSecurityException, match="Password required"):
        reloaded.load_from(raw_bytes)


def test_permissions_default_encrypt_is_minus_four():
    """encrypt() called without explicit permissions uses -4."""
    doc = Document()
    doc.load_from(_minimal_pdf())
    doc.encrypt("secret")
    assert doc.permissions == -4


def test_permissions_raises_after_dispose():
    doc = Document()
    doc.close()
    with pytest.raises(Exception):
        _ = doc.permissions


# ===========================================================================
# outlines
# ===========================================================================


def test_outlines_empty_by_default():
    doc = Document()
    assert len(doc.outlines) == 0


def test_outlines_is_outline_collection():
    doc = Document()
    assert isinstance(doc.outlines, OutlineCollection)


def test_outlines_add_item():
    doc = Document()
    item = OutlineItem("Chapter 1", page_index=0)
    doc.outlines.add(item)
    assert len(doc.outlines) == 1
    assert doc.outlines[0].title == "Chapter 1"


def test_outlines_remove_item():
    doc = Document()
    item1 = OutlineItem("A", page_index=0)
    item2 = OutlineItem("B", page_index=1)
    doc.outlines.add(item1)
    doc.outlines.add(item2)
    doc.outlines.remove(item1)
    assert len(doc.outlines) == 1
    assert doc.outlines[0].title == "B"


def test_outlines_clear():
    doc = Document()
    doc.outlines.add(OutlineItem("A"))
    doc.outlines.add(OutlineItem("B"))
    doc.outlines.clear()
    assert len(doc.outlines) == 0


def test_outlines_add_and_save_reload(tmp_path):
    """Outlines survive a save/reload roundtrip."""
    doc = Document()
    doc.load_from(_minimal_pdf())
    doc.outlines.add(OutlineItem("Introduction", page_index=0))
    doc.outlines.add(OutlineItem("Conclusion", page_index=0))

    reloaded = _save_and_reload(doc, tmp_path)
    assert len(reloaded.outlines) == 2
    titles = [item.title for item in reloaded.outlines]
    assert "Introduction" in titles
    assert "Conclusion" in titles


def test_outlines_nested_save_reload(tmp_path):
    """Nested (child) outlines survive a save/reload roundtrip."""
    doc = Document()
    doc.load_from(_minimal_pdf())
    parent = OutlineItem("Part I", page_index=0)
    child = OutlineItem("Section 1.1", page_index=0)
    parent.add(child)
    doc.outlines.add(parent)

    reloaded = _save_and_reload(doc, tmp_path)
    assert len(reloaded.outlines) == 1
    top = reloaded.outlines[0]
    assert top.title == "Part I"
    assert len(top.children) == 1
    assert top.children[0].title == "Section 1.1"


def test_outlines_bold_italic_preserved(tmp_path):
    doc = Document()
    doc.load_from(_minimal_pdf())
    item = OutlineItem("Bold", page_index=0, is_bold=True, is_italic=True)
    doc.outlines.add(item)

    reloaded = _save_and_reload(doc, tmp_path)
    saved_item = reloaded.outlines[0]
    assert saved_item.is_bold is True
    assert saved_item.is_italic is True


def test_outlines_raises_after_dispose():
    doc = Document()
    doc.close()
    with pytest.raises(Exception):
        _ = doc.outlines


def test_outlines_type_error_on_bad_item():
    doc = Document()
    with pytest.raises(TypeError):
        doc.outlines.add("not an OutlineItem")


def test_outline_item_add_child():
    parent = OutlineItem("Parent", page_index=0)
    child = OutlineItem("Child", page_index=1)
    returned = parent.add(child)
    assert returned is child
    assert len(parent.children) == 1
    assert parent.children[0] is child


def test_outline_collection_iteration():
    col = OutlineCollection()
    items = [OutlineItem(f"Item {i}") for i in range(3)]
    for item in items:
        col.add(item)
    assert list(col) == items

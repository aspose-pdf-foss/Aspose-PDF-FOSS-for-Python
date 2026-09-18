"""``aspose_pdf.generated.*`` gives the real classes, not an older copy of them.

``generated.document.Document`` was a second implementation that had fallen
behind the canonical one: ``merge`` appended blank pages, ``save`` rewrote a
signed file and broke its signature, ``decrypt`` accepted a wrong password on a
loaded encrypted file, and ``info``, ``version``, ``form``, ``outlines`` and
``permissions`` were always ``None``. Each module there now re-exports the
canonical class, as ``generated.security`` already did.
"""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document as CanonicalDocument
from aspose_pdf.engine.sign_field import sign_field
from aspose_pdf.engine.signing import SigningUtils
from aspose_pdf.generated.document import Document


def _bytes(document) -> bytes:
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _with_text(text: str) -> bytes:
    document = CanonicalDocument()
    document.pages.add().add_text(text, 72, 700, font_size=18)
    return _bytes(document)


def test_it_is_the_canonical_document():
    assert Document is CanonicalDocument


def test_merge_carries_the_pages_content():
    document = Document(io.BytesIO(_with_text("First")))
    document.merge(Document(io.BytesIO(_with_text("Second"))))
    merged = Document(io.BytesIO(_bytes(document)))
    assert [page.extract_text().strip() for page in merged.pages] == ["First", "Second"]


def test_saving_a_signed_document_keeps_its_signature():
    authored = CanonicalDocument()
    page = authored.pages.add()
    page.add_text("Signed", 72, 700, font_size=18)
    authored.form.add_signature_field("Sig1", page, (300, 600, 500, 680))
    cert, key = SigningUtils.create_self_signed_cert()
    signed = sign_field(_bytes(authored), "Sig1", cert, key)

    document = Document(io.BytesIO(signed))
    document.info["Title"] = "Edited"
    resaved = Document(io.BytesIO(_bytes(document)))
    assert [signature.valid for signature in resaved.signatures] == [True]


def test_decrypt_refuses_a_wrong_password():
    source = CanonicalDocument()
    source.pages.add()
    source.encrypt("user", "owner")
    document = Document(io.BytesIO(_bytes(source)), password="user")
    with pytest.raises(Exception):
        document.decrypt("WRONG")


def test_document_properties_are_populated():
    document = Document(io.BytesIO(_with_text("Body")))
    assert document.info is not None
    assert document.version
    assert document.form is not None
    assert document.outlines is not None
    assert document.permissions is not None

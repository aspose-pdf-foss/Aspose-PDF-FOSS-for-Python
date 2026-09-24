"""Signing through the public ``Document``: ``sign``, ``add_ltv``, ``add_document_timestamp``.

Signing a document took the engine: ``SimplePdf.signing_creds``,
``engine.sign_field.sign_field`` and ``engine.dss``. The three are methods now,
queued and done by ``save`` -- a signature covers bytes, so it is made on the
bytes the save writes, as an appended revision -- after which the document is
the file it wrote.

Found on the way and fixed where they live: a document timestamp's
``/ByteRange`` left the ``<``/``>`` of its ``/Contents`` inside the signed
ranges (pyHanko: coverage unclear), a second one reused the field name
``Timestamp`` (pyHanko: suspicious modification of every earlier signature), a
second ``/DSS`` replaced the first one's material, and a certifying signature
could be put on a document that was signed already (pyHanko: the earlier
signature's DocMDP check fails).
"""

from __future__ import annotations

import dataclasses
import hashlib
import io
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed448, ed25519
from cryptography.x509.oid import NameOID

from aspose_pdf import CertificationLevel, Document, Recipient
from aspose_pdf.engine import cms as cms_mod
from aspose_pdf.engine import dss
from aspose_pdf.engine.sign_field import sign_field
from aspose_pdf.engine.signing import SigningUtils
from aspose_pdf.exceptions import PdfSecurityException, PdfValidationException
from aspose_pdf.outlines import OutlineItem
from aspose_pdf.validation import (
    PadesLevel,
    RevocationStatus,
    ValidationOptions,
    ValidationStatus,
)
from tests.helpers_signatures import timestamp_authority


@pytest.fixture(scope="module")
def chain():
    root, root_key = SigningUtils.create_self_signed_ca("Signing Root")
    leaf, leaf_key = SigningUtils.issue_certificate("Signer", root, root_key)
    return root, root_key, leaf, leaf_key


@pytest.fixture(scope="module")
def tsa():
    return timestamp_authority()


def _saved(document: Document, **kwargs) -> bytes:
    buffer = io.BytesIO()
    document.save(buffer, **kwargs)
    return buffer.getvalue()


def _with_field(name: str = "Approval") -> Document:
    document = Document()
    page = document.pages.add()
    page.add_text("Signed body", 72, 700, font_size=18)
    document.form.add_signature_field(name, page, (300, 600, 500, 680))
    return document


def _crl(issuer, issuer_key) -> x509.CertificateRevocationList:
    now = datetime.now(UTC)
    return (
        x509.CertificateRevocationListBuilder()
        .issuer_name(issuer.subject)
        .last_update(now - timedelta(hours=1))
        .next_update(now + timedelta(days=1))
        .sign(issuer_key, hashes.SHA256())
    )


def _der(item) -> bytes:
    return item.public_bytes(serialization.Encoding.DER)


def _covers_whole_file(signature, data: bytes) -> bool:
    start, gap_start, gap_end, tail = signature.byte_range
    return (
        start == 0
        and gap_end + tail == len(data)
        and data[gap_start : gap_start + 1] == b"<"
        and data[gap_end - 1 : gap_end] == b">"
    )


# --- signing ------------------------------------------------------------------------------


def test_sign_fills_the_named_field_on_save(chain):
    root, _, leaf, key = chain
    document = _with_field()
    document.sign("Approval", certificate=leaf, private_key=key, extra_certificates=[root], reason="Approved")
    assert document.signatures == []  # nothing is signed until the bytes exist
    data = _saved(document)

    signatures = Document(io.BytesIO(data)).signatures
    assert [(s.name, s.valid, s.reason) for s in signatures] == [("Approval", True, "Approved")]
    assert _covers_whole_file(signatures[0], data)


def test_after_the_save_the_document_is_the_file_it_wrote(chain):
    _, _, leaf, key = chain
    document = _with_field()
    outlines = document.outlines
    outlines.add(OutlineItem("Start", page_index=0))
    assert [field.name for field in document.form.fields] == ["Approval"]
    document.sign(certificate=leaf, private_key=key)
    signed = _saved(document)
    assert [s.name for s in document.signatures] == ["Signature1"]
    # The form read before the save is not the one after it.
    assert sorted(field.name for field in document.form.fields) == ["Approval", "Signature1"]

    # Saved again untouched: the signature is not made twice.
    assert _saved(document) == signed
    # Edited -- through the bookmarks read before the save, too -- and saved:
    # a revision appended to the signed file, the signature intact.
    outlines.add(OutlineItem("Later", page_index=0))
    document.info["Title"] = "Edited after signing"
    edited = _saved(document)
    assert edited.startswith(signed)
    reopened = Document(io.BytesIO(edited))
    assert [item.title for item in reopened.outlines] == ["Start", "Later"]
    assert [s.valid for s in reopened.signatures] == [True]


def test_sign_without_a_field_adds_an_invisible_one(chain):
    _, _, leaf, key = chain
    document = Document()
    document.pages.add()
    document.form.add_signature_field("Signature1", document.pages[0], (10, 10, 100, 50))
    document.sign(certificate=leaf, private_key=key)
    document.sign(certificate=leaf, private_key=key)
    reopened = Document(io.BytesIO(_saved(document)))
    assert [s.name for s in reopened.signatures] == ["Signature2", "Signature3"]
    assert all(s.valid for s in reopened.signatures)


def test_a_signed_document_is_signed_again_without_breaking_the_first(chain):
    _, _, leaf, key = chain
    document = _with_field()
    document.form.add_signature_field("Second", document.pages[0], (300, 500, 500, 580))
    once = sign_field(_saved(document), "Approval", leaf, key)

    loaded = Document(io.BytesIO(once))
    loaded.sign("Second", certificate=leaf, private_key=key)
    twice = _saved(loaded)
    assert twice.startswith(once)
    assert [(s.name, s.valid) for s in Document(io.BytesIO(twice)).signatures] == [
        ("Approval", True),
        ("Second", True),
    ]


def test_pades_with_a_timestamp_is_pades_t(chain, tsa):
    root, _, leaf, key = chain
    document = _with_field()
    document.sign("Approval", certificate=leaf, private_key=key, extra_certificates=[root], pades=True, timestamp_authority=tsa)
    signature = Document(io.BytesIO(_saved(document))).signatures[0]
    assert signature.sub_filter == "ETSI.CAdES.detached"
    result = signature.validate(ValidationOptions(trusted_certificates=[root, tsa[0]], check_timestamp=True))
    assert result.status == ValidationStatus.VALID
    assert result.pades_level == PadesLevel.T


def _self_signed(key):
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, type(key).__name__)])
    now = datetime.now(UTC)
    return (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(7)
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(key, None)
    )


def test_an_ed25519_key_signs_as_pades_with_sha512():
    key = ed25519.Ed25519PrivateKey.generate()
    certificate = _self_signed(key)
    document = _with_field()
    with pytest.raises(PdfValidationException, match="pades=True"):
        document.sign("Approval", certificate=certificate, private_key=key)
    document.sign("Approval", certificate=certificate, private_key=key, pades=True)
    signature = Document(io.BytesIO(_saved(document))).signatures[0]
    assert signature.valid
    # RFC 8419 3.1: Ed25519 with signed attributes digests with SHA-512;
    # pyHanko rejects any other pairing (CRYPTO_CONSTRAINTS_FAILURE).
    assert cms_mod.parse_signed_data(signature.contents).digest_algo == "sha512"


def test_an_ed448_key_is_refused():
    key = ed448.Ed448PrivateKey.generate()
    certificate = _self_signed(key)
    with pytest.raises(PdfValidationException, match="SHAKE256"):
        _with_field().sign("Approval", certificate=certificate, private_key=key, pades=True)
    # The engine refuses it too, rather than write a digest validators reject.
    with pytest.raises(PdfSecurityException, match="SHAKE256"):
        sign_field(_saved(_with_field()), "Approval", certificate, key, pades=True)


def test_a_local_timestamp_authority_signs_with_rsa(chain):
    root, _, leaf, key = chain
    ed_key = ed25519.Ed25519PrivateKey.generate()
    with pytest.raises(PdfValidationException, match="RSA"):
        _with_field().sign("Approval", certificate=leaf, private_key=key, timestamp_authority=(_self_signed(ed_key), ed_key))
    with pytest.raises(PdfValidationException, match="does not belong"):
        _with_field().add_document_timestamp(timestamp_authority=(root, key))


@pytest.mark.parametrize("level", [CertificationLevel.FORM_FILLING, 3])
def test_certify_makes_a_docmdp_signature(chain, level):
    _, _, leaf, key = chain
    document = _with_field()
    document.sign("Approval", certificate=leaf, private_key=key, certify=level)
    signature = Document(io.BytesIO(_saved(document))).signatures[0]
    assert signature.valid
    assert signature.docmdp_level == (level.value if isinstance(level, CertificationLevel) else level)


def test_certification_has_to_be_the_first_signature(chain):
    _, _, leaf, key = chain
    document = _with_field()
    document.form.add_signature_field("Second", document.pages[0], (300, 500, 500, 580))
    unsigned = _saved(document)
    signed = sign_field(unsigned, "Approval", leaf, key)

    with pytest.raises(PdfSecurityException, match="first"):
        Document(io.BytesIO(signed)).sign("Second", certificate=leaf, private_key=key, certify=1)
    queued = Document(io.BytesIO(unsigned)).sign("Approval", certificate=leaf, private_key=key)
    with pytest.raises(PdfSecurityException, match="first"):
        queued.sign("Second", certificate=leaf, private_key=key, certify=1)
    # The engine refuses it too, whoever calls it.
    with pytest.raises(PdfSecurityException, match="first"):
        sign_field(signed, "Second", leaf, key, certify_permissions=2)


def test_a_signature_deep_in_the_field_tree_counts_as_signed(chain):
    _, _, leaf, key = chain
    document = Document()
    page = document.pages.add()
    document.form.add_signature_field("group.First", page, (300, 600, 500, 680))
    document.form.add_signature_field("Second", page, (300, 500, 500, 580))
    signed = sign_field(_saved(document), "group.First", leaf, key)
    with pytest.raises(PdfSecurityException, match="first"):
        sign_field(signed, "Second", leaf, key, certify_permissions=1)


# --- what sign() refuses when it is called -----------------------------------------------------


def test_sign_rejects_a_field_it_cannot_fill(chain):
    _, _, leaf, key = chain
    document = _with_field()
    document.form.add_text_field("Name", document.pages[0], (72, 600, 272, 624))
    with pytest.raises(PdfValidationException, match="not found"):
        document.sign("Missing", certificate=leaf, private_key=key)
    with pytest.raises(PdfValidationException, match="not a signature field"):
        document.sign("Name", certificate=leaf, private_key=key)
    document.sign("Approval", certificate=leaf, private_key=key)
    with pytest.raises(PdfValidationException, match="already"):
        document.sign("Approval", certificate=leaf, private_key=key)

    signed = Document(io.BytesIO(_saved(document)))
    with pytest.raises(PdfSecurityException, match="already signed"):
        signed.sign("Approval", certificate=leaf, private_key=key)


def test_sign_rejects_a_key_that_is_not_the_certificates(chain):
    root, root_key, leaf, _ = chain
    with pytest.raises(PdfValidationException, match="does not belong"):
        _with_field().sign("Approval", certificate=leaf, private_key=root_key)
    with pytest.raises(TypeError, match=r"x509\.Certificate"):
        _with_field().sign("Approval", certificate=_der(leaf), private_key=root_key)
    with pytest.raises(TypeError, match="private key"):
        _with_field().sign("Approval", certificate=root, private_key=b"key")
    with pytest.raises(TypeError, match="extra_certificates"):
        _with_field().sign("Approval", certificate=root, private_key=root_key, extra_certificates=[_der(root)])


def test_sign_checks_its_timestamp_and_certification_arguments(chain, tsa):
    root, root_key, _, _ = chain
    document = _with_field()
    with pytest.raises(PdfValidationException, match="not both"):
        document.sign("Approval", certificate=root, private_key=root_key, timestamp_url="http://tsa.invalid", timestamp_authority=tsa)
    with pytest.raises(TypeError, match="pair"):
        document.sign("Approval", certificate=root, private_key=root_key, timestamp_authority=tsa[0])
    with pytest.raises(PdfValidationException, match="certify"):
        document.sign("Approval", certificate=root, private_key=root_key, certify=4)
    with pytest.raises(PdfValidationException, match="certify"):
        document.sign("Approval", certificate=root, private_key=root_key, certify=True)
    with pytest.raises(TypeError, match="reason"):
        document.sign("Approval", certificate=root, private_key=root_key, reason=b"bytes")
    with pytest.raises(TypeError, match="timestamp_url"):
        document.sign("Approval", certificate=root, private_key=root_key, timestamp_url="")
    assert document._pending_signing == []


def test_an_ed25519_signer_follows_only_a_sha512_seed(chain):
    key = ed25519.Ed25519PrivateKey.generate()
    certificate = _self_signed(key)

    def authored(**seed):
        document = Document()
        page = document.pages.add()
        document.form.add_signature_field("Approval", page, (60, 600, 260, 680), seed_value=seed)
        return _saved(document)

    with pytest.raises(PdfSecurityException, match="DigestMethod"):
        sign_field(authored(digest_method=["SHA256"], required=["digest_method"]), "Approval", certificate, key, pades=True)
    advisory = sign_field(authored(digest_method=["SHA256"]), "Approval", certificate, key, pades=True)
    signature = Document(io.BytesIO(advisory)).signatures[0]
    assert signature.valid and cms_mod.parse_signed_data(signature.contents).digest_algo == "sha512"


def test_a_refused_save_keeps_what_was_queued(chain, tmp_path, monkeypatch):
    from aspose_pdf.engine import timestamp as ts_mod

    def no_network(*args, **kwargs):
        raise AssertionError("a save refused for its destination must not reach the authority")

    monkeypatch.setattr(ts_mod, "request_timestamp", no_network)
    _, _, leaf, key = chain
    source, target = tmp_path / "unsigned.pdf", tmp_path / "signed.pdf"
    source.write_bytes(_saved(_with_field()))
    target.write_bytes(b"not overwritten")
    document = Document(source)
    document.sign("Approval", certificate=leaf, private_key=key, timestamp_url="http://tsa.invalid/")
    with pytest.raises(FileExistsError):
        document.save(target)
    assert target.read_bytes() == b"not overwritten"

    monkeypatch.undo()
    document._pending_signing[0] = dataclasses.replace(document._pending_signing[0], timestamp_url=None)
    document.save(target, overwrite=True)
    assert [s.valid for s in Document(target).signatures] == [True]
    assert document.file_name == str(source)


def test_protection_changed_after_sign_is_refused_on_save(chain):
    root, _, leaf, key = chain
    document = _with_field()
    document.sign("Approval", certificate=leaf, private_key=key)
    document.encrypt_for_recipients([Recipient(root)], ignore_key_usage=True)
    buffer = io.BytesIO()
    with pytest.raises(PdfSecurityException, match="recipient"):
        document.save(buffer)
    assert buffer.getvalue() == b""


def test_loading_another_file_drops_what_was_queued(chain):
    _, _, leaf, key = chain
    document = _with_field()
    document.sign("Approval", certificate=leaf, private_key=key)
    document.load_from(_saved(_with_field("Other")))
    assert Document(io.BytesIO(_saved(document))).signatures == []


# --- encrypted documents -----------------------------------------------------------------------


def test_an_encrypted_document_is_signed_and_reopened(chain):
    _, _, leaf, key = chain
    document = _with_field()
    document.encrypt("user", "owner", algorithm="AES-128")
    document.sign("Approval", certificate=leaf, private_key=key)
    data = _saved(document)
    assert [s.valid for s in document.signatures] == [True]
    assert [s.valid for s in Document(io.BytesIO(data), password="user").signatures] == [True]

    # Opened with its password, signed again, and still protected.
    loaded = Document(io.BytesIO(data), password="owner")
    loaded.form.add_signature_field("Second", loaded.pages[0], (300, 500, 500, 580))
    loaded.save(io.BytesIO(), incremental=True)  # an appendable, encrypted document
    loaded.sign("Second", certificate=leaf, private_key=key)
    resigned = _saved(loaded)
    assert b"/Encrypt" in resigned[len(data) :]
    assert [s.valid for s in Document(io.BytesIO(resigned), password="user").signatures] == [True, True]


@pytest.mark.parametrize("algorithm", ["AES-256", "AES-128", "RC4"])
def test_an_encrypted_document_gets_its_store_and_timestamp(chain, tsa, algorithm):
    # The store and the timestamp field are enciphered with the file's own
    # key, as an incremental save writes anything else. pyHanko opens each of
    # these files, reads three certificates out of the store and reports both
    # signatures intact, valid and trusted.
    root, _, leaf, key = chain
    document = _with_field()
    document.encrypt("user", "owner", algorithm=algorithm)
    document.sign("Approval", certificate=leaf, private_key=key, extra_certificates=[root], pades=True, timestamp_authority=tsa)
    document.add_ltv(certificates=[tsa[0]])
    document.add_document_timestamp(timestamp_authority=tsa)
    data = _saved(document)

    reopened = Document(io.BytesIO(data), password="user")
    assert [(s.name, s.valid) for s in reopened.signatures] == [("Approval", True), ("Timestamp", True)]
    result = reopened.signatures[0].validate(
        ValidationOptions(trusted_certificates=[root, tsa[0]], check_timestamp=True)
    )
    # Certificates alone do not establish embedded revocation evidence.
    assert (result.status, result.pades_level) == (ValidationStatus.VALID, PadesLevel.T)

    handler = reopened._engine_pdf._writer_encryption()
    with_key = dss.read_dss(data, encryption=handler)
    assert _der(root) in with_key.certs and _der(tsa[0]) in with_key.certs
    # Nothing in the store, nor the timestamp field's name, is in the clear.
    assert dss.read_dss(data).certs != with_key.certs
    assert b"Timestamp" not in data[len(_saved(_with_field())) :]


def test_an_encrypted_store_is_read_back_when_a_signature_is_checked(chain):
    # Revocation data only the store holds: validation has to decrypt it to
    # find the CRL, or the signer's status stays unknown.
    root, root_key, leaf, key = chain
    document = _with_field()
    document.encrypt("user", "owner")
    document.sign("Approval", certificate=leaf, private_key=key, extra_certificates=[root], pades=True)
    document.add_ltv(crls=[_crl(root, root_key)])
    reopened = Document(io.BytesIO(_saved(document)), password="user")
    result = reopened.signatures[0].validate(
        ValidationOptions(trusted_certificates=[root], check_revocation=True)
    )
    assert (result.status, result.revocation_status) == (ValidationStatus.VALID, RevocationStatus.GOOD)


def test_an_encrypted_documents_own_strings_survive_the_update(chain, tsa):
    # The graph is decrypted to be read and enciphered again on the way out;
    # left as it came, every string re-emitted around the store would be
    # encrypted twice.
    from aspose_pdf.engine.cos import PdfName, PdfString

    root, _, leaf, key = chain
    document = _with_field()
    engine = document._engine_pdf
    engine._ensure_cos()
    catalog = engine._resolve(engine._cos_doc.trailer.mapping[PdfName("Root")])
    catalog.mapping[PdfName("Lang")] = PdfString("en-GB")
    catalog.mapping[PdfName("XNote")] = PdfString("kept through the update")
    document.encrypt("user", "owner")
    document.sign("Approval", certificate=leaf, private_key=key, extra_certificates=[root])
    document.add_ltv()
    document.add_document_timestamp(timestamp_authority=tsa)
    document.add_document_timestamp(timestamp_authority=tsa)
    data = _saved(document)

    # Nothing re-emitted around the store is written in the clear; this
    # reader recovers a string that is, but a strict one shows noise.
    assert b"kept through the update" not in data
    reopened = Document(io.BytesIO(data), password="user")
    engine = reopened._engine_pdf
    catalog = engine._resolve(engine._cos_doc.trailer.mapping[PdfName("Root")])
    assert engine._resolve(catalog.mapping[PdfName("Lang")]).value == b"en-GB"
    assert engine._resolve(catalog.mapping[PdfName("XNote")]).value == b"kept through the update"
    acroform = engine._resolve(catalog.mapping[PdfName("AcroForm")])
    assert b"Helv" in engine._resolve(acroform.mapping[PdfName("DA")]).value
    # The second timestamp reads the first one's name to avoid repeating it.
    assert [s.name for s in reopened.signatures] == ["Approval", "Timestamp", "Timestamp2"]
    assert all(s.valid for s in reopened.signatures)


def test_a_signature_nested_under_a_parent_field_is_harvested(chain):
    root, _, leaf, key = chain
    document = Document()
    page = document.pages.add()
    document.form.add_signature_field("group.inner", page, (300, 600, 500, 680))
    document.sign("group.inner", certificate=leaf, private_key=key, extra_certificates=[root])
    document.add_ltv()
    data = _saved(document)
    assert _der(root) in dss.read_dss(data).certs


def test_a_store_added_to_a_document_that_is_being_encrypted(chain, tsa):
    # The protection is applied by this very save, so the handler the store is
    # written with is the one the save produces.
    root, _, leaf, key = chain
    document = _with_field()
    document.sign("Approval", certificate=leaf, private_key=key, extra_certificates=[root])
    document.add_ltv()
    document.encrypt("user", "owner")
    data = _saved(document)
    reopened = Document(io.BytesIO(data), password="user")
    assert [s.valid for s in reopened.signatures] == [True]
    handler = reopened._engine_pdf._writer_encryption()
    assert _der(leaf) in dss.read_dss(data, encryption=handler).certs


def test_a_document_opened_with_a_recipient_credential_is_signed_and_reopened(chain, tsa):
    root, _, leaf, key = chain
    recipient, recipient_key = SigningUtils.create_self_signed_cert()
    document = _with_field()
    document.encrypt_for_recipients([Recipient(recipient)], ignore_key_usage=True)
    encrypted = _saved(document)

    opened = Document(io.BytesIO(encrypted), certificate=recipient, private_key=recipient_key)
    opened.sign("Approval", certificate=leaf, private_key=key, extra_certificates=[root], pades=True, timestamp_authority=tsa)
    opened.add_ltv(certificates=[tsa[0]])
    opened.add_document_timestamp(timestamp_authority=tsa)
    signed = _saved(opened)
    assert [s.valid for s in opened.signatures] == [True, True]
    reopened = Document(io.BytesIO(signed), certificate=recipient, private_key=recipient_key)
    assert [s.valid for s in reopened.signatures] == [True, True]
    result = reopened.signatures[0].validate(
        ValidationOptions(trusted_certificates=[root, tsa[0]], check_timestamp=True)
    )
    # This store contains certificates, but no CRL or OCSP evidence.
    assert result.pades_level == PadesLevel.T


def test_a_document_encrypted_for_recipients_is_not_signed_here(chain):
    root, _, leaf, key = chain
    document = _with_field()
    document.encrypt_for_recipients([Recipient(root)], ignore_key_usage=True)
    with pytest.raises(PdfSecurityException, match="recipient"):
        document.sign("Approval", certificate=leaf, private_key=key)


# --- long-term validation and document timestamps ------------------------------------------------


def test_add_ltv_writes_the_signatures_material_and_what_is_given(chain):
    root, root_key, leaf, key = chain
    crl = _crl(root, root_key)
    document = _with_field()
    document.sign("Approval", certificate=leaf, private_key=key, extra_certificates=[root])
    document.add_ltv(crls=[crl])
    data = _saved(document)
    material = dss.read_dss(data)
    assert _der(root) in material.certs and _der(leaf) in material.certs
    assert material.crls == [_der(crl)]
    assert [s.valid for s in Document(io.BytesIO(data)).signatures] == [True]


def test_a_second_store_keeps_the_first_ones_material(chain):
    root, root_key, leaf, key = chain
    first_crl, second_crl = _crl(root, root_key), _crl(leaf, key)
    document = _with_field()
    document.sign("Approval", certificate=leaf, private_key=key)
    document.add_ltv(crls=[first_crl])
    document.save(io.BytesIO())
    document.add_ltv(crls=[second_crl])
    assert dss.read_dss(_saved(document)).crls == [_der(first_crl), _der(second_crl)]


def test_a_store_is_extended_in_place(chain):
    from aspose_pdf.engine.pdf_parser_cos import PdfCosParser

    root, root_key, leaf, key = chain
    document = _with_field()
    document.sign("Approval", certificate=leaf, private_key=key, extra_certificates=[root])
    document.add_ltv(crls=[_crl(root, root_key)])
    first = _saved(document)

    def store(data):
        doc = PdfCosParser(data).parse()
        catalog = doc.get_object(doc.trailer.get(dss.PdfName("Root")))
        ref = catalog.get(dss.PdfName("DSS"))
        value = doc.get_object(ref)
        writer = dss.PdfCosWriter(doc)
        certs = [item.object_number for item in value.get(dss.PdfName("Certs")).items]
        return ref.object_number, certs, writer.serialize_object(value.get(dss.PdfName("VRI")))

    # Nothing new to add: no revision at all.
    document.add_ltv()
    assert _saved(document) == first
    # Something new: the store's object, its streams and its /VRI entry stay.
    document.add_ltv(ocsp_responses=[b"\x30\x03\x0a\x01\x01"])
    second = _saved(document)
    number, certs, vri = store(first)
    assert store(second) == (number, certs, vri)
    assert dss.read_dss(second).ocsps == [b"\x30\x03\x0a\x01\x01"]
    assert second.count(_der(root)) == first.count(_der(root))


def test_a_store_keeps_other_signatures_vri_entries():
    from aspose_pdf.engine.pdf_parser_cos import PdfCosParser

    cert, key = SigningUtils.create_self_signed_cert()
    document = _with_field()
    data = sign_field(_saved(document), "Approval", cert, key)
    # A store whose /VRI has an entry for some other signature.
    with_other = dss.build_dss(data, dss.DssMaterial(certs=[_der(cert)]), vri_contents=b"other signature")
    rebuilt = dss.enable_ltv(with_other)
    doc = PdfCosParser(rebuilt).parse()
    catalog = doc.get_object(doc.trailer.get(dss.PdfName("Root")))
    store = doc.get_object(catalog.get(dss.PdfName("DSS")))
    keys = {name.name for name in store.get(dss.PdfName("VRI")).mapping}
    assert "/" + hashlib.sha1(b"other signature").hexdigest().upper() in keys
    assert len(keys) == 2


def test_document_timestamps_cover_the_file_and_are_named_apart(chain, tsa):
    _, _, leaf, key = chain
    document = _with_field()
    document.sign("Approval", certificate=leaf, private_key=key, pades=True)
    document.add_ltv(certificates=[tsa[0]])
    document.add_document_timestamp(timestamp_authority=tsa)
    first = _saved(document)
    document.add_document_timestamp(timestamp_authority=tsa)
    second = _saved(document)
    assert second.startswith(first)

    signatures = Document(io.BytesIO(second)).signatures
    assert [(s.name, s.sub_filter) for s in signatures] == [
        ("Approval", "ETSI.CAdES.detached"),
        ("Timestamp", "ETSI.RFC3161"),
        ("Timestamp2", "ETSI.RFC3161"),
    ]
    assert all(s.valid for s in signatures)
    assert _covers_whole_file(signatures[2], second)
    stamped = signatures[1].byte_range
    assert first[stamped[1] : stamped[1] + 1] == b"<" and first[stamped[2] - 1 : stamped[2]] == b">"


def test_add_document_timestamp_needs_an_authority(tsa):
    document = _with_field()
    with pytest.raises(PdfValidationException, match="timestamp_url or a timestamp_authority"):
        document.add_document_timestamp()
    with pytest.raises(TypeError, match="sequence"):
        document.add_ltv(certificates=b"one DER blob")
    with pytest.raises(TypeError, match="DER bytes"):
        document.add_ltv(crls=["not a CRL"])

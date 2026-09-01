"""Seed value enforcement, and one signing path instead of two.

A signature field's ``/SV`` dictionary is the field author's instruction to
whoever signs it. An entry is advisory until its bit in ``/Ff`` is set, and then
a signer that cannot honour it **must not sign** (ISO 32000-1 §12.7.4.5). Only
``/SubFilter`` and ``/Reasons`` were checked; the rest were ignored, so a field
demanding SHA-512, a particular handler, a timestamp or a certifying signature
got a signature that quietly ignored the demand.

Some entries can be *followed* rather than merely checked -- a digest to sign
with, an authority to stamp with -- and those are, so the seed value shapes the
signature instead of only vetoing it.

The other half: whole-document signing (``SimplePdf.signing_creds``) had its own
implementation, which synthesised a field, patched its own byte range, and
rebuilt the file from the in-memory model on the way -- discarding whatever COS
structure the document had. It now authors a field and hands the saved bytes to
the same ``sign_field`` everything else uses.
"""

from __future__ import annotations

from io import BytesIO

import pytest
from cryptography.hazmat.primitives import serialization

from aspose_pdf import Document
from aspose_pdf.engine.cos import PdfName
from aspose_pdf.engine.sign_field import sign_field
from aspose_pdf.engine.signing import SigningUtils
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.exceptions import PdfSecurityException, PdfValidationException
from aspose_pdf.outlines import OutlineItem
from aspose_pdf.validation import ValidationOptions, ValidationStatus


@pytest.fixture(scope="module")
def creds():
    return SigningUtils.create_self_signed_cert()


def _options(cert) -> ValidationOptions:
    return ValidationOptions(
        trusted_certificates=[cert.public_bytes(serialization.Encoding.DER)]
    )


def _authored(**kwargs) -> bytes:
    document = Document()
    page = document.pages.add()
    document.form.add_signature_field("Signature1", page, (60, 600, 260, 680), **kwargs)
    output = BytesIO()
    document.save(output)
    return output.getvalue()


def _cms(signed: bytes) -> bytes:
    """The CMS blob out of the last /Contents, trimmed of its zero padding."""
    from aspose_pdf.engine.simple_pdf import _trim_der_padding

    start = signed.rfind(b"/Contents <") + len(b"/Contents <")
    end = signed.index(b">", start)
    return _trim_der_padding(bytes.fromhex(signed[start:end].decode("ascii")))


def _digest_algorithm(signed: bytes) -> str:
    from asn1crypto import cms

    content = cms.ContentInfo.load(_cms(signed))
    return content["content"]["digest_algorithms"][0]["algorithm"].native


# ---------------------------------------------------------------------------
# Entries the signer can follow
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("wanted", ["SHA256", "SHA384", "SHA512"])
def test_the_seed_value_chooses_the_digest(creds, wanted):
    cert, key = creds
    data = _authored(seed_value={"digest_method": [wanted], "required": ["digest_method"]})

    signed = sign_field(data, "Signature1", cert, key)

    assert _digest_algorithm(signed) == wanted.lower()


def test_an_advisory_digest_is_followed_too(creds):
    # Nothing forces the signer's hand here, but the field author asked, and
    # there is no reason to sign with something weaker than requested.
    cert, key = creds
    data = _authored(seed_value={"digest_method": ["SHA384"]})
    assert _digest_algorithm(sign_field(data, "Signature1", cert, key)) == "sha384"


def test_a_digest_this_signer_cannot_produce_is_refused(creds):
    # SHA-1 is in the specification's list of acceptable values. Signing with
    # it anyway, or silently substituting SHA-256, would both be wrong.
    cert, key = creds
    data = _authored(seed_value={"digest_method": ["SHA1"], "required": ["digest_method"]})

    with pytest.raises(PdfSecurityException, match="DigestMethod"):
        sign_field(data, "Signature1", cert, key)


def test_an_unusable_advisory_digest_falls_back_rather_than_refusing(creds):
    cert, key = creds
    data = _authored(seed_value={"digest_method": ["SHA1"]})
    assert _digest_algorithm(sign_field(data, "Signature1", cert, key)) == "sha256"


def test_the_first_usable_digest_in_the_list_wins(creds):
    cert, key = creds
    data = _authored(seed_value={"digest_method": ["RIPEMD160", "SHA512", "SHA256"]})
    assert _digest_algorithm(sign_field(data, "Signature1", cert, key)) == "sha512"


def test_a_seed_timestamp_url_is_used_when_the_caller_gave_none(creds, monkeypatch):
    cert, key = creds
    called: list[str] = []

    def fake_request(imprint, algo, url, timeout=10.0):
        called.append(url)
        raise RuntimeError("stop here: the URL is what this test is about")

    from aspose_pdf.engine import timestamp as ts_mod

    monkeypatch.setattr(ts_mod, "request_timestamp", fake_request)
    data = _authored(seed_value={"timestamp": {"url": "http://tsa.example/rfc3161"}})

    with pytest.raises(RuntimeError):
        sign_field(data, "Signature1", cert, key)

    assert called == ["http://tsa.example/rfc3161"]


def test_a_caller_supplied_authority_beats_the_seed_url(creds, monkeypatch):
    cert, key = creds
    called: list[str] = []
    from aspose_pdf.engine import timestamp as ts_mod

    monkeypatch.setattr(
        ts_mod,
        "request_timestamp",
        lambda imprint, algo, url, timeout=10.0: called.append(url) or b"",
    )
    data = _authored(seed_value={"timestamp": {"url": "http://seed.example/tsa"}})

    with pytest.raises(Exception):
        sign_field(
            data, "Signature1", cert, key, timestamp_url="http://caller.example/tsa"
        )

    assert called == ["http://caller.example/tsa"]


def test_a_required_timestamp_without_a_url_or_a_caller_is_refused(creds):
    cert, key = creds
    data = _authored(seed_value={"timestamp": {"required": True}})

    with pytest.raises(PdfSecurityException, match="timestamp"):
        sign_field(data, "Signature1", cert, key)


def test_lock_document_makes_the_signature_certify(creds):
    # /LockDocument /true says the document must not change after signing,
    # which is a DocMDP certification at "no changes permitted".
    cert, key = creds
    data = _authored(seed_value={"lock_document": "true"})

    signed = sign_field(data, "Signature1", cert, key)

    assert b"/DocMDP" in signed
    assert SimplePdf.from_bytes(signed).signatures[0].docmdp_level == 1


def test_lock_document_auto_and_false_leave_the_signature_alone(creds):
    cert, key = creds
    for setting in ("false", "auto"):
        signed = sign_field(
            _authored(seed_value={"lock_document": setting}), "Signature1", cert, key
        )
        assert b"/DocMDP" not in signed


def test_a_caller_who_certifies_more_strictly_is_not_overridden(creds):
    cert, key = creds
    data = _authored(seed_value={"lock_document": "true"})
    signed = sign_field(data, "Signature1", cert, key, certify_permissions=2)
    assert SimplePdf.from_bytes(signed).signatures[0].docmdp_level == 2


# ---------------------------------------------------------------------------
# Entries that can only be refused
# ---------------------------------------------------------------------------


def test_a_required_handler_this_signer_is_not_is_refused(creds):
    cert, key = creds
    data = _authored(seed_value={"filter": "Other.Handler", "required": ["filter"]})

    with pytest.raises(PdfSecurityException, match="signature handler"):
        sign_field(data, "Signature1", cert, key)


def test_the_handler_this_signer_is_passes(creds):
    cert, key = creds
    data = _authored(seed_value={"filter": "Adobe.PPKLite", "required": ["filter"]})
    assert sign_field(data, "Signature1", cert, key)


def test_an_advisory_handler_mismatch_does_not_block(creds):
    cert, key = creds
    data = _authored(seed_value={"filter": "Other.Handler"})
    assert sign_field(data, "Signature1", cert, key)


def test_a_handler_version_beyond_this_signer_is_refused(creds):
    cert, key = creds
    data = _authored(seed_value={"v": 9, "required": ["v"]})

    with pytest.raises(PdfSecurityException, match="version"):
        sign_field(data, "Signature1", cert, key)


def test_a_handler_version_this_signer_implements_passes(creds):
    cert, key = creds
    for version in (1, 2):
        data = _authored(seed_value={"v": version, "required": ["v"]})
        assert sign_field(data, "Signature1", cert, key)


def test_a_required_legal_attestation_is_refused(creds):
    # There is no attestation this signer can produce, and signing without one
    # would ignore an instruction the field author made binding.
    cert, key = creds
    data = _authored(
        seed_value={
            "legal_attestation": ["No changes were made"],
            "required": ["legal_attestation"],
        }
    )

    with pytest.raises(PdfSecurityException, match="legal attestation"):
        sign_field(data, "Signature1", cert, key)


def test_an_advisory_legal_attestation_does_not_block(creds):
    cert, key = creds
    data = _authored(seed_value={"legal_attestation": ["No changes were made"]})
    assert sign_field(data, "Signature1", cert, key)


def test_required_revocation_info_inside_the_signature_is_refused(creds):
    cert, key = creds
    data = _authored(seed_value={"add_rev_info": True, "required": ["add_rev_info"]})

    with pytest.raises(PdfSecurityException, match="revocation"):
        sign_field(data, "Signature1", cert, key)


def test_required_add_rev_info_set_to_false_is_satisfied(creds):
    # The requirement is that revocation info is *absent*, which it is.
    cert, key = creds
    data = _authored(seed_value={"add_rev_info": False, "required": ["add_rev_info"]})
    assert sign_field(data, "Signature1", cert, key)


def test_a_required_appearance_filter_is_refused(creds):
    cert, key = creds
    data = _authored(
        seed_value={"appearance_filter": "Ours", "required": ["appearance_filter"]}
    )

    with pytest.raises(PdfSecurityException, match="appearance"):
        sign_field(data, "Signature1", cert, key)


# ---------------------------------------------------------------------------
# /MDP, the one entry with no flag of its own
# ---------------------------------------------------------------------------


def test_mdp_binds_without_being_listed_as_required(creds):
    cert, key = creds
    data = _authored(seed_value={"mdp": {"p": 2}})

    with pytest.raises(PdfSecurityException, match="/MDP"):
        sign_field(data, "Signature1", cert, key)  # not certifying at all


def test_mdp_is_satisfied_by_the_matching_certification(creds):
    cert, key = creds
    data = _authored(seed_value={"mdp": {"p": 2}})
    signed = sign_field(data, "Signature1", cert, key, certify_permissions=2)
    assert SimplePdf.from_bytes(signed).signatures[0].docmdp_level == 2


def test_mdp_rejects_the_wrong_certification_level(creds):
    cert, key = creds
    data = _authored(seed_value={"mdp": {"p": 2}})
    with pytest.raises(PdfSecurityException, match="/MDP"):
        sign_field(data, "Signature1", cert, key, certify_permissions=3)


def test_mdp_zero_forbids_certifying(creds):
    cert, key = creds
    data = _authored(seed_value={"mdp": {"p": 0}})

    assert sign_field(data, "Signature1", cert, key)
    with pytest.raises(PdfSecurityException, match="forbids"):
        sign_field(data, "Signature1", cert, key, certify_permissions=1)


# ---------------------------------------------------------------------------
# Authoring the seed value
# ---------------------------------------------------------------------------


def test_the_new_seed_entries_are_written():
    data = _authored(
        seed_value={
            "v": 2,
            "legal_attestation": ["Nothing was altered"],
            "add_rev_info": False,
            "lock_document": "auto",
            "appearance_filter": "Ours",
            "mdp": {"p": 1},
            "timestamp": {"url": "http://tsa.example/", "required": True},
        }
    )
    for marker in (
        b"/V 2",
        b"/LegalAttestation",
        b"/AddRevInfo false",
        b"/LockDocument /auto",
        b"/AppearanceFilter",
        b"/MDP",
        b"/TimeStamp",
        b"/URL",
    ):
        assert marker in data, marker


@pytest.mark.parametrize(
    "spec",
    [
        {"v": "two"},
        {"lock_document": "maybe"},
        {"mdp": {"p": 4}},
        {"mdp": {}},
        {"timestamp": {}},
        {"timestamp": {"nope": 1}},
        {"legal_attestation": "not a sequence"},
    ],
)
def test_bad_seed_entries_are_rejected(spec):
    document = Document()
    page = document.pages.add()
    with pytest.raises((PdfValidationException, TypeError)):
        document.form.add_signature_field("S", page, (0, 0, 10, 10), seed_value=spec)


def test_every_required_name_has_a_flag_bit():
    document = Document()
    page = document.pages.add()
    for name, value in (
        ("v", 1),
        ("legal_attestation", ["x"]),
        ("add_rev_info", True),
        ("lock_document", "true"),
        ("appearance_filter", "Ours"),
    ):
        document.form.add_signature_field(
            f"S{name}", page, (0, 0, 10, 10),
            seed_value={name: value, "required": [name]},
        )


# ---------------------------------------------------------------------------
# One signing path: whole-document signing goes through sign_field
# ---------------------------------------------------------------------------


def _structured_document() -> bytes:
    document = Document()
    page = document.pages.add()
    page.add_text("Body text", 60, 700, font_size=14)
    document.outlines.add(OutlineItem("Chapter one", 0))
    document.form.add_text_field("nickname", 0, (50, 50, 200, 70), value="kept")
    output = BytesIO()
    document.save(output)
    return output.getvalue()


def _sign_whole(pdf_bytes: bytes, cert, key, **details) -> bytes:
    document = Document(BytesIO(pdf_bytes))
    engine = document._engine_pdf
    engine.signing_creds = (cert, key)
    engine.signature = {"Name": "Signature1", **details}
    output = BytesIO()
    document.save(output)
    return output.getvalue()


def test_whole_document_signing_keeps_the_documents_other_form_fields(creds):
    # The legacy path rebuilt the file from the in-memory model and lost this.
    cert, key = creds
    signed = _sign_whole(_structured_document(), cert, key)

    assert b"nickname" in signed
    assert b"/Outlines" in signed
    assert b"Body text" in signed


def test_whole_document_signing_produces_a_valid_signature(creds):
    cert, key = creds
    signed = _sign_whole(_structured_document(), cert, key, Reason="Approved")

    signature = SimplePdf.from_bytes(signed).signatures[0]
    assert signature.name == "Signature1"
    assert signature.reason == "Approved"
    assert signature.validate(_options(cert)).status is ValidationStatus.VALID


def test_a_signature_dictionary_is_typed_sig(creds):
    # ISO 32000-1 table 252: /Type shall be /Sig. The old path wrote
    # /Type /Signature, which is not a PDF object type at all.
    cert, key = creds
    signed = _sign_whole(_structured_document(), cert, key)
    assert b"/Type /Sig" in signed
    assert b"/Type /Signature" not in signed


def test_whole_document_signing_fills_a_field_the_caller_authored(creds):
    # A caller who authored a field with a seed value gets *that* field signed,
    # not a second one appended beside it -- so the seed value still binds.
    cert, key = creds
    document = Document()
    page = document.pages.add()
    document.form.add_signature_field(
        "Signature1",
        page,
        (60, 600, 260, 680),
        seed_value={"digest_method": ["SHA512"], "required": ["digest_method"]},
    )
    buffer = BytesIO()
    document.save(buffer)

    signed = _sign_whole(buffer.getvalue(), cert, key)

    assert _digest_algorithm(signed) == "sha512"
    # ...and there is still only one field, not a second one beside it.
    assert _acroform_field_count(signed) == 1


def test_whole_document_signing_refuses_an_already_signed_field(creds):
    cert, key = creds
    once = _sign_whole(_structured_document(), cert, key)
    with pytest.raises(PdfSecurityException, match="already signed"):
        _sign_whole(once, cert, key)


def test_whole_document_signing_refuses_a_name_taken_by_another_field(creds):
    cert, key = creds
    document = Document()
    page = document.pages.add()
    document.form.add_text_field("Signature1", page, (10, 10, 100, 40))
    buffer = BytesIO()
    document.save(buffer)

    with pytest.raises(PdfValidationException, match="not a signature field"):
        _sign_whole(buffer.getvalue(), cert, key)


def test_a_document_with_no_pages_cannot_be_signed(creds):
    cert, key = creds
    engine = SimplePdf()
    engine.signing_creds = (cert, key)
    with pytest.raises(PdfValidationException, match="no pages"):
        engine.to_bytes()


def test_signing_details_reach_the_signature_dictionary(creds):
    cert, key = creds
    signed = _sign_whole(
        _structured_document(),
        cert,
        key,
        Reason="Approved",
        Location="Prague",
        ContactInfo="signer@example.com",
    )
    signature = SimplePdf.from_bytes(signed).signatures[0]
    assert signature.reason == "Approved"
    assert signature.location == "Prague"
    assert signature.contact_info == "signer@example.com"


def test_the_signing_credentials_survive_the_save(creds):
    # They are cleared while the unsigned revision is written; a document that
    # lost them could not be saved twice.
    cert, key = creds
    document = Document(BytesIO(_structured_document()))
    engine = document._engine_pdf
    engine.signing_creds = (cert, key)
    engine.signature = {"Name": "Signature1"}
    first = BytesIO()
    document.save(first)
    assert engine.signing_creds == (cert, key)


# ---------------------------------------------------------------------------
# The byte range covers the /Contents delimiters
# ---------------------------------------------------------------------------


def _acroform_field_count(pdf_bytes: bytes) -> int:
    from aspose_pdf.engine.pdf_parser_cos import PdfCosParser

    doc = PdfCosParser(pdf_bytes).parse()

    def resolve(value):
        return doc.get_object(value) if hasattr(value, "object_number") else value

    root = resolve(doc.trailer.mapping[PdfName("Root")])
    acroform = resolve(root.mapping[PdfName("AcroForm")])
    return len(resolve(acroform.mapping[PdfName("Fields")]).items)


def _byte_range(signed: bytes) -> tuple[int, int, int, int]:
    import re

    match = re.search(rb"/ByteRange \[(\d+) (\d+) (\d+) (\d+)\]", signed)
    assert match is not None
    return tuple(int(value) for value in match.groups())  # type: ignore[return-value]


def test_the_byte_range_gap_is_exactly_the_contents_string(creds):
    """A validator matches the gap against ``/Contents`` to prove full coverage.

    Excluding only the hex digits leaves the ``<`` and ``>`` signed, which
    verifies fine but leaves a validator unable to say the signature covers the
    document -- pyHanko reports the coverage as indeterminate rather than
    "entire file".
    """
    cert, key = creds
    signed = sign_field(_authored(), "Signature1", cert, key)

    start, length1, start2, length2 = _byte_range(signed)
    assert start == 0
    assert signed[length1 : length1 + 1] == b"<"
    assert signed[start2 - 1 : start2] == b">"
    assert start2 + length2 == len(signed)
    # ...and the gap is the whole hex string plus its two delimiters.
    contents = signed[length1 + 1 : start2 - 1]
    assert start2 - length1 == len(contents) + 2


def test_the_whole_document_path_uses_the_same_gap(creds):
    cert, key = creds
    signed = _sign_whole(_structured_document(), cert, key)
    _, length1, start2, length2 = _byte_range(signed)
    assert signed[length1 : length1 + 1] == b"<"
    assert signed[start2 - 1 : start2] == b">"
    assert start2 + length2 == len(signed)


def test_the_signature_still_verifies_over_the_wider_gap(creds):
    cert, key = creds
    signed = sign_field(_authored(), "Signature1", cert, key)
    signature = SimplePdf.from_bytes(signed).signatures[0]
    assert signature.validate(_options(cert)).status is ValidationStatus.VALID


def test_tampering_inside_the_signed_range_is_still_caught(creds):
    cert, key = creds
    signed = bytearray(sign_field(_authored(), "Signature1", cert, key))
    marker = signed.index(b"/Type /Sig")
    signed[marker + 1] = ord("t")  # corrupt a byte the signature covers
    signature = SimplePdf.from_bytes(bytes(signed)).signatures[0]
    assert signature.validate(_options(cert)).status is not ValidationStatus.VALID


def test_the_encrypted_writer_keeps_the_same_convention(creds):
    # An encrypted document that is also signed stays on the legacy writer;
    # its byte range has to mean the same thing.
    cert, key = creds
    engine = SimplePdf()
    engine.pages = [(0, 0, 200, 200)]
    engine.page_contents = [b"BT /F1 12 Tf 20 100 Td (hi) Tj ET"]
    engine.encrypt("", "owner")
    engine.signing_creds = (cert, key)
    signed = engine.to_bytes()

    _, length1, start2, length2 = _byte_range(signed)
    assert signed[length1 : length1 + 1] == b"<"
    assert signed[start2 - 1 : start2] == b">"
    assert start2 + length2 == len(signed)
    assert b"/Type /Sig" in signed

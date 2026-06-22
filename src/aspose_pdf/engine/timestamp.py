"""RFC 3161 timestamp handling.

Covers three things:

* **Verification** of a timestamp token (a CMS ``SignedData`` whose encapsulated
  content is a ``TSTInfo``): the TSA's signature, and that the token's message
  imprint binds to the value being timestamped.
* **Creation** of a token with a local TSA — used for offline tests and for
  embedding a timestamp when signing without a network TSA.
* **Opt-in online** requests to a real TSA over HTTP (RFC 3161 ``TimeStampReq`` /
  ``TimeStampResp``).  The HTTP helper is isolated for monkeypatching in tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from asn1crypto import cms as asn1_cms
from asn1crypto import tsp
from asn1crypto import x509 as asn1_x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

from aspose_pdf.engine import cms as cms_mod

# id-ct-TSTInfo
TST_INFO_OID = "1.2.840.113549.1.9.16.1.4"
# An arbitrary TSA policy OID used by the bundled local TSA.
_LOCAL_TSA_POLICY = "1.3.6.1.4.1.59999.1.1"


@dataclass
class TimestampInfo:
    """Result of verifying an RFC 3161 timestamp token."""

    verified: bool
    gen_time: Optional[datetime] = None
    tsa: Optional[str] = None
    imprint_ok: bool = False
    signature_ok: bool = False
    reason: str = ""


def _tst_info_and_der(token_der: bytes):
    content_info = asn1_cms.ContentInfo.load(token_der)
    signed_data = content_info["content"]
    encap = signed_data["encap_content_info"]
    if encap["content_type"].native != "tst_info":
        raise ValueError("timestamp token does not encapsulate a TSTInfo")
    tst_info = encap["content"].parsed
    return tst_info, tst_info.dump()


def verify_timestamp_token(token_der: bytes, signed_value: bytes) -> TimestampInfo:
    """Verify an RFC 3161 token and that it timestamps *signed_value*.

    *signed_value* is the data the timestamp protects — for a PDF signature
    timestamp it is the signer's signature bytes.
    """
    try:
        tst_info, tst_der = _tst_info_and_der(token_der)
    except (ValueError, TypeError, KeyError) as exc:
        return TimestampInfo(False, reason=f"malformed timestamp token: {exc}")

    gen_time = None
    try:
        gen_time = tst_info["gen_time"].native
    except (ValueError, TypeError, KeyError):
        pass

    # Verify the TSA signature over the TSTInfo content.
    try:
        info = cms_mod.parse_signed_data(token_der)
    except (ValueError, TypeError, KeyError) as exc:
        return TimestampInfo(False, gen_time, reason=f"unparsable TSA CMS: {exc}")
    signer = cms_mod.verify_signer(info, tst_der)
    tsa_name = None
    if info.signer_cert is not None:
        tsa_name = info.signer_cert.subject.rfc4514_string()

    # Verify the message imprint binds to the value being timestamped.
    imprint_ok = False
    try:
        imprint = tst_info["message_imprint"]
        algo = imprint["hash_algorithm"]["algorithm"].native
        digest = hashes.Hash(cms_mod.hash_from_name(algo))
        digest.update(signed_value)
        imprint_ok = imprint["hashed_message"].native == digest.finalize()
    except Exception as exc:  # noqa: BLE001 - report any imprint failure
        return TimestampInfo(
            False, gen_time, tsa_name, False, signer.signature_ok,
            reason=f"imprint check failed: {exc}",
        )

    verified = signer.signature_ok and imprint_ok
    reason = ""
    if not signer.signature_ok:
        reason = signer.reason or "TSA signature invalid"
    elif not imprint_ok:
        reason = "timestamp imprint does not match the signature"
    return TimestampInfo(
        verified, gen_time, tsa_name, imprint_ok, signer.signature_ok, reason
    )


# ---------------------------------------------------------------------------
# Creation (local TSA)
# ---------------------------------------------------------------------------
def make_timestamp_token(
    message_imprint: bytes,
    hash_algo: str,
    tsa_cert,
    tsa_key,
    *,
    gen_time: Optional[datetime] = None,
    serial: int = 1,
) -> bytes:
    """Build an RFC 3161 timestamp token signed by a local TSA.

    *message_imprint* is the digest (using *hash_algo*) of the value to be
    timestamped.  Returns the DER of the token (a CMS ``ContentInfo``).
    """
    if gen_time is None:
        gen_time = datetime.now(timezone.utc)

    tst_info = tsp.TSTInfo(
        {
            "version": "v1",
            "policy": _LOCAL_TSA_POLICY,
            "message_imprint": {
                "hash_algorithm": {"algorithm": hash_algo},
                "hashed_message": message_imprint,
            },
            "serial_number": serial,
            "gen_time": gen_time,
        }
    )
    tst_der = tst_info.dump()

    digest = hashes.Hash(cms_mod.hash_from_name(hash_algo))
    digest.update(tst_der)
    content_digest = digest.finalize()

    signed_attrs = asn1_cms.CMSAttributes(
        [
            asn1_cms.CMSAttribute(
                {"type": "content_type", "values": ["tst_info"]}
            ),
            asn1_cms.CMSAttribute(
                {"type": "message_digest", "values": [content_digest]}
            ),
        ]
    )
    to_sign = b"\x31" + signed_attrs.dump(force=True)[1:]
    signature = tsa_key.sign(to_sign, padding.PKCS1v15(), _crypto_hash(hash_algo))

    asn1_cert = asn1_x509.Certificate.load(_cert_der(tsa_cert))
    signer_info = asn1_cms.SignerInfo(
        {
            "version": "v1",
            "sid": {
                "issuer_and_serial_number": {
                    "issuer": asn1_cert.issuer,
                    "serial_number": tsa_cert.serial_number,
                }
            },
            "digest_algorithm": {"algorithm": hash_algo},
            "signed_attrs": signed_attrs,
            "signature_algorithm": {"algorithm": "rsassa_pkcs1v15"},
            "signature": signature,
        }
    )
    signed_data = asn1_cms.SignedData(
        {
            "version": "v3",
            "digest_algorithms": [{"algorithm": hash_algo}],
            "encap_content_info": {
                "content_type": "tst_info",
                "content": tst_info,
            },
            "certificates": [asn1_cert],
            "signer_infos": [signer_info],
        }
    )
    return asn1_cms.ContentInfo(
        {"content_type": "signed_data", "content": signed_data}
    ).dump()


def _crypto_hash(name: str):
    return cms_mod.hash_from_name(name)


def _cert_der(cert) -> bytes:
    from cryptography.hazmat.primitives import serialization

    return cert.public_bytes(serialization.Encoding.DER)


# ---------------------------------------------------------------------------
# Online (opt-in) TSA request
# ---------------------------------------------------------------------------
def _http_post(url: str, data: bytes, content_type: str, timeout: float) -> bytes:
    import urllib.request

    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": content_type}
    )
    with urllib.request.urlopen(request, timeout=timeout) as resp:  # noqa: S310
        return resp.read()


def request_timestamp(
    message_imprint: bytes, hash_algo: str, tsa_url: str, *, timeout: float = 10.0
) -> bytes:
    """Request a timestamp token from a network TSA (RFC 3161 over HTTP)."""
    request = tsp.TimeStampReq(
        {
            "version": "v1",
            "message_imprint": {
                "hash_algorithm": {"algorithm": hash_algo},
                "hashed_message": message_imprint,
            },
            "cert_req": True,
        }
    )
    body = _http_post(
        tsa_url, request.dump(), "application/timestamp-query", timeout
    )
    response = tsp.TimeStampResp.load(body)
    token = response["time_stamp_token"]
    return token.dump()

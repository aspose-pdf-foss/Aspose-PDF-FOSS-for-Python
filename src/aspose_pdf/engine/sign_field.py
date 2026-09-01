"""Sign an existing (authored) AcroForm signature field in place.

The document-wide signing path in :mod:`aspose_pdf.engine.simple_pdf` builds a
fresh file and synthesises its own single ``/Sig`` field, so it cannot fill a
field a caller already authored with ``Form.add_signature_field`` — and it
would discard the surrounding COS structure on the way. This module signs the
*existing* field instead, as an **incremental update**: the original bytes are
emitted verbatim and only the signature value, the field, and the AcroForm are
re-emitted in an appended revision. Any signature already in the document
therefore stays valid, and the authored field keeps its widget, appearance,
seed value, and lock dictionary.

The layout follows ISO 32000-1 §12.8: ``/ByteRange`` spans the file either side
of the ``/Contents`` placeholder, which is then patched with the detached
PKCS#7 (or CAdES, for PAdES) blob.
"""

from __future__ import annotations

from typing import Any

from aspose_pdf.engine.cos import (
    PdfArray,
    PdfDictionary,
    PdfIndirectReference,
    PdfName,
    PdfNumber,
    PdfString,
)
from aspose_pdf.engine.incremental_update import IncrementalUpdate
from aspose_pdf.engine.pdf_parser_cos import PdfCosParser
from aspose_pdf.engine.pdf_writer_cos import PdfCosWriter
from aspose_pdf.engine.signing import SigningUtils
from aspose_pdf.exceptions import PdfSecurityException, PdfValidationException
from aspose_pdf.load_limits import PdfLoadLimits, _coerce_limits, _LoadBudget

__all__ = ["sign_field"]

# Hex capacity reserved for the CMS blob. A bare signature needs ~3 KiB; an
# embedded chain and/or an RFC 3161 timestamp make it substantially larger.
_CONTENTS_HEX = 16384
_CONTENTS_HEX_LARGE = 65536

# /ByteRange holds four 10-digit numbers separated by single spaces.
_BYTE_RANGE_WIDTH = 43


def _resolve(doc: Any, obj: Any) -> Any:
    if isinstance(obj, PdfIndirectReference):
        return doc.get_object(obj)
    return obj


def _text(obj: Any) -> str | None:
    """Decode a COS text string, or return ``None`` for anything else."""
    if not isinstance(obj, PdfString):
        return None
    from aspose_pdf.engine.simple_pdf import decode_pdf_text_string

    return decode_pdf_text_string(obj)


def _pdf_text_string(value: str) -> PdfString:
    """Encode *value* as PDFDocEncoded latin-1, or UTF-16BE with a BOM."""
    try:
        return PdfString(value.encode("latin-1"))
    except UnicodeEncodeError:
        return PdfString(b"\xfe\xff" + value.encode("utf-16-be"))


def _find_field(
    doc: Any, budget: _LoadBudget, name: str
) -> tuple[PdfIndirectReference, PdfDictionary]:
    """Locate the terminal field whose fully qualified name is *name*.

    Returns its indirect reference and dictionary. The field must be an
    indirect object: signing re-emits it in the appended revision, which is
    only addressable for an object with its own number.
    """
    if not isinstance(name, str) or not name or "\x00" in name:
        raise PdfValidationException("Field name must be a non-empty text string")

    root = _resolve(doc, doc.trailer.mapping.get(PdfName("Root")))
    if not isinstance(root, PdfDictionary):
        raise PdfValidationException("Document has no catalog")
    acro = _resolve(doc, root.mapping.get(PdfName("AcroForm")))
    if not isinstance(acro, PdfDictionary):
        raise PdfValidationException("Document has no AcroForm")
    fields = _resolve(doc, acro.mapping.get(PdfName("Fields")))
    if not isinstance(fields, PdfArray):
        raise PdfValidationException("AcroForm has no /Fields array")

    target = name.split(".")
    # (field_ref_or_dict, remaining name components, depth)
    stack: list[tuple[Any, list[str], int]] = [
        (ref, target, 1) for ref in reversed(fields.items)
    ]
    seen: set[int] = set()
    while stack:
        ref, remaining, depth = stack.pop()
        budget.check(depth, "max_nesting_depth", "AcroForm field depth")
        field = _resolve(doc, ref)
        if not isinstance(field, PdfDictionary) or not remaining:
            continue
        marker = id(field)
        if marker in seen:
            continue
        seen.add(marker)

        partial = _text(_resolve(doc, field.mapping.get(PdfName("T"))))
        if partial is None:
            # An unnamed node is transparent: its kids keep the same path.
            rest = remaining
        elif partial == remaining[0]:
            rest = remaining[1:]
        else:
            continue

        if not rest:
            if not isinstance(ref, PdfIndirectReference):
                raise PdfValidationException(
                    f"Signature field '{name}' is a direct object; only an "
                    "indirect field can be signed"
                )
            return ref, field

        kids = _resolve(doc, field.mapping.get(PdfName("Kids")))
        if isinstance(kids, PdfArray):
            budget.check(
                len(kids.items), "max_container_items", "AcroForm field kids"
            )
            for kid in reversed(kids.items):
                stack.append((kid, rest, depth + 1))

    raise PdfValidationException(f"Signature field '{name}' not found")


#: ``/Ff`` bit for each ``/SV`` entry (ISO 32000-1 table 234, bit *positions*).
_SEED_FLAGS = {
    "Filter": 1 << 0,
    "SubFilter": 1 << 1,
    "V": 1 << 2,
    "Reasons": 1 << 3,
    "LegalAttestation": 1 << 4,
    "AddRevInfo": 1 << 5,
    "DigestMethod": 1 << 6,
    "LockDocument": 1 << 7,
    "AppearanceFilter": 1 << 8,
}

#: ``/SV /DigestMethod`` names this signer can actually produce. SHA-1 and
#: RIPEMD160 are in the specification's list and are deliberately absent: a
#: field demanding one of them gets a refusal, not a weak signature.
_DIGEST_BY_NAME = {
    "SHA256": "sha256",
    "SHA384": "sha384",
    "SHA512": "sha512",
}

#: The highest ``/SV /V`` this signer understands. Version 1 is the PDF 1.5 set
#: of seed value entries, 2 adds the PDF 1.7 ones; every entry of both is
#: either honoured or refused below.
_SEED_VERSION = 2


class _SeedValue:
    """What a field's ``/SV`` dictionary decided for this signature."""

    __slots__ = ("digest_algorithm", "lock_document", "timestamp_url")

    def __init__(
        self,
        digest_algorithm: str | None = None,
        timestamp_url: str | None = None,
        lock_document: bool = False,
    ) -> None:
        self.digest_algorithm = digest_algorithm
        self.timestamp_url = timestamp_url
        self.lock_document = lock_document


def _sv_names(doc: Any, value: Any) -> list[str]:
    array = _resolve(doc, value)
    if not isinstance(array, PdfArray):
        return []
    return [
        item.name.lstrip("/")
        for item in (_resolve(doc, i) for i in array.items)
        if isinstance(item, PdfName)
    ]


def _check_seed_value(
    doc: Any,
    field: PdfDictionary,
    *,
    sub_filter: str,
    reason: str | None,
    certify_permissions: int | None,
    has_timestamp: bool,
) -> _SeedValue:
    """Enforce the field's ``/SV`` dictionary and report what it chose.

    A seed value entry is advisory unless its bit in ``/Ff`` is set, in which
    case a signer that cannot honour it **must not sign** (ISO 32000-1
    §12.7.4.5, table 234). Some entries can be honoured -- a digest to use, a
    timestamp authority to call -- and those are returned rather than merely
    checked; the rest either match or raise.

    ``/MDP`` carries no ``/Ff`` bit at all, so it is always binding.
    """
    decision = _SeedValue()
    sv = _resolve(doc, field.mapping.get(PdfName("SV")))
    if not isinstance(sv, PdfDictionary):
        return decision
    flags_obj = _resolve(doc, sv.mapping.get(PdfName("Ff")))
    flags = int(flags_obj.value) if isinstance(flags_obj, PdfNumber) else 0

    def required(entry: str) -> bool:
        return bool(flags & _SEED_FLAGS[entry])

    version = _resolve(doc, sv.mapping.get(PdfName("V")))
    if required("V") and isinstance(version, PdfNumber):
        if float(version.value) > _SEED_VERSION:
            raise PdfSecurityException(
                f"Seed value requires signature handler version {version.value}; "
                f"this signer implements {_SEED_VERSION}"
            )

    handler = _resolve(doc, sv.mapping.get(PdfName("Filter")))
    if required("Filter") and isinstance(handler, PdfName):
        wanted = handler.name.lstrip("/")
        if wanted != "Adobe.PPKLite":
            raise PdfSecurityException(
                f"Seed value requires the /{wanted} signature handler; "
                "this signer is /Adobe.PPKLite"
            )

    names = _sv_names(doc, sv.mapping.get(PdfName("SubFilter")))
    if required("SubFilter") and names and sub_filter not in names:
        raise PdfSecurityException(
            f"Seed value requires /SubFilter in {names}; got /{sub_filter}"
        )

    digests = _sv_names(doc, sv.mapping.get(PdfName("DigestMethod")))
    if digests:
        usable = [_DIGEST_BY_NAME[name] for name in digests if name in _DIGEST_BY_NAME]
        if usable:
            decision.digest_algorithm = usable[0]
        elif required("DigestMethod"):
            raise PdfSecurityException(
                f"Seed value requires /DigestMethod in {digests}; this signer "
                f"produces {sorted(_DIGEST_BY_NAME)}"
            )

    reasons = _resolve(doc, sv.mapping.get(PdfName("Reasons")))
    if required("Reasons") and isinstance(reasons, PdfArray):
        allowed_reasons = [
            text
            for text in (_text(_resolve(doc, i)) for i in reasons.items)
            if text is not None
        ]
        if reason not in allowed_reasons:
            raise PdfSecurityException(
                f"Seed value requires /Reason in {allowed_reasons}; got {reason!r}"
            )

    if required("LegalAttestation"):
        raise PdfSecurityException(
            "Seed value requires a legal attestation, which this signer cannot "
            "produce; sign the field with a tool that can, or clear the "
            "requirement from /SV"
        )

    add_rev_info = _resolve(doc, sv.mapping.get(PdfName("AddRevInfo")))
    if required("AddRevInfo") and getattr(add_rev_info, "value", False) is True:
        raise PdfSecurityException(
            "Seed value requires revocation information inside the signature, "
            "which this signer does not embed; build a /DSS with enable_ltv "
            "after signing, or clear the requirement from /SV"
        )

    if required("AppearanceFilter"):
        raise PdfSecurityException(
            "Seed value requires a named appearance, which this signer does not "
            "build; author the field's appearance first, or clear the "
            "requirement from /SV"
        )

    _check_seed_mdp(doc, sv, certify_permissions)
    decision.lock_document = _check_seed_lock(doc, sv, required("LockDocument"))
    decision.timestamp_url = _check_seed_timestamp(doc, sv, has_timestamp)
    return decision


def _check_seed_mdp(doc: Any, sv: PdfDictionary, certify_permissions: int | None) -> None:
    """``/SV /MDP /P`` fixes whether this signature certifies, and at what level.

    There is no ``/Ff`` bit for ``/MDP``, so unlike its neighbours it is not
    "advisory unless flagged" -- it always binds.
    """
    mdp = _resolve(doc, sv.mapping.get(PdfName("MDP")))
    if not isinstance(mdp, PdfDictionary):
        return
    p = _resolve(doc, mdp.mapping.get(PdfName("P")))
    if not isinstance(p, PdfNumber):
        return
    wanted = int(p.value)
    if wanted == 0:
        if certify_permissions is not None:
            raise PdfSecurityException(
                "Seed value /MDP /P 0 forbids a certifying signature"
            )
        return
    if certify_permissions != wanted:
        raise PdfSecurityException(
            f"Seed value /MDP requires a certifying signature with /P {wanted}; "
            f"got {certify_permissions!r}"
        )


def _check_seed_lock(doc: Any, sv: PdfDictionary, required: bool) -> bool:
    """``/SV /LockDocument``: ``/true``, ``/false`` or ``/auto`` (PDF 2.0)."""
    value = _resolve(doc, sv.mapping.get(PdfName("LockDocument")))
    if not isinstance(value, PdfName):
        return False
    setting = value.name.lstrip("/")
    if setting == "true":
        return True
    if setting in ("false", "auto"):
        return False
    if required:
        raise PdfSecurityException(
            f"Seed value /LockDocument is /{setting}, which is not one of "
            "/true, /false or /auto"
        )
    return False


def _check_seed_timestamp(doc: Any, sv: PdfDictionary, has_timestamp: bool) -> str | None:
    """``/SV /TimeStamp``: a ``/URL`` to use, and a ``/Ff`` of 1 making it binding."""
    entry = _resolve(doc, sv.mapping.get(PdfName("TimeStamp")))
    if not isinstance(entry, PdfDictionary):
        return None
    flags = _resolve(doc, entry.mapping.get(PdfName("Ff")))
    binding = isinstance(flags, PdfNumber) and int(flags.value) & 1
    url = _text(_resolve(doc, entry.mapping.get(PdfName("URL"))))
    if binding and not has_timestamp and not url:
        raise PdfSecurityException(
            "Seed value requires a timestamp but names no /URL, and none was "
            "supplied to the signer"
        )
    return url


def _signature_references(
    doc: Any,
    field: PdfDictionary,
    field_ref: PdfIndirectReference,
    certify_permissions: int | None,
) -> PdfArray | None:
    """Build the ``/Reference`` array for DocMDP certification and ``/Lock``."""
    refs: list[PdfDictionary] = []
    if certify_permissions in (1, 2, 3):
        refs.append(
            PdfDictionary(
                {
                    PdfName("Type"): PdfName("SigRef"),
                    PdfName("TransformMethod"): PdfName("DocMDP"),
                    PdfName("TransformParams"): PdfDictionary(
                        {
                            PdfName("Type"): PdfName("TransformParams"),
                            PdfName("P"): PdfNumber(certify_permissions),
                            PdfName("V"): PdfName("1.2"),
                        }
                    ),
                    PdfName("DigestMethod"): PdfName("SHA256"),
                }
            )
        )

    # A /Lock on the field says which fields this signature freezes; on signing
    # it becomes a FieldMDP transform so a verifier can enforce it.
    lock = _resolve(doc, field.mapping.get(PdfName("Lock")))
    if isinstance(lock, PdfDictionary):
        params: dict[PdfName, Any] = {
            PdfName("Type"): PdfName("TransformParams"),
            PdfName("V"): PdfName("1.2"),
        }
        action = _resolve(doc, lock.mapping.get(PdfName("Action")))
        params[PdfName("Action")] = (
            action if isinstance(action, PdfName) else PdfName("All")
        )
        fields = _resolve(doc, lock.mapping.get(PdfName("Fields")))
        if isinstance(fields, PdfArray):
            params[PdfName("Fields")] = fields
        refs.append(
            PdfDictionary(
                {
                    PdfName("Type"): PdfName("SigRef"),
                    PdfName("TransformMethod"): PdfName("FieldMDP"),
                    PdfName("TransformParams"): PdfDictionary(params),
                    PdfName("Data"): field_ref,
                    PdfName("DigestMethod"): PdfName("SHA256"),
                }
            )
        )
    return PdfArray(list(refs)) if refs else None


def _acroform_objects(
    doc: Any, writer: PdfCosWriter, sig_ref: PdfIndirectReference,
    certify_permissions: int | None,
) -> list[tuple[int, bytes]]:
    """Re-emit AcroForm (``/SigFlags``) and, when certifying, ``/Perms``."""
    root_ref = doc.trailer.mapping.get(PdfName("Root"))
    catalog = _resolve(doc, root_ref)
    updates: list[tuple[int, bytes]] = []

    acro_value = catalog.mapping.get(PdfName("AcroForm"))
    acro = _resolve(doc, acro_value)
    existing = _resolve(doc, acro.mapping.get(PdfName("SigFlags")))
    flags = int(existing.value) if isinstance(existing, PdfNumber) else 0
    # SignaturesExist | AppendOnly: the file must now only grow.
    acro.mapping[PdfName("SigFlags")] = PdfNumber(flags | 3)

    catalog_dirty = not isinstance(acro_value, PdfIndirectReference)
    if certify_permissions in (1, 2, 3):
        catalog.mapping[PdfName("Perms")] = PdfDictionary(
            {PdfName("DocMDP"): sig_ref}
        )
        catalog_dirty = True

    if isinstance(acro_value, PdfIndirectReference):
        num = acro_value.object_number
        body = writer.serialize_object(acro)
        updates.append((num, f"{num} 0 obj\n{body}\nendobj\n".encode("latin-1")))
    if catalog_dirty:
        num = root_ref.object_number
        body = writer.serialize_object(catalog)
        updates.append((num, f"{num} 0 obj\n{body}\nendobj\n".encode("latin-1")))
    return updates


def sign_field(
    pdf_bytes: bytes,
    field_name: str,
    cert: Any,
    key: Any,
    *,
    pades: bool = False,
    reason: str | None = None,
    location: str | None = None,
    contact: str | None = None,
    signer_name: str | None = None,
    extra_certs: Any = None,
    tsa: tuple | None = None,
    timestamp_url: str | None = None,
    timestamp_timeout: float = 10.0,
    certify_permissions: int | None = None,
    limits: PdfLoadLimits | None = None,
) -> bytes:
    """Sign the existing signature field *field_name* and return the new bytes.

    *pdf_bytes* is returned verbatim with one appended revision holding the
    signature, so signatures already present stay valid. The field must be an
    unsigned ``/FT /Sig`` field (as authored by ``Form.add_signature_field``);
    a field that already carries a ``/V`` is rejected rather than overwritten.

    Set *pades* for a CAdES-BES signature (``ETSI.CAdES.detached``, PAdES-B),
    which a *tsa* or *timestamp_url* upgrades to PAdES-T. *certify_permissions*
    (1/2/3) makes this a DocMDP certifying signature.

    The field's ``/SV`` seed value is enforced where its ``/Ff`` marks an entry
    required: an entry this signer cannot honour makes it refuse rather than
    sign around the constraint. Some entries are *followed* rather than merely
    checked -- ``/DigestMethod`` chooses the digest, ``/TimeStamp /URL`` names
    the authority to call when the caller supplied none, and ``/LockDocument``
    turns the signature into a certifying one. ``/MDP`` binds regardless of
    ``/Ff``, which is the one entry with no flag of its own. A ``/Lock`` on the
    field (as opposed to ``/SV``) is carried into the signature as a FieldMDP
    transform.
    """
    if certify_permissions is not None and certify_permissions not in (1, 2, 3):
        raise PdfValidationException(
            "certify_permissions must be 1, 2, or 3 (DocMDP /P)"
        )

    budget = _LoadBudget(_coerce_limits(limits))
    budget.check_input(len(pdf_bytes))
    doc = PdfCosParser(pdf_bytes, budget=budget).parse()

    field_ref, field = _find_field(doc, budget, field_name)
    ft = _resolve(doc, field.mapping.get(PdfName("FT")))
    if ft != PdfName("Sig"):
        raise PdfValidationException(
            f"Field '{field_name}' is not a signature field (/FT /Sig)"
        )
    if field.mapping.get(PdfName("V")) is not None:
        raise PdfSecurityException(f"Field '{field_name}' is already signed")

    sub_filter = "ETSI.CAdES.detached" if pades else "adbe.pkcs7.detached"
    seed = _check_seed_value(
        doc,
        field,
        sub_filter=sub_filter,
        reason=reason,
        certify_permissions=certify_permissions,
        has_timestamp=bool(tsa or timestamp_url),
    )
    # A seed value may supply what the caller did not: the authority to stamp
    # with, the digest to use, and -- for /LockDocument -- a certification the
    # caller never asked for.
    if timestamp_url is None and tsa is None and seed.timestamp_url:
        timestamp_url = seed.timestamp_url
    if seed.lock_document and certify_permissions is None:
        certify_permissions = 1  # no changes permitted
    digest_algorithm = seed.digest_algorithm or "sha256"

    inc = IncrementalUpdate(pdf_bytes, budget=budget)
    sig_num = inc.get_next_object_number()
    sig_ref = PdfIndirectReference(sig_num, 0)

    contents_hex = (
        _CONTENTS_HEX_LARGE
        if (extra_certs or tsa or timestamp_url)
        else _CONTENTS_HEX
    )
    sig_map: dict[PdfName, Any] = {
        PdfName("Type"): PdfName("Sig"),
        PdfName("Filter"): PdfName("Adobe.PPKLite"),
        PdfName("SubFilter"): PdfName(sub_filter),
    }
    for entry, value in (
        ("Reason", reason),
        ("Location", location),
        ("ContactInfo", contact),
        ("Name", signer_name),
    ):
        if value is not None:
            sig_map[PdfName(entry)] = _pdf_text_string(value)
    references = _signature_references(doc, field, field_ref, certify_permissions)
    if references is not None:
        sig_map[PdfName("Reference")] = references

    writer = PdfCosWriter(doc)
    # /ByteRange and /Contents are appended as literal text so their byte
    # offsets survive serialization unchanged and can be patched in place.
    head = writer.serialize_object(PdfDictionary(sig_map))
    if not head.endswith(">>"):
        raise PdfValidationException("Unexpected signature dictionary encoding")
    sig_body = (
        head[:-2]
        + " /ByteRange [0000000000 0000000000 0000000000 0000000000]"
        + f" /Contents <{'0' * contents_hex}> >>"
    )
    inc.add_object(
        sig_num, f"{sig_num} 0 obj\n{sig_body}\nendobj\n".encode("latin-1")
    )

    field.mapping[PdfName("V")] = sig_ref
    field_num = field_ref.object_number
    field_body = writer.serialize_object(field)
    inc.add_object(
        field_num,
        f"{field_num} 0 obj\n{field_body}\nendobj\n".encode("latin-1"),
    )
    for num, body in _acroform_objects(doc, writer, sig_ref, certify_permissions):
        inc.add_object(num, body)

    combined = bytearray(pdf_bytes + inc.generate())

    # The placeholders just written are the last in the file.
    br_marker = combined.rfind(b"/ByteRange [")
    contents_marker = combined.rfind(b"/Contents <")
    if br_marker < 0 or contents_marker < 0:
        raise PdfValidationException("Signature placeholders were not emitted")
    arr_start = br_marker + len(b"/ByteRange [")
    contents_start = contents_marker + len(b"/Contents <")
    contents_end = combined.index(b">", contents_start)

    total = len(combined)
    # The excluded gap runs from the ``<`` through the ``>`` inclusive, not
    # just the hex digits between them. Leaving the delimiters inside the
    # signed ranges still yields a verifiable signature, but a validator cannot
    # then match the gap to the /Contents string, and reports the coverage as
    # indeterminate rather than "the whole file".
    gap_start = contents_start - 1  # the '<'
    gap_end = contents_end + 1  # one past the '>'
    byte_range = (0, gap_start, gap_end, total - gap_end)
    combined[arr_start : arr_start + _BYTE_RANGE_WIDTH] = (
        "{:010d} {:010d} {:010d} {:010d}".format(*byte_range).encode("latin-1")
    )

    signed_data = bytes(combined[0:gap_start] + combined[gap_end:])
    signer = SigningUtils.sign_data_cades if pades else SigningUtils.sign_data_pkcs7
    blob = signer(
        signed_data,
        cert,
        key,
        extra_certs=extra_certs,
        hash_algo=digest_algorithm,
        tsa=tsa,
        timestamp_url=timestamp_url,
        timestamp_timeout=timestamp_timeout,
    )
    blob_hex = blob.hex().encode("latin-1")
    if len(blob_hex) > contents_hex:
        raise PdfSecurityException(
            "CMS signature exceeds the reserved /Contents placeholder "
            f"({len(blob_hex)} > {contents_hex} hex chars)"
        )
    blob_hex += b"0" * (contents_hex - len(blob_hex))
    combined[contents_start:contents_end] = blob_hex
    return bytes(combined)

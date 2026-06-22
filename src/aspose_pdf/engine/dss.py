"""Document Security Store (``/DSS``) — PAdES long-term validation (LTV).

A DSS dictionary in the document catalog carries the certificates, CRLs and
OCSP responses needed to validate the document's signatures long after signing,
without any network access.  This module:

* **builds** a ``/DSS`` (optionally with a ``/VRI`` entry keyed to a specific
  signature) and appends it as an *incremental update* so existing signatures
  stay byte-for-byte intact (:func:`build_dss`);
* **harvests** the validation material from an existing ``/DSS`` so the
  validator can consult it (:func:`read_dss`);
* **collects** material out of a CMS blob to feed the builder
  (:func:`collect_validation_material`).

The on-wire structure follows ISO 32000-2 §12.8.4.3 / ETSI EN 319 142.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from aspose_pdf.engine.cos import (
    PdfArray,
    PdfDictionary,
    PdfIndirectReference,
    PdfName,
    PdfNumber,
    PdfStream,
)
from aspose_pdf.engine.incremental_update import IncrementalUpdate
from aspose_pdf.engine.pdf_parser_cos import PdfCosParser
from aspose_pdf.engine.pdf_writer_cos import PdfCosWriter

# Failures while reading a third-party DSS must degrade to "no material",
# never raise into a validation path that promises not to.
_READ_ERRORS = (ValueError, TypeError, KeyError, IndexError, AttributeError)


@dataclass
class DssMaterial:
    """Validation material destined for (or harvested from) a ``/DSS``.

    Each list holds DER-encoded blobs: X.509 certificates, ``CertificateList``
    CRLs and ``OCSPResponse`` responses respectively.
    """

    certs: List[bytes] = field(default_factory=list)
    crls: List[bytes] = field(default_factory=list)
    ocsps: List[bytes] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.certs or self.crls or self.ocsps)

    def merge(self, other: "DssMaterial") -> "DssMaterial":
        """Extend this material in place with *other* and return ``self``."""
        self.certs.extend(other.certs)
        self.crls.extend(other.crls)
        self.ocsps.extend(other.ocsps)
        return self

    def deduped(self) -> "DssMaterial":
        """Return a copy with order-preserving de-duplication of each list."""

        def uniq(items: Sequence[bytes]) -> List[bytes]:
            seen: set[bytes] = set()
            out: List[bytes] = []
            for item in items:
                if item and item not in seen:
                    seen.add(item)
                    out.append(item)
            return out

        return DssMaterial(uniq(self.certs), uniq(self.crls), uniq(self.ocsps))


def collect_validation_material(contents_der: bytes) -> DssMaterial:
    """Harvest certificates and revocation data embedded in a CMS signature."""
    from cryptography.hazmat.primitives import serialization

    from aspose_pdf.engine import cms

    try:
        info = cms.parse_signed_data(contents_der)
    except _READ_ERRORS:
        return DssMaterial()
    certs = [c.public_bytes(serialization.Encoding.DER) for c in info.certificates]
    return DssMaterial(
        certs=certs, crls=list(info.crls_der), ocsps=list(info.ocsps_der)
    ).deduped()


# ---------------------------------------------------------------------------
# Build (incremental update)
# ---------------------------------------------------------------------------
def _stream_object_bytes(obj_num: int, data: bytes) -> bytes:
    """Serialize *data* as an uncompressed indirect stream object."""
    header = f"{obj_num} 0 obj\n<< /Length {len(data)} >>\nstream\n".encode("latin-1")
    return header + data + b"\nendstream\nendobj\n"


def _refs_array(nums: Sequence[int]) -> str:
    return "[ " + " ".join(f"{n} 0 R" for n in nums) + " ]"


def build_dss(
    original_pdf: bytes,
    material: DssMaterial,
    *,
    vri_contents: Optional[bytes] = None,
) -> bytes:
    """Return *original_pdf* with a ``/DSS`` added via an incremental update.

    The original bytes are preserved verbatim, so any existing signature keeps
    covering exactly the same range and stays valid.

    Parameters
    ----------
    material:
        Certificates / CRLs / OCSP responses to embed.
    vri_contents:
        When given (the ``/Contents`` bytes of a signature), a ``/VRI`` entry is
        added keyed by the uppercase SHA-1 of those bytes, associating the
        material with that specific signature per ISO 32000-2.
    """
    material = material.deduped()
    if material.is_empty():
        return original_pdf

    inc = IncrementalUpdate(original_pdf)

    def add_stream(data: bytes) -> int:
        num = inc.get_next_object_number()
        inc.add_object(num, _stream_object_bytes(num, data))
        return num

    cert_nums = [add_stream(d) for d in material.certs]
    crl_nums = [add_stream(d) for d in material.crls]
    ocsp_nums = [add_stream(d) for d in material.ocsps]

    def section(key: str, nums: Sequence[int]) -> str:
        return f"/{key} {_refs_array(nums)}" if nums else ""

    dss_num = inc.get_next_object_number()
    parts = [
        section("Certs", cert_nums),
        section("CRLs", crl_nums),
        section("OCSPs", ocsp_nums),
    ]
    if vri_contents is not None:
        key = hashlib.sha1(vri_contents).hexdigest().upper()
        inner = " ".join(
            p
            for p in (
                section("Cert", cert_nums),
                section("CRL", crl_nums),
                section("OCSP", ocsp_nums),
            )
            if p
        )
        parts.append(f"/VRI << /{key} << {inner} >> >>")
    dss_body = "<< " + " ".join(p for p in parts if p) + " >>"
    inc.add_object(
        dss_num, f"{dss_num} 0 obj\n{dss_body}\nendobj\n".encode("latin-1")
    )

    # Re-emit the catalog with /DSS added, preserving its existing entries.
    doc = PdfCosParser(original_pdf).parse()
    root_ref = doc.trailer.get(PdfName("Root"))
    catalog = doc.get_object(root_ref)
    if not isinstance(catalog, PdfDictionary) or root_ref is None:
        raise ValueError("cannot locate document catalog to attach /DSS")
    catalog.mapping[PdfName("DSS")] = PdfIndirectReference(dss_num, 0)
    catalog_str = PdfCosWriter(doc).serialize_object(catalog)
    catalog_num = root_ref.object_number
    inc.add_object(
        catalog_num,
        f"{catalog_num} 0 obj\n{catalog_str}\nendobj\n".encode("latin-1"),
    )

    return original_pdf + inc.generate()


def enable_ltv(
    signed_pdf: bytes, *, extra: Optional[DssMaterial] = None
) -> bytes:
    """Add a ``/DSS`` to an already-signed PDF, turning PAdES-T into PAdES-LT.

    Validation material embedded in each signature's CMS (certificates, and any
    CRLs/OCSP responses) is harvested automatically; *extra* lets the caller add
    revocation data that was not embedded at signing time (the usual case).  The
    first signature receives a ``/VRI`` entry.

    The original bytes are preserved, so the signatures stay valid.
    """
    from aspose_pdf.engine.simple_pdf import SimplePdf

    material = DssMaterial()
    vri_contents: Optional[bytes] = None
    for sig in SimplePdf.from_bytes(signed_pdf).signatures:
        material.merge(collect_validation_material(sig.contents))
        if vri_contents is None:
            vri_contents = sig.contents
    if extra is not None:
        material.merge(extra)
    if material.deduped().is_empty():
        return signed_pdf
    return build_dss(signed_pdf, material, vri_contents=vri_contents)


# ---------------------------------------------------------------------------
# Document timestamp (PAdES-LTA archive timestamp)
# ---------------------------------------------------------------------------
# Hex capacity reserved for the RFC 3161 token in the DocTimeStamp /Contents.
_DOCTS_CONTENTS_HEX = 16384


def _acroform_update_objects(doc, field_num: int) -> List[tuple]:
    """Return ``(obj_num, bytes)`` re-emissions that add *field_num* to AcroForm.

    Handles an inline AcroForm (re-emit the catalog) and an indirect one
    (re-emit just the AcroForm object), creating one if absent.
    """
    root_ref = doc.trailer.get(PdfName("Root"))
    catalog = doc.get_object(root_ref)
    if not isinstance(catalog, PdfDictionary) or root_ref is None:
        raise ValueError("cannot locate document catalog for the timestamp field")
    writer = PdfCosWriter(doc)
    field_ref = PdfIndirectReference(field_num, 0)

    acro_value = catalog.get(PdfName("AcroForm"))
    acro = _resolve(doc, acro_value)
    if isinstance(acro, PdfDictionary):
        fields = _resolve(doc, acro.get(PdfName("Fields")))
        if not isinstance(fields, PdfArray):
            fields = PdfArray([])
        fields.append(field_ref)
        acro.mapping[PdfName("Fields")] = fields
        acro.mapping[PdfName("SigFlags")] = PdfNumber(3)
        if isinstance(acro_value, PdfIndirectReference):
            num = acro_value.object_number
            body = writer.serialize_object(acro)
            return [(num, f"{num} 0 obj\n{body}\nendobj\n".encode("latin-1"))]
    else:
        catalog.mapping[PdfName("AcroForm")] = PdfDictionary(
            {
                PdfName("Fields"): PdfArray([field_ref]),
                PdfName("SigFlags"): PdfNumber(3),
            }
        )

    num = root_ref.object_number
    body = writer.serialize_object(catalog)
    return [(num, f"{num} 0 obj\n{body}\nendobj\n".encode("latin-1"))]


def add_document_timestamp(
    pdf_bytes: bytes,
    *,
    tsa: Optional[tuple] = None,
    timestamp_url: Optional[str] = None,
    hash_algo: str = "sha256",
    timeout: float = 10.0,
) -> bytes:
    """Append a document timestamp (``ETSI.RFC3161``) — the PAdES-LTA step.

    The timestamp is a signature dictionary whose ``/Contents`` is an RFC 3161
    token over its own ``ByteRange``; it therefore protects everything signed so
    far, including the ``/DSS``.  Supply either a local *tsa* ``(cert, key)`` or
    a network *timestamp_url*.
    """
    if tsa is None and not timestamp_url:
        raise ValueError("a local 'tsa' or a 'timestamp_url' is required")

    inc = IncrementalUpdate(pdf_bytes)
    ts_sig_num = inc.get_next_object_number()
    ts_field_num = inc.get_next_object_number()

    placeholder = "0" * _DOCTS_CONTENTS_HEX
    sig_body = (
        "<< /Type /DocTimeStamp /Filter /Adobe.PPKLite /SubFilter /ETSI.RFC3161 "
        "/ByteRange [0000000000 0000000000 0000000000 0000000000] "
        f"/Contents <{placeholder}> >>"
    )
    inc.add_object(
        ts_sig_num, f"{ts_sig_num} 0 obj\n{sig_body}\nendobj\n".encode("latin-1")
    )
    field_body = f"<< /FT /Sig /T (Timestamp) /V {ts_sig_num} 0 R >>"
    inc.add_object(
        ts_field_num,
        f"{ts_field_num} 0 obj\n{field_body}\nendobj\n".encode("latin-1"),
    )

    doc = PdfCosParser(pdf_bytes).parse()
    for num, body in _acroform_update_objects(doc, ts_field_num):
        inc.add_object(num, body)

    combined = bytearray(pdf_bytes + inc.generate())

    # Locate the DocTimeStamp placeholders (the last in the file) and patch.
    br_marker = combined.rfind(b"/ByteRange [")
    arr_start = br_marker + len(b"/ByteRange [")
    contents_marker = combined.rfind(b"/Contents <")
    contents_start = contents_marker + len(b"/Contents <")
    contents_end = combined.index(b">", contents_start)

    total = len(combined)
    byte_range = (0, contents_start, contents_end, total - contents_end)
    br_text = "{:010d} {:010d} {:010d} {:010d}".format(*byte_range).encode("latin-1")
    combined[arr_start : arr_start + 43] = br_text

    signed = bytes(combined[0:contents_start] + combined[contents_end:])
    imprint = hashlib.new(hash_algo, signed).digest()

    from aspose_pdf.engine import timestamp as ts_mod

    if tsa is not None:
        token = ts_mod.make_timestamp_token(imprint, hash_algo, tsa[0], tsa[1])
    else:
        token = ts_mod.request_timestamp(
            imprint, hash_algo, timestamp_url, timeout=timeout
        )
    token_hex = token.hex().encode("latin-1")
    if len(token_hex) > _DOCTS_CONTENTS_HEX:
        raise ValueError(
            "timestamp token exceeds the reserved /Contents placeholder "
            f"({len(token_hex)} > {_DOCTS_CONTENTS_HEX} hex chars)"
        )
    token_hex += b"0" * (_DOCTS_CONTENTS_HEX - len(token_hex))
    combined[contents_start:contents_end] = token_hex
    return bytes(combined)


# ---------------------------------------------------------------------------
# Read / harvest
# ---------------------------------------------------------------------------
def _resolve(doc, obj):
    if isinstance(obj, PdfIndirectReference):
        return doc.get_object(obj)
    return obj


def _decoded_stream_bytes(doc, stream: PdfStream) -> Optional[bytes]:
    """Return a stream's decoded bytes, applying ``/Filter`` if present."""
    filt = _resolve(doc, stream.get(PdfName("Filter")))
    if filt is None:
        return stream.content
    from aspose_pdf.engine.filters import StreamDecoder

    parms = _resolve(doc, stream.get(PdfName("DecodeParms")))
    try:
        return StreamDecoder.decode(stream.content, filt, parms)
    except Exception:
        # Unknown/edge filter — fall back to the raw bytes.
        return stream.content


def _read_stream_array(doc, arr) -> List[bytes]:
    arr = _resolve(doc, arr)
    if not isinstance(arr, PdfArray):
        return []
    out: List[bytes] = []
    for item in arr.items:
        stream = _resolve(doc, item)
        if isinstance(stream, PdfStream):
            data = _decoded_stream_bytes(doc, stream)
            if data:
                out.append(data)
    return out


def read_dss(pdf_bytes: bytes) -> DssMaterial:
    """Harvest validation material from a document's ``/DSS`` (best effort)."""
    try:
        doc = PdfCosParser(pdf_bytes).parse()
        root = _resolve(doc, doc.trailer.get(PdfName("Root")))
        if not isinstance(root, PdfDictionary):
            return DssMaterial()
        dss = _resolve(doc, root.get(PdfName("DSS")))
        if not isinstance(dss, PdfDictionary):
            return DssMaterial()
        return DssMaterial(
            certs=_read_stream_array(doc, dss.get(PdfName("Certs"))),
            crls=_read_stream_array(doc, dss.get(PdfName("CRLs"))),
            ocsps=_read_stream_array(doc, dss.get(PdfName("OCSPs"))),
        ).deduped()
    except Exception:
        # read_dss feeds validation, which must never raise.
        return DssMaterial()

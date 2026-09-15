"""An appended revision takes object numbers nobody in the file already has.

``IncrementalUpdate`` -- behind signing, the ``/DSS`` and document timestamps --
took the next free number from the *newest* cross-reference section. That
section lists only what its own revision changed, so after any update that
rewrote a low-numbered object -- a catalog touched by an earlier save, a field
filled in by Acrobat -- it offered numbers older revisions still own. Signing
such a file gave the signature dictionary the page's number: the new revision
replaced the page, pdfium could not open the result and MuPDF found no text on
it, while the signature itself validated, since the bytes it covers were intact.

The next number is now above the trailer's ``/Size`` (which covers the whole
file, ISO 32000-1 7.5.5) and above every object header in the file.
"""

from __future__ import annotations

import io
import re
import zlib

import pytest
from cryptography.hazmat.primitives import serialization

from aspose_pdf import Document
from aspose_pdf.engine import dss
from aspose_pdf.engine.cos import PdfDictionary, PdfName
from aspose_pdf.engine.incremental_update import IncrementalUpdate
from aspose_pdf.engine.pdf_parser_cos import PdfCosParser
from aspose_pdf.engine.sign_field import sign_field
from aspose_pdf.engine.signing import SigningUtils


def _with_signature_field() -> bytes:
    document = Document()
    page = document.pages.add()
    page.add_text("Signed body", 72, 700, font_size=18)
    document.form.add_signature_field("Sig1", page, (60, 600, 260, 680))
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _updated_catalog_only(data: bytes, *, size: int | None = None) -> bytes:
    """Append a revision that rewrites just the catalog, its lowest-numbered object."""
    trailer = data[data.rindex(b"trailer") :]
    root = int(re.search(rb"/Root (\d+) 0 R", trailer).group(1))
    body = re.search(rb"\n%d 0 obj\n(.*?)\nendobj" % root, data, re.S).group(1)
    declared = size if size is not None else int(re.search(rb"/Size (\d+)", trailer).group(1))
    previous = int(re.search(rb"startxref\s+(\d+)", trailer).group(1))
    at = len(data)
    revision = b"%d 0 obj\n" % root + body + b"\nendobj\n"
    table_at = at + len(revision)
    revision += b"xref\n0 1\n0000000000 65535 f \n%d 1\n%010d 00000 n \n" % (root, at)
    revision += b"trailer\n<< /Size %d /Root %d 0 R /Prev %d >>\nstartxref\n%d\n%%%%EOF\n" % (
        declared,
        root,
        previous,
        table_at,
    )
    return data + revision


def _kinds(data: bytes) -> dict[int, object]:
    """Each object's number and what it is: a stream, or a dictionary's /Type."""
    objects = PdfCosParser(data).parse().objects
    kinds = {}
    for number in objects:
        obj = objects[number]
        kinds[number] = obj.mapping.get(PdfName("Type")) if isinstance(obj, PdfDictionary) else type(obj).__name__
    return kinds


def _assert_intact(original: bytes, updated: bytes) -> None:
    before, after = _kinds(original), _kinds(updated)
    appended = {int(n) for n in re.findall(rb"(?:^|[\r\n])(\d+) 0 obj", updated[len(original) :])}
    new = appended - set(before)
    # A revision may rewrite an object, but not put something else in its place...
    replaced = {n: (before[n], after[n]) for n in appended & set(before) if before[n] != after[n]}
    assert not replaced, f"replaced {replaced}"
    # ...and what it adds takes numbers above everything the file had.
    assert new and min(new) > max(before), f"new objects {sorted(new)} against {max(before)}"
    document = Document(io.BytesIO(updated))
    assert len(document.pages) == 1
    assert document.pages[0].extract_text().strip() == "Signed body"


@pytest.fixture(scope="module")
def creds():
    return SigningUtils.create_self_signed_cert()


def test_signing_after_an_update_keeps_every_object(creds):
    original = _updated_catalog_only(_with_signature_field())
    cert, key = creds
    signed = sign_field(original, "Sig1", cert, key)
    _assert_intact(original, signed)
    trailer = signed[signed.rindex(b"trailer") :]
    assert int(re.search(rb"/Size (\d+)", trailer).group(1)) > max(_kinds(original))


def test_the_dss_and_a_document_timestamp_keep_every_object(creds):
    original = _updated_catalog_only(_with_signature_field())
    cert, _key = creds
    der = cert.public_bytes(serialization.Encoding.DER)
    _assert_intact(original, dss.build_dss(original, dss.DssMaterial(certs=[der])))
    _assert_intact(original, dss.add_document_timestamp(original, tsa=creds))


def test_a_size_that_is_too_small_is_overruled_by_the_objects_present():
    original = _updated_catalog_only(_with_signature_field(), size=3)
    assert IncrementalUpdate(original).next_obj_num > max(_kinds(original))


def test_objects_that_only_the_size_accounts_for_are_not_reused():
    # A cross-reference stream file whose highest-numbered objects live in an
    # object stream: no header anywhere names 7 or 8, only /Size does.
    content = b"BT /F1 24 Tf 72 700 Td (Signed body) Tj ET"
    members = {7: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>", 8: b"<< /Title (Packed) >>"}
    header = body = b""
    for number, value in members.items():
        header += b"%d %d " % (number, len(body))
        body += value + b" "
    packed = header + body
    loose = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
        3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 7 0 R >> >> "
        b"/Contents 4 0 R >>",
        4: b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        5: b"<< /Type /ObjStm /N 2 /First %d /Length %d >>\nstream\n" % (len(header), len(packed)) + packed + b"\nendstream",
    }
    raw = bytearray(b"%PDF-1.5\n")
    offsets = {}
    for number in sorted(loose):
        offsets[number] = len(raw)
        raw += b"%d 0 obj\n" % number + loose[number] + b"\nendobj\n"
    offsets[6] = len(raw)
    rows = b""
    for number in range(9):
        if number in members:
            rows += bytes([2]) + (5).to_bytes(4, "big") + sorted(members).index(number).to_bytes(2, "big")
        elif number in offsets:
            rows += bytes([1]) + offsets[number].to_bytes(4, "big") + (0).to_bytes(2, "big")
        else:
            rows += bytes([0]) + (0).to_bytes(4, "big") + (65535).to_bytes(2, "big")
    stream = zlib.compress(rows)
    raw += (
        b"6 0 obj\n<< /Type /XRef /Size 9 /W [1 4 2] /Root 1 0 R /Info 8 0 R /Filter /FlateDecode /Length %d >>\n"
        b"stream\n" % len(stream)
        + stream
        + b"\nendstream\nendobj\n"
    )
    raw += b"startxref\n%d\n%%%%EOF\n" % offsets[6]
    original = bytes(raw)

    assert IncrementalUpdate(original).next_obj_num == 9
    cert, _key = SigningUtils.create_self_signed_cert()
    updated = dss.build_dss(original, dss.DssMaterial(certs=[cert.public_bytes(serialization.Encoding.DER)]))
    _assert_intact(original, updated)
    font = PdfCosParser(updated).parse().objects[7]
    assert isinstance(font, PdfDictionary) and font.mapping[PdfName("Type")] == PdfName("Font")

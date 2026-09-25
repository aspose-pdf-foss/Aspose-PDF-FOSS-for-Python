"""Object identities survive parsing, rewriting and incremental updates."""

from __future__ import annotations

import io
import re

import pytest

from aspose_pdf import Document
from aspose_pdf.engine.cos import (
    PdfDictionary,
    PdfIndirectReference,
    PdfName,
    PdfNumber,
)
from aspose_pdf.engine.pdf_parser_cos import PdfCosParser
from aspose_pdf.engine.pdf_writer_cos import PdfCosWriter

GENERATIONS = {1: 2, 2: 7, 3: 9, 4: 3, 5: 0, 6: 5, 7: 4, 8: 8, 9: 6}


def source(*, stream=False):
    """A minimal fixture written independently of the production writer."""
    content = b"BT /F1 12 Tf 20 100 Td (Generation text) Tj ET"
    objects = {
        1: b"<< /Type /Catalog /Pages 2 7 R /AcroForm 7 4 R >>",
        2: b"<< /Type /Pages /Count 1 /Kids [3 9 R] >>",
        3: b"<< /Type /Page /Parent 2 7 R /MediaBox [0 0 200 200] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 3 R /Annots [9 6 R] >>",
        4: b"<< /Length "
        + str(len(content)).encode()
        + b" >>\nstream\n"
        + content
        + b"\nendstream",
        5: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        6: b"<< /Title (Generation title) >>",
        7: b"<< /Fields [8 8 R] /SigFlags 0 >>",
        8: b"<< /FT /Sig /T (Approval) /Kids [9 6 R] >>",
        9: b"<< /Type /Annot /Subtype /Widget /Rect [0 0 0 0] /Parent 8 8 R /P 3 9 R >>",
    }
    data = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = {}
    for number, body in objects.items():
        offsets[number] = len(data)
        data += f"{number} {GENERATIONS[number]} obj\n".encode() + body + b"\nendobj\n"
    xref = len(data)
    if stream:
        offsets[10] = xref
        entries = b"\0" + bytes(4) + b"\xff\xff"
        entries += b"".join(
            b"\x01"
            + offsets[n].to_bytes(4, "big")
            + GENERATIONS.get(n, 0).to_bytes(2, "big")
            for n in range(1, 11)
        )
        data += (
            f"10 0 obj\n<< /Type /XRef /Size 11 /W [1 4 2] /Root 1 2 R /Info 6 5 R /Length {len(entries)} >>\nstream\n".encode()
            + entries
            + b"\nendstream\nendobj\n"
        )
    else:
        data += b"xref\n0 10\n0000000000 65535 f \n"
        for number in range(1, 10):
            data += f"{offsets[number]:010d} {GENERATIONS[number]:05d} n \n".encode()
        data += b"trailer\n<< /Size 10 /Root 1 2 R /Info 6 5 R >>\n"
    data += f"startxref\n{xref}\n%%EOF\n".encode()
    return bytes(data)


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("compressed", [False, True])
def test_writer_preserves_object_headers_and_references(stream, compressed):
    doc = PdfCosParser(source(stream=stream)).parse()
    output = PdfCosWriter(doc, use_object_streams=compressed).write()
    for number, generation in GENERATIONS.items():
        if generation:
            assert f"{number} {generation} obj".encode() in output
    assert b"/Root 1 2 R" in output
    reopened = PdfCosParser(output).parse()
    assert (
        reopened.get_object(PdfIndirectReference(6, 5))[PdfName("Title")].value
        == b"Generation title"
    )


@pytest.mark.parametrize("stream", [False, True])
def test_parser_checks_reference_generation_without_loading_objects(stream):
    doc = PdfCosParser(source(stream=stream)).parse()
    assert doc.objects.materialized_count == 0
    assert doc.get_object(PdfIndirectReference(6, 0)) is None
    assert doc.objects.materialized_count == 0
    assert (
        doc.get_object(PdfIndirectReference(6, 5))[PdfName("Title")].value
        == b"Generation title"
    )


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("incremental", [False, True])
def test_public_edit_and_save_preserves_generations(stream, incremental, tmp_path):
    original = source(stream=stream)
    with Document(io.BytesIO(original)) as document:
        document.info["Title"] = "Updated title"
        document.pages[0].add_text("Added text", 20, 50)
        target = tmp_path / "edited.pdf"
        document.save(target, incremental=incremental)
    result = target.read_bytes()
    if incremental:
        assert result.startswith(original)
        assert b"6 5 obj" in result[len(original) :]
    with Document(target) as reopened:
        assert reopened.page_count == 1
        assert reopened.info["Title"] == "Updated title"
        assert "Generation text" in reopened.pages[0].extract_text()
        assert "Added text" in reopened.pages[0].extract_text()
    assert_generations(result)


def assert_generations(data):
    """Xref identity must agree with the object header in either layout."""
    doc = PdfCosParser(data).parse()
    for number, generation in GENERATIONS.items():
        obj = doc.get_object(PdfIndirectReference(number, generation))
        assert obj is not None, (number, generation)
        if generation:
            header = re.match(rb"(\d+)\s+(\d+)\s+obj", data[doc.xref_table[number] :])
            assert tuple(map(int, header.groups())) == (number, generation)


@pytest.mark.parametrize("algorithm", ["RC4", "AES-128", "AES-256"])
@pytest.mark.parametrize("incremental", [False, True])
def test_encryption_uses_the_preserved_generation(algorithm, incremental):
    with Document(io.BytesIO(source())) as document:
        document.encrypt("user", "owner", algorithm=algorithm)
        output = io.BytesIO()
        document.save(output)
    sealed = output.getvalue()
    assert b"Generation title" not in sealed
    with Document(io.BytesIO(sealed), password="user") as document:
        assert document.info["Title"] == "Generation title"
        document.info["Title"] = "Resaved encrypted title"
        output = io.BytesIO()
        document.save(output, incremental=incremental)
    with Document(io.BytesIO(output.getvalue()), password="user") as reopened:
        assert reopened.info["Title"] == "Resaved encrypted title"
        assert "Generation text" in reopened.pages[0].extract_text()


def test_generation_changes_with_a_reused_object_number():
    data = source()
    previous = int(re.findall(rb"startxref\s+(\d+)", data)[-1])
    body = b"6 6 obj\n<< /Title (Replacement) >>\nendobj\n"
    xref = len(data) + len(body)
    update = (
        body
        + f"xref\n6 1\n{len(data):010d} 00006 n \ntrailer\n<< /Size 10 /Root 1 2 R /Info 6 6 R /Prev {previous} >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    doc = PdfCosParser(data + update).parse()
    assert doc.get_object(PdfIndirectReference(6, 5)) is None
    assert (
        doc.get_object(PdfIndirectReference(6, 6))[PdfName("Title")].value
        == b"Replacement"
    )


def test_free_entry_in_a_new_revision_does_not_restore_old_object():
    data = source()
    previous = int(re.findall(rb"startxref\s+(\d+)", data)[-1])
    update = f"xref\n6 1\n0000000000 00006 f \ntrailer\n<< /Size 10 /Root 1 2 R /Prev {previous} >>\nstartxref\n{len(data)}\n%%EOF\n".encode()
    doc = PdfCosParser(data + update).parse()
    assert 6 not in doc.objects
    assert doc.get_object(PdfIndirectReference(6, 5)) is None


def test_nonzero_generation_is_never_packed_into_an_object_stream():
    doc = PdfCosParser(source()).parse()
    written = PdfCosWriter(doc, use_object_streams=True).write()
    assert b"/ObjStm" in written
    parsed = PdfCosParser(written).parse()
    assert parsed.objects._compressed.keys() == {5}
    assert parsed.trailer[PdfName("Size")].value > 10


@pytest.fixture(scope="module")
def credentials():
    from aspose_pdf.engine.signing import SigningUtils
    from tests.helpers_signatures import timestamp_authority

    root, root_key = SigningUtils.create_self_signed_ca("Generation root")
    leaf, key = SigningUtils.issue_certificate("Generation signer", root, root_key)
    return (
        root,
        root_key,
        leaf,
        key,
        timestamp_authority("Generation TSA", root, root_key),
    )


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("algorithm", [None, "RC4", "AES-128"])
def test_signing_dss_and_timestamp_preserve_existing_identities(
    credentials, stream, algorithm
):
    from datetime import UTC, datetime, timedelta

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes

    from aspose_pdf.validation import PadesLevel, ValidationOptions, ValidationStatus

    root, root_key, leaf, key, tsa = credentials
    parsed = PdfCosParser(source()).parse()
    # The DSS already has a nonzero generation before signing. Extending it
    # must preserve that identity just like the catalog, AcroForm and field.
    parsed.objects[10] = PdfDictionary()
    parsed.generations[10] = 12
    parsed.get_object(parsed.trailer[PdfName("Root")])[PdfName("DSS")] = (
        parsed.reference(10)
    )
    original = PdfCosWriter(parsed, use_object_streams=stream).write()
    if algorithm:
        with Document(io.BytesIO(original)) as document:
            document.encrypt("user", "owner", algorithm=algorithm)
            buffer = io.BytesIO()
            document.save(buffer)
            original = buffer.getvalue()
    crl = (
        x509.CertificateRevocationListBuilder()
        .issuer_name(root.subject)
        .last_update(datetime.now(UTC) - timedelta(hours=1))
        .next_update(datetime.now(UTC) + timedelta(days=1))
        .sign(root_key, hashes.SHA256())
    )
    password = "user" if algorithm else None
    with Document(io.BytesIO(original), password=password) as document:
        document.sign(
            "Approval",
            certificate=leaf,
            private_key=key,
            extra_certificates=[root],
            pades=True,
            timestamp_authority=tsa,
            certify=2,
        )
        document.add_ltv(certificates=[tsa[0]], crls=[crl])
        document.add_document_timestamp(timestamp_authority=tsa)
        buffer = io.BytesIO()
        document.save(buffer, incremental=True)
    result = buffer.getvalue()
    assert result.startswith(original)
    assert b"10 12 obj" in result[len(original) :]
    with Document(io.BytesIO(result), password=password) as document:
        assert len(document.signatures) == 2
        options = ValidationOptions(trusted_certificates=[root], check_revocation=True)
        approval = document.signatures[0].validate(options)
        assert approval.status == ValidationStatus.VALID, approval.errors
        assert approval.pades_level == PadesLevel.LTA
        assert document.signatures[1].validate(options).status == ValidationStatus.VALID


def test_header_and_xref_generation_mismatch_is_rejected():
    from aspose_pdf.exceptions import PdfParseException

    data = source().replace(b"6 5 obj", b"6 4 obj")
    doc = PdfCosParser(data).parse()
    with pytest.raises(PdfParseException, match="disagrees with its header"):
        doc.get_object(PdfIndirectReference(6, 5))


@pytest.mark.parametrize("stream", [False, True])
def test_incremental_xref_matches_nonzero_object_header(stream):
    from aspose_pdf.engine.incremental_update import IncrementalUpdate

    original = source(stream=stream)
    update = IncrementalUpdate(original)
    update.add_object(6, b"6 5 obj\n<< /Title (Incremental title) >>\nendobj\n")
    result = original + update.generate()
    doc = PdfCosParser(result).parse()
    assert doc.generations[6] == 5
    assert (
        doc.get_object(PdfIndirectReference(6, 5))[PdfName("Title")].value
        == b"Incremental title"
    )


def test_new_objects_do_not_reuse_freed_numbers_with_old_references():
    doc = PdfCosParser(source()).parse()
    doc.generations[20] = 4
    doc.xref_table[20] = 0
    new = doc.register_object(PdfNumber(42))
    assert new.object_number == 21
    assert new.gen_number == 0
    assert doc.get_object(PdfIndirectReference(20, 3)) is None


@pytest.mark.parametrize("compressed", [False, True])
def test_free_generation_is_preserved_and_new_writer_objects_do_not_reuse_it(
    compressed,
):
    doc = PdfCosParser(source()).parse()
    doc.generations[10] = 3
    doc.xref_table[10] = 0
    del doc.objects[6]
    output = PdfCosWriter(doc, use_object_streams=compressed).write()
    result = PdfCosParser(output).parse()
    assert result.generations[6] == 6
    assert result.generations[10] == 3
    assert result.get_object(PdfIndirectReference(6, 5)) is None
    assert 10 not in result.objects


def test_reconstruction_preserves_generation_from_object_headers():
    data = source()
    data = data[: data.rfind(b"startxref")] + b"startxref\n1\n%%EOF\n"
    doc = PdfCosParser(data).parse()
    assert doc.generations[6] == 5
    assert b"6 5 obj" in PdfCosWriter(doc).write()

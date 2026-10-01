"""A streamed document answers what an eagerly loaded one answers.

`supported-features.md` promises that "a streamed document reads and edits like
any other, and stays lazy doing it". The two load paths are each other's oracle:
both are ours, they must agree, and a disagreement is a defect in one of them.

`from_file_lazy` defers page content and the resources a page reaches, which is
the point of it -- but it also used to defer things that are *descriptions of the
document*, and deferring those did not make the load lazier, it made them
absent:

* a signed document reported **no signatures at all** through
  `open_streaming`, so a caller asking the security question got the wrong
  answer with nothing said. (The bytes were never at risk: `can_append` and
  `existing_signatures_bind` read the COS graph, not that list, so the save
  still appended a revision and left the signature intact.)
* `attachments` came back empty, and `_sync_attachments_to_cos` reads an empty
  mapping as *the caller removed them all* -- so **saving a streamed document
  deleted its attachments**, dropping the `/Names /EmbeddedFiles` tree and
  leaving the `/Filespec` and `/EmbeddedFile` objects unreachable. Silent: no
  warning, no exception, 1023 bytes in and 975 out.

Both are extracted at load now, beside the metadata and the outlines. The
payloads they read are bounded by the same `PdfLoadLimits` as any other stream.
"""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document, PdfLoadLimits
from aspose_pdf.engine.simple_pdf import SimplePdf
from aspose_pdf.exceptions import PdfResourceLimitException


def _both_ways(path):
    """The same file as an eagerly loaded and as a streamed document."""
    return Document(path), Document.open_streaming(path)


@pytest.fixture
def with_attachment(tmp_path):
    document = Document()
    document.pages.add()
    document.pages[0].add_text("Body", 50, 700)
    document.add_attachment("note.txt", b"payload")
    path = tmp_path / "attached.pdf"
    document.save(path)
    return path


@pytest.fixture
def signed(tmp_path):
    from datetime import UTC, datetime, timedelta

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Signer")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .sign(key, hashes.SHA256())
    )

    document = Document()
    document.pages.add()
    document.pages[0].add_text("Signed", 50, 700)
    document.sign(certificate=certificate, private_key=key, reason="R", location="L")
    path = tmp_path / "signed.pdf"
    document.save(path)
    return path


def test_a_streamed_document_sees_its_attachments(with_attachment):
    eager, lazy = _both_ways(with_attachment)
    assert list(lazy.attachments) == list(eager.attachments) == ["note.txt"]
    assert lazy.get_embedded_file("note.txt").contents == b"payload"
    assert [f.name for f in lazy.embedded_files] == ["note.txt"]


def test_saving_a_streamed_document_keeps_its_attachments(with_attachment):
    # The defect: an empty `attachments` read as a removal, so the save dropped
    # the name tree and the attachment could not be found again by any reader.
    streamed = Document.open_streaming(with_attachment)
    buffer = io.BytesIO()
    streamed.save(buffer)
    data = buffer.getvalue()

    assert b"/EmbeddedFiles" in data
    reopened = Document(io.BytesIO(data))
    assert list(reopened.attachments) == ["note.txt"]
    assert reopened.get_embedded_file("note.txt").contents == b"payload"


def test_a_streamed_document_sees_an_attachments_metadata(tmp_path):
    # The /Subtype mime type, the /Desc description and the /Params dates come
    # from a second mapping, which was deferred along with the payloads.
    import datetime

    document = Document()
    document.pages.add()
    document.add_attachment(
        "note.txt",
        b"payload",
        mime="text/plain",
        description="A note",
        creation_date=datetime.datetime(2026, 1, 2, 3, 4, 5),
    )
    path = tmp_path / "meta.pdf"
    document.save(path)

    eager, lazy = _both_ways(path)
    for loaded in (eager, lazy):
        spec = loaded.get_embedded_file("note.txt")
        assert spec.mime_type == "text/plain"
        assert spec.description == "A note"
        assert spec.creation_date == datetime.datetime(2026, 1, 2, 3, 4, 5)


def test_both_paths_save_the_same_file(with_attachment):
    def saved(document):
        buffer = io.BytesIO()
        document.save(buffer)
        return buffer.getvalue()

    eager, lazy = _both_ways(with_attachment)
    assert len(saved(lazy)) == len(saved(eager))


def test_removing_the_last_attachment_still_drops_the_tree(with_attachment):
    # The behaviour the empty mapping was meant to express has to survive: an
    # emptied mapping means a removal, now that an unloaded one cannot occur.
    for document in _both_ways(with_attachment):
        document.remove_attachment("note.txt")
        buffer = io.BytesIO()
        document.save(buffer)
        data = buffer.getvalue()
        assert b"/EmbeddedFiles" not in data
        assert list(Document(io.BytesIO(data)).attachments) == []


def test_a_streamed_document_sees_its_signatures(signed):
    eager, lazy = _both_ways(signed)
    assert len(lazy.signatures) == len(eager.signatures) == 1
    assert [(s.reason, s.location) for s in lazy.signatures] == [("R", "L")]


def test_a_streamed_save_still_appends_to_a_signed_document(signed):
    original = signed.read_bytes()
    streamed = Document.open_streaming(signed)
    buffer = io.BytesIO()
    streamed.save(buffer)
    data = buffer.getvalue()

    # An incremental update: the signed bytes are kept exactly as a prefix.
    assert data[: len(original)] == original
    assert len(Document(io.BytesIO(data)).signatures) == 1


def test_a_streamed_document_stays_lazy(with_attachment):
    streamed = Document.open_streaming(with_attachment)
    engine = streamed._engine_pdf
    assert engine._lazy is True
    assert engine.page_contents == []        # nothing decoded yet
    assert streamed.pages[0].extract_text().strip() == "Body"
    assert engine.page_contents == []        # and still nothing cached


def test_the_attachments_a_streamed_load_reads_are_bounded(with_attachment):
    # Reading them at load does not escape the load limits.
    with pytest.raises(PdfResourceLimitException):
        SimplePdf.from_file_lazy(
            with_attachment, limits=PdfLoadLimits(max_decoded_stream_bytes=4)
        )


@pytest.mark.parametrize(
    ("name", "answer"),
    [
        ("page_count", lambda d: len(d.pages)),
        ("text", lambda d: d.extract_text()),
        ("rects", lambda d: [tuple(p.rect) for p in d.pages]),
        ("info", lambda d: dict(sorted(d.info.items()))),
        ("attachments", lambda d: sorted(d.attachments)),
        ("signatures", lambda d: len(d.signatures)),
        ("outlines", lambda d: [(i.title, i.page_index) for i in d.outlines]),
        ("fields", lambda d: sorted(f.name for f in d.form.fields)),
        ("validate", lambda d: d.validate()),
        ("html length", lambda d: len(d.to_html())),
    ],
)
def test_the_two_paths_agree(with_attachment, name, answer):
    eager, lazy = _both_ways(with_attachment)
    assert answer(lazy) == answer(eager)

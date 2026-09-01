"""Encryption applied by the COS writer, as it serialises.

Encryption used to exist only in the legacy writer, which rebuilds a file from
the in-memory model. Encrypting a *loaded* document therefore threw away
everything the model does not carry -- form fields, attachments, optional
content, marked content -- and, worse, a document merely opened with a password
and saved again came back with every string turned to noise: they had been
decrypted at load and were written back in the clear under a trailer that still
said ``/Encrypt``.

The writer applies the handler itself now, so an encrypted save keeps the
graph. These tests pin what that has to mean, in both directions: what a reader
gets back, and what the bytes actually contain. The per-object key derivation
itself is pinned by ``test_standard_security_handler``; here it is used to open
what the writer wrote, so a string or stream left in the clear -- or enciphered
twice -- shows up as a mismatch.

The other half of the check lives in ``test_cross_validation``, where qpdf has
to agree.
"""

from __future__ import annotations

import hashlib
import io
import re
import zlib

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from aspose_pdf import Document, OptimizationOptions
from aspose_pdf.engine.cos import (
    PdfDictionary,
    PdfDocument,
    PdfName,
    PdfNumber,
    PdfStream,
    PdfString,
)
from aspose_pdf.engine.encryption import (
    EncryptionUtils,
    decrypt_object_in_place,
)
from aspose_pdf.engine.pdf_writer_cos import PdfCosWriter, WriterEncryption
from aspose_pdf.exceptions import PdfSecurityException
from aspose_pdf.outlines import OutlineItem

ALGORITHMS = ["RC4", "AES-128", "AES-256"]


# ---------------------------------------------------------------------------
# Opening what the writer wrote, from the specification
# ---------------------------------------------------------------------------


def _object_key(file_key: bytes, obj_num: int, aes: bool) -> bytes:
    """ISO 32000-1 Algorithm 1. A 256-bit file key is used directly (R6)."""
    if len(file_key) >= 32:
        return file_key
    extended = file_key + bytes(
        [obj_num & 0xFF, (obj_num >> 8) & 0xFF, (obj_num >> 16) & 0xFF, 0, 0]
    )
    if aes:
        extended += b"sAlT"
    return hashlib.md5(extended).digest()[: min(len(file_key) + 5, 16)]


def _rc4(key: bytes, data: bytes) -> bytes:
    state = list(range(256))
    j = 0
    for i in range(256):
        j = (j + state[i] + key[i % len(key)]) % 256
        state[i], state[j] = state[j], state[i]
    out, i, j = bytearray(), 0, 0
    for byte in data:
        i = (i + 1) % 256
        j = (j + state[i]) % 256
        state[i], state[j] = state[j], state[i]
        out.append(byte ^ state[(state[i] + state[j]) % 256])
    return bytes(out)


def _decipher(file_key: bytes, algorithm: str, obj_num: int, data: bytes) -> bytes:
    aes = algorithm.startswith("AES")
    key = _object_key(file_key, obj_num, aes)
    if not aes:
        return _rc4(key, data)
    decryptor = Cipher(algorithms.AES(key), modes.CBC(data[:16])).decryptor()
    plain = decryptor.update(data[16:]) + decryptor.finalize()
    return plain[: -plain[-1]] if plain else plain


def _objects(data: bytes) -> dict[int, bytes]:
    """Every ``N 0 obj ... endobj`` body in *data*, by object number."""
    found = {}
    for match in re.finditer(rb"(\d+) 0 obj\n(.*?)\nendobj\n", data, re.S):
        found[int(match.group(1))] = match.group(2)
    return found


def _stream_body(body: bytes) -> bytes:
    start = body.index(b"stream\n") + len(b"stream\n")
    return body[start : body.rindex(b"\nendstream")]


def _hex_string(body: bytes, key: str) -> bytes:
    match = re.search(rb"/" + key.encode() + rb" <([0-9a-fA-F]*)>", body)
    assert match is not None, f"/{key} not written as a hex string"
    return bytes.fromhex(match.group(1).decode())


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


def _page_of_everything() -> bytes:
    """A saved document carrying one of each construct the writer must keep."""
    document = Document()
    page = document.pages.add()
    page.add_text("Heading", 60, 720, font_size=20)
    page.add_text("Body text that runs on for a while.", 60, 700, font_size=11)
    document.outlines.add(OutlineItem("Chapter one", 0))
    document.form.add_text_field("nickname", 0, (60, 100, 260, 130), value="typed")
    document.add_attachment("notes.txt", b"an attachment", mime="text/plain")
    document.info["Title"] = "Sealed but structured"
    watermark = document.layers.add("Watermark")
    with document.pages[0].layer(watermark):
        document.pages[0].add_text("DRAFT", 100, 400, font_size=40)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _sealed(plain: bytes, algorithm: str, password: str = "u") -> bytes:
    document = Document(io.BytesIO(plain))
    document.encrypt(password, "owner", algorithm=algorithm)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


@pytest.fixture(scope="module")
def plain() -> bytes:
    return _page_of_everything()


# ---------------------------------------------------------------------------
# The structure an encrypted save has to keep
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("algorithm", ALGORITHMS)
def test_an_encrypted_save_keeps_the_structure_it_was_given(plain, algorithm):
    """The whole point: encryption is a serialisation concern, not a rewrite."""
    reopened = Document(io.BytesIO(_sealed(plain, algorithm)), password="u")

    assert reopened.page_count == 1
    assert [field.name for field in reopened.form] == ["nickname"]
    assert reopened.attachments["notes.txt"] == b"an attachment"
    assert reopened.layers.names() == ["Watermark"]
    assert reopened.outlines[0].title == "Chapter one"
    assert reopened.info["Title"] == "Sealed but structured"


@pytest.mark.parametrize("algorithm", ALGORITHMS)
def test_strings_come_back_as_they_went_in(plain, algorithm):
    """A document opened and saved again used to have every string destroyed.

    Loading decrypts the strings in place; without the writer putting the
    cipher back they were emitted as plaintext under a trailer that still
    declared ``/Encrypt``, so every reader "decrypted" them into noise.
    """
    once = _sealed(plain, algorithm)
    document = Document(io.BytesIO(once), password="u")
    twice = io.BytesIO()
    document.save(twice)

    reopened = Document(io.BytesIO(twice.getvalue()), password="u")
    assert reopened.info["Title"] == "Sealed but structured"
    assert [field.name for field in reopened.form] == ["nickname"]
    assert reopened.form["nickname"].value == "typed"


@pytest.mark.parametrize("algorithm", ALGORITHMS)
def test_resaving_keeps_the_file_id_the_key_is_bound_to(plain, algorithm):
    """Revisions below 5 derive the key from ``/ID[0]``; a new one locks it."""
    once = _sealed(plain, algorithm)
    document = Document(io.BytesIO(once), password="u")
    twice = io.BytesIO()
    document.save(twice)

    def first_id(data: bytes) -> bytes:
        return bytes.fromhex(
            re.search(rb"/ID \[\s*<([0-9a-fA-F]*)>", data).group(1).decode()
        )

    assert first_id(twice.getvalue()) == first_id(once)


@pytest.mark.parametrize("algorithm", ALGORITHMS)
def test_changing_the_password_replaces_the_dictionary_that_is_there(plain, algorithm):
    """Re-encrypting has to overwrite ``/Encrypt``, not point at the old one.

    The trailer already names an encryption dictionary; leaving that object as
    it was would publish the previous ``/O`` and ``/U`` beside a file key
    derived from the new password, and nothing would open the document.
    """
    document = Document(io.BytesIO(_sealed(plain, algorithm)), password="u")
    document.change_passwords("u", "second", "owner2")
    buffer = io.BytesIO()
    document.save(buffer)
    data = buffer.getvalue()

    assert len(re.findall(rb"/Filter /Standard", data)) == 1
    reopened = Document(io.BytesIO(data), password="second")
    assert reopened.info["Title"] == "Sealed but structured"
    with pytest.raises(PdfSecurityException):
        Document(io.BytesIO(data), password="u")


def test_an_unusable_file_id_is_replaced_by_the_one_the_key_was_derived_from():
    """A producer that writes ``/ID [<> <>]`` leaves nothing to derive from.

    ``encrypt`` then generates its own, and the trailer has to carry *that* --
    keeping the empty array would bind every reader to a different key.
    """
    document = Document()
    document.pages.add().add_text("Body", 60, 700, font_size=11)
    buffer = io.BytesIO()
    document.save(buffer)
    blank_id = buffer.getvalue().replace(
        re.search(rb"/ID \[[^\]]*\]", buffer.getvalue()).group(0), b"/ID [<> <>]"
    )

    document = Document(io.BytesIO(blank_id))
    document.encrypt("u", "owner", algorithm="RC4")
    derived = document._engine_pdf._file_id
    out = io.BytesIO()
    document.save(out)
    data = out.getvalue()

    written = bytes.fromhex(
        re.search(rb"/ID \[\s*<([0-9a-fA-F]*)>", data).group(1).decode()
    )
    assert written == derived
    assert Document(io.BytesIO(data), password="u").page_count == 1


# ---------------------------------------------------------------------------
# What the bytes actually contain
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("algorithm", ALGORITHMS)
def test_the_page_content_is_enciphered_under_its_own_object_key(plain, algorithm):
    document = Document(io.BytesIO(plain))
    document.encrypt("u", "owner", algorithm=algorithm)
    file_key = document._engine_pdf.encryption_key
    buffer = io.BytesIO()
    document.save(buffer)
    data = buffer.getvalue()

    assert b"Body text that runs on" not in data
    for number, body in _objects(data).items():
        if b"stream\n" not in body or b"/Type /ObjStm" in body:
            continue
        plaintext = _decipher(file_key, algorithm, number, _stream_body(body))
        if b"Body text that runs on" in plaintext:
            return
    raise AssertionError("no stream decrypted to the page content")


@pytest.mark.parametrize("algorithm", ALGORITHMS)
def test_the_encrypt_dictionary_itself_stays_in_the_clear(plain, algorithm):
    """A reader needs ``/O`` and ``/U`` before it can derive any key at all."""
    document = Document(io.BytesIO(plain))
    document.encrypt("u", "owner", algorithm=algorithm)
    engine = document._engine_pdf
    expected_o, expected_u = engine.O, engine.U
    buffer = io.BytesIO()
    document.save(buffer)
    data = buffer.getvalue()

    enc_number = int(re.search(rb"/Encrypt (\d+) 0 R", data).group(1))
    body = _objects(data)[enc_number]
    assert b"/Filter /Standard" in body
    # Written as computed: enciphering them would lock every reader out, and
    # this library first.
    assert _hex_string(body, "O") == expected_o
    assert _hex_string(body, "U") == expected_u
    assert Document(io.BytesIO(data), password="u").page_count == 1


def test_the_signature_contents_are_never_enciphered(plain):
    """ISO 32000-1 7.6.2: ``/Contents`` is written over the encrypted file."""
    from aspose_pdf.engine.signing import SigningUtils

    cert, key = SigningUtils.create_self_signed_cert()
    document = Document(io.BytesIO(plain))
    document.encrypt("u", "owner", algorithm="AES-128")
    engine = document._engine_pdf
    engine.signing_creds = (cert, key)
    engine.signature = {"Name": "Signature1"}
    buffer = io.BytesIO()
    document.save(buffer)
    data = buffer.getvalue()

    contents = re.search(rb"/Contents <([0-9a-fA-F]+)>", data).group(1)
    # A DER SEQUENCE header, i.e. the CMS blob as the signer produced it, with
    # its length spanning most of the placeholder. Enciphered, the leading byte
    # would be anything but 0x30 and the length would not add up.
    assert contents[:2].lower() == b"30"
    header = bytes.fromhex(contents[:8].decode())
    assert header[1] == 0x82  # two length bytes follow
    assert int.from_bytes(header[2:4], "big") * 2 < len(contents)


# ---------------------------------------------------------------------------
# Object streams
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("algorithm", ALGORITHMS)
def test_an_object_stream_is_enciphered_whole_not_string_by_string(plain, algorithm):
    """ISO 32000-1 7.5.7: strings inside an ``/ObjStm`` are not encrypted again.

    Enciphering them individually would leave a reader -- which decrypts the
    stream and then parses plain objects out of it -- with unusable values.
    """
    document = Document(io.BytesIO(plain))
    document.optimize(OptimizationOptions(use_object_streams=True))
    document.encrypt("u", "owner", algorithm=algorithm)
    file_key = document._engine_pdf.encryption_key
    buffer = io.BytesIO()
    document.save(buffer)
    data = buffer.getvalue()

    number, body = next(
        (num, body)
        for num, body in _objects(data).items()
        if b"/Type /ObjStm" in body
    )
    inflated = zlib.decompress(
        _decipher(file_key, algorithm, number, _stream_body(body))
    )
    assert b"Sealed but structured" in inflated

    # And the whole thing round-trips.
    reopened = Document(io.BytesIO(data), password="u")
    assert reopened.info["Title"] == "Sealed but structured"


@pytest.mark.parametrize("algorithm", ALGORITHMS)
def test_the_encrypt_dictionary_is_written_outside_the_object_stream(plain, algorithm):
    """It cannot live in a stream a reader needs its own key to read."""
    document = Document(io.BytesIO(plain))
    document.optimize(OptimizationOptions(use_object_streams=True))
    document.encrypt("u", "owner", algorithm=algorithm)
    buffer = io.BytesIO()
    document.save(buffer)
    data = buffer.getvalue()

    assert b"/Type /ObjStm" in data
    enc_number = int(re.search(rb"/Encrypt (\d+) 0 R", data).group(1))
    assert enc_number in _objects(data), "/Encrypt was packed away or dropped"


def test_a_cross_reference_stream_is_left_in_the_clear(plain):
    """ISO 32000-1 7.5.8.2 -- it is how a reader finds ``/Encrypt``."""
    document = Document(io.BytesIO(plain))
    document.optimize(OptimizationOptions(use_object_streams=True))
    document.encrypt("u", "owner", algorithm="AES-128")
    buffer = io.BytesIO()
    document.save(buffer)
    data = buffer.getvalue()

    number, body = next(
        (num, body)
        for num, body in _objects(data).items()
        if b"/Type /XRef" in body
    )
    entries = zlib.decompress(_stream_body(body))
    width = sum(
        int(value)
        for value in re.search(rb"/W \[ ([0-9 ]+)\]", body).group(1).split()
    )
    assert len(entries) % width == 0
    # The entry for the /Encrypt object is a type-1 (in-file) record.
    assert entries[number * width : number * width + 1] == b"\x01"


# ---------------------------------------------------------------------------
# Streams that are not encrypted, and streams that were never encrypted
# ---------------------------------------------------------------------------


def _written_payload(stream: PdfStream, **handler) -> bytes:
    """Serialise a one-object document and return that object's stream bytes."""
    doc = PdfDocument()
    doc.register_object(stream)
    written = PdfCosWriter(
        doc, encryption=WriterEncryption(b"0" * 16, "AES-128", **handler)
    ).write()
    start = written.index(b"stream\n") + len(b"stream\n")
    return written[start : written.index(b"\nendstream", start)]


def test_a_cross_reference_stream_in_the_graph_is_never_enciphered():
    """The rule holds wherever the object turns up, not only where we write one.

    A file loaded from an xref-stream producer carries the old ``/XRef`` object
    in its graph, and a full rewrite emits it like any other object.
    """
    entries = b"\x01\x00\x00\x00\x09"
    plain = PdfStream(content=entries, mapping={PdfName("Type"): PdfName("XRef")})
    other = PdfStream(content=entries, mapping={PdfName("Type"): PdfName("ObjStm")})

    assert _written_payload(plain) == entries
    assert _written_payload(other) != entries


@pytest.mark.parametrize(
    "mapping",
    [
        {PdfName("Type"): PdfName("Sig")},
        # /Type is optional in a signature dictionary; /ByteRange is what makes
        # one recognisable when a producer leaves it out.
        {PdfName("ByteRange"): PdfNumber(0)},
    ],
    ids=["typed", "byte-range only"],
)
def test_a_signature_dictionary_keeps_its_contents_in_the_clear(mapping):
    """Rewriting an already-signed document must not encipher the CMS blob."""
    doc = PdfDocument()
    signature = PdfDictionary(
        {**mapping, PdfName("Contents"): PdfString(b"\x30\x82CMS")}
    )
    signature.mapping[PdfName("Reason")] = PdfString(b"Approved")
    doc.register_object(signature)
    written = PdfCosWriter(
        doc, encryption=WriterEncryption(b"0" * 16, "AES-128")
    ).write()

    assert b"<3082434d53>" in written  # /Contents, byte for byte
    assert b"Approved" not in written  # every other string still enciphered


def test_metadata_stays_plain_when_the_handler_says_encrypt_metadata_false():
    """``/EncryptMetadata false`` exists so a tool with no password can read it."""
    packet = b"<?xpacket begin=?><x:xmpmeta/></xpacket>"
    doc = PdfDocument()
    stream = PdfStream(
        content=packet,
        mapping={PdfName("Type"): PdfName("Metadata")},
    )
    doc.register_object(stream)
    doc.trailer.mapping[PdfName("Root")] = PdfNumber(0)  # unused here

    handler = WriterEncryption(b"0" * 16, "AES-128", encrypt_metadata=False)
    written = PdfCosWriter(doc, encryption=handler).write()
    assert packet in written

    encrypting = WriterEncryption(b"0" * 16, "AES-128", encrypt_metadata=True)
    assert packet not in PdfCosWriter(doc, encryption=encrypting).write()


def test_a_stream_built_in_code_is_read_back_as_the_plaintext_it_is(plain):
    """Whether a payload needs deciphering is a property of the object.

    A document opened with a password holds both kinds of stream at once --
    what came out of the file, and what the caller has added since -- so
    deciding from "is there a key?" turns every new stream into noise.
    """
    document = Document(io.BytesIO(_sealed(plain, "AES-128")), password="u")
    engine = document._engine_pdf
    payload = b"q 1 0 0 1 0 0 cm Q"
    authored = PdfStream(content=payload)
    engine._cos_doc.register_object(authored)

    assert engine._decode_cos_stream(authored) == payload


def test_a_stream_authored_after_load_is_not_deciphered_as_if_it_were_stored(plain):
    """New content is plaintext already; "decrypting" it would be noise.

    The graph of a document opened with a password holds both kinds at once --
    what came out of the file, and what the caller added since -- so the two
    have to be told apart by the object rather than by whether a key exists.
    """
    sealed = _sealed(plain, "AES-128")
    document = Document(io.BytesIO(sealed), password="u")
    document.pages[0].add_text("Added after opening", 60, 660, font_size=11)
    buffer = io.BytesIO()
    document.save(buffer)

    reopened = Document(io.BytesIO(buffer.getvalue()), password="u")
    content = reopened.pages[0].content
    assert b"Added after opening" in content
    assert b"Body text that runs on" in content


def test_decrypting_an_object_twice_leaves_it_alone_the_second_time():
    """The flag is the helper's own contract, not only the store's bookkeeping.

    Objects reach it once per document because the store tracks that, but the
    helper is shared -- signing unlocks its own parse of the same file -- and a
    second pass would cipher the plaintext back into noise.
    """
    # RC4 rather than AES: a stream cipher transforms whatever it is given, so
    # a second pass is always visible. AES would fail its padding check on
    # plaintext and be left alone -- correct behaviour that happens to hide the
    # missing guard.
    key, algorithm = b"k" * 16, "RC4"
    payload = b"q 1 0 0 1 0 0 cm Q"
    stream = PdfStream(
        content=EncryptionUtils.encrypt_object_data(key, algorithm, 4, payload)
    )
    stream.content_decrypted = False

    decrypt_object_in_place(stream, 4, 0, key, algorithm)
    assert stream.content == payload
    decrypt_object_in_place(stream, 4, 0, key, algorithm)
    assert stream.content == payload


@pytest.mark.parametrize("algorithm", ALGORITHMS)
def test_unlocking_for_signing_leaves_the_encryption_dictionary_alone(algorithm):
    """``/O`` and ``/U`` are stored unencrypted; "decrypting" them destroys them."""
    from aspose_pdf.engine.pdf_parser_cos import PdfCosParser
    from aspose_pdf.engine.sign_field import _unlock

    plain = _page_of_everything()
    document = Document(io.BytesIO(plain))
    document.encrypt("u", "owner", algorithm=algorithm)
    expected_o = document._engine_pdf.O
    handler = document._engine_pdf._writer_encryption()
    buffer = io.BytesIO()
    document.save(buffer)
    data = buffer.getvalue()

    doc = PdfCosParser(data).parse()
    _unlock(doc, handler)
    (enc_number,) = handler.exempt
    assert doc.objects[enc_number].mapping[PdfName("O")].value == expected_o


@pytest.mark.parametrize("algorithm", ALGORITHMS)
def test_a_signature_over_an_encrypted_document_survives_being_reopened(algorithm):
    """The whole encrypted+signed path, read back by this library.

    Both halves have to hold at once: the signature dictionary's strings are
    enciphered like any other object's, while ``/Contents`` is not -- decrypt
    the CMS blob and the signature stops verifying.

    RC4 is in the list for a reason. A block cipher fed something that was
    never enciphered usually fails its padding check, and a value that will not
    decrypt is deliberately left as stored -- so an entry wrongly written in
    the clear, or wrongly deciphered on the way in, can survive an AES round
    trip untouched. A stream cipher transforms it either way.
    """
    from cryptography.hazmat.primitives import serialization

    from aspose_pdf.engine.signing import SigningUtils
    from aspose_pdf.validation import ValidationOptions, ValidationStatus

    cert, key = SigningUtils.create_self_signed_cert()
    document = Document(io.BytesIO(_page_of_everything()))
    document.encrypt("u", "owner", algorithm=algorithm)
    engine = document._engine_pdf
    engine.signing_creds = (cert, key)
    engine.signature = {"Name": "Signature1", "Reason": "Approved"}
    buffer = io.BytesIO()
    document.save(buffer)

    reopened = Document(io.BytesIO(buffer.getvalue()), password="u")
    names = {field.name for field in reopened.form}
    assert {"nickname", "Signature1"} <= names

    signature = reopened._engine_pdf.signatures[0]
    assert signature.reason == "Approved"
    options = ValidationOptions(
        trusted_certificates=[cert.public_bytes(serialization.Encoding.DER)]
    )
    assert signature.validate(options).status is ValidationStatus.VALID


# ---------------------------------------------------------------------------
# Taking the protection off
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("algorithm", ALGORITHMS)
def test_decrypt_writes_a_plain_file_and_keeps_nothing_behind(plain, algorithm):
    """``decrypt`` is the counterpart of ``encrypt``, not merely "unlock"."""
    document = Document(io.BytesIO(_sealed(plain, algorithm)), password="u")
    document.decrypt("u")
    buffer = io.BytesIO()
    document.save(buffer)
    data = buffer.getvalue()

    assert b"/Encrypt" not in data
    # The owner-password hashes have no business outliving the protection.
    assert b"/O <" not in data and b"/U <" not in data

    reopened = Document(io.BytesIO(data))
    assert reopened.info["Title"] == "Sealed but structured"
    assert [field.name for field in reopened.form] == ["nickname"]
    assert b"Body text that runs on" in reopened.pages[0].content


# ---------------------------------------------------------------------------
# The writer used as a plain serialiser
# ---------------------------------------------------------------------------


def test_without_a_handler_nothing_is_touched():
    doc = PdfDocument()
    doc.register_object(
        PdfDictionary({PdfName("Title"): PdfString(b"readable")})
    )
    assert b"(readable)" in PdfCosWriter(doc).write()


def test_serialize_indirect_encrypts_what_serialize_object_cannot():
    """An incremental revision has to encrypt too, and it writes one object."""
    doc = PdfDocument()
    handler = WriterEncryption(b"k" * 16, "RC4")
    writer = PdfCosWriter(doc, encryption=handler)
    value = PdfDictionary({PdfName("T"): PdfString(b"fieldname")})

    assert "fieldname" in writer.serialize_object(value)
    assert "fieldname" not in writer.serialize_indirect(7, value)
    # ...and the state does not leak into the next call.
    assert "fieldname" in writer.serialize_object(value)

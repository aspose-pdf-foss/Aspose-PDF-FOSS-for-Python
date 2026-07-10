"""Dependency-free Type 1 (``/FontFile``) font subsetting by glyph erasure.

A Type 1 program is a cleartext PostScript header, an ``eexec``-encrypted binary
section (the Private dict with ``/Subrs`` and ``/CharStrings``) and a zero-padded
trailer.  Each charstring is itself encrypted.  Subsetting keeps every glyph
*name* but replaces each unused glyph's charstring with an empty one
(``0 0 hsbw endchar``), so the CharStrings dictionary keeps its structure and no
subroutine renumbering is needed -- mirroring the glyph-erasure approach used for
TrueType and CFF.  The cleartext header and trailer are unchanged, so only the
``/Length2`` (encrypted) size changes.

Decryption helpers and the cipher constants are shared with
:mod:`aspose_pdf.engine.type1_outlines`; this module only adds the inverse
(encryption) and the byte-level rewrite.
"""

from __future__ import annotations

import re

from .type1_outlines import _C1, _C2, _CHARSTRING_R, _EEXEC_R, _decrypt, _is_hex

__all__ = ["subset_type1"]

# ``/name <len> (RD|-|) `` immediately followed by <len> encrypted bytes.
_CHARSTRING_ENTRY = re.compile(rb"/([^ \t\r\n/{}\[\]()]+)\s+(\d+)\s+(RD|-\|) ")

# An empty glyph: "0 0 hsbw endchar" -> 0 encodes as 139, hsbw is op 13,
# endchar is op 14.
_EMPTY_CHARSTRING = bytes([139, 139, 13, 14])


def _encrypt(data: bytes, r: int) -> bytes:
    """Encrypt *data* with the Type 1 eexec/charstring cipher (inverse of decrypt)."""
    out = bytearray(len(data))
    for i, plain in enumerate(data):
        cipher = (plain ^ (r >> 8)) & 0xFF
        out[i] = cipher
        r = ((cipher + r) * _C1 + _C2) & 0xFFFF
    return bytes(out)


def _encrypt_charstring(plain: bytes, len_iv: int) -> bytes:
    """Charstring-encrypt *plain* with *len_iv* discarded leading bytes."""
    return _encrypt(bytes(len_iv) + plain, _CHARSTRING_R)


def subset_type1(
    font_bytes: bytes, keep_names: set, length1: int, length2: int
) -> bytes | None:
    """Return a subset of *font_bytes* keeping *keep_names* (others emptied).

    *length1*/*length2* are the ``/FontFile`` cleartext and encrypted section
    sizes. Returns ``None`` when nothing can be stripped or the program cannot be
    rewritten safely (the caller then keeps the font whole).
    """
    data = bytes(font_bytes)
    if not (length1 and length2 and length1 + length2 <= len(data)):
        return None
    clear = data[:length1]
    encrypted = data[length1 : length1 + length2]
    trailer = data[length1 + length2 :]
    if _is_hex(encrypted):
        return None  # only binary eexec (the usual PDF /FontFile form) is handled

    private = _decrypt(encrypted, _EEXEC_R, 4)
    len_iv = 4
    m = re.search(rb"/lenIV\s+(\d+)", private)
    if m:
        len_iv = int(m.group(1))

    new_private = _rewrite_charstrings(private, keep_names, len_iv)
    if new_private is None:
        return None
    # Re-encrypt: four discarded lead-in bytes then the rewritten Private dict.
    new_encrypted = _encrypt(b"\x00\x00\x00\x00" + new_private, _EEXEC_R)
    return clear + new_encrypted + trailer


def _rewrite_charstrings(private: bytes, keep_names: set, len_iv: int) -> bytes | None:
    """Rebuild the decrypted Private dict with unused glyphs emptied."""
    start = private.find(b"/CharStrings")
    if start < 0:
        return None
    header = re.search(rb"begin", private[start : start + 400])
    if not header:
        return None
    pos = start + header.end()
    out = bytearray()
    erased = 0
    while True:
        match = _CHARSTRING_ENTRY.search(private, pos)
        if not match:
            break
        name = match.group(1).decode("latin-1")
        length = int(match.group(2))
        token = match.group(3)
        blob_end = match.end() + length
        out += private[pos : match.start()]  # inter-entry bytes, verbatim
        if name in keep_names or name == ".notdef":
            out += private[match.start() : blob_end]  # keep the glyph verbatim
        else:
            new_blob = _encrypt_charstring(_EMPTY_CHARSTRING, len_iv)
            out += (
                b"/" + match.group(1) + b" "
                + str(len(new_blob)).encode("ascii") + b" "
                + token + b" " + new_blob
            )
            erased += 1
        pos = blob_end
    out += private[pos:]
    if erased == 0:
        return None  # nothing to strip -> leave the font whole
    return private[: start + header.end()] + bytes(out)

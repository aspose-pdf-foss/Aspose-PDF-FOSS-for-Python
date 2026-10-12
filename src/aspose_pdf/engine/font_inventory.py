"""Build the list of fonts a document uses, one entry per font.

The walk is :meth:`~aspose_pdf.engine.simple_pdf.SimplePdf._rendering_font_occurrences`
-- the same one PDF/A, PDF/UA and PDF/X check embedding with, so the inventory
and the conformance reports can never disagree about which fonts a document
has. What this adds is **grouping**: that walk reports font *dictionaries*, and
a document that writes its own ``/Helv`` on every page has one font and twenty
dictionaries. Entries that say the same thing are folded together and their
pages unioned.

"The same thing" is the font's own description -- its name, its kind, its
encoding, and the object its program lives in. Two subsets of one family
(``ABCDEF+Arial`` and ``GHIJKL+Arial``) are deliberately *not* folded: they
carry different glyphs, and a caller counting embedded programs needs both.
"""

from __future__ import annotations

import re
from typing import Any

from aspose_pdf.document_fonts import DocumentFont
from aspose_pdf.exceptions import PDF_OPERATION_ERRORS

from .cos import PdfArray, PdfDictionary, PdfIndirectReference, PdfName, PdfStream

__all__ = ["inventory"]

#: Six capitals and a plus, the subset tag of ISO 32000-1 9.6.4.
_SUBSET_TAG = re.compile(r"^[A-Z]{6}\+")

_PROGRAM_KEYS = ("FontFile", "FontFile2", "FontFile3")
_PROGRAM_BY_KEY = {"FontFile": "Type 1", "FontFile2": "TrueType"}


def inventory(pdf: Any) -> list[DocumentFont]:
    """Every font *pdf* draws with, in the order the pages reach them."""
    from .simple_pdf import _is_standard14_base_font_name

    grouped: dict[tuple, dict] = {}
    for font, page_index, where in pdf._rendering_font_occurrences():
        key, entry = _describe(pdf, font)
        record = grouped.get(key)
        if record is None:
            record = {"entry": entry, "pages": [], "locations": []}
            grouped[key] = record
        if page_index is not None and page_index not in record["pages"]:
            record["pages"].append(page_index)
        if where not in record["locations"]:
            record["locations"].append(where)

    out: list[DocumentFont] = []
    for record in grouped.values():
        entry = record["entry"]
        base_font = entry["base_font"]
        out.append(
            DocumentFont(
                base_font=base_font,
                name=_SUBSET_TAG.sub("", base_font),
                subtype=entry["subtype"],
                descendant_subtype=entry["descendant_subtype"],
                encoding=entry["encoding"],
                has_differences=entry["has_differences"],
                # A Type 3 font has no program to embed: its glyphs *are* in
                # the file, as the content streams of /CharProcs (9.6.5). It is
                # nothing a reader has to find elsewhere, which is what this
                # field is asked for, so it counts as embedded and carries no
                # program key -- the same reason the conformance checks pass
                # over it rather than reporting it unembedded.
                embedded=(
                    entry["program_key"] is not None or entry["subtype"] == "Type3"
                ),
                program_key=entry["program_key"],
                program_format=entry["program_format"],
                program_bytes=entry["program_bytes"],
                stored_bytes=entry["stored_bytes"],
                is_subset=bool(_SUBSET_TAG.match(base_font)),
                is_standard_14=_is_standard14_base_font_name(base_font),
                has_to_unicode=entry["has_to_unicode"],
                page_indexes=tuple(sorted(record["pages"])),
                locations=tuple(record["locations"]),
            )
        )
    return out


def _describe(pdf: Any, font: PdfDictionary) -> tuple[tuple, dict]:
    """``(grouping key, fields)`` for one font dictionary."""
    subtype = pdf._get_name(font.mapping.get(PdfName("Subtype"))) or ""
    base_font = pdf._get_name(font.mapping.get(PdfName("BaseFont"))) or ""
    encoding, has_differences = _encoding(pdf, font)

    carrier, kind = pdf._font_program_carrier(font)
    descendant_subtype = kind if carrier is not None and carrier is not font else None
    descriptor = (
        pdf._resolve(carrier.mapping.get(PdfName("FontDescriptor")))
        if isinstance(carrier, PdfDictionary)
        else None
    )
    program = (
        _program(pdf, descriptor) if isinstance(descriptor, PdfDictionary) else {}
    )

    fields = {
        "base_font": base_font,
        "subtype": subtype,
        "descendant_subtype": descendant_subtype,
        "encoding": encoding,
        "has_differences": has_differences,
        "program_key": program.get("program_key"),
        "program_format": program.get("program_format"),
        "program_bytes": program.get("program_bytes"),
        "stored_bytes": program.get("stored_bytes"),
        "has_to_unicode": PdfName("ToUnicode") in font.mapping,
    }
    # The program is part of the identity: two dictionaries naming the same
    # font but carrying different programs are two fonts, and folding them
    # would hide one of them. Where the program is not an indirect object there
    # is nothing to compare, and the rest of the description decides.
    key = (
        base_font,
        subtype,
        descendant_subtype,
        encoding,
        has_differences,
        program.get("program_key"),
        program.get("program_id"),
    )
    return key, fields


def _encoding(pdf: Any, font: PdfDictionary) -> tuple[str | None, bool]:
    """``(encoding name, whether codes are remapped)`` from ``/Encoding``.

    The entry is a name, or a dictionary whose ``/BaseEncoding`` names one and
    whose ``/Differences`` remaps individual codes (table 114). A composite
    font's is the CMap that turns codes into CIDs, which may itself be an
    embedded stream rather than a name -- then there is no name to give.
    """
    raw = pdf._resolve(font.mapping.get(PdfName("Encoding")))
    if isinstance(raw, PdfName):
        return raw.name.lstrip("/"), False
    if isinstance(raw, PdfStream):
        # An embedded CMap: it has a name of its own, which is more use than
        # saying nothing.
        name = pdf._get_name(raw.mapping.get(PdfName("CMapName")))
        return (name or None), False
    if isinstance(raw, PdfDictionary):
        base = pdf._get_name(raw.mapping.get(PdfName("BaseEncoding")))
        differences = pdf._resolve(raw.mapping.get(PdfName("Differences")))
        return (base or None), isinstance(differences, PdfArray) and bool(
            differences.items
        )
    return None, False


def _program(pdf: Any, descriptor: PdfDictionary) -> dict:
    """What the descriptor says about the embedded program, or empty fields.

    Two sizes, because they answer different questions. *stored* is what the
    program costs **this file** -- the bytes as they sit in it, compressed by
    whatever filter the producer used, which is the number behind "why is this
    PDF 40 MB". *decoded* is how big the program actually **is**, which is the
    number that says whether a face is a small subset or a whole CJK font.
    """
    for key in _PROGRAM_KEYS:
        reference = descriptor.mapping.get(PdfName(key))
        if reference is None:
            continue
        stream = pdf._resolve(reference)
        if not isinstance(stream, PdfStream):
            continue
        fmt = _PROGRAM_BY_KEY.get(key)
        if fmt is None:
            # /FontFile3 says which compact form it is (table 126): /Type1C,
            # /CIDFontType0C, or /OpenType for a whole sfnt.
            fmt = pdf._get_name(stream.mapping.get(PdfName("Subtype"))) or "compact"
        return {
            "program_key": key,
            "program_format": fmt,
            "program_bytes": _decoded_size(pdf, stream, reference),
            "stored_bytes": len(stream.content),
            "program_id": (
                reference.object_number
                if isinstance(reference, PdfIndirectReference)
                else None
            ),
        }
    return {}


def _decoded_size(pdf: Any, stream: PdfStream, reference: Any) -> int | None:
    """How many bytes the program is once its filters are undone.

    ``/Length1`` is the file's own statement of it for a Type 1 or TrueType
    program (table 122), and trusting it saves decompressing every font in the
    document just to be asked how big they are. Without it -- ``/FontFile3``
    has no such entry -- the stream is decoded. A program this build cannot
    decode reports ``None`` rather than the stored size, which would be a
    different number wearing this one's name.
    """
    length1 = pdf._pdf_number(stream.mapping.get(PdfName("Length1")))
    if length1 is not None and length1 >= 0:
        return int(length1)
    if not stream.mapping.get(PdfName("Filter")):
        return len(stream.content)
    try:
        return len(pdf._decode_cos_stream(stream, reference))
    except PDF_OPERATION_ERRORS:
        return None


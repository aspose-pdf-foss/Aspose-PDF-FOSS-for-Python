"""Extended structural conformance checks for PDF/A, PDF/UA and PDF/X.

This module materially expands the heuristic PDF/A (ISO 19005), PDF/UA
(ISO 14289) and PDF/X (ISO 15930) coverage of
:mod:`aspose_pdf.engine.simple_pdf`.  Everything here
operates purely on the parsed COS object graph of a loaded document: the
checks look for structures that are *prohibited* or *required* by the
standards and that are observable without rendering the page — catalog
entries, the page tree, fonts, annotations, actions, transparency and
optional-content constructs.

They remain **heuristic**: they do not verify glyph coverage, colour
rendering, or the semantic correctness of a structure tree, so they are not a
substitute for a certification-grade validator such as veraPDF (which covers
PDF/A and PDF/UA) or a prepress preflight tool (for PDF/X, which no free
validator checks).  Together with
the base checks in ``SimplePdf`` they nonetheless catch the large majority of
the catalog-, page-, font-, annotation-, action- and transparency-level rules
that real validators enforce.

All public functions accept a duck-typed ``pdf`` object exposing the
``SimplePdf`` engine surface (``_cos_doc``, ``pages``, ``pdf_version``,
``_resolve``, ``_get_name``, ``_get_page_dict``) and return ``(errors,
warnings)`` tuples.  None of them mutate the document.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from ..exceptions import PDF_OPERATION_ERRORS, PdfResourceLimitException
from .cos import (
    _MAX_PDF_INTEGER,
    PdfArray,
    PdfBoolean,
    PdfDictionary,
    PdfName,
    PdfNumber,
    PdfStream,
    PdfString,
    decode_pdf_text_string,
)

# Annotation subtypes not permitted in PDF/A (multimedia / 3D).
_PROHIBITED_ANNOT_SUBTYPES = frozenset(
    {"Sound", "Movie", "Screen", "RichMedia", "3D"}
)

# ...of which PDF/A-4e, the engineering level, exists precisely to allow.
_ENGINEERING_ANNOT_SUBTYPES = frozenset({"RichMedia", "3D"})

# ISO 19005-4 6.9: *every* embedded file shall conform to PDF/A-1, PDF/A-2 or
# PDF/A-4 -- veraPDF puts it as "All of the embedded files shall be compliant
# with ISO 19005-1, 19005-2 or 19005-4", and fails a base part-4 file carrying
# anything else. This was read more narrowly here, as a rule about PDF payloads
# only, which passed a part-4 document with a spreadsheet attached that veraPDF
# rejects. The existence of PDF/A-4f settles it: that level is defined to carry
# embedded files of any type and *requires* an /EmbeddedFiles key, which would
# add nothing if the base level already permitted arbitrary attachments.
# Part 3 is deliberately absent from the list -- its whole purpose is to carry
# arbitrary attachments, so a PDF/A-3 file is no guarantee about what it
# contains. PDF/A-4e does not lift the rule either: it adds 3D and rich media,
# nothing else.
_EMBEDDED_PDFA_PARTS = frozenset({"1", "2", "4"})

# How far down a chain of attached PDFs the *structural* checks are run. Each
# level costs a full parse and validation of the payload, so the descent stops
# while identification -- which is what the rule turns on -- keeps being
# checked all the way down.
_MAX_EMBEDDED_PDF_DEPTH = 2

# Action ``/S`` types prohibited by PDF/A.
_PROHIBITED_ACTION_TYPES = frozenset(
    {
        "Launch",
        "Sound",
        "Movie",
        "ResetForm",
        "ImportData",
        "JavaScript",
        "SetOCGState",
        "Rendition",
        "GoTo3DView",
        "Trans",
    }
)

# Annotation flag bits (PDF 32000-1 Table 165).
ANNOT_FLAG_INVISIBLE = 1 << 0
ANNOT_FLAG_HIDDEN = 1 << 1
ANNOT_FLAG_PRINT = 1 << 2
ANNOT_FLAG_NOVIEW = 1 << 5

# Annotation subtypes that do not need a printable normal appearance.
_ANNOT_NO_APPEARANCE = frozenset({"Popup", "Link"})

_MAX_RESOURCE_DEPTH = 12
_MAX_STRUCT_DEPTH = 50

# ISO 32000-1 Table 333 — standard structure types.  Names are compared with the
# leading slash stripped (matching ``pdf._get_name``).  Any structure element
# ``/S`` that is neither in this set nor remapped to one via the structure
# tree's ``/RoleMap`` is non-standard and breaks tagged-PDF / PDF/UA.
_STANDARD_STRUCT_TYPES = frozenset(
    {
        # Grouping
        "Document", "Part", "Art", "Sect", "Div", "BlockQuote", "Caption",
        "TOC", "TOCI", "Index", "NonStruct", "Private",
        # Paragraph-like (block-level)
        "P", "H", "H1", "H2", "H3", "H4", "H5", "H6",
        # Lists
        "L", "LI", "Lbl", "LBody",
        # Tables
        "Table", "TR", "TH", "TD", "THead", "TBody", "TFoot",
        # Inline-level
        "Span", "Quote", "Note", "Reference", "BibEntry", "Code", "Link",
        "Annot",
        # Ruby / Warichu
        "Ruby", "RB", "RT", "RP", "Warichu", "WT", "WP",
        # Illustration
        "Figure", "Formula", "Form",
    }
)

# Structure types whose accessible content requires an alternate description.
_ALT_REQUIRED_STRUCT_TYPES = frozenset({"Figure", "Formula"})

# Heading structure types mapped to their numeric level (for skip detection).
_HEADING_LEVELS = {"H1": 1, "H2": 2, "H3": 3, "H4": 4, "H5": 5, "H6": 6}


# ---------------------------------------------------------------------------
# Small COS helpers (operate through the engine's resolver)
# ---------------------------------------------------------------------------
def _get_dict(pdf: Any, obj: Any) -> PdfDictionary | None:
    obj = pdf._resolve(obj)
    return obj if isinstance(obj, PdfDictionary) else None


def catalog(pdf: Any) -> PdfDictionary | None:
    """Return the document catalog (``/Root``) dictionary, or ``None``."""
    if pdf._cos_doc is None:
        return None
    try:
        return _get_dict(pdf, pdf._cos_doc.trailer.get(PdfName("Root")))
    except PdfResourceLimitException:
        raise
    except PDF_OPERATION_ERRORS:
        return None


def _is_part1(level_short: str) -> bool:
    return level_short[:1] == "1"


def _parse_pdf_version(value: str) -> float | None:
    m = re.match(r"\s*(\d+)\.(\d+)", value or "")
    if not m:
        return None
    return float(f"{m.group(1)}.{m.group(2)}")


# ---------------------------------------------------------------------------
# PDF/A
# ---------------------------------------------------------------------------
def pdfa_extended(
    pdf: Any, level_short: str, *, _depth: int = 0
) -> tuple[list[str], list[str]]:
    """Return ``(errors, warnings)`` for the extended PDF/A structural checks.

    ``level_short`` is the normalised level (e.g. ``"1b"``, ``"2a"``).
    ``_depth`` counts how many attached PDFs deep this validation already is;
    it bounds the descent and is not part of the caller-facing surface.
    """
    errors: list[str] = []
    warnings: list[str] = []
    if pdf._cos_doc is None:
        return errors, warnings

    part1 = _is_part1(level_short)
    part = level_short[:1]
    # Every part from 3 on permits embedded files, so every one of them needs
    # the /AFRelationship that says what the attachment is *for*.
    allows_embedded = part in ("3", "4")
    # PDF/A-4e is the engineering level: 3D and rich media are the reason it
    # exists, so the annotation types every other part forbids are allowed.
    engineering = level_short == "4e"
    # There is no PDF/A-4a: ISO 19005-4 dropped the accessible/basic/unicode
    # split, so tagging is not a conformance level there.
    level_a = level_short.endswith("a")

    try:
        _check_trailer_id(pdf, errors)
        _check_pdf_version(pdf, level_short, errors)
        _check_binary_comment(pdf, warnings)
        _check_implementation_limits(pdf, errors)
        _check_catalog_rules(pdf, part1, errors)
        _check_acroform(pdf, errors)
        _check_metadata_unfiltered(pdf, errors)
        _check_pages(pdf, part1, allows_embedded, errors, warnings, engineering)
        if allows_embedded:
            _check_embedded_files(pdf, errors, part)
        # PDF/A-4f is the level that exists to carry arbitrary attachments;
        # every other part-4 level requires an attached PDF to be PDF/A itself.
        if part == "4" and level_short != "4f":
            _check_embedded_pdfs(pdf, errors, warnings, _depth)
        if level_a:
            _check_tagging_for_level_a(pdf, errors, warnings)
    except PdfResourceLimitException:
        raise
    except PDF_OPERATION_ERRORS:
        # Conformance checks are best-effort: a malformed object must never
        # crash validation. The base checks already flag structural damage.
        pass

    return errors, warnings


def _check_trailer_id(pdf: Any, errors: list[str]) -> None:
    id_obj = pdf._resolve(pdf._cos_doc.trailer.get(PdfName("ID")))
    if not isinstance(id_obj, PdfArray) or len(id_obj.items) < 1:
        errors.append("PDF/A requires a file identifier (/ID) in the trailer.")


BINARY_COMMENT_MISSING = (
    "PDF/A requires the header to be followed by a comment holding at least "
    "four bytes above 127, marking the file as binary; the file this document "
    "was loaded from has none. A full save writes one."
)


def _check_binary_comment(pdf: Any, warnings: list[str]) -> None:
    """The header must be followed by a comment of four bytes above 127.

    ISO 19005-1 6.1.2, and a convention of ISO 32000-1 7.5.2 for every file
    that holds binary data: it tells anything inspecting the first two lines --
    a file transfer in text mode, an editor deciding whether to translate line
    endings -- to copy the bytes as they stand.

    A *warning*, because it is the one requirement here that is not a property
    of the object graph. The check can only read the bytes the document was
    loaded from, and whether their absence survives depends on how the document
    is saved next: a full save writes a header with the comment, while an
    incremental save keeps the original bytes as its prefix and does not.
    """
    raw = getattr(pdf, "_raw_bytes", None)
    head = bytes(raw[:1024]) if raw is not None else b""
    end_of_header = head.find(b"\n")
    if end_of_header < 0:
        # No loaded bytes to read a header out of -- a document built in
        # memory, or one whose first line never ended. Either way the next full
        # save writes the header, so there is nothing here to have got wrong.
        return
    comment = head[end_of_header + 1 :].split(b"\n", 1)[0]
    if comment.startswith(b"%") and sum(1 for byte in comment if byte > 127) >= 4:
        return
    warnings.append(BINARY_COMMENT_MISSING)


#: ISO 32000-1 annex C.1, which ISO 19005-1 6.1.13 adopts. A conforming reader
#: is not obliged to handle anything past these, so a file that needs it to is
#: not archivable.
_MAX_NAME_BYTES = 127
_MAX_STRING_BYTES = 65535


def _check_implementation_limits(pdf: Any, errors: list[str]) -> None:
    """Report values past the limits a conforming reader must accept."""
    seen: set[int] = set()
    over_long_name: str | None = None
    over_long_string = False
    over_range_integer: int | None = None

    for number, obj in list(pdf._cos_doc.objects.items()):
        if number in seen:
            continue
        seen.add(number)
        for value in _walk_values(pdf, obj, 0):
            if isinstance(value, PdfName):
                text = value.name.lstrip("/")
                if len(text.encode("utf-8")) > _MAX_NAME_BYTES:
                    over_long_name = over_long_name or text[:32]
            elif isinstance(value, PdfString):
                if len(value.value) > _MAX_STRING_BYTES:
                    over_long_string = True
            elif isinstance(value, PdfNumber) and isinstance(value.value, int):
                if abs(value.value) > _MAX_PDF_INTEGER:
                    over_range_integer = over_range_integer or value.value

    if over_long_name is not None:
        errors.append(
            f"PDF/A limits a name to {_MAX_NAME_BYTES} bytes; "
            f"'{over_long_name}...' is longer."
        )
    if over_long_string:
        errors.append(
            f"PDF/A limits a string to {_MAX_STRING_BYTES} bytes; "
            "the document holds a longer one."
        )
    if over_range_integer is not None:
        errors.append(
            "PDF/A limits an integer to +/-2,147,483,647; the document holds "
            f"{over_range_integer}."
        )


def _walk_values(pdf: Any, obj: Any, depth: int):
    """Yield every direct value inside *obj*, references not followed.

    Each object is visited on its own, so following a reference would only
    visit it twice; the depth bound is for a container nested inside itself,
    which a hand-built graph can be.
    """
    if depth > 32:
        return
    if isinstance(obj, PdfDictionary):
        for key, value in obj.mapping.items():
            yield key
            yield from _walk_values(pdf, value, depth + 1)
    elif isinstance(obj, PdfArray):
        for item in obj.items:
            yield from _walk_values(pdf, item, depth + 1)
    else:
        yield obj


def _check_pdf_version(pdf: Any, level_short: str, errors: list[str]) -> None:
    version = _parse_pdf_version(getattr(pdf, "pdf_version", "") or "")
    if version is None:
        return
    if level_short[:1] == "4":
        # PDF/A-4 is defined on PDF 2.0, not merely capped by it: an older
        # header would put the file under a specification it does not follow.
        if abs(version - 2.0) > 1e-9:
            errors.append(
                "PDF/A-4 requires PDF version 2.0; the header declares "
                f"{pdf.pdf_version}."
            )
        return
    if _is_part1(level_short):
        if version > 1.4 + 1e-9:
            errors.append(
                "PDF/A-1 requires PDF version 1.4 or lower; the header declares "
                f"{pdf.pdf_version}."
            )
    elif version > 1.7 + 1e-9:
        errors.append(
            f"PDF/A-{level_short[:1]} requires PDF version 1.7 or lower; the "
            f"header declares {pdf.pdf_version}."
        )


def _check_catalog_rules(pdf: Any, part1: bool, errors: list[str]) -> None:
    root = catalog(pdf)
    if root is None:
        return
    if PdfName("AA") in root:
        errors.append("PDF/A prohibits document-level additional actions (/AA).")
    if part1 and PdfName("OCProperties") in root:
        errors.append("PDF/A-1 prohibits optional content / layers (/OCProperties).")
    if PdfName("Requirements") in root:
        errors.append("PDF/A prohibits the catalog /Requirements entry.")


def _check_acroform(pdf: Any, errors: list[str]) -> None:
    root = catalog(pdf)
    if root is None:
        return
    acro = _get_dict(pdf, root.get(PdfName("AcroForm")))
    if acro is None:
        return
    need = pdf._resolve(acro.get(PdfName("NeedAppearances")))
    if isinstance(need, PdfBoolean) and need.value:
        errors.append("PDF/A prohibits AcroForm /NeedAppearances true.")
    if PdfName("XFA") in acro:
        errors.append("PDF/A prohibits dynamic XFA forms (AcroForm /XFA).")


def _check_metadata_unfiltered(pdf: Any, errors: list[str]) -> None:
    """PDF/A requires the document XMP ``/Metadata`` stream to be unfiltered.

    The packet must be readable by processors that do not decode PDF streams, so
    a ``/Filter`` on the catalog metadata stream is prohibited (ISO 19005-1
    6.7.3, carried into later parts).
    """
    root = catalog(pdf)
    if root is None:
        return
    metadata = pdf._resolve(root.get(PdfName("Metadata")))
    if isinstance(metadata, PdfStream) and metadata.get(PdfName("Filter")) is not None:
        errors.append(
            "PDF/A requires the document XMP /Metadata stream to be unfiltered "
            "(no /Filter)."
        )


def _check_pages(
    pdf: Any,
    part1: bool,
    allows_embedded: bool,
    errors: list[str],
    warnings: list[str],
    engineering: bool = False,
) -> None:
    for i in range(len(pdf.pages)):
        page = pdf._get_page_dict(i)
        if not isinstance(page, PdfDictionary):
            continue
        if PdfName("AA") in page:
            errors.append(
                f"PDF/A prohibits page additional actions (/AA) on page {i + 1}."
            )
        if part1:
            group = _get_dict(pdf, page.get(PdfName("Group")))
            if group is not None and _name(pdf, group, "S") == "Transparency":
                errors.append(
                    "PDF/A-1 prohibits transparency groups "
                    f"(page {i + 1} /Group /S /Transparency)."
                )
        _check_annotations(
            pdf, page, i, part1, allows_embedded, errors, warnings, engineering
        )
        resources = _get_dict(pdf, page.get(PdfName("Resources")))
        if resources is not None:
            _check_resources(pdf, resources, i, part1, errors, set(), 0)


def _name(pdf: Any, d: PdfDictionary, key: str) -> str | None:
    return pdf._get_name(d.get(PdfName(key)))


def _check_annotations(
    pdf: Any,
    page: PdfDictionary,
    i: int,
    part1: bool,
    allows_embedded: bool,
    errors: list[str],
    warnings: list[str],
    engineering: bool = False,
) -> None:
    annots = pdf._resolve(page.get(PdfName("Annots")))
    if not isinstance(annots, PdfArray):
        return
    for ref in annots.items:
        annot = pdf._resolve(ref)
        if not isinstance(annot, PdfDictionary):
            continue
        subtype = _name(pdf, annot, "Subtype")
        label = subtype or "annotation"
        prohibited = _PROHIBITED_ANNOT_SUBTYPES - (
            _ENGINEERING_ANNOT_SUBTYPES if engineering else frozenset()
        )
        if subtype in prohibited:
            errors.append(f"PDF/A prohibits /{subtype} annotations (page {i + 1}).")
        elif subtype == "FileAttachment" and not allows_embedded:
            errors.append(
                "PDF/A-1 and PDF/A-2 prohibit /FileAttachment annotations "
                f"(page {i + 1})."
            )

        flags_obj = pdf._resolve(annot.get(PdfName("F")))
        flags = int(flags_obj.value) if isinstance(flags_obj, PdfNumber) else 0
        if subtype != "Popup":
            if not flags & ANNOT_FLAG_PRINT:
                errors.append(
                    f"PDF/A requires the Print flag on annotations (page {i + 1}, "
                    f"{label})."
                )
            if flags & (ANNOT_FLAG_HIDDEN | ANNOT_FLAG_NOVIEW | ANNOT_FLAG_INVISIBLE):
                errors.append(
                    "PDF/A prohibits the Hidden, NoView and Invisible annotation "
                    f"flags (page {i + 1}, {label})."
                )
            if part1:
                ca = pdf._resolve(annot.get(PdfName("CA")))
                if isinstance(ca, PdfNumber) and ca.value < 1.0 - 1e-9:
                    errors.append(
                        "PDF/A-1 prohibits annotation constant opacity < 1 (/CA) "
                        f"(page {i + 1}, {label})."
                    )
        if subtype not in _ANNOT_NO_APPEARANCE:
            ap = _get_dict(pdf, annot.get(PdfName("AP")))
            if ap is None or PdfName("N") not in ap:
                warnings.append(
                    f"PDF/A: annotation on page {i + 1} ({label}) has no normal "
                    "appearance stream (/AP /N)."
                )

        _check_action(pdf, annot.get(PdfName("A")), i, errors)
        extra = _get_dict(pdf, annot.get(PdfName("AA")))
        if extra is not None:
            for value in extra.mapping.values():
                _check_action(pdf, value, i, errors)


def _check_action(pdf: Any, action_ref: Any, i: int, errors: list[str]) -> None:
    """Flag prohibited action ``/S`` types, following a one-level ``/Next``."""
    queue = [pdf._resolve(action_ref)]
    seen: set[int] = set()
    while queue:
        action = queue.pop()
        if not isinstance(action, PdfDictionary) or id(action) in seen:
            continue
        seen.add(id(action))
        s = _name(pdf, action, "S")
        if s in _PROHIBITED_ACTION_TYPES:
            errors.append(f"PDF/A prohibits /{s} actions (page {i + 1}).")
        nxt = pdf._resolve(action.get(PdfName("Next")))
        if isinstance(nxt, PdfDictionary):
            queue.append(nxt)
        elif isinstance(nxt, PdfArray):
            queue.extend(pdf._resolve(x) for x in nxt.items)


def _check_resources(
    pdf: Any,
    resources: PdfDictionary,
    i: int,
    part1: bool,
    errors: list[str],
    visited: set[int],
    depth: int,
) -> None:
    if depth > _MAX_RESOURCE_DEPTH or id(resources) in visited:
        return
    visited.add(id(resources))

    extgstates = _get_dict(pdf, resources.get(PdfName("ExtGState")))
    if extgstates is not None:
        for ref in extgstates.mapping.values():
            gs = pdf._resolve(ref)
            if isinstance(gs, PdfDictionary):
                _check_extgstate(pdf, gs, i, part1, errors)

    xobjects = _get_dict(pdf, resources.get(PdfName("XObject")))
    if xobjects is None:
        return
    for ref in xobjects.mapping.values():
        xobj = pdf._resolve(ref)
        if not isinstance(xobj, PdfStream):
            continue
        subtype = _name(pdf, xobj, "Subtype")
        if subtype == "PS":
            errors.append(f"PDF/A prohibits PostScript XObjects (page {i + 1}).")
        elif subtype == "Image":
            interpolate = pdf._resolve(xobj.get(PdfName("Interpolate")))
            if isinstance(interpolate, PdfBoolean) and interpolate.value:
                errors.append(
                    "PDF/A prohibits image interpolation (/Interpolate true) "
                    f"(page {i + 1})."
                )
            if part1 and isinstance(
                pdf._resolve(xobj.get(PdfName("SMask"))), PdfStream
            ):
                errors.append(
                    f"PDF/A-1 prohibits soft-mask images (/SMask) (page {i + 1})."
                )
        elif subtype == "Form":
            if PdfName("Ref") in xobj.mapping:
                errors.append(
                    "PDF/A prohibits reference XObjects pointing at external "
                    f"content (page {i + 1})."
                )
            if part1:
                group = _get_dict(pdf, xobj.get(PdfName("Group")))
                if group is not None and _name(pdf, group, "S") == "Transparency":
                    errors.append(
                        "PDF/A-1 prohibits transparency groups (Form XObject, "
                        f"page {i + 1})."
                    )
            nested = _get_dict(pdf, xobj.get(PdfName("Resources")))
            if nested is not None:
                _check_resources(pdf, nested, i, part1, errors, visited, depth + 1)


def _check_extgstate(
    pdf: Any, gs: PdfDictionary, i: int, part1: bool, errors: list[str]
) -> None:
    for key, allowed in (("TR", {"Identity"}), ("TR2", {"Identity", "Default"})):
        if PdfName(key) not in gs:
            continue
        if _name(pdf, gs, key) not in allowed:
            errors.append(
                f"PDF/A prohibits transfer functions in ExtGState (/{key}) "
                f"(page {i + 1})."
            )
    if not part1:
        return
    smask = pdf._resolve(gs.get(PdfName("SMask")))
    if isinstance(smask, PdfDictionary):
        errors.append(
            f"PDF/A-1 prohibits soft masks in ExtGState (/SMask) (page {i + 1})."
        )
    blend = pdf._resolve(gs.get(PdfName("BM")))
    blend_names = (
        [pdf._get_name(x) for x in blend.items]
        if isinstance(blend, PdfArray)
        else [pdf._get_name(blend)]
    )
    for name in blend_names:
        if name not in (None, "Normal", "Compatible"):
            errors.append(
                f"PDF/A-1 prohibits blend mode /{name} in ExtGState (page {i + 1})."
            )
            break
    for key in ("CA", "ca"):
        alpha = pdf._resolve(gs.get(PdfName(key)))
        if isinstance(alpha, PdfNumber) and alpha.value < 1.0 - 1e-9:
            errors.append(
                f"PDF/A-1 prohibits constant alpha < 1 (/{key}) in ExtGState "
                f"(page {i + 1})."
            )


def _check_tagging_for_level_a(
    pdf: Any, errors: list[str], warnings: list[str]
) -> None:
    root = catalog(pdf)
    if root is None:
        return
    if root.get(PdfName("StructTreeRoot")) is None:
        errors.append(
            "PDF/A level A requires a tagged structure tree (/StructTreeRoot)."
        )
    mark_info = _get_dict(pdf, root.get(PdfName("MarkInfo")))
    marked = pdf._resolve(mark_info.get(PdfName("Marked"))) if mark_info else None
    if not (isinstance(marked, PdfBoolean) and marked.value):
        errors.append(
            "PDF/A level A requires MarkInfo /Marked true (tagged PDF)."
        )
    if root.get(PdfName("Lang")) is None:
        warnings.append(
            "PDF/A level A: a default document /Lang is recommended."
        )
    # Level A is tagged PDF: reuse the structure-tree walker for the universally
    # valid subset (RoleMap mapping + Figure/Formula alternate text + Note /ID).
    s_err, s_warn = _walk_struct_for(pdf, "PDF/A level A", full=False)
    errors.extend(s_err)
    warnings.extend(s_warn)


def _check_embedded_files(pdf: Any, errors: list[str], part: str = "3") -> None:
    """Flag embedded file specifications lacking ``/AFRelationship``.

    Applies to the parts that permit attachments at all -- PDF/A-3 and PDF/A-4.
    """
    root = catalog(pdf)
    if root is None:
        return
    names = _get_dict(pdf, root.get(PdfName("Names")))
    if names is None:
        return
    ef_tree = _get_dict(pdf, names.get(PdfName("EmbeddedFiles")))
    if ef_tree is None:
        return
    for value in _iter_name_tree_values(pdf, ef_tree, set(), 0):
        filespec = _get_dict(pdf, value)
        if filespec is None or PdfName("AFRelationship") in filespec:
            continue
        fname = pdf._resolve(filespec.get(PdfName("F")))
        if isinstance(fname, PdfString):
            label = decode_pdf_text_string(fname)
        else:
            label = "embedded file"
        errors.append(
            f"PDF/A-{part} requires /AFRelationship on embedded file "
            f"specifications ({label})."
        )


def _embedded_payload(pdf: Any, filespec: PdfDictionary) -> bytes | None:
    """The decoded bytes of an embedded file, or ``None`` if unreadable."""
    ef = _get_dict(pdf, filespec.get(PdfName("EF")))
    if ef is None:
        return None
    for key in ("F", "UF"):
        ref = ef.get(PdfName(key))
        stream = pdf._resolve(ref)
        if not isinstance(stream, PdfStream):
            continue
        try:
            return pdf._decode_cos_stream(stream, ref)
        except PdfResourceLimitException:
            raise
        except PDF_OPERATION_ERRORS:
            return None
    return None


def _filespec_label(pdf: Any, filespec: PdfDictionary) -> str:
    """The name to blame in a message about this embedded file."""
    for key in ("UF", "F"):
        value = pdf._resolve(filespec.get(PdfName(key)))
        if isinstance(value, PdfString):
            return decode_pdf_text_string(value)
    return "embedded file"


def _embedded_files_to_check(pdf: Any) -> list[tuple[str, bytes | None]]:
    """``(label, payload)`` for every embedded file the next save would write.

    Both sources, because they hold different things: the catalog's
    ``/EmbeddedFiles`` name tree is what a loaded document carries, and
    ``attachments`` is where one added in this session lives until a save writes
    it. Reading only the graph passed a document that was about to be written
    non-conformant -- the same blind spot the PDF/A-4 ``/Info`` check had.
    """
    found: list[tuple[str, bytes | None]] = []
    seen: set[str] = set()

    root = catalog(pdf)
    if root is not None:
        names = _get_dict(pdf, root.get(PdfName("Names")))
        ef_tree = (
            _get_dict(pdf, names.get(PdfName("EmbeddedFiles")))
            if names is not None
            else None
        )
        if ef_tree is not None:
            for value in _iter_name_tree_values(pdf, ef_tree, set(), 0):
                filespec = _get_dict(pdf, value)
                if filespec is None:
                    continue
                label = _filespec_label(pdf, filespec)
                seen.add(label)
                found.append((label, _embedded_payload(pdf, filespec)))

    for name, payload in sorted(getattr(pdf, "attachments", {}).items()):
        if name not in seen:
            found.append((name, bytes(payload) if payload is not None else None))
    return found


def _check_embedded_pdfs(
    pdf: Any, errors: list[str], warnings: list[str], depth: int
) -> None:
    """Every embedded file has to be PDF/A itself (ISO 19005-4 6.9).

    Only PDF/A-4f lifts this, and it applies to the attachment whatever it is: a
    spreadsheet or an image in a base part-4 file fails the rule as surely as a
    non-conforming PDF does, which is what PDF/A-4f exists for. A payload that
    is not a PDF used to be skipped here on the reading that the rule was about
    PDF payloads only, so this passed documents veraPDF rejects.

    Conformance of a PDF payload is established the way the rule states it: it
    has to declare ``pdfaid:part`` 1, 2 or 4. What it declares is then checked
    against the same rules as any other document, so a file that merely *claims*
    PDF/A does not pass -- until the descent bound stops that second half, past
    which the declaration alone is checked.
    """
    from .simple_pdf import SimplePdf, _extract_xmp_pdfaid_fields

    for label, payload in _embedded_files_to_check(pdf):
        # The bytes decide, not the declared MIME type or the file extension:
        # both are producer-supplied labels, and the rule is about what the
        # attachment *is*.
        if payload is None:
            errors.append(
                f"PDF/A-4 requires every embedded file to conform to PDF/A, "
                f"and {label!r} could not be read."
            )
            continue
        if not payload.startswith(b"%PDF-"):
            errors.append(
                f"PDF/A-4 requires every embedded file to conform to PDF/A; "
                f"{label!r} is not a PDF. Use PDF/A-4f, the level for embedded "
                f"files of any type."
            )
            continue

        try:
            inner = SimplePdf.from_bytes(
                payload, limits=getattr(pdf, "_load_limits", None)
            )
        except PdfResourceLimitException:
            raise
        except PDF_OPERATION_ERRORS as exc:
            errors.append(
                f"PDF/A-4 requires the embedded PDF {label!r} to conform to "
                f"PDF/A, but it could not be read ({exc})."
            )
            continue

        metadata = _embedded_xmp(inner)
        part, conformance = (
            _extract_xmp_pdfaid_fields(metadata) if metadata else (None, None)
        )
        if part is None:
            errors.append(
                f"PDF/A-4 requires the embedded PDF {label!r} to conform to "
                "PDF/A; it declares no pdfaid:part."
            )
            continue
        if part not in _EMBEDDED_PDFA_PARTS:
            errors.append(
                f"PDF/A-4 accepts an embedded PDF conforming to PDF/A-1, -2 "
                f"or -4; {label!r} declares part {part!r}."
            )
            continue
        if depth >= _MAX_EMBEDDED_PDF_DEPTH:
            # Say so rather than passing it in silence: the declaration is all
            # that was checked this far down, and a file can declare PDF/A
            # without being it.
            warnings.append(
                f"Embedded PDF {label!r} declares PDF/A-{part} but was not "
                "checked further: attachments nested more than "
                f"{_MAX_EMBEDDED_PDF_DEPTH} deep are taken at their word."
            )
            continue

        inner_level = f"{part}{(conformance or '').lower()}"
        try:
            inner_errors, inner_warnings = inner.check_pdfa_compliance_detailed(
                inner_level, _depth=depth + 1
            )
        except PdfResourceLimitException:
            raise
        except PDF_OPERATION_ERRORS:
            continue
        # Both lists carry across, named for the file they came from. An
        # advisory raised about an attachment is still advisory, and dropping
        # them would hide the one that says how deep the descent went.
        for problem in inner_errors:
            errors.append(f"Embedded PDF {label!r}: {problem}")
        for advisory in inner_warnings:
            warnings.append(f"Embedded PDF {label!r}: {advisory}")


def _embedded_xmp(pdf: Any) -> bytes | None:
    """The catalog ``/Metadata`` packet of *pdf*, or ``None``."""
    root = catalog(pdf)
    if root is None:
        return None
    stream = pdf._resolve(root.get(PdfName("Metadata")))
    return stream.content if isinstance(stream, PdfStream) else None


def _iter_name_tree_values(pdf: Any, node: PdfDictionary, visited: set[int], depth: int):
    """Yield the value objects of a PDF name tree (``/Names`` and ``/Kids``)."""
    if depth > _MAX_RESOURCE_DEPTH or id(node) in visited:
        return
    visited.add(id(node))
    pairs = pdf._resolve(node.get(PdfName("Names")))
    if isinstance(pairs, PdfArray):
        # Name tree leaves store [key1, value1, key2, value2, ...].
        for idx in range(1, len(pairs.items), 2):
            yield pairs.items[idx]
    kids = pdf._resolve(node.get(PdfName("Kids")))
    if isinstance(kids, PdfArray):
        for kid in kids.items:
            kd = _get_dict(pdf, kid)
            if kd is not None:
                yield from _iter_name_tree_values(pdf, kd, visited, depth + 1)


# ---------------------------------------------------------------------------
# PDF/UA — structure tree (shared with PDF/A level A)
# ---------------------------------------------------------------------------
def _build_role_map(pdf: Any, struct_root: PdfDictionary) -> dict:
    """Return a ``{non-standard-type: target-type}`` mapping from ``/RoleMap``."""
    role_map: dict = {}
    rm = _get_dict(pdf, struct_root.get(PdfName("RoleMap")))
    if rm is None:
        return role_map
    for key, value in rm.mapping.items():
        if isinstance(key, PdfName):
            target = pdf._get_name(value)
            if target:
                role_map[key.name.lstrip("/")] = target
    return role_map


def _resolved_struct_type(s: str, role_map: dict) -> str:
    """Resolve a structure type name through ``/RoleMap`` (following chains)."""
    seen: set[str] = set()
    while s in role_map and s not in seen:
        seen.add(s)
        s = role_map[s]
    return s


def _has_alt_text(pdf: Any, elem: PdfDictionary) -> bool:
    for key in ("Alt", "ActualText"):
        value = pdf._resolve(elem.get(PdfName(key)))
        if isinstance(value, PdfString) and value.value:
            return True
    return False


def _check_struct_element(
    pdf: Any,
    elem: PdfDictionary,
    s: str,
    role_map: dict,
    ctx: dict,
    parent_type: str | None,
    errors: list[str],
    warnings: list[str],
) -> None:
    label = ctx["label"]
    full = ctx["full"]
    resolved = _resolved_struct_type(s, role_map)

    if resolved not in _STANDARD_STRUCT_TYPES:
        errors.append(
            f"{label}: structure type /{s} is not a standard type and is not "
            "mapped to a standard type via /RoleMap."
        )
    if resolved in _ALT_REQUIRED_STRUCT_TYPES and not _has_alt_text(pdf, elem):
        errors.append(
            f"{label}: /{resolved} structure element requires an alternate "
            "description (/Alt or /ActualText)."
        )
    if resolved == "Note":
        nid = pdf._resolve(elem.get(PdfName("ID")))
        if not (isinstance(nid, PdfString) and nid.value):
            errors.append(f"{label}: /Note structure element requires an /ID.")

    if not full:
        return

    if resolved in _HEADING_LEVELS:
        level = _HEADING_LEVELS[resolved]
        ctx["numbered_headings"] = True
        prev = ctx.get("last_heading_level", 0)
        if prev and level > prev + 1:
            warnings.append(
                f"{label}: heading levels should not skip (H{prev} to H{level})."
            )
        ctx["last_heading_level"] = level
    elif resolved == "H":
        ctx["unnumbered_headings"] = True

    # List / table containment (advisory: only fires on observed anomalies).
    if resolved == "LI" and parent_type != "L":
        warnings.append(f"{label}: /LI should be a child of /L.")
    elif resolved in ("Lbl", "LBody") and parent_type != "LI":
        warnings.append(f"{label}: /{resolved} should be a child of /LI.")
    elif resolved == "TR" and parent_type not in (
        "Table", "THead", "TBody", "TFoot"
    ):
        warnings.append(f"{label}: /TR should be a child of /Table or a row group.")
    elif resolved in ("TH", "TD") and parent_type != "TR":
        warnings.append(f"{label}: /{resolved} should be a child of /TR.")


def _walk_struct_kids(
    pdf: Any,
    k: Any,
    role_map: dict,
    ctx: dict,
    parent_type: str | None,
    visited: set[int],
    depth: int,
    errors: list[str],
    warnings: list[str],
) -> None:
    if depth > _MAX_STRUCT_DEPTH:
        return
    k = pdf._resolve(k)
    if isinstance(k, PdfArray):
        for item in k.items:
            _walk_struct_kids(
                pdf, item, role_map, ctx, parent_type, visited, depth,
                errors, warnings,
            )
        return
    if not isinstance(k, PdfDictionary):
        # Integer MCID leaf, or unresolved — nothing to validate here.
        return
    s = _name(pdf, k, "S")
    if s is None:
        # Marked-content (/MCR) or object (/OBJR) reference: not an element.
        return
    if id(k) in visited:
        return
    visited.add(id(k))
    _check_struct_element(
        pdf, k, s, role_map, ctx, parent_type, errors, warnings
    )
    _walk_struct_kids(
        pdf, k.get(PdfName("K")), role_map, ctx,
        _resolved_struct_type(s, role_map), visited, depth + 1,
        errors, warnings,
    )


def _walk_struct_for(
    pdf: Any, label: str, full: bool
) -> tuple[list[str], list[str]]:
    """Walk the structure tree, returning ``(errors, warnings)``.

    ``full`` enables the PDF/UA-only advisory checks (heading order, list/table
    containment, ParentTree).  The error-level checks (non-standard types,
    Figure/Formula alt text, Note /ID) apply to both PDF/UA and PDF/A level A.
    """
    errors: list[str] = []
    warnings: list[str] = []
    root = catalog(pdf)
    if root is None:
        return errors, warnings
    struct_root = _get_dict(pdf, root.get(PdfName("StructTreeRoot")))
    if struct_root is None:
        return errors, warnings

    top = pdf._resolve(struct_root.get(PdfName("K")))
    has_content = isinstance(top, PdfDictionary) or (
        isinstance(top, PdfArray) and len(top.items) > 0
    )
    if full and has_content and struct_root.get(PdfName("ParentTree")) is None:
        errors.append(
            f"{label}: /StructTreeRoot with content requires a /ParentTree for "
            "marked-content mapping."
        )

    role_map = _build_role_map(pdf, struct_root)
    ctx = {"label": label, "full": full}
    try:
        _walk_struct_kids(
            pdf, struct_root.get(PdfName("K")), role_map, ctx, None, set(), 0,
            errors, warnings,
        )
    except PdfResourceLimitException:
        raise
    except PDF_OPERATION_ERRORS:
        pass
    if full and ctx.get("numbered_headings") and ctx.get("unnumbered_headings"):
        warnings.append(
            f"{label}: do not mix numbered (H1-H6) and unnumbered (H) headings."
        )
    return errors, warnings


def _font_is_embedded(pdf: Any, font: PdfDictionary) -> bool:
    """Return ``True`` when *font* (or its CIDFont descendant) embeds a program."""
    descriptor = _get_dict(pdf, font.get(PdfName("FontDescriptor")))
    if descriptor is None:
        descendants = pdf._resolve(font.get(PdfName("DescendantFonts")))
        if isinstance(descendants, PdfArray) and descendants.items:
            cid = _get_dict(pdf, descendants.items[0])
            if cid is not None:
                descriptor = _get_dict(pdf, cid.get(PdfName("FontDescriptor")))
    if descriptor is None:
        return False
    return any(
        PdfName(k) in descriptor for k in ("FontFile", "FontFile2", "FontFile3")
    )


def _check_fonts_embedded(pdf: Any, errors: list[str]) -> None:
    """PDF/UA requires every font (including the standard 14) to be embedded."""
    for i in range(len(pdf.pages)):
        page = pdf._get_page_dict(i)
        if not isinstance(page, PdfDictionary):
            continue
        resources = _get_dict(pdf, page.get(PdfName("Resources")))
        if resources is None:
            continue
        fonts = _get_dict(pdf, resources.get(PdfName("Font")))
        if fonts is None:
            continue
        for ref in fonts.mapping.values():
            font = _get_dict(pdf, ref)
            if font is None or _name(pdf, font, "Subtype") == "Type3":
                continue  # Type 3 glyphs are self-contained content streams.
            if not _font_is_embedded(pdf, font):
                base = pdf._get_name(font.get(PdfName("BaseFont"))) or "a font"
                errors.append(
                    "PDF/UA requires all fonts to be embedded; "
                    f"{base} on page {i + 1} is not embedded."
                )


def pdfua_structure(pdf: Any) -> tuple[list[str], list[str]]:
    """Return ``(errors, warnings)`` for the PDF/UA structure-tree checks."""
    return _walk_struct_for(pdf, "PDF/UA", full=True)


# Markup annotation subtypes expected to carry a /Contents text alternative.
_MARKUP_ANNOT_SUBTYPES = frozenset(
    {
        "Link", "Text", "FreeText", "Line", "Square", "Circle", "Polygon",
        "PolyLine", "Highlight", "Underline", "Squiggly", "StrikeOut", "Stamp",
        "Ink", "FileAttachment", "Redact",
    }
)


def pdfua_pages(pdf: Any) -> tuple[list[str], list[str]]:
    """Return ``(errors, warnings)`` for the PDF/UA page/annotation checks."""
    errors: list[str] = []
    warnings: list[str] = []
    root = catalog(pdf)
    if root is None:
        return errors, warnings

    mark_info = _get_dict(pdf, root.get(PdfName("MarkInfo")))
    if mark_info is not None:
        suspects = pdf._resolve(mark_info.get(PdfName("Suspects")))
        if isinstance(suspects, PdfBoolean) and suspects.value:
            errors.append("PDF/UA prohibits MarkInfo /Suspects true.")

    has_struct = _get_dict(pdf, root.get(PdfName("StructTreeRoot"))) is not None
    try:
        for i in range(len(pdf.pages)):
            page = pdf._get_page_dict(i)
            if not isinstance(page, PdfDictionary):
                continue
            annots = pdf._resolve(page.get(PdfName("Annots")))
            annot_items = annots.items if isinstance(annots, PdfArray) else []
            if annot_items and _name(pdf, page, "Tabs") != "S":
                errors.append(
                    f"PDF/UA requires page {i + 1} with annotations to declare "
                    "/Tabs /S (structure tab order)."
                )
            for ref in annot_items:
                annot = pdf._resolve(ref)
                if not isinstance(annot, PdfDictionary):
                    continue
                subtype = _name(pdf, annot, "Subtype")
                if subtype == "Popup":
                    continue
                flags_obj = pdf._resolve(annot.get(PdfName("F")))
                flags = int(flags_obj.value) if isinstance(flags_obj, PdfNumber) else 0
                if flags & (ANNOT_FLAG_HIDDEN | ANNOT_FLAG_NOVIEW):
                    continue
                if has_struct and annot.get(PdfName("StructParent")) is None:
                    warnings.append(
                        f"PDF/UA: annotation on page {i + 1} "
                        f"({subtype or 'annotation'}) is not in the structure "
                        "tree (no /StructParent)."
                    )
                if subtype in _MARKUP_ANNOT_SUBTYPES:
                    contents = pdf._resolve(annot.get(PdfName("Contents")))
                    if not (isinstance(contents, PdfString) and contents.value):
                        warnings.append(
                            f"PDF/UA: {subtype} annotation on page {i + 1} should "
                            "have a /Contents text alternative."
                        )
        _check_fonts_embedded(pdf, errors)
    except PdfResourceLimitException:
        raise
    except PDF_OPERATION_ERRORS:
        pass
    return errors, warnings


def _parent_tree_map(pdf: Any, struct_root: PdfDictionary) -> dict:
    """Return ``{StructParents key: parent PdfArray}`` from the ``/ParentTree``."""
    parent_tree = _get_dict(pdf, struct_root.get(PdfName("ParentTree")))
    if parent_tree is None:
        return {}
    mapping: dict = {}
    stack = [(parent_tree, 0)]
    visited: set[int] = set()
    while stack:
        node, depth = stack.pop()
        if depth > _MAX_STRUCT_DEPTH or id(node) in visited:
            continue
        visited.add(id(node))
        nums = pdf._resolve(node.get(PdfName("Nums")))
        if isinstance(nums, PdfArray):
            for index in range(0, len(nums.items) - 1, 2):
                key = pdf._resolve(nums.items[index])
                array = pdf._resolve(nums.items[index + 1])
                if isinstance(key, PdfNumber) and isinstance(array, PdfArray):
                    mapping[int(key.value)] = array
        kids = pdf._resolve(node.get(PdfName("Kids")))
        if isinstance(kids, PdfArray):
            for raw_child in reversed(kids.items):
                child = _get_dict(pdf, raw_child)
                if child is not None:
                    stack.append((child, depth + 1))
    return mapping


def _named_property_mcids(pdf: Any, page: PdfDictionary) -> dict[str, int]:
    """Resolve page ``/Properties`` resource names to marked-content IDs."""
    resources = None
    resource_resolver = getattr(pdf, "_resolve_resources_cos", None)
    if callable(resource_resolver):
        resources = resource_resolver(page)
    if not isinstance(resources, PdfDictionary):
        current: Any = page
        visited: set[int] = set()
        for _depth in range(_MAX_RESOURCE_DEPTH):
            if not isinstance(current, PdfDictionary) or id(current) in visited:
                break
            visited.add(id(current))
            resources = _get_dict(pdf, current.get(PdfName("Resources")))
            if resources is not None:
                break
            current = pdf._resolve(current.get(PdfName("Parent")))
    if not isinstance(resources, PdfDictionary):
        return {}
    properties = _get_dict(pdf, resources.get(PdfName("Properties")))
    if properties is None:
        return {}
    result: dict[str, int] = {}
    for name, raw_property in properties.mapping.items():
        if not isinstance(name, PdfName):
            continue
        property_list = _get_dict(pdf, raw_property)
        if property_list is None:
            continue
        mcid = pdf._resolve(property_list.get(PdfName("MCID")))
        if isinstance(mcid, PdfNumber) and mcid.value >= 0:
            result[name.name.lstrip("/")] = int(mcid.value)
    return result


def pdfua_mcid_coverage(pdf: Any) -> tuple[list[str], list[str]]:
    """Advisory MCID coverage checks between page content and the structure tree.

    For every page carrying a ``/StructParents`` key, each marked-content
    ``/MCID`` in the content must map to a structure element through the
    ``/ParentTree`` (uncovered marked content is not reachable from the tree),
    and each ``/ParentTree`` slot that points at a structure element should
    correspond to marked content that actually exists (a dangling reference).
    All findings are warnings -- this is a heuristic, not certification.
    """
    from .auto_tag import find_mcids

    errors: list[str] = []
    warnings: list[str] = []
    root = catalog(pdf)
    if root is None:
        return errors, warnings
    struct_root = _get_dict(pdf, root.get(PdfName("StructTreeRoot")))
    if struct_root is None:
        return errors, warnings
    key_to_arr = _parent_tree_map(pdf, struct_root)
    if not key_to_arr:
        return errors, warnings

    try:
        for i in range(len(pdf.pages)):
            page = pdf._get_page_dict(i)
            if not isinstance(page, PdfDictionary):
                continue
            key_obj = pdf._resolve(page.get(PdfName("StructParents")))
            if not isinstance(key_obj, PdfNumber):
                continue
            arr = key_to_arr.get(int(key_obj.value))
            if arr is None:
                warnings.append(
                    f"PDF/UA: page {i + 1} declares /StructParents "
                    f"{int(key_obj.value)} with no matching /ParentTree entry."
                )
                continue
            try:
                used = find_mcids(
                    pdf.get_page_content(i),
                    named_properties=_named_property_mcids(pdf, page),
                    limits=pdf._load_limits,
                    budget=pdf._load_budget,
                )
            except PdfResourceLimitException:
                raise
            except PDF_OPERATION_ERRORS:
                continue
            length = len(arr.items)
            for mcid in sorted(used):
                parent = pdf._resolve(arr.items[mcid]) if 0 <= mcid < length else None
                if not isinstance(parent, PdfDictionary):
                    warnings.append(
                        f"PDF/UA: marked-content MCID {mcid} on page {i + 1} is "
                        "not mapped to a structure element in the /ParentTree."
                    )
            for mcid in range(length):
                if mcid in used:
                    continue
                if isinstance(pdf._resolve(arr.items[mcid]), PdfDictionary):
                    warnings.append(
                        f"PDF/UA: /ParentTree maps MCID {mcid} on page {i + 1} "
                        "but no marked content uses it."
                    )
    except PdfResourceLimitException:
        raise
    except PDF_OPERATION_ERRORS:
        pass
    return errors, warnings


# ---------------------------------------------------------------------------
# PDF/UA — catalog
# ---------------------------------------------------------------------------
#: The PDF 2.0 standard structure namespace PDF/UA-2 draws its element types
#: from (ISO 32000-2 14.7.4; ISO 14289-2 8.2).
PDF2_STRUCTURE_NS = "http://iso.org/pdf2/ssn"

#: The revision year each PDF/UA part identifies itself with in XMP.
_PDFUA_REVISIONS = {1: None, 2: "2024"}

#: Ceiling on a structure-tree walk, so a malformed tree cannot turn validation
#: into an unbounded traversal.
_MAX_STRUCT_ELEMENTS = 100_000


def pdfua_extended(pdf: Any, part: int = 1) -> tuple[list[str], list[str]]:
    """Return ``(errors, warnings)`` for the extended PDF/UA catalog checks.

    *part* selects ISO 14289-1 or -2. Part 2 is defined on PDF 2.0 and adds two
    identification requirements of its own: a ``pdfuaid:rev`` year, and
    structure element types drawn from the PDF 2.0 standard structure namespace
    rather than the unqualified names part 1 used.
    """
    errors: list[str] = []
    warnings: list[str] = []
    root = catalog(pdf)
    if root is None:
        return errors, warnings

    try:
        _check_pdfua_version(pdf, part, errors)
        if part >= 2:
            _check_structure_namespace(pdf, root, errors)
            _check_single_document_root(pdf, root, errors)
        viewer = _get_dict(pdf, root.get(PdfName("ViewerPreferences")))
        display = pdf._resolve(viewer.get(PdfName("DisplayDocTitle"))) if viewer else None
        if not (isinstance(display, PdfBoolean) and display.value):
            errors.append(
                "PDF/UA requires ViewerPreferences /DisplayDocTitle true."
            )

        if not _has_document_title(pdf, root):
            errors.append(
                "PDF/UA requires a document title (Info /Title or XMP dc:title)."
            )

        metadata = pdf._resolve(root.get(PdfName("Metadata")))
        if not isinstance(metadata, PdfStream):
            errors.append(
                "PDF/UA requires an XMP metadata stream declaring pdfuaid:part."
            )
        else:
            _check_pdfua_identifier(metadata.content, part, errors)
    except PdfResourceLimitException:
        raise
    except PDF_OPERATION_ERRORS:
        pass

    # Structure-tree semantics and page/annotation rules.
    struct_errors, struct_warnings = pdfua_structure(pdf)
    errors.extend(struct_errors)
    warnings.extend(struct_warnings)
    page_errors, page_warnings = pdfua_pages(pdf)
    errors.extend(page_errors)
    warnings.extend(page_warnings)
    mcid_errors, mcid_warnings = pdfua_mcid_coverage(pdf)
    errors.extend(mcid_errors)
    warnings.extend(mcid_warnings)
    artifact_errors, artifact_warnings = pdfua_artifact_coverage(pdf)
    errors.extend(artifact_errors)
    warnings.extend(artifact_warnings)

    return errors, warnings


def _check_single_document_root(pdf: Any, root: PdfDictionary, errors: list[str]) -> None:
    """ISO 14289-2 8.2.5.2: one ``Document`` element, and nothing else, at the root.

    By way of ISO 32000-2 Annex L and ISO/TS 32005. veraPDF states it three
    ways -- exactly one ``Document``, no ``Hn`` under the root, no ``P`` under
    the root -- and this check used to pass a document whose authored headings
    and paragraphs hung straight off ``/StructTreeRoot``, which is what the
    converter produced. Part 1 has no such rule.
    """
    struct_root = _get_dict(pdf, root.get(PdfName("StructTreeRoot")))
    if struct_root is None:
        return  # the missing-StructTreeRoot error is raised elsewhere
    kids = pdf._resolve(struct_root.get(PdfName("K")))
    if isinstance(kids, PdfArray):
        items = list(kids.items)
    elif kids is None:
        items = []
    else:
        items = [struct_root.get(PdfName("K"))]

    role_map = _build_role_map(pdf, struct_root)
    types = []
    for item in items:
        element = _get_dict(pdf, item)
        name = _name(pdf, element, "S") if element is not None else None
        types.append(_resolved_struct_type(name or "", role_map))

    if types == ["Document"]:
        return
    if not types:
        errors.append(
            "PDF/UA-2 requires the structure tree root to contain a single "
            "Document element; it has none (ISO 14289-2 8.2.5.2)."
        )
        return
    shown = ", ".join(types[:5])
    errors.append(
        "PDF/UA-2 requires the structure tree root to contain a single "
        f"Document element as its only child; it has {len(types)} "
        f"({shown}) (ISO 14289-2 8.2.5.2)."
    )


def _check_pdfua_version(pdf: Any, part: int, errors: list[str]) -> None:
    """PDF/UA-2 is defined on PDF 2.0; part 1 puts no ceiling on the version."""
    if part < 2:
        return
    version = _parse_pdf_version(getattr(pdf, "pdf_version", "") or "")
    if version is not None and abs(version - 2.0) > 1e-9:
        errors.append(
            "PDF/UA-2 requires PDF version 2.0; the header declares "
            f"{pdf.pdf_version}."
        )


def _check_pdfua_identifier(xmp: bytes, part: int, errors: list[str]) -> None:
    """Check the ``pdfuaid`` part (and, from part 2, revision) in an XMP packet."""
    text = xmp.decode("utf-8", errors="replace")
    declared = _xmp_value(text, "pdfuaid:part")
    if declared is None:
        errors.append(
            f"PDF/UA XMP metadata must declare pdfuaid:part (= {part})."
        )
    elif declared != str(part):
        errors.append(
            f"XMP pdfuaid:part is {declared!r} but validation requires "
            f"part {part}."
        )
    revision = _PDFUA_REVISIONS.get(part)
    if revision is None:
        return
    declared_rev = _xmp_value(text, "pdfuaid:rev")
    if declared_rev != revision:
        errors.append(
            f"PDF/UA-{part} requires XMP pdfuaid:rev = {revision}; got "
            f"{declared_rev!r}."
        )


def _xmp_value(text: str, qualified_name: str) -> str | None:
    """Read an XMP property written either as an element or as an attribute.

    The element form tolerates attributes on the opening tag, because a
    namespace may be bound there rather than on an ancestor:
    ``<pdfxid:GTS_PDFXVersion xmlns:pdfxid="...">PDF/X-4</...>`` is how pikepdf
    writes a property in a namespace nothing else in the packet uses, and
    reading only ``name>`` reported such a packet as not declaring the property
    at all.
    """
    pattern = re.escape(qualified_name)
    match = re.search(rf"{pattern}(?:\s+[^<>]*?)?\s*>([^<]+)</", text, re.IGNORECASE)
    if match is None:
        match = re.search(
            rf"{pattern}\s*=\s*[\"\']([^\"\']+)[\"\']", text, re.IGNORECASE
        )
    return match.group(1).strip() if match else None


def _check_structure_namespace(
    pdf: Any, root: PdfDictionary, errors: list[str]
) -> None:
    """PDF/UA-2 structure types come from the PDF 2.0 standard namespace."""
    struct_root = _get_dict(pdf, root.get(PdfName("StructTreeRoot")))
    if struct_root is None:
        return  # the missing /StructTreeRoot is already reported on its own
    namespaces = pdf._resolve(struct_root.get(PdfName("Namespaces")))
    declared = []
    if isinstance(namespaces, PdfArray):
        for item in namespaces.items:
            entry = _get_dict(pdf, item)
            if entry is None:
                continue
            uri = pdf._resolve(entry.get(PdfName("NS")))
            if isinstance(uri, PdfString):
                declared.append(decode_pdf_text_string(uri))
    if PDF2_STRUCTURE_NS not in declared:
        errors.append(
            "PDF/UA-2 requires the StructTreeRoot to declare the PDF 2.0 "
            f"standard structure namespace ({PDF2_STRUCTURE_NS}) in "
            "/Namespaces."
        )
        return  # nothing to belong to yet; the elements are a later problem
    _check_elements_are_namespaced(pdf, struct_root, errors)


def _check_elements_are_namespaced(
    pdf: Any, struct_root: PdfDictionary, errors: list[str]
) -> None:
    """Every structure element must say which namespace its type comes from.

    Declaring the namespace on the root says it exists; an element's ``/NS`` is
    what says its type is drawn from it. A tree carried over from part 1 has
    the declaration and none of the elements, and its types are still the
    unqualified names ISO 14289-2 replaced.
    """
    plain: list[str] = []
    seen: set[int] = set()
    stack = [struct_root.get(PdfName("K"))]
    while stack and len(seen) <= _MAX_STRUCT_ELEMENTS:
        node = pdf._resolve(stack.pop())
        if isinstance(node, PdfArray):
            stack.extend(node.items)
            continue
        if not isinstance(node, PdfDictionary) or id(node) in seen:
            continue
        seen.add(id(node))
        kind = pdf._get_name(node.get(PdfName("S")))
        if kind is None:
            continue
        if node.get(PdfName("NS")) is None:
            plain.append(kind)
        kids = node.get(PdfName("K"))
        if kids is not None:
            stack.append(kids)
    if plain:
        shown = ", ".join(sorted(set(plain))[:5])
        errors.append(
            f"PDF/UA-2 requires every structure element to name its namespace "
            f"(/NS); {len(plain)} do not (e.g. {shown})."
        )


def _has_document_title(pdf: Any, root: PdfDictionary) -> bool:
    info = _get_dict(pdf, pdf._cos_doc.trailer.get(PdfName("Info")))
    if info is not None:
        title = pdf._resolve(info.get(PdfName("Title")))
        if isinstance(title, PdfString) and title.value:
            return True
    metadata = pdf._resolve(root.get(PdfName("Metadata")))
    if isinstance(metadata, PdfStream):
        if "dc:title" in metadata.content.decode("utf-8", errors="replace"):
            return True
    return False


# ---------------------------------------------------------------------------
# PDF/X (ISO 15930)
# ---------------------------------------------------------------------------
#
# PDF/X is a family of print-exchange profiles rather than one standard, and the
# parts differ in what they *allow* rather than in how they are structured: a
# PDF/X-1a file is CMYK-or-spot with no live transparency, a PDF/X-3 file may
# carry colour-managed (device-independent) colour under the same output intent,
# and a PDF/X-4 file may additionally carry live transparency and optional
# content. The rules each part states are therefore held as data in
# :data:`PDFX_STANDARDS` and the checks below read them, so that adding a part
# is a table entry rather than another branch in every function.
#
# Two details of ISO 15930 are easy to get wrong and are worth stating here.
# The first: ISO 15930-4:2003 clause 5 says in as many words that "neither the
# version number in the header of a PDF file, nor the value of the Version key
# in the Catalog of a PDF file shall be used in determining whether a file is in
# accordance with this part" -- so unlike PDF/A, which pins the header to the
# part, **PDF/X validation here never looks at the header version**. The
# converter still raises it to the version the part is written against, because
# the features a part permits are those of that PDF version, but a file with an
# older header is not thereby non-conformant.
# The second: the identification keys are not the same shape across the family.
# Parts 1 to 3 identify the file in the **information dictionary**
# (``/GTS_PDFXVersion``, plus ``/GTS_PDFXConformance`` for part 1, the two
# mirrored into XMP under the ``pdfx`` schema), while PDF/X-4 identifies it in
# **XMP only**, under the separate PDF/X ID schema (``pdfxid``), and carries no
# year in the value. ISO 15930-1:2001 is the odd one out even within parts 1-3:
# its version string omits the conformance letter (``PDF/X-1:2001`` with
# ``PDF/X-1a:2001`` as the conformance), which the 2003 part quotes verbatim in
# its clause 5 when it says which older files a reader must accept.


@dataclass(frozen=True)
class PdfXStandardRules:
    """What one PDF/X conformance level requires, as data.

    ``name`` is the canonical identifier (``"PDF/X-4"``) and the value the
    public API takes and reports.
    """

    name: str
    #: The conformance family: 1 (PDF/X-1a), 3 (PDF/X-3) or 4 (PDF/X-4). What a
    #: file may contain follows from this, not from the ISO part number.
    part: int
    #: The part of ISO 15930 that defines this level, which is *not* the family
    #: number: PDF/X-1a:2001 is part 1 but PDF/X-1a:2003 is part 4, PDF/X-3:2002
    #: is part 3 but PDF/X-3:2003 is part 6, and PDF/X-4 is part 7. Messages
    #: cite this.
    iso_part: int
    #: ``/GTS_PDFXVersion`` for the information dictionary, or ``None`` when the
    #: part identifies itself in XMP alone (part 4 on).
    info_version: str | None
    #: ``/GTS_PDFXConformance``, which only parts 1 and 2 carry.
    info_conformance: str | None
    #: XMP schema prefix the identification lives under: ``pdfx`` up to part 3,
    #: ``pdfxid`` (the PDF/X ID schema) from part 4.
    xmp_prefix: str
    xmp_version: str
    #: The PDF version the part is written against. Used by the converter only
    #: (see the note above): validation does not read the header.
    base_pdf_version: str
    allows_transparency: bool
    #: Device-independent (ICCBased, CalRGB, CalGray, Lab) and RGB colour.
    #: Part 1 is CMYK, grey and spot colour only.
    allows_device_independent_colour: bool
    allows_optional_content: bool
    allows_jpx: bool
    #: Topic -> clause number, for the parts whose clause structure this
    #: library has read. It is deliberately **empty** for the parts it has not:
    #: a citation is only useful if it is right, so a message about one of those
    #: names the standard without a clause rather than borrowing a sibling
    #: part's numbering. See :func:`pdfx_cite`.
    clauses: dict[str, str]


#: Clause numbers of ISO 15930-4:2003 (PDF/X-1a:2003), from its table of
#: contents. ISO 15930-1 (PDF/X-1a:2001), -3 and -6 (PDF/X-3) number their
#: clauses differently and are left without a map.
_ISO15930_4_CLAUSES = {
    "colour": "6.2",
    "fonts": "6.3",
    "files": "6.4",
    "compression": "6.5",
    "trapping": "6.6",
    "identification": "6.7",
    "boxes": "6.8",
    "extgstate": "6.9",
    "postscript": "6.10",
    "encryption": "6.11",
    "annotations": "6.13",
    "actions": "6.14",
    "transparency": "6.16",
}

#: Clause numbers of ISO 15930-7:2010 (PDF/X-4), from its table of contents.
_ISO15930_7_CLAUSES = {
    "colour": "6.4",
    "fonts": "6.5",
    "files": "6.7",
    "compression": "6.8",
    "trapping": "6.9",
    "identification": "6.11",
    "boxes": "6.12",
    "extgstate": "6.13",
    "postscript": "6.14",
    "encryption": "6.15",
    "images": "6.16",
    "annotations": "6.17",
    "actions": "6.18",
    "transparency": "6.20",
    "optional_content": "6.24",
    "xfa": "6.26",
    "jpeg2000": "6.27",
}


#: The PDF/X conformance levels this library knows, keyed by canonical name.
#: ISO 15930-2 (PDF/X-2) and the external-profile variants (PDF/X-4p, PDF/X-5,
#: PDF/X-6) are deliberately absent -- see ``supported-features.md``.
PDFX_STANDARDS: dict[str, PdfXStandardRules] = {
    "PDF/X-1a:2001": PdfXStandardRules(
        name="PDF/X-1a:2001",
        part=1,
        iso_part=1,
        info_version="PDF/X-1:2001",
        info_conformance="PDF/X-1a:2001",
        xmp_prefix="pdfx",
        xmp_version="PDF/X-1:2001",
        base_pdf_version="1.3",
        allows_transparency=False,
        allows_device_independent_colour=False,
        allows_optional_content=False,
        allows_jpx=False,
        clauses={},
    ),
    "PDF/X-1a:2003": PdfXStandardRules(
        name="PDF/X-1a:2003",
        part=1,
        iso_part=4,
        info_version="PDF/X-1a:2003",
        info_conformance="PDF/X-1a:2003",
        xmp_prefix="pdfx",
        xmp_version="PDF/X-1a:2003",
        base_pdf_version="1.4",
        allows_transparency=False,
        allows_device_independent_colour=False,
        allows_optional_content=False,
        allows_jpx=False,
        clauses=_ISO15930_4_CLAUSES,
    ),
    "PDF/X-3:2002": PdfXStandardRules(
        name="PDF/X-3:2002",
        part=3,
        iso_part=3,
        info_version="PDF/X-3:2002",
        info_conformance=None,
        xmp_prefix="pdfx",
        xmp_version="PDF/X-3:2002",
        base_pdf_version="1.3",
        allows_transparency=False,
        allows_device_independent_colour=True,
        allows_optional_content=False,
        allows_jpx=False,
        clauses={},
    ),
    "PDF/X-3:2003": PdfXStandardRules(
        name="PDF/X-3:2003",
        part=3,
        iso_part=6,
        info_version="PDF/X-3:2003",
        info_conformance=None,
        xmp_prefix="pdfx",
        xmp_version="PDF/X-3:2003",
        base_pdf_version="1.4",
        allows_transparency=False,
        allows_device_independent_colour=True,
        allows_optional_content=False,
        allows_jpx=False,
        clauses={},
    ),
    "PDF/X-4": PdfXStandardRules(
        name="PDF/X-4",
        part=4,
        iso_part=7,
        info_version=None,
        info_conformance=None,
        xmp_prefix="pdfxid",
        xmp_version="PDF/X-4",
        base_pdf_version="1.6",
        allows_transparency=True,
        allows_device_independent_colour=True,
        allows_optional_content=True,
        allows_jpx=True,
        clauses=_ISO15930_7_CLAUSES,
    ),
}

def pdfx_cite(rules: PdfXStandardRules, topic: str) -> str:
    """The standard, and the clause, to cite for *topic* under *rules*.

    ``"ISO 15930-7 6.12"`` where the clause is known, ``"ISO 15930-3"`` where it
    is not. Citing a clause number taken from another part of the same family
    would read as authority this library does not have.
    """
    clause = rules.clauses.get(topic)
    if clause:
        return f"ISO 15930-{rules.iso_part} {clause}"
    return f"ISO 15930-{rules.iso_part}"


#: ``/S`` value of the PDF/X output intent (ISO 15930, carried from PDF 1.3).
PDFX_OUTPUT_INTENT_SUBTYPE = "GTS_PDFX"

#: Spelling variants the public API accepts for each canonical standard name.
#: A bare part without a year resolves to the newest edition of that part,
#: which is what a caller asking for "PDF/X-1a" means.
_PDFX_ALIASES: dict[str, str] = {
    "x1a": "PDF/X-1a:2003",
    "x1a2001": "PDF/X-1a:2001",
    "x1a2003": "PDF/X-1a:2003",
    "x1": "PDF/X-1a:2003",
    "x3": "PDF/X-3:2003",
    "x32002": "PDF/X-3:2002",
    "x32003": "PDF/X-3:2003",
    "x4": "PDF/X-4",
}


def normalize_pdfx_standard(value: Any) -> str:
    """Resolve *value* to a key of :data:`PDFX_STANDARDS`, or raise.

    Accepts the canonical names (``"PDF/X-4"``), the same without the ``PDF/``
    prefix, and spellings without punctuation or case (``"x-1a"``, ``"X1a"``,
    ``"pdf/x-3:2002"``). A part named without a year resolves to its newest
    edition. An unknown name raises rather than falling back to a standard the
    caller did not ask for -- which would write a file claiming conformance to
    something nobody chose.
    """
    text = str(getattr(value, "value", value)).strip()
    if text in PDFX_STANDARDS:
        return text
    key = re.sub(r"[^a-z0-9]", "", text.lower())
    if key.startswith("pdf"):
        key = key[3:]
    resolved = _PDFX_ALIASES.get(key)
    if resolved is None:
        raise ValueError(
            f"Unknown PDF/X standard {text!r}; expected one of "
            + ", ".join(sorted(PDFX_STANDARDS))
        )
    return resolved


def pdfx_extended(pdf: Any, rules: PdfXStandardRules) -> tuple[list[str], list[str]]:
    """Return ``(errors, warnings)`` for the structural PDF/X checks.

    *rules* is an entry of :data:`PDFX_STANDARDS`. As with
    :func:`pdfa_extended`, nothing here mutates the document and a malformed
    object never escapes as an exception.
    """
    errors: list[str] = []
    warnings: list[str] = []
    if pdf._cos_doc is None:
        return errors, warnings

    try:
        _check_trailer_id(pdf, errors)
        _check_pdfx_identification(pdf, rules, errors, warnings)
        _check_pdfx_output_intent(pdf, rules, errors, warnings)
        _check_pdfx_trapped(pdf, rules, errors)
        _check_pdfx_catalog(pdf, rules, errors)
        _check_pdfx_filters(pdf, rules, errors)
        for i in range(len(pdf.pages)):
            page = pdf._get_page_dict(i)
            if not isinstance(page, PdfDictionary):
                continue
            _check_pdfx_boxes(pdf, page, i, rules, errors)
            _check_pdfx_page_group(pdf, page, i, rules, errors)
            _check_pdfx_annotations(pdf, page, i, rules, errors, warnings)
            if PdfName("AA") in page:
                errors.append(
                    f"PDF/X prohibits page additional actions (/AA) on page {i + 1} "
                    f"({pdfx_cite(rules, 'actions')})."
                )
            resources = _get_dict(pdf, page.get(PdfName("Resources")))
            if resources is not None:
                _check_pdfx_resources(pdf, resources, i, rules, errors, set(), 0)
    except PdfResourceLimitException:
        raise
    except PDF_OPERATION_ERRORS:
        pass

    return errors, warnings


def _info_dict(pdf: Any) -> PdfDictionary | None:
    return _get_dict(pdf, pdf._cos_doc.trailer.get(PdfName("Info")))


def _info_value(pdf: Any, key: str) -> str | None:
    """The ``/Info`` entry *key* as the **next save** would write it.

    ``pdf.metadata`` is the view a save writes ``/Info`` from: it is filled from
    the dictionary on load, and an entry set or removed through it has not
    reached the object graph yet. Reading the graph alone would report an entry
    the file is about to carry as missing -- which is exactly what a check run
    straight after a conversion does -- so the pending view is consulted first.
    The graph is still the fallback, for an entry whose value has no text form
    (a ``/GTS_PDFXVersion`` some producer wrote as a name, say), which the view
    does not carry.
    """
    metadata = getattr(pdf, "metadata", None)
    view_is_authoritative = isinstance(metadata, dict)
    if view_is_authoritative and key in metadata:
        value = metadata[key]
        return None if value is None else str(value).strip()
    info = _info_dict(pdf)
    if info is None:
        return None
    value = pdf._resolve(info.get(PdfName(key)))
    if isinstance(value, PdfString):
        # The view holds every text-valued entry, so a string that is in the
        # dictionary and not in the view is one the next save removes.
        return None if view_is_authoritative else decode_pdf_text_string(value).strip()
    if isinstance(value, PdfName):
        return value.name.lstrip("/")
    return None


def _document_xmp(pdf: Any) -> str | None:
    """The catalog's XMP packet as text, or ``None`` when there is none."""
    root = catalog(pdf)
    if root is None:
        return None
    metadata = pdf._resolve(root.get(PdfName("Metadata")))
    if not isinstance(metadata, PdfStream):
        return None
    return metadata.content.decode("utf-8", errors="replace")


def _check_pdfx_identification(
    pdf: Any, rules: PdfXStandardRules, errors: list[str], warnings: list[str]
) -> None:
    """The file has to say which PDF/X level it claims, and say it once.

    Where the claim lives depends on the part (see the note above the standards
    table): the information dictionary for parts 1 to 3, the XMP PDF/X ID schema
    for part 4. Both places are *read* for every part, because a file that says
    the right thing in the wrong place is better reported as misplaced than as
    missing; only a value that names a different level is an error.
    """
    clause = pdfx_cite(rules, "identification")
    info_version = _info_value(pdf, "GTS_PDFXVersion")
    info_conformance = _info_value(pdf, "GTS_PDFXConformance")
    xmp = _document_xmp(pdf)
    xmp_version = None
    if xmp is not None:
        xmp_version = _xmp_value(xmp, f"{rules.xmp_prefix}:GTS_PDFXVersion")
        if xmp_version is None and rules.xmp_prefix == "pdfxid":
            # A producer that wrote the part-3 schema for a part-4 file states
            # the right value under the wrong prefix; naming that is more use
            # than reporting the identification as absent.
            misplaced = _xmp_value(xmp, "pdfx:GTS_PDFXVersion")
            if misplaced == rules.xmp_version:
                errors.append(
                    f"{rules.name} identification must use the PDF/X ID schema "
                    f"(pdfxid:GTS_PDFXVersion); the packet declares it as "
                    f"pdfx:GTS_PDFXVersion ({clause})."
                )
                return

    if rules.info_version is None:
        # Part 4 identifies itself in XMP alone.
        if xmp_version is None:
            errors.append(
                f"{rules.name} requires the XMP metadata to declare "
                f"{rules.xmp_prefix}:GTS_PDFXVersion = {rules.xmp_version} "
                f"({clause})."
            )
        elif xmp_version != rules.xmp_version:
            errors.append(
                f"XMP {rules.xmp_prefix}:GTS_PDFXVersion is {xmp_version!r} but "
                f"{rules.name} requires {rules.xmp_version!r} ({clause})."
            )
        if info_version is not None:
            # Sources disagree on whether the legacy key is merely redundant
            # here or a violation; preflight tools report it, so it is said.
            warnings.append(
                f"{rules.name} identifies itself in XMP; the information "
                f"dictionary also carries /GTS_PDFXVersion ({info_version!r}), "
                "which some preflight tools reject."
            )
        return

    if info_version is None:
        errors.append(
            f"{rules.name} requires /GTS_PDFXVersion = ({rules.info_version}) in "
            f"the document information dictionary ({clause})."
        )
    elif info_version != rules.info_version:
        errors.append(
            f"/GTS_PDFXVersion is {info_version!r} but {rules.name} requires "
            f"{rules.info_version!r} ({clause})."
        )
    if rules.info_conformance is not None:
        if info_conformance is None:
            errors.append(
                f"{rules.name} requires /GTS_PDFXConformance = "
                f"({rules.info_conformance}) in the document information "
                f"dictionary ({clause})."
            )
        elif info_conformance != rules.info_conformance:
            errors.append(
                f"/GTS_PDFXConformance is {info_conformance!r} but {rules.name} "
                f"requires {rules.info_conformance!r} ({clause})."
            )
    if xmp_version is not None and xmp_version != rules.xmp_version:
        errors.append(
            f"XMP {rules.xmp_prefix}:GTS_PDFXVersion is {xmp_version!r} but "
            f"{rules.name} requires {rules.xmp_version!r} ({clause})."
        )


def _pdfx_output_intents(pdf: Any) -> list[PdfDictionary]:
    """Every ``/OutputIntents`` entry whose ``/S`` is ``GTS_PDFX``."""
    root = catalog(pdf)
    if root is None:
        return []
    array = pdf._resolve(root.get(PdfName("OutputIntents")))
    if not isinstance(array, PdfArray):
        return []
    found = []
    for ref in array.items:
        intent = _get_dict(pdf, ref)
        if intent is None:
            continue
        if _name(pdf, intent, "S") == PDFX_OUTPUT_INTENT_SUBTYPE:
            found.append(intent)
    return found


def _check_pdfx_output_intent(
    pdf: Any, rules: PdfXStandardRules, errors: list[str], warnings: list[str]
) -> None:
    """One PDF/X output intent, naming the printing condition it was made for.

    The output intent is what makes a PDF/X file exchangeable: it says which
    characterized printing condition the colour in the file was prepared for.
    Part 1 permits no colour that is not already in the output device's own
    space, so its intent profile has to be that space -- a CMYK or grey one;
    parts 3 and 4 may carry colour-managed content and accept an RGB profile
    too. ``/DestOutputProfileRef`` (the externally referenced profile) is
    recognised, since a file carrying one is a PDF/X-4p rather than a file with
    no profile at all, and is reported as the different level it claims.
    """
    clause = pdfx_cite(rules, "identification")
    intents = _pdfx_output_intents(pdf)
    if not intents:
        errors.append(
            "PDF/X requires a /OutputIntents entry with /S /GTS_PDFX naming the "
            f"printing condition the file was prepared for ({clause})."
        )
        return
    if len(intents) > 1:
        errors.append(
            f"PDF/X permits one /GTS_PDFX output intent; the catalog has "
            f"{len(intents)} ({clause})."
        )
    intent = intents[0]
    identifier = pdf._resolve(intent.get(PdfName("OutputConditionIdentifier")))
    if not isinstance(identifier, PdfString) or not identifier.value:
        errors.append(
            "The PDF/X output intent requires /OutputConditionIdentifier, the "
            f"name of the characterized printing condition ({clause})."
        )
    profile = pdf._resolve(intent.get(PdfName("DestOutputProfile")))
    if not isinstance(profile, PdfStream):
        if PdfName("DestOutputProfileRef") in intent.mapping:
            errors.append(
                "The output intent references an external profile "
                "(/DestOutputProfileRef) rather than embedding one, which makes "
                f"the file PDF/X-4p rather than {rules.name} ({clause})."
            )
        else:
            errors.append(
                "The PDF/X output intent requires an embedded ICC profile "
                f"(/DestOutputProfile) ({clause})."
            )
        return

    space = _pdfx_profile_space(pdf, profile)
    if space is None:
        errors.append(
            "The output intent's /DestOutputProfile is not a readable ICC "
            f"profile ({clause})."
        )
        return
    permitted = ("CMYK", "Gray") if not rules.allows_device_independent_colour else (
        "CMYK",
        "Gray",
        "RGB",
    )
    if space not in permitted:
        errors.append(
            f"{rules.name} requires the output intent profile to be "
            f"{' or '.join(permitted)}; the embedded profile is {space} "
            f"({clause})."
        )


def _pdfx_profile_space(pdf: Any, profile: PdfStream) -> str | None:
    """``"CMYK"``/``"RGB"``/``"Gray"`` for an output intent profile stream."""
    try:
        data = pdf._decode_cos_stream(profile, None)
    except PdfResourceLimitException:
        raise
    except PDF_OPERATION_ERRORS:
        return None
    if len(data) < 128 or data[36:40] != b"acsp":
        return None
    return {b"GRAY": "Gray", b"RGB ": "RGB", b"CMYK": "CMYK"}.get(bytes(data[16:20]))


def _check_pdfx_trapped(
    pdf: Any, rules: PdfXStandardRules, errors: list[str]
) -> None:
    """``/Trapped`` has to state whether the file has been trapped.

    ISO 32000-1 14.3.3 allows ``/Unknown``, and it is the default; PDF/X does
    not, because a printer receiving the file has to know whether to trap it.
    """
    clause = pdfx_cite(rules, "trapping")
    value = _info_value(pdf, "Trapped")
    if value is None:
        errors.append(
            "PDF/X requires /Trapped in the document information dictionary, "
            f"with the value /True or /False ({clause})."
        )
    elif value not in ("True", "False"):
        errors.append(
            f"/Trapped is /{value}; PDF/X requires /True or /False ({clause})."
        )


def _check_pdfx_catalog(
    pdf: Any, rules: PdfXStandardRules, errors: list[str]
) -> None:
    """Catalog-level prohibitions: actions, scripts, forms, optional content."""
    root = catalog(pdf)
    if root is None:
        return
    actions = pdfx_cite(rules, "actions")
    if PdfName("AA") in root:
        errors.append(
            f"PDF/X prohibits document-level additional actions (/AA) ({actions})."
        )
    names = _get_dict(pdf, root.get(PdfName("Names")))
    if names is not None and PdfName("JavaScript") in names.mapping:
        errors.append(f"PDF/X prohibits document-level JavaScript ({actions}).")
    if PdfName("OpenAction") in root:
        _check_pdfx_action(pdf, root.get(PdfName("OpenAction")), None, rules, errors)
    acro = _get_dict(pdf, root.get(PdfName("AcroForm")))
    if acro is not None and PdfName("XFA") in acro.mapping:
        topic = "xfa" if "xfa" in rules.clauses else "actions"
        errors.append(
            f"PDF/X prohibits XFA forms (AcroForm /XFA) "
            f"({pdfx_cite(rules, topic)})."
        )
    if not rules.allows_optional_content and PdfName("OCProperties") in root:
        errors.append(
            f"{rules.name} prohibits optional content / layers (/OCProperties); "
            "it is a PDF 1.5 feature and the part is written against PDF "
            f"{rules.base_pdf_version}."
        )


def _check_pdfx_action(
    pdf: Any,
    action_ref: Any,
    page_index: int | None,
    rules: PdfXStandardRules,
    errors: list[str],
) -> None:
    """Flag prohibited action types, following ``/Next`` as PDF/A's check does."""
    where = "" if page_index is None else f" (page {page_index + 1})"
    clause = pdfx_cite(rules, "actions")
    queue = [pdf._resolve(action_ref)]
    seen: set[int] = set()
    while queue:
        action = queue.pop()
        if not isinstance(action, PdfDictionary) or id(action) in seen:
            continue
        seen.add(id(action))
        s = _name(pdf, action, "S")
        if s in _PROHIBITED_ACTION_TYPES:
            errors.append(f"PDF/X prohibits /{s} actions{where} ({clause}).")
        nxt = pdf._resolve(action.get(PdfName("Next")))
        if isinstance(nxt, PdfDictionary):
            queue.append(nxt)
        elif isinstance(nxt, PdfArray):
            queue.extend(pdf._resolve(x) for x in nxt.items)


def _box(pdf: Any, page: PdfDictionary, key: str) -> tuple[float, float, float, float] | None:
    """A page's own *key* rectangle with corners ordered, or ``None``.

    The page's own dictionary only: none of the production boxes is inheritable
    (ISO 32000-1 Table 30), so a ``/TrimBox`` on a page-tree node is not this
    page's trim box -- and PDF/X asks for the entry, not for a default.
    """
    value = pdf._resolve(page.get(PdfName(key)))
    if not isinstance(value, PdfArray) or len(value.items) < 4:
        return None
    numbers = []
    for item in value.items[:4]:
        number = pdf._resolve(item)
        if not isinstance(number, PdfNumber):
            return None
        numbers.append(float(number.value))
    x0, y0, x1, y1 = numbers
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def _contains(outer: tuple[float, ...], inner: tuple[float, ...]) -> bool:
    """Whether *outer* contains *inner*, to within a thousandth of a point."""
    tol = 1e-3
    return (
        outer[0] <= inner[0] + tol
        and outer[1] <= inner[1] + tol
        and outer[2] >= inner[2] - tol
        and outer[3] >= inner[3] - tol
    )


def _check_pdfx_boxes(
    pdf: Any,
    page: PdfDictionary,
    i: int,
    rules: PdfXStandardRules,
    errors: list[str],
) -> None:
    """Every page says where the finished piece is, and says it once.

    A print exchange file has to state the final trimmed size: a ``/TrimBox``
    (the trimmed page) or an ``/ArtBox`` (the extent of the artwork), and not
    both, since the two would be two answers to one question. The bleed box,
    where present, holds the trim box, and the media box holds everything.
    """
    clause = pdfx_cite(rules, "boxes")
    trim = _box(pdf, page, "TrimBox")
    art = _box(pdf, page, "ArtBox")
    has_trim = PdfName("TrimBox") in page.mapping
    has_art = PdfName("ArtBox") in page.mapping
    if has_trim and has_art:
        errors.append(
            f"PDF/X requires a /TrimBox or an /ArtBox on page {i + 1}, not both "
            f"({clause})."
        )
    elif not has_trim and not has_art:
        errors.append(
            f"PDF/X requires a /TrimBox or an /ArtBox on page {i + 1} ({clause})."
        )
    final = trim if trim is not None else art
    if final is None:
        if has_trim or has_art:
            errors.append(
                f"The /{'TrimBox' if has_trim else 'ArtBox'} on page {i + 1} is "
                f"not four numbers ({clause})."
            )
        return

    bleed = _box(pdf, page, "BleedBox")
    if bleed is not None and not _contains(bleed, final):
        errors.append(
            f"The /BleedBox on page {i + 1} does not contain the "
            f"/{'TrimBox' if trim is not None else 'ArtBox'} ({clause})."
        )
    media = pdf._page_media_box(i)
    if media is not None:
        outer = bleed if bleed is not None else final
        if not _contains(media, outer):
            errors.append(
                f"The /MediaBox on page {i + 1} does not contain the "
                f"/{'BleedBox' if bleed is not None else 'TrimBox'} ({clause})."
            )


def _check_pdfx_page_group(
    pdf: Any,
    page: PdfDictionary,
    i: int,
    rules: PdfXStandardRules,
    errors: list[str],
) -> None:
    """Live transparency: prohibited before part 4, colour-managed from it.

    A transparency group blends its contents in a colour space, and what that
    space is decides the result. Part 4 permits live transparency and asks a
    page group to name the space (``/CS``) so that the blend is defined rather
    than left to whatever the reader picks; the earlier parts do not permit the
    group at all.
    """
    group = _get_dict(pdf, page.get(PdfName("Group")))
    if group is None or _name(pdf, group, "S") != "Transparency":
        return
    clause = pdfx_cite(rules, "transparency")
    if not rules.allows_transparency:
        errors.append(
            f"{rules.name} prohibits transparency groups (page {i + 1} /Group "
            f"/S /Transparency) ({clause})."
        )
        return
    if PdfName("CS") not in group.mapping:
        errors.append(
            f"{rules.name} requires a page's transparency group to name its "
            f"blending colour space (/Group /CS) (page {i + 1}) "
            f"({pdfx_cite(rules, 'colour')})."
        )


def _check_pdfx_annotations(
    pdf: Any,
    page: PdfDictionary,
    i: int,
    rules: PdfXStandardRules,
    errors: list[str],
    warnings: list[str],
) -> None:
    """Multimedia annotations are out; the rest stay off the printed area.

    A PDF/X file is print data, so an annotation that would be rendered on the
    sheet is a mark nobody authored. The prohibition on the multimedia subtypes
    is stated as an error; whether a given rectangle counts as "inside" the
    trimmed area is a judgement about intent, so an annotation overlapping it is
    reported as a warning rather than a failure.
    """
    annots = pdf._resolve(page.get(PdfName("Annots")))
    if not isinstance(annots, PdfArray):
        return
    clause = pdfx_cite(rules, "annotations")
    keep_out = _box(pdf, page, "BleedBox") or _box(pdf, page, "TrimBox") or _box(
        pdf, page, "ArtBox"
    )
    for ref in annots.items:
        annot = _get_dict(pdf, ref)
        if annot is None:
            continue
        subtype = _name(pdf, annot, "Subtype")
        if subtype in _PROHIBITED_ANNOT_SUBTYPES:
            errors.append(
                f"PDF/X prohibits /{subtype} annotations (page {i + 1}) ({clause})."
            )
            continue
        if subtype in ("Popup", "TrapNet"):
            continue
        flags_obj = pdf._resolve(annot.get(PdfName("F")))
        flags = int(flags_obj.value) if isinstance(flags_obj, PdfNumber) else 0
        if not flags & ANNOT_FLAG_PRINT:
            continue
        rect = _box(pdf, annot, "Rect")
        if keep_out is None or rect is None:
            continue
        overlaps = (
            rect[0] < keep_out[2]
            and rect[2] > keep_out[0]
            and rect[1] < keep_out[3]
            and rect[3] > keep_out[1]
        )
        if overlaps:
            warnings.append(
                f"A printable /{subtype or 'annotation'} on page {i + 1} lies "
                "over the area PDF/X reserves for print data; annotations "
                f"belong outside the bleed or trim box ({clause})."
            )


#: Colour space families that carry colour independently of any device, which
#: part 1 (CMYK, grey and spot colour only) does not permit.
_DEVICE_INDEPENDENT_SPACES = frozenset({"CalRGB", "CalGray", "Lab", "ICCBased"})


def _check_pdfx_colour_space(
    pdf: Any,
    space: Any,
    i: int,
    rules: PdfXStandardRules,
    errors: list[str],
    seen: set[int],
    depth: int = 0,
) -> None:
    """Walk one colour space, reporting families the part does not permit.

    Indexed, Separation, DeviceN and Pattern all stand on another space, so the
    walk follows the base and alternate spaces: an ``/Indexed`` palette over
    ``/DeviceRGB`` paints RGB however its own name reads.
    """
    if rules.allows_device_independent_colour or depth > _MAX_RESOURCE_DEPTH:
        return
    space = pdf._resolve(space)
    if space is None or id(space) in seen:
        return
    seen.add(id(space))
    clause = pdfx_cite(rules, "colour")

    if isinstance(space, PdfName):
        name = space.name.lstrip("/")
        if name == "DeviceRGB":
            errors.append(
                f"{rules.name} permits CMYK, grey and spot colour only; page "
                f"{i + 1} uses /DeviceRGB ({clause})."
            )
        return
    if not isinstance(space, PdfArray) or not space.items:
        return
    family = pdf._get_name(space.items[0])
    if family in _DEVICE_INDEPENDENT_SPACES:
        errors.append(
            f"{rules.name} permits CMYK, grey and spot colour only; page "
            f"{i + 1} uses the device-independent /{family} ({clause})."
        )
        return
    if family == "Indexed" and len(space.items) > 1:
        _check_pdfx_colour_space(
            pdf, space.items[1], i, rules, errors, seen, depth + 1
        )
    elif family in ("Separation", "DeviceN") and len(space.items) > 2:
        # The alternate space is what a reader paints when it has no colorant
        # of that name, so it is as much a part of the file's colour as a
        # directly selected space.
        _check_pdfx_colour_space(
            pdf, space.items[2], i, rules, errors, seen, depth + 1
        )
    elif family == "Pattern" and len(space.items) > 1:
        _check_pdfx_colour_space(
            pdf, space.items[1], i, rules, errors, seen, depth + 1
        )


def _check_pdfx_resources(
    pdf: Any,
    resources: PdfDictionary,
    i: int,
    rules: PdfXStandardRules,
    errors: list[str],
    visited: set[int],
    depth: int,
) -> None:
    """Resource-level prohibitions, recursing through form XObjects."""
    if depth > _MAX_RESOURCE_DEPTH or id(resources) in visited:
        return
    visited.add(id(resources))

    spaces = _get_dict(pdf, resources.get(PdfName("ColorSpace")))
    if spaces is not None:
        for ref in spaces.mapping.values():
            _check_pdfx_colour_space(pdf, ref, i, rules, errors, set())

    extgstates = _get_dict(pdf, resources.get(PdfName("ExtGState")))
    if extgstates is not None:
        for ref in extgstates.mapping.values():
            gs = _get_dict(pdf, ref)
            if gs is not None:
                _check_pdfx_extgstate(pdf, gs, i, rules, errors)

    shadings = _get_dict(pdf, resources.get(PdfName("Shading")))
    if shadings is not None:
        for ref in shadings.mapping.values():
            shading = _get_dict(pdf, ref)
            if shading is not None:
                _check_pdfx_colour_space(
                    pdf, shading.get(PdfName("ColorSpace")), i, rules, errors, set()
                )

    xobjects = _get_dict(pdf, resources.get(PdfName("XObject")))
    if xobjects is None:
        return
    for ref in xobjects.mapping.values():
        xobj = pdf._resolve(ref)
        if not isinstance(xobj, PdfStream):
            continue
        subtype = _name(pdf, xobj, "Subtype")
        if subtype == "PS":
            errors.append(
                f"PDF/X prohibits PostScript XObjects (page {i + 1}) "
                f"({pdfx_cite(rules, 'postscript')})."
            )
        elif subtype == "Image":
            _check_pdfx_image(pdf, xobj, i, rules, errors)
        elif subtype == "Form":
            if PdfName("Ref") in xobj.mapping:
                errors.append(
                    "PDF/X prohibits reference XObjects pointing at external "
                    f"content (page {i + 1}) "
                    f"({pdfx_cite(rules, 'files')})."
                )
            group = _get_dict(pdf, xobj.get(PdfName("Group")))
            if (
                group is not None
                and _name(pdf, group, "S") == "Transparency"
                and not rules.allows_transparency
            ):
                errors.append(
                    f"{rules.name} prohibits transparency groups (form XObject, "
                    f"page {i + 1}) "
                    f"({pdfx_cite(rules, 'transparency')})."
                )
            nested = _get_dict(pdf, xobj.get(PdfName("Resources")))
            if nested is not None:
                _check_pdfx_resources(
                    pdf, nested, i, rules, errors, visited, depth + 1
                )


def _check_pdfx_image(
    pdf: Any,
    image: PdfStream,
    i: int,
    rules: PdfXStandardRules,
    errors: list[str],
) -> None:
    """An image's colour, its codec, and the alternates the parts rule out."""
    _check_pdfx_colour_space(
        pdf, image.get(PdfName("ColorSpace")), i, rules, errors, set()
    )
    if not rules.allows_jpx:
        names = []
        filters = pdf._resolve(image.get(PdfName("Filter")))
        if isinstance(filters, PdfName):
            names = [filters.name.lstrip("/")]
        elif isinstance(filters, PdfArray):
            names = [
                pdf._get_name(f) or "" for f in filters.items
            ]
        if "JPXDecode" in names:
            topic = "jpeg2000" if "jpeg2000" in rules.clauses else "compression"
            errors.append(
                f"{rules.name} prohibits JPEG 2000 images (/JPXDecode) "
                f"(page {i + 1}) ({pdfx_cite(rules, topic)})."
            )
    if rules.part != 4 and PdfName("Alternates") in image.mapping:
        errors.append(
            f"{rules.name} prohibits alternate images (/Alternates) "
            f"(page {i + 1})."
        )
    if not rules.allows_transparency and isinstance(
        pdf._resolve(image.get(PdfName("SMask"))), PdfStream
    ):
        errors.append(
            f"{rules.name} prohibits soft-mask images (/SMask) (page {i + 1}) "
            f"({pdfx_cite(rules, 'transparency')})."
        )


def _check_pdfx_extgstate(
    pdf: Any,
    gs: PdfDictionary,
    i: int,
    rules: PdfXStandardRules,
    errors: list[str],
) -> None:
    """Transfer functions always, and the transparency knobs before part 4."""
    clause = pdfx_cite(rules, "extgstate")
    for key, allowed in (("TR", {"Identity"}), ("TR2", {"Identity", "Default"})):
        if PdfName(key) in gs.mapping and _name(pdf, gs, key) not in allowed:
            errors.append(
                f"PDF/X prohibits transfer functions in ExtGState (/{key}) "
                f"(page {i + 1}) ({clause})."
            )
    if rules.allows_transparency:
        return
    transparency = pdfx_cite(rules, "transparency")
    if isinstance(pdf._resolve(gs.get(PdfName("SMask"))), PdfDictionary):
        errors.append(
            f"{rules.name} prohibits soft masks in ExtGState (/SMask) "
            f"(page {i + 1}) ({transparency})."
        )
    blend = pdf._resolve(gs.get(PdfName("BM")))
    blend_names = (
        [pdf._get_name(x) for x in blend.items]
        if isinstance(blend, PdfArray)
        else [pdf._get_name(blend)]
    )
    for name in blend_names:
        if name not in (None, "Normal", "Compatible"):
            errors.append(
                f"{rules.name} prohibits blend mode /{name} in ExtGState "
                f"(page {i + 1}) ({transparency})."
            )
            break
    for key in ("CA", "ca"):
        alpha = pdf._resolve(gs.get(PdfName(key)))
        if isinstance(alpha, PdfNumber) and alpha.value < 1.0 - 1e-9:
            errors.append(
                f"{rules.name} prohibits constant alpha < 1 (/{key}) in "
                f"ExtGState (page {i + 1}) ({transparency})."
            )


def _check_pdfx_filters(
    pdf: Any, rules: PdfXStandardRules, errors: list[str]
) -> None:
    """``LZWDecode`` is out of every part; JBIG2 is out of the PDF 1.4 parts."""
    clause = pdfx_cite(rules, "compression")
    flagged: set[str] = set()
    prohibited = {"LZWDecode"}
    if rules.part != 4:
        prohibited.add("JBIG2Decode")
    for obj in pdf._cos_doc.objects.values():
        if not isinstance(obj, PdfStream):
            continue
        for name in _collect_filter_names(obj.mapping.get(PdfName("Filter")), pdf._resolve):
            if name in prohibited and name not in flagged:
                flagged.add(name)
                errors.append(
                    f"{rules.name} prohibits the /{name} stream filter ({clause})."
                )
        if flagged == prohibited:
            return


def _collect_filter_names(filter_obj: Any, resolve: Any) -> list[str]:
    """``/Filter`` as a list of names without the leading slash."""
    filter_obj = resolve(filter_obj)
    if isinstance(filter_obj, PdfName):
        return [filter_obj.name.lstrip("/")]
    names = []
    if isinstance(filter_obj, PdfArray):
        for item in filter_obj.items:
            resolved = resolve(item)
            if isinstance(resolved, PdfName):
                names.append(resolved.name.lstrip("/"))
    return names


# ---------------------------------------------------------------------------
# "Tagged or artifact": the rule a stamp used to break silently
# ---------------------------------------------------------------------------

#: Operators that put a mark on the page. ``n`` is absent on purpose -- it ends
#: a path without painting it -- and so are the text-positioning and
#: graphics-state operators, which move the pen without marking anything.
_MARK_OPERATORS = frozenset(
    {
        # text showing
        "Tj", "TJ", "'", '"',
        # path painting, filling and stroking
        "S", "s", "f", "F", "f*", "B", "B*", "b", "b*",
        # a shading fill, and an inline image's samples
        "sh", "BI",
    }
)

#: How far down a chain of form XObjects the coverage scan follows an
#: *uncovered* invocation. A form invoked inside a covered scope is covered with
#: it and is not descended into at all.
_MAX_ARTIFACT_FORM_DEPTH = 6


def _uncovered_marks(
    pdf: Any,
    content: bytes,
    resources: PdfDictionary | None,
    depth: int,
    seen: set[int],
) -> list[str]:
    """Operators in *content* that no marked-content sequence encloses.

    The scan tracks ``BDC``/``BMC``/``EMC`` nesting and reports a mark-producing
    operator only when **nothing** is open around it -- the unambiguous case, and
    the one authored decoration used to produce. A sequence that is open but
    carries neither an ``/MCID`` nor ``/Artifact`` (a bare ``/P BMC``) is content
    a structure tree cannot reach either, but saying so would mean deciding what
    a producer meant by it, so it is left alone; see the docstring of
    :func:`pdfua_artifact_coverage`.

    A ``Do`` outside any sequence is followed: a form XObject may cover its own
    content, in which case the invocation needs no sequence of its own (14.8.2.2
    lets a whole form be an artifact, or carry tagged content). An image has no
    content of its own, so an uncovered one is a mark.
    """
    from .auto_tag import _tokens

    found: list[str] = []
    open_sequences = 0
    dict_depth = 0
    last_name: str | None = None
    xobjects = (
        _get_dict(pdf, resources.get(PdfName("XObject")))
        if resources is not None
        else None
    )
    for token, _start, _end in _tokens(
        content, limits=pdf._load_limits, budget=pdf._load_budget
    ):
        if token is None:
            continue
        if token == "<<":
            dict_depth += 1
            continue
        if token == ">>":
            dict_depth = max(0, dict_depth - 1)
            continue
        if dict_depth:
            continue  # inside a property list, not a stream of operators
        if token in ("BDC", "BMC"):
            open_sequences += 1
            last_name = None
            continue
        if token == "EMC":
            open_sequences = max(0, open_sequences - 1)
            last_name = None
            continue
        if token.startswith("/"):
            last_name = token.lstrip("/")
            continue
        if open_sequences:
            last_name = None
            continue
        if token == "Do":
            name = last_name
            last_name = None
            xobject = (
                pdf._resolve(xobjects.get(PdfName(name)))
                if xobjects is not None and name
                else None
            )
            if not isinstance(xobject, PdfStream):
                continue  # a name the page does not define: not this check's business
            subtype = pdf._get_name(xobject.get(PdfName("Subtype")))
            if subtype == "Form":
                if depth >= _MAX_ARTIFACT_FORM_DEPTH or id(xobject) in seen:
                    continue
                seen.add(id(xobject))
                try:
                    body = pdf._decode_cos_stream(xobject, None)
                except PdfResourceLimitException:
                    raise
                except PDF_OPERATION_ERRORS:
                    continue
                nested = _get_dict(pdf, xobject.get(PdfName("Resources")))
                found.extend(
                    _uncovered_marks(
                        pdf, body, nested if nested is not None else resources,
                        depth + 1, seen,
                    )
                )
            else:
                found.append("Do")
            continue
        last_name = None
        if token in _MARK_OPERATORS:
            found.append(token)
    return found


def pdfua_artifact_coverage(pdf: Any) -> tuple[list[str], list[str]]:
    """Content on a tagged page that is neither tagged nor marked as an artifact.

    ISO 14289-1 7.1 requires every mark on the page of a tagged document to be
    one or the other: real content inside a tagged marked-content sequence, or
    decoration inside an ``/Artifact`` one. A watermark, a page number or a rule
    that says neither leaves the document non-conformant however well the rest
    of it is tagged -- which is why this library marks the decoration it authors
    (see :func:`.content_authoring.wrap_artifact`).

    Reported as **warnings**, for two reasons. The scan sees the operators of a
    content stream, not everything a producer may have meant: a sequence that is
    open but carries neither an ``/MCID`` nor ``/Artifact`` is not counted
    against the page, because deciding what it was for is not something a scan
    can do. And ``convert_to_pdfua()`` deliberately offers a shell-only
    conversion whose content is untagged by design, which this would otherwise
    turn from "a shell, as asked for" into a failure. Use
    ``convert_to_pdfua(auto_tag=True)``, tag the content as it is authored, or
    mark it as an artifact, and the warnings go.
    """
    errors: list[str] = []
    warnings: list[str] = []
    root = catalog(pdf)
    if root is None:
        return errors, warnings
    if _get_dict(pdf, root.get(PdfName("StructTreeRoot"))) is None:
        return errors, warnings  # not a tagged document; the rule does not apply

    try:
        for i in range(len(pdf.pages)):
            page = pdf._get_page_dict(i)
            if not isinstance(page, PdfDictionary):
                continue
            resources = _get_dict(pdf, page.get(PdfName("Resources")))
            found = _uncovered_marks(
                pdf, pdf.get_page_content(i), resources, 0, set()
            )
            if not found:
                continue
            counted = Counter(found)
            shown = ", ".join(
                f"{operator} x{count}" if count > 1 else operator
                for operator, count in sorted(counted.items())
            )
            warnings.append(
                f"PDF/UA: page {i + 1} marks content that is neither tagged nor "
                f"an artifact ({shown}); ISO 14289-1 7.1 asks for one or the "
                "other."
            )
    except PdfResourceLimitException:
        raise
    except PDF_OPERATION_ERRORS:
        pass
    return errors, warnings

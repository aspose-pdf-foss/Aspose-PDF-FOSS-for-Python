"""Extended structural conformance checks for PDF/A and PDF/UA.

This module materially expands the heuristic PDF/A (ISO 19005) and PDF/UA
(ISO 14289) coverage of :mod:`aspose_pdf.engine.simple_pdf`.  Everything here
operates purely on the parsed COS object graph of a loaded document: the
checks look for structures that are *prohibited* or *required* by the
standards and that are observable without rendering the page — catalog
entries, the page tree, fonts, annotations, actions, transparency and
optional-content constructs.

They remain **heuristic**: they do not verify glyph coverage, colour
rendering, or the semantic correctness of a structure tree, so they are not a
substitute for a certification-grade validator such as veraPDF.  Together with
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
from typing import Any, List, Optional, Set, Tuple

from ..exceptions import PDF_OPERATION_ERRORS
from .cos import (
    PdfArray,
    PdfBoolean,
    PdfDictionary,
    PdfName,
    PdfNumber,
    PdfStream,
    PdfString,
)

# Annotation subtypes that are never permitted in PDF/A (multimedia / 3D).
_PROHIBITED_ANNOT_SUBTYPES = frozenset(
    {"Sound", "Movie", "Screen", "RichMedia", "3D"}
)

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
def _get_dict(pdf: Any, obj: Any) -> Optional[PdfDictionary]:
    obj = pdf._resolve(obj)
    return obj if isinstance(obj, PdfDictionary) else None


def catalog(pdf: Any) -> Optional[PdfDictionary]:
    """Return the document catalog (``/Root``) dictionary, or ``None``."""
    if pdf._cos_doc is None:
        return None
    try:
        return _get_dict(pdf, pdf._cos_doc.trailer.get(PdfName("Root")))
    except PDF_OPERATION_ERRORS:
        return None


def _is_part1(level_short: str) -> bool:
    return level_short[:1] == "1"


def _parse_pdf_version(value: str) -> Optional[float]:
    m = re.match(r"\s*(\d+)\.(\d+)", value or "")
    if not m:
        return None
    return float(f"{m.group(1)}.{m.group(2)}")


# ---------------------------------------------------------------------------
# PDF/A
# ---------------------------------------------------------------------------
def pdfa_extended(pdf: Any, level_short: str) -> Tuple[List[str], List[str]]:
    """Return ``(errors, warnings)`` for the extended PDF/A structural checks.

    ``level_short`` is the normalised level (e.g. ``"1b"``, ``"2a"``).
    """
    errors: List[str] = []
    warnings: List[str] = []
    if pdf._cos_doc is None:
        return errors, warnings

    part1 = _is_part1(level_short)
    is_a3 = level_short[:1] == "3"
    level_a = level_short.endswith("a")

    try:
        _check_trailer_id(pdf, errors)
        _check_pdf_version(pdf, level_short, errors)
        _check_catalog_rules(pdf, part1, errors)
        _check_acroform(pdf, errors)
        _check_metadata_unfiltered(pdf, errors)
        _check_pages(pdf, part1, is_a3, errors, warnings)
        if is_a3:
            _check_embedded_files(pdf, errors)
        if level_a:
            _check_tagging_for_level_a(pdf, errors, warnings)
    except PDF_OPERATION_ERRORS:
        # Conformance checks are best-effort: a malformed object must never
        # crash validation. The base checks already flag structural damage.
        pass

    return errors, warnings


def _check_trailer_id(pdf: Any, errors: List[str]) -> None:
    id_obj = pdf._resolve(pdf._cos_doc.trailer.get(PdfName("ID")))
    if not isinstance(id_obj, PdfArray) or len(id_obj.items) < 1:
        errors.append("PDF/A requires a file identifier (/ID) in the trailer.")


def _check_pdf_version(pdf: Any, level_short: str, errors: List[str]) -> None:
    version = _parse_pdf_version(getattr(pdf, "pdf_version", "") or "")
    if version is None:
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


def _check_catalog_rules(pdf: Any, part1: bool, errors: List[str]) -> None:
    root = catalog(pdf)
    if root is None:
        return
    if PdfName("AA") in root:
        errors.append("PDF/A prohibits document-level additional actions (/AA).")
    if part1 and PdfName("OCProperties") in root:
        errors.append("PDF/A-1 prohibits optional content / layers (/OCProperties).")
    if PdfName("Requirements") in root:
        errors.append("PDF/A prohibits the catalog /Requirements entry.")


def _check_acroform(pdf: Any, errors: List[str]) -> None:
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


def _check_metadata_unfiltered(pdf: Any, errors: List[str]) -> None:
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
    is_a3: bool,
    errors: List[str],
    warnings: List[str],
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
        _check_annotations(pdf, page, i, part1, is_a3, errors, warnings)
        resources = _get_dict(pdf, page.get(PdfName("Resources")))
        if resources is not None:
            _check_resources(pdf, resources, i, part1, errors, set(), 0)


def _name(pdf: Any, d: PdfDictionary, key: str) -> Optional[str]:
    return pdf._get_name(d.get(PdfName(key)))


def _check_annotations(
    pdf: Any,
    page: PdfDictionary,
    i: int,
    part1: bool,
    is_a3: bool,
    errors: List[str],
    warnings: List[str],
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
        if subtype in _PROHIBITED_ANNOT_SUBTYPES:
            errors.append(f"PDF/A prohibits /{subtype} annotations (page {i + 1}).")
        elif subtype == "FileAttachment" and not is_a3:
            errors.append(
                "PDF/A (except PDF/A-3) prohibits /FileAttachment annotations "
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


def _check_action(pdf: Any, action_ref: Any, i: int, errors: List[str]) -> None:
    """Flag prohibited action ``/S`` types, following a one-level ``/Next``."""
    queue = [pdf._resolve(action_ref)]
    seen: Set[int] = set()
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
    errors: List[str],
    visited: Set[int],
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
    pdf: Any, gs: PdfDictionary, i: int, part1: bool, errors: List[str]
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
    pdf: Any, errors: List[str], warnings: List[str]
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


def _check_embedded_files(pdf: Any, errors: List[str]) -> None:
    """Flag PDF/A-3 embedded file specifications lacking ``/AFRelationship``."""
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
            raw = fname.value
            label = (
                raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
            )
        else:
            label = "embedded file"
        errors.append(
            "PDF/A-3 requires /AFRelationship on embedded file specifications "
            f"({label})."
        )


def _iter_name_tree_values(pdf: Any, node: PdfDictionary, visited: Set[int], depth: int):
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
    seen: Set[str] = set()
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
    parent_type: Optional[str],
    errors: List[str],
    warnings: List[str],
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
    parent_type: Optional[str],
    visited: Set[int],
    depth: int,
    errors: List[str],
    warnings: List[str],
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
) -> Tuple[List[str], List[str]]:
    """Walk the structure tree, returning ``(errors, warnings)``.

    ``full`` enables the PDF/UA-only advisory checks (heading order, list/table
    containment, ParentTree).  The error-level checks (non-standard types,
    Figure/Formula alt text, Note /ID) apply to both PDF/UA and PDF/A level A.
    """
    errors: List[str] = []
    warnings: List[str] = []
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


def _check_fonts_embedded(pdf: Any, errors: List[str]) -> None:
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


def pdfua_structure(pdf: Any) -> Tuple[List[str], List[str]]:
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


def pdfua_pages(pdf: Any) -> Tuple[List[str], List[str]]:
    """Return ``(errors, warnings)`` for the PDF/UA page/annotation checks."""
    errors: List[str] = []
    warnings: List[str] = []
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
    except PDF_OPERATION_ERRORS:
        pass
    return errors, warnings


# ---------------------------------------------------------------------------
# PDF/UA — catalog
# ---------------------------------------------------------------------------
def pdfua_extended(pdf: Any) -> Tuple[List[str], List[str]]:
    """Return ``(errors, warnings)`` for the extended PDF/UA catalog checks."""
    errors: List[str] = []
    warnings: List[str] = []
    root = catalog(pdf)
    if root is None:
        return errors, warnings

    try:
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
        elif "pdfuaid:part" not in metadata.content.decode("utf-8", errors="replace"):
            errors.append("PDF/UA XMP metadata must declare pdfuaid:part (= 1).")
    except PDF_OPERATION_ERRORS:
        pass

    # Structure-tree semantics and page/annotation rules.
    struct_errors, struct_warnings = pdfua_structure(pdf)
    errors.extend(struct_errors)
    warnings.extend(struct_warnings)
    page_errors, page_warnings = pdfua_pages(pdf)
    errors.extend(page_errors)
    warnings.extend(page_warnings)

    return errors, warnings


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

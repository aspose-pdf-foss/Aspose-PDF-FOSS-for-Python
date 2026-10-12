"""Take out of a document everything that is not what it shows.

Each of these is already removable on its own -- ``metadata``,
``javascript``, ``attachments``, ``flatten_layers``, the annotation
collections. What was missing is the *pass*: one call that goes through all of
them and says what it took, because the point of sanitizing is to be able to
state what is no longer in the file.

The parts that had no API of their own are the ones a caller is most likely to
miss: a page's additional actions, a field's keystroke and format scripts, a
link whose action launches a program rather than opening a URL, the producer's
private ``/PieceInfo`` store, and the ``/AF`` entries that point at embedded
files from outside the name tree.

Nothing here decides *whether* to remove something -- the caller passes that
in, because what counts as safe depends on what the document is for.
"""

from __future__ import annotations

from typing import Any

from aspose_pdf.exceptions import PDF_OPERATION_ERRORS
from aspose_pdf.sanitization import SanitizationReport

from .cos import PdfArray, PdfDictionary, PdfName

__all__ = ["ACTIVE_ACTIONS", "MARKUP_SUBTYPES", "sanitize"]

#: Action types that do more than move the reader around the document
#: (ISO 32000-1 table 193). A ``/GoTo`` jumps to a page and a ``/URI`` opens a
#: link, which is what a link is *for*; these run code, start programs, or send
#: the document's data somewhere.
ACTIVE_ACTIONS = frozenset(
    {
        "JavaScript",
        "Launch",
        "SubmitForm",
        "ImportData",
        "Movie",
        "Sound",
        "Rendition",
        "GoTo3DView",
        "RichMediaExecute",
        "SetOCGState",
        "Trans",
    }
)

#: Annotation subtypes that are somebody's **comment** on the document: each
#: carries an author, a date and a contents string (12.5.6.2). ``/Popup`` is
#: the window one of them opens in, and goes with it.
MARKUP_SUBTYPES = frozenset(
    {
        "Text",
        "FreeText",
        "Line",
        "Square",
        "Circle",
        "Polygon",
        "PolyLine",
        "Highlight",
        "Underline",
        "Squiggly",
        "StrikeOut",
        "Stamp",
        "Caret",
        "Ink",
        "Popup",
        "FileAttachment",
        "Sound",
        "Movie",
        "Redact",
    }
)

#: Where an annotation or a field keeps the things it does.
_ACTION_KEYS = (PdfName("A"), PdfName("AA"))

_PRIVATE_KEYS = (PdfName("PieceInfo"), PdfName("Metadata"))


def sanitize(
    pdf: Any,
    *,
    metadata: bool = True,
    javascript: bool = True,
    attachments: bool = True,
    hidden_layers: bool = True,
    comments: bool = True,
    links: bool = False,
    forms: bool = False,
) -> SanitizationReport:
    """Strip *pdf* of what the caller asked for and report the counts."""
    pdf._ensure_cos()
    catalog = pdf._resolve(pdf._cos_doc.trailer.mapping.get(PdfName("Root")))
    if not isinstance(catalog, PdfDictionary):
        return SanitizationReport()

    counts = {
        "metadata_entries": 0,
        "scripts": 0,
        "actions": 0,
        "attachments": 0,
        "layers": 0,
        "pages_flattened": 0,
        "comments": 0,
        "links": 0,
        "form_fields": 0,
    }

    # Layers first: flattening deletes the content hidden ones hold, and that
    # content may itself carry annotations. Doing it after would leave the
    # annotation counts describing things that were about to go anyway.
    if hidden_layers:
        counts["layers"], counts["pages_flattened"] = _flatten_layers(pdf, catalog)
    if metadata:
        counts["metadata_entries"] = _strip_metadata(pdf, catalog)
    if javascript:
        counts["scripts"], counts["actions"] = _strip_active_content(pdf, catalog)
    if attachments:
        counts["attachments"] = _strip_attachments(pdf, catalog)
    if forms:
        counts["form_fields"] = _strip_forms(pdf, catalog)
    if comments or links:
        counts["comments"], counts["links"] = _strip_annotations(
            pdf, comments=comments, links=links
        )
    # A /Names dictionary that held only the scripts and the embedded files is
    # an empty shell once they are gone, and says the document has a name tree
    # when it has none.
    names = pdf._resolve(catalog.mapping.get(PdfName("Names")))
    if isinstance(names, PdfDictionary) and not names.mapping:
        del catalog.mapping[PdfName("Names")]
    return SanitizationReport(**counts)


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


def _strip_metadata(pdf: Any, catalog: PdfDictionary) -> int:
    """Clear ``/Info``, the XMP packet, and the producer's private stores."""
    removed = len(pdf.metadata)
    pdf.metadata.clear()
    info = pdf._resolve(pdf._cos_doc.trailer.mapping.get(PdfName("Info")))
    if isinstance(info, PdfDictionary):
        removed += len(info.mapping)
        info.mapping.clear()

    # /PieceInfo is where an editor keeps its own working data (14.5), and
    # /Metadata the XMP packet -- both on the catalog and on every page.
    for holder in (catalog, *_page_dicts(pdf)):
        for key in _PRIVATE_KEYS:
            if key in holder.mapping:
                del holder.mapping[key]
                removed += 1
    return removed


# ---------------------------------------------------------------------------
# Scripts and actions
# ---------------------------------------------------------------------------


def _strip_active_content(pdf: Any, catalog: PdfDictionary) -> tuple[int, int]:
    """Remove document scripts and every action that runs something."""
    scripts = 0
    names = pdf._resolve(catalog.mapping.get(PdfName("Names")))
    if isinstance(names, PdfDictionary) and PdfName("JavaScript") in names.mapping:
        tree = pdf._resolve(names.mapping.get(PdfName("JavaScript")))
        scripts = _name_tree_size(pdf, tree)
        del names.mapping[PdfName("JavaScript")]

    actions = 0
    # The open action is only a risk when it *is* an action; a plain
    # destination just says which page to show (12.6.2).
    open_action = pdf._resolve(catalog.mapping.get(PdfName("OpenAction")))
    if isinstance(open_action, PdfDictionary) and PdfName("S") in open_action.mapping:
        del catalog.mapping[PdfName("OpenAction")]
        actions += 1
    if PdfName("AA") in catalog.mapping:
        del catalog.mapping[PdfName("AA")]
        actions += 1

    for page in _page_dicts(pdf):
        if PdfName("AA") in page.mapping:
            del page.mapping[PdfName("AA")]
            actions += 1
        for annotation in _annotations_of(pdf, page):
            actions += _strip_active_actions(pdf, annotation)

    for _name, field in _form_fields(pdf, catalog):
        actions += _strip_active_actions(pdf, field)
    return scripts, actions


def _strip_active_actions(pdf: Any, holder: PdfDictionary) -> int:
    """Drop *holder*'s ``/A`` and ``/AA`` entries that do more than navigate.

    A link whose action opens a URL keeps it; one that launches a program or
    submits the form loses it, and the annotation stays as a rectangle that
    does nothing. That is the narrower cut: taking the whole annotation would
    remove a piece of the document's content to deal with its behaviour.
    """
    removed = 0
    for key in _ACTION_KEYS:
        value = pdf._resolve(holder.mapping.get(key))
        if not isinstance(value, PdfDictionary):
            continue
        if key == PdfName("A"):
            if _is_active(pdf, value):
                del holder.mapping[key]
                removed += 1
            continue
        # /AA is a dictionary of trigger -> action; each is judged on its own.
        for trigger in list(value.mapping):
            action = pdf._resolve(value.mapping.get(trigger))
            if isinstance(action, PdfDictionary) and _is_active(pdf, action):
                del value.mapping[trigger]
                removed += 1
        if not value.mapping:
            del holder.mapping[key]
    return removed


def _is_active(pdf: Any, action: PdfDictionary, depth: int = 0) -> bool:
    """Whether *action*, or anything it chains to through ``/Next``, runs something."""
    if depth > 8:
        return True  # a chain this deep is not one to keep
    if pdf._get_name(action.mapping.get(PdfName("S"))) in ACTIVE_ACTIONS:
        return True
    nxt = pdf._resolve(action.mapping.get(PdfName("Next")))
    candidates = (
        nxt.items if isinstance(nxt, PdfArray) else [nxt] if nxt is not None else []
    )
    for candidate in candidates:
        candidate = pdf._resolve(candidate)
        if isinstance(candidate, PdfDictionary) and _is_active(
            pdf, candidate, depth + 1
        ):
            return True
    return False


# ---------------------------------------------------------------------------
# Embedded files
# ---------------------------------------------------------------------------


def _strip_attachments(pdf: Any, catalog: PdfDictionary) -> int:
    """Remove embedded files, wherever the document reaches them from."""
    removed = len(getattr(pdf, "attachments", {}) or {})
    if hasattr(pdf, "attachments"):
        pdf.attachments.clear()
    if hasattr(pdf, "attachment_read_meta"):
        pdf.attachment_read_meta.clear()

    names = pdf._resolve(catalog.mapping.get(PdfName("Names")))
    if isinstance(names, PdfDictionary) and PdfName("EmbeddedFiles") in names.mapping:
        tree = pdf._resolve(names.mapping.get(PdfName("EmbeddedFiles")))
        removed = max(removed, _name_tree_size(pdf, tree))
        del names.mapping[PdfName("EmbeddedFiles")]

    # /AF points at embedded files from the catalog and from pages, and a
    # file-attachment annotation carries one of its own (table 184).
    for holder in (catalog, *_page_dicts(pdf)):
        if PdfName("AF") in holder.mapping:
            del holder.mapping[PdfName("AF")]
    return removed


# ---------------------------------------------------------------------------
# Annotations and fields
# ---------------------------------------------------------------------------


def _strip_annotations(pdf: Any, *, comments: bool, links: bool) -> tuple[int, int]:
    """Remove comment and link annotations, page by page."""
    removed_comments = removed_links = 0
    for page in _page_dicts(pdf):
        annots = pdf._resolve(page.mapping.get(PdfName("Annots")))
        if not isinstance(annots, PdfArray):
            continue
        kept = []
        for reference in annots.items:
            annotation = pdf._resolve(reference)
            subtype = (
                pdf._get_name(annotation.mapping.get(PdfName("Subtype")))
                if isinstance(annotation, PdfDictionary)
                else None
            )
            if comments and subtype in MARKUP_SUBTYPES:
                removed_comments += 1
                continue
            if links and subtype == "Link":
                removed_links += 1
                continue
            kept.append(reference)
        annots.items[:] = kept
        if not kept:
            del page.mapping[PdfName("Annots")]
    if removed_comments or removed_links:
        pdf._page_cache_valid = False
    return removed_comments, removed_links


def _strip_forms(pdf: Any, catalog: PdfDictionary) -> int:
    """Remove the interactive form, and the widgets that showed it."""
    fields = _form_fields(pdf, catalog)
    if PdfName("AcroForm") in catalog.mapping:
        del catalog.mapping[PdfName("AcroForm")]
    for page in _page_dicts(pdf):
        annots = pdf._resolve(page.mapping.get(PdfName("Annots")))
        if not isinstance(annots, PdfArray):
            continue
        annots.items[:] = [
            reference
            for reference in annots.items
            if pdf._get_name(
                (pdf._resolve(reference) or PdfDictionary({})).mapping.get(
                    PdfName("Subtype")
                )
            )
            != "Widget"
        ]
        if not annots.items:
            del page.mapping[PdfName("Annots")]
    pdf._page_cache_valid = False
    return len(fields)


# ---------------------------------------------------------------------------
# Layers
# ---------------------------------------------------------------------------


def _flatten_layers(pdf: Any, catalog: PdfDictionary) -> tuple[int, int]:
    """Resolve optional content, deleting what the current state hides.

    ``/OCProperties /OCGs`` is every group the document declares (table 100),
    which is counted before flattening because flattening removes the entry.
    """
    from .optional_content import flatten

    properties = pdf._resolve(catalog.mapping.get(PdfName("OCProperties")))
    if not isinstance(properties, PdfDictionary):
        return 0, 0
    groups = pdf._resolve(properties.mapping.get(PdfName("OCGs")))
    count = len(groups.items) if isinstance(groups, PdfArray) else 0
    return count, flatten(pdf)


# ---------------------------------------------------------------------------
# Walking
# ---------------------------------------------------------------------------


def _page_dicts(pdf: Any) -> list[PdfDictionary]:
    out = []
    for index in range(len(pdf.pages)):
        page = pdf._get_page_dict(index)
        if isinstance(page, PdfDictionary):
            out.append(page)
    return out


def _annotations_of(pdf: Any, page: PdfDictionary) -> list[PdfDictionary]:
    annots = pdf._resolve(page.mapping.get(PdfName("Annots")))
    if not isinstance(annots, PdfArray):
        return []
    return [
        item
        for item in (pdf._resolve(reference) for reference in annots.items)
        if isinstance(item, PdfDictionary)
    ]


def _form_fields(pdf: Any, catalog: PdfDictionary) -> list[tuple[str, PdfDictionary]]:
    """The document's fields -- the definitions, not the widgets showing them.

    ``_iter_form_fields`` walks the whole tree, so a field with a separate
    widget kid comes back twice under the same name. A field carries its own
    partial name ``/T`` (table 220) and a widget does not, which is what tells
    the two apart; counting without that said a one-field form had two. The
    widgets are not missed on the way: they are annotations, and the page walk
    reaches them.
    """
    acroform = pdf._resolve(catalog.mapping.get(PdfName("AcroForm")))
    if not isinstance(acroform, PdfDictionary):
        return []
    return [
        (name, field)
        for name, field in pdf._iter_form_fields()
        if PdfName("T") in field.mapping
    ]


def _name_tree_size(pdf: Any, tree: Any) -> int:
    """How many entries a name tree holds, without materialising their values."""
    from . import conformance

    if not isinstance(tree, PdfDictionary):
        return 0
    try:
        return sum(
            1 for _ in conformance._iter_name_tree_values(pdf, tree, set(), 0)
        )
    except PDF_OPERATION_ERRORS:
        return 0


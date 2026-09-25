"""What changed after a document was signed.

A signature covers the bytes of the revision it signs -- its ``/ByteRange`` --
and nothing appended later. Content added or altered afterwards, by a later
incremental update or by an edit in memory, is *unsigned*: a viewer shows it,
but no signature vouches for it. :class:`SignedChanges` answers that question
per page, per annotation and per form field, by comparing the object graph as it
stands now with the one the newest signature covers, object by object, through
the same canonical serialization an incremental save uses to decide what it has
to write.
"""

from __future__ import annotations

from typing import Any

from aspose_pdf.exceptions import PdfParseException

from .cos import PdfArray, PdfDictionary, PdfIndirectReference, PdfName, PdfStream
from .pdf_parser_cos import PdfCosParser
from .pdf_writer_cos import PdfCosWriter

#: Keys that lead from an object to one judged on its own: a page's annotations
#: and parent, an annotation's page, parent, popup and the note it answers.
_PAGE_SKIP = frozenset({"/Annots", "/Parent", "/B"})
_ANNOT_SKIP = frozenset({"/P", "/Parent", "/Popup", "/IRT"})
_FIELD_SKIP = frozenset({"/P", "/Parent", "/Popup", "/IRT"})


class SignedChanges:
    """The objects that are new or different since the newest signed revision."""

    def __init__(self, pdf: Any, pristine: Any, changed: set[int]) -> None:
        self._pdf = pdf
        self._pristine = pristine
        self.changed = changed

    # -- the three kinds of content ------------------------------------------

    def page_changed(self, index: int) -> bool:
        """Whether page *index*, its content or the resources it draws with changed."""
        refs = getattr(self._pdf, "_page_obj_ids", [])
        if index >= len(refs) or not refs[index]:
            return True
        return self._reaches_change(self._pdf._cos_doc.reference(refs[index]), _PAGE_SKIP)

    def annotation_changed(self, page_index: int, annot_index: int) -> bool:
        """Whether annotation *annot_index* of page *page_index* is new or changed."""
        ref = self._annotation_ref(page_index, annot_index)
        if ref is None:
            return True
        if isinstance(ref, PdfIndirectReference):
            return self._reaches_change(ref, _ANNOT_SKIP)
        # A direct annotation is part of the page's own dictionary.
        return self._pdf._page_obj_ids[page_index] in self.changed

    def field_changed(self, name: str) -> bool:
        """Whether the terminal field *name* (fully qualified) is new or changed."""
        ref = self._field_refs().get(name)
        if ref is None:
            return True
        return self._reaches_change(ref, _FIELD_SKIP)

    # -- plumbing ---------------------------------------------------------------

    def _reaches_change(self, start: Any, skip: frozenset[str]) -> bool:
        """Whether *start* or anything it reaches (not through *skip*) changed."""
        seen: set[int] = set()
        stack: list[Any] = [start]
        budget = self._pdf._load_budget
        while stack:
            value = stack.pop()
            if isinstance(value, PdfIndirectReference):
                number = value.object_number
                if number in seen:
                    continue
                seen.add(number)
                budget.check(len(seen), "max_container_items", "signed-content walk")
                value = self._pdf._cos_doc.get_object(value)
                if number in self.changed and self._differs(number, value, skip):
                    return True
            if isinstance(value, (PdfDictionary, PdfStream)):
                stack.extend(
                    item for key, item in value.mapping.items() if key.name not in skip
                )
            elif isinstance(value, PdfArray):
                stack.extend(value.items)
        return False

    def _differs(self, number: int, value: Any, skip: frozenset[str]) -> bool:
        """Whether object *number* changed in more than the entries in *skip*.

        A page that gained an annotation has a new ``/Annots`` and nothing else
        new: the annotation is reported on its own, and the page is not.
        """
        before = self._pristine.objects.get(number)
        if before is None or not isinstance(value, PdfDictionary) or isinstance(value, PdfStream):
            return True
        if not isinstance(before, PdfDictionary) or isinstance(before, PdfStream):
            return True

        def kept(dictionary: PdfDictionary) -> PdfDictionary:
            return PdfDictionary({k: v for k, v in dictionary.mapping.items() if k.name not in skip})

        writer = PdfCosWriter(self._pdf._cos_doc)
        return writer.serialize_object(kept(before)) != writer.serialize_object(kept(value))

    def _annotation_ref(self, page_index: int, annot_index: int) -> Any:
        """The ``/Annots`` entry the public annotation at *annot_index* was read from."""
        pdf = self._pdf
        refs = getattr(pdf, "_page_obj_ids", [])
        if page_index >= len(refs):
            return None
        page = pdf._cos_doc.objects.get(refs[page_index])
        if not isinstance(page, PdfDictionary):
            return None
        annots = pdf._resolve(page.mapping.get(PdfName("Annots")))
        if not isinstance(annots, PdfArray):
            return None
        # Only entries that resolve to a dictionary are annotations to the reader.
        entries = [item for item in annots.items if isinstance(pdf._resolve(item), PdfDictionary)]
        return entries[annot_index] if annot_index < len(entries) else None

    def _field_refs(self) -> dict[str, Any]:
        """Each terminal field's fully qualified name and the object it is."""
        pdf = self._pdf
        root = pdf._resolve(pdf._cos_doc.trailer.mapping.get(PdfName("Root")))
        acro = pdf._resolve(root.mapping.get(PdfName("AcroForm"))) if isinstance(root, PdfDictionary) else None
        fields = pdf._resolve(acro.mapping.get(PdfName("Fields"))) if isinstance(acro, PdfDictionary) else None
        found: dict[str, Any] = {}
        if not isinstance(fields, PdfArray):
            return found
        stack: list[tuple[Any, str]] = [(item, "") for item in reversed(fields.items)]
        seen: set[int] = set()
        while stack:
            ref, prefix = stack.pop()
            node = pdf._resolve(ref)
            if not isinstance(node, PdfDictionary) or id(node) in seen:
                continue
            seen.add(id(node))
            pdf._load_budget.check(len(seen), "max_container_items", "signed-content fields")
            partial = pdf._get_cos_string(node.mapping.get(PdfName("T")))
            name = f"{prefix}.{partial}" if prefix and partial else (partial or prefix)
            kids = pdf._resolve(node.mapping.get(PdfName("Kids")))
            named_kids = (
                [kid for kid in kids.items if isinstance(pdf._resolve(kid), PdfDictionary)
                 and PdfName("T") in pdf._resolve(kid).mapping]
                if isinstance(kids, PdfArray)
                else []
            )
            if named_kids:
                stack.extend((kid, name) for kid in reversed(named_kids))
            elif name:
                found.setdefault(name, ref)
        return found


def changes_since_signing(pdf: Any) -> SignedChanges | None:
    """The changes since the newest signature, or ``None`` if nothing is signed.

    The newest signature is the one whose ``/ByteRange`` reaches furthest into
    the file: what it covers, every earlier signature covers less of. Only a
    signature that verifies counts -- one that does not vouches for nothing.
    """
    raw = getattr(pdf, "_raw_bytes", None)
    if raw is None or pdf._cos_doc is None:
        return None
    ends = [
        int(sig.byte_range[2]) + int(sig.byte_range[3])
        for sig in getattr(pdf, "signatures", None) or []
        if isinstance(getattr(sig, "byte_range", None), list)
        and len(sig.byte_range) == 4
        and sig.valid
    ]
    ends = [end for end in ends if 0 < end <= len(raw)]
    if not ends:
        return None
    signed = bytes(raw[: max(ends)])
    pristine = PdfCosParser(signed, limits=pdf._load_limits, budget=pdf._load_budget).parse()
    if PdfName("Encrypt") in pristine.trailer.mapping:
        # Unlocked exactly as the document was at load, so the two graphs hold
        # the same plain values wherever nothing changed. (Once opened with its
        # password a document no longer reports itself encrypted, so the
        # signed revision's own trailer is what says so.)
        from .simple_pdf import CosExtractor

        CosExtractor(
            pristine,
            signed,
            limits=pdf._load_limits,
            budget=pdf._load_budget,
            credential=getattr(pdf, "_credential", None),
        ).unlock(getattr(pdf, "password", None))

    current_writer = PdfCosWriter(pdf._cos_doc)
    pristine_writer = PdfCosWriter(pristine)
    changed: set[int] = set()
    for number in list(pdf._cos_doc.objects):
        obj = pdf._cos_doc.objects.get(number)
        if obj is None:
            continue
        try:
            before = pristine.objects.get(number)
        except PdfParseException:
            before = None  # not readable as signed, so nothing vouches for it
        if before is None or pristine_writer.serialize_object(before) != current_writer.serialize_object(obj):
            changed.add(number)
    return SignedChanges(pdf, pristine, changed)

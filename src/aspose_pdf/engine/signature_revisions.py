"""Strict revision boundaries and conservative DocMDP change validation.

Signature policy must never use the parser's damaged-file recovery. Each
revision is checked separately: restoring a forbidden edit in a later update
does not erase that edit from the document's history.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import pairwise

from aspose_pdf.engine.cos import (
    PdfArray,
    PdfDictionary,
    PdfIndirectReference,
    PdfName,
    PdfNumber,
    PdfStream,
    PdfString,
)
from aspose_pdf.engine.pdf_parser_cos import PdfCosParser
from aspose_pdf.exceptions import PdfParseException

_EOF = re.compile(
    rb"startxref[\x00\t\n\f\r ]+(\d+)[\x00\t\n\f\r ]+%%EOF(?:\r\n|\r|\n)?"
)


def resolve(doc, value):
    return doc.get_object(value) if isinstance(value, PdfIndirectReference) else value


def entry(doc, obj, key):
    obj = resolve(doc, obj)
    return (
        resolve(doc, obj.get(PdfName(key))) if isinstance(obj, PdfDictionary) else None
    )


def fingerprint(value):
    """Compare direct COS values without accidentally following references."""
    if isinstance(value, PdfIndirectReference):
        return ("ref", value.object_number, value.gen_number)
    if isinstance(value, PdfArray):
        return ("array", tuple(map(fingerprint, value.items)))
    if isinstance(value, PdfDictionary):
        pairs = tuple(
            sorted((k.name, fingerprint(v)) for k, v in value.mapping.items())
        )
        return (
            ("stream", pairs, value.content)
            if isinstance(value, PdfStream)
            else ("dict", pairs)
        )
    if isinstance(value, PdfName):
        return ("name", value.name)
    return (type(value).__name__, getattr(value, "value", None))


def insignificant(data):
    return all(
        not line.strip() or line.lstrip().startswith(b"%") for line in data.splitlines()
    )


class _StrictParser(PdfCosParser):
    def _reconstruct_xref(self):
        raise PdfParseException(
            "Signature validation requires an intact cross-reference chain"
        )


@dataclass
class Revision:
    end: int
    xref: int
    doc: object


def parse_revision(data, *, budget, encryption=None):
    parser = _StrictParser(data, budget=budget)
    doc = parser.parse()
    if doc.offset_shift:
        raise ValueError("Signature revision has shifted cross-reference offsets")
    if PdfName("Encrypt") in doc.trailer.mapping:
        if encryption is None:
            raise ValueError("Encrypted signature revisions require a security handler")
        from .encryption import attach_document_decryption

        attach_document_decryption(
            doc,
            encryption.key,
            encryption.algorithm,
            skip=encryption.exempt,
            encrypt_metadata=encryption.encrypt_metadata,
        )
    return doc


def revisions(data, signed_end, *, budget, encryption=None):
    """Return the signed revision and its complete, linked incremental history."""
    matches = []
    for match in _EOF.finditer(data):
        budget.check(
            len(matches) + 1, "max_xref_sections", "signature revision boundaries"
        )
        matches.append(match)
    if not matches or not insignificant(data[matches[-1].end() :]):
        raise ValueError("Unterminated or malformed data after the signed revision")
    by_xref = {}
    for match in matches:
        offset = int(match[1])
        if offset >= match.start():
            raise ValueError("Invalid revision cross-reference offset")
        by_xref.setdefault(offset, []).append(match)
    chain = []
    match = matches[-1]
    seen = set()
    while True:
        offset = int(match[1])
        if offset in seen:
            raise ValueError("Cycle in signature revision chain")
        seen.add(offset)
        end = match.end()
        # A writer may include trailing whitespace in its signed revision.
        if end <= signed_end and insignificant(data[end:signed_end]):
            end = signed_end
        doc = parse_revision(data[:end], budget=budget, encryption=encryption)
        chain.append(Revision(end, offset, doc))
        if end == signed_end:
            return list(reversed(chain))
        if end < signed_end:
            raise ValueError("ByteRange does not end at a linked PDF revision")
        prev = doc.trailer.get(PdfName("Prev"))
        candidates = (
            by_xref.get(int(prev.value), []) if isinstance(prev, PdfNumber) else []
        )
        candidates = [item for item in candidates if item.end() <= offset]
        if len(candidates) != 1:
            raise ValueError(
                "Signed revision is missing from the cross-reference chain"
            )
        match = candidates[0]


def signature_values(doc, budget):
    """Read signature dictionaries through AcroForm, including inherited FT."""
    root = entry(doc, doc.trailer, "Root")
    fields = entry(doc, entry(doc, root, "AcroForm"), "Fields")
    seen = set()
    out = []

    def walk(items, inherited=None, depth=0):
        budget.check(depth, "max_nesting_depth", "signature field depth")
        for ref in items.items if isinstance(items, PdfArray) else ():
            field = resolve(doc, ref)
            if not isinstance(field, PdfDictionary) or id(field) in seen:
                raise ValueError("Invalid or repeated signature field")
            seen.add(id(field))
            budget.check(len(seen), "max_container_items", "signature fields")
            ft = entry(doc, field, "FT") or inherited
            value = entry(doc, field, "V")
            if ft == PdfName("Sig") and isinstance(value, PdfDictionary):
                out.append(value)
            walk(entry(doc, field, "Kids"), ft, depth + 1)

    walk(fields)
    return out


def signature_range(doc, value):
    br = entry(doc, value, "ByteRange")
    if not isinstance(br, PdfArray) or len(br.items) != 4:
        raise ValueError("Invalid signature ByteRange")
    if any(not isinstance(v, PdfNumber) or type(v.value) is not int for v in br.items):
        raise ValueError("Signature ByteRange must contain integers")
    return [v.value for v in br.items]


def covered_signature(data, revision, value, contents, byte_range):
    """Require a real signature whose sole gap is its own hex Contents."""
    from .simple_pdf import _trim_der_padding

    doc = revision.doc
    if signature_range(doc, value) != byte_range:
        return False
    stored = entry(doc, value, "Contents")
    if not isinstance(stored, PdfString) or _trim_der_padding(stored.value) != contents:
        return False
    start, gap_start, gap_end, length = byte_range
    if (
        start != 0
        or not 0 < gap_start < gap_end < revision.end
        or gap_end + length != revision.end
    ):
        return False
    gap = data[gap_start:gap_end]
    if not gap.startswith(b"<") or not gap.endswith(b">") or gap.startswith(b"<<"):
        return False
    try:
        if bytes.fromhex(gap[1:-1].decode("ascii")) != stored.value:
            return False
    except (ValueError, UnicodeError):
        return False
    # Contents must belong to this dictionary, not a matching string elsewhere.
    for number, offset in sorted(
        doc.xref_table.items(), key=lambda item: item[1], reverse=True
    ):
        if 0 < offset < gap_start:
            if doc.objects.get(number) is not value:
                return False
            if not 0 <= offset < gap_start:
                return False
            prefix = data[offset:gap_start]
            if b"endobj" in prefix or not re.search(
                rb"/Contents[\x00\t\n\f\r ]*$", prefix
            ):
                return False
            return True
    return False


def certification_level(revision, contents, byte_range):
    """Read the policy from signed bytes, never from unsigned field metadata."""
    from .simple_pdf import _trim_der_padding

    doc = revision.doc
    root = entry(doc, doc.trailer, "Root")
    value = entry(doc, entry(doc, root, "Perms"), "DocMDP")
    stored = entry(doc, value, "Contents")
    if not isinstance(stored, PdfString) or _trim_der_padding(stored.value) != contents:
        return None
    if signature_range(doc, value) != byte_range:
        raise ValueError("Certification ByteRange differs from the signed policy")
    refs = entry(doc, value, "Reference")
    levels = []
    for ref in refs.items if isinstance(refs, PdfArray) else ():
        if entry(doc, ref, "TransformMethod") == PdfName("DocMDP"):
            p = entry(doc, entry(doc, ref, "TransformParams"), "P")
            level = 2 if p is None else p.value if isinstance(p, PdfNumber) else None
            if type(level) is not int or level not in (1, 2, 3):
                raise ValueError("Invalid signed DocMDP permission level")
            levels.append(level)
    if len(levels) != 1:
        raise ValueError("Certification must contain one DocMDP transform")
    return levels[0]


class _PolicyComparison:
    def __init__(self, old, new, level, budget):
        self.old, self.new, self.level, self.budget = old, new, level, budget
        self.seen = set()
        self.reached = [set(), set()]

    def field_state(self, field, inherited):
        field_type = inherited
        filled = False
        seen = set()
        while isinstance(field, PdfDictionary):
            if id(field) in seen:
                raise ValueError("cycle in form field parents")
            seen.add(id(field))
            self.budget.check(len(seen), "max_nesting_depth", "form field parents")
            field_type = field_type or entry(self.old, field, "FT")
            filled |= entry(self.old, field, "V") is not None
            field = entry(self.old, field, "Parent")
        return field_type, filled

    def mark(self, value, side, depth=0):
        self.budget.check(depth, "max_nesting_depth", "DocMDP object depth")
        doc = (self.old, self.new)[side]
        if isinstance(value, PdfIndirectReference):
            if value.object_number in self.reached[side]:
                return
            self.reached[side].add(value.object_number)
            value = resolve(doc, value)
        if isinstance(value, PdfDictionary):
            for item in value.mapping.values():
                self.mark(item, side, depth + 1)
        elif isinstance(value, PdfArray):
            for item in value.items:
                self.mark(item, side, depth + 1)

    def new_signature(self, ref):
        field = resolve(self.new, ref)
        value = entry(self.new, field, "V")
        if entry(self.new, field, "FT") != PdfName("Sig") or not isinstance(
            value, PdfDictionary
        ):
            return False
        if self.level == 1 and entry(self.new, value, "Type") != PdfName(
            "DocTimeStamp"
        ):
            return False
        # New visible widgets require layout analysis beyond this validator.
        rect = entry(self.new, field, "Rect")
        if rect is not None and (
            not isinstance(rect, PdfArray)
            or len(rect.items) != 4
            or any(not isinstance(v, PdfNumber) or v.value != 0 for v in rect.items)
        ):
            return False
        return set(k.name for k in field.mapping) <= {
            "/FT",
            "/T",
            "/V",
            "/Type",
            "/Subtype",
            "/Rect",
            "/F",
            "/P",
        }

    def compare(self, a, b, mode="fixed", depth=0, inherited=None):
        self.budget.check(depth, "max_nesting_depth", "DocMDP comparison depth")
        for value, side in ((a, 0), (b, 1)):
            if isinstance(value, PdfIndirectReference):
                self.reached[side].add(value.object_number)
        if isinstance(a, PdfIndirectReference) or isinstance(b, PdfIndirectReference):
            if fingerprint(a) != fingerprint(b):
                raise ValueError("protected object reference changed")
            key = (a.object_number, b.object_number, mode, fingerprint(inherited))
            if key in self.seen:
                return
            self.seen.add(key)
            a, b = resolve(self.old, a), resolve(self.new, b)
        if mode == "fields" and a is None:
            a = PdfArray()
        if mode == "acro" and a is None:
            a = PdfDictionary()
        if mode in ("fields", "annots"):
            a_items = a.items if isinstance(a, PdfArray) else []
            b_items = b.items if isinstance(b, PdfArray) else []
            remaining = {fingerprint(v): v for v in b_items}
            if len(remaining) != len(b_items):
                raise ValueError("duplicate form or annotation reference")
            if mode == "fields" or self.level < 3:
                original_order = [fingerprint(v) for v in a_items]
                original_keys = set(original_order)
                retained_order = [
                    fingerprint(v) for v in b_items if fingerprint(v) in original_keys
                ]
                if retained_order != original_order:
                    raise ValueError("existing fields or widgets reordered or removed")
            for old in a_items:
                newer = remaining.pop(fingerprint(old), None)
                widget = entry(self.old, old, "Subtype") == PdfName("Widget")
                if mode == "annots" and self.level == 3 and not widget:
                    self.mark(old, 0)
                    if newer is not None:
                        if entry(self.new, newer, "Subtype") == PdfName("Widget"):
                            raise ValueError("annotation changed into a widget")
                        self.mark(newer, 1)
                    continue
                if newer is None:
                    raise ValueError("existing form field or annotation removed")
                self.compare(
                    old,
                    newer,
                    "field" if mode == "fields" or widget else "fixed",
                    depth + 1,
                    inherited,
                )
            for newer in remaining.values():
                annot_allowed = (
                    mode == "annots"
                    and self.level == 3
                    and entry(self.new, newer, "Subtype") != PdfName("Widget")
                )
                if not annot_allowed and not self.new_signature(newer):
                    raise ValueError("new field or annotation is not permitted")
                self.mark(newer, 1)
            return
        if type(a) is not type(b):
            raise ValueError("protected object type changed")
        if isinstance(a, PdfDictionary):
            if isinstance(a, PdfStream) and a.content != b.content:
                raise ValueError("protected stream content changed")
            field_type = entry(self.old, a, "FT") or inherited
            filled = False
            if mode == "field":
                field_type, filled = self.field_state(a, field_type)
            for key in a.mapping.keys() | b.mapping.keys():
                av, bv = a.get(key), b.get(key)
                name = key.name
                child = "fixed"
                mutable = False
                if mode == "catalog":
                    child = {"/Pages": "pages", "/AcroForm": "acro"}.get(name, child)
                    mutable = name == "/DSS"
                elif mode == "pages":
                    child = {"/Kids": "pages", "/Annots": "annots"}.get(name, child)
                elif mode == "acro":
                    child = "fields" if name == "/Fields" else child
                    if (
                        name == "/SigFlags"
                        and isinstance(bv, PdfNumber)
                        and bv.value == 3
                    ):
                        mutable = True
                elif mode == "field":
                    child = "fields" if name == "/Kids" else child
                    if self.level >= 2:
                        mutable = name in (
                            "/V",
                            "/I",
                            "/AP",
                            "/AS",
                        ) and field_type != PdfName("Sig")
                        if field_type == PdfName("Sig") and not filled:
                            mutable = name in ("/V", "/AP", "/AS")
                if mutable:
                    self.mark(av, 0)
                    self.mark(bv, 1)
                elif name in ("/Parent", "/P") and mode in ("pages", "field"):
                    if fingerprint(av) != fingerprint(bv):
                        raise ValueError("page or field parent changed")
                elif name == "/Data" and entry(
                    self.old, a, "TransformMethod"
                ) == PdfName("DocMDP"):
                    if fingerprint(av) != fingerprint(bv):
                        raise ValueError("certification target changed")
                else:
                    self.compare(av, bv, child, depth + 1, field_type)
            return
        if isinstance(a, PdfArray):
            if len(a.items) != len(b.items):
                raise ValueError("protected array length changed")
            for av, bv in zip(a.items, b.items):
                self.compare(av, bv, mode, depth + 1, inherited)
        elif fingerprint(a) != fingerprint(b):
            raise ValueError("protected document value changed")

    def check(self):
        for key in self.old.trailer.mapping.keys() | self.new.trailer.mapping.keys():
            if key.name in (
                "/Prev",
                "/Size",
                "/XRefStm",
                "/Type",
                "/W",
                "/Index",
                "/Length",
                "/Filter",
                "/DecodeParms",
            ):
                continue
            a, b = self.old.trailer.get(key), self.new.trailer.get(key)
            if (
                key == PdfName("ID")
                and isinstance(a, PdfArray)
                and isinstance(b, PdfArray)
            ):
                a, b = a.items[0], b.items[0]
            self.compare(a, b, "catalog" if key == PdfName("Root") else "fixed")
        for number in set(self.old.objects) | set(self.new.objects):
            if number in self.reached[0] or number in self.reached[1]:
                continue
            a, b = self.old.objects.get(number), self.new.objects.get(number)
            if entry(self.new, b, "Type") in (PdfName("XRef"), PdfName("ObjStm")):
                continue
            if fingerprint(a) != fingerprint(b):
                raise ValueError("unclassified indirect object changed")


def check_docmdp(chain, level, budget):
    for old, new in pairwise(chain):
        try:
            _PolicyComparison(old.doc, new.doc, level, budget).check()
        except ValueError as exc:
            return [
                f"certification level {level} violated after revision {old.end}: {exc}"
            ]
    return []

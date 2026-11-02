"""Bounded catalog URI metadata access without loading page content."""

from __future__ import annotations

import re
import string
from urllib.parse import urlsplit

from aspose_pdf.exceptions import PdfParseException, PdfValidationException
from aspose_pdf.load_limits import _LoadBudget

from .cos import (
    PdfDictionary,
    PdfIndirectReference,
    PdfName,
    PdfNull,
    PdfStream,
    PdfString,
)

_URI_CHARACTERS = frozenset(
    string.ascii_letters + string.digits + "-._~:/?#[]@!$&'()*+,;=%"
)


class _Metadata:
    def __init__(self, document):
        self.pdf = document._engine_pdf
        self.budget = _LoadBudget(document.load_limits)

    def resolve(self, value):
        seen = set()
        while isinstance(value, PdfIndirectReference):
            self.budget.check(
                len(seen) + 1, "max_nesting_depth", "URI metadata reference depth"
            )
            key = value.object_number, value.gen_number
            if key in seen:
                raise PdfParseException("URI metadata reference contains a cycle")
            seen.add(key)
            value = self.pdf._resolve(value)
            if value is None:
                raise PdfParseException("URI metadata reference was not found")
        return value

    def dictionary(self, value):
        if not isinstance(value, PdfDictionary) or isinstance(value, PdfStream):
            raise PdfParseException("URI metadata must be a dictionary")
        self.budget.check(
            len(value.mapping), "max_container_items", "URI metadata dictionary"
        )
        return value

    def catalog(self, *, create=False):
        if self.pdf._cos_doc is None:
            if not create:
                return None
            self.budget.check_objects(2)
            self.pdf._ensure_cos()
        return self.dictionary(
            self.resolve(self.pdf._cos_doc.trailer.mapping.get(PdfName("Root")))
        )


def get_base_uri(document):
    """Read the declared ASCII string; absence and PDF null mean no base."""
    metadata = _Metadata(document)
    root = metadata.catalog()
    if root is None:
        return None
    uri = metadata.resolve(root.mapping.get(PdfName("URI")))
    if uri is None or isinstance(uri, PdfNull):
        return None
    uri = metadata.dictionary(uri)
    value = metadata.resolve(uri.mapping.get(PdfName("Base")))
    if value is None or isinstance(value, PdfNull):
        return None
    if not isinstance(value, PdfString):
        raise PdfParseException("URI Base must be an ASCII string")
    metadata.budget.check(len(value.value), "max_object_bytes", "URI Base string")
    try:
        return value.value.decode("ascii")
    except UnicodeError as exc:
        raise PdfParseException("URI Base must contain ASCII bytes") from exc


def _encoded_base(value, budget):
    if not isinstance(value, str):
        raise TypeError("base_uri must be a string or None")
    budget.check(len(value), "max_object_bytes", "URI Base string")
    if not value or any(char not in _URI_CHARACTERS for char in value):
        raise PdfValidationException(
            "base_uri must be an ASCII URI; percent-encode spaces and non-ASCII characters"
        )
    if re.search(r"%(?![0-9A-Fa-f]{2})", value):
        raise PdfValidationException("base_uri contains an invalid percent escape")
    try:
        parts = urlsplit(value)
        if not parts.scheme or not (parts.netloc or parts.path):
            raise ValueError
        if parts.scheme in {"http", "https", "ftp"} and not parts.hostname:
            raise ValueError
        if parts.netloc:
            if not parts.hostname:
                raise ValueError
            _ = parts.port
    except ValueError as exc:
        raise PdfValidationException(
            "base_uri must be an absolute URI with a valid authority"
        ) from exc
    return value.encode("ascii")


def set_base_uri(document, value):
    """Replace only the catalog's Base entry, preserving shared dictionaries."""
    metadata = _Metadata(document)
    encoded = None if value is None else _encoded_base(value, metadata.budget)
    root = metadata.catalog(create=value is not None)
    if root is None:
        return
    uri = metadata.resolve(root.mapping.get(PdfName("URI")))
    if isinstance(uri, PdfDictionary) and not isinstance(uri, PdfStream):
        metadata.dictionary(uri)
        if value is None and PdfName("Base") not in uri.mapping:
            return
        mapping = dict(uri.mapping)
    else:
        mapping = {}
    if value is None:
        mapping.pop(PdfName("Base"), None)
    else:
        mapping[PdfName("Base")] = PdfString(encoded)
    metadata.budget.check(
        len(mapping), "max_container_items", "URI metadata dictionary"
    )
    if mapping:
        size = len(root.mapping) + int(PdfName("URI") not in root.mapping)
        metadata.budget.check(size, "max_container_items", "URI catalog dictionary")
        root.mapping[PdfName("URI")] = PdfDictionary(mapping)
    else:
        root.mapping.pop(PdfName("URI"), None)

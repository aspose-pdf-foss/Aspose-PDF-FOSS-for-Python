"""Bounded resolution of bundled Adobe predefined CJK CMaps."""

from __future__ import annotations

import hashlib
import json
import math
import zlib
from bisect import bisect_right
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from types import MappingProxyType
from typing import Any

from aspose_pdf.load_limits import PdfLoadLimits, _coerce_limits, _LoadBudget

# The tables are split one file per character collection, behind a small index.
# A document names exactly one collection through its CIDSystemInfo, so only
# that file is ever decompressed; a single combined file would make one CJK
# document pay for all four. Only the index digest is pinned in code — it in
# turn pins each collection file's digest and size.
_INDEX_NAME = "cmaps/predefined_cmaps_index.json.zlib"
_INDEX_SHA256 = "6d8564c57ad67b0e0e9929e40591aa8f2e437198f92150dbad4bb47361fef5c2"
_MAX_INDEX_BYTES = 64 * 1024
_MAX_INDEX_OUTPUT = 256 * 1024
_MAX_BUNDLE_BYTES = 4 * 1024 * 1024
_MAX_BUNDLE_OUTPUT = 16 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class CharacterCollection:
    """A CIDSystemInfo registry, ordering, and supplement triple."""

    registry: str
    ordering: str
    supplement: int


@dataclass(frozen=True, slots=True)
class PredefinedCMap:
    """A resolved predefined CMap and its semantic Unicode mapping."""

    name: str
    collection: CharacterCollection
    vertical: bool
    codespaces: tuple[tuple[bytes, bytes], ...]
    code_to_cid: Mapping[bytes, int]
    code_to_text: Mapping[bytes, str]

    def decode_units(
        self,
        raw: bytes,
        *,
        budget: _LoadBudget | None = None,
    ) -> list[tuple[int, int, int | None, str | None]]:
        """Split show-string bytes into codespace-aware decoded units."""
        units: list[tuple[int, int, int | None, str | None]] = []
        by_length: dict[int, list[tuple[int, int]]] = {}
        for low, high in self.codespaces:
            by_length.setdefault(len(low), []).append(
                (int.from_bytes(low, "big"), int.from_bytes(high, "big"))
            )
        lengths = sorted(by_length)
        offset = 0
        while offset < len(raw):
            matched: bytes | None = None
            for length in lengths:
                if offset + length > len(raw):
                    continue
                candidate = raw[offset : offset + length]
                value = int.from_bytes(candidate, "big")
                if any(low <= value <= high for low, high in by_length[length]):
                    matched = candidate
                    break
            if matched is None:
                length = 1
                cid = None
                text = None
            else:
                length = len(matched)
                cid = self.code_to_cid.get(matched)
                text = self.code_to_text.get(matched) if cid is not None else None
            if budget is not None:
                budget.check(
                    len(units) + 1,
                    "max_container_items",
                    "predefined CMap decoded units",
                )
            units.append((offset, length, cid, text))
            offset += length
        return units


@dataclass(frozen=True, slots=True)
class PredefinedCMapEncoding:
    """Compact code-to-CID view of a predefined CMap.

    This view is used when a font already has a usable ``/ToUnicode`` map. It
    validates codes and supplies metrics without expanding every bundled CMap
    range into a document-sized dictionary.
    """

    name: str
    collection: CharacterCollection
    vertical: bool
    codespaces: tuple[tuple[bytes, bytes], ...]
    _range_layers: tuple[
        tuple[tuple[int, tuple[int, ...], tuple[tuple[int, int], ...]], ...],
        ...,
    ]

    def cid_for(self, code: bytes) -> int | None:
        """Return the CID for *code*, honoring local ``usecmap`` overrides."""
        length = len(code)
        if not any(
            range_length == length
            for layer in self._range_layers
            for range_length, _lows, _endpoints in layer
        ):
            return None
        value = int.from_bytes(code, "big")
        for layer in self._range_layers:
            for range_length, lows, endpoints in layer:
                if range_length != length:
                    continue
                index = bisect_right(lows, value) - 1
                if index < 0:
                    break
                high, first_cid = endpoints[index]
                if value <= high:
                    return first_cid + value - lows[index]
                break
        return None

    def decode_units(
        self,
        raw: bytes,
        *,
        budget: _LoadBudget | None = None,
    ) -> list[tuple[int, int, int | None]]:
        """Split show-string bytes into codespace-aware code/CID units."""
        by_length: dict[int, list[tuple[int, int]]] = {}
        for low, high in self.codespaces:
            by_length.setdefault(len(low), []).append(
                (int.from_bytes(low, "big"), int.from_bytes(high, "big"))
            )
        lengths = sorted(by_length)
        units: list[tuple[int, int, int | None]] = []
        offset = 0
        while offset < len(raw):
            matched: bytes | None = None
            for length in lengths:
                if offset + length > len(raw):
                    continue
                candidate = raw[offset : offset + length]
                value = int.from_bytes(candidate, "big")
                if any(low <= value <= high for low, high in by_length[length]):
                    matched = candidate
                    break
            length = len(matched) if matched is not None else 1
            cid = self.cid_for(matched) if matched is not None else None
            if budget is not None:
                budget.check(
                    len(units) + 1,
                    "max_container_items",
                    "predefined CMap decoded units",
                )
            units.append((offset, length, cid))
            offset += length
        return units


def _active_budget(
    limits: PdfLoadLimits | None,
    budget: _LoadBudget | None,
) -> _LoadBudget:
    if budget is None:
        return _LoadBudget(_coerce_limits(limits))
    if not isinstance(budget, _LoadBudget):
        raise TypeError("budget must be a _LoadBudget instance or None")
    if limits is not None and limits != budget.limits:
        raise ValueError("limits must match budget.limits")
    return budget


def _ascii_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value.lstrip("/")
    if isinstance(value, (bytes, bytearray)):
        try:
            return bytes(value).decode("ascii").lstrip("/")
        except UnicodeDecodeError:
            return None
    return None


def character_collection(value: Any) -> CharacterCollection | None:
    """Normalize a plain CIDSystemInfo dictionary, or return ``None``."""
    if not isinstance(value, Mapping):
        return None
    registry = _ascii_text(value.get("Registry"))
    ordering = _ascii_text(value.get("Ordering"))
    supplement_value = value.get("Supplement")
    supplement: int | None = None
    if isinstance(supplement_value, int) and not isinstance(supplement_value, bool):
        supplement = supplement_value
    elif (
        isinstance(supplement_value, float)
        and math.isfinite(supplement_value)
        and supplement_value.is_integer()
    ):
        supplement = int(supplement_value)
    if registry is None or ordering is None or supplement is None or supplement < 0:
        return None
    return CharacterCollection(registry, ordering, supplement)


def _read_bundle_file(
    name: str,
    *,
    digest: str,
    max_compressed: int,
    max_output: int,
) -> dict[str, Any]:
    """Read, verify and decode one bundled data file under fixed bounds."""
    compressed = (
        resources.files("aspose_pdf.engine.data").joinpath(name).read_bytes()
    )
    if len(compressed) > max_compressed:
        raise RuntimeError("Bundled predefined CMap data is unexpectedly large")
    if hashlib.sha256(compressed).hexdigest() != digest:
        raise RuntimeError("Bundled predefined CMap data failed its integrity check")
    decompressor = zlib.decompressobj()
    raw = decompressor.decompress(compressed, max_output + 1)
    if len(raw) > max_output or decompressor.unconsumed_tail:
        raise RuntimeError("Bundled predefined CMap data exceeds its output bound")
    raw += decompressor.flush()
    if len(raw) > max_output:
        raise RuntimeError("Bundled predefined CMap data exceeds its output bound")
    payload = json.loads(raw)
    if not isinstance(payload, dict) or payload.get("format") != 2:
        raise RuntimeError("Unsupported bundled predefined CMap data format")
    return payload


@lru_cache(maxsize=1)
def _index() -> dict[str, Any]:
    payload = _read_bundle_file(
        _INDEX_NAME,
        digest=_INDEX_SHA256,
        max_compressed=_MAX_INDEX_BYTES,
        max_output=_MAX_INDEX_OUTPUT,
    )
    if not isinstance(payload.get("collections"), dict):
        raise RuntimeError("Bundled predefined CMap index is malformed")
    return payload


@lru_cache(maxsize=1)
def _cmap_orderings() -> Mapping[str, str]:
    """Map every bundled CMap name to the collection file that holds it."""
    owners: dict[str, str] = {}
    for ordering, entry in _index()["collections"].items():
        for name in entry.get("cmaps", ()):
            owners[str(name)] = str(ordering)
    return MappingProxyType(owners)


@lru_cache(maxsize=4)
def _collection_bundle(ordering: str) -> dict[str, Any]:
    entry = _index()["collections"].get(ordering)
    if not isinstance(entry, dict):
        raise KeyError(ordering)
    payload = _read_bundle_file(
        f"cmaps/{entry['file']}",
        digest=str(entry["sha256"]),
        max_compressed=min(int(entry["compressed_bytes"]), _MAX_BUNDLE_BYTES),
        max_output=min(int(entry["output_bytes"]), _MAX_BUNDLE_OUTPUT),
    )
    if payload.get("ordering") != ordering:
        raise RuntimeError("Bundled predefined CMap file does not match its index")
    return payload


def _cmap_entry(name: str) -> dict[str, Any] | None:
    """Return one CMap's table, loading only its own collection file."""
    ordering = _cmap_orderings().get(name)
    if ordering is None:
        return None
    entry = _collection_bundle(ordering)["cmaps"].get(name)
    return entry if isinstance(entry, dict) else None


def supported_cmap_names() -> tuple[str, ...]:
    """Return the exact allowlist of bundled predefined CMap names.

    Answered from the index alone: this is called while parsing every composite
    font, and must not pull a collection's tables into memory.
    """
    return tuple(sorted(_cmap_orderings()))


def _range_index(entry: Mapping[str, Any]) -> tuple[
    tuple[int, tuple[int, ...], tuple[tuple[int, int], ...]], ...
]:
    by_length: dict[int, list[tuple[int, int, int]]] = {}
    for length, low, high, first_cid in entry.get("ranges", ()):
        by_length.setdefault(int(length), []).append(
            (int(low), int(high), int(first_cid))
        )
    result = []
    for length, ranges in sorted(by_length.items()):
        ranges.sort()
        result.append(
            (
                length,
                tuple(low for low, _high, _cid in ranges),
                tuple((high, cid) for _low, high, cid in ranges),
            )
        )
    return tuple(result)


def _encoding_layout(
    name: str,
    *,
    visiting: frozenset[str] = frozenset(),
) -> tuple[
    tuple[tuple[bytes, bytes], ...],
    tuple[
        tuple[tuple[int, tuple[int, ...], tuple[tuple[int, int], ...]], ...],
        ...,
    ],
]:
    entry = _cmap_entry(name)
    if entry is None:
        raise KeyError(name)
    if name in visiting:
        raise RuntimeError("Bundled predefined CMap usecmap cycle")

    codespaces: tuple[tuple[bytes, bytes], ...] = ()
    layers = [_range_index(entry)]
    local_spaces = entry.get("codespaces")
    if isinstance(local_spaces, list) and local_spaces:
        codespaces = tuple(
            (
                int(low).to_bytes(int(length), "big"),
                int(high).to_bytes(int(length), "big"),
            )
            for length, low, high in local_spaces
        )
    base = entry.get("base")
    if isinstance(base, str):
        base_spaces, base_layers = _encoding_layout(
            base,
            visiting=visiting | {name},
        )
        if not codespaces:
            codespaces = base_spaces
        layers.extend(base_layers)
    return codespaces, tuple(layers)


@lru_cache(maxsize=16)
def _encoding_cached(name: str) -> PredefinedCMapEncoding | None:
    entry = _cmap_entry(name)
    if entry is None:
        return None
    codespaces, layers = _encoding_layout(name)
    if not codespaces:
        return None
    return PredefinedCMapEncoding(
        name=name,
        collection=CharacterCollection(
            str(entry["registry"]),
            str(entry["ordering"]),
            int(entry["supplement"]),
        ),
        vertical=int(entry["wmode"]) == 1,
        codespaces=codespaces,
        _range_layers=layers,
    )


def _expand_cmap(
    name: str,
    *,
    visiting: frozenset[str] = frozenset(),
) -> tuple[dict[bytes, int], tuple[tuple[bytes, bytes], ...], dict[str, Any]]:
    entry = _cmap_entry(name)
    if entry is None:
        raise KeyError(name)
    if name in visiting:
        raise RuntimeError("Bundled predefined CMap usecmap cycle")

    mapping: dict[bytes, int] = {}
    codespaces: tuple[tuple[bytes, bytes], ...] = ()
    base = entry.get("base")
    if isinstance(base, str):
        mapping, codespaces, _base_entry = _expand_cmap(
            base,
            visiting=visiting | {name},
        )

    local_spaces = entry.get("codespaces")
    if isinstance(local_spaces, list) and local_spaces:
        parsed_spaces: list[tuple[bytes, bytes]] = []
        for length, low, high in local_spaces:
            parsed_spaces.append(
                (int(low).to_bytes(int(length), "big"), int(high).to_bytes(int(length), "big"))
            )
        codespaces = tuple(parsed_spaces)

    for length, low, high, first_cid in entry.get("ranges", ()):
        length = int(length)
        low = int(low)
        high = int(high)
        first_cid = int(first_cid)
        for offset, code in enumerate(range(low, high + 1)):
            mapping[code.to_bytes(length, "big")] = first_cid + offset
    return mapping, codespaces, entry


def cid_to_unicode_text(ordering: str, cid: int) -> str | None:
    """Return the Unicode text Adobe maps *cid* to in *ordering*, or ``None``.

    Reads the bundled ``<Ordering>-UCS2`` mapping for the character collection,
    which is what lets a composite font with no embedded program be drawn (or
    searched) through a substitute face indexed by Unicode.
    """
    if not ordering or cid < 0:
        return None
    return _lookup_collection_text(ordering, cid)


def _lookup_collection_text(ordering: str, cid: int) -> str | None:
    try:
        entry = _collection_bundle(ordering)["collection"]
    except (KeyError, RuntimeError):
        return None
    if not isinstance(entry, dict):
        return None
    ranges = entry.get("ranges", ())
    low = 0
    high = len(ranges)
    while low < high:
        middle = (low + high) // 2
        first, last, first_codepoint = ranges[middle]
        if cid < first:
            high = middle
        elif cid > last:
            low = middle + 1
        else:
            return chr(first_codepoint + cid - first)
    values = entry.get("values", ())
    low = 0
    high = len(values)
    while low < high:
        middle = (low + high) // 2
        item_cid, text = values[middle]
        if cid < item_cid:
            high = middle
        elif cid > item_cid:
            low = middle + 1
        else:
            return str(text)
    return None


# Unicode-keyed CMap families: the code *is* the character, in the encoding the
# name states. Taking the scalar from the code keeps text and code a bijection,
# so a replacement can be written back; going through CID -> Unicode instead is
# many-to-one (Adobe maps both U+2F47 and U+65E5 to Japan1 CID 3284) and would
# make those characters unencodable.
_UNICODE_CODE_ENCODINGS = (
    ("-UTF16-", "utf-16-be"),
    ("-UCS2-", "utf-16-be"),
    ("-UTF8-", "utf-8"),
    ("-UTF32-", "utf-32-be"),
)


def _semantic_text(
    name: str,
    code: bytes,
    cid: int,
    ordering: str,
    horizontal_mapping: Mapping[bytes, int],
) -> str | None:
    for marker, encoding in _UNICODE_CODE_ENCODINGS:
        if marker in name:
            try:
                return code.decode(encoding)
            except UnicodeDecodeError:
                return None
    semantic_cid = horizontal_mapping.get(code, cid)
    return _lookup_collection_text(ordering, semantic_cid)


@lru_cache(maxsize=16)
def _resolve_cached(name: str) -> PredefinedCMap | None:
    if _cmap_entry(name) is None:
        return None
    mapping, codespaces, entry = _expand_cmap(name)
    if not codespaces:
        return None
    base_name = entry.get("base")
    if isinstance(base_name, str):
        horizontal_mapping, _spaces, _entry = _expand_cmap(base_name)
    else:
        horizontal_mapping = mapping
    ordering = str(entry["ordering"])
    code_to_text: dict[bytes, str] = {}
    for code, cid in mapping.items():
        text = _semantic_text(name, code, cid, ordering, horizontal_mapping)
        if text is not None:
            code_to_text[code] = text
    collection = CharacterCollection(
        str(entry["registry"]),
        ordering,
        int(entry["supplement"]),
    )
    return PredefinedCMap(
        name=name,
        collection=collection,
        vertical=int(entry["wmode"]) == 1,
        codespaces=codespaces,
        code_to_cid=MappingProxyType(mapping),
        code_to_text=MappingProxyType(code_to_text),
    )


def _declared_mapping_count(
    name: str,
    *,
    visiting: frozenset[str] = frozenset(),
) -> int:
    """Return a conservative mapping count without expanding any code range."""
    entry = _cmap_entry(name)
    if entry is None or name in visiting:
        return 0
    total = sum(
        max(int(high) - int(low) + 1, 0)
        for _length, low, high, _first_cid in entry.get("ranges", ())
    )
    base = entry.get("base")
    if isinstance(base, str):
        total += _declared_mapping_count(
            base,
            visiting=visiting | {name},
        )
    return total


def resolve_predefined_cmap_encoding(
    name: Any,
    collection: CharacterCollection | None,
    *,
    limits: PdfLoadLimits | None = None,
    budget: _LoadBudget | None = None,
) -> PredefinedCMapEncoding | None:
    """Resolve a compact predefined CMap encoding view.

    Unlike :func:`resolve_predefined_cmap`, this does not build the complete
    code-to-CID or Unicode dictionaries. It is suitable when ``/ToUnicode``
    already supplies the bounded set of document-controlled character codes.
    """
    normalized_name = _ascii_text(name)
    if normalized_name is None or collection is None:
        return None
    entry = _cmap_entry(normalized_name)
    if entry is None:
        return None
    if (
        entry.get("registry") != collection.registry
        or entry.get("ordering") != collection.ordering
    ):
        return None
    resolved = _encoding_cached(normalized_name)
    if resolved is None:
        return None
    active_budget = _active_budget(limits, budget)
    active_budget.check(
        len(resolved.codespaces),
        "max_container_items",
        "predefined CMap codespaces",
    )
    return resolved


def resolve_predefined_cmap(
    name: Any,
    collection: CharacterCollection | None,
    *,
    limits: PdfLoadLimits | None = None,
    budget: _LoadBudget | None = None,
) -> PredefinedCMap | None:
    """Resolve an exact bundled CMap compatible with *collection*.

    Unknown names, missing CIDSystemInfo, and registry/ordering mismatches are
    unsupported and return ``None``. The PDF never controls a resource path.
    """
    normalized_name = _ascii_text(name)
    if normalized_name is None or collection is None:
        return None
    entry = _cmap_entry(normalized_name)
    if entry is None:
        return None
    if (
        entry.get("registry") != collection.registry
        or entry.get("ordering") != collection.ordering
    ):
        return None
    active_budget = _active_budget(limits, budget)
    active_budget.check(
        _declared_mapping_count(normalized_name),
        "max_container_items",
        "predefined CMap declared mappings",
    )
    resolved = _resolve_cached(normalized_name)
    if resolved is None:
        return None
    active_budget.check(
        len(resolved.code_to_cid),
        "max_container_items",
        "predefined CMap code-to-CID mappings",
    )
    active_budget.check(
        len(resolved.codespaces),
        "max_container_items",
        "predefined CMap codespaces",
    )
    return resolved

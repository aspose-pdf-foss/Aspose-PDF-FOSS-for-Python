"""Small content-stream text editing helpers.

The functions here intentionally edit only straightforward PDF text-showing
operands. They are a conservative base for public replace/redact APIs and avoid
guessing at layout, shaping, or font-specific CMap rewrites.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from aspose_pdf.exceptions import PdfValidationException

_WHITESPACE = b" \t\n\r\x0c"
_DELIMITERS = b"()<>[]{}/%"
_TEXT_SHOW_OPS = {"Tj", "'", '"'}
_ALL_SHOW_OPS = _TEXT_SHOW_OPS | {"TJ"}
_LINE_SHOW_OPS = {"'", '"'}

# Operators that may appear between two text-showing operators without breaking
# the logical text run: they change neither the font, the spacing/scale, nor the
# pen position. A run interrupted by anything else (positioning, font, CTM or
# graphics-state save/restore) starts a fresh run.
_NEUTRAL_OPS = frozenset(
    {
        # colour selection
        "g", "G", "rg", "RG", "k", "K", "cs", "CS", "sc", "scn", "SC", "SCN",
        # non-text graphics state (no effect on glyph metrics or pen position)
        "gs", "ri", "i", "j", "J", "M", "d", "w", "Tr",
        # marked content (positionally neutral; common in tagged PDFs)
        "BMC", "BDC", "EMC", "DP", "MP",
        # dict delimiters of a BDC/DP property list
        "<<", ">>",
    }
)


@dataclass(frozen=True)
class _Token:
    kind: str
    value: Any
    start: int
    end: int
    style: str = ""


def replace_text_in_content(
    content: bytes,
    search: str,
    replacement: str,
    *,
    case_sensitive: bool = True,
    max_count: int = 0,
) -> tuple[bytes, int]:
    """Replace text inside PDF text-showing operands.

    ``max_count=0`` means unlimited. Replacement is attempted for literal and
    hex string operands used by ``Tj``, ``'``, ``"`` and inside ``TJ`` arrays,
    where the string elements are matched as one logical string. Consecutive
    text-showing operators (e.g. two adjacent ``Tj``, or a ``Tj`` followed by a
    ``TJ``) separated only by positionally-neutral operators are likewise joined
    into one logical run, so a phrase split across several show operators —
    common with kerning or per-word painting — is rewritten across the boundary:
    the replacement is placed in the first matched element and the remaining
    matched characters are removed from the others. A line-moving operator
    (``'``/``"``) or any positioning, font or CTM change starts a new run.
    """
    _validate_edit_args(search, max_count)
    tokens = _lex(content)
    groups = _group_show_runs(tokens)
    replacements: list[tuple[int, int, bytes]] = []
    total = 0

    for segs in groups:
        if max_count and total >= max_count:
            break
        remaining = 0 if max_count == 0 else max(max_count - total, 0)
        edits, count = _edit_string_group(
            segs,
            search,
            replacement,
            case_sensitive=case_sensitive,
            max_count=remaining,
        )
        if count:
            replacements.extend(edits)
            total += count

    if not replacements:
        return content, 0
    return _apply_replacements(content, replacements), total


def _is_number_word(value: str) -> bool:
    try:
        float(value)
        return True
    except ValueError:
        return False


def _group_show_runs(tokens: list[_Token]) -> list[list[_Token]]:
    """Group consecutive text-showing operators into logical-string runs.

    Inside a ``BT``/``ET`` block a run is a maximal sequence of show operators
    separated only by neutral operators (colour or minor graphics state that
    changes neither the font, spacing/scale nor pen position). The string
    operands of every show operator in the run are concatenated, so a phrase
    split across several operators is matched as one string. A line-moving show
    operator (``'``/``"``) or any positioning/font/CTM/state operator starts a
    new run. Each returned list holds the run's string-operand tokens in order.
    """
    groups: list[list[_Token]] = []
    current: list[_Token] = []
    in_text = False
    broke = True  # a run boundary currently separates us from ``current``

    def flush() -> None:
        nonlocal current
        if current:
            groups.append(current)
            current = []

    for idx, token in enumerate(tokens):
        if token.kind != "word":
            continue  # operands belong to the operator that follows them
        value = token.value
        if value == "BT":
            flush()
            in_text = True
            broke = True
            continue
        if value == "ET":
            flush()
            in_text = False
            broke = True
            continue
        if not in_text:
            continue
        if value in _ALL_SHOW_OPS:
            segs = _show_operator_strings(tokens, idx, value)
            if value in _LINE_SHOW_OPS or broke:
                flush()
                current = list(segs)
            else:
                current.extend(segs)
            broke = False
            continue
        if value in _NEUTRAL_OPS or _is_number_word(value):
            continue  # does not break the current run
        broke = True  # any other operator is a run boundary

    flush()
    return groups


def _show_operator_strings(
    tokens: list[_Token], idx: int, op: str
) -> list[_Token]:
    """Return the string operand(s) painted by the show operator at *idx*.

    A single string for ``Tj``/``'``/``"``; every string element of the array
    for ``TJ`` (so a phrase spanning elements can be matched as one string).
    """
    if op in _TEXT_SHOW_OPS:
        prev = _previous_token(tokens, idx)
        return [prev] if prev is not None and prev.kind == "string" else []
    if op == "TJ":
        prev = _previous_token(tokens, idx)
        if prev is None or prev.kind != "array_end":
            return []
        start_idx = _matching_array_start(tokens, idx - 1)
        if start_idx is None:
            return []
        return [t for t in tokens[start_idx + 1 : idx - 1] if t.kind == "string"]
    return []


def redact_text_in_content(
    content: bytes,
    search: str,
    *,
    case_sensitive: bool = True,
    max_count: int = 0,
) -> tuple[bytes, int]:
    """Remove text from simple text-showing operands."""
    return replace_text_in_content(
        content,
        search,
        "",
        case_sensitive=case_sensitive,
        max_count=max_count,
    )


def _validate_edit_args(search: str, max_count: int) -> None:
    if not isinstance(search, str):
        raise TypeError("search must be a string")
    if search == "":
        raise ValueError("search must not be empty")
    if int(max_count) < 0:
        raise ValueError("max_count must be greater than or equal to zero")


def _previous_token(tokens: list[_Token], index: int) -> Optional[_Token]:
    return tokens[index - 1] if index > 0 else None


def _matching_array_start(tokens: list[_Token], end_index: int) -> Optional[int]:
    depth = 0
    for i in range(end_index, -1, -1):
        token = tokens[i]
        if token.kind == "array_end":
            depth += 1
        elif token.kind == "array_start":
            depth -= 1
            if depth == 0:
                return i
    return None


def _find_matches(
    full: str, search: str, case_sensitive: bool, max_count: int
) -> list[tuple[int, int]]:
    """Return non-overlapping ``(start, end)`` match spans in *full*."""
    spans: list[tuple[int, int]] = []
    if case_sensitive:
        pos = 0
        step = len(search)
        while True:
            j = full.find(search, pos)
            if j < 0:
                break
            spans.append((j, j + step))
            pos = j + step
            if max_count and len(spans) >= max_count:
                break
    else:
        for m in re.finditer(re.escape(search), full, re.IGNORECASE):
            spans.append((m.start(), m.end()))
            if max_count and len(spans) >= max_count:
                break
    return spans


def _edit_string_group(
    segs: list[_Token],
    search: str,
    replacement: str,
    *,
    case_sensitive: bool,
    max_count: int,
) -> tuple[list[tuple[int, int, bytes]], int]:
    """Match *search* across the concatenation of *segs* and rewrite them.

    The matched characters are removed from every element they cover and the
    replacement is injected into the element that holds the match start, so a
    phrase split across ``TJ`` elements is replaced in place. Each element is
    re-encoded with its own literal/hex style and Latin-1/UTF-16BE encoding;
    untouched elements are left byte-for-byte intact.
    """
    decoded = [_decode_operand(s.value) for s in segs]
    texts = [d[0] for d in decoded]
    full = "".join(texts)
    if not full:
        return [], 0
    spans = _find_matches(full, search, case_sensitive, max_count)
    if not spans:
        return [], 0

    char_seg: list[int] = []
    for si, text in enumerate(texts):
        char_seg.extend([si] * len(text))

    rebuilt = ["" for _ in segs]
    i = 0
    mi = 0
    n = len(full)
    while i < n:
        if mi < len(spans) and i == spans[mi][0]:
            rebuilt[char_seg[i]] += replacement
            i = spans[mi][1]
            mi += 1
        else:
            rebuilt[char_seg[i]] += full[i]
            i += 1

    edits: list[tuple[int, int, bytes]] = []
    for si, seg in enumerate(segs):
        if rebuilt[si] == texts[si]:
            continue  # leave unmatched elements byte-for-byte intact
        new_bytes = _format_operand(
            _encode_operand(rebuilt[si], decoded[si][1]), seg.style
        )
        edits.append((seg.start, seg.end, new_bytes))
    return edits, len(spans)


def _decode_operand(raw: bytes) -> tuple[str, str]:
    if raw.startswith(b"\xfe\xff"):
        return raw[2:].decode("utf-16-be", errors="replace"), "utf-16-be-bom"
    return raw.decode("latin-1"), "latin-1"


def _encode_operand(text: str, encoding: str) -> bytes:
    if encoding == "utf-16-be-bom":
        return b"\xfe\xff" + text.encode("utf-16-be")
    try:
        return text.encode("latin-1")
    except UnicodeEncodeError as exc:
        raise PdfValidationException(
            "Replacement text must be encodable as Latin-1 for this content stream."
        ) from exc


def _format_operand(raw: bytes, style: str) -> bytes:
    if style == "hex":
        return b"<" + raw.hex().upper().encode("ascii") + b">"
    return _literal_string(raw)


def _literal_string(raw: bytes) -> bytes:
    out = bytearray(b"(")
    for byte in raw:
        if byte == 0x0A:
            out.extend(b"\\n")
        elif byte == 0x0D:
            out.extend(b"\\r")
        elif byte == 0x09:
            out.extend(b"\\t")
        elif byte == 0x08:
            out.extend(b"\\b")
        elif byte == 0x0C:
            out.extend(b"\\f")
        elif byte in (0x28, 0x29, 0x5C):
            out.append(0x5C)
            out.append(byte)
        elif byte < 0x20 or byte > 0x7E:
            out.extend(f"\\{byte:03o}".encode("ascii"))
        else:
            out.append(byte)
    out.append(0x29)
    return bytes(out)


def _apply_replacements(
    content: bytes, replacements: Iterable[tuple[int, int, bytes]]
) -> bytes:
    out = bytearray(content)
    for start, end, value in sorted(replacements, key=lambda item: item[0], reverse=True):
        out[start:end] = value
    return bytes(out)


def _lex(data: bytes) -> list[_Token]:
    tokens: list[_Token] = []
    i = 0
    n = len(data)
    while i < n:
        byte = data[i]
        if byte in _WHITESPACE:
            i += 1
            continue
        if byte == 0x25:
            i = _skip_comment(data, i)
            continue
        if byte == 0x28:
            token, i = _read_literal(data, i)
            tokens.append(token)
            continue
        if byte == 0x3C:
            if i + 1 < n and data[i + 1] == 0x3C:
                tokens.append(_Token("word", "<<", i, i + 2))
                i += 2
                continue
            token, i = _read_hex(data, i)
            tokens.append(token)
            continue
        if byte == 0x3E:
            if i + 1 < n and data[i + 1] == 0x3E:
                tokens.append(_Token("word", ">>", i, i + 2))
                i += 2
            else:
                i += 1
            continue
        if byte == 0x5B:
            tokens.append(_Token("array_start", "[", i, i + 1))
            i += 1
            continue
        if byte == 0x5D:
            tokens.append(_Token("array_end", "]", i, i + 1))
            i += 1
            continue
        if byte == 0x2F:
            token, i = _read_name(data, i)
            tokens.append(token)
            continue
        token, i = _read_word(data, i)
        tokens.append(token)
    return tokens


def _skip_comment(data: bytes, i: int) -> int:
    n = len(data)
    while i < n and data[i] not in (0x0A, 0x0D):
        i += 1
    return i


def _read_literal(data: bytes, i: int) -> tuple[_Token, int]:
    start = i
    i += 1
    depth = 1
    out = bytearray()
    n = len(data)
    while i < n and depth > 0:
        byte = data[i]
        if byte == 0x5C:
            i += 1
            if i >= n:
                break
            esc = data[i]
            if esc == 0x6E:
                out.append(0x0A)
            elif esc == 0x72:
                out.append(0x0D)
            elif esc == 0x74:
                out.append(0x09)
            elif esc == 0x62:
                out.append(0x08)
            elif esc == 0x66:
                out.append(0x0C)
            elif esc in (0x28, 0x29, 0x5C):
                out.append(esc)
            elif esc in (0x0A, 0x0D):
                if esc == 0x0D and i + 1 < n and data[i + 1] == 0x0A:
                    i += 1
            elif 0x30 <= esc <= 0x37:
                digits = bytearray([esc])
                for _ in range(2):
                    if i + 1 < n and 0x30 <= data[i + 1] <= 0x37:
                        i += 1
                        digits.append(data[i])
                    else:
                        break
                out.append(int(digits.decode("ascii"), 8) & 0xFF)
            else:
                out.append(esc)
        elif byte == 0x28:
            depth += 1
            out.append(byte)
        elif byte == 0x29:
            depth -= 1
            if depth > 0:
                out.append(byte)
        else:
            out.append(byte)
        i += 1
    return _Token("string", bytes(out), start, i, "literal"), i


def _read_hex(data: bytes, i: int) -> tuple[_Token, int]:
    start = i
    i += 1
    chars = bytearray()
    n = len(data)
    while i < n:
        byte = data[i]
        if byte == 0x3E:
            i += 1
            break
        if byte not in _WHITESPACE:
            chars.append(byte)
        i += 1
    if len(chars) % 2:
        chars.append(0x30)
    try:
        value = bytes.fromhex(chars.decode("ascii"))
    except ValueError:
        value = b""
    return _Token("string", value, start, i, "hex"), i


def _read_name(data: bytes, i: int) -> tuple[_Token, int]:
    start = i
    i += 1
    n = len(data)
    while i < n and data[i] not in _WHITESPACE + _DELIMITERS:
        i += 1
    return _Token("name", data[start:i].decode("latin-1"), start, i), i


def _read_word(data: bytes, i: int) -> tuple[_Token, int]:
    start = i
    n = len(data)
    while i < n and data[i] not in _WHITESPACE + _DELIMITERS:
        i += 1
    return _Token("word", data[start:i].decode("latin-1"), start, i), i

"""Content Stream Parser Module.

Implements a minimal PDF content‑stream parser capable of extracting text
from a page's content stream.  The implementation follows the subset of the
PDF text operators required by the SDK.
"""

from __future__ import annotations

import codecs
import math
import re
from collections import deque
from decimal import Decimal
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

from aspose_pdf.exceptions import (
    CONTENT_PARSER_RECOVERABLE,
    PdfResourceLimitException,
)
from aspose_pdf.load_limits import PdfLoadLimits, _LoadBudget, _coerce_limits

from .pdf_matrix import (
    identity_affine_decimal,
    multiply_pdf_affine,
    pdf_scalar_to_decimal,
)
from .predefined_cmaps import (
    PredefinedCMap,
    PredefinedCMapEncoding,
    character_collection,
    resolve_predefined_cmap,
    resolve_predefined_cmap_encoding,
    supported_cmap_names,
)


def _resolve_resource_budget(
    limits: PdfLoadLimits | None,
    budget: _LoadBudget | None,
) -> _LoadBudget:
    """Return a validated budget for a font-resource parsing operation."""
    if budget is None:
        return _LoadBudget(_coerce_limits(limits))
    if not isinstance(budget, _LoadBudget):
        raise TypeError("budget must be a _LoadBudget instance or None")
    if limits is not None and limits != budget.limits:
        raise ValueError("limits must match budget.limits")
    return budget


def _iter_cmap_lines(text: str) -> Iterator[str]:
    """Yield CR/LF-delimited lines without first materializing every line."""
    start = 0
    i = 0
    n = len(text)
    while i < n:
        if text[i] not in "\r\n":
            i += 1
            continue
        yield text[start:i]
        if text[i] == "\r" and i + 1 < n and text[i + 1] == "\n":
            i += 1
        i += 1
        start = i
    if start < n:
        yield text[start:]


def _cmap_lines(
    text: str,
    budget: _LoadBudget,
    context: str,
) -> List[str]:
    """Return bounded, nonempty CMap lines with comments removed."""
    lines: List[str] = []
    for raw in _iter_cmap_lines(text):
        if "%" in raw:
            raw = raw.split("%", 1)[0]
        stripped = raw.strip()
        if not stripped:
            continue
        budget.check(
            len(lines) + 1,
            "max_container_items",
            f"{context} nonempty lines",
        )
        lines.append(stripped)
    return lines


def _check_cmap_input(
    cmap_bytes: bytes,
    budget: _LoadBudget,
    context: str,
) -> None:
    budget.check(
        len(cmap_bytes),
        "max_decoded_stream_bytes",
        f"{context} bytes",
    )


def _put_bounded(
    target: Dict[Any, Any],
    key: Any,
    value: Any,
    budget: _LoadBudget,
    context: str,
) -> None:
    if key not in target:
        budget.check(
            len(target) + 1,
            "max_container_items",
            context,
        )
    target[key] = value


def _add_bounded(
    target: set[int],
    value: int,
    budget: _LoadBudget,
    context: str,
) -> None:
    if value not in target:
        budget.check(
            len(target) + 1,
            "max_container_items",
            context,
        )
    target.add(value)


def load_cid_widths(
    w_obj: Any,
    *,
    limits: PdfLoadLimits | None = None,
    budget: _LoadBudget | None = None,
) -> Dict[int, int]:
    """Parse a CIDFont /W array (plain Python lists) into code -> width."""
    active_budget = _resolve_resource_budget(limits, budget)
    out: Dict[int, int] = {}
    if not isinstance(w_obj, list):
        return out
    active_budget.check(1, "max_nesting_depth", "CID width array nesting")
    active_budget.check(
        len(w_obj),
        "max_container_items",
        "CID width array items",
    )
    i = 0
    n = len(w_obj)
    while i < n:
        first = w_obj[i]
        if i + 1 >= n:
            break
        second = w_obj[i + 1]
        if isinstance(second, list):
            active_budget.check(2, "max_nesting_depth", "CID width array nesting")
            active_budget.check(
                len(second),
                "max_container_items",
                "CID width subarray items",
            )
            if isinstance(first, (int, float)):
                code0 = int(first)
                for j, w in enumerate(second):
                    if isinstance(w, (int, float)):
                        _put_bounded(
                            out,
                            code0 + j,
                            int(w),
                            active_budget,
                            "CID width mappings",
                        )
            i += 2
        elif i + 2 < n:
            third = w_obj[i + 2]
            if (
                isinstance(first, (int, float))
                and isinstance(second, (int, float))
                and isinstance(third, (int, float))
            ):
                c1, c2, w = int(first), int(second), int(third)
                if c2 >= c1:
                    active_budget.check(
                        c2 - c1 + 1,
                        "max_container_items",
                        "CID width range entries",
                    )
                    for code in range(c1, c2 + 1):
                        _put_bounded(
                            out,
                            code,
                            w,
                            active_budget,
                            "CID width mappings",
                        )
            i += 3
        else:
            i += 1
    return out


def load_cid_vertical_metrics(
    w2_obj: Any,
    *,
    limits: PdfLoadLimits | None = None,
    budget: _LoadBudget | None = None,
) -> Dict[int, tuple[float, float, float]]:
    """Parse a CIDFont /W2 array into CID -> ``(w1y, v1x, v1y)``."""
    active_budget = _resolve_resource_budget(limits, budget)
    out: Dict[int, tuple[float, float, float]] = {}
    if not isinstance(w2_obj, list):
        return out
    active_budget.check(1, "max_nesting_depth", "CID vertical width nesting")
    active_budget.check(
        len(w2_obj),
        "max_container_items",
        "CID vertical width array items",
    )

    def finite_number(value: Any) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        try:
            result = float(value)
        except (OverflowError, ValueError):
            return None
        if not math.isfinite(result) or abs(result) > 1_000_000_000:
            return None
        return result

    i = 0
    while i < len(w2_obj):
        first_value = finite_number(w2_obj[i])
        if (
            first_value is None
            or first_value != int(first_value)
            or not 0 <= first_value <= 0xFFFF
        ):
            i += 1
            continue
        first = int(first_value)
        if i + 1 >= len(w2_obj):
            break
        second = w2_obj[i + 1]
        if isinstance(second, list):
            active_budget.check(2, "max_nesting_depth", "CID vertical width nesting")
            active_budget.check(
                len(second),
                "max_container_items",
                "CID vertical width subarray items",
            )
            triples = len(second) // 3
            active_budget.check(
                triples,
                "max_container_items",
                "CID vertical width mappings",
            )
            for offset in range(triples):
                values = tuple(
                    finite_number(second[offset * 3 + component])
                    for component in range(3)
                )
                if all(value is not None for value in values):
                    _put_bounded(
                        out,
                        first + offset,
                        values,
                        active_budget,
                        "CID vertical width mappings",
                    )
            i += 2
            continue
        if i + 4 >= len(w2_obj):
            break
        last_value = finite_number(second)
        metrics = tuple(finite_number(w2_obj[i + j]) for j in range(2, 5))
        if (
            last_value is not None
            and last_value == int(last_value)
            and 0 <= last_value <= 0xFFFF
            and int(last_value) >= first
            and all(value is not None for value in metrics)
        ):
            last = int(last_value)
            active_budget.check(
                last - first + 1,
                "max_container_items",
                "CID vertical width range entries",
            )
            for cid in range(first, last + 1):
                _put_bounded(
                    out,
                    cid,
                    metrics,
                    active_budget,
                    "CID vertical width mappings",
                )
        i += 5
    return out


def parse_to_unicode_cmap(
    cmap_bytes: bytes,
    *,
    limits: PdfLoadLimits | None = None,
    budget: _LoadBudget | None = None,
) -> Dict[bytes, str]:
    """Parse a ToUnicode CMap stream into a code-bytes -> unicode-text map."""
    active_budget = _resolve_resource_budget(limits, budget)
    _check_cmap_input(cmap_bytes, active_budget, "ToUnicode CMap")
    mapping: Dict[bytes, str] = {}
    try:
        text = cmap_bytes.decode("utf-8", errors="ignore")
    except UnicodeError:
        return mapping

    lines = _cmap_lines(text, active_budget, "ToUnicode CMap")
    mode = None
    for line in lines:
        if line.endswith("beginbfchar"):
            mode = "bfchar"
            continue
        if line.endswith("endbfchar"):
            mode = None
            continue
        if line.endswith("beginbfrange"):
            mode = "bfrange"
            continue
        if line.endswith("endbfrange"):
            mode = None
            continue

        if mode == "bfchar":
            matches = iter(re.finditer(r"<([0-9A-Fa-f]+)>", line))
            for src_match in matches:
                dst_match = next(matches, None)
                if dst_match is None:
                    break
                try:
                    src = bytes.fromhex(src_match.group(1))
                    dst = bytes.fromhex(dst_match.group(1)).decode("utf-16-be")
                    _put_bounded(
                        mapping,
                        src,
                        dst,
                        active_budget,
                        "ToUnicode CMap mappings",
                    )
                except (ValueError, TypeError, UnicodeError):
                    pass
        elif mode == "bfrange":
            # Array form is matched by the second pass below.
            if "[" in line:
                continue
            matches = iter(re.finditer(r"<([0-9A-Fa-f]+)>", line))
            for start_match in matches:
                end_match = next(matches, None)
                dst_match = next(matches, None)
                if end_match is None or dst_match is None:
                    break
                try:
                    start_src = bytes.fromhex(start_match.group(1))
                    end_src = bytes.fromhex(end_match.group(1))
                    dst_hex = dst_match.group(1)

                    if len(start_src) != len(end_src):
                        continue

                    dst_start_val = int(dst_hex, 16)
                    start_int = int.from_bytes(start_src, "big")
                    end_int = int.from_bytes(end_src, "big")
                    if end_int < start_int:
                        continue
                    active_budget.check(
                        end_int - start_int + 1,
                        "max_container_items",
                        "ToUnicode bfrange entries",
                    )

                    src_len = len(start_src)

                    for idx, code in enumerate(range(start_int, end_int + 1)):
                        src_bytes = code.to_bytes(src_len, "big")
                        dst_char = chr(dst_start_val + idx)
                        _put_bounded(
                            mapping,
                            src_bytes,
                            dst_char,
                            active_budget,
                            "ToUnicode CMap mappings",
                        )
                except (ValueError, TypeError, OverflowError, UnicodeError):
                    pass
    # bfrange with destination array (often one line): <s> <e> [ <h1> <h2> ... ]
    for m in re.finditer(
        r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*\[([^\]]+)\]",
        text,
    ):
        try:
            start_src = bytes.fromhex(m.group(1))
            end_src = bytes.fromhex(m.group(2))
            inner = m.group(3)
            if len(start_src) != len(end_src):
                continue
            start_int = int.from_bytes(start_src, "big")
            end_int = int.from_bytes(end_src, "big")
            if end_int < start_int:
                continue
            active_budget.check(
                end_int - start_int + 1,
                "max_container_items",
                "ToUnicode bfrange entries",
            )
            src_len = len(start_src)
            for idx, dst_match in enumerate(
                re.finditer(r"<([0-9A-Fa-f]+)>", inner)
            ):
                code = start_int + idx
                if code > end_int:
                    break
                src_bytes = code.to_bytes(src_len, "big")
                dst = bytes.fromhex(dst_match.group(1)).decode("utf-16-be")
                _put_bounded(
                    mapping,
                    src_bytes,
                    dst,
                    active_budget,
                    "ToUnicode CMap mappings",
                )
        except (ValueError, TypeError, IndexError, OverflowError, UnicodeError):
            pass
    return mapping


def parse_encoding_cmap(
    cmap_bytes: bytes,
    *,
    limits: PdfLoadLimits | None = None,
    budget: _LoadBudget | None = None,
) -> Tuple[Dict[bytes, int], List[int]]:
    """Parse a CID Encoding CMap into ``(code-bytes -> CID, sorted code lengths)``.

    Reads ``codespacerange`` (for the code lengths), ``cidrange`` and
    ``cidchar``; the CID destinations are decimal integers. Predefined imports
    (``usecmap``) are ignored here. Named predefined CMaps are handled by the
    separate bundled resolver rather than by this embedded-stream parser.
    """
    active_budget = _resolve_resource_budget(limits, budget)
    _check_cmap_input(cmap_bytes, active_budget, "CID Encoding CMap")
    code_to_cid: Dict[bytes, int] = {}
    lengths: set[int] = set()
    try:
        text = cmap_bytes.decode("latin-1", errors="ignore")
    except UnicodeError:
        return code_to_cid, []

    lines = _cmap_lines(text, active_budget, "CID Encoding CMap")

    mode = None
    for line in lines:
        if line.endswith("begincodespacerange"):
            mode = "csr"
            continue
        if line.endswith("begincidrange"):
            mode = "cidrange"
            continue
        if line.endswith("begincidchar"):
            mode = "cidchar"
            continue
        if line.startswith("end"):
            mode = None
            continue

        if mode == "csr":
            for match in re.finditer(r"<([0-9A-Fa-f]+)>", line):
                hex_str = match.group(1)
                if len(hex_str) % 2 == 0:
                    _add_bounded(
                        lengths,
                        len(hex_str) // 2,
                        active_budget,
                        "CID Encoding CMap code lengths",
                    )
        elif mode == "cidrange":
            m = re.match(
                r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(\d+)", line
            )
            if not m or len(m.group(1)) % 2 or len(m.group(2)) % 2:
                continue
            try:
                lo = bytes.fromhex(m.group(1))
                hi = bytes.fromhex(m.group(2))
                cid0 = int(m.group(3))
            except ValueError:
                continue
            if len(lo) != len(hi):
                continue
            n = len(lo)
            a, b = int.from_bytes(lo, "big"), int.from_bytes(hi, "big")
            if b < a:
                continue
            active_budget.check(
                b - a + 1,
                "max_container_items",
                "CID Encoding CMap range entries",
            )
            _add_bounded(
                lengths,
                n,
                active_budget,
                "CID Encoding CMap code lengths",
            )
            for i, code in enumerate(range(a, b + 1)):
                _put_bounded(
                    code_to_cid,
                    code.to_bytes(n, "big"),
                    cid0 + i,
                    active_budget,
                    "CID Encoding CMap mappings",
                )
        elif mode == "cidchar":
            m = re.match(r"<([0-9A-Fa-f]+)>\s*(\d+)", line)
            if not m or len(m.group(1)) % 2:
                continue
            try:
                code = bytes.fromhex(m.group(1))
            except ValueError:
                continue
            _put_bounded(
                code_to_cid,
                code,
                int(m.group(2)),
                active_budget,
                "CID Encoding CMap mappings",
            )
            _add_bounded(
                lengths,
                len(code),
                active_budget,
                "CID Encoding CMap code lengths",
            )

    return code_to_cid, sorted(lengths)


def parse_encoding_cmap_wmode(
    cmap_bytes: bytes,
    *,
    limits: PdfLoadLimits | None = None,
    budget: _LoadBudget | None = None,
) -> int:
    """Return an embedded Encoding CMap's bounded ``WMode`` value."""
    active_budget = _resolve_resource_budget(limits, budget)
    _check_cmap_input(cmap_bytes, active_budget, "CID Encoding CMap")
    try:
        text = cmap_bytes.decode("latin-1", errors="ignore")
    except UnicodeError:
        return 0
    for line in _cmap_lines(text, active_budget, "CID Encoding CMap"):
        match = re.fullmatch(r"/WMode\s+([01])\s+def", line)
        if match is not None:
            return int(match.group(1))
    return 0


def parse_encoding_cmap_codespaces(
    cmap_bytes: bytes,
    *,
    limits: PdfLoadLimits | None = None,
    budget: _LoadBudget | None = None,
) -> tuple[tuple[bytes, bytes], ...]:
    """Return validated codespace ranges from an embedded Encoding CMap."""
    active_budget = _resolve_resource_budget(limits, budget)
    _check_cmap_input(cmap_bytes, active_budget, "CID Encoding CMap")
    try:
        text = cmap_bytes.decode("latin-1", errors="ignore")
    except UnicodeError:
        return ()
    ranges: list[tuple[bytes, bytes]] = []
    in_codespace = False
    for line in _cmap_lines(text, active_budget, "CID Encoding CMap"):
        if line.endswith("begincodespacerange"):
            in_codespace = True
            continue
        if line.startswith("endcodespacerange"):
            in_codespace = False
            continue
        if not in_codespace:
            continue
        for match in re.finditer(
            r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>",
            line,
        ):
            low_hex, high_hex = match.groups()
            if (
                len(low_hex) != len(high_hex)
                or not low_hex
                or len(low_hex) % 2
            ):
                continue
            low = bytes.fromhex(low_hex)
            high = bytes.fromhex(high_hex)
            if low > high:
                continue
            active_budget.check(
                len(ranges) + 1,
                "max_container_items",
                "CID Encoding CMap codespaces",
            )
            ranges.append((low, high))
    return tuple(ranges)


class ContentStreamParser:
    """Parse a PDF content stream and extract plain text.

    Parameters
    ----------
    content_stream: bytes
        Raw bytes of the page content stream.
    resources: dict
        Dictionary containing PDF resources (fonts, XObjects, …).
    limits: PdfLoadLimits, optional
        Resource limits applied while tokenizing the content stream.
    budget: _LoadBudget, optional
        Per-document budget shared with the PDF loader and lazy operations.
    """

    def __init__(
        self,
        content_stream: bytes,
        resources: Dict[str, Any],
        *,
        limits: PdfLoadLimits | None = None,
        budget: _LoadBudget | None = None,
    ):
        if budget is None:
            self._limits = _coerce_limits(limits)
            self._budget = _LoadBudget(self._limits)
        else:
            if not isinstance(budget, _LoadBudget):
                raise TypeError("budget must be a _LoadBudget instance or None")
            if limits is not None and limits != budget.limits:
                raise ValueError("limits must match budget.limits")
            self._limits = budget.limits
            self._budget = budget
        self._budget.check(
            len(content_stream),
            "max_content_stream_bytes",
            "PDF content stream bytes",
        )
        self._token_count = 0
        self._data = content_stream
        # We process text as latin1 strings to preserve byte values 1:1 while allowing string ops
        self._text = content_stream.decode("latin1")
        self._len = len(self._text)
        self._pos = 0

        self._resources = resources
        self._in_text = False
        self._current_font: Dict[str, Any] | None = None
        self._font_encoding_map: Dict[int, str] | None = None
        self._to_unicode_map: Dict[bytes, str] | None = None
        self._predefined_cmap: PredefinedCMap | None = None
        self._predefined_encoding: PredefinedCMapEncoding | None = None
        self._embedded_encoding_map: Dict[bytes, int] | None = None
        self._embedded_encoding_lengths: tuple[int, ...] = ()
        self._embedded_encoding_codespaces: tuple[tuple[bytes, bytes], ...] = ()
        self._opaque_composite = False
        self._buffer: List[str] = []
        self._marked_actual_text: List[str | None] = []
        self._font_size: float = 12.0
        self._last_glyph_width: int = (
            500  # thousandths of text space unit (em fraction)
        )
        self._widths_by_code: Dict[int, int] | None = None
        self._default_glyph_width: int = 1000
        self._is_cid_identity: bool = False
        self._gs_stack: List[Dict[str, str | None]] = []

        self.WHITESPACE = " \t\n\r\x0c"
        self.DELIMITERS = "()<>[]{}/%"

        # Operand counts for operators that often appear between BT and ET; without these,
        # unknown ops are mistaken for operands and corrupt Tj/TJ stack binding.
        self._FIXED_OP_ARITY: Dict[str, int] = {
            "BT": 0,
            "ET": 0,
            "Tf": 2,
            "Td": 2,
            "TD": 2,
            "Tm": 6,
            "T*": 0,
            "Tj": 1,
            "TJ": 1,
            "'": 3,
            '"': 3,
            "Tc": 1,
            "Tw": 1,
            "Tz": 1,
            "TL": 1,
            "Tr": 1,
            "Ts": 1,
            "d0": 2,
            "d1": 2,
            "q": 0,
            "Q": 0,
            "cm": 6,
            "w": 1,
            "J": 1,
            "j": 1,
            "M": 1,
            "d": 2,
            "ri": 1,
            "i": 1,
            "gs": 1,
            "m": 2,
            "l": 2,
            "c": 6,
            "v": 4,
            "y": 4,
            "re": 4,
            "S": 0,
            "s": 0,
            "f": 0,
            "F": 0,
            "f*": 0,
            "B": 0,
            "B*": 0,
            "b": 0,
            "b*": 0,
            "n": 0,
            "W": 0,
            "W*": 0,
            "rg": 3,
            "RG": 3,
            "g": 1,
            "G": 1,
            "k": 4,
            "K": 4,
            "CS": 1,
            "cs": 1,
            "sh": 1,
            "Do": 1,
            "BX": 0,
            "EX": 0,
            "BMC": 1,
            "BDC": 2,
            "EMC": 0,
            "MP": 1,
            "DP": 2,
        }
        self._VARIABLE_COLOR_OPS = frozenset({"sc", "scn", "SC", "SCN"})

    # ---------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------
    def extract_text(self) -> str:
        """Extract and return the textual content of the stream."""
        self._buffer = []
        self._in_text = False
        self._marked_actual_text = []
        self._gs_stack = [{"nonstroking_cs": None, "stroking_cs": None}]
        stack: List[Any] = []

        for token in self._tokenize():
            if isinstance(token, str) and token in self._VARIABLE_COLOR_OPS:
                need = self._color_components_arity(token in ("SC", "SCN"))
                if need > len(stack):
                    continue
                if need:
                    del stack[-need:]
                continue

            if isinstance(token, str) and token in self._FIXED_OP_ARITY:
                needed = self._FIXED_OP_ARITY[token]
                if needed > len(stack):
                    continue

                operands = stack[-needed:] if needed else []
                if needed:
                    del stack[-needed:]

                if token == "q":
                    self._check_container_items(
                        len(self._gs_stack) + 1,
                        "PDF graphics state stack items",
                    )
                    self._gs_stack.append(dict(self._gs_stack[-1]))
                elif token == "Q":
                    if len(self._gs_stack) > 1:
                        self._gs_stack.pop()
                elif token == "cs" and operands:
                    self._set_colorspace_name(operands[0], nonstroking=True)
                elif token == "CS" and operands:
                    self._set_colorspace_name(operands[0], nonstroking=False)
                elif token in {"BMC", "BDC", "EMC"}:
                    self._handle_marked_content(token, operands)
                elif token in {
                    "BT",
                    "ET",
                    "Tf",
                    "Td",
                    "TD",
                    "Tm",
                    "T*",
                    "Tj",
                    "TJ",
                    "'",
                    '"',
                    "Tc",
                    "Tw",
                    "Tz",
                    "TL",
                    "Tr",
                    "Ts",
                    "d0",
                    "d1",
                }:
                    self._handle_operator(token, operands)
                continue

            self._check_container_items(
                len(stack) + 1,
                "PDF content operand stack items",
            )
            stack.append(token)

        return "".join(self._buffer).strip()

    @staticmethod
    def _decode_actual_text(value: Any) -> str | None:
        if isinstance(value, str):
            return value
        if not isinstance(value, (bytes, bytearray)):
            return None
        raw = bytes(value)
        if raw.startswith(b"\xfe\xff"):
            return raw[2:].decode("utf-16-be", errors="replace")
        if raw.startswith(b"\xff\xfe"):
            return raw[2:].decode("utf-16-le", errors="replace")
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("latin-1", errors="replace")

    def _handle_marked_content(self, op: str, operands: List[Any]) -> None:
        if op == "EMC":
            if not self._marked_actual_text:
                return
            actual_text = self._marked_actual_text.pop()
            if actual_text is not None and not any(
                item is not None for item in self._marked_actual_text
            ):
                self._buffer.append(actual_text)
            return
        actual_text = None
        if op == "BDC" and len(operands) >= 2:
            property_name = operands[-1]
            if isinstance(property_name, str):
                properties = self._resources.get("Properties")
                if isinstance(properties, dict):
                    value = properties.get(property_name.lstrip("/"))
                    if isinstance(value, dict):
                        actual_text = self._decode_actual_text(value.get("ActualText"))
        self._marked_actual_text.append(actual_text)

    def _inside_actual_text(self) -> bool:
        return any(item is not None for item in self._marked_actual_text)

    def _top_gs(self) -> Dict[str, str | None]:
        return self._gs_stack[-1]

    def _set_colorspace_name(self, name_obj: Any, *, nonstroking: bool) -> None:
        if not isinstance(name_obj, str):
            return
        key = "nonstroking_cs" if nonstroking else "stroking_cs"
        self._top_gs()[key] = name_obj.lstrip("/")

    def _color_components_arity(self, stroking: bool) -> int:
        """Operand count for sc/SC (PDF) given current colorspace name on the stack."""
        key = "stroking_cs" if stroking else "nonstroking_cs"
        raw = self._top_gs().get(key) or "DeviceGray"
        cs = raw.replace("/", "").split("#")[0]

        if cs in ("DeviceGray", "Indexed"):
            return 1
        if cs in ("DeviceRGB", "CalRGB", "Lab"):
            return 3
        if cs in ("DeviceCMYK", "CalCMYK"):
            return 4
        if cs == "Pattern":
            return 1
        if cs.startswith("ICCBased"):
            return 3
        return 1

    def best_effort_extract_text(self) -> str:
        """Robust fallback: extract all strings/hex-strings regardless of BT/ET state.

        This method tokenizes the stream and collects all literal and hex strings
        found, including those nested in arrays (useful for partially broken TJ).
        It bypasses operator arity checks and text state (BT/ET) to maximize
        recovery from malformed or complex streams.
        """
        self._buffer = []
        try:
            for token in self._tokenize():
                if isinstance(token, bytes):
                    # Literal or Hex string
                    self._buffer.append(self._decode_bytes(token))
                elif isinstance(token, list):
                    # Array (could contain strings for TJ)
                    for item in token:
                        if isinstance(item, bytes):
                            self._buffer.append(self._decode_bytes(item))
                        elif isinstance(item, (int, float)) and item < -200:
                            self._buffer.append(" ")
        except PdfResourceLimitException:
            raise
        except CONTENT_PARSER_RECOVERABLE:
            return ""

        return "".join(self._buffer).strip()

    # ---------------------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------------------
    def _handle_operator(self, op: str, ops: List[Any]) -> None:
        if op == "BT":
            self._reset_text_state()
            self._in_text = True
            return
        if op == "ET":
            self._in_text = False
            return

        # Strict mode: only process if in text object
        if not self._in_text:
            return

        if op in {"Tc", "Tw", "Tz", "TL", "Tr", "Ts", "d0", "d1"}:
            return

        if op == "Tf":
            # ops: [font_name (name), font_size (number)]
            font_name = ops[0]
            # Remove leading slash if present (it should be a name object, usually str)
            if isinstance(font_name, str) and font_name.startswith("/"):
                font_name = font_name[1:]

            font_key = str(font_name)
            self._current_font = self._resources.get("Font", {}).get(font_key)
            if len(ops) >= 2 and isinstance(ops[1], (int, float)):
                self._font_size = float(ops[1])
            self._prepare_font_maps()
            return

        if op in {"Td", "TD", "Tm", "T*"}:
            if not self._inside_actual_text():
                self._buffer.append(" ")
            return

        if op == "Tj":
            # ops: [bytes]
            if not ops:
                return
            raw = ops[0]
            self._note_glyph_widths_from_bytes(raw)
            if not self._inside_actual_text():
                self._buffer.append(self._decode_bytes(raw))
            return

        if op == "TJ":
            # ops: [list]
            if not ops:
                return
            array = ops[0]
            if not isinstance(array, list):
                return

            for element in array:
                if isinstance(element, bytes):
                    self._note_glyph_widths_from_bytes(element)
                    if not self._inside_actual_text():
                        self._buffer.append(self._decode_bytes(element))
                elif isinstance(element, (int, float)):
                    # TJ numbers: thousandths of a text space unit; large negative
                    # gaps often separate words — compare to last glyph width.
                    adj = float(element)
                    if adj < 0:
                        lw = max(self._last_glyph_width, 1)
                        if (
                            not self._inside_actual_text()
                            and -adj > max(100.0, 0.3 * float(lw))
                        ):
                            self._buffer.append(" ")
            return

        if op == "'":
            if not self._inside_actual_text():
                self._buffer.append("\n")
            if len(ops) >= 1 and not self._inside_actual_text():
                self._buffer.append(
                    self._decode_bytes(ops[-1])
                )  # Last operand is string
            return

        if op == '"':
            if not self._inside_actual_text():
                self._buffer.append("\n")
            if len(ops) >= 1 and not self._inside_actual_text():
                self._buffer.append(
                    self._decode_bytes(ops[-1])
                )  # Last operand is string
            return

    def _reset_text_state(self) -> None:
        self._current_font = None
        self._font_encoding_map = None
        self._to_unicode_map = None
        self._predefined_cmap = None
        self._predefined_encoding = None
        self._embedded_encoding_map = None
        self._embedded_encoding_lengths = ()
        self._embedded_encoding_codespaces = ()
        self._opaque_composite = False
        self._widths_by_code = None
        self._default_glyph_width = 1000
        self._is_cid_identity = False
        self._last_glyph_width = 500

    def _load_cid_widths(self, w_obj: Any) -> Dict[int, int]:
        """Parse a CIDFont /W array into code -> width (thousandths)."""
        return load_cid_widths(w_obj, budget=self._budget)

    def _load_simple_widths(self, font: Dict[str, Any]) -> Dict[int, int]:
        out: Dict[int, int] = {}
        first = font.get("FirstChar")
        widths = font.get("Widths")
        if not isinstance(widths, list) or not isinstance(first, (int, float)):
            return out
        base = int(first)
        for idx, w in enumerate(widths):
            if isinstance(w, (int, float)):
                out[base + idx] = int(w)
        return out

    def _apply_metrics_from_font(self) -> None:
        """Populate width tables from font dict (simple, Type0 / CID)."""
        self._widths_by_code = None
        self._default_glyph_width = 1000
        self._is_cid_identity = False
        if not self._current_font:
            return
        st = self._current_font.get("Subtype")
        enc = self._current_font.get("Encoding")
        enc_s = (enc if isinstance(enc, str) else "").replace("/", "")

        if st == "Type0":
            desc = self._current_font.get("DescendantFonts")
            cid: Optional[Dict[str, Any]] = None
            if isinstance(desc, list) and desc and isinstance(desc[0], dict):
                cid = desc[0]
            if cid:
                dw = cid.get("DW", 1000)
                if isinstance(dw, (int, float)):
                    self._default_glyph_width = int(dw)
                w_entry = cid.get("W")
                self._widths_by_code = self._load_cid_widths(w_entry)
                if enc_s in ("Identity-H", "Identity-V"):
                    self._is_cid_identity = True
            return

        wmap = self._load_simple_widths(self._current_font)
        if wmap:
            self._widths_by_code = wmap
            dw = self._current_font.get("MissingWidth", 1000)
            if isinstance(dw, (int, float)):
                self._default_glyph_width = int(dw)

    def _note_glyph_widths_from_bytes(self, data: Any) -> None:
        if not isinstance(data, bytes) or not data:
            return
        wtable = self._widths_by_code
        if self._embedded_encoding_map is not None:
            for _offset, _length, cid in self._embedded_encoding_units(data):
                self._last_glyph_width = (
                    wtable.get(cid, self._default_glyph_width)
                    if wtable is not None and cid is not None
                    else self._default_glyph_width
                )
            return
        if self._predefined_encoding is not None:
            for _offset, _length, cid in self._predefined_encoding.decode_units(
                data,
                budget=self._budget,
            ):
                if cid is None:
                    self._last_glyph_width = self._default_glyph_width
                else:
                    self._last_glyph_width = (
                        wtable.get(cid, self._default_glyph_width)
                        if wtable
                        else self._default_glyph_width
                    )
            return
        if self._predefined_cmap is not None:
            for _offset, _length, cid, _text in self._predefined_cmap.decode_units(
                data,
                budget=self._budget,
            ):
                if cid is None:
                    self._last_glyph_width = self._default_glyph_width
                else:
                    self._last_glyph_width = (
                        wtable.get(cid, self._default_glyph_width)
                        if wtable
                        else self._default_glyph_width
                    )
            return
        if self._is_cid_identity:
            i = 0
            while i + 1 < len(data):
                cid = int.from_bytes(data[i : i + 2], "big")
                w = (
                    wtable.get(cid, self._default_glyph_width)
                    if wtable
                    else self._default_glyph_width
                )
                self._last_glyph_width = w
                i += 2
            if i < len(data):
                b = data[i]
                w = (
                    wtable.get(b, self._default_glyph_width)
                    if wtable
                    else self._default_glyph_width
                )
                self._last_glyph_width = w
            return
        if wtable:
            for b in data:
                w = wtable.get(b, self._default_glyph_width)
                self._last_glyph_width = w
        else:
            self._last_glyph_width = self._default_glyph_width

    def _embedded_encoding_units(
        self,
        data: bytes,
    ) -> list[tuple[int, int, int | None]]:
        """Tokenize bytes with the current embedded Encoding CMap."""
        mapping = self._embedded_encoding_map
        if mapping is None:
            return []
        spaces_by_length: dict[int, list[tuple[int, int]]] = {}
        for low, high in self._embedded_encoding_codespaces:
            spaces_by_length.setdefault(len(low), []).append(
                (int.from_bytes(low, "big"), int.from_bytes(high, "big"))
            )
        units: list[tuple[int, int, int | None]] = []
        offset = 0
        if spaces_by_length:
            lengths = sorted(spaces_by_length)
            while offset < len(data):
                matched: bytes | None = None
                for length in lengths:
                    if offset + length > len(data):
                        continue
                    candidate = data[offset : offset + length]
                    value = int.from_bytes(candidate, "big")
                    if any(
                        low <= value <= high
                        for low, high in spaces_by_length[length]
                    ):
                        matched = candidate
                        break
                take = len(matched) if matched is not None else 1
                cid = mapping.get(matched) if matched is not None else None
                self._budget.check(
                    len(units) + 1,
                    "max_container_items",
                    "embedded Encoding CMap decoded units",
                )
                units.append((offset, take, cid))
                offset += take
            return units

        lengths = sorted(self._embedded_encoding_lengths, reverse=True)
        minimum_length = lengths[-1] if lengths else 1
        while offset < len(data):
            matched: bytes | None = None
            for length in lengths:
                if offset + length > len(data):
                    continue
                candidate = data[offset : offset + length]
                if candidate in mapping:
                    matched = candidate
                    break
            take = (
                len(matched)
                if matched is not None
                else min(minimum_length, len(data) - offset)
            )
            self._budget.check(
                len(units) + 1,
                "max_container_items",
                "embedded Encoding CMap decoded units",
            )
            units.append(
                (offset, take, mapping.get(matched) if matched is not None else None)
            )
            offset += take
        return units

    def _prepare_font_maps(self) -> None:
        if not self._current_font:
            return

        self._font_encoding_map = None
        self._to_unicode_map = None
        self._predefined_cmap = None
        self._predefined_encoding = None
        self._embedded_encoding_map = None
        self._embedded_encoding_lengths = ()
        self._embedded_encoding_codespaces = ()
        self._opaque_composite = False
        self._apply_metrics_from_font()

        subtype = self._current_font.get("Subtype")
        encoding = self._current_font.get("Encoding")

        # 1. ToUnicode CMap (High Priority)
        to_unicode = self._current_font.get("ToUnicode")
        if to_unicode:
            # If it's a reference, follow it (simple_pdf handles this in resources extraction usually,
            # but let's be safe if it's raw bytes or a stream object)
            cmap_bytes = b""
            if isinstance(to_unicode, bytes):
                cmap_bytes = to_unicode
            elif isinstance(to_unicode, dict):
                content = to_unicode.get("content")
                if isinstance(content, (bytes, bytearray)):
                    cmap_bytes = bytes(content)
            elif hasattr(to_unicode, "content"):
                cmap_bytes = to_unicode.content

            if cmap_bytes:
                self._to_unicode_map = self._parse_to_unicode(cmap_bytes)
                if self._to_unicode_map and subtype != "Type0":
                    self._font_encoding_map = None
                    return

        if subtype == "Type0":
            if isinstance(encoding, str):
                descendants = self._current_font.get("DescendantFonts")
                cid_font = (
                    descendants[0]
                    if isinstance(descendants, list)
                    and descendants
                    and isinstance(descendants[0], dict)
                    else None
                )
                collection = character_collection(
                    cid_font.get("CIDSystemInfo") if cid_font else None
                )
                if self._to_unicode_map:
                    self._predefined_encoding = resolve_predefined_cmap_encoding(
                        encoding,
                        collection,
                        budget=self._budget,
                    )
                    if (
                        self._predefined_encoding is None
                        and encoding.lstrip("/") in supported_cmap_names()
                    ):
                        self._to_unicode_map = None
                        self._opaque_composite = True
                        return
                else:
                    self._predefined_cmap = resolve_predefined_cmap(
                        encoding,
                        collection,
                        budget=self._budget,
                    )
            elif isinstance(encoding, dict):
                encoding_content = encoding.get("content")
                if isinstance(encoding_content, (bytes, bytearray)):
                    cmap, lengths = parse_encoding_cmap(
                        bytes(encoding_content),
                        budget=self._budget,
                    )
                    if cmap:
                        self._embedded_encoding_map = cmap
                        self._embedded_encoding_lengths = tuple(lengths)
                        self._embedded_encoding_codespaces = (
                            parse_encoding_cmap_codespaces(
                                bytes(encoding_content),
                                budget=self._budget,
                            )
                        )
                if self._to_unicode_map and self._embedded_encoding_map is None:
                    self._to_unicode_map = None
                    self._opaque_composite = True
                    return
            if self._to_unicode_map:
                if self._predefined_encoding is None and not self._is_cid_identity:
                    self._opaque_composite = True
                return
            if self._predefined_cmap is not None or self._is_cid_identity:
                return
            self._opaque_composite = True
            return

        # 2. Encoding Registry (Simple / Base Fonts)
        enc = self._current_font.get("Encoding")
        base_enc = None
        differences = None

        if isinstance(enc, dict):
            # Encoding dictionary with /Differences
            base_enc = enc.get("BaseEncoding")
            differences = enc.get("Differences")
        elif isinstance(enc, str):
            base_enc = enc

        # Default maps for standard encodings
        if base_enc == "WinAnsiEncoding":
            self._font_encoding_map = {
                i: codecs.decode(bytes([i]), "cp1252", errors="replace")
                for i in range(256)
            }
        elif base_enc == "MacRomanEncoding":
            self._font_encoding_map = {
                i: codecs.decode(bytes([i]), "mac_roman", errors="replace")
                for i in range(256)
            }
        elif base_enc == "StandardEncoding":
            # StandardEncoding is roughly latin1 but with some differences in PDF
            # For simplicity, we use latin1 as a baseline
            self._font_encoding_map = {
                i: codecs.decode(bytes([i]), "latin1", errors="replace")
                for i in range(256)
            }
        elif base_enc == "PDFDocEncoding":
            # PDFDocEncoding is used for strings in dictionaries, but sometimes in fonts too
            self._font_encoding_map = {
                i: codecs.decode(bytes([i]), "latin1", errors="replace")
                for i in range(256)
            }
        else:
            # Fallback to Latin-1 or Standard Encoding heuristic
            self._font_encoding_map = {
                i: bytes([i]).decode("latin1", errors="ignore") for i in range(256)
            }

        # Apply /Differences if present
        if differences and isinstance(differences, list):
            # Format: [CODE name1 name2 CODE name3 ...]
            curr_code = 0
            for item in differences:
                if isinstance(item, (int, float)):
                    curr_code = int(item)
                elif isinstance(item, str):
                    glyph_name = item.lstrip("/")
                    # Map common glyph names to Unicode
                    unicode_char = self._map_glyph_to_unicode(glyph_name)
                    if unicode_char:
                        self._font_encoding_map[curr_code] = unicode_char
                    curr_code += 1

    def _map_glyph_to_unicode(self, name: str) -> str | None:
        """Map standard PDF glyph names to Unicode characters."""
        if len(name) == 1:
            return name
        # Adobe AGL: uniXXXX (+ multiples of 4 hex) and uXXXX[XX] for Unicode literals.
        if name.startswith("uni") and len(name) > 3:
            hx = name[3:]
            if (
                len(hx) >= 4
                and len(hx) % 4 == 0
                and all(c in "0123456789abcdefABCDEF" for c in hx)
            ):
                parts: list[str] = []
                for i in range(0, len(hx), 4):
                    cp = int(hx[i : i + 4], 16)
                    try:
                        parts.append(chr(cp))
                    except ValueError:
                        return None
                return "".join(parts)
        if name.startswith("u") and len(name) >= 5:
            hx = name[1:]
            if 4 <= len(hx) <= 6 and all(c in "0123456789abcdefABCDEF" for c in hx):
                try:
                    return chr(int(hx, 16))
                except ValueError:
                    return None
        # A minimal mapping for common glyphs
        mapping = {
            "space": " ",
            "exclam": "!",
            "quotedbl": '"',
            "numbersign": "#",
            "dollar": "$",
            "percent": "%",
            "ampersand": "&",
            "quotesingle": "'",
            "parenleft": "(",
            "parenright": ")",
            "asterisk": "*",
            "plus": "+",
            "comma": ",",
            "hyphen": "-",
            "period": ".",
            "slash": "/",
            "zero": "0",
            "one": "1",
            "two": "2",
            "three": "3",
            "four": "4",
            "five": "5",
            "six": "6",
            "seven": "7",
            "eight": "8",
            "nine": "9",
            "colon": ":",
            "semicolon": ";",
            "less": "<",
            "equal": "=",
            "greater": ">",
            "question": "?",
            "at": "@",
            "A": "A",
            "B": "B",
            "C": "C",
            "D": "D",
            "E": "E",
            "F": "F",
            "G": "G",
            "H": "H",
            "I": "I",
            "J": "J",
            "K": "K",
            "L": "L",
            "M": "M",
            "N": "N",
            "O": "O",
            "P": "P",
            "Q": "Q",
            "R": "R",
            "S": "S",
            "T": "T",
            "U": "U",
            "V": "V",
            "W": "W",
            "X": "X",
            "Y": "Y",
            "Z": "Z",
            "bracketleft": "[",
            "backslash": "\\",
            "bracketright": "]",
            "asciicircum": "^",
            "underscore": "_",
            "grave": "`",
            "a": "a",
            "b": "b",
            "c": "c",
            "d": "d",
            "e": "e",
            "f": "f",
            "g": "g",
            "h": "h",
            "i": "i",
            "j": "j",
            "k": "k",
            "l": "l",
            "m": "m",
            "n": "n",
            "o": "o",
            "p": "p",
            "q": "q",
            "r": "r",
            "s": "s",
            "t": "t",
            "u": "u",
            "v": "v",
            "w": "w",
            "x": "x",
            "y": "y",
            "z": "z",
            "braceleft": "{",
            "bar": "|",
            "braceright": "}",
            "asciitilde": "~",
            "bullet": "•",
            "dagger": "†",
            "daggerdbll": "‡",
            "ellipsis": "…",
            "emdash": "—",
            "endash": "–",
            "fi": "fi",
            "fl": "fl",
            "fraction": "⁄",
            "guillemotleft": "«",
            "guillemotright": "»",
            "guilsinglleft": "‹",
            "guilsinglright": "›",
            "minus": "−",
            "quotedblbase": "„",
            "quotedblleft": "“",
            "quotedblright": "”",
            "quoteleft": "‘",
            "quoteright": "’",
            "quotesinglbase": "‚",
            "trademark": "™",
        }
        return mapping.get(name)

    def _decode_bytes(self, data: bytes) -> str:
        if not isinstance(data, bytes):
            return ""

        if self._to_unicode_map:
            if self._embedded_encoding_map is not None:
                return "".join(
                    self._to_unicode_map.get(
                        data[offset : offset + length],
                        "\ufffd",
                    )
                    if cid is not None
                    else "\ufffd"
                    for offset, length, cid in self._embedded_encoding_units(data)
                )
            if self._predefined_encoding is not None:
                return "".join(
                    self._to_unicode_map.get(
                        data[offset : offset + length],
                        "\ufffd",
                    )
                    if cid is not None
                    else "\ufffd"
                    for offset, length, cid in self._predefined_encoding.decode_units(
                        data,
                        budget=self._budget,
                    )
                )
            if self._is_cid_identity:
                return "".join(
                    self._to_unicode_map.get(data[i : i + 2], "\ufffd")
                    if len(data[i : i + 2]) == 2
                    else "\ufffd"
                    for i in range(0, len(data), 2)
                )

            out: List[str] = []
            i = 0
            lengths = sorted(
                {len(key) for key in self._to_unicode_map if key},
                reverse=True,
            )
            minimum_length = lengths[-1] if lengths else 1

            while i < len(data):
                matched = None
                # Greedy match longest key
                for key_len in lengths:
                    if i + key_len > len(data):
                        continue
                    chunk = data[i : i + key_len]
                    if chunk in self._to_unicode_map:
                        matched = self._to_unicode_map[chunk]
                        i += key_len
                        break
                if matched is None:
                    out.append("\ufffd")
                    i += min(minimum_length, len(data) - i)
                else:
                    out.append(matched)
            return "".join(out)

        if self._predefined_cmap is not None:
            return "".join(
                text if text is not None else "\ufffd"
                for _offset, _length, _cid, text
                in self._predefined_cmap.decode_units(data, budget=self._budget)
            )

        if self._is_cid_identity and not self._to_unicode_map:
            out: List[str] = []
            j = 0
            while j + 1 < len(data):
                code = int.from_bytes(data[j : j + 2], "big")
                try:
                    out.append(chr(code))
                except ValueError:
                    out.append("\ufffd")
                j += 2
            if j < len(data):
                out.append(
                    bytes([data[j]]).decode("latin1", errors="replace"),
                )
            return "".join(out)

        if self._font_encoding_map:
            parts: List[str] = []
            for b in data:
                ch = self._font_encoding_map.get(b)
                if ch:
                    parts.append(ch)
                else:
                    parts.append(bytes([b]).decode("latin1", errors="replace"))
            return "".join(parts)

        if self._opaque_composite:
            return "\ufffd" if data else ""

        return data.decode("utf-8", errors="ignore")

    def _parse_to_unicode(self, cmap_bytes: bytes) -> Dict[bytes, str]:
        return parse_to_unicode_cmap(cmap_bytes, budget=self._budget)

    # ---------------------------------------------------------------------
    # Tokenizer
    # ---------------------------------------------------------------------
    def _count_token(self) -> None:
        self._token_count += 1
        self._budget.check(
            self._token_count,
            "max_content_tokens",
            "PDF content stream tokens",
        )

    def _check_nesting(self, depth: int, context: str) -> None:
        self._budget.check(depth, "max_nesting_depth", context)

    def _check_container_items(self, count: int, context: str) -> None:
        self._budget.check(count, "max_container_items", context)

    def _skip_ws(self):
        while self._pos < self._len and self._text[self._pos] in self.WHITESPACE:
            self._pos += 1

    def _tokenize(self) -> Iterator[Any]:
        self._pos = 0
        self._token_count = 0
        while self._pos < self._len:
            self._skip_ws()
            if self._pos >= self._len:
                break

            ch = self._text[self._pos]

            if ch == "%":
                # Comment
                while self._pos < self._len and self._text[self._pos] not in "\r\n":
                    self._pos += 1
                continue

            if ch == "(":
                token = self._read_string()
                self._count_token()
                yield token
                continue

            if ch == "<":
                if self._pos + 1 < self._len and self._text[self._pos + 1] == "<":
                    # Dict start << (yield as operator or separate tokens?)
                    # For text parser, usually we don't care about dicts inside text?
                    # But ToUnicode map parsing might need it?
                    # ToUnicode parsing is done separately on the stream.
                    # Here we are parsing content stream. << could be inline image dict.
                    self._pos += 2
                    self._count_token()
                    yield "<<"
                    continue
                token = self._read_hex_string()
                self._count_token()
                yield token
                continue

            if ch == ">":
                if self._pos + 1 < self._len and self._text[self._pos + 1] == ">":
                    self._pos += 2
                    self._count_token()
                    yield ">>"
                    continue
                self._pos += 1
                continue  # Unexpected >

            if ch == "[":
                yield self._read_array(1)
                continue

            if ch == "]":
                # Should not happen if _read_array consumes it, but robustness
                self._pos += 1
                continue

            if ch == "/":
                token = self._read_name()
                self._count_token()
                yield token
                continue

            # Number or Operator
            token = self._read_number_or_operator()
            self._count_token()
            yield token

    def _read_string(self) -> bytes:
        # Assumes at "("
        self._pos += 1
        depth = 1
        self._check_nesting(depth, "PDF content string nesting")
        acc = bytearray()

        while self._pos < self._len and depth > 0:
            c = self._text[self._pos]
            if c == "\\":
                self._pos += 1
                if self._pos >= self._len:
                    break
                esc = self._text[self._pos]
                if esc == "n":
                    acc.append(10)  # \n
                elif esc == "r":
                    acc.append(13)  # \r
                elif esc == "t":
                    acc.append(9)  # \t
                elif esc == "b":
                    acc.append(8)  # \b
                elif esc == "f":
                    acc.append(12)  # \f
                elif esc == "(":
                    acc.append(40)
                elif esc == ")":
                    acc.append(41)
                elif esc == "\\":
                    acc.append(92)
                elif esc.isdigit():
                    # Octal
                    octal = esc
                    # check next 2 chars
                    for _ in range(2):
                        if (
                            self._pos + 1 < self._len
                            and self._text[self._pos + 1].isdigit()
                        ):
                            self._pos += 1
                            octal += self._text[self._pos]
                    acc.append(int(octal, 8))
                else:
                    acc.append(ord(esc))
            elif c == "(":
                depth += 1
                self._check_nesting(depth, "PDF content string nesting")
                acc.append(ord(c))
            elif c == ")":
                depth -= 1
                if depth > 0:
                    acc.append(ord(c))
            else:
                acc.append(ord(c))
            self._pos += 1

        return bytes(acc)

    def _read_hex_string(self) -> bytes:
        # Assumes at "<"
        self._pos += 1
        acc = []
        while self._pos < self._len:
            c = self._text[self._pos]
            if c == ">":
                self._pos += 1
                break
            if c in self.WHITESPACE:
                self._pos += 1
                continue
            acc.append(c)
            self._pos += 1

        hex_str = "".join(acc)
        if len(hex_str) % 2 == 1:
            hex_str += "0"
        try:
            return bytes.fromhex(hex_str)
        except ValueError:
            return b""

    def _read_array(self, depth: int) -> List[Any]:
        # Assumes at "["; returns list of tokens inside
        self._check_nesting(depth, "PDF content array nesting")
        self._count_token()
        self._pos += 1
        arr = []
        while self._pos < self._len:
            self._skip_ws()
            if self._pos >= self._len:
                break
            if self._text[self._pos] == "]":
                self._pos += 1
                break

            # Recursively read one token
            # But since we flattened _tokenize, we need a way to read ONE token.
            # We can replicate checks or extract a _read_next_token helper.
            # Let's verify what types are allowed in array. Integers, floats, strings, names.
            # Arrays can be nested? Yes.

            ch = self._text[self._pos]
            token = None
            if ch == "(":
                token = self._read_string()
            elif ch == "<":
                if self._pos + 1 < self._len and self._text[self._pos + 1] == "<":
                    self._pos += 2
                    token = "<<"
                else:
                    token = self._read_hex_string()
            elif ch == "[":
                token = self._read_array(depth + 1)
            elif ch == "/":
                token = self._read_name()
            elif ch in "%]":
                # % comment, ] end
                if ch == "%":
                    # skip comment
                    while self._pos < self._len and self._text[self._pos] not in "\r\n":
                        self._pos += 1
                    continue
                # if ] should have handled by loop check.
                pass
            elif ch == ">":
                self._pos += (
                    2
                    if self._pos + 1 < self._len
                    and self._text[self._pos + 1] == ">"
                    else 1
                )
                token = ">>"
            else:
                token = self._read_number_or_operator()

            if token is not None:
                if ch != "[":
                    self._count_token()
                self._check_container_items(
                    len(arr) + 1,
                    "PDF content array items",
                )
                arr.append(token)

        return arr

    def _read_name(self) -> str:
        # Assumes at "/"
        start = self._pos
        self._pos += 1
        while (
            self._pos < self._len
            and self._text[self._pos] not in self.WHITESPACE + self.DELIMITERS
        ):
            self._pos += 1
        # Include / or not? PDF spec says name object includes /.
        # But for 'Tf' lookups we often strip it.
        # Let's keep / to be correct token.
        return self._text[start : self._pos]

    def _read_number_or_operator(self) -> Union[int, float, str]:
        start = self._pos
        while (
            self._pos < self._len
            and self._text[self._pos] not in self.WHITESPACE + self.DELIMITERS
        ):
            self._pos += 1

        chunk = self._text[start : self._pos]
        try:
            if "." in chunk:
                return float(chunk)
            return int(chunk)
        except ValueError:
            return chunk


def parse_image_placements_from_content(
    content: bytes,
    *,
    limits: PdfLoadLimits | None = None,
    budget: _LoadBudget | None = None,
) -> List[Tuple[str, Tuple[Decimal, ...]]]:
    """Parse PDF content stream and extract image placements (Do operator with matrix).

    Returns a list of (xobject_name, matrix) for each Do operator, where matrix
    is (a, b, c, d, e, f) in PDF order as Decimals. Affine composition uses
    :mod:`aspose_pdf.engine.pdf_matrix` so large translations are not rounded
    away during ``cm`` chaining. Callers coerce to ``float`` where
    needed for API surfaces.
    """
    if budget is None:
        resolved_limits = _coerce_limits(limits)
        active_budget = _LoadBudget(resolved_limits)
    else:
        if not isinstance(budget, _LoadBudget):
            raise TypeError("budget must be a _LoadBudget instance or None")
        if limits is not None and limits != budget.limits:
            raise ValueError("limits must match budget.limits")
        active_budget = budget
    active_budget.check(
        len(content),
        "max_content_stream_bytes",
        "image placement content stream bytes",
    )

    result: List[Tuple[str, Tuple[Decimal, ...]]] = []
    IDENTITY = identity_affine_decimal()
    token_count = 0

    def _count_token() -> None:
        nonlocal token_count
        token_count += 1
        active_budget.check(
            token_count,
            "max_content_tokens",
            "image placement content tokens",
        )

    def _check_nesting(depth: int, context: str) -> None:
        active_budget.check(depth, "max_nesting_depth", context)

    def _tokenize(data: bytes) -> Iterator[Any]:
        text = data.decode("latin1", errors="replace")
        i = 0
        n = len(text)
        ws = " \t\n\r\x0c"
        while i < n:
            while i < n and text[i] in ws:
                i += 1
            if i >= n:
                break
            if text[i] == "%":
                while i < n and text[i] not in "\r\n":
                    i += 1
                continue
            if text[i] == "/":
                start = i
                i += 1
                while i < n and text[i] not in ws + "()<>[]{}/%":
                    i += 1
                _count_token()
                yield text[start:i]
                continue
            if text[i] == "(":
                i += 1
                depth = 1
                _count_token()
                _check_nesting(depth, "image placement string nesting")
                while i < n and depth > 0:
                    if text[i] == "\\" and i + 1 < n:
                        i += 2
                        continue
                    if text[i] == "(":
                        depth += 1
                        _check_nesting(depth, "image placement string nesting")
                    elif text[i] == ")":
                        depth -= 1
                    i += 1
                continue
            if text[i] == ")":
                i += 1
                continue
            if text[i] == "<":
                if i + 1 < n and text[i + 1] == "<":
                    i += 2
                    depth = 1
                    _count_token()
                    _check_nesting(depth, "image placement dictionary nesting")
                    while i < n and depth > 0:
                        if text[i] == "(":
                            i += 1
                            while i < n:
                                if text[i] == "\\" and i + 1 < n:
                                    i += 2
                                    continue
                                if text[i] == ")":
                                    i += 1
                                    break
                                i += 1
                            continue
                        if i + 1 < n and text[i : i + 2] == "<<":
                            depth += 1
                            _check_nesting(
                                depth,
                                "image placement dictionary nesting",
                            )
                            i += 2
                            continue
                        if i + 1 < n and text[i : i + 2] == ">>":
                            depth -= 1
                            i += 2
                            continue
                        i += 1
                    continue
                i += 1
                _count_token()
                while i < n and text[i] != ">":
                    i += 1 if text[i] in ws else 2
                if i < n:
                    i += 1
                continue
            if text[i] == ">":
                i += 2 if i + 1 < n and text[i + 1] == ">" else 1
                continue
            if text[i] == "[":
                i += 1
                depth = 1
                _count_token()
                _check_nesting(depth, "image placement array nesting")
                while i < n and depth > 0:
                    if text[i] == "[":
                        depth += 1
                        _check_nesting(depth, "image placement array nesting")
                    elif text[i] == "]":
                        depth -= 1
                    i += 1
                continue
            if text[i] == "]":
                i += 1
                continue
            start = i
            while i < n and text[i] not in ws + "()<>[]{}/%":
                i += 1
            chunk = text[start:i]
            try:
                token = float(chunk) if "." in chunk else int(chunk)
            except ValueError:
                token = chunk
            _count_token()
            yield token

    ctm_stack: List[Tuple[Decimal, ...]] = [IDENTITY]
    ctm: Tuple[Decimal, ...] = IDENTITY
    recent: deque[Any] = deque(maxlen=6)

    for t in _tokenize(content):
        if t == "q":
            active_budget.check(
                len(ctm_stack) + 1,
                "max_container_items",
                "image placement graphics state stack items",
            )
            active_budget.check(
                len(ctm_stack) + 1,
                "max_nesting_depth",
                "image placement graphics state depth",
            )
            ctm_stack.append(ctm)
        elif t == "Q":
            if len(ctm_stack) > 1:
                ctm_stack.pop()
                ctm = ctm_stack[-1]
        elif t == "cm":
            if len(recent) == 6:
                vals_d: List[Decimal] = []
                for v in recent:
                    if isinstance(v, (int, float)):
                        vals_d.append(pdf_scalar_to_decimal(v))
                    else:
                        vals_d = []
                        break
                if len(vals_d) == 6:
                    ctm = multiply_pdf_affine(tuple(vals_d), ctm)
                    ctm_stack[-1] = ctm
        elif t == "Do":
            if recent:
                name = recent[-1]
                if isinstance(name, str):
                    name = name.lstrip("/")
                active_budget.check(
                    len(result) + 1,
                    "max_container_items",
                    "image placement results",
                )
                result.append((str(name), ctm))
        recent.append(t)

    return result

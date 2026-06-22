"""Content Stream Parser Module.

Implements a minimal PDF content‑stream parser capable of extracting text
from a page's content stream.  The implementation follows the subset of the
PDF text operators required by the SDK.
"""

from __future__ import annotations

import codecs
import re
from decimal import Decimal
from typing import Any, Dict, List, Tuple, Union, Iterator, Optional

from aspose_pdf.exceptions import CONTENT_PARSER_RECOVERABLE

from .pdf_matrix import (
    identity_affine_decimal,
    multiply_pdf_affine,
    pdf_scalar_to_decimal,
)


class ContentStreamParser:
    """Parse a PDF content stream and extract plain text.

    Parameters
    ----------
    content_stream: bytes
        Raw bytes of the page content stream.
    resources: dict
        Dictionary containing PDF resources (fonts, XObjects, …).
    """

    def __init__(self, content_stream: bytes, resources: Dict[str, Any]):
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
        self._buffer: List[str] = []
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
                    self._gs_stack.append(dict(self._gs_stack[-1]))
                elif token == "Q":
                    if len(self._gs_stack) > 1:
                        self._gs_stack.pop()
                elif token == "cs" and operands:
                    self._set_colorspace_name(operands[0], nonstroking=True)
                elif token == "CS" and operands:
                    self._set_colorspace_name(operands[0], nonstroking=False)
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

            stack.append(token)

        return "".join(self._buffer).strip()

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
            self._buffer.append(" ")
            return

        if op == "Tj":
            # ops: [bytes]
            if not ops:
                return
            raw = ops[0]
            self._note_glyph_widths_from_bytes(raw)
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
                    self._buffer.append(self._decode_bytes(element))
                elif isinstance(element, (int, float)):
                    # TJ numbers: thousandths of a text space unit; large negative
                    # gaps often separate words — compare to last glyph width.
                    adj = float(element)
                    if adj < 0:
                        lw = max(self._last_glyph_width, 1)
                        if -adj > max(100.0, 0.3 * float(lw)):
                            self._buffer.append(" ")
            return

        if op == "'":
            self._buffer.append("\n")
            if len(ops) >= 1:
                self._buffer.append(
                    self._decode_bytes(ops[-1])
                )  # Last operand is string
            return

        if op == '"':
            self._buffer.append("\n")
            if len(ops) >= 1:
                self._buffer.append(
                    self._decode_bytes(ops[-1])
                )  # Last operand is string
            return

    def _reset_text_state(self) -> None:
        self._current_font = None
        self._font_encoding_map = None
        self._to_unicode_map = None
        self._widths_by_code = None
        self._default_glyph_width = 1000
        self._is_cid_identity = False
        self._last_glyph_width = 500

    def _load_cid_widths(self, w_obj: Any) -> Dict[int, int]:
        """Parse a CIDFont /W array into code -> width (thousandths)."""
        out: Dict[int, int] = {}
        if not isinstance(w_obj, list):
            return out
        i = 0
        n = len(w_obj)
        while i < n:
            first = w_obj[i]
            if i + 1 >= n:
                break
            second = w_obj[i + 1]
            if isinstance(second, list):
                if isinstance(first, (int, float)):
                    code0 = int(first)
                    for j, w in enumerate(second):
                        if isinstance(w, (int, float)):
                            out[code0 + j] = int(w)
                i += 2
            elif i + 2 < n:
                third = w_obj[i + 2]
                if (
                    isinstance(first, (int, float))
                    and isinstance(second, (int, float))
                    and isinstance(third, (int, float))
                ):
                    c1, c2, w = int(first), int(second), int(third)
                    for code in range(c1, c2 + 1):
                        out[code] = w
                i += 3
            else:
                i += 1
        return out

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
                # Show strings are UTF-16BE code units for Identity-* and *UTF16* CMaps (common CJK case).
                if enc_s in ("Identity-H", "Identity-V") or (
                    enc_s and "UTF16" in enc_s.upper()
                ):
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

    def _prepare_font_maps(self) -> None:
        if not self._current_font:
            return

        self._apply_metrics_from_font()

        # 1. ToUnicode CMap (High Priority)
        to_unicode = self._current_font.get("ToUnicode")
        if to_unicode:
            # If it's a reference, follow it (simple_pdf handles this in resources extraction usually,
            # but let's be safe if it's raw bytes or a stream object)
            cmap_bytes = b""
            if isinstance(to_unicode, bytes):
                cmap_bytes = to_unicode
            elif hasattr(to_unicode, "content"):
                cmap_bytes = to_unicode.content

            if cmap_bytes:
                self._to_unicode_map = self._parse_to_unicode(cmap_bytes)
                if self._to_unicode_map:
                    self._font_encoding_map = None
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
            out = []
            i = 0
            max_key_len = 4  # Optimized guess
            if self._to_unicode_map:
                max_key_len = max(len(k) for k in self._to_unicode_map)

            while i < len(data):
                matched = None
                # Greedy match longest key
                for key_len in range(max_key_len, 0, -1):
                    if i + key_len > len(data):
                        continue
                    chunk = data[i : i + key_len]
                    if chunk in self._to_unicode_map:
                        matched = self._to_unicode_map[chunk]
                        i += key_len
                        break
                if matched is None:
                    if self._is_cid_identity and i + 1 < len(data):
                        code = int.from_bytes(data[i : i + 2], "big")
                        try:
                            out.append(chr(code))
                        except ValueError:
                            out.append("\ufffd")
                        i += 2
                    else:
                        out.append(bytes([data[i]]).decode("latin1", errors="replace"))
                        i += 1
                else:
                    out.append(matched)
            return "".join(out)

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

        return data.decode("utf-8", errors="ignore")

    def _parse_to_unicode(self, cmap_bytes: bytes) -> Dict[bytes, str]:
        mapping: Dict[bytes, str] = {}
        try:
            text = cmap_bytes.decode("utf-8", errors="ignore")
        except UnicodeError:
            return mapping

        lines: list[str] = []
        for raw in text.splitlines():
            if "%" in raw:
                raw = raw.split("%", 1)[0]
            stripped = raw.strip()
            if stripped:
                lines.append(stripped)
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
                hexes = re.findall(r"<([0-9A-Fa-f]+)>", line)
                for i in range(0, len(hexes) - 1, 2):
                    try:
                        src = bytes.fromhex(hexes[i])
                        dst = bytes.fromhex(hexes[i + 1]).decode("utf-16-be")
                        mapping[src] = dst
                    except (ValueError, TypeError, UnicodeError):
                        pass
            elif mode == "bfrange":
                # Array form is matched by the second pass below.
                if "[" in line:
                    continue
                hexes = re.findall(r"<([0-9A-Fa-f]+)>", line)
                for j in range(0, len(hexes) - 2, 3):
                    try:
                        start_src = bytes.fromhex(hexes[j])
                        end_src = bytes.fromhex(hexes[j + 1])
                        dst_hex = hexes[j + 2]

                        dst_start_val = int(dst_hex, 16)
                        start_int = int.from_bytes(start_src, "big")
                        end_int = int.from_bytes(end_src, "big")

                        src_len = len(start_src)

                        for idx, code in enumerate(range(start_int, end_int + 1)):
                            src_bytes = code.to_bytes(src_len, "big")
                            dst_char = chr(dst_start_val + idx)
                            mapping[src_bytes] = dst_char
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
                dest_hexes = re.findall(r"<([0-9A-Fa-f]+)>", inner)
                start_int = int.from_bytes(start_src, "big")
                end_int = int.from_bytes(end_src, "big")
                src_len = len(start_src)
                for idx, code in enumerate(range(start_int, end_int + 1)):
                    src_bytes = code.to_bytes(src_len, "big")
                    if idx < len(dest_hexes):
                        dst = bytes.fromhex(dest_hexes[idx]).decode("utf-16-be")
                        mapping[src_bytes] = dst
            except (ValueError, TypeError, IndexError, OverflowError, UnicodeError):
                pass
        return mapping

    # ---------------------------------------------------------------------
    # Tokenizer
    # ---------------------------------------------------------------------
    def _skip_ws(self):
        while self._pos < self._len and self._text[self._pos] in self.WHITESPACE:
            self._pos += 1

    def _tokenize(self) -> Iterator[Any]:
        self._pos = 0
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
                yield self._read_string()
                continue

            if ch == "<":
                if self._pos + 1 < self._len and self._text[self._pos + 1] == "<":
                    # Dict start << (yield as operator or separate tokens?)
                    # For text parser, usually we don't care about dicts inside text?
                    # But ToUnicode map parsing might need it?
                    # ToUnicode parsing is done separately on the stream.
                    # Here we are parsing content stream. << could be inline image dict.
                    self._pos += 2
                    yield "<<"
                    continue
                yield self._read_hex_string()
                continue

            if ch == ">":
                if self._pos + 1 < self._len and self._text[self._pos + 1] == ">":
                    self._pos += 2
                    yield ">>"
                    continue
                self._pos += 1
                continue  # Unexpected >

            if ch == "[":
                yield self._read_array()
                continue

            if ch == "]":
                # Should not happen if _read_array consumes it, but robustness
                self._pos += 1
                continue

            if ch == "/":
                yield self._read_name()
                continue

            # Number or Operator
            yield self._read_number_or_operator()

    def _read_string(self) -> bytes:
        # Assumes at "("
        self._pos += 1
        depth = 1
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

    def _read_array(self) -> List[Any]:
        # Assumes at "["; returns list of tokens inside
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
                token = self._read_hex_string()  # strictly <...>, simple check
            elif ch == "[":
                token = self._read_array()
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
            else:
                token = self._read_number_or_operator()

            if token is not None:
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
) -> List[Tuple[str, Tuple[Decimal, ...]]]:
    """Parse PDF content stream and extract image placements (Do operator with matrix).

    Returns a list of (xobject_name, matrix) for each Do operator, where matrix
    is (a, b, c, d, e, f) in PDF order as Decimals. Affine composition uses
    :mod:`aspose_pdf.engine.pdf_matrix` so large translations are not rounded
    away during ``cm`` chaining. Callers coerce to ``float`` where
    needed for API surfaces.
    """
    result: List[Tuple[str, Tuple[Decimal, ...]]] = []
    IDENTITY = identity_affine_decimal()

    def _tokenize(data: bytes) -> List[Any]:
        tokens: List[Any] = []
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
                tokens.append(text[start:i])
                continue
            if text[i] in "()":
                paren = text[i]
                i += 1
                depth = 1
                while i < n and depth > 0:
                    if text[i] == "\\" and i + 1 < n:
                        i += 2
                        continue
                    if text[i] == paren:
                        depth += 1
                    elif text[i] in "()":
                        depth -= 1
                    i += 1
                continue
            if text[i] == "<":
                if i + 1 < n and text[i + 1] == "<":
                    i += 2
                    depth = 1
                    while i < n and depth > 0:
                        if text[i] == "(":
                            paren = text[i]
                            i += 1
                            while i < n:
                                if text[i] == "\\" and i + 1 < n:
                                    i += 2
                                    continue
                                if text[i] == paren:
                                    i += 1
                                    break
                                i += 1
                            continue
                        if i + 1 < n and text[i : i + 2] == "<<":
                            depth += 1
                            i += 2
                            continue
                        if i + 1 < n and text[i : i + 2] == ">>":
                            depth -= 1
                            i += 2
                            continue
                        i += 1
                    continue
                i += 1
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
                while i < n and depth > 0:
                    if text[i] == "[":
                        depth += 1
                    elif text[i] == "]":
                        depth -= 1
                    i += 1
                continue
            start = i
            while i < n and text[i] not in ws + "()<>[]{}/%":
                i += 1
            chunk = text[start:i]
            try:
                tokens.append(float(chunk) if "." in chunk else int(chunk))
            except ValueError:
                tokens.append(chunk)
        return tokens

    tokens = _tokenize(content)
    ctm_stack: List[Tuple[Decimal, ...]] = [IDENTITY]
    ctm: Tuple[Decimal, ...] = IDENTITY
    i = 0

    while i < len(tokens):
        t = tokens[i]
        if t == "q":
            ctm_stack.append(ctm)
            i += 1
        elif t == "Q":
            if len(ctm_stack) > 1:
                ctm_stack.pop()
                ctm = ctm_stack[-1]
            i += 1
        elif t == "cm":
            if i >= 6:
                vals_d: List[Decimal] = []
                for j in range(i - 6, i):
                    v = tokens[j]
                    if isinstance(v, (int, float)):
                        vals_d.append(pdf_scalar_to_decimal(v))
                    else:
                        vals_d = []
                        break
                if len(vals_d) == 6:
                    ctm = multiply_pdf_affine(tuple(vals_d), ctm)
                    ctm_stack[-1] = ctm
            i += 1
        elif t == "Do":
            if i >= 1:
                name = tokens[i - 1]
                if isinstance(name, str):
                    name = name.lstrip("/")
                result.append((str(name), ctm))
            i += 1
        else:
            i += 1

    return result

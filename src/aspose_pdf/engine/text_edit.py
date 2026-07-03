"""Small content-stream text editing helpers.

The functions here intentionally edit only straightforward PDF text-showing
operands. They are a conservative base for public replace/redact APIs and avoid
guessing at layout, shaping, or font-specific CMap rewrites.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Optional

from aspose_pdf.exceptions import PdfValidationException

_WHITESPACE = b" \t\n\r\x0c"
_DELIMITERS = b"()<>[]{}/%"
_LINE_SHOW_OPS = {"'", '"'}

_ID_MATRIX = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

# Geometric run-joining thresholds, as fractions of the current font size.
# A positioning operator (Td/TD/Tm/T*) continues the current run when the new
# position stays on the same baseline and the horizontal gap from the pen is
# small; a word-sized gap contributes a synthetic space to the logical string.
_JOIN_BASELINE_TOL = 0.05
_JOIN_MIN_GAP = -0.10
_JOIN_MAX_GAP = 1.0
_JOIN_SPACE_GAP = 0.18

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


class CidTextCodec:
    """Two-byte code codec for a composite (Type0) font's show strings.

    Built from the font's ToUnicode CMap. Decoding walks the operand two bytes
    at a time (the Identity-H codespace) and yields one *unit* per code, so
    matched units can be spliced out of the raw operand byte-for-byte without
    re-encoding the untouched codes. Encoding (for replacement text) uses the
    reverse mapping and fails softly when a character has no known code.
    """

    def __init__(self, code_to_text: Mapping[bytes, str]) -> None:
        self.code_to_text = {
            code: text for code, text in code_to_text.items() if len(code) == 2
        }
        self._reverse: dict[str, bytes] = {}
        self._max_reverse_len = 1
        for code, text in self.code_to_text.items():
            if text and text not in self._reverse:
                self._reverse[text] = code
                self._max_reverse_len = max(self._max_reverse_len, len(text))

    def decode_units(self, raw: bytes) -> list[tuple[int, int, str]]:
        """Split *raw* into ``(offset, length, text)`` units, one per code.

        An unmapped code decodes to U+FFFD (it can never match a search
        string but keeps its raw bytes when the operand is rebuilt); a
        trailing odd byte becomes its own unmapped unit.
        """
        units: list[tuple[int, int, str]] = []
        i = 0
        n = len(raw)
        while i < n:
            if i + 2 > n:
                units.append((i, n - i, "�"))
                break
            units.append((i, 2, self.code_to_text.get(raw[i : i + 2], "�")))
            i += 2
        return units

    def encode(self, text: str) -> Optional[bytes]:
        """Encode *text* as code bytes, or None if any part is unmappable.

        Greedy longest-match against the reverse ToUnicode map, so multi-char
        targets (ligature codes) are usable as well.
        """
        out = bytearray()
        i = 0
        n = len(text)
        while i < n:
            for length in range(min(self._max_reverse_len, n - i), 0, -1):
                code = self._reverse.get(text[i : i + length])
                if code is not None:
                    out += code
                    i += length
                    break
            else:
                return None
        return bytes(out)


# ---------------------------------------------------------------------------
# Geometric show-run walker
# ---------------------------------------------------------------------------


def _mat_mul(m: tuple, n: tuple) -> tuple:
    """Compose affines: apply *m* then *n* (PDF row-vector convention)."""
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return (
        a * A + b * C,
        a * B + b * D,
        c * A + d * C,
        c * B + d * D,
        e * A + f * C + E,
        e * B + f * D + F,
    )


def _linear_close(m: tuple, n: tuple) -> bool:
    """Whether two matrices share the same rotation/scale part."""
    return all(abs(m[i] - n[i]) <= 1e-6 * max(1.0, abs(m[i])) for i in range(4))


def _text_delta(origin: tuple, m: tuple) -> Optional[tuple[float, float]]:
    """Translation of *m* relative to *origin*, in origin's text space."""
    if not _linear_close(origin, m):
        return None
    a, b, c, d = origin[:4]
    det = a * d - b * c
    if abs(det) < 1e-12:
        return None
    dx = m[4] - origin[4]
    dy = m[5] - origin[5]
    return ((dx * d - dy * c) / det, (dy * a - dx * b) / det)


@dataclass
class _RunSegment:
    """One string operand (or a synthesized inter-operator gap) of a run."""

    token: Optional[_Token]  # None for a synthesized gap
    codec: Optional[CidTextCodec]
    metric: Any  # duck-typed: width_of / ascent / descent / code_to_text
    size: float
    char_spacing: float
    word_spacing: float
    h_scale: float
    rise: float
    pen: Optional[tuple[float, float]]  # text-space offset from the run origin
    gap_text: str = ""  # synthesized logical text ("" or " ")
    gap_width: float = 0.0  # text-space width of the synthesized gap


@dataclass
class _Run:
    """A logical text run: show operands joined into one matchable string."""

    segments: list[_RunSegment]
    origin_trm: Optional[tuple]  # tm x ctm at the run start, if trackable
    geometry_ok: bool  # per-segment pens and advances are trustworthy


def _string_advance(
    raw: bytes,
    codec: Optional[CidTextCodec],
    metric: Any,
    size: float,
    char_spacing: float,
    word_spacing: float,
    h_scale: float,
) -> Optional[float]:
    """Text-space advance of a show string, or None when unmeasurable."""
    if metric is None:
        return None
    total = 0.0
    if codec is not None:
        if len(raw) % 2:
            return None
        for i in range(0, len(raw), 2):
            cid = int.from_bytes(raw[i : i + 2], "big")
            glyph = metric.width_of(cid) / 1000.0 * size
            # Word spacing applies only to single-byte code 32, never to
            # two-byte codes (PDF 32000-1, 9.3.3).
            total += (glyph + char_spacing) * h_scale
        return total
    if raw.startswith(b"\xfe\xff"):
        return None  # UTF-16BE operand -> byte codes are not glyph codes
    for code in raw:
        glyph = metric.width_of(code) / 1000.0 * size
        extra = char_spacing + (word_spacing if code == 32 else 0.0)
        total += (glyph + extra) * h_scale
    return total


def _walk_show_runs(
    tokens: list[_Token],
    *,
    codec_for_name: Optional[Callable[[Optional[str]], Optional[CidTextCodec]]] = None,
    metric_for_name: Optional[Callable[[str], Any]] = None,
    base_ctm: tuple = _ID_MATRIX,
) -> list[_Run]:
    """Walk a content stream and group show operands into logical runs.

    A run is a maximal sequence of show operands whose text reads as one
    string. Besides positionally-neutral operators, the following keep a run
    open: ``Tf``/``Tc``/``Tw``/``Tz``/``TL`` (the pen does not move), the
    line-moving show operators ``'``/``"``, and -- when advance widths are
    available from *metric_for_name* -- positioning operators (``Td``, ``TD``,
    ``Tm``, ``T*``) whose target stays on the same baseline within a small
    horizontal gap of the tracked pen; a word-sized gap synthesizes a space
    segment. Without metrics the pen is untrackable, so positioning operators
    break runs exactly as before.

    For composite fonts the segment codec falls back to one derived from the
    metric's ``code_to_text`` map, keeping editor and locator matching
    identical by construction.
    """
    runs: list[_Run] = []

    ctm = base_ctm
    gstack: list[tuple] = []
    tm = tlm = _ID_MATRIX
    tm_valid = True
    metric: Any = None
    codec: Optional[CidTextCodec] = None
    size = 0.0
    char_spacing = 0.0
    word_spacing = 0.0
    h_scale = 1.0
    leading = 0.0
    rise = 0.0

    cur: list[_RunSegment] = []
    origin_tm: Optional[tuple] = None
    origin_trm: Optional[tuple] = None
    geom_ok = False
    broke = True

    def close_run() -> None:
        nonlocal cur
        if cur:
            runs.append(_Run(cur, origin_trm, geom_ok))
            cur = []

    def start_run() -> None:
        nonlocal origin_tm, origin_trm, geom_ok
        close_run()
        origin_tm = tm if tm_valid else None
        origin_trm = _mat_mul(tm, ctm) if tm_valid else None
        geom_ok = tm_valid

    def add_string(tok: _Token) -> None:
        nonlocal broke, geom_ok, tm, tm_valid
        if broke:
            start_run()
            broke = False
        pen = (
            _text_delta(origin_tm, tm)
            if origin_tm is not None and tm_valid
            else None
        )
        if pen is None:
            geom_ok = False
        cur.append(
            _RunSegment(
                tok, codec, metric, size, char_spacing, word_spacing,
                h_scale, rise, pen,
            )
        )
        advance = _string_advance(
            tok.value, codec, metric, size, char_spacing, word_spacing, h_scale
        )
        if advance is None:
            geom_ok = False
            tm_valid = False
        else:
            tm = _mat_mul((1.0, 0.0, 0.0, 1.0, advance, 0.0), tm)

    def add_kern(value: float) -> None:
        nonlocal tm
        tm = _mat_mul(
            (1.0, 0.0, 0.0, 1.0, -value / 1000.0 * size * h_scale, 0.0), tm
        )

    def move_text_position(new_tm: tuple, new_tlm: tuple) -> None:
        """Apply a positioning operator, joining the run when it continues."""
        nonlocal tm, tlm, tm_valid, broke
        joined = False
        if cur and not broke and tm_valid and origin_tm is not None and size > 0:
            pen_end = _text_delta(origin_tm, tm)
            pen_new = _text_delta(origin_tm, new_tm)
            if pen_end is not None and pen_new is not None:
                dy = pen_new[1] - pen_end[1]
                gap = pen_new[0] - pen_end[0]
                if (
                    abs(dy) <= _JOIN_BASELINE_TOL * size
                    and _JOIN_MIN_GAP * size <= gap <= _JOIN_MAX_GAP * size
                ):
                    joined = True
                    if gap >= _JOIN_SPACE_GAP * size:
                        cur.append(
                            _RunSegment(
                                None, codec, metric, size, char_spacing,
                                word_spacing, h_scale, rise, pen_end,
                                gap_text=" ", gap_width=gap,
                            )
                        )
        tm, tlm = new_tm, new_tlm
        tm_valid = True
        if not joined:
            broke = True

    def resolve_font(name: Optional[str]) -> None:
        nonlocal metric, codec
        metric = metric_for_name(name) if metric_for_name and name else None
        codec = codec_for_name(name) if codec_for_name else None
        if codec is None and metric is not None:
            code_to_text = getattr(metric, "code_to_text", None)
            if code_to_text:
                codec = CidTextCodec(code_to_text)

    operands: list[tuple] = []
    i = 0
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        kind = tok.kind
        if kind == "string":
            operands.append(("str", tok))
            i += 1
            continue
        if kind == "name":
            operands.append(("name", tok.value.lstrip("/")))
            i += 1
            continue
        if kind == "array_start":
            arr: list = []
            i += 1
            while i < n and tokens[i].kind != "array_end":
                inner = tokens[i]
                if inner.kind == "string":
                    arr.append(inner)
                elif inner.kind == "word" and _is_number_word(inner.value):
                    arr.append(float(inner.value))
                i += 1
            i += 1  # skip array_end
            operands.append(("arr", arr))
            continue
        if kind == "array_end":
            i += 1
            continue
        if kind != "word":
            i += 1
            continue

        value = tok.value
        if _is_number_word(value):
            operands.append(("num", float(value)))
            i += 1
            continue

        if value == "Tj":
            seg = _last_operand(operands, "str")
            if seg is not None:
                add_string(seg)
        elif value == "TJ":
            arr = _last_operand(operands, "arr")
            if arr is not None:
                for item in arr:
                    if isinstance(item, _Token):
                        add_string(item)
                    else:
                        add_kern(item)
        elif value in _LINE_SHOW_OPS:
            # Line-moving show operators extend the current run (phrases may
            # span line breaks); the pen moves to the next line first.
            if value == '"':
                nums = [v for k, v in operands if k == "num"]
                if len(nums) >= 2:
                    word_spacing, char_spacing = nums[0], nums[1]
            tlm = _mat_mul((1.0, 0.0, 0.0, 1.0, 0.0, -leading), tlm)
            tm = tlm
            tm_valid = True
            seg = _last_operand(operands, "str")
            if seg is not None:
                add_string(seg)
        elif value == "Tf":
            name = _last_operand(operands, "name")
            nums = [v for k, v in operands if k == "num"]
            if nums:
                size = nums[-1]
            resolve_font(name)
            # The pen does not move: the run continues across a font change.
        elif value == "Tc":
            nums = [v for k, v in operands if k == "num"]
            if nums:
                char_spacing = nums[-1]
        elif value == "Tw":
            nums = [v for k, v in operands if k == "num"]
            if nums:
                word_spacing = nums[-1]
        elif value == "Tz":
            nums = [v for k, v in operands if k == "num"]
            if nums:
                h_scale = nums[-1] / 100.0
        elif value == "TL":
            nums = [v for k, v in operands if k == "num"]
            if nums:
                leading = nums[-1]
        elif value in ("Td", "TD"):
            nums = [v for k, v in operands if k == "num"]
            if len(nums) >= 2:
                tx, ty = nums[-2], nums[-1]
                if value == "TD":
                    leading = -ty
                new_tlm = _mat_mul((1.0, 0.0, 0.0, 1.0, tx, ty), tlm)
                move_text_position(new_tlm, new_tlm)
            else:
                broke = True
        elif value == "Tm":
            nums = [v for k, v in operands if k == "num"]
            if len(nums) >= 6:
                new = tuple(nums[-6:])
                move_text_position(new, new)
            else:
                broke = True
        elif value == "T*":
            new_tlm = _mat_mul((1.0, 0.0, 0.0, 1.0, 0.0, -leading), tlm)
            move_text_position(new_tlm, new_tlm)
        elif value == "BT":
            tm = tlm = _ID_MATRIX
            tm_valid = True
            broke = True
        elif value == "ET":
            broke = True
        elif value == "q":
            gstack.append(
                (
                    ctm, metric, codec, size, char_spacing, word_spacing,
                    h_scale, leading, rise,
                )
            )
            broke = True
        elif value == "Q":
            if gstack:
                (
                    ctm, metric, codec, size, char_spacing, word_spacing,
                    h_scale, leading, rise,
                ) = gstack.pop()
            broke = True
        elif value == "cm":
            nums = [v for k, v in operands if k == "num"]
            if len(nums) >= 6:
                ctm = _mat_mul(tuple(nums[-6:]), ctm)
            broke = True
        elif value == "Ts":
            nums = [v for k, v in operands if k == "num"]
            if nums:
                rise = nums[-1]
            broke = True
        elif value in _NEUTRAL_OPS:
            pass  # keeps the current run open
        else:
            broke = True

        operands = []
        i += 1

    close_run()
    return runs


def _last_operand(operands: list[tuple], kind: str):
    for entry_kind, entry_value in reversed(operands):
        if entry_kind == kind:
            return entry_value
    return None


def replace_text_in_content(
    content: bytes,
    search: str,
    replacement: str,
    *,
    case_sensitive: bool = True,
    max_count: int = 0,
    codec_for_name: Optional[Callable[[Optional[str]], Optional[CidTextCodec]]] = None,
    metric_for_name: Optional[Callable[[str], Any]] = None,
) -> tuple[bytes, int]:
    """Replace text inside PDF text-showing operands.

    ``max_count=0`` means unlimited. Replacement is attempted for literal and
    hex string operands used by ``Tj``, ``'``, ``"`` and inside ``TJ`` arrays,
    where the string elements are matched as one logical string. Consecutive
    text-showing operators (including line-moving operators ``'``/``"``, e.g. two
    adjacent ``Tj``, or a ``Tj`` followed by a ``'`` or ``TJ``) separated only by
    positionally-neutral operators are likewise joined into one logical run, so a
    phrase split across several show operators — common with kerning, per-word
    painting, or line breaks — is rewritten across the boundary: the replacement
    is placed in the first matched element and the remaining matched characters
    are removed from the others.

    Font and text-state changes (``Tf``/``Tc``/``Tw``/``Tz``/``TL``) do not
    move the pen and never break a run, so a phrase with a styled word in the
    middle is matched across the font change (each element keeps its own
    font's encoding). When *metric_for_name* provides advance widths, a
    positioning operator that continues the same baseline within a small gap
    also keeps the run open, and a word-sized gap is matched as a single
    space; without metrics any positioning or CTM change starts a new run.

    ``codec_for_name`` maps the segment's font resource name to a
    :class:`CidTextCodec` for composite (Type0) fonts, enabling matching over
    the ToUnicode-decoded text of two-byte show strings. Fonts without a codec
    use the default Latin-1/UTF-16BE operand decoding.
    """
    _validate_edit_args(search, max_count)
    tokens = _lex(content)
    runs = _walk_show_runs(
        tokens, codec_for_name=codec_for_name, metric_for_name=metric_for_name
    )
    replacements: list[tuple[int, int, bytes]] = []
    total = 0

    for run in runs:
        if max_count and total >= max_count:
            break
        remaining = 0 if max_count == 0 else max(max_count - total, 0)
        edits, count = _edit_run(
            run,
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

    Compatibility view over :func:`_walk_show_runs` (no metrics, so
    positioning operators break runs): each returned list holds the run's
    string-operand tokens in order.
    """
    return [
        [seg.token for seg in run.segments if seg.token is not None]
        for run in _walk_show_runs(tokens)
        if any(seg.token is not None for seg in run.segments)
    ]


def redact_text_in_content(
    content: bytes,
    search: str,
    *,
    case_sensitive: bool = True,
    max_count: int = 0,
    codec_for_name: Optional[Callable[[Optional[str]], Optional[CidTextCodec]]] = None,
    metric_for_name: Optional[Callable[[str], Any]] = None,
) -> tuple[bytes, int]:
    """Remove text from simple text-showing operands."""
    return replace_text_in_content(
        content,
        search,
        "",
        case_sensitive=case_sensitive,
        max_count=max_count,
        codec_for_name=codec_for_name,
        metric_for_name=metric_for_name,
    )


def _validate_edit_args(search: str, max_count: int) -> None:
    if not isinstance(search, str):
        raise TypeError("search must be a string")
    if search == "":
        raise ValueError("search must not be empty")
    if int(max_count) < 0:
        raise ValueError("max_count must be greater than or equal to zero")


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


def _run_char_data(run: _Run):
    """Decode a run into its logical string plus per-char metadata.

    Returns ``(infos, full, entries, seg_starts)``. ``infos[i]`` is
    ``(kind, text, units)`` per segment, where *kind* is ``"cid"`` (two-byte
    codes decoded through the segment codec, *units* from
    :meth:`CidTextCodec.decode_units`), ``"virtual"`` (a synthesized gap), or
    the operand encoding (``"latin-1"``/``"utf-16-be-bom"``). ``entries[g]``
    is ``(seg, unit, first, last, virtual)`` for the char at global index
    *g* -- *unit* is the code-unit index for CID segments and the char index
    otherwise. ``seg_starts[i]`` is the global index of segment *i*'s first
    char.
    """
    infos: list[tuple[str, str, Optional[list]]] = []
    for seg in run.segments:
        if seg.token is None:
            infos.append(("virtual", seg.gap_text, None))
        elif seg.codec is not None:
            units = seg.codec.decode_units(seg.token.value)
            infos.append(("cid", "".join(u[2] for u in units), units))
        else:
            text, encoding = _decode_operand(seg.token.value)
            infos.append((encoding, text, None))

    full_parts: list[str] = []
    entries: list[tuple[int, int, bool, bool, bool]] = []
    seg_starts: list[int] = []
    pos = 0
    for si, (kind, text, units) in enumerate(infos):
        seg_starts.append(pos)
        if kind == "cid":
            for ui, (_off, _length, unit_text) in enumerate(units):
                m = len(unit_text)
                for ci in range(m):
                    entries.append((si, ui, ci == 0, ci == m - 1, False))
        else:
            virtual = kind == "virtual"
            for ci in range(len(text)):
                entries.append((si, ci, True, True, virtual))
        full_parts.append(text)
        pos += len(text)
    return infos, "".join(full_parts), entries, seg_starts


def _aligned_spans(
    full: str,
    entries: list[tuple[int, int, bool, bool, bool]],
    search: str,
    case_sensitive: bool,
    max_count: int,
) -> list[tuple[int, int]]:
    """Match spans that cover whole code units and at least one real char.

    A span ending inside a multi-char code unit (a ligature) cannot be
    spliced code-exactly and is skipped; a span covering only synthesized gap
    chars has nothing to edit.
    """
    kept: list[tuple[int, int]] = []
    for s, e in _find_matches(full, search, case_sensitive, 0):
        if not entries[s][2] or not entries[e - 1][3]:
            continue
        if all(entries[i][4] for i in range(s, e)):
            continue
        kept.append((s, e))
        if max_count and len(kept) >= max_count:
            break
    return kept


def _edit_run(
    run: _Run,
    search: str,
    replacement: str,
    *,
    case_sensitive: bool,
    max_count: int,
) -> tuple[list[tuple[int, int, bytes]], int]:
    """Match *search* across the run's logical string and rewrite operands.

    The matched characters are removed from every element they cover and the
    replacement is injected into the element holding the first real matched
    char. Simple-font elements are re-encoded with their own literal/hex
    style and Latin-1/UTF-16BE encoding; composite-font elements are spliced
    byte-for-byte per two-byte code and the replacement is encoded through
    the reverse ToUnicode map (raising when unmappable). Untouched elements
    are left byte-for-byte intact; synthesized gaps have no bytes to edit.
    """
    segs = run.segments
    infos, full, entries, seg_starts = _run_char_data(run)
    if not full:
        return [], 0
    spans = _aligned_spans(full, entries, search, case_sensitive, max_count)
    if not spans:
        return [], 0

    covered = [False] * len(full)
    inject: dict[int, dict[int, Any]] = {}  # seg -> unit/char index -> payload
    for s, e in spans:
        for i in range(s, e):
            covered[i] = True
        target = next(i for i in range(s, e) if not entries[i][4])
        t_seg, t_unit = entries[target][0], entries[target][1]
        if infos[t_seg][0] == "cid":
            payload: Any = b""
            if replacement:
                encoded = segs[t_seg].codec.encode(replacement)
                if encoded is None:
                    raise PdfValidationException(
                        "Replacement text cannot be encoded with this font's "
                        "ToUnicode CMap."
                    )
                payload = encoded
        else:
            payload = replacement
        inject.setdefault(t_seg, {})[t_unit] = payload

    edits: list[tuple[int, int, bytes]] = []
    for si, seg in enumerate(segs):
        kind, text, units = infos[si]
        if kind == "virtual":
            continue
        g0 = seg_starts[si]
        if si not in inject and not any(
            covered[g0 + j] for j in range(len(text))
        ):
            continue  # leave unmatched elements byte-for-byte intact
        token = seg.token
        seg_inject = inject.get(si, {})
        if kind == "cid":
            raw = token.value
            out = bytearray()
            local = 0
            for ui, (off, length, unit_text) in enumerate(units):
                payload = seg_inject.get(ui)
                if payload is not None:
                    out += payload
                unit_covered = bool(unit_text) and covered[g0 + local]
                if not unit_covered:
                    out += raw[off : off + length]
                local += len(unit_text)
            edits.append(
                (token.start, token.end, _format_operand(bytes(out), token.style))
            )
        else:
            parts: list[str] = []
            for j, ch in enumerate(text):
                if j in seg_inject:
                    parts.append(seg_inject[j])
                if not covered[g0 + j]:
                    parts.append(ch)
            new_text = "".join(parts)
            if new_text == text:
                continue
            edits.append(
                (
                    token.start,
                    token.end,
                    _format_operand(_encode_operand(new_text, kind), token.style),
                )
            )
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

"""HarfBuzz shaping, Unicode bidi runs, font fallback, and line layout."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

from aspose_pdf.exceptions import FontEmbeddingException, PdfValidationException
from aspose_pdf.text_layout import TextLayoutOptions

_ZERO_WIDTH_TEXT = "\u200b"
_DEFAULT_IGNORABLES = frozenset({"\u200c", "\u200d", "\ufeff"})
_BIDI_ISOLATES = frozenset({"\u2066", "\u2067", "\u2068", "\u2069"})
_COMMON_SCRIPTS = frozenset({"Zinh", "Zyyy", "Zzzz"})


@dataclass(frozen=True, slots=True)
class GlyphPlacement:
    """One shaped glyph at an em-relative position within a laid-out line."""

    font_index: int
    gid: int
    unicode_text: str
    x: float
    y: float
    x_advance: float


@dataclass(frozen=True, slots=True)
class LayoutLine:
    """One visual line with logical replacement text and shaped glyphs."""

    text: str
    actual_text: str
    glyphs: tuple[GlyphPlacement, ...]
    width: float
    base_direction: str


@dataclass(frozen=True, slots=True)
class LayoutResult:
    """Complete line layout; glyph coordinates and widths are in em units."""

    lines: tuple[LayoutLine, ...]


@dataclass(frozen=True, slots=True)
class _BidiRun:
    text: str
    direction: str


def _load_dependencies() -> tuple[Any, Any, Any]:
    try:
        import uharfbuzz as hb
        from bidi import algorithm as bidi_algorithm
        from fontTools import unicodedata as unicode_data
    except ImportError as exc:
        raise PdfValidationException(
            "Complex text layout requires the optional 'text-layout' extra: "
            "pip install 'aspose-pdf-foss-for-python[text-layout]'"
        ) from exc
    return hb, bidi_algorithm, unicode_data


def _bidi_runs(text: str, direction: str, algorithm: Any) -> tuple[list[_BidiRun], str]:
    if any(character in _BIDI_ISOLATES for character in text):
        raise PdfValidationException(
            "Bidirectional isolate controls are not supported by complex-text "
            "layout."
        )
    storage = algorithm.get_empty_storage()
    base_dir = None if direction == "auto" else direction[0].upper()
    base_level = (
        algorithm.get_base_level(text)
        if base_dir is None
        else algorithm.PARAGRAPH_LEVELS[base_dir]
    )
    storage["base_level"] = base_level
    storage["base_dir"] = ("L", "R")[base_level]
    algorithm.get_embedding_levels(text, storage, False, False)
    for index, character in enumerate(storage["chars"]):
        character["index"] = index
    algorithm.explicit_embed_and_overrides(storage, False)
    algorithm.resolve_weak_types(storage, False)
    algorithm.resolve_neutral_types(storage, False)
    algorithm.resolve_implicit_levels(storage, False)
    algorithm.reorder_resolved_levels(storage, False)
    algorithm.apply_mirroring(storage, False)

    visual = storage["chars"]
    groups: list[list[dict[str, Any]]] = []
    for character in visual:
        if groups:
            previous = groups[-1][-1]
            step = -1 if int(character["level"]) % 2 else 1
            contiguous = int(character["index"]) == int(previous["index"]) + step
        else:
            contiguous = False
        if (
            not groups
            or groups[-1][-1]["level"] != character["level"]
            or not contiguous
        ):
            groups.append([character])
        else:
            groups[-1].append(character)

    runs: list[_BidiRun] = []
    for group in groups:
        level = int(group[0]["level"])
        logical = sorted(group, key=lambda item: int(item["index"]))
        run_text = "".join(str(item["ch"]) for item in logical)
        if run_text:
            runs.append(_BidiRun(run_text, "rtl" if level % 2 else "ltr"))
    return runs, "rtl" if storage["base_dir"] == "R" else "ltr"


def _grapheme_clusters(text: str) -> list[str]:
    clusters: list[str] = []
    for character in text:
        variation_selector = (
            0xFE00 <= ord(character) <= 0xFE0F
            or 0xE0100 <= ord(character) <= 0xE01EF
        )
        if (
            not clusters
            or (
                not unicodedata.combining(character)
                and not variation_selector
                and character != "\u200d"
                and not clusters[-1].endswith("\u200d")
            )
        ):
            clusters.append(character)
        else:
            clusters[-1] += character
    return clusters


def _font_supports_cluster(font: Any, cluster: str) -> bool:
    for character in cluster:
        if character in _DEFAULT_IGNORABLES or unicodedata.category(character) == "Cf":
            continue
        lookup = " " if character.isspace() else character
        if font.glyph_id(lookup) is None:
            return False
    return True


def _cluster_script(cluster: str, unicode_data: Any) -> str | None:
    for character in cluster:
        script = str(unicode_data.script(character))
        if script not in _COMMON_SCRIPTS:
            return script
    return None


def _resolve_scripts(
    clusters: Sequence[str],
    unicode_data: Any,
    explicit_script: str | None,
) -> list[str | None]:
    """Resolve each cluster's script, filling common/neutral runs from neighbours."""
    resolved = [
        explicit_script or _cluster_script(cluster, unicode_data)
        for cluster in clusters
    ]
    previous_script = None
    for index, script in enumerate(resolved):
        if script is not None:
            previous_script = script
        elif previous_script is not None:
            resolved[index] = previous_script
    next_script = None
    for index in range(len(resolved) - 1, -1, -1):
        script = resolved[index]
        if script is not None:
            next_script = script
        elif next_script is not None:
            resolved[index] = next_script
    return resolved


def _script_groups(
    text: str,
    unicode_data: Any,
    direction: str,
    explicit_script: str | None,
) -> list[tuple[str, str | None]]:
    """Group *text* into ``(run_text, script)`` spans, reversed for RTL."""
    clusters = _grapheme_clusters(text)
    scripts = _resolve_scripts(clusters, unicode_data, explicit_script)
    grouped: list[tuple[str, str | None]] = []
    for cluster, script in zip(clusters, scripts):
        if grouped and grouped[-1][1] == script:
            grouped[-1] = (grouped[-1][0] + cluster, script)
        else:
            grouped.append((cluster, script))
    if direction == "rtl":
        grouped.reverse()
    return grouped


def _font_script_runs(
    text: str,
    fonts: Sequence[Any],
    direction: str,
    unicode_data: Any,
    explicit_script: str | None,
) -> list[tuple[int, str, str | None]]:
    clusters = _grapheme_clusters(text)
    font_indices: list[int] = []
    for cluster in clusters:
        font_index = next(
            (
                index
                for index, font in enumerate(fonts)
                if _font_supports_cluster(font, cluster)
            ),
            None,
        )
        if font_index is None:
            visible = next(
                (
                    character
                    for character in cluster
                    if character not in _DEFAULT_IGNORABLES
                ),
                cluster[0],
            )
            raise FontEmbeddingException(
                f"No layout font has a glyph for U+{ord(visible):04X}."
            )
        font_indices.append(font_index)

    resolved_scripts = _resolve_scripts(clusters, unicode_data, explicit_script)
    grouped: list[tuple[int, str, str | None]] = []
    for font_index, cluster, script in zip(
        font_indices, clusters, resolved_scripts
    ):
        if grouped and grouped[-1][0] == font_index and grouped[-1][2] == script:
            grouped[-1] = (font_index, grouped[-1][1] + cluster, script)
        else:
            grouped.append((font_index, cluster, script))
    if direction == "rtl":
        grouped.reverse()
    return grouped


def _cluster_unicode_assignments(text: str, infos: Sequence[Any]) -> list[str]:
    starts = sorted({max(0, min(len(text), int(info.cluster))) for info in infos})
    boundaries = {
        start: starts[index + 1] if index + 1 < len(starts) else len(text)
        for index, start in enumerate(starts)
    }
    assignments = [_ZERO_WIDTH_TEXT] * len(infos)
    positions_by_cluster: dict[int, list[int]] = {}
    for index, info in enumerate(infos):
        start = max(0, min(len(text), int(info.cluster)))
        positions_by_cluster.setdefault(start, []).append(index)

    for start, positions in positions_by_cluster.items():
        source = text[start : boundaries.get(start, len(text))] or _ZERO_WIDTH_TEXT
        if len(positions) == 1:
            assignments[positions[0]] = source
            continue
        characters = list(source)
        for offset, position in enumerate(positions):
            if offset < len(characters):
                if offset == len(positions) - 1:
                    assignments[position] = "".join(characters[offset:])
                else:
                    assignments[position] = characters[offset]
            else:
                assignments[position] = _ZERO_WIDTH_TEXT
    return assignments


def shape_run(
    program: bytes,
    text: str,
    *,
    direction: str,
    script: str | None = None,
    features: Any = (),
    language: str | None = None,
    font_index: int = 0,
    origin_x: float = 0.0,
    hb: Any = None,
) -> tuple[list[GlyphPlacement], float]:
    """Shape *text* against one SFNT *program*; coords/advances in em units.

    A ``.notdef`` for a non-empty cluster raises ``FontEmbeddingException`` so
    callers can fall back when the program lacks a needed glyph. This is the
    low-level primitive shared by authored layout, the edit reshape path, and
    the substitute-font render path.
    """
    if hb is None:
        hb, _bidi, _unicode = _load_dependencies()
    face = hb.Face(program)
    hb_font = hb.Font(face)
    upem = int(face.upem) or 1000
    hb_font.scale = (upem, upem)
    buffer = hb.Buffer()
    buffer.add_str(text)
    buffer.guess_segment_properties()
    buffer.direction = direction
    if language is not None:
        buffer.language = language
    if script is not None:
        buffer.script = script
    hb.shape(hb_font, buffer, dict(features))
    infos = buffer.glyph_infos
    positions = buffer.glyph_positions
    assignments = _cluster_unicode_assignments(text, infos)

    placements: list[GlyphPlacement] = []
    pen_x = 0.0
    for info, position, unicode_text in zip(infos, positions, assignments):
        gid = int(info.codepoint)
        advance = float(position.x_advance) / upem
        x_offset = float(position.x_offset) / upem
        y_offset = float(position.y_offset) / upem
        if gid == 0:
            source = unicode_text.replace(_ZERO_WIDTH_TEXT, "")
            if source or abs(advance) > 1e-9:
                character = source[0] if source else text[0]
                raise FontEmbeddingException(
                    f"HarfBuzz produced .notdef for U+{ord(character):04X}."
                )
            pen_x += advance
            continue
        placements.append(
            GlyphPlacement(
                font_index=font_index,
                gid=gid,
                unicode_text=unicode_text,
                x=origin_x + pen_x + x_offset,
                y=y_offset,
                x_advance=advance,
            )
        )
        pen_x += advance
    return placements, pen_x


def _shape_line(
    text: str,
    fonts: Sequence[Any],
    options: TextLayoutOptions,
    hb: Any,
    bidi_algorithm: Any,
    unicode_data: Any,
) -> LayoutLine:
    display_text = text.replace("\t", "    ")
    bidi_runs, base_direction = _bidi_runs(
        display_text, options.direction, bidi_algorithm
    )
    placements: list[GlyphPlacement] = []
    pen_x = 0.0
    for bidi_run in bidi_runs:
        for font_index, run_text, script in _font_script_runs(
            bidi_run.text,
            fonts,
            bidi_run.direction,
            unicode_data,
            options.script,
        ):
            glyphs, advance = shape_run(
                fonts[font_index].shaping_program,
                run_text,
                direction=bidi_run.direction,
                script=script,
                features=options.features,
                language=options.language,
                font_index=font_index,
                origin_x=pen_x,
                hb=hb,
            )
            placements.extend(glyphs)
            pen_x += advance
    return LayoutLine(
        text=text,
        actual_text=text,
        glyphs=tuple(placements),
        width=max(0.0, pen_x),
        base_direction=base_direction,
    )


def shape_single_font_line(
    program: bytes,
    text: str,
    *,
    base_direction: str = "auto",
    features: Any = (),
    language: str | None = None,
    explicit_script: str | None = None,
) -> LayoutLine:
    """Bidi-reorder and shape one line of *text* against a single SFNT *program*.

    Unlike :func:`layout_text` there is no font fallback: every cluster must be
    covered by *program* or ``shape_run`` raises ``FontEmbeddingException``. Used
    by the edit reshape path (against the document's own embedded font) and the
    substitute-font render path. Glyph coordinates and advances are in em units.
    """
    hb, bidi_algorithm, unicode_data = _load_dependencies()
    display_text = text.replace("\t", "    ")
    bidi_runs, base_direction_resolved = _bidi_runs(
        display_text, base_direction, bidi_algorithm
    )
    placements: list[GlyphPlacement] = []
    pen_x = 0.0
    for bidi_run in bidi_runs:
        for run_text, script in _script_groups(
            bidi_run.text, unicode_data, bidi_run.direction, explicit_script
        ):
            glyphs, advance = shape_run(
                program,
                run_text,
                direction=bidi_run.direction,
                script=script,
                features=features,
                language=language,
                origin_x=pen_x,
                hb=hb,
            )
            placements.extend(glyphs)
            pen_x += advance
    return LayoutLine(
        text=text,
        actual_text=text,
        glyphs=tuple(placements),
        width=max(0.0, pen_x),
        base_direction=base_direction_resolved,
    )


def shape_join_preserving(program: bytes, text: str) -> dict[int, int] | None:
    """Map each input character index to its shaped glyph id, order-preserving.

    Shapes *text* against *program* and returns ``{cluster_index: gid}`` keeping
    the input order (no bidi reordering), so a renderer can swap an isolated
    glyph for its cursive-joined form at the character's stored position without
    moving anything. Ligatures collapse to the first covered index; indices with
    no entry were absorbed into a preceding glyph. Returns ``None`` on failure.
    """
    hb, _bidi, _unicode = _load_dependencies()
    face = hb.Face(program)
    font = hb.Font(face)
    upem = int(face.upem) or 1000
    font.scale = (upem, upem)
    buffer = hb.Buffer()
    buffer.add_str(text)
    buffer.guess_segment_properties()
    hb.shape(font, buffer, {})
    mapping: dict[int, int] = {}
    for info in buffer.glyph_infos:
        gid = int(info.codepoint)
        if gid == 0:
            continue  # .notdef: leave the original glyph in place
        mapping.setdefault(int(info.cluster), gid)
    return mapping


def needs_shaping(text: str) -> bool:
    """Whether *text* contains any RTL or complex-script scalar worth shaping.

    A cheap, dependency-free pre-check so the common LTR/ASCII edit keeps its
    byte-splice fast path and never imports the optional layout stack.
    """
    for character in text:
        if character in _BIDI_ISOLATES:
            return True
        bidi = unicodedata.bidirectional(character)
        if bidi in ("R", "AL", "AN"):
            return True
        # Combining marks imply positioning/reordering work (e.g. Indic, Thai).
        if unicodedata.combining(character):
            return True
    return False


def _split_long_token(
    token: str,
    max_width: float,
    shape: Any,
) -> list[str]:
    pieces: list[str] = []
    current = ""
    for cluster in _grapheme_clusters(token):
        candidate = current + cluster
        if current and shape(candidate).width > max_width:
            pieces.append(current)
            current = cluster
        else:
            current = candidate
    if current or not pieces:
        pieces.append(current)
    return pieces


def _wrap_paragraph(paragraph: str, max_width: float, shape: Any) -> list[str]:
    if not paragraph:
        return [""]
    lines: list[str] = []
    current = ""
    for token in re.findall(r"\s+|\S+", paragraph):
        candidate = current + token
        if not current or shape(candidate).width <= max_width:
            current = candidate
            if shape(current).width <= max_width:
                continue
        if current and current != token:
            lines.append(current)
            current = ""
        if token.isspace():
            if lines:
                lines[-1] += token
            else:
                current = token
            continue
        pieces = _split_long_token(token, max_width, shape)
        lines.extend(pieces[:-1])
        current = pieces[-1]
    if current or not lines:
        lines.append(current)
    return lines


def layout_text(
    text: str,
    fonts: Sequence[Any],
    options: TextLayoutOptions,
    *,
    font_size: float,
) -> LayoutResult:
    """Shape and lay out *text* into visual lines using prepared authored fonts."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if not fonts:
        raise PdfValidationException("Complex text layout requires a primary font.")
    hb, bidi_algorithm, unicode_data = _load_dependencies()
    cache: dict[str, LayoutLine] = {}

    def shape(value: str) -> LayoutLine:
        if value not in cache:
            cache[value] = _shape_line(
                value, fonts, options, hb, bidi_algorithm, unicode_data
            )
        return cache[value]

    max_width_em = (
        options.max_width / font_size if options.max_width is not None else None
    )
    result: list[LayoutLine] = []
    paragraphs = text.split("\n")
    for paragraph_index, paragraph in enumerate(paragraphs):
        line_texts = (
            _wrap_paragraph(paragraph, max_width_em, shape)
            if max_width_em is not None
            else [paragraph]
        )
        for line_index, line_text in enumerate(line_texts):
            line = shape(line_text)
            is_paragraph_end = line_index == len(line_texts) - 1
            if is_paragraph_end and paragraph_index < len(paragraphs) - 1:
                line = replace(line, actual_text=line.actual_text + "\n")
            result.append(line)
    return LayoutResult(tuple(result))

"""HarfBuzz shaping, Unicode bidi runs, font fallback, and line layout."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from typing import Any, Sequence

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


def _font_script_runs(
    text: str,
    fonts: Sequence[Any],
    direction: str,
    unicode_data: Any,
    explicit_script: str | None,
) -> list[tuple[int, str, str | None]]:
    selected: list[tuple[int, str, str | None]] = []
    for cluster in _grapheme_clusters(text):
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
        script = explicit_script or _cluster_script(cluster, unicode_data)
        selected.append((font_index, cluster, script))

    resolved_scripts = [item[2] for item in selected]
    previous_script = None
    for index, script in enumerate(resolved_scripts):
        if script is not None:
            previous_script = script
        elif previous_script is not None:
            resolved_scripts[index] = previous_script
    next_script = None
    for index in range(len(resolved_scripts) - 1, -1, -1):
        script = resolved_scripts[index]
        if script is not None:
            next_script = script
        elif next_script is not None:
            resolved_scripts[index] = next_script

    grouped: list[tuple[int, str, str | None]] = []
    for (font_index, cluster, _script), script in zip(selected, resolved_scripts):
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


def _shape_font_run(
    text: str,
    *,
    font: Any,
    font_index: int,
    direction: str,
    script: str | None,
    options: TextLayoutOptions,
    hb: Any,
    origin_x: float,
) -> tuple[list[GlyphPlacement], float]:
    face = hb.Face(font.shaping_program)
    hb_font = hb.Font(face)
    upem = int(face.upem) or 1000
    hb_font.scale = (upem, upem)
    buffer = hb.Buffer()
    buffer.add_str(text)
    buffer.guess_segment_properties()
    buffer.direction = direction
    if options.language is not None:
        buffer.language = options.language
    if script is not None:
        buffer.script = script
    hb.shape(hb_font, buffer, dict(options.features))
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
            glyphs, advance = _shape_font_run(
                run_text,
                font=fonts[font_index],
                font_index=font_index,
                direction=bidi_run.direction,
                script=script,
                options=options,
                hb=hb,
                origin_x=pen_x,
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

"""Compose existing text and visual comparisons into a bounded offline report."""

from __future__ import annotations

import base64
import html
from itertools import zip_longest

from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.load_limits import _combined_limits, _LoadBudget

from . import visual_comparison
from .text_selection import page_indexes

_STYLE = """
:root{color-scheme:light;--ink:#17243b;--muted:#52627a;--line:#dbe3ed;
--accent:#215bb5;--paper:#fff;--canvas:#f3f6fa}
*{box-sizing:border-box}html{scroll-behavior:smooth}
body{margin:0;background:var(--canvas);color:var(--ink);
font:15px/1.55 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
header,main,footer{max-width:1280px;margin:auto;padding:28px 32px}
header{padding-top:42px;padding-bottom:12px}h1{font-size:32px;line-height:1.2;
letter-spacing:-.03em;margin:8px 0 12px;overflow-wrap:anywhere}
h2{font-size:20px;margin:0 0 16px}h3{font-size:15px;margin:0 0 10px}
p{margin:8px 0}.eyebrow{font-size:12px;font-weight:700;letter-spacing:.14em;
color:var(--accent)}.muted,footer{color:var(--muted)}
.sources{font-size:17px;overflow-wrap:anywhere}.sources span{padding:0 10px}
.settings{margin-top:20px;font-size:13px}.settings summary{cursor:pointer}
.settings p{max-width:90ch}.stats{display:grid;grid-template-columns:repeat(4,1fr);
gap:12px;margin-bottom:24px}.stat{background:var(--paper);border:1px solid var(--line);
border-radius:12px;padding:18px}.stat strong{display:block;font-size:28px}
.stat span{color:var(--muted);font-size:13px}.table-wrap{overflow-x:auto;
border:1px solid var(--line);border-radius:12px;background:var(--paper);margin-bottom:28px}
table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;
padding:12px 16px;border-bottom:1px solid var(--line);vertical-align:top}
th{background:#eaf0f8;font-size:12px;color:var(--muted)}tr:last-child td{border-bottom:0}
a{color:var(--accent);text-decoration:underline;text-underline-offset:3px}
.badge{display:inline-block;border-radius:20px;padding:3px 10px;font-size:12px;
font-weight:650;white-space:nowrap;background:#e9f5ee;color:#246040}
.badge.changed,.badge.missing{background:#fff0e9;color:#93451d}
.badge.allowed{background:#fff6d9;color:#785500}
.pair{border:1px solid var(--line);border-radius:12px;margin:0 0 18px;
background:var(--paper);scroll-margin-top:16px;overflow:hidden}
.pair>summary{padding:18px 20px;cursor:pointer;font-weight:650}
.pair>summary .badge{margin-left:12px}.pair-content{border-top:1px solid var(--line);
padding:20px;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:24px}
.pair-content.single{grid-template-columns:1fr}.pair-content>section{min-width:0}
dl{display:grid;grid-template-columns:1fr 1fr;gap:4px 16px;font-size:13px;margin:0 0 16px}
dt{color:var(--muted)}dd{margin:0;overflow-wrap:anywhere}
figure{margin:0}img{display:block;max-width:100%;height:auto;border:1px solid var(--line);
background:white}figcaption{font-size:12px;color:var(--muted);margin-top:8px}
pre{margin:0;padding:16px;background:#f7f9fc;border:1px solid var(--line);
border-radius:8px;overflow:auto;max-height:640px;font:12px/1.65 ui-monospace,
SFMono-Regular,Consolas,monospace;tab-size:4}
.diff-add{background:#e2f4e7;color:#1b5930}.diff-del{background:#fde9e7;color:#8e3027}
.diff-meta{color:#52627a}.diff-hunk{color:#214e96;background:#e8effa}
.empty{background:white;border:1px solid var(--line);border-radius:12px;padding:24px}
footer{font-size:12px;padding-top:0}
@media(max-width:800px){header,main,footer{padding-left:16px;padding-right:16px}
.stats{grid-template-columns:repeat(2,1fr)}.pair-content{grid-template-columns:1fr}
h1{font-size:26px}th,td{padding:10px}.sources span{padding:0 4px}}
@media print{body{background:white}header,main,footer{max-width:none;padding:12px 0}
.pair{break-inside:avoid}.settings{display:none}pre{max-height:none;white-space:pre-wrap}
a{color:inherit}.stats{gap:8px}}
"""


def _text(value, name, budget):
    if not isinstance(value, str) or not value.strip():
        raise PdfValidationException(f"{name} must be a non-blank string")
    budget.check(len(value), "max_object_bytes", "comparison report text")
    size = 0
    try:
        for offset in range(0, len(value), 4096):
            size += len(value[offset : offset + 4096].encode("utf-8"))
            budget.check(size, "max_object_bytes", "comparison report text")
    except UnicodeError as exc:
        raise PdfValidationException(f"{name} contains invalid Unicode") from exc


class _HTML:
    """Share an output budget across fragments assembled in document order."""

    def __init__(self, budget, state=None):
        self.budget = budget
        self.state = [0, 0] if state is None else state
        self.parts = []

    def check_size(self, extra):
        for limit in ("max_input_bytes", "max_total_decoded_bytes"):
            self.budget.check(self.state[0] + extra, limit, "comparison HTML bytes")

    def write(self, data):
        self.check_size(len(data))
        self.budget.check(
            self.state[1] + 1, "max_container_items", "comparison HTML fragments"
        )
        self.state[0] += len(data)
        self.state[1] += 1
        self.parts.append(data)

    def text(self, value):
        try:
            for offset in range(0, len(value), 4096):
                self.write(
                    html.escape(value[offset : offset + 4096], quote=True).encode(
                        "utf-8"
                    )
                )
        except UnicodeError as exc:
            raise PdfValidationException(
                "Comparison report contains invalid Unicode"
            ) from exc

    def fork(self):
        return _HTML(self.budget, self.state)

    def include(self, other):
        self.parts.extend(other.parts)
        other.parts.clear()

    def image(self, image, label):
        self.budget.check(
            image.width * image.height * 12 + image.height,
            "max_codec_work_bytes",
            "comparison report PNG working bytes",
        )
        png = image.to_png()
        encoded_size = 4 * ((len(png) + 2) // 3)
        self.budget.check(
            encoded_size, "max_object_bytes", "comparison report image URL"
        )
        self.check_size(encoded_size)
        self.write(b'<figure><img loading="lazy" alt="')
        self.text(label)
        self.write(b'" src="data:image/png;base64,')
        self.write(base64.b64encode(png))
        self.write(
            b'"><figcaption>Red: changed pixels. Blue: ignored areas. '
            b"Pale gray: page context.</figcaption></figure>"
        )


def _status(text, visual, missing):
    matches = (text is None or text.matches) and (visual is None or visual.matches)
    if missing:
        return False, "missing", "Missing page"
    if not matches:
        return False, "changed", "Changed"
    if visual is not None and visual.changed_pixels:
        return True, "allowed", "Within tolerance"
    if text is not None and text.text != text.other_text:
        return True, "allowed", "Normalized text match"
    return True, "match", "Match"


def _badge(writer, kind, label):
    writer.write(f'<span class="badge {kind}">'.encode())
    writer.text(label)
    writer.write(b"</span>")


def _metric(writer, label, value):
    writer.write(b"<dt>")
    writer.text(label)
    writer.write(b"</dt><dd>")
    writer.text(str(value))
    writer.write(b"</dd>")


def _diff(writer, value):
    writer.write(b'<pre class="diff" tabindex="0" aria-label="Text changes"><code>')
    for number, line in enumerate(value.splitlines(keepends=True)):
        if number < 2 or line.startswith("\\"):
            kind = "meta"
        elif line.startswith("@@"):
            kind = "hunk"
        elif line.startswith("+"):
            kind = "add"
        elif line.startswith("-"):
            kind = "del"
        else:
            kind = "context"
        writer.write(f'<span class="diff-{kind}">'.encode())
        writer.text(line)
        writer.write(b"</span>")
    writer.write(b"</code></pre>")


def _pair(body, rows, number, index, other_index, text, visual, mode):
    missing = index is None or other_index is None
    matches, kind, status = _status(text, visual, missing)
    left = str(index + 1) if index is not None else "Missing"
    right = str(other_index + 1) if other_index is not None else "Missing"
    label = f"Page {left} → page {right}"
    rows.write(f'<tr data-pair="{number}"><td><a href="#pair-{number}">'.encode())
    rows.text(left)
    rows.write(b"</a></td><td>")
    rows.text(right)
    rows.write(b"</td><td>")
    _badge(rows, kind, status)
    rows.write(b"</td><td>")
    rows.text(f"{visual.difference_ratio * 100:.6g}%" if visual else "—")
    rows.write(b"</td><td>")
    rows.text("Match" if text and text.matches else "Changed" if text else "—")
    rows.write(b"</td></tr>")
    opened = " open" if kind != "match" else ""
    body.write(f'<details class="pair" id="pair-{number}"{opened}><summary>'.encode())
    body.text(label)
    _badge(body, kind, status)
    body.write(b"</summary>")
    body.write(
        f'<div class="pair-content{" single" if mode != "both" else ""}">'.encode()
    )
    if visual is not None:
        body.write(b'<section class="visual"><h3>Visual comparison</h3><dl>')
        for name, value in (
            (
                "Result",
                "Within tolerance"
                if visual.matches and visual.changed_pixels
                else "Match"
                if visual.matches
                else "Changed",
            ),
            ("Changed pixels", f"{visual.changed_pixels:,} / {visual.total_pixels:,}"),
            ("Changed area", f"{visual.difference_ratio * 100:.6g}%"),
            ("Ignored pixels", f"{visual.ignored_pixels:,}"),
            (
                "Original size (px)",
                " x ".join(map(str, visual.size)) if visual.size else "Missing",
            ),
            (
                "Revised size (px)",
                " x ".join(map(str, visual.other_size))
                if visual.other_size
                else "Missing",
            ),
            (
                "Changed region (px)",
                ", ".join(map(str, visual.bbox)) if visual.bbox else "None",
            ),
        ):
            _metric(body, name, value)
        body.write(b"</dl>")
        if visual.diff_image is not None:
            body.image(visual.diff_image, f"Visual differences: {label}")
        body.write(b"</section>")
    if text is not None:
        body.write(b'<section class="text"><h3>Text comparison</h3>')
        if text.matches:
            body.write(
                b'<p class="muted">No text differences under the selected settings.</p>'
            )
        else:
            _diff(body, text.unified_diff)
        body.write(b"</section>")
    body.write(b"</div></details>")
    return matches, missing


def export(
    document,
    other,
    *,
    pages,
    other_pages,
    mode,
    dpi,
    tolerance,
    max_diff_ratio,
    include_annotations,
    include_diff_images,
    ignore_regions,
    case_sensitive,
    ignore_whitespace,
    skip_artifacts,
    title,
    left_label,
    right_label,
):
    """Validate selections first, then serialize one comparison pair at a time."""
    budget = _LoadBudget(_combined_limits(document.load_limits, other.load_limits))
    if not isinstance(mode, str) or mode not in ("both", "text", "visual"):
        raise PdfValidationException("mode must be 'both', 'text', or 'visual'")
    for name, value in (
        ("case_sensitive", case_sensitive),
        ("ignore_whitespace", ignore_whitespace),
        ("skip_artifacts", skip_artifacts),
    ):
        if not isinstance(value, bool):
            raise PdfValidationException(f"{name} must be a boolean")
    for name, value in (
        ("title", title),
        ("left_label", left_label),
        ("right_label", right_label),
    ):
        _text(value, name, budget)
    regions = visual_comparison._regions(ignore_regions, budget)
    visual_options = dict(
        dpi=dpi,
        tolerance=tolerance,
        max_diff_ratio=max_diff_ratio,
        include_annotations=include_annotations,
        include_diff_images=include_diff_images,
        ignore_regions=regions,
    )
    visual_comparison.compare_visual(
        document, other, pages=(), other_pages=(), **visual_options
    )
    selection = page_indexes(pages, document.page_count, limits=budget.limits)
    other_selection = page_indexes(other_pages, other.page_count, limits=budget.limits)
    selection = range(document.page_count) if selection is None else selection
    other_selection = (
        range(other.page_count) if other_selection is None else other_selection
    )
    count = max(len(selection), len(other_selection))
    budget.check(count, "max_container_items", "comparison report page pairs")
    budget.check_pages(count)
    plans = list(zip_longest(selection, other_selection))
    retained_pixels = 0
    if mode != "text":
        for index, other_index in plans:
            size = visual_comparison._size(document, index, dpi)
            other_size = visual_comparison._size(other, other_index, dpi)
            width = max(size[0] if size else 0, other_size[0] if other_size else 0)
            height = max(size[1] if size else 0, other_size[1] if other_size else 0)
            budget.check_raster_pixels(width, height, "comparison report canvas")
            if include_diff_images:
                retained_pixels += width * height
                budget.check_raster_pixels(
                    retained_pixels, 1, "comparison report images"
                )

    output = _HTML(budget)
    output.write(
        b'<!doctype html><html lang="en"><head><meta charset="utf-8">'
        b'<meta name="viewport" content="width=device-width,initial-scale=1">'
        b'<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
        b"img-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'\">"
        b"<title>"
    )
    output.text(title)
    output.write(
        b"</title><style>" + _STYLE.encode() + b"</style></head><body><header>"
    )
    output.write(b'<div class="eyebrow">PDF REVIEW</div><h1>')
    output.text(title)
    output.write(b'</h1><p class="sources">')
    output.text(left_label)
    output.write('<span aria-label="compared with">→</span>'.encode())
    output.text(right_label)
    output.write(
        b'</p><details class="settings"><summary>Comparison settings</summary><p>'
    )
    settings = [f"Mode: {mode}."]
    if mode != "text":
        settings.append(
            f"Visual: {dpi} DPI, color tolerance {tolerance}/255, "
            f"allowed changed area {max_diff_ratio * 100:.6g}%, annotations "
            f"{'included' if include_annotations else 'excluded'}, "
            f"{len(regions)} ignored regions."
        )
    if mode != "visual":
        settings.append(
            f"Text: {'case sensitive' if case_sensitive else 'case insensitive'}, "
            f"whitespace {'normalized' if ignore_whitespace else 'preserved'}, "
            f"marked artifacts {'excluded' if skip_artifacts else 'included'}."
        )
    output.text(" ".join(settings))
    output.write(b"</p></details></header><main>")
    body, rows = output.fork(), output.fork()
    matched = missing = 0
    for number, (index, other_index) in enumerate(plans, 1):
        left_pages = () if index is None else (index,)
        right_pages = () if other_index is None else (other_index,)
        text = (
            document.compare_text(
                other,
                pages=left_pages,
                other_pages=right_pages,
                case_sensitive=case_sensitive,
                ignore_whitespace=ignore_whitespace,
                skip_artifacts=skip_artifacts,
            )[0]
            if mode != "visual"
            else None
        )
        visual = (
            document.compare_visual(
                other,
                pages=left_pages,
                other_pages=right_pages,
                **visual_options,
            )[0]
            if mode != "text"
            else None
        )
        matches, absent = _pair(
            body, rows, number, index, other_index, text, visual, mode
        )
        matched += matches
        missing += absent
        del text, visual
    output.write(b'<div class="stats">')
    for value, label in (
        (count, "Page pairs"),
        (count - matched, "Needs review"),
        (matched, "Matches"),
        (missing, "Missing pages"),
    ):
        output.write(
            f'<div class="stat"><strong>{value}</strong><span>{label}</span></div>'.encode()
        )
    output.write(b"</div><h2>Page summary</h2>")
    if count:
        output.write(b'<div class="table-wrap"><table><thead><tr><th scope="col">')
        output.text(left_label)
        output.write(b' page</th><th scope="col">')
        output.text(right_label)
        output.write(
            b' page</th><th scope="col">Result</th><th scope="col">Visual change</th>'
            b'<th scope="col">Text</th></tr></thead><tbody>'
        )
        output.include(rows)
        output.write(b"</tbody></table></div>")
        output.include(body)
    else:
        output.write(b'<p class="empty">No pages selected for comparison.</p>')
    output.write(
        b"</main><footer>Pages are paired by selection order. Visual checks depend on "
        b"renderer coverage and resolution; text checks use the existing text layer."
        b"</footer></body></html>\n"
    )
    return b"".join(output.parts)

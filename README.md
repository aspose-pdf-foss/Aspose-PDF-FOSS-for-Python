# Aspose.PDF FOSS for Python

[![CI](https://github.com/aspose-pdf-foss/Aspose-PDF-FOSS-for-Python/actions/workflows/ci.yml/badge.svg)](https://github.com/aspose-pdf-foss/Aspose-PDF-FOSS-for-Python/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Aspose.PDF FOSS for Python is an open-source Python library for creating,
reading, editing, rendering, and validating PDF documents.

The package is implemented in Python and ships type information. The project is
currently in alpha, so APIs and feature coverage may evolve before the first
stable release.

## Features

- Create, load, save, merge, split, and inspect PDF documents
- Add Standard-14 or embedded Unicode text, including shaped bidirectional
  text, plus images, lines, rectangles, annotations, attachments, and form data
- Extract text, images, attachments, metadata, and bookmarks
- Render pages to PNG or TIFF
- Replace or redact text in supported content streams
- Encrypt and decrypt documents with RC4 or AES
- Create and inspect PDF signatures
- Optimize streams, images, fonts, and unused objects
- Apply configurable resource limits when processing untrusted PDFs
- Work with XMP metadata and low-level PDF objects
- Perform heuristic PDF/A and PDF/UA checks and conversions

See the [supported features](supported-features.md) document for the
detailed capability matrix and known limitations.

## Requirements

- Python 3.11 or newer
- `cryptography`
- `asn1crypto`

Optional extras add Pillow-based image support, Brotli-based WOFF2 decoding,
and HarfBuzz/bidi complex-text layout:

```bash
python -m pip install 'aspose-pdf-foss-for-python[images,woff2,text-layout]'
```

## Installation

Install a published prerelease:

```bash
python -m pip install --pre aspose-pdf-foss-for-python
```

Install the latest source checkout for development:

```bash
git clone https://github.com/aspose-pdf-foss/Aspose-PDF-FOSS-for-Python.git
cd Aspose-PDF-FOSS-for-Python
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

## Quick Start

### Create a PDF

```python
from aspose_pdf import Document

with Document() as document:
    page = document.pages.add()
    page.add_text(
        "Hello from Aspose.PDF FOSS!",
        x=72,
        y=720,
        font_size=18,
    )
    document.save("hello.pdf")
```

For Unicode outside the Standard-14 encodings, provide an embeddable
TrueType/OpenType font as bytes, a path, or a `FontDescriptor`:

```python
with Document() as document:
    page = document.pages.add()
    page.add_text(
        "Latin Č · Кириллица · Ελληνικά · 漢字",
        x=72,
        y=720,
        font="NotoSans-Regular.ttf",
    )
    document.save("unicode.pdf")
```

The writer creates a subset Type0/CID font, two-byte character codes,
`/ToUnicode`, widths, and the required CID-to-glyph mapping. A character that
the supplied font cannot represent raises `FontEmbeddingException` instead of
silently writing `.notdef`.

Install the `text-layout` extra and pass `TextLayoutOptions` for OpenType
shaping, bidirectional runs, ordered font fallback, and width-constrained line
layout:

```python
from aspose_pdf import Document, TextLayoutOptions

with Document() as document:
    page = document.pages.add()
    page.add_text(
        "English العربية 123",
        x=72,
        y=720,
        font_size=16,
        font="NotoSansArabic-Regular.ttf",
        layout=TextLayoutOptions(
            fallback_fonts=["NotoSans-Regular.ttf"],
            max_width=300,
            alignment="start",
            language="ar",
        ),
    )
    document.save("complex-text.pdf")
```

Logical text is retained for extraction while glyphs are painted in their
shaped visual order.

### Read a document

```python
from aspose_pdf import Document

with Document() as document:
    document.load_from("input.pdf")

    print(f"Pages: {document.page_count}")
    print(f"PDF version: {document.version}")
    print(document.info)
```

### Extract text

```python
from aspose_pdf import PdfExtractor

with PdfExtractor() as extractor:
    extractor.bind_pdf("input.pdf")
    extractor.extract_text()
    print(extractor.get_text())
```

### Merge PDF files

```python
from aspose_pdf import PdfFileEditor

with PdfFileEditor() as editor:
    if not editor.concatenate(["part-1.pdf", "part-2.pdf"], "merged.pdf"):
        raise RuntimeError(editor.last_exception)
```

### Render a page

```python
from aspose_pdf import Document

with Document() as document:
    document.load_from("input.pdf")
    document.pages[0].save_as_image("page-1.png", dpi=144)
```

## Feature Boundaries

This project aims to fail explicitly when an operation is unsupported, but PDF
is a large format and coverage is not yet complete.

- Page rendering is best effort and does not implement every PDF graphics
  feature.
- PDF/A and PDF/UA validation is heuristic, not certification-grade.
- OCR and layout reflow are not implemented.
- Signature-chain, revocation, and timestamp validation have documented
  limitations.
- Compatibility modules may expose names whose operations are not implemented.

Review [supported-features.md](supported-features.md) before relying
on the library for compliance-sensitive or security-sensitive workflows.

## Development

Activate the project virtual environment and install development dependencies:

```bash
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

Run lint and tests:

```bash
python -m ruff check src/
python -m pytest -q
```

Build and validate the distributions:

```bash
python -m build
python -m twine check dist/*
```

The convenience scripts run the standard checks:

```bash
scripts/check.sh
scripts/build.sh
```

## Repository Map

| Path | Description |
| --- | --- |
| `src/aspose_pdf/` | Public Python package |
| `src/aspose_pdf/engine/` | PDF parser, writer, filters, renderer, encryption, and signing internals |
| `src/aspose_pdf/generated/` | Supported API compatibility modules |
| `tests/` | Unit, regression, and integration tests |
| `supported-features.md` | Detailed feature coverage and limitations |
| `scripts/` | Local check and build commands |
| `.github/workflows/` | CI and publishing workflows |

## Contributing

Issues and pull requests are welcome. Please:

1. Keep changes focused.
2. Add tests for new behavior and bug fixes.
3. Write code comments and docstrings in English.
4. Run `python -m ruff check src/` and `python -m pytest -q`.
5. Document public API changes and important limitations.

When reporting a parser or rendering problem, include a minimal PDF that can be
shared publicly whenever possible.

## Security

PDF files are untrusted binary input. Loading uses a generous default
`PdfLoadLimits` policy that bounds input size, parser/object complexity,
decoded streams, page content, images, and rasterization. Customize the policy
when an application needs tighter limits:

```python
from aspose_pdf import Document, PdfLoadLimits, PdfResourceLimitException

limits = PdfLoadLimits(
    max_input_bytes=64 * 1024 * 1024,
    max_decoded_stream_bytes=16 * 1024 * 1024,
    max_image_pixels=25_000_000,
)

try:
    with Document(limits=limits) as document:
        document.load_from("input.pdf")
except PdfResourceLimitException as error:
    print(f"PDF rejected: {error}")
```

The same `limits=` argument is accepted by `Document.load_from()` and
`Document.open_streaming()`; lazy decoding continues to use the document's
shared budget. `PdfLoadLimits.unlimited()` disables every safeguard and should
only be used for trusted input in an environment with external resource
controls. These limits reduce known parser and allocation risks but are not an
exhaustive DoS sandbox; isolate highly hostile workloads at the process level.

If you discover a security issue, follow the [security policy](SECURITY.md) and
use GitHub private vulnerability reporting instead of opening a public issue.

## License

Aspose.PDF FOSS for Python is licensed under the [MIT License](LICENSE).

Copyright © 2026 Aspose Pty Ltd.

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
- Add text, images, lines, rectangles, annotations, attachments, and form data
- Extract text, images, attachments, metadata, and bookmarks
- Render pages to PNG or TIFF
- Replace or redact text in supported content streams
- Encrypt and decrypt documents with RC4 or AES
- Create and inspect PDF signatures
- Optimize streams, images, fonts, and unused objects
- Work with XMP metadata and low-level PDF objects
- Perform heuristic PDF/A and PDF/UA checks and conversions

See the [supported features](supported-features.md) document for the
detailed capability matrix and known limitations.

## Requirements

- Python 3.11 or newer
- `cryptography`
- `asn1crypto`

Optional extras add Pillow-based image support and Brotli-based WOFF2 decoding:

```bash
python -m pip install 'aspose-pdf-foss-for-python[images,woff2]'
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

PDF files are untrusted binary input. If you discover a security issue, please
follow the [security policy](SECURITY.md) and use GitHub private vulnerability
reporting instead of opening a public issue.

## License

Aspose.PDF FOSS for Python is licensed under the [MIT License](LICENSE).

Copyright © 2026 Aspose Pty Ltd.

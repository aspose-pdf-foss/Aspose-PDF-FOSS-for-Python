"""Explicit rejection of compatibility names that have no implementation.

The package ships option, enumeration, and value objects for the parts of the
Aspose.PDF API this project does not implement (HTML/SVG/CGM/OFD/CDR/Markdown/
LaTeX conversion, PPTX export, printing). They exist so that porting code keeps
importing, not so that it silently produces nothing: every real operation routes
its format/option arguments through the helpers below and raises
:class:`~aspose_pdf.exceptions.UnsupportedFeatureException` instead.

Surfaces are matched by class name rather than by import so that this module
stays free of package-level import cycles and keeps working for the thin
subclasses in :mod:`aspose_pdf.svg` and friends.
"""

from __future__ import annotations

from typing import Any, NoReturn

from aspose_pdf.exceptions import UnsupportedFeatureException

__all__ = [
    "describe",
    "reject_load_options",
    "require_pdf_save_format",
]

_DOC_POINTER = (
    "See 'Known Unsupported Compatibility Surfaces' in supported-features.md."
)

# Class name -> feature the class stands for.
_UNSUPPORTED_TYPES: dict[str, str] = {
    "CdrLoadOptions": "CorelDRAW (CDR) import",
    "CgmLoadOptions": "CGM import",
    "HtmlLoadOptions": "HTML import",
    "LatexFragment": "LaTeX authoring",
    "OfdLoadOptions": "OFD import",
    "PrinterSettings": "printing",
    "SvgLoadOptions": "SVG import",
}

# (enum class name, member name) -> feature the member stands for.
_UNSUPPORTED_MEMBERS: dict[tuple[str, str], str] = {
    ("SaveFormat", "PPTX"): "PPTX export",
}

# (enum class name, member name) pairs that mean "plain PDF".
_PDF_MEMBERS: frozenset[tuple[str, str]] = frozenset(
    {("DocFormat", "PDF"), ("SaveFormat", "PDF")}
)


def _is_package_class(cls: type) -> bool:
    module = getattr(cls, "__module__", "")
    return module == "aspose_pdf" or module.startswith("aspose_pdf.")


def _member_key(value: Any) -> tuple[str, str] | None:
    name = getattr(value, "name", None)
    if not isinstance(name, str):
        return None
    cls = type(value)
    if not _is_package_class(cls):
        return None
    return (cls.__name__, name)


def describe(value: Any) -> str | None:
    """Return the unimplemented feature *value* stands for, else ``None``."""
    if value is None:
        return None

    key = _member_key(value)
    if key is not None and key in _UNSUPPORTED_MEMBERS:
        return _UNSUPPORTED_MEMBERS[key]

    for cls in type(value).__mro__:
        if not _is_package_class(cls):
            continue
        feature = _UNSUPPORTED_TYPES.get(cls.__name__)
        if feature is not None:
            return feature
    return None


def _fail(feature: str, subject: str) -> NoReturn:
    raise UnsupportedFeatureException(
        f"{feature} is not implemented; {subject} is an API-compatibility "
        f"placeholder. {_DOC_POINTER}"
    )


def reject_load_options(value: Any) -> NoReturn:
    """Reject a non-``None`` load-options argument.

    No load options are implemented: importing a non-PDF format is out of
    scope, and PDF loading is configured through ``password`` and ``limits``.
    """
    feature = describe(value)
    if feature is not None:
        _fail(feature, f"`{type(value).__name__}`")
    raise TypeError(
        "load options are not supported; loading accepts PDF data only, "
        "configured through the password and limits keywords. "
        f"{_DOC_POINTER}"
    )


def require_pdf_save_format(value: Any) -> None:
    """Accept ``None`` or a PDF save format; reject every other export."""
    if value is None:
        return

    key = _member_key(value)
    if key is not None:
        if key in _PDF_MEMBERS:
            return
        feature = _UNSUPPORTED_MEMBERS.get(key)
        if feature is not None:
            _fail(feature, f"`{key[0]}.{key[1]}`")

    feature = describe(value)
    if feature is not None:
        _fail(feature, f"`{type(value).__name__}`")

    raise TypeError(
        "save_format must be SaveFormat.PDF, DocFormat.PDF, or None; "
        f"saving writes PDF only. {_DOC_POINTER}"
    )

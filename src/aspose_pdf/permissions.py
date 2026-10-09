"""Access permissions: the ``/P`` word, as flags with names.

``/P`` is a *signed 32-bit integer* whose bits say what a reader may do with an
encrypted document, and whose unused bits are all **1** -- so "everything
allowed" is ``-4`` and "nothing allowed" is ``-3904``, numbers nobody reads off
a page. Worse, combining permissions by hand goes wrong quietly:
``Permissions.PRINT | Permissions.COPY`` as plain integers is ``20``, a ``/P``
with every reserved bit *cleared*, which ISO 32000-1 Table 22 does not allow and
some readers take as "nothing is allowed".

:class:`Permissions` is that integer with the names attached. It **is** an
``int`` -- ``document.permissions`` still compares, masks and passes anywhere a
number did -- and it carries the reserved bits for you::

    from aspose_pdf import Document, Permissions

    with Document("report.pdf") as document:
        document.encrypt(
            "",                                        # no password to open
            "owner-secret",
            permissions=Permissions.all().without("copy", "modify"),
        )
        document.save("report-protected.pdf")

    with Document("report-protected.pdf", password="owner-secret") as document:
        print(document.permissions)        # Permissions(print, annotate, ...)
        print(document.permissions.can_copy)       # False
        print(document.permissions.can_print)      # True

Granting nothing is not the same as leaving a document unencrypted: an
unencrypted document has no ``/P`` at all, and :attr:`Document.permissions`
reports :meth:`Permissions.all` for it, because nothing is being withheld.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from aspose_pdf.exceptions import PdfValidationException

__all__ = ["Permissions"]

#: Bit 3 of Table 22: print the document. With
#: :data:`Permissions.PRINT_HIGH_RESOLUTION` cleared, a reader may degrade the
#: print to a low-resolution rendering.
_PRINT = 1 << 2
#: Bit 4: modify the contents by operations other than those named by the
#: annotation, form-filling and assembly flags.
_MODIFY = 1 << 3
#: Bit 5: copy or otherwise extract text and graphics.
_COPY = 1 << 4
#: Bit 6: add or modify annotations and -- with :data:`_MODIFY` -- fill in form
#: fields. The PDF 1.3 word had no separate form-filling flag; bit 9 is the
#: later, narrower one.
_ANNOTATE = 1 << 5
#: Bit 9: fill in existing form fields, including signature fields, even when
#: bit 6 is clear.
_FILL_FORMS = 1 << 8
#: Bit 10: extract text and graphics for accessibility. A document that denies
#: this denies it to assistive technology, which is why PDF/UA has a view on it.
_ACCESSIBILITY = 1 << 9
#: Bit 11: assemble the document -- insert, rotate or delete pages, and add
#: bookmarks or thumbnails -- even when bit 4 is clear.
_ASSEMBLE = 1 << 10
#: Bit 12: print at full fidelity. It **qualifies** bit 3 rather than standing
#: on its own: clear, and printing is limited to a low-level representation;
#: granted while bit 3 is clear, and there is still no printing at all. qpdf
#: reads such a ``/P`` as forbidding both, which is the right reading -- so
#: ``allowing("print_high_resolution")`` without ``"print"`` is a document
#: nobody may print, and is written as asked rather than silently corrected.
_PRINT_HIGH_RESOLUTION = 1 << 11

#: Every bit Table 22 gives a meaning to.
_GRANTS = (
    _PRINT
    | _MODIFY
    | _COPY
    | _ANNOTATE
    | _FILL_FORMS
    | _ACCESSIBILITY
    | _ASSEMBLE
    | _PRINT_HIGH_RESOLUTION
)

#: Bits 1 and 2 "shall be 0" (Table 22); every other bit the table does not use
#: "shall be 1". ``-4`` is exactly that, and is therefore what "everything
#: allowed" looks like -- not ``-1``, which sets the two bits that shall not be.
_RESERVED = -4

#: Name -> bit, for every spelling this module accepts.
_BY_NAME: dict[str, int] = {
    "print": _PRINT,
    "modify": _MODIFY,
    "copy": _COPY,
    "extract": _COPY,
    "annotate": _ANNOTATE,
    "fill_forms": _FILL_FORMS,
    "fill-forms": _FILL_FORMS,
    "accessibility": _ACCESSIBILITY,
    "extract_for_accessibility": _ACCESSIBILITY,
    "assemble": _ASSEMBLE,
    "print_high_resolution": _PRINT_HIGH_RESOLUTION,
    "print-high-resolution": _PRINT_HIGH_RESOLUTION,
}

#: The canonical name of each bit, in Table 22's order -- what :attr:`allowed`
#: and :meth:`to_dict` report and what ``repr`` lists.
_NAMES: tuple[tuple[str, int], ...] = (
    ("print", _PRINT),
    ("modify", _MODIFY),
    ("copy", _COPY),
    ("annotate", _ANNOTATE),
    ("fill_forms", _FILL_FORMS),
    ("accessibility", _ACCESSIBILITY),
    ("assemble", _ASSEMBLE),
    ("print_high_resolution", _PRINT_HIGH_RESOLUTION),
)


def _bits(flags: Iterable[Any], what: str) -> int:
    """The bits *flags* names, as one mask.

    Each flag is a name (``"print"``, ``"print_high_resolution"``, with hyphens
    accepted for either), one of the class constants, or several of them already
    ORed together. A number carrying any bit **outside** Table 22's eight is
    refused rather than being ORed in: the reserved bits are this class's to
    set, and a ``/P`` with one of them cleared says something nobody meant.
    """
    mask = 0
    for flag in flags:
        if isinstance(flag, str):
            key = flag.strip().lower().replace(" ", "_")
            bit = _BY_NAME.get(key) or _BY_NAME.get(key.replace("_", "-"))
            if bit is None:
                raise PdfValidationException(
                    f"{what}: {flag!r} is not a permission; expected one of "
                    + ", ".join(name for name, _bit in _NAMES)
                )
            mask |= bit
            continue
        if isinstance(flag, bool) or not isinstance(flag, int):
            raise PdfValidationException(
                f"{what}: a permission is a name or one of the Permissions "
                f"constants, not {flag!r}"
            )
        value = int(flag)
        if value & ~_GRANTS or value == 0:
            raise PdfValidationException(
                f"{what}: {value} is not a single Table 22 permission bit; use "
                "the Permissions constants, or the names, so the reserved bits "
                "stay as the standard requires"
            )
        mask |= value
    return mask


class Permissions(int):
    """What a reader may do with an encrypted document: ``/P``, with names.

    An ``int`` subclass, so it goes anywhere the raw value went and compares
    equal to it. Build one with :meth:`all`, :meth:`none`, :meth:`allowing` or
    :meth:`denying`, adjust it with :meth:`with_` and :meth:`without`, and ask
    it what it allows with the ``can_*`` properties or :meth:`allows`.

    The eight class constants are Table 22's bit **values**, so
    ``Permissions.PRINT == 4``. They are inputs to the constructors above rather
    than values to OR together by hand -- ORing two of them loses the reserved
    bits, which is the mistake this class exists to make impossible.

    Two bits are not independent of each other. ``print_high_resolution``
    (bit 12) qualifies ``print`` (bit 3): granted alone it grants no printing,
    which is how qpdf reads it, so a document meant to be printed properly
    allows **both**. ``Permissions.denying("print")`` likewise leaves bit 12 set
    and still forbids printing, which is what the bits mean.

    Revision matters for what a reader honours: under the old ``/R 2`` only bits
    3 to 6 are defined, so denying only ``assemble`` or ``accessibility`` has no
    effect in a document encrypted that way. This library writes ``/R 4`` or
    ``/R 6`` unless RC4 is asked for.
    """

    PRINT = _PRINT
    MODIFY = _MODIFY
    COPY = _COPY
    ANNOTATE = _ANNOTATE
    FILL_FORMS = _FILL_FORMS
    ACCESSIBILITY = _ACCESSIBILITY
    ASSEMBLE = _ASSEMBLE
    PRINT_HIGH_RESOLUTION = _PRINT_HIGH_RESOLUTION

    #: The bits Table 22 defines, as one mask -- everything these flags can say.
    GRANTS = _GRANTS

    # -- building ----------------------------------------------------------

    @classmethod
    def all(cls) -> Permissions:
        """Everything allowed: ``-4``, the value an unencrypted document means."""
        return cls(_RESERVED)

    @classmethod
    def none(cls) -> Permissions:
        """Nothing allowed, with the reserved bits still as Table 22 requires."""
        return cls(_RESERVED & ~_GRANTS)

    @classmethod
    def allowing(cls, *flags: Any) -> Permissions:
        """Only *flags* allowed; everything else denied.

        ``Permissions.allowing("print", "print_high_resolution")`` is a document
        that may be printed properly and nothing else.
        """
        return cls((_RESERVED & ~_GRANTS) | _bits(flags, "allowing"))

    @classmethod
    def denying(cls, *flags: Any) -> Permissions:
        """Everything allowed except *flags*.

        ``Permissions.denying("copy")`` is the common case: a document that may
        be read and printed but not lifted from.
        """
        return cls(_RESERVED & ~_bits(flags, "denying"))

    @classmethod
    def of(cls, value: Any) -> Permissions:
        """*value* as a :class:`Permissions`, whatever kind of number it is.

        A plain ``/P`` read out of a file goes through here unchanged -- the bits
        a producer wrote are the bits this reports, reserved ones included.
        """
        if isinstance(value, Permissions):
            return value
        if isinstance(value, bool) or not isinstance(value, int):
            raise PdfValidationException(
                f"permissions must be an integer /P value, not {value!r}"
            )
        return cls(int(value))

    def with_(self, *flags: Any) -> Permissions:
        """This, plus *flags*."""
        return Permissions(int(self) | _bits(flags, "with_"))

    def without(self, *flags: Any) -> Permissions:
        """This, minus *flags*."""
        return Permissions(int(self) & ~_bits(flags, "without"))

    # -- asking ------------------------------------------------------------

    def allows(self, *flags: Any) -> bool:
        """Whether **every** one of *flags* is allowed."""
        mask = _bits(flags, "allows")
        return int(self) & mask == mask

    @property
    def can_print(self) -> bool:
        """Print the document, perhaps only at low resolution."""
        return bool(int(self) & _PRINT)

    @property
    def can_modify(self) -> bool:
        """Change the contents, beyond annotating, filling and assembling."""
        return bool(int(self) & _MODIFY)

    @property
    def can_copy(self) -> bool:
        """Copy or extract text and graphics."""
        return bool(int(self) & _COPY)

    @property
    def can_annotate(self) -> bool:
        """Add or change annotations."""
        return bool(int(self) & _ANNOTATE)

    @property
    def can_fill_forms(self) -> bool:
        """Fill in form fields that are already there."""
        return bool(int(self) & _FILL_FORMS)

    @property
    def can_extract_for_accessibility(self) -> bool:
        """Extract content for assistive technology."""
        return bool(int(self) & _ACCESSIBILITY)

    @property
    def can_assemble(self) -> bool:
        """Insert, rotate or delete pages, and add bookmarks."""
        return bool(int(self) & _ASSEMBLE)

    @property
    def can_print_high_resolution(self) -> bool:
        """Whether bit 12 is set: printing, where allowed, is at full fidelity.

        Bit 12 qualifies bit 3 and does not replace it, so this being true while
        :attr:`can_print` is false means no printing at all -- which is how qpdf
        reads it too. :attr:`can_print_faithfully` asks the question most callers
        mean.
        """
        return bool(int(self) & _PRINT_HIGH_RESOLUTION)

    @property
    def can_print_faithfully(self) -> bool:
        """Whether the document may be printed, and at full fidelity.

        Both bits, because bit 12 alone grants no printing.
        """
        return self.allows(_PRINT, _PRINT_HIGH_RESOLUTION)

    @property
    def allowed(self) -> tuple[str, ...]:
        """The names of what is allowed, in Table 22's order."""
        return tuple(name for name, bit in _NAMES if int(self) & bit)

    @property
    def denied(self) -> tuple[str, ...]:
        """The names of what is not allowed, in Table 22's order."""
        return tuple(name for name, bit in _NAMES if not int(self) & bit)

    def to_dict(self) -> dict[str, bool]:
        """Every permission and whether it is granted."""
        return {name: bool(int(self) & bit) for name, bit in _NAMES}

    @property
    def is_everything(self) -> bool:
        """Whether every permission Table 22 defines is granted."""
        return int(self) & _GRANTS == _GRANTS

    @property
    def is_nothing(self) -> bool:
        """Whether no permission Table 22 defines is granted."""
        return not int(self) & _GRANTS

    def __repr__(self) -> str:
        if self.is_everything:
            inside = "everything"
        elif self.is_nothing:
            inside = "nothing"
        else:
            inside = ", ".join(self.allowed)
        return f"Permissions({int(self)}: {inside})"

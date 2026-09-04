# PDF COS Object Model

from __future__ import annotations

import abc
import math
from typing import Any

from aspose_pdf.exceptions import PdfValidationException


class PdfObject(abc.ABC):
    """Base class for all PDF COS objects."""

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}>"


class PdfNull(PdfObject):
    """Represent PDF null object."""

    def __init__(self) -> None:
        self.value = None

    def __repr__(self) -> str:
        return "PdfNull()"


class PdfBoolean(PdfObject):
    def __init__(self, value: bool) -> None:
        self.value = bool(value)

    def __repr__(self) -> str:
        return f"PdfBoolean({self.value})"


class PdfNumber(PdfObject):
    def __init__(self, value: int | float) -> None:
        if not isinstance(value, (int, float)):
            raise TypeError("PdfNumber value must be int or float")
        self.value = value

    def __repr__(self) -> str:
        return f"PdfNumber({self.value})"


#: The comment that follows the header, four bytes above 127 behind a ``%``
#: (ISO 32000-1 7.5.2). It tells anything that inspects the first two lines --
#: a file transfer in text mode, an editor deciding whether to translate line
#: endings -- that the file is binary and must be copied as it stands. PDF/A-1
#: 6.1.2 makes it a requirement rather than a convention.
PDF_BINARY_COMMENT = b"%\xe2\xe3\xcf\xd3\n"


#: ISO 32000-1 annex C.1: the largest integer a conforming reader must accept.
_MAX_PDF_INTEGER = 2147483647


def format_pdf_number(value: Any) -> str:
    """Write *value* as the number token a file holds.

    ISO 32000-1 7.3.3: a real is decimal digits with an optional sign and a
    period, and **exponential notation is not permitted**. Python writes small
    and large floats as ``1e-05`` and ``1.5e+20``, which is not a number in a
    PDF at all -- the ``e`` starts a keyword, and the file stops parsing there.
    Infinities and NaN have no decimal form and are refused rather than written
    as the words Python names them by.

    Six decimal places, trailing zeros dropped, and a value that is a whole
    number written without a fraction: the same rule content streams have
    always used, so a coordinate is spelled the same way wherever it lands.
    """
    if isinstance(value, bool):
        raise PdfValidationException("PDF numbers must be numbers, not booleans.")
    if isinstance(value, int):
        return _format_pdf_integer(value)
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise PdfValidationException("PDF numbers must be numbers.") from None
    if not math.isfinite(number):
        raise PdfValidationException(
            "PDF has no way to write an infinity or a NaN as a number."
        )
    if abs(number) < 0.0000005:
        number = 0.0
    if number.is_integer():
        return _format_pdf_integer(int(number))
    return f"{number:.6f}".rstrip("0").rstrip(".")


def _format_pdf_integer(whole: int) -> str:
    """A whole number, kept a *real* when it is too large to be an integer.

    A token with no period is an integer, and an integer is guaranteed only to
    +/-2,147,483,647 (ISO 32000-1 annex C.1). Past that, a reader holding
    integers in a fixed-width type reads the token as nothing at all -- qpdf
    resolves such an object to null -- so the value keeps a fraction and stays
    a real, which is held as a double.
    """
    return str(whole) if abs(whole) <= _MAX_PDF_INTEGER else f"{whole}.0"


#: The characters a name may hold as themselves (ISO 32000-1 7.3.5): the
#: printable ASCII range less ``#``, which introduces an escape, and the eight
#: delimiters, which would end the name.
_NAME_REGULAR = frozenset(
    chr(code) for code in range(0x21, 0x7F) if chr(code) not in "#()<>[]{}/%"
)


def encode_pdf_name(name: str) -> str:
    """Write *name* -- without its slash -- as the token a file holds.

    ISO 32000-1 7.3.5. A name is a sequence of bytes, and any byte that is not
    a regular character is written ``#`` followed by two hex digits. Emitting
    them raw does not merely misrepresent the name: a space, a bracket or a
    parenthesis *ends* it, so the file that comes out cannot be parsed at all.
    Bytes above ASCII are UTF-8, which is what PDF 2.0 asks for and what
    readers assume of a PDF 1.x name that has them.
    """
    out = []
    for byte in name.encode("utf-8"):
        character = chr(byte)
        out.append(character if character in _NAME_REGULAR else f"#{byte:02X}")
    return "".join(out)


def decode_pdf_name(token: str) -> str:
    """Read a name token back, resolving its ``#`` escapes.

    A ``#`` that is not followed by two hex digits is malformed; it is taken
    for itself rather than refusing the file, and is written back escaped.
    """
    if "#" not in token:
        return token
    out = bytearray()
    index = 0
    while index < len(token):
        character = token[index]
        digits = token[index + 1 : index + 3] if character == "#" else ""
        if len(digits) == 2 and all(
            digit in "0123456789abcdefABCDEF" for digit in digits
        ):
            out.append(int(digits, 16))
            index += 3
            continue
        out.append(ord(character) & 0xFF)
        index += 1
    try:
        return out.decode("utf-8")
    except UnicodeDecodeError:
        return out.decode("latin-1")


def encode_pdf_text_string(text: str) -> bytes:
    """Encode *text* as the octets of a PDF ``text string``.

    ISO 32000-1 7.9.2.2 gives a text string two encodings: PDFDocEncoding, or
    UTF-16BE behind a ``FEFF`` byte order mark. Raw UTF-8 is neither -- a
    conforming reader takes those bytes for PDFDocEncoding and shows mojibake,
    which is what every non-Latin title, bookmark and annotation this library
    wrote used to look like outside it. ASCII is PDFDocEncoding's own first
    128 characters and stays as it is; anything else goes to UTF-16BE, which
    covers every string PDFDocEncoding could and every string it could not.
    """
    try:
        return text.encode("ascii")
    except UnicodeEncodeError:
        return b"\xfe\xff" + text.encode("utf-16-be")


def decode_pdf_text_string(s: PdfString) -> str:
    """Read a PDF ``text string`` back, in whichever encoding it arrived in.

    The inverse of :func:`encode_pdf_text_string`, plus the two things files in
    the wild also hold: UTF-16LE behind its own mark, and UTF-8 (which
    ISO 32000-2 admits behind ``EFBBBF``, and which producers write bare).
    PDFDocEncoding's upper half is approximated by Latin-1, which it agrees
    with except for a handful of typographic characters.
    """
    octets = s.value
    if octets[:2] == b"\xfe\xff":
        return octets[2:].decode("utf-16-be", errors="replace")
    if octets[:2] == b"\xff\xfe":
        return octets[2:].decode("utf-16-le", errors="replace")
    if octets[:3] == b"\xef\xbb\xbf":
        return octets[3:].decode("utf-8", errors="replace")
    try:
        return octets.decode("utf-8")
    except UnicodeDecodeError:
        return octets.decode("latin-1", errors="replace")


class PdfString(PdfObject):
    def __init__(self, value: bytes | str) -> None:
        if isinstance(value, str):
            self.value = encode_pdf_text_string(value)
        elif isinstance(value, (bytes, bytearray)):
            self.value = bytes(value)
        else:
            raise TypeError("PdfString value must be bytes or str")

    def __repr__(self) -> str:
        return f"PdfString({self.value!r})"


class PdfName(PdfObject):
    def __init__(self, name: str) -> None:
        if not isinstance(name, str):
            raise TypeError("PdfName must be a string")
        if not name.startswith("/"):
            name = f"/{name}"
        self.name = name

    def __repr__(self) -> str:
        return f"PdfName({self.name})"

    def __hash__(self) -> int:
        return hash(self.name)

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, PdfName) and self.name == other.name


class PdfArray(PdfObject):
    def __init__(self, items: list[PdfObject] | None = None) -> None:
        self.items: list[PdfObject] = items[:] if items else []

    def __repr__(self) -> str:
        return f"PdfArray({self.items})"

    def append(self, obj: PdfObject) -> None:
        self.items.append(obj)


class PdfDictionary(PdfObject):
    def __init__(self, mapping: dict[PdfName, PdfObject] | None = None) -> None:
        self.mapping: dict[PdfName, PdfObject] = dict(mapping) if mapping else {}

    def __repr__(self) -> str:
        return f"PdfDictionary({self.mapping})"

    def __getitem__(self, key: PdfName) -> PdfObject:
        return self.mapping[key]

    def __setitem__(self, key: PdfName, value: PdfObject) -> None:
        self.mapping[key] = value

    def __delitem__(self, key: PdfName) -> None:
        if key in self.mapping:
            del self.mapping[key]

    def __contains__(self, key: PdfName) -> bool:
        return key in self.mapping

    def get(self, key: PdfName, default: Any = None) -> Any:
        return self.mapping.get(key, default)

    def pop(self, key: PdfName, default: Any = None) -> Any:
        return self.mapping.pop(key, default)


class PdfStream(PdfDictionary):
    """A stream object: a dictionary plus a byte payload.

    ``content_decrypted`` says whether :attr:`content` has been through the
    document's security handler. A stream built in code holds plaintext by
    construction, so it starts ``True``; one parsed out of a file holds the
    bytes exactly as stored, so the parser sets it ``False`` until the handler
    runs. Without the distinction a reader cannot tell an encrypted payload
    from a plain one sitting in the same graph -- which is how a stream
    authored after load ends up "decrypted" into noise, and how an encrypted
    payload ends up written out as if it were plain.
    """

    def __init__(
        self, content: bytes = b"", mapping: dict[PdfName, PdfObject] | None = None
    ) -> None:
        super().__init__(mapping)
        self.content: bytes = content
        self.content_decrypted: bool = True

    def __repr__(self) -> str:
        return f"PdfStream(content={self.content!r}, dict={self.mapping})"


class PdfIndirectReference(PdfObject):
    def __init__(self, object_number: int, gen_number: int = 0) -> None:
        self.object_number = int(object_number)
        self.gen_number = int(gen_number)

    def __repr__(self) -> str:
        return f"PdfIndirectReference({self.object_number}, {self.gen_number})"


class PdfDocument:
    """Container for a PDF's COS object graph."""

    def __init__(self) -> None:
        self.objects: dict[int, Any] = {}
        self.trailer: PdfDictionary = PdfDictionary()
        self.xref_table: dict[int, int] = {}

    def get_object(self, ref: PdfIndirectReference) -> Any:
        """Return the object for *ref*, or ``None`` if it cannot be loaded."""
        if ref is None:
            return None
        return self.objects.get(ref.object_number)

    def register_object(self, obj: PdfObject) -> PdfIndirectReference:
        """Register an object and assign it an object number if it does not have one."""
        obj_number = getattr(obj, "_obj_number", None)
        if obj_number is None:
            obj_number = max(self.objects.keys(), default=0) + 1
            setattr(obj, "_obj_number", obj_number)
        self.objects[obj_number] = obj
        return PdfIndirectReference(obj_number)

    def __repr__(self) -> str:
        return (
            f"PdfDocument(objects={list(self.objects.keys())}, trailer={self.trailer})"
        )


class AnnotationName(str):
    """A ``str`` subclass that marks a value to be serialized as a PDF name.

    Annotation property values cross the engine/public boundary as plain Python
    objects. Numbers, booleans, strings, lists, and dicts map unambiguously onto
    COS types, but a PDF *name* (``/Foo``) is otherwise indistinguishable from a
    string. Wrap a value in :class:`AnnotationName` to force name serialization.
    Because it subclasses ``str`` the value still compares equal to the plain
    string, so a round-tripped name stays ergonomic to assert on while remaining
    distinguishable via :func:`isinstance`.
    """

    __slots__ = ()


def annotation_value_to_cos(value: Any) -> PdfObject:
    """Convert a plain Python annotation property value into a COS object.

    Supports the value shapes used by standard annotation dictionaries:
    booleans, numbers, strings/bytes, :class:`AnnotationName` (PDF names),
    nested lists/tuples (arrays), and dicts (dictionaries). ``None`` maps to the
    PDF null object.
    """
    if value is None:
        return PdfNull()
    if isinstance(value, AnnotationName):
        return PdfName(str(value))
    if isinstance(value, bool):
        return PdfBoolean(value)
    if isinstance(value, (int, float)):
        return PdfNumber(value)
    if isinstance(value, (bytes, bytearray)):
        return PdfString(bytes(value))
    if isinstance(value, str):
        return PdfString(value)
    if isinstance(value, (list, tuple)):
        return PdfArray([annotation_value_to_cos(item) for item in value])
    if isinstance(value, dict):
        return PdfDictionary(
            {PdfName(str(key)): annotation_value_to_cos(val) for key, val in value.items()}
        )
    raise TypeError(
        f"Unsupported annotation property value of type {type(value).__name__!r}"
    )


def cos_dict_to_plain(obj: Any, resolve: Any = None) -> dict | None:
    """Flatten a :class:`PdfDictionary` into plain Python keys and values.

    ``StreamDecoder`` takes ``/DecodeParms`` as an ordinary mapping with string
    keys, because it knows nothing about the COS model. Handing it a
    :class:`PdfDictionary` instead is silent rather than loud -- ``get("Predictor")``
    simply misses, and the filter runs as if no predictor were declared -- so
    every caller has to convert, and they all convert the same way.

    *resolve* dereferences an indirect value; without it, values are taken as
    they are.
    """
    if not isinstance(obj, PdfDictionary):
        return None
    deref = resolve if callable(resolve) else (lambda value: value)
    result: dict[str, Any] = {}
    for key, value in obj.mapping.items():
        resolved = deref(value)
        if isinstance(resolved, PdfNumber):
            result[key.name.lstrip("/")] = resolved.value
        elif isinstance(resolved, PdfName):
            result[key.name.lstrip("/")] = resolved.name.lstrip("/")
        elif isinstance(resolved, PdfBoolean):
            result[key.name.lstrip("/")] = resolved.value
        else:
            result[key.name.lstrip("/")] = resolved
    return result

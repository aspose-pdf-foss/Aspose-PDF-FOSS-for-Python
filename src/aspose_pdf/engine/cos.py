# PDF COS Object Model

from __future__ import annotations

import abc
from typing import Any, Dict, List, Union, Optional


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
    def __init__(self, value: Union[int, float]) -> None:
        if not isinstance(value, (int, float)):
            raise TypeError("PdfNumber value must be int or float")
        self.value = value

    def __repr__(self) -> str:
        return f"PdfNumber({self.value})"


class PdfString(PdfObject):
    def __init__(self, value: Union[bytes, str]) -> None:
        if isinstance(value, str):
            self.value = value.encode("utf-8")
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
    def __init__(self, items: Optional[List[PdfObject]] = None) -> None:
        self.items: List[PdfObject] = items[:] if items else []

    def __repr__(self) -> str:
        return f"PdfArray({self.items})"

    def append(self, obj: PdfObject) -> None:
        self.items.append(obj)


class PdfDictionary(PdfObject):
    def __init__(self, mapping: Optional[Dict[PdfName, PdfObject]] = None) -> None:
        self.mapping: Dict[PdfName, PdfObject] = dict(mapping) if mapping else {}

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
    def __init__(
        self, content: bytes = b"", mapping: Optional[Dict[PdfName, PdfObject]] = None
    ) -> None:
        super().__init__(mapping)
        self.content: bytes = content

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
        self.objects: Dict[int, Any] = {}
        self.trailer: PdfDictionary = PdfDictionary()
        self.xref_table: Dict[int, int] = {}

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

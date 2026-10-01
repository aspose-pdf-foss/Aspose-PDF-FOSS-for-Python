"""Utility functions for Aspose.PDF Python SDK."""

from __future__ import annotations

from typing import Any


def _object_to_dict(obj: Any) -> dict:
    """Convert an object to a dictionary representation suitable for JSON comparison.
    
    This function handles:
    - Basic types (str, int, float, bool, None)
    - Lists and tuples
    - Dictionaries
    - Custom objects by converting their __dict__ or using dir()
    """
    if obj is None:
        return None
    elif isinstance(obj, (str, int, float, bool)):
        return obj
    elif isinstance(obj, (list, tuple)):
        return [_object_to_dict(item) for item in obj]
    elif isinstance(obj, dict):
        return {key: _object_to_dict(value) for key, value in obj.items()}
    elif hasattr(obj, "__dict__"):
        result = {}
        for key, value in obj.__dict__.items():
            if not key.startswith("_"):
                result[key] = _object_to_dict(value)
        return result
    elif _slot_names(obj):
        # An object that declares __slots__ has no __dict__ to read, but its
        # attributes are named for us. Without this, the ones that do -- an
        # OutlineItem, say -- would come back as repr strings.
        return {
            name: _object_to_dict(getattr(obj, name))
            for name in _slot_names(obj)
            if not name.startswith("_") and hasattr(obj, name)
        }
    else:
        # Fallback for objects with neither __dict__ nor __slots__
        return str(obj)


def _slot_names(obj: Any) -> tuple[str, ...]:
    """Every ``__slots__`` name of *obj*'s class and the classes it inherits from."""
    names: list[str] = []
    for klass in type(obj).__mro__:
        slots = klass.__dict__.get("__slots__")
        if isinstance(slots, str):
            slots = (slots,)
        for name in slots or ():
            if name not in names:
                names.append(name)
    return tuple(names)


def are_objects_json_equal(obj1: Any, obj2: Any, message: str | None = None) -> None:
    """Compare two objects by their JSON representation.
    
    Args:
        obj1: First object to compare
        obj2: Second object to compare
        message: Optional custom error message if objects are not equal
    
    Raises:
        AssertionError: If the JSON representations of the objects are not equal
    """
    normalized_obj1 = _object_to_dict(obj1)
    normalized_obj2 = _object_to_dict(obj2)
    if normalized_obj1 != normalized_obj2:
        raise AssertionError(
            message or f"Objects are not JSON-equal: {obj1!r} != {obj2!r}"
        )

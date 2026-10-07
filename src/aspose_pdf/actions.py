"""What a document, a page or a field does when something happens to it.

ISO 32000-1 12.6.3: an *additional-actions* dictionary (``/AA``) maps a trigger
to an action. The triggers are different for each kind of owner, and naming one
that the owner has no trigger for is a mistake nothing would ever report -- a
viewer simply never fires it -- so each collection knows its own::

    field.actions["validate"] = JavaScriptAction("if (event.value < 0) ...")
    page.actions["open"] = JavaScriptAction("app.alert('page one')")
    document.actions["will_print"] = JavaScriptAction("...")

The actions themselves are the ones in :mod:`aspose_pdf.interactive`.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from aspose_pdf.exceptions import PdfValidationException

__all__ = [
    "DOCUMENT_TRIGGERS",
    "FIELD_TRIGGERS",
    "PAGE_TRIGGERS",
    "ActionCollection",
    "JavaScriptCollection",
]

#: A form field's triggers (Table 196), and the widget's own (Table 195).
#: ``/K``, ``/F``, ``/V`` and ``/C`` belong on the field, the rest on its widget,
#: which is where a viewer looks for each -- the collection writes whichever
#: dictionary the trigger belongs to.
FIELD_TRIGGERS: dict[str, str] = {
    "keystroke": "K",
    "format": "F",
    "validate": "V",
    "calculate": "C",
    "enter": "E",
    "exit": "X",
    "mouse_down": "D",
    "mouse_up": "U",
    "focus": "Fo",
    "blur": "Bl",
    "page_open": "PO",
    "page_close": "PC",
    "page_visible": "PV",
    "page_invisible": "PI",
}

#: The triggers that belong on the *field* rather than on its widget.
FIELD_ONLY_TRIGGERS = frozenset({"keystroke", "format", "validate", "calculate"})

#: A page's triggers (Table 194).
PAGE_TRIGGERS: dict[str, str] = {"open": "O", "close": "C"}

#: The document's triggers (Table 197), on the catalog.
DOCUMENT_TRIGGERS: dict[str, str] = {
    "will_close": "WC",
    "will_save": "WS",
    "did_save": "DS",
    "will_print": "WP",
    "did_print": "DP",
}


class ActionCollection:
    """A mapping of trigger name to :class:`~aspose_pdf.interactive.Action`.

    Reads go to the document each time, so the collection is never a stale copy.
    Assigning ``None``, or deleting a key, removes the trigger; an action this API
    has no class for is read as ``None`` and left in the file as it is, the way
    every other typed view in the package treats one.
    """

    __slots__ = ("_read", "_triggers", "_what", "_write")

    def __init__(
        self,
        triggers: dict[str, str],
        read: Any,
        write: Any,
        what: str,
    ) -> None:
        self._triggers = triggers
        self._read = read
        self._write = write
        self._what = what

    def _key(self, trigger: str) -> str:
        """The ``/AA`` entry *trigger* names, or a refusal listing the ones there are."""
        if not isinstance(trigger, str):
            raise PdfValidationException(f"A {self._what} trigger is named by a string.")
        key = trigger.strip().lower().replace("-", "_").replace(" ", "_")
        if key in self._triggers:
            return self._triggers[key]
        # The PDF key itself is accepted, so code that knows the standard can say
        # what it means in the standard's own terms.
        if trigger in self._triggers.values():
            return trigger
        raise PdfValidationException(
            f"{trigger!r} is not a {self._what} trigger; use one of: "
            + ", ".join(sorted(self._triggers))
        )

    def _name_of(self, key: str) -> str:
        """The friendly name of a ``/AA`` entry, or the entry itself."""
        for name, entry in self._triggers.items():
            if entry == key:
                return name
        return key

    def __len__(self) -> int:
        return len(self._read())

    def __iter__(self) -> Iterator[str]:
        return iter(sorted(self._name_of(key) for key in self._read()))

    def __contains__(self, trigger: object) -> bool:
        if not isinstance(trigger, str):
            return False
        try:
            return self._key(trigger) in self._read()
        except PdfValidationException:
            return False

    def __getitem__(self, trigger: str) -> Any:
        key = self._key(trigger)
        actions = self._read()
        if key not in actions:
            raise KeyError(trigger)
        return actions[key]

    def get(self, trigger: str, default: Any = None) -> Any:
        """The action *trigger* fires, or *default* when it has none.

        A trigger that *is* set but whose action this API has no class for reads
        as ``None`` rather than as *default*: the action is there, and it is only
        this typed view that cannot describe it.
        """
        try:
            return self[trigger]
        except KeyError:
            return default

    def __setitem__(self, trigger: str, action: Any) -> None:
        self._write(self._key(trigger), action)

    def __delitem__(self, trigger: str) -> None:
        key = self._key(trigger)
        if key not in self._read():
            raise KeyError(trigger)
        self._write(key, None)

    def set(self, trigger: str, action: Any) -> None:
        """Set *trigger* to fire *action*; ``None`` removes it."""
        self[trigger] = action

    def remove(self, trigger: str) -> bool:
        """Remove *trigger*; ``True`` when it had an action."""
        key = self._key(trigger)
        if key not in self._read():
            return False
        self._write(key, None)
        return True

    def clear(self) -> None:
        """Remove every trigger."""
        for key in list(self._read()):
            self._write(key, None)

    def keys(self) -> list[str]:
        return list(self)

    def values(self) -> list[Any]:
        return [self[name] for name in self]

    def items(self) -> list[tuple[str, Any]]:
        return [(name, self[name]) for name in self]

    @property
    def triggers(self) -> tuple[str, ...]:
        """Every trigger this owner has, whether it is set or not."""
        return tuple(sorted(self._triggers))

    def __repr__(self) -> str:
        return f"ActionCollection({self._what}, {self.keys()!r})"


class JavaScriptCollection:
    """The document's own scripts, as a mapping of name to source.

    ISO 32000-1 12.6.4.17: these live in the catalog's ``/Names /JavaScript`` name
    tree, and a viewer runs them when the document opens -- in **key order**, so
    the names are what decides which runs first. This is where the functions a
    field's ``/AA`` script calls are defined.

    Reads go to the document each time. A name is encoded with latin-1, the one
    codec that round-trips a name tree's byte keys unchanged.
    """

    __slots__ = ("_document",)

    def __init__(self, document: Any) -> None:
        self._document = document

    def _engine(self) -> Any:
        self._document._ensure_not_disposed()
        engine = self._document._engine_pdf
        if engine is None:
            from aspose_pdf.exceptions import AsposePdfException

            raise AsposePdfException("No document loaded")
        return engine

    def _scripts(self) -> dict[str, str]:
        return self._engine().document_javascript()

    def __len__(self) -> int:
        return len(self._scripts())

    def __iter__(self) -> Iterator[str]:
        """The names, in the order a viewer runs them: key order."""
        return iter(sorted(self._scripts()))

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._scripts()

    def __getitem__(self, name: str) -> str:
        scripts = self._scripts()
        if name not in scripts:
            raise KeyError(name)
        return scripts[name]

    def get(self, name: str, default: Any = None) -> Any:
        """The script *name* holds, or *default*."""
        return self._scripts().get(name, default)

    def __setitem__(self, name: str, script: str) -> None:
        self.add(name, script)

    def add(self, name: str, script: str) -> None:
        """Define (or replace) the script *name*."""
        if not isinstance(script, str):
            raise PdfValidationException("A document script is source text.")
        self._engine().set_document_javascript(name, script)

    def __delitem__(self, name: str) -> None:
        if not self.remove(name):
            raise KeyError(name)

    def remove(self, name: str) -> bool:
        """Remove the script *name*; ``True`` when there was one."""
        if name not in self._scripts():
            return False
        self._engine().set_document_javascript(name, None)
        return True

    def clear(self) -> None:
        """Remove every document-level script."""
        self._engine().clear_document_javascript()

    def keys(self) -> list[str]:
        return list(self)

    def values(self) -> list[str]:
        return [self[name] for name in self]

    def items(self) -> list[tuple[str, str]]:
        return [(name, self[name]) for name in self]

    def __repr__(self) -> str:
        return f"JavaScriptCollection({self.keys()!r})"

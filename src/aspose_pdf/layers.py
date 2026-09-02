"""Optional content layers of a document.

A layer is an optional content group (ISO 32000-1 8.11): content tagged with
it is shown or hidden as a unit. :attr:`aspose_pdf.Document.layers` lists the
groups the document declares, in the order of its default configuration, and
each :class:`Layer` can be switched on or off -- which the page renderer, the
graphics absorber and a later ``save()`` all honour.

A document may also ship *alternate configurations* -- named presets a viewer
offers in place of the default -- and a layer may carry a ``/Usage``
declaration saying what it should do when printed or exported. Both are
reachable here: :attr:`LayerCollection.configurations` lists the presets,
:meth:`LayerCollection.apply_configuration` switches to one, and
:meth:`Layer.set_usage` writes a usage declaration together with the
application entry that makes it take effect.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any

from aspose_pdf.engine.optional_content import content_groups, tag_content
from aspose_pdf.exceptions import AsposePdfException, PdfValidationException

__all__ = ["Layer", "LayerCollection", "LayerConfiguration"]


class Layer:
    """One optional content group, and whether it is currently shown."""

    __slots__ = ("_group", "_state")

    def __init__(self, state: Any, group: Any) -> None:
        self._state = state
        self._group = group

    @property
    def name(self) -> str:
        """The group's ``/Name``, as a viewer shows it in its layers panel."""
        return self._group.name

    @property
    def object_number(self) -> int:
        """COS object number of the group; its identity within the document."""
        return self._group.object_number

    @property
    def intent(self) -> tuple[str, ...]:
        """The group's ``/Intent`` names (``View`` unless stated otherwise)."""
        return self._group.intent

    @property
    def visible(self) -> bool:
        """Whether the default configuration shows this layer."""
        return self._group.visible

    @visible.setter
    def visible(self, value: bool) -> None:
        self._state.set_visible(self._group.object_number, bool(value))

    def _target(self, content: Any):
        """The COS dictionary *content* is tagged on, or raise.

        Two kinds of content can carry a layer after the fact, and each says
        where it lives in its own terms: an annotation by its index on a page,
        an image or form XObject by the name its page's resources give it.
        """
        from aspose_pdf.engine.optional_content import content_target

        page_index = getattr(content, "page_index", None)
        if page_index is None:
            page = getattr(content, "_page", None)
            page_index = getattr(page, "index", None)
        index = getattr(content, "_index", None)
        name = getattr(content, "name", None)

        target = None
        if isinstance(page_index, int) and isinstance(index, int):
            target = content_target(
                self._state._pdf, page_index, annotation_index=index
            )
        elif isinstance(page_index, int) and isinstance(name, str):
            target = content_target(
                self._state._pdf, page_index, resource_name=name
            )
        if target is None:
            raise PdfValidationException(
                "content must be an annotation or an image placement from a "
                "loaded page; nothing on that page matches it"
            )
        return target

    def add(self, content: Any) -> bool:
        """Tag existing *content* with this layer; ``True`` when it changed.

        *content* is an :class:`~aspose_pdf.annotations.Annotation` or an
        :class:`~aspose_pdf.images.ImagePlacement`. Content authored inside a
        ``Page.layer`` block is marked in the content stream; this is the way
        to put a layer on something that is already in the document::

            watermark = document.layers.add("Watermark")
            watermark.add(document.pages[0].annotations[0])

        Any tag already there is replaced, a membership dictionary included --
        asking for membership of one group is asking to trade that logic away.
        """
        return tag_content(
            self._state._pdf, self._target(content), self._group.object_number
        )

    def remove(self, content: Any) -> bool:
        """Untag *content*, which then shows unconditionally; ``True`` if it did.

        Only a tag naming *this* layer is removed: taking one layer's tag off
        content that carries another's would silently reveal it.
        """
        target = self._target(content)
        if self._group.object_number not in content_groups(self._state._pdf, target):
            return False
        return tag_content(self._state._pdf, target, None)

    def contains(self, content: Any) -> bool:
        """Whether *content* is tagged with this layer.

        A membership dictionary counts: content that names an ``/OCMD`` listing
        this group belongs to it, whatever else the dictionary combines it with.
        """
        return self._group.object_number in content_groups(
            self._state._pdf, self._target(content)
        )

    def set_usage(
        self,
        *,
        view: bool | None = None,
        printing: bool | None = None,
        export: bool | None = None,
        zoom: tuple[float | None, float | None] | None = None,
        language: str | None = None,
        preferred: bool = False,
    ) -> None:
        """Say what this layer should do when viewed, printed or exported.

        A usage declaration on its own is inert -- it states something *about*
        the layer, and a configuration's usage application entry is what turns
        that statement into a state for a given event. Both are written here,
        so a layer told not to print really is off when the document is
        resolved for printing.

        *zoom* is a ``(min, max)`` magnification range, either end optional;
        *language* is a BCP 47 tag, and *preferred* marks it the one to use
        when nothing matches exactly. Both apply to the on-screen event.

        Example
        -------
        ::

            watermark = document.layers.add("Draft watermark")
            watermark.set_usage(printing=False)   # on screen, never on paper
        """
        from aspose_pdf.engine.optional_content import set_usage

        set_usage(
            self._state._pdf,
            self._group.object_number,
            view=view,
            printing=printing,
            export=export,
            zoom=zoom,
            language=language,
            preferred=preferred,
        )

    def __repr__(self) -> str:
        return f"Layer({self.name!r}, visible={self.visible})"


class LayerConfiguration:
    """A named optional content configuration a viewer can switch to."""

    __slots__ = ("_config",)

    def __init__(self, config: Any) -> None:
        self._config = config

    @property
    def name(self) -> str:
        """The configuration's ``/Name``, as a viewer lists it."""
        return self._config.name

    @property
    def creator(self) -> str:
        """The application that wrote this configuration, if it said."""
        return self._config.creator

    @property
    def is_default(self) -> bool:
        """Whether this is ``/D``, the configuration the document opens in."""
        return self._config.is_default

    @property
    def index(self) -> int:
        """Position in ``/Configs``; ``-1`` for the default configuration."""
        return self._config.index

    def shows(self, layer: Layer) -> bool:
        """Whether this configuration has *layer* switched on."""
        return self._config.states.get(layer.object_number, True)

    def locks(self, layer: Layer) -> bool:
        """Whether this configuration forbids a viewer changing *layer*."""
        return layer.object_number in self._config.locked

    def __repr__(self) -> str:
        which = "default" if self.is_default else f"index {self.index}"
        return f"LayerConfiguration({self.name!r}, {which})"


class LayerCollection(Sequence[Layer]):
    """The document's layers, indexable by position or by name."""

    def __init__(self, state: Any) -> None:
        self._state = state
        self._layers = [Layer(state, group) for group in state.groups]

    def __len__(self) -> int:
        return len(self._layers)

    def __iter__(self) -> Iterator[Layer]:
        return iter(self._layers)

    def __getitem__(self, key: int | str) -> Layer:  # type: ignore[override]
        if isinstance(key, str):
            for layer in self._layers:
                if layer.name == key:
                    return layer
            raise KeyError(f"No layer named {key!r}")
        return self._layers[key]

    def __contains__(self, item: object) -> bool:
        if isinstance(item, str):
            return any(layer.name == item for layer in self._layers)
        return item in self._layers

    def add(
        self,
        name: str,
        *,
        visible: bool = True,
        intent: Sequence[str] = ("View",),
    ) -> Layer:
        """Create a layer and return it.

        The group is added to the document's optional content structure -- the
        whole structure is created if the document had none -- and appears in a
        viewer's layers panel under *name*. Nothing is on the layer yet: tag
        content with it through :meth:`aspose_pdf.pages.Page.layer`.

        Example
        -------
        ::

            draft = document.layers.add("Draft", visible=False)
            with document.pages[0].layer(draft):
                document.pages[0].add_text("DRAFT", 200, 400, font_size=64)
        """
        from aspose_pdf.engine.optional_content import create_group

        number = create_group(
            self._state._pdf, name, visible=visible, intent=intent
        )
        self._reload()
        for layer in self._layers:
            if layer.object_number == number:
                return layer
        raise AsposePdfException("The new layer could not be read back")

    def remove(self, layer: Layer | str) -> bool:
        """Remove a layer, leaving its content unconditionally visible.

        Removing the *group* is not removing the *content*: marks that named it
        stay in the page and, with nothing left to switch them off, are simply
        always shown -- which is what a viewer does with an ``/OC`` it cannot
        resolve. To delete what a hidden layer holds, use
        :meth:`aspose_pdf.Document.flatten_layers` instead.
        """
        from aspose_pdf.engine.optional_content import remove_group

        target = self[layer] if isinstance(layer, str) else layer
        removed = remove_group(self._state._pdf, target.object_number)
        self._reload()
        return removed

    @property
    def configurations(self) -> tuple[LayerConfiguration, ...]:
        """The document's optional content configurations, default first.

        A viewer offers the alternates as presets -- "Print view", "German" --
        and switches between them without changing the file. Each one reports
        which layers it shows and which it locks; hand one to
        :meth:`apply_configuration` to make it the document's state.
        """
        return tuple(
            LayerConfiguration(config) for config in self._state.configurations
        )

    def apply_configuration(self, configuration: LayerConfiguration | str | int) -> bool:
        """Adopt an alternate configuration as the document's default state.

        Everything here resolves visibility from the default configuration, so
        applying a preset copies its state into it -- base state, the per-layer
        overrides, the layer order, the locks and the usage applications. The
        preset itself stays in the document, so the choice can be made again,
        and rendering, extraction, absorption and flattening all follow the new
        state from here on.

        Accepts a :class:`LayerConfiguration`, a name, or an index into
        ``/Configs``. Returns whether anything was applied; applying the
        default configuration is a no-op that returns ``False``.
        """
        from aspose_pdf.engine.optional_content import apply_configuration

        index = self._configuration_index(configuration)
        if index < 0:
            return False
        applied = apply_configuration(self._state._pdf, index)
        if applied:
            self._reload()
        return applied

    def _configuration_index(
        self, configuration: LayerConfiguration | str | int
    ) -> int:
        if isinstance(configuration, LayerConfiguration):
            return configuration.index
        if isinstance(configuration, int):
            return configuration
        for config in self._state.configurations:
            if not config.is_default and config.name == configuration:
                return config.index
        raise KeyError(f"No layer configuration named {configuration!r}")

    def save_configuration(
        self, name: str, *, creator: str | None = None
    ) -> LayerConfiguration:
        """Snapshot the current layer states as a named alternate configuration.

        Switch the layers to the arrangement you want, then save it under a
        name: a viewer will offer it alongside the document's own default.

        Example
        -------
        ::

            document.layers["Annotations"].visible = False
            document.layers.save_configuration("Clean copy")
        """
        from aspose_pdf.engine.optional_content import save_configuration

        index = save_configuration(self._state._pdf, name, creator=creator)
        self._reload()
        for config in self.configurations:
            if config.index == index:
                return config
        raise AsposePdfException("The new configuration could not be read back")

    def resolve(
        self,
        event: str = "View",
        *,
        zoom: float | None = None,
        language: str | None = None,
    ) -> dict[str, bool]:
        """Return the layer states for an event, without changing anything.

        ``View`` is what a reader sees on screen, ``Print`` what a printer
        would get, ``Export`` what a conversion would keep. The difference
        between them comes from the layers' usage declarations: a watermark
        marked "do not print" is on under ``View`` and off under ``Print``.

        *zoom* is the magnification a zoom-dependent layer is judged at and
        *language* the BCP 47 tag a language layer is matched against; leaving
        either out leaves that kind of layer at its configured state rather
        than deciding it on an invented viewer.
        """
        state = self._state_for(event, zoom=zoom, language=language)
        return {
            group.name: group.visible for group in state.groups
        }

    def apply_usage(
        self,
        event: str = "Print",
        *,
        zoom: float | None = None,
        language: str | None = None,
    ) -> int:
        """Switch the layers to what *event* calls for; return how many moved.

        This makes the usage-derived states the document's own, which is what
        you want before flattening or exporting for that event -- a
        "do not print" watermark is then genuinely gone from a flattened print
        copy rather than merely marked. The default event is ``Print`` because
        ``View`` is already what the layers resolve to, so applying it moves
        nothing.
        """
        state = self._state_for(event, zoom=zoom, language=language)
        changed = 0
        for number, visible in state.visible_by_number.items():
            if self._state.visible_by_number.get(number) != visible:
                self._state.set_visible(number, visible)
                changed += 1
        self._reload()
        return changed

    def _state_for(
        self, event: str, *, zoom: float | None, language: str | None
    ) -> Any:
        from aspose_pdf.engine.optional_content import USAGE_EVENTS, OptionalContent

        if event not in USAGE_EVENTS:
            raise ValueError(
                f"Unknown optional content event {event!r}; "
                f"expected one of {', '.join(USAGE_EVENTS)}"
            )
        return OptionalContent(
            self._state._pdf, event=event, zoom=zoom, language=language
        )

    def _reload(self) -> None:
        from aspose_pdf.engine.optional_content import OptionalContent

        self._state = OptionalContent(self._state._pdf)
        self._layers = [Layer(self._state, group) for group in self._state.groups]

    def names(self) -> list[str]:
        """Layer names in document order."""
        return [layer.name for layer in self._layers]

    def __repr__(self) -> str:
        return f"LayerCollection({self.names()!r})"

"""Graphics element absorption and manipulation functionality."""

from __future__ import annotations
from typing import List, Optional, Iterator, Any


class InvalidOperationException(RuntimeError):
    """Raised when a graphics element is attached to the wrong parent."""


class GraphicElementCollection:
    """Collection of graphic elements that can be added to or removed from a page."""
    
    def __init__(self):
        """Initialize a new instance of GraphicElementCollection."""
        self._elements: List[Any] = []
        self._parent: Any = None
    
    def add(self, element: Any) -> None:
        """Add a graphic element to the collection.
        
        Args:
            element: The graphic element to add.
            
        Raises:
            InvalidOperationException: If the element has a different parent.
        """
        # Check if element has a parent and if it's consistent with this collection's parent
        if self._parent is not None:
            # Check if element has a different parent
            if hasattr(element, 'parent') and element.parent is not None:
                if element.parent is not self._parent:
                    raise InvalidOperationException(
                        "Cannot add element with different parent to this collection"
                    )
            # Check if element already belongs to another collection
            if hasattr(element, '_collection') and element._collection is not None:
                if element._collection is not self:
                    raise InvalidOperationException(
                        "Cannot add element with different parent to this collection"
                    )
        
        self._elements.append(element)
        # Mark element as belonging to this collection
        if hasattr(element, '_collection'):
            element._collection = self
    
    def remove(self, element: Any) -> None:
        """Remove a graphic element from the collection.
        
        Args:
            element: The graphic element to remove.
        """
        if element in self._elements:
            self._elements.remove(element)
            # Clear the collection reference
            if hasattr(element, '_collection'):
                element._collection = None
    
    @property
    def elements(self) -> List[Any]:
        """Get the collection of graphic elements."""
        return self._elements
    
    def __len__(self) -> int:
        return len(self._elements)
    
    def __iter__(self) -> Iterator[Any]:
        return iter(self._elements)
    
    def __getitem__(self, index: int) -> Any:
        return self._elements[index]


class GraphicsAbsorber:
    """Absorbs graphic elements from PDF pages."""
    
    def __init__(self):
        """Initialize a new instance of GraphicsAbsorber."""
        self._elements: Optional[GraphicElementCollection] = None
        self._suppressed = False
    
    @property
    def elements(self) -> GraphicElementCollection:
        """Get the collection of absorbed graphic elements."""
        if self._elements is None:
            self._elements = GraphicElementCollection()
        return self._elements
    
    def visit(self, page: Any) -> None:
        """Visit a page and absorb its graphic elements.
        
        Args:
            page: The page to visit.
        """
        # Clear previous elements
        self._elements = GraphicElementCollection()
        
        # Graphic-element extraction from the page content stream (paths,
        # images, operators) is not performed in the FOSS build; callers
        # receive an empty collection.
    
    def suppress_update(self) -> None:
        """Suppress updates to the graphic elements."""
        self._suppressed = True
    
    def resume_update(self) -> None:
        """Resume updates to the graphic elements."""
        self._suppressed = False

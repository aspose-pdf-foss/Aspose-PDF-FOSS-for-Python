"""Save format enumeration for document export."""

from __future__ import annotations
from enum import Enum


class SaveFormat(Enum):
    """Format for saving PDF documents."""
    
    PDF = "PDF"
    """Portable Document Format."""
    
    PPTX = "PPTX"
    """PowerPoint Open XML Presentation."""
    

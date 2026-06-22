"""Printing compatibility helpers for the prerelease package."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

__all__ = ["Duplex", "PrintRange", "PrinterSettings"]


class Duplex(str, Enum):
    NONE = "None"
    SIMPLEX = "Simplex"
    DUPLEX = "Duplex"
    TUMBLE = "Tumble"


class PrintRange(str, Enum):
    ALL_PAGES = "AllPages"
    SOME_PAGES = "SomePages"
    SELECTION = "Selection"
    PAGE_RANGE = "PageRange"


@dataclass(slots=True)
class PrinterSettings:
    collate: bool = False
    copies: int = 1
    duplex: Duplex | None = None
    from_page: int = 1
    maximum_page: int = 100
    minimum_page: int = 0
    printer_name: str = ""
    print_file_name: str = ""
    print_range: PrintRange | None = None
    print_to_file: bool = False
    to_page: int = 1

    def __post_init__(self) -> None:
        if self.copies < 1:
            raise ValueError("copies must be at least 1")

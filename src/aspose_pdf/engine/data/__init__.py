"""Minimal data-layer helpers kept for prerelease compatibility."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from .types import Color

__all__ = [
    "Color",
    "Encoding",
    "EncodingType",
    "FilterType",
    "PdfNull",
    "PdfObjectID",
    "PdfObjectRegistry",
    "PdfTrailerable",
]


class FilterType(str, Enum):
    NONE = "None"
    FLATE_DECODE = "FlateDecode"
    LZW_DECODE = "LZWDecode"
    DCT_DECODE = "DCTDecode"
    JPX_DECODE = "JPXDecode"
    CCITT_FAX_DECODE = "CCITTFaxDecode"
    JBIG2_DECODE = "JBIG2Decode"


class EncodingType(str, Enum):
    WIN_ANSI = "WinAnsiEncoding"
    MAC_ROMAN = "MacRomanEncoding"
    MAC_EXPERT = "MacExpertEncoding"
    PDF_DOC = "PDFDocEncoding"
    UNICODE = "UnicodeEncoding"


class Encoding:
    UTF8 = "UTF8"
    UTF16 = "UTF16"
    LATIN1 = "Latin1"
    WIN_ANSI = "WinAnsi"

    @staticmethod
    def encode(text: str, encoding_type: EncodingType = EncodingType.WIN_ANSI) -> bytes:
        del encoding_type
        return text.encode("utf-8", errors="replace")


@dataclass(frozen=True, slots=True)
class PdfObjectID:
    object_number: int
    generation_number: int = 0


class PdfObjectRegistry:
    def __init__(self) -> None:
        self._objects: dict[PdfObjectID, Any] = {}
        self._next_id = 1

    def register(self, obj: Any) -> PdfObjectID:
        object_id = PdfObjectID(self._next_id, 0)
        self._objects[object_id] = obj
        self._next_id += 1
        return object_id

    def get(self, obj_id: PdfObjectID) -> Any:
        return self._objects.get(obj_id)


class PdfTrailerable:
    def get_dictionary(self) -> dict[str, Any]:
        return {}


class PdfNull:
    def __repr__(self) -> str:
        return "PdfNull()"

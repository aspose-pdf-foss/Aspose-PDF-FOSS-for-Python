"""Write complete byte sequences to binary streams."""

from __future__ import annotations

from typing import BinaryIO

from aspose_pdf.exceptions import AsposePdfException


def write_all(stream: BinaryIO, data: bytes) -> None:
    """Write all of *data*, rejecting streams that stop making progress."""
    remaining = memoryview(data)
    while remaining:
        written = stream.write(remaining)
        if (
            isinstance(written, bool)
            or not isinstance(written, int)
            or written <= 0
            or written > len(remaining)
        ):
            raise AsposePdfException("Stream write did not consume the supplied bytes")
        remaining = remaining[written:]

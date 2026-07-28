"""Resource limits for untrusted PDF data and externally authored assets."""

from __future__ import annotations

from dataclasses import dataclass, fields
from threading import Lock

from aspose_pdf.exceptions import PdfResourceLimitException

__all__ = ["PdfLoadLimits"]


@dataclass(frozen=True, slots=True)
class PdfLoadLimits:
    """Immutable safety limits for untrusted PDF input and authored assets.

    Every field accepts a positive integer or ``None``. ``None`` disables that
    individual limit. The defaults are intentionally generous enough for normal
    documents while bounding allocations and parser work triggered by malformed
    or hostile files.
    """

    max_input_bytes: int | None = 512 * 1024 * 1024
    max_objects: int | None = 250_000
    max_xref_sections: int | None = 256
    max_nesting_depth: int | None = 100
    max_container_items: int | None = 1_000_000
    max_object_bytes: int | None = 128 * 1024 * 1024
    max_decoded_stream_bytes: int | None = 128 * 1024 * 1024
    max_codec_work_bytes: int | None = 512 * 1024 * 1024
    max_compression_ratio: int | None = 2_000
    max_content_stream_bytes: int | None = 64 * 1024 * 1024
    max_total_decoded_bytes: int | None = 512 * 1024 * 1024
    max_stream_filters: int | None = 16
    max_pages: int | None = 100_000
    max_image_pixels: int | None = 100_000_000
    max_raster_pixels: int | None = 100_000_000
    max_content_tokens: int | None = 5_000_000

    def __post_init__(self) -> None:
        for item in fields(self):
            value = getattr(self, item.name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value <= 0
            ):
                raise ValueError(f"{item.name} must be a positive integer or None")

    @classmethod
    def unlimited(cls) -> PdfLoadLimits:
        """Return a configuration with every limit disabled."""
        return cls(**{item.name: None for item in fields(cls)})


def _coerce_limits(limits: PdfLoadLimits | None) -> PdfLoadLimits:
    if limits is None:
        return PdfLoadLimits()
    if not isinstance(limits, PdfLoadLimits):
        raise TypeError("limits must be a PdfLoadLimits instance or None")
    return limits


def _read_limited(stream, budget: _LoadBudget) -> bytes:
    """Read a binary stream without crossing the configured input limit."""
    limit = budget.limits.max_input_bytes
    if limit is None:
        data = stream.read()
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("stream read() must return bytes")
        return bytes(data)

    out = bytearray()
    while True:
        remaining = limit + 1 - len(out)
        if remaining <= 0:
            budget.check_input(len(out))
        chunk = stream.read(min(64 * 1024, remaining))
        if not isinstance(chunk, (bytes, bytearray)):
            raise TypeError("stream read() must return bytes")
        if not chunk:
            break
        out.extend(chunk)
        budget.check_input(len(out))
    return bytes(out)


class _LoadBudget:
    """Mutable per-document accounting shared by eager and lazy code paths."""

    def __init__(self, limits: PdfLoadLimits | None = None) -> None:
        self.limits = _coerce_limits(limits)
        self._decoded_total = 0
        self._lock = Lock()

    @staticmethod
    def _raise(context: str, value: int, name: str, limit: int) -> None:
        raise PdfResourceLimitException(
            f"Resource limit exceeded for {context}: {value} exceeds "
            f"{name}={limit}"
        )

    def check(self, value: int, name: str, context: str) -> None:
        limit = getattr(self.limits, name)
        if limit is not None and value > limit:
            self._raise(context, value, name, limit)

    def check_input(self, size: int) -> None:
        self.check(size, "max_input_bytes", "PDF input bytes")

    def check_objects(self, count: int) -> None:
        self.check(count, "max_objects", "PDF objects")

    def check_object_id(self, object_number: int) -> None:
        if object_number < 0:
            raise PdfResourceLimitException(
                f"Invalid negative PDF object number: {object_number}"
            )
        limit = self.limits.max_objects
        if limit is not None and object_number >= limit:
            self._raise(
                "PDF object slots",
                object_number + 1,
                "max_objects",
                limit,
            )

    def check_pages(self, count: int) -> None:
        self.check(count, "max_pages", "PDF pages")

    def check_image_pixels(self, width: int, height: int, context: str) -> None:
        if width < 0 or height < 0:
            raise PdfResourceLimitException(
                f"Invalid negative image dimensions for {context}: {width}x{height}"
            )
        self.check(width * height, "max_image_pixels", f"{context} pixels")

    def check_raster_pixels(self, width: int, height: int, context: str) -> None:
        if width < 0 or height < 0:
            raise PdfResourceLimitException(
                f"Invalid negative raster dimensions for {context}: {width}x{height}"
            )
        self.check(width * height, "max_raster_pixels", f"{context} pixels")

    def reserve_decoded(
        self,
        size: int,
        context: str,
    ) -> None:
        """Charge decoded output and enforce the cumulative document total."""
        self.check(size, "max_decoded_stream_bytes", context)
        with self._lock:
            total = self._decoded_total + size
            limit = self.limits.max_total_decoded_bytes
            if limit is not None and total > limit:
                self._raise(
                    "total decoded stream bytes",
                    total,
                    "max_total_decoded_bytes",
                    limit,
                )
            self._decoded_total = total

"""Optimization option container for the prerelease package."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["OptimizationOptions"]


@dataclass(slots=True)
class OptimizationOptions:
    remove_unused_objects: bool = True
    remove_unused_streams: bool = True
    allow_reuse_page_content: bool = True
    link_duplicate_streams: bool = True
    unembed_fonts: bool = False
    image_compression_quality: int | None = None
    image_max_dimension: int | None = None
    remove_duplicate_images: bool = True
    compress_fonts: bool = True
    use_object_streams: bool = True
    subset_fonts: bool = False

    def __post_init__(self) -> None:
        if self.image_compression_quality is not None and not (
            0 <= self.image_compression_quality <= 100
        ):
            raise ValueError("image_compression_quality must be between 0 and 100")
        if self.image_max_dimension is not None and self.image_max_dimension <= 0:
            raise ValueError("image_max_dimension must be a positive pixel count")

    def to_dict(self) -> dict[str, object]:
        return {
            "remove_unused_objects": self.remove_unused_objects,
            "remove_unused_streams": self.remove_unused_streams,
            "allow_reuse_page_content": self.allow_reuse_page_content,
            "link_duplicate_streams": self.link_duplicate_streams,
            "unembed_fonts": self.unembed_fonts,
            "image_compression_quality": self.image_compression_quality,
            "image_max_dimension": self.image_max_dimension,
            "remove_duplicate_images": self.remove_duplicate_images,
            "compress_fonts": self.compress_fonts,
            "use_object_streams": self.use_object_streams,
            "subset_fonts": self.subset_fonts,
        }

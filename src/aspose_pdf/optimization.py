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
    image_target_dpi: int | None = None
    image_progressive: bool = False
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
        if self.image_target_dpi is not None and self.image_target_dpi <= 0:
            raise ValueError("image_target_dpi must be a positive DPI value")

    def to_dict(self) -> dict[str, object]:
        return {
            "remove_unused_objects": self.remove_unused_objects,
            "remove_unused_streams": self.remove_unused_streams,
            "allow_reuse_page_content": self.allow_reuse_page_content,
            "link_duplicate_streams": self.link_duplicate_streams,
            "unembed_fonts": self.unembed_fonts,
            "image_compression_quality": self.image_compression_quality,
            "image_max_dimension": self.image_max_dimension,
            "image_target_dpi": self.image_target_dpi,
            "image_progressive": self.image_progressive,
            "remove_duplicate_images": self.remove_duplicate_images,
            "compress_fonts": self.compress_fonts,
            "use_object_streams": self.use_object_streams,
            "subset_fonts": self.subset_fonts,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> OptimizationOptions:
        """Construct an OptimizationOptions instance from a dictionary.

        Unrecognized keys are ignored. Missing keys use constructor defaults.
        Raises ValueError if a value has the wrong type or if numeric values
        are out of allowed ranges.

        The valid keys are derived from :meth:`to_dict`, ensuring automatic
        alignment when new fields are added to the class.
        """
        # Derive valid keys from to_dict() to stay in sync with class fields
        valid_keys = set(cls().to_dict().keys())

        # Filter to only valid keys, ignoring unrecognized ones
        filtered_data = {k: v for k, v in data.items() if k in valid_keys}

        # Validate types for boolean fields
        bool_fields = {
            "remove_unused_objects",
            "remove_unused_streams",
            "allow_reuse_page_content",
            "link_duplicate_streams",
            "unembed_fonts",
            "image_progressive",
            "remove_duplicate_images",
            "compress_fonts",
            "use_object_streams",
            "subset_fonts",
        }
        for field in bool_fields:
            if field in filtered_data:
                value = filtered_data[field]
                if not isinstance(value, bool):
                    raise ValueError(f"{field} must be a boolean value")

        # Validate types and ranges for integer fields
        if "image_compression_quality" in filtered_data:
            value = filtered_data["image_compression_quality"]
            if value is not None:
                if not isinstance(value, int) or isinstance(value, bool):
                    raise ValueError("image_compression_quality must be an integer or None")
                if not (0 <= value <= 100):
                    raise ValueError("image_compression_quality must be between 0 and 100")

        if "image_max_dimension" in filtered_data:
            value = filtered_data["image_max_dimension"]
            if value is not None:
                if not isinstance(value, int) or isinstance(value, bool):
                    raise ValueError("image_max_dimension must be an integer or None")
                if value <= 0:
                    raise ValueError("image_max_dimension must be a positive pixel count")

        if "image_target_dpi" in filtered_data:
            value = filtered_data["image_target_dpi"]
            if value is not None:
                if not isinstance(value, int) or isinstance(value, bool):
                    raise ValueError("image_target_dpi must be an integer or None")
                if value <= 0:
                    raise ValueError("image_target_dpi must be a positive DPI value")

        return cls(**filtered_data)

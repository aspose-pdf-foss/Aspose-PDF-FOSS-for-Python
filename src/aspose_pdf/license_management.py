"""License management functionality for Aspose.PDF Python SDK."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class StatisticsEntry:
    """Entry for tracking statistics and timing information.

    This class provides functionality to track elapsed time for operations
    and store associated key information.
    """

    key: str = ""
    """The key associated with this statistics entry."""

    _start_time: float = field(default_factory=time.time, init=False)
    """Timestamp when the entry was created."""

    _end_time: Optional[float] = field(default=None, init=False)
    """Timestamp when the entry was stopped."""

    def __post_init__(self) -> None:
        """Initialize the start time after dataclass initialization."""
        self._start_time = time.time()
        self._end_time = None

    def stop(self) -> None:
        """Stop the timer and record the end time."""
        self._end_time = time.time()

    def get_elapsed_time(self) -> float:
        """Get the elapsed time in milliseconds.

        Returns:
            float: Elapsed time in milliseconds since the entry was created
                   (or since stop() was called if applicable).
        """
        if self._end_time is not None:
            end_time = self._end_time
        else:
            end_time = time.time()

        elapsed_seconds = end_time - self._start_time
        return elapsed_seconds * 1000  # Convert to milliseconds

    def reset(self) -> None:
        """Reset the entry and restart the timer."""
        self._start_time = time.time()
        self._end_time = None

    def __repr__(self) -> str:
        """Return a string representation of the StatisticsEntry."""
        elapsed = self.get_elapsed_time()
        return f"StatisticsEntry(key='{self.key}', elapsed={elapsed:.2f}ms)"


class License:
    """License management class for Aspose.PDF."""

    def __init__(self) -> None:
        """Initialize a new License instance."""

"""Where a render spent its time.

:class:`PerformanceLogger` is caller-owned: hand one to
:meth:`aspose_pdf.pages.Page.render` and the rasterizer records how long each
phase took into it. Nothing is measured unless a logger is passed, so a render
that does not ask for timings pays nothing for them, and two renders in
different threads keep their own numbers.

:class:`VirtualizationPerformance` is the older, process-global stopwatch of
the same API. It works, and callers may use it for their own timings, but the
package never writes to it: a library that timed itself into module-level
mutable state would interleave two documents rendered at once. It does not
virtualise or accelerate anything -- the name is inherited, not a description.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import ClassVar

from aspose_pdf.engine.rasterizer import RasterizedPage

__all__ = ["PerformanceLogger", "RasterizedPage", "VirtualizationPerformance"]


class PerformanceLogger:
    """Collects named phase timings, and free-form lines.

    Example
    -------
    ::

        from aspose_pdf.visualization import PerformanceLogger

        timings = PerformanceLogger()
        document.pages[0].render(dpi=150, performance=timings)
        print(timings.timings)  # {'content': 0.001, 'interpret': 0.42, ...}
    """

    def __init__(self) -> None:
        self.log: list[str] = []
        #: Seconds spent per phase name, accumulated across measurements.
        self.timings: dict[str, float] = {}

    def log_line(self, line: str) -> None:
        """Append a free-form line to :attr:`log`."""
        self.log.append(line)

    def record(self, name: str, seconds: float) -> None:
        """Add *seconds* to the phase called *name*."""
        self.timings[name] = self.timings.get(name, 0.0) + seconds

    @contextmanager
    def measure(self, name: str) -> Iterator[None]:
        """Time the block and :meth:`record` it, even if the block raises."""
        started = time.perf_counter()
        try:
            yield
        finally:
            self.record(name, time.perf_counter() - started)

    @property
    def total(self) -> float:
        """Seconds across every recorded phase."""
        return sum(self.timings.values())

    def summarise(self) -> list[str]:
        """One ``name: Nms`` line per phase, slowest first, also into :attr:`log`."""
        lines = [
            f"{name}: {round(seconds * 1000)}ms"
            for name, seconds in sorted(
                self.timings.items(), key=lambda item: -item[1]
            )
        ]
        self.log.extend(lines)
        return lines

    def __repr__(self) -> str:
        return f"PerformanceLogger({self.timings!r})"


class VirtualizationPerformance:
    """A process-global stopwatch. Nothing in this package writes to it."""

    _start_times: ClassVar[dict[str, float]] = {}
    _elapsed_times: ClassVar[dict[str, float]] = {}
    _current_key: ClassVar[str | None] = None

    @classmethod
    def start(cls, key: str) -> None:
        cls._start_times[key] = time.time()
        cls._current_key = key

    @classmethod
    def stop(cls) -> None:
        key = cls._current_key
        if key is None:
            return
        elapsed = time.time() - cls._start_times.get(key, time.time())
        cls._elapsed_times[key] = cls._elapsed_times.get(key, 0.0) + elapsed
        cls._current_key = None

    @classmethod
    def print_statistics(cls, logger: PerformanceLogger) -> None:
        for key, elapsed in sorted(cls._elapsed_times.items()):
            logger.log_line(f"{key}: {round(elapsed * 1000)}ms")

    @classmethod
    def reset(cls) -> None:
        cls._start_times.clear()
        cls._elapsed_times.clear()
        cls._current_key = None

    @classmethod
    def get_elapsed_time(cls, key: str) -> float:
        return cls._elapsed_times.get(key, 0.0)

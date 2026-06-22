"""Visualization performance helpers."""

from __future__ import annotations

import time

from aspose_pdf.engine.rasterizer import RasterizedPage

__all__ = ["PerformanceLogger", "RasterizedPage", "VirtualizationPerformance"]


class PerformanceLogger:
    def __init__(self) -> None:
        self.log: list[str] = []

    def log_line(self, line: str) -> None:
        self.log.append(line)


class VirtualizationPerformance:
    _start_times: dict[str, float] = {}
    _elapsed_times: dict[str, float] = {}
    _current_key: str | None = None

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

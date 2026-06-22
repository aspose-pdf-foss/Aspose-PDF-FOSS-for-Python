"""Bit-level I/O helpers."""

from __future__ import annotations

__all__ = ["BitStream"]


class BitStream:
    """Minimal bit-oriented buffer used by compatibility code."""

    def __init__(self, capacity: int = 0) -> None:
        self._buffer = bytearray(max(capacity, 0))
        self._bit_length = 0
        self._read_position = 0

    @property
    def length(self) -> int:
        return self._bit_length

    @property
    def position(self) -> int:
        return self._read_position

    def write(self, value: int, offset: int, bit_count: int) -> None:
        del offset
        for index in range(bit_count):
            bit = (value >> index) & 1
            byte_index = self._bit_length // 8
            bit_index = self._bit_length % 8
            if byte_index == len(self._buffer):
                self._buffer.append(0)
            if bit:
                self._buffer[byte_index] |= 1 << bit_index
            self._bit_length += 1

    def read_array(self, bit_count: int) -> list[int]:
        result: list[int] = []
        for _ in range(bit_count):
            byte_index = self._read_position // 8
            bit_index = self._read_position % 8
            if byte_index >= len(self._buffer):
                result.append(0)
            else:
                result.append((self._buffer[byte_index] >> bit_index) & 1)
            self._read_position += 1
        return result

    def read_byte(self) -> int:
        bits = self.read_array(8)
        value = 0
        for index, bit in enumerate(bits):
            value |= bit << index
        return value

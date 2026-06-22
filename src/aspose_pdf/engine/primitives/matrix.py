"""Simple 2D matrix type used by prerelease compatibility code."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Matrix"]


@dataclass(slots=True)
class Matrix:
    a: float = 1.0
    b: float = 0.0
    c: float = 0.0
    d: float = 1.0
    e: float = 0.0
    f: float = 0.0

    def translate(self, x: float, y: float) -> None:
        self.e += float(x)
        self.f += float(y)

    def multiply(self, other: "Matrix") -> "Matrix":
        return Matrix(
            a=self.a * other.a + self.b * other.c,
            b=self.a * other.b + self.b * other.d,
            c=self.c * other.a + self.d * other.c,
            d=self.c * other.b + self.d * other.d,
            e=self.e + other.e,
            f=self.f + other.f,
        )

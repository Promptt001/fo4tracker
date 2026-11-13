"""Small helpers for reading little-endian Bethesda binary formats."""
from __future__ import annotations

import struct


class ParseError(Exception):
    pass


class Reader:
    """Cursor over a bytes-like object."""

    __slots__ = ("data", "pos", "end")

    def __init__(self, data, pos: int = 0, end: int | None = None):
        self.data = data
        self.pos = pos
        # Never trust a length field to stay inside the buffer.
        self.end = len(data) if end is None else min(end, len(data))

    def remaining(self) -> int:
        return self.end - self.pos

    def _take(self, n: int) -> int:
        p = self.pos
        if p + n > self.end:
            raise ParseError(f"read past end ({p}+{n} > {self.end})")
        self.pos = p + n
        return p

    def bytes(self, n: int) -> bytes:
        p = self._take(n)
        return bytes(self.data[p:p + n])

    def skip(self, n: int) -> None:
        self._take(n)

    def u8(self) -> int:
        return self.data[self._take(1)]

    def u16(self) -> int:
        return struct.unpack_from("<H", self.data, self._take(2))[0]

    def u32(self) -> int:
        return struct.unpack_from("<I", self.data, self._take(4))[0]

    def i32(self) -> int:
        return struct.unpack_from("<i", self.data, self._take(4))[0]

    def u64(self) -> int:
        return struct.unpack_from("<Q", self.data, self._take(8))[0]

    def f32(self) -> float:
        return struct.unpack_from("<f", self.data, self._take(4))[0]

    def wstring(self) -> str:
        """uint16 length-prefixed string (no terminator)."""
        n = self.u16()
        return self.bytes(n).decode("utf-8", "replace")

    def vsval(self) -> int:
        """Bethesda variable-size value: low 2 bits give the byte width."""
        if self.pos >= self.end:
            raise ParseError(f"read past end ({self.pos} >= {self.end})")
        b = self.data[self.pos]
        size = b & 3
        if size == 0:
            self._take(1)
            return b >> 2
        if size == 1:
            return self.u16() >> 2
        if size == 2:
            p = self._take(3)
            v = self.data[p] | (self.data[p + 1] << 8) | (self.data[p + 2] << 16)
            return v >> 2
        raise ParseError("invalid vsval")

    def refid(self) -> int:
        """Raw 3-byte big-endian save RefID."""
        p = self._take(3)
        d = self.data
        return (d[p] << 16) | (d[p + 1] << 8) | d[p + 2]

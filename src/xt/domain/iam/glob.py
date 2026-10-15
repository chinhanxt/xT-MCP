# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Khớp wildcard an toàn: chỉ `*` là ký tự đặc biệt, khớp toàn chuỗi, phân biệt hoa thường.

Không dùng regex nên không có injection hay backtracking bùng nổ: chi phí O(độ dài chuỗi * số đoạn),
mà số đoạn bị chặn bởi `MAX_STARS` lúc validate policy.
"""

from __future__ import annotations

MAX_PATTERN_LEN = 256
MAX_STARS = 8


class Glob:
    """Pattern đã biên dịch. `*` khớp mọi chuỗi con (kể cả rỗng, kể cả `:` và `.`)."""

    __slots__ = ("_first", "_last", "_literal", "_middle", "pattern")

    def __init__(self, pattern: str) -> None:
        self.pattern = pattern
        parts = pattern.split("*")
        self._literal: str | None = pattern if len(parts) == 1 else None
        self._first = parts[0]
        self._last = parts[-1]
        self._middle = tuple(p for p in parts[1:-1] if p)

    def matches(self, value: str) -> bool:
        if self._literal is not None:
            return value == self._literal
        first, last = self._first, self._last
        if len(value) < len(first) + len(last):
            return False
        if not (value.startswith(first) and value.endswith(last)):
            return False
        pos = len(first)
        end = len(value) - len(last)
        for mid in self._middle:
            i = value.find(mid, pos, end)
            if i < 0:
                return False
            pos = i + len(mid)
        return True

    def __repr__(self) -> str:
        return f"Glob({self.pattern!r})"


def glob_match(pattern: str, value: str) -> bool:
    """Tiện ích một lần; đường nóng dùng `Glob` đã biên dịch."""
    return Glob(pattern).matches(value)

# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Parse JSON từ chối khóa trùng (tool-contracts-v1 C2, §3.2).

JSON cho phép khóa trùng và parser thường giữ giá trị cuối, nên `{"amount":"1","amount":"9000000"}`
vẫn qua schema. Mọi biên nhận JSON từ agent (MCP HTTP, stdio, REST) phải parse bằng hàm này.
"""

from __future__ import annotations

import json
import re
from typing import Any


class DuplicateKeyError(ValueError):
    def __init__(self, keys: list[str]) -> None:
        self.keys = keys
        super().__init__(f"duplicate JSON object key(s): {', '.join(keys)}")


MAX_REPORTED_KEYS = 20


def _pairs_collector(dups: dict[str, None]) -> Any:
    """O(n): `dups` là dict (tập có thứ tự); kiểm thành viên O(1) (audit R2-H1)."""

    def hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in pairs:
            if k in out:
                dups[k] = None
            out[k] = v
        return out

    return hook


class JsonTooDeepError(ValueError):
    """JSON lồng quá sâu: từ chối trước khi parser đệ quy (RecursionError)."""


MAX_DEPTH = 64
# Trần số phần tử (dấu `,` ngoài chuỗi ~ số cặp khóa/phần tử mảng) của một thông điệp tool. Schema
# rộng nhất: values <= 200 khóa, mảng <= 100 phần tử, propose_model_change <= 50 op (audit R3-M1).
MAX_ITEMS = 20_000
_STRING = re.compile(rb'"(?:[^"\\]|\\.)*"', re.S)
_BRACKET = re.compile(rb"[\[\]{}]")


class JsonTooLargeError(ValueError):
    """Quá nhiều phần tử: từ chối trước khi parse (SDK parse lại trên event loop)."""


def check_shape(raw: bytes, max_depth: int = MAX_DEPTH, max_items: int = MAX_ITEMS) -> None:
    """Kiểm độ sâu và số phần tử ở tốc độ C: bỏ nội dung chuỗi bằng regex rồi đếm."""
    if raw.count(b",") <= max_items and raw.count(b"[") + raw.count(b"{") <= max_depth:
        return  # đường nhanh: không thể vượt trần
    bare = _STRING.sub(b'""', raw)
    if bare.count(b",") > max_items:
        raise JsonTooLargeError(f"JSON has more than {max_items} items")
    depth = 0
    for m in _BRACKET.finditer(bare):
        if m.group(0) in (b"[", b"{"):
            depth += 1
            if depth > max_depth:
                raise JsonTooDeepError(f"JSON nesting deeper than {max_depth}")
        else:
            depth -= 1


def check_depth(raw: bytes, max_depth: int = MAX_DEPTH) -> None:
    check_shape(raw, max_depth, max_items=2**62)


def find_duplicate_keys(text: str | bytes, max_depth: int = MAX_DEPTH) -> tuple[Any, list[str]]:
    """Trả (giá trị, danh sách khóa trùng). JSON hỏng -> `json.JSONDecodeError`; lồng quá
    `max_depth` -> `JsonTooDeepError` (đều là `ValueError`, không bao giờ `RecursionError`)."""
    raw = text.encode("utf-8") if isinstance(text, str) else text
    check_shape(raw, max_depth)
    dups: dict[str, None] = {}
    try:
        value = json.loads(raw, object_pairs_hook=_pairs_collector(dups))
    except RecursionError as exc:
        raise JsonTooDeepError("JSON nesting too deep") from exc
    except UnicodeDecodeError as exc:
        raise ValueError("JSON is not valid UTF-8") from exc
    return value, list(dups)[:MAX_REPORTED_KEYS]


def loads_strict(text: str | bytes) -> Any:
    value, dups = find_duplicate_keys(text)
    if dups:
        raise DuplicateKeyError(dups)
    return value

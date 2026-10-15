# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Parse JSON từ chối khóa trùng (tool-contracts-v1 C2, §3.2).

JSON cho phép khóa trùng và parser thường giữ giá trị cuối, nên `{"amount":"1","amount":"9000000"}`
vẫn qua schema. Mọi biên nhận JSON từ agent (MCP HTTP, stdio, REST) phải parse bằng hàm này.
"""

from __future__ import annotations

import json
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
_OPEN = {ord("["), ord("{")}
_CLOSE = {ord("]"), ord("}")}


def check_depth(raw: bytes, max_depth: int = MAX_DEPTH) -> None:
    """Đếm độ lồng `[`/`{` ngoài chuỗi JSON, O(n), không đệ quy."""
    depth = 0
    in_str = esc = False
    for b in raw:
        if in_str:
            if esc:
                esc = False
            elif b == 0x5C:  # backslash
                esc = True
            elif b == 0x22:
                in_str = False
        elif b == 0x22:
            in_str = True
        elif b in _OPEN:
            depth += 1
            if depth > max_depth:
                raise JsonTooDeepError(f"JSON nesting deeper than {max_depth}")
        elif b in _CLOSE:
            depth -= 1


def find_duplicate_keys(text: str | bytes, max_depth: int = MAX_DEPTH) -> tuple[Any, list[str]]:
    """Trả (giá trị, danh sách khóa trùng). JSON hỏng -> `json.JSONDecodeError`; lồng quá
    `max_depth` -> `JsonTooDeepError` (đều là `ValueError`, không bao giờ `RecursionError`)."""
    raw = text.encode("utf-8") if isinstance(text, str) else text
    check_depth(raw, max_depth)
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

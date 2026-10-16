# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Phát hiện khóa JSON trùng ở byte thô, trước khi SDK parse (parser giữ giá trị cuối, C2).

Thông điệp `tools/call` có khóa trùng được đánh dấu bằng `params._meta["xt.dev/duplicate_keys"]`;
dispatcher trả `invalid_request` reason `duplicate_key`. Client tự đặt dấu này chỉ làm hỏng chính
lời gọi của nó.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from xt.contracts.tools import JsonTooDeepError, find_duplicate_keys
from xt.contracts.tools.strict_json import JsonTooLargeError, check_shape

DUPLICATE_KEYS_META = "xt.dev/duplicate_keys"


def _mark(msg: Any, dups: list[str]) -> bool:
    if not isinstance(msg, dict) or msg.get("method") != "tools/call":
        return False
    params = msg.get("params")
    if not isinstance(params, dict):
        return False
    meta = params.get("_meta")
    if not isinstance(meta, dict):
        meta = {}
    meta[DUPLICATE_KEYS_META] = dups
    params["_meta"] = meta
    return True


def _render(value: Any, like: str | bytes) -> str | bytes:
    # ensure_ascii: chuỗi chứa lone surrogate (JSON hợp lệ) vẫn mã hóa được (audit R2-L1).
    text = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    return text.encode("ascii") if isinstance(like, bytes) else text


def mark_duplicate_keys(raw: str | bytes) -> str | bytes | None:
    """Thông điệp đã đánh dấu (cùng kiểu với đầu vào), hoặc None nếu không cần đổi."""
    try:
        value, dups = find_duplicate_keys(raw)
    except ValueError:
        return None  # JSON hỏng: để SDK trả parse error chuẩn JSON-RPC
    if not dups:
        return None
    messages = value if isinstance(value, list) else [value]
    if not any([_mark(m, dups) for m in messages]):
        return None
    return _render(value, raw)


@dataclass(frozen=True, slots=True)
class BodyRejection:
    """Lỗi JSON-RPC gọn, trả trước khi SDK parse (không echo input, không traceback)."""

    code: int
    message: str


def inspect_body(raw: bytes) -> bytes | BodyRejection | None:
    """Body HTTP: quá sâu -> -32700; batch (bỏ từ 2025-06-18) -> -32600; khóa trùng ở thông
    điệp không phải `tools/call` -> -32600; khóa trùng trong `tools/call` -> body đã đánh dấu."""
    try:
        check_shape(raw)
    except JsonTooDeepError:
        return BodyRejection(-32700, "Parse error: JSON nesting too deep")
    except JsonTooLargeError:
        return BodyRejection(-32600, "Invalid Request: too many JSON items")
    if raw.lstrip()[:1] == b"[":
        return BodyRejection(-32600, "Invalid Request: JSON-RPC batches are not supported")
    try:
        value, dups = find_duplicate_keys(raw)  # parse một lần, O(n)
    except ValueError:
        return BodyRejection(-32700, "Parse error")
    if not dups:
        return None
    if _mark(value, dups):
        out = _render(value, raw)
        assert isinstance(out, bytes)
        return out
    return BodyRejection(-32600, "Invalid Request: duplicate JSON object keys")
